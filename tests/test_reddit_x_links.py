"""Links, Ziele, Konten und Cookies für Reddit und X (ohne Netz, ohne Browser)."""
import json
import os
import pathlib
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from socialscraper import discovery, guicore as core, platformlinks, repo
from socialscraper.adapters import ALL, resolve
from socialscraper.net import cookies, errors, proxymode
from socialscraper.net.cookies import CookieJar
from socialscraper.store import Store
from socialscraper.util import matches_host


class RedditLinkTests(unittest.TestCase):
    def test_all_variants_lead_to_the_same_post(self):
        variants = [
            'https://www.reddit.com/r/python/comments/1abcd2/some_title/?utm_source=share&utm_medium=web',
            'https://reddit.com/r/python/comments/1abcd2/',
            'https://old.reddit.com/r/python/comments/1abcd2/some_title/',
            'https://np.reddit.com/r/python/comments/1abcd2/',
            'https://m.reddit.com/r/python/comments/1abcd2/some_title/',
            'https://www.reddit.com/r/python/comments/1abcd2/some_title/kxyz123/',     # Kommentar-Permalink -> ganzer Beitrag
            'https://www.reddit.com/r/python/comments/1abcd2',
            'http://www.reddit.com/r/python/comments/1ABCD2/',
        ]
        for url in variants:
            self.assertEqual(core.detect_platform(url), 'reddit', url)
            self.assertEqual(core.canonical_video(url), ('reddit:1abcd2', 'https://www.reddit.com/r/python/comments/1abcd2/'), url)
            self.assertEqual(discovery.canonical_url(url), 'https://www.reddit.com/r/python/comments/1abcd2/', url)

    def test_links_without_subreddit_and_short_links(self):
        for url in ('https://redd.it/1abcd2', 'https://www.reddit.com/comments/1abcd2/titel/', 'https://www.reddit.com/user/someone/comments/1abcd2/titel/',
                    'https://www.reddit.com/gallery/1abcd2'):
            self.assertEqual(core.canonical_video(url), ('reddit:1abcd2', 'https://www.reddit.com/comments/1abcd2/'), url)
        self.assertEqual(platformlinks.reddit_post('https://redd.it/1abcd2')[:2], ('1abcd2', None))
        self.assertEqual(platformlinks.reddit_post('https://www.reddit.com/r/Python/comments/1abcd2/')[1], 'Python')

    def test_share_links_are_recognised_but_not_resolved(self):
        url = 'https://www.reddit.com/r/python/s/AbCdEf1234'
        self.assertEqual(core.detect_platform(url), 'reddit')
        self.assertTrue(platformlinks.reddit_share(url))
        self.assertIsNone(platformlinks.reddit_post(url))
        self.assertEqual(core.canonical_video(url), (url, url))      # unverändert: erst der Browser löst auf
        self.assertIsNone(discovery.canonical_url(url))

    def test_lookalikes_and_foreign_hosts_are_not_reddit(self):
        for url in ('https://evil.com/?u=reddit.com', 'https://evil.com/r/python/comments/1abcd2/', 'https://notreddit.com/r/python/comments/1abcd2/',
                    'https://reddit.com.evil.com/r/python/comments/1abcd2/', 'https://evil.com/redd.it/1abcd2', 'https://xreddit.com/',
                    'https://reddit.com@evil.com/r/a/comments/1abcd2/', 'ftp://www.reddit.com/r/a/comments/1abcd2/',
                    'https://www.reddit.com\\@evil.com/r/a/comments/1abcd2/', 'https://redd.it.evil.com/1abcd2', 'reddit.com'):
            self.assertIsNone(core.detect_platform(url), url)
            self.assertIsNone(platformlinks.reddit_post(url), url)
            self.assertIsNone(resolve(url), url)
        self.assertFalse(matches_host('https://evil.com/?u=reddit.com', 'reddit.com'))

    def test_non_post_links_stay_unchanged(self):
        for url in ('https://www.reddit.com/r/python/', 'https://www.reddit.com/user/someone/', 'https://i.redd.it/bild.jpg',
                    'https://v.redd.it/abc123', 'https://www.reddit.com/r/python/comments/', 'https://www.reddit.com/'):
            self.assertIsNone(platformlinks.reddit_post(url), url)
            self.assertEqual(core.canonical_video(url), (url, url), url)
            self.assertIsNone(discovery.canonical_url(url), url)

    def test_pasted_links_are_grouped_and_deduplicated(self):
        text = ('https://old.reddit.com/r/python/comments/1abcd2/a/\n'
                'www.reddit.com/r/python/comments/1abcd2/b/?utm_source=x\n'
                'https://redd.it/1abcd2\n'
                'https://evil.com/?u=reddit.com\n')
        groups, bad, merged = core.parse_links_detailed(text)
        self.assertEqual(groups, {'reddit': ['https://www.reddit.com/r/python/comments/1abcd2/']})
        self.assertEqual((bad, merged), (['https://evil.com/?u=reddit.com'], 2))


