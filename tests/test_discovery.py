import os
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from socialscraper import discovery
from socialscraper.adapters import tiktok as tiktok_module
from socialscraper.adapters import youtube as youtube_module
from socialscraper.adapters.base import ScrapeContext
from socialscraper.adapters.tiktok import TikTokAdapter
from socialscraper.adapters.youtube import YouTubeAdapter, search_url
from socialscraper.config import Settings
from socialscraper.net.retry import PermanentFetchError

NETWORK = bool(os.environ.get('SS_RUN_LOCAL_BROWSER_TESTS') or os.environ.get('SS_RUN_NETWORK_TESTS'))


def ctx(**kw):
    return ScrapeContext(cfg=Settings(impersonate='', notify_file=''), store=None, im=None, **kw)


class CanonicalTests(unittest.TestCase):
    def test_canonical_urls(self):
        c = discovery.canonical_url
        self.assertEqual(c('https://youtu.be/dQw4w9WgXcQ?t=3'), 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(c('https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=x'), 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(c('https://www.youtube.com/shorts/abcdefghijk'), 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(c('https://www.tiktok.com/@a.b/video/123/?lang=de#x'), 'https://www.tiktok.com/@a.b/video/123')
        for bad in ('https://www.tiktok.com/@a', 'https://example.com/watch?v=abcdefghijk', None, '',
                    'https://www.youtube.com/watch', 'https://evil.test/?u=https://www.tiktok.com/@a/video/1'):
            self.assertIsNone(c(bad), bad)

    def test_normalize_dedupes_and_limits(self):
        urls = ['https://www.tiktok.com/@a/video/1', 'https://www.tiktok.com/@a/video/1?x=1', 'muell',
                'https://www.tiktok.com/@b/video/2', 'https://www.tiktok.com/@c/video/3']
        self.assertEqual(discovery.normalize(urls, 2),
                         ['https://www.tiktok.com/@a/video/1', 'https://www.tiktok.com/@b/video/2'])


class DiscoverDispatchTests(unittest.TestCase):
    def test_instagram_is_refused_with_clear_message(self):
        for kind in ('hashtag', 'search', 'profile'):
            with self.assertRaisesRegex(discovery.DiscoveryError, 'Instagram'):
                discovery.discover(kind, 'instagram', 'x', ctx())

    def test_youtube_hashtag_becomes_hash_query_and_search_is_plain(self):
        with patch.object(YouTubeAdapter, 'search_urls', return_value=[
                'https://youtu.be/dQw4w9WgXcQ', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'junk']) as m:
            urls = discovery.discover('hashtag', 'youtube', 'Lofi', ctx(), 5)
            self.assertEqual(m.call_args.args[0], '#Lofi')
            self.assertEqual(urls, ['https://www.youtube.com/watch?v=dQw4w9WgXcQ'])
            discovery.discover('search', 'youtube', 'hip hop', ctx(), 5)
            self.assertEqual(m.call_args.args[0], 'hip hop')
        with self.assertRaises(discovery.DiscoveryError):
            discovery.discover('profile', 'youtube', 'x', ctx())

    def test_tiktok_dispatch(self):
        links = ['https://www.tiktok.com/@a/video/1', 'https://www.tiktok.com/@b/video/2']
        with patch.object(TikTokAdapter, 'hashtag_urls', return_value=links) as h, \
             patch.object(TikTokAdapter, 'search_urls', return_value=links[:1]) as s, \
             patch.object(TikTokAdapter, 'profile_urls', return_value=links[1:]) as p:
            self.assertEqual(discovery.discover('hashtag', 'tiktok', 'cats', ctx(), 10), links)
            self.assertEqual(discovery.discover('search', 'tiktok', 'cats', ctx(), 10), links[:1])
            self.assertEqual(discovery.discover('profile', 'tiktok', 'a', ctx(), 10), links[1:])
            h.assert_called_once(); s.assert_called_once(); p.assert_called_once()

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            discovery.discover('hashtag', 'youtube', 'x', ctx(), -1)
        with self.assertRaises(discovery.DiscoveryError):
            discovery.discover('video', 'youtube', 'x', ctx())

    def test_needs_login(self):
        self.assertTrue(discovery.needs_login('hashtag', 'tiktok'))
        self.assertTrue(discovery.needs_login('search', 'tiktok'))
        self.assertFalse(discovery.needs_login('profile', 'tiktok'))
        self.assertFalse(discovery.needs_login('search', 'youtube'))


class YouTubeSearchTests(unittest.TestCase):
    def fake_ydl(self, info, captured):
        class FakeYDL:
            def __init__(self, options): captured['options'] = options
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                captured['url'] = url; captured['download'] = download
                return info
        return FakeYDL

    def test_search_url_sorted_by_date(self):
        self.assertEqual(search_url('#lofi beats'),
                         'https://www.youtube.com/results?search_query=%23lofi+beats&sp=CAISAhAB')

    def test_flat_metadata_only_and_entries_are_filtered(self):
        info = {'entries': [
            {'id': 'aaaaaaaaaaa', 'ie_key': 'Youtube'}, {'id': 'aaaaaaaaaaa'},  # doppelt
            {'id': 'UCchannelxxxxxxxxxxxxxxx', 'ie_key': 'YoutubeTab'}, None, {'id': 'x'},
            {'id': 'bbbbbbbbbbb'}, {'id': 'ccccccccccc'}]}
        captured = {}
        with patch.object(youtube_module, 'youtube_runtime_options', return_value={'js_runtimes': {'node': {}}}), \
             patch.dict(sys.modules, {'yt_dlp': types.SimpleNamespace(YoutubeDL=self.fake_ydl(info, captured))}):
            urls = YouTubeAdapter().search_urls('#lofi', ctx(), 2)
        self.assertEqual(urls, ['https://www.youtube.com/watch?v=aaaaaaaaaaa', 'https://www.youtube.com/watch?v=bbbbbbbbbbb'])
        o = captured['options']
        self.assertEqual((o['extract_flat'], o['skip_download'], o['getcomments'], captured['download']),
                         ('in_playlist', True, False, False))
        self.assertIn('js_runtimes', o)
        self.assertEqual(o['playlist_items'], '1-2')
        self.assertIn('search_query=%23lofi', captured['url'])

    def test_proxy_is_used(self):
        cfg = Settings(impersonate='', notify_file='', proxies=['http://u:p@h:1'])
        im = Mock(); im.proxy_for.return_value = types.SimpleNamespace(url='http://u:p@h:1')
        captured = {}
        c = ScrapeContext(cfg=cfg, store=None, im=im)
        with patch.object(youtube_module, 'youtube_runtime_options', return_value={}), \
             patch.dict(sys.modules, {'yt_dlp': types.SimpleNamespace(YoutubeDL=self.fake_ydl({'entries': []}, captured))}):
            self.assertEqual(YouTubeAdapter().search_urls('x', c, 3), [])
        self.assertEqual(captured['options']['proxy'], 'http://u:p@h:1')

    def test_zero_limit_and_empty_query(self):
        self.assertEqual(YouTubeAdapter().search_urls('x', ctx(), 0), [])
        with self.assertRaises(ValueError):
            YouTubeAdapter().search_urls('  ', ctx(), 3)


class FakePage:
    def __init__(self, pages, text=''):
        self.pages, self.text, self.urls, self.i = pages, text, [], 0

    def goto(self, url, **kw): self.urls.append(url)
    def wait_for_timeout(self, ms): pass

    def evaluate(self, js):
        if 'innerText' in js:
            return self.text
        batch = self.pages[min(self.i, len(self.pages) - 1)]
        self.i += 1
        return batch


class TikTokLinkTests(unittest.TestCase):
    def setUp(self):
        for name in ('warmup', 'scroll'):
            p = patch.object(tiktok_module.humanize, name); p.start(); self.addCleanup(p.stop)

    def context(self, page):
        sessions = Mock()

        @contextmanager
        def acquire(*a, **k):
            yield page
        sessions.acquire = acquire
        return ScrapeContext(cfg=Settings(notify_file=''), store=None, im=None, sessions=sessions)

    def test_hashtag_collects_unique_links_from_tag_page(self):
        page = FakePage([['https://www.tiktok.com/@a/video/1?x=1', 'https://www.tiktok.com/@b/video/2', 'https://evil.test/@a/video/9'],
                         ['https://www.tiktok.com/@b/video/2', 'https://www.tiktok.com/@c/video/3', 'https://www.tiktok.com/@c']])
        urls = TikTokAdapter().hashtag_urls('#Katzen', self.context(page), 3)
        self.assertEqual(urls, ['https://www.tiktok.com/@a/video/1', 'https://www.tiktok.com/@b/video/2',
                                'https://www.tiktok.com/@c/video/3'])
        self.assertEqual(page.urls[-1], 'https://www.tiktok.com/tag/Katzen')

    def test_search_uses_search_page_and_limit(self):
        page = FakePage([['https://www.tiktok.com/@a/video/1', 'https://www.tiktok.com/@b/video/2']])
        urls = TikTokAdapter().search_urls('hallo welt', self.context(page), 1)
        self.assertEqual(urls, ['https://www.tiktok.com/@a/video/1'])
        self.assertEqual(page.urls[-1], 'https://www.tiktok.com/search/video?q=hallo%20welt')

    def test_captcha_wall_gives_clear_error_without_retry(self):
        page = FakePage([[]], text='#cats | Anmelden | Bewege den Schieberegler, um das Puzzle zusammenzusetzen')
        with self.assertRaisesRegex(PermanentFetchError, 'CAPTCHA'):
            TikTokAdapter().hashtag_urls('cats', self.context(page), 3)

    def test_empty_result_without_wall_is_empty_list(self):
        self.assertEqual(TikTokAdapter().hashtag_urls('cats', self.context(FakePage([[]], text='nichts')), 3), [])

    def test_validation(self):
        a = TikTokAdapter()
        with self.assertRaises(ValueError): a.hashtag_urls('mit leerzeichen', ctx(sessions=Mock()), 3)
        with self.assertRaises(ValueError): a.hashtag_urls('cats', ctx(), 3)  # keine Browser-Session
        self.assertEqual(a.hashtag_urls('cats', ctx(sessions=Mock()), 0), [])


@unittest.skipUnless(NETWORK, 'echtes Netz: SS_RUN_LOCAL_BROWSER_TESTS=1 oder SS_RUN_NETWORK_TESTS=1')
class RealYouTubeDiscoveryTests(unittest.TestCase):
    def test_real_youtube_search_and_hashtag(self):
        c = ScrapeContext(cfg=Settings(notify_file=''), store=None, im=None)
        for kind, value in (('search', 'lofi hip hop'), ('hashtag', 'lofi')):
            urls = discovery.discover(kind, 'youtube', value, c, 3)
            self.assertTrue(1 <= len(urls) <= 3, (kind, urls))
            self.assertTrue(all(u.startswith('https://www.youtube.com/watch?v=') for u in urls))
            self.assertEqual(len(set(urls)), len(urls))


if __name__ == '__main__':
    unittest.main()