class XLinkTests(unittest.TestCase):
    def test_all_variants_lead_to_the_same_post(self):
        variants = [
            'https://x.com/beispiel/status/1234567890123456789',
            'https://x.com/beispiel/status/1234567890123456789?s=20&t=AbC_dEf',
            'https://twitter.com/beispiel/status/1234567890123456789',
            'https://www.twitter.com/Beispiel/statuses/1234567890123456789',
            'https://mobile.twitter.com/beispiel/status/1234567890123456789?s=09',
            'https://www.x.com/beispiel/status/1234567890123456789/photo/1',
            'https://x.com/beispiel/status/1234567890123456789/analytics',
        ]
        for url in variants:
            self.assertEqual(core.detect_platform(url), 'x', url)
            key, link = core.canonical_video(url)
            self.assertEqual(key, 'x:1234567890123456789', url)
            self.assertTrue(link.startswith('https://x.com/') and link.endswith('/status/1234567890123456789'), link)
        self.assertEqual(core.canonical_video('https://twitter.com/Beispiel/status/12')[1], 'https://x.com/Beispiel/status/12')

    def test_links_without_account_name(self):
        for url in ('https://x.com/i/web/status/1234567890123456789', 'https://twitter.com/i/status/1234567890123456789',
                    'https://mobile.twitter.com/i/web/status/1234567890123456789?s=20'):
            self.assertEqual(core.canonical_video(url), ('x:1234567890123456789', 'https://x.com/i/status/1234567890123456789'), url)
            self.assertEqual(platformlinks.x_status(url)[1], None)

    def test_short_links_are_recognised_but_never_followed(self):
        url = 'https://t.co/AbCdEf12'
        self.assertEqual(core.detect_platform(url), 'x')
        self.assertTrue(platformlinks.x_short(url))
        self.assertIsNone(platformlinks.x_status(url))
        self.assertEqual(core.canonical_video(url), (url, url))
        self.assertIsNone(discovery.canonical_url(url))
        self.assertIs(resolve(url).platform, 'x')        # damit der Abruf eine klare Meldung geben kann

    def test_lookalikes_and_services_are_not_x(self):
        for url in ('https://fxtwitter.com/beispiel/status/12345', 'https://vxtwitter.com/beispiel/status/12345', 'https://evil.com/?u=x.com',
                    'https://evil.com/beispiel/status/12345', 'https://box.com/beispiel/status/12345', 'https://x.com.evil.com/a/status/12',
                    'https://twitter.com.evil.com/a/status/12', 'https://nottwitter.com/a/status/12', 'https://t.co.evil.com/AbCdEf12',
                    'https://x.com@evil.com/a/status/12', 'https://evil.com/t.co/AbCdEf12', 'x.com/beispiel/status/12'):
            self.assertIsNone(core.detect_platform(url), url)
            self.assertIsNone(platformlinks.x_status(url), url)
            self.assertIsNone(resolve(url), url)

    def test_profile_and_other_pages_are_not_posts(self):
        for url in ('https://x.com/beispiel', 'https://x.com/beispiel/status/', 'https://x.com/beispiel/status/abc', 'https://x.com/search?q=a',
                    'https://x.com/home', 'https://x.com/hashtag/tanzen'):
            self.assertIsNone(platformlinks.x_status(url), url)
            self.assertEqual(core.canonical_video(url), (url, url), url)

    def test_pasted_links_are_grouped(self):
        groups, bad, merged = core.parse_links_detailed(
            'https://x.com/a/status/10?s=20\nhttps://twitter.com/a/status/10\nhttps://fxtwitter.com/a/status/10\nhttps://t.co/AbCdEf12')
        self.assertEqual(groups, {'x': ['https://x.com/a/status/10', 'https://t.co/AbCdEf12']})
        self.assertEqual((bad, merged), (['https://fxtwitter.com/a/status/10'], 1))


class AdapterRegistryTests(unittest.TestCase):
    def test_adapters_flags_and_resolve(self):
        by_platform = {a.platform: a for a in ALL}
        self.assertTrue(by_platform['reddit'].needs_browser and not by_platform['reddit'].needs_auth)
        self.assertTrue(by_platform['x'].needs_browser and by_platform['x'].needs_auth)
        self.assertIs(resolve('https://www.reddit.com/r/abc/comments/abcdef/'), by_platform['reddit'])
        self.assertIs(resolve('https://redd.it/abcdef'), by_platform['reddit'])
        self.assertIs(resolve('https://mobile.twitter.com/a/status/1'), by_platform['x'])
        self.assertIs(resolve('https://x.com/a/status/1'), by_platform['x'])
        self.assertIsNone(resolve('https://evil.com/?u=reddit.com'))

    def test_platform_tables_contain_both(self):
        for table in (repo.PLATFORMS, repo.LOGIN_PLATFORMS):
            self.assertIn('reddit', table); self.assertIn('x', table)
        self.assertEqual(core.PLATFORMS['reddit']['name'], 'Reddit')
        self.assertEqual(core.PLATFORMS['x']['name'], 'X')
        self.assertEqual((core.PLATFORMS['reddit']['identity'], core.PLATFORMS['x']['identity']), ('reddit_main', 'x_main'))
        self.assertTrue(core.PLATFORMS['reddit']['login'] and core.PLATFORMS['x']['login'])


class CookieTests(unittest.TestCase):
    def cookie(self, name, domain, value='geheim-wert'):
        return {'name': name, 'value': value, 'domain': domain, 'path': '/', 'secure': True}

    def test_reddit_cookies(self):
        self.assertTrue(CookieJar.authenticated([self.cookie('reddit_session', '.reddit.com')], 'reddit'))
        self.assertTrue(CookieJar.authenticated([self.cookie('token_v2', 'www.reddit.com')], 'reddit'))
        self.assertFalse(CookieJar.authenticated([self.cookie('loid', '.reddit.com')], 'reddit'))
        self.assertFalse(CookieJar.authenticated([self.cookie('reddit_session', '.evil.com')], 'reddit'))
        self.assertFalse(CookieJar.authenticated([self.cookie('reddit_session', '.reddit.com', '')], 'reddit'))

    def test_x_needs_both_cookies_and_accepts_twitter_domain(self):
        both = [self.cookie('auth_token', '.x.com'), self.cookie('ct0', '.x.com')]
        self.assertTrue(CookieJar.authenticated(both, 'x'))
        self.assertTrue(CookieJar.authenticated([self.cookie('auth_token', '.twitter.com'), self.cookie('ct0', '.twitter.com')], 'x'))
        self.assertFalse(CookieJar.authenticated(both[:1], 'x'))
        self.assertFalse(CookieJar.authenticated(both[1:], 'x'))
        self.assertFalse(CookieJar.authenticated([self.cookie('auth_token', '.x.com'), self.cookie('ct0', '.evil.com')], 'x'))
        self.assertFalse(CookieJar.authenticated([self.cookie('auth_token', '.x.com'), self.cookie('ct0', '.x.com', '')], 'x'))
        self.assertEqual(cookies.DOMAINS['reddit'], 'reddit.com'); self.assertEqual(cookies.DOMAINS['x'], 'x.com')

    def test_files_are_scoped_per_platform_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            both = [self.cookie('auth_token', '.x.com'), self.cookie('ct0', '.x.com'), self.cookie('fremd', '.evil.com')]
            CookieJar.save('x_main', 'x', both, base_dir=tmp)
            saved = CookieJar.load('x_main', 'x', base_dir=tmp)
            self.assertEqual({c['name'] for c in saved}, {'auth_token', 'ct0'})       # Fremdes fliegt raus
            self.assertTrue(CookieJar.is_authenticated('x_main', 'x', base_dir=tmp))
            self.assertFalse(CookieJar.is_authenticated('x_main', 'reddit', base_dir=tmp))
            self.assertTrue(pathlib.Path(tmp, 'x_main.x.json').is_file())
            CookieJar.save('reddit_main', 'reddit', [self.cookie('reddit_session', '.reddit.com')], base_dir=tmp)
            self.assertTrue(pathlib.Path(tmp, 'reddit_main.reddit.json').is_file())

    def test_import_formats_for_the_new_platforms(self):
        text = json.dumps([self.cookie('auth_token', '.x.com'), self.cookie('ct0', '.x.com'), self.cookie('gt', '.twitter.com')])
        parsed = CookieJar.for_platform(cookies.parse_any(text), 'x', strict=True)
        self.assertEqual(len(parsed), 3)
        with self.assertRaises(ValueError):
            CookieJar.for_platform('keine Liste', 'x')


class AccountStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'data' / 'cookies').mkdir(parents=True)

    def write(self, name, items):
        (self.root / 'data' / 'cookies' / name).write_text(json.dumps([{'name': n, 'value': 'v'} for n in items]), encoding='utf-8')

    def test_reddit_status_is_optional(self):
        state, text = core.account_status('reddit', self.root)
        self.assertEqual(state, 'missing'); self.assertIn('freiwillig', text)
        self.write('reddit_main.reddit.json', ['token_v2'])
        self.assertEqual(core.account_status('reddit', self.root)[0], 'saved')

    def test_x_status_needs_both_cookies(self):
        self.write('x_main.x.json', ['auth_token'])
        self.assertEqual(core.account_status('x', self.root)[0], 'missing')
        self.write('x_main.x.json', ['auth_token', 'ct0'])
        self.assertEqual(core.account_status('x', self.root)[0], 'saved')
        self.assertEqual(core.account_status('x', self.root, 'x_zweit')[0], 'missing')

    def test_other_platforms_unchanged(self):
        self.write('tiktok_main.tiktok.json', ['sessionid_ss'])
        self.write('instagram_main.instagram.json', ['sessionid'])
        self.assertEqual(core.account_status('tiktok', self.root)[0], 'saved')
        self.assertEqual(core.account_status('instagram', self.root)[0], 'saved')
        self.assertEqual(core.account_status('youtube', self.root)[0], 'none')

    def test_commands_for_the_new_platforms(self):
        cmd = core.login_command('x', 'qr', 'x_main', 'de')
        self.assertIn('--platform', cmd); self.assertEqual(cmd[cmd.index('--platform') + 1], 'x')
        self.assertEqual(core.check_command('reddit', 'reddit_main')[0], 'check')
        self.assertEqual(core.logout_command('x', 'x_main')[-1], 'x_main')
        self.assertEqual(core.import_cookies_command('reddit', 'reddit_main', 'c.json')[0], 'import-cookies')
        for platform in ('reddit', 'x'):
            with self.assertRaisesRegex(ValueError, 'selbst an'):
                core.login_command(platform, 'password')
        cmd = core.scrape_command('x', ['https://x.com/a/status/1'], 5, require_auth=True, identity='x_main')
        self.assertEqual(cmd[0], 'scrape'); self.assertIn('--require-auth', cmd)


class RepoRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(str(pathlib.Path(self.tmp.name) / 'db.sqlite')); self.addCleanup(self.store.close)
        self.db = self.store.db

    def test_accounts_for_reddit_and_x(self):
        repo.ensure_default_accounts(self.db)
        self.assertEqual({a['identity'] for a in repo.list_accounts(self.db)}, {'tiktok_main', 'instagram_main', 'reddit_main', 'x_main'})
        a = repo.add_account(self.db, 'reddit', 'Mein Zweitkonto')
        self.assertEqual((a['identity'], a['platform']), ('reddit_mein_zweitkonto', 'reddit'))
        b = repo.add_account(self.db, 'x', 'Zweitkonto X')
        self.assertEqual(b['identity'], 'x_zweitkonto_x')
        with self.assertRaises(ValueError):
            repo.add_account(self.db, 'unsupported', 'Nein')

    def test_target_kinds_per_platform(self):
        ok = [('search', 'reddit', 'r/python'), ('search', 'reddit', 'lofi musik'), ('profile', 'reddit', 'u/spez'), ('video', 'reddit', 'https://www.reddit.com/r/abc/comments/abcdef/'),
              ('search', 'x', 'lofi'), ('hashtag', 'x', '#tanzen'), ('profile', 'x', '@beispiel_konto'), ('video', 'x', 'https://x.com/a/status/1')]
        for kind, platform, value in ok:
            self.assertEqual(repo.add_target(self.db, kind, platform, value)['platform'], platform, (kind, platform, value))
        with self.assertRaisesRegex(ValueError, 'Hashtags'):
            repo.add_target(self.db, 'hashtag', 'reddit', 'python')
        with self.assertRaises(ValueError):
            repo.add_target(self.db, 'profile', 'youtube', 'abc')
        with self.assertRaises(ValueError):
            repo.add_target(self.db, 'profile', 'instagram', 'abc')

    def test_values_are_normalised(self):
        n = repo.normalize_target_value
        self.assertEqual(n('profile', 'u/spez', 'reddit'), 'spez')
        self.assertEqual(n('profile', '/user/Some-Name_1', 'reddit'), 'Some-Name_1')
        self.assertEqual(n('profile', '@spez', 'reddit'), 'spez')
        self.assertEqual(n('search', 'r/Python', 'reddit'), 'r/Python')
        self.assertEqual(n('search', '/r/python/', 'reddit'), 'r/python')
        self.assertEqual(n('search', 'python tipps', 'reddit'), 'python tipps')
        self.assertEqual(n('profile', '@Beispiel_Konto', 'x'), 'Beispiel_Konto')
        self.assertEqual(n('hashtag', '#Tanzen', 'x'), 'tanzen')
        self.assertEqual(n('profile', 'name.mit.punkt', 'tiktok'), 'name.mit.punkt')   # TikTok wie bisher
        for kind, value, platform in (('profile', 'ab', 'reddit'), ('profile', 'u/zu_langer_name_der_nicht_geht', 'reddit'), ('profile', 'a b', 'reddit'),
                                      ('search', 'r/x', 'reddit'), ('search', 'r/', 'reddit'), ('search', 'r/mit leerzeichen', 'reddit'),
                                      ('profile', 'name.mit.punkt', 'x'), ('profile', 'zu_langer_kontoname_x', 'x'), ('profile', '', 'x'),
                                      ('hashtag', 'a b', 'x')):
            with self.assertRaises(ValueError, msg=(kind, value, platform)):
                n(kind, value, platform)

    def test_duplicate_targets_are_refused(self):
        repo.add_target(self.db, 'search', 'reddit', 'r/python')
        with self.assertRaisesRegex(ValueError, 'schon'):
            repo.add_target(self.db, 'search', 'reddit', '/r/python/')


class DiscoveryRuleTests(unittest.TestCase):
    def ctx(self):
        return types.SimpleNamespace(cfg=types.SimpleNamespace(proxy_mode='aus'))

    class Fake:
        needs_browser = True

        def __init__(self, platform):
            self.platform, self.calls = platform, []

        def __getattr__(self, name):
            if name.endswith('_urls'):
                return lambda value, ctx, limit: self.calls.append((name, value, limit)) or [
                    'https://www.reddit.com/r/abc/comments/abcdef/x/?utm=1', 'https://www.reddit.com/r/abc/comments/abcdef/', 'https://x.com/b/status/5?s=1',
                    'https://evil.com/?u=reddit.com']
            raise AttributeError(name)

    def discover_with(self, kind, platform, value):
        fake = self.Fake(platform)
        with patch.object(discovery, '_adapter', return_value=fake):
            return discovery.discover(kind, platform, value, self.ctx(), 10), fake.calls

    def test_reddit_discovery_routes(self):
        urls, calls = self.discover_with('search', 'reddit', 'r/python')
        self.assertEqual(calls, [('search_urls', 'r/python', 10)])
        self.assertEqual(urls, ['https://www.reddit.com/r/abc/comments/abcdef/', 'https://x.com/b/status/5'])      # kanonisch, ohne Doppelte und Fremdes
        self.assertEqual(self.discover_with('profile', 'reddit', 'spez')[1], [('profile_urls', 'spez', 10)])
        with self.assertRaisesRegex(discovery.DiscoveryError, 'keine Hashtags'):
            self.discover_with('hashtag', 'reddit', 'python')

    def test_x_discovery_routes(self):
        self.assertEqual(self.discover_with('hashtag', 'x', 'tanzen')[1], [('hashtag_urls', 'tanzen', 10)])
        self.assertEqual(self.discover_with('search', 'x', 'lofi')[1], [('search_urls', 'lofi', 10)])
        self.assertEqual(self.discover_with('profile', 'x', 'beispiel')[1], [('profile_urls', 'beispiel', 10)])

    def test_login_need(self):
        self.assertTrue(all(discovery.needs_login(kind, 'x') for kind in ('hashtag', 'search', 'profile', 'video')))
        self.assertFalse(any(discovery.needs_login(kind, 'reddit') for kind in ('search', 'profile', 'video')))
        self.assertTrue(discovery.needs_login('search', 'tiktok'))
        self.assertFalse(discovery.needs_login('profile', 'tiktok'))

    def test_x_discovery_is_refused_with_free_proxies_and_tor(self):
        for mode in ('frei', 'tor'):
            ctx = types.SimpleNamespace(cfg=types.SimpleNamespace(proxy_mode=mode))
            with self.assertRaises(proxymode.AccountsForbidden):
                discovery.discover('search', 'x', 'lofi', ctx, 5)


class ProxyRuleTests(unittest.TestCase):
    def cfg(self, mode):
        return types.SimpleNamespace(proxy_mode=mode)

    def test_x_is_always_refused_in_anonymous_modes(self):
        for mode in ('frei', 'tor'):
            for comments in (0, 5):
                with self.assertRaisesRegex(proxymode.AccountsForbidden, 'X braucht immer ein Konto'):
                    proxymode.assert_fetch_allowed(self.cfg(mode), 'x', comments)
            with self.assertRaises(proxymode.AccountsForbidden):
                proxymode.assert_fetch_allowed(self.cfg(mode), 'x', 0, 'search')

    def test_reddit_is_allowed_without_account(self):
        for mode in ('frei', 'tor', 'aus', 'liste'):
            for kind in ('video', 'search', 'profile'):
                proxymode.assert_fetch_allowed(self.cfg(mode), 'reddit', 50, kind)      # kein Fehler

    def test_x_and_reddit_are_fine_in_normal_modes(self):
        for mode in ('aus', 'liste'):
            proxymode.assert_fetch_allowed(self.cfg(mode), 'x', 50)
            proxymode.assert_fetch_allowed(self.cfg(mode), 'reddit', 50)

    def test_existing_rules_unchanged(self):
        with self.assertRaises(proxymode.AccountsForbidden):
            proxymode.assert_fetch_allowed(self.cfg('tor'), 'instagram')
        with self.assertRaises(proxymode.AccountsForbidden):
            proxymode.assert_fetch_allowed(self.cfg('frei'), 'tiktok', 3)
        proxymode.assert_fetch_allowed(self.cfg('frei'), 'youtube', 99)

    def test_texts_mention_x(self):
        cfg = types.SimpleNamespace(proxy_mode='frei', proxies=[], db_path=str(pathlib.Path(tempfile.gettempdir(), 'rx_dummy.db')))
        with patch.object(proxymode.freeproxy, 'status', return_value={'gueltig': False, 'vorhanden': 0, 'alter_s': None}):
            text = proxymode.describe(cfg)[1]
        self.assertIn('Instagram, X und TikTok-Kommentare sind gesperrt', text)


class ErrorTextTests(unittest.TestCase):
    def test_reddit_block_is_explained_in_german(self):
        text = errors.explain(RuntimeError('You\'ve been blocked by network security.'))
        self.assertIn('Sicherheitsprüfung', text); self.assertIn('keine eindeutige Ursache', text)
        self.assertNotIn('Handy-Hotspot', text)
        self.assertEqual(errors.explain(RuntimeError('blocked by network security')), errors.MESSAGES['sperre'])
        self.assertNotIn('Traceback', text)

    def test_other_messages_unchanged(self):
        self.assertEqual(errors.explain(RuntimeError('HTTP 429')), errors.MESSAGES['429'])
        self.assertEqual(errors.explain(RuntimeError('HTTP Error 403: Forbidden')), errors.MESSAGES['403'])


if __name__ == '__main__':
    unittest.main()
