"""Reddit-Adapter: Auswertung und Abruf mit simulierten Seiten (kein Netz, kein Browser)."""
import json
import pathlib
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from socialscraper.adapters import reddit
from socialscraper.adapters.base import ScrapeContext
from socialscraper.adapters.reddit import RedditAdapter
from socialscraper.config import Settings
from socialscraper.net import proxymode
from socialscraper.net.retry import PermanentFetchError

FIXTURES = pathlib.Path(__file__).parent / 'fixtures'
BASE = '/r/AskReddit/comments/abc123/'
POST_URL = 'https://www.reddit.com/r/AskReddit/comments/abc123/welches_buch/'


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def ok(data, status=200):
    return {'status': status, 'text': data if isinstance(data, str) else json.dumps(data)}


class FakePage:
    """Ersatz für die Browserseite: antwortet auf TEXT_JS, FETCH_JS (nach Pfad) und DOM_JS."""

    def __init__(self, thread=None, text='', url=POST_URL, dom=None, respond=None):
        self.thread = load('reddit_thread.json') if thread is None else thread
        self.text, self.url, self.dom, self.custom = text, url, dom, respond
        self.gotos, self.fetched = [], []

    def goto(self, url, **kwargs):
        self.gotos.append(url)

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script, arg=None):
        if script == reddit.TEXT_JS:
            return self.text
        if script == reddit.DOM_JS:
            return self.dom
        if script == reddit.FETCH_JS:
            path = arg[0]
            self.fetched.append(path)
            if self.custom is not None:
                answer = self.custom(path)
                if answer is not None:
                    return answer
            return self.respond(path)
        raise AssertionError('Unerwartetes Skript: ' + str(script)[:60])

    def respond(self, path):
        if path.startswith(BASE + '.json'):
            return ok(self.thread)
        if path.startswith('/api/morechildren.json'):
            ids = parse_qs(urlsplit(path).query)['children'][0].split(',')
            return ok(load('reddit_morechildren.json' if 'c4' in ids else 'reddit_morechildren_2.json'))
        raise AssertionError('Unerwarteter Pfad: ' + path)


class FakePool:
    def __init__(self, page):
        self.page, self.calls = page, []

    @contextmanager
    def acquire(self, *args):
        self.calls.append(args)
        yield self.page


class Case(unittest.TestCase):
    def setUp(self):
        p = patch('socialscraper.net.retry.time.sleep'); p.start(); self.addCleanup(p.stop)
        self.saved = []

    def ctx(self, page, max_comments=50, **kw):
        cfg = Settings(max_comments=max_comments, **kw)
        ctx = ScrapeContext(cfg=cfg, store=None, im=None, sessions=FakePool(page), identity='reddit_main', country='de')
        ctx.checkpoint = lambda video, comments: self.saved.append((video.uid, len(comments)))
        return ctx

    def fetch(self, page=None, url=POST_URL, max_comments=50, **kw):
        page = page or FakePage()
        return RedditAdapter().fetch(url, self.ctx(page, max_comments, **kw)), page


class ParsingTests(unittest.TestCase):
    def post(self):
        return load('reddit_thread.json')[0]['data']['children'][0]['data']

    def test_post_becomes_video_as_specified(self):
        v = reddit.video_from_post(self.post())
        self.assertEqual((v.platform, v.video_id, v.uid), ('reddit', 'abc123', 'reddit:abc123'))
        self.assertEqual(v.url, 'https://www.reddit.com/r/AskReddit/comments/abc123/welches_buch_hat_dich_gepraegt/')
        self.assertEqual(v.author, 'beispiel_op')
        self.assertEqual(v.caption, 'Welches Buch hat dich geprägt?\n\nIch suche Tipps.\n\nDanke!')     # Titel zuerst
        self.assertEqual(v.created_at, '2025-10-09T08:53:20+00:00')
        self.assertIsNone(v.view_count); self.assertIsNone(v.share_count); self.assertIsNone(v.music)
        self.assertEqual((v.like_count, v.comment_count, v.hashtags), (1520, 9, []))
        self.assertEqual(v.raw['subreddit'], 'AskReddit'); self.assertEqual(v.raw['upvote_ratio'], 0.96)
        self.assertEqual(v.raw['flair'], 'Frage'); self.assertIs(v.raw['is_self'], True); self.assertIs(v.raw['over_18'], False)
        self.assertEqual(v.raw['url_overridden_by_dest'], 'https://example.org/artikel')
        self.assertEqual(v.raw['crosspost'], {'id': 'orig99', 'subreddit': 'books', 'author': 'autor_x', 'permalink': '/r/books/comments/orig99/titel/'})

    def test_title_only_link_post_and_removed_body(self):
        data = {**self.post(), 'selftext': '', 'is_self': False}
        self.assertEqual(reddit.video_from_post(data).caption, 'Welches Buch hat dich geprägt?')
        data = {**self.post(), 'selftext': '[removed]', 'author': '[deleted]'}
        v = reddit.video_from_post(data)
        self.assertEqual(v.caption, 'Welches Buch hat dich geprägt?'); self.assertIsNone(v.author)

    def test_missing_id_is_an_error(self):
        with self.assertRaises(ValueError):
            reddit.video_from_post({'title': 'x'})

    def test_comment_mapping_and_placeholders(self):
        data = {'id': 'k1', 'parent_id': 't1_k0', 'author': 'Beispiel_OP', 'body': ' Hallo ', 'score': 3, 'created_utc': 1760000100}
        c = reddit.comment_from_data(data, 'abc123', 'beispiel_op')
        self.assertEqual((c.platform, c.uid, c.video_id, c.comment_id, c.parent_id, c.text, c.author, c.like_count),
                         ('reddit', 'reddit:k1', 'abc123', 'k1', 'k0', 'Hallo', 'Beispiel_OP', 3))
        self.assertTrue(c.is_author_reply)                                   # Autor == Beitragsautor (Groß-/Kleinschreibung egal)
        self.assertEqual(c.created_at, '2025-10-09T08:55:00+00:00')
        top = reddit.comment_from_data({**data, 'parent_id': 't3_abc123', 'author': 'andere'}, 'abc123', 'beispiel_op')
        self.assertIsNone(top.parent_id); self.assertFalse(top.is_author_reply)
        for body in ('[deleted]', '[removed]', '', '   '):
            self.assertIsNone(reddit.comment_from_data({**data, 'body': body}, 'abc123', None), body)
        gone = reddit.comment_from_data({**data, 'author': '[deleted]'}, 'abc123', 'beispiel_op')
        self.assertIsNone(gone.author); self.assertEqual(gone.text, 'Hallo'); self.assertFalse(gone.is_author_reply)

    def test_thread_flattens_nested_replies_and_skips_placeholders(self):
        post, listing = reddit.parse_thread_json(load('reddit_thread.json'))
        thread = reddit.Thread(post['id'], 'beispiel_op')
        thread.add_listing(listing)
        self.assertEqual([c.comment_id for c in thread.comments], ['c1', 'c1a', 'c2a', 'c3'])
        self.assertEqual([c.parent_id for c in thread.comments], [None, 'c1', 'c2', None])
        self.assertEqual(thread.removed, 2)
        self.assertEqual(thread.more, [('children', ['c4', 'c5', 'c6'], 't3_abc123')])

    def test_limit_stops_the_walk(self):
        _post, listing = reddit.parse_thread_json(load('reddit_thread.json'))
        thread = reddit.Thread('abc123'); thread.add_listing(listing, 2)
        self.assertEqual([c.comment_id for c in thread.comments], ['c1', 'c1a'])

    def test_continue_thread_marker_is_queued(self):
        thread = reddit.Thread('abc123')
        thread.add_thing({'kind': 'more', 'data': {'children': [], 'parent_id': 't1_deep', 'id': '_'}})
        thread.add_thing({'kind': 'more', 'data': {'children': [], 'parent_id': 't3_abc123', 'id': '_'}})     # nichts nachzuladen
        thread.add_thing('kein dict'); thread.add_thing({'kind': 't5', 'data': {}})
        self.assertEqual(thread.more, [('thread', 'deep')])

    def test_bad_payloads_raise_value_error(self):
        for payload in (None, {}, [], [{}], [{'data': {'children': []}}, {}]):
            with self.assertRaises(ValueError):
                reddit.parse_thread_json(payload)
        with self.assertRaises(ValueError):
            reddit.morechildren_things({'json': {'errors': [['RATELIMIT', 'x', 'y']], 'data': {'things': []}}})
        with self.assertRaises(ValueError):
            reddit.morechildren_things({})

    def test_listing_links(self):
        links, after = reddit.listing_links(load('reddit_listing.json'))
        self.assertEqual(links, ['https://www.reddit.com/r/python/comments/p1/erster_beitrag/', 'https://www.reddit.com/r/python/comments/p2/zweiter_beitrag/'])
        self.assertEqual(after, 't3_p2')
        self.assertEqual(reddit.listing_links({}), ([], None))

    def test_dom_fallback_structure(self):
        dom = {'post': {'id': 't3_zzz999', 'title': 'Titel', 'author': 'op', 'score': '1,2 Tsd.', 'comments': '5', 'created': '2026-10-01T10:00:00.000Z',
                        'permalink': '/r/test/comments/zzz999/titel/', 'subreddit': 'r/test', 'text': 'Text', 'nsfw': False, 'content_href': None},
               'comments': [{'id': 't1_k1', 'parent': '', 'author': 'a', 'score': '3', 'depth': '0', 'created': '2026-10-01T10:05:00.000Z', 'text': 'Eins'},
                            {'id': 't1_k2', 'parent': 't1_k1', 'author': 'op', 'score': '1', 'depth': '1', 'created': None, 'text': 'Zwei'},
                            {'id': 't1_k3', 'parent': '', 'author': '[deleted]', 'score': '0', 'depth': '0', 'text': '[deleted]'}]}
        # DOM_JS liefert die Kennungen ohne Vorsilbe; hier zusätzlich mit, die Auswertung darf nicht daran scheitern
        dom['comments'] = [{**c, 'id': c['id'].removeprefix('t1_'), 'parent': c['parent'].removeprefix('t1_')} for c in dom['comments']]
        video, thread = reddit.thread_from_dom(dom, 'zzz999')
        self.assertEqual((video.uid, video.like_count, video.comment_count, video.caption), ('reddit:zzz999', 1200, 5, 'Titel\n\nText'))
        self.assertEqual([(c.comment_id, c.parent_id, c.is_author_reply) for c in thread.comments], [('k1', None, False), ('k2', 'k1', True)])
        self.assertEqual(thread.removed, 1)


class FetchTests(Case):
    def test_full_thread_with_more_objects(self):
        result, page = self.fetch()
        self.assertEqual(result.comments_status, 'received')
        self.assertEqual(page.gotos, ['https://www.reddit.com/r/AskReddit/comments/abc123/'])
        self.assertTrue(page.fetched[0].startswith(BASE + '.json?limit=50&sort=top&raw_json=1'))
        self.assertEqual([c.comment_id for c in result.comments], ['c1', 'c1a', 'c2a', 'c3', 'c4', 'c5', 'c6'])
        self.assertEqual({c.comment_id: c.parent_id for c in result.comments},
                         {'c1': None, 'c1a': 'c1', 'c2a': 'c2', 'c3': None, 'c4': None, 'c5': 'c3', 'c6': 'c5'})
        self.assertTrue(next(c for c in result.comments if c.comment_id == 'c1a').is_author_reply)
        self.assertIn('2 gelöschte oder entfernte Kommentare übersprungen', result.message)
        self.assertEqual(result.video.uid, 'reddit:abc123')
        self.assertTrue(any('/api/morechildren.json' in p and 'link_id=t3_abc123' in p and 'children=c4,c5,c6' in p for p in page.fetched))
        self.assertEqual(self.saved[0], ('reddit:abc123', 0))      # erst die Videodaten
        self.assertEqual(self.saved[-1], ('reddit:abc123', 7))
        self.assertEqual(page.fetched.count(page.fetched[0]), 1)

    def test_session_is_acquired_for_reddit_with_the_identity(self):
        page = FakePage()
        ctx = self.ctx(page)
        RedditAdapter().fetch(POST_URL, ctx)
        self.assertEqual(ctx.sessions.calls, [('reddit_main', 'reddit', 'de', False)])

    def test_comment_limit_is_respected(self):
        result, page = self.fetch(max_comments=3)
        self.assertEqual([c.comment_id for c in result.comments], ['c1', 'c1a', 'c2a'])
        self.assertEqual(result.comments_status, 'received')
        self.assertFalse(any('morechildren' in p for p in page.fetched))      # nichts nachgeladen, das nicht gebraucht wird
        self.assertTrue(page.fetched[0].startswith(BASE + '.json?limit=3&'))

    def test_only_video_data(self):
        result, page = self.fetch(max_comments=0)
        self.assertEqual((result.comments_status, result.comments), ('not_requested', []))
        self.assertEqual(result.video.uid, 'reddit:abc123')
        self.assertEqual(len(page.fetched), 1)
        self.assertEqual(self.saved, [('reddit:abc123', 0)])

    def test_post_without_comments_is_empty(self):
        thread = load('reddit_thread.json')
        thread[0]['data']['children'][0]['data']['num_comments'] = 0
        thread[1]['data']['children'] = []
        result, _ = self.fetch(FakePage(thread))
        self.assertEqual((result.comments_status, result.comments), ('empty', []))

    def test_only_removed_comments_is_empty_not_failed(self):
        thread = load('reddit_thread.json')
        thread[1]['data']['children'] = [thread[1]['data']['children'][1]]                     # nur der entfernte c2 mit lebender Antwort
        thread[1]['data']['children'][0]['data']['replies'] = ''
        result, _ = self.fetch(FakePage(thread))
        self.assertEqual(result.comments_status, 'empty')
        self.assertIn('1 gelöschte oder entfernte', result.message)

    def test_nothing_returned_but_comments_expected_is_unavailable(self):
        thread = load('reddit_thread.json')
        thread[1]['data']['children'] = []
        result, _ = self.fetch(FakePage(thread))
        self.assertEqual(result.comments_status, 'unavailable')
        self.assertIn('keine leere', result.message)

    def test_rate_limit_while_loading_more_keeps_what_was_read(self):
        def respond(path):
            if 'morechildren' in path:
                return {'status': 429, 'retry': '60', 'text': 'Too Many Requests'}
        result, page = self.fetch(FakePage(respond=respond))
        self.assertEqual(result.comments_status, 'partial')
        self.assertEqual([c.comment_id for c in result.comments], ['c1', 'c1a', 'c2a', 'c3'])
        self.assertIn('Reddit bremst uns aus', result.message)
        self.assertEqual(self.saved[-1], ('reddit:abc123', 4))

    def test_second_round_of_more_objects(self):
        result, page = self.fetch(FakePage())
        self.assertEqual(len([p for p in page.fetched if 'morechildren' in p and 'children=c6' in p]), 1)      # zweite Runde für das nachgeladene more

    def test_wrong_json_from_more_is_reported_as_partial(self):
        result, _ = self.fetch(FakePage(respond=lambda path: ok({'json': {'errors': [['X', 'y', 'z']], 'data': {}}}) if 'morechildren' in path else None))
        self.assertEqual(result.comments_status, 'partial')
        self.assertEqual(len(result.comments), 4)

    def test_continue_thread_link_is_loaded(self):
        thread = load('reddit_thread.json')
        thread[1]['data']['children'][-1] = {'kind': 'more', 'data': {'children': [], 'parent_id': 't1_c3', 'id': '_', 'count': 0}}

        def respond(path):
            if '/_/c3.json' in path:
                sub = [thread[0], {'kind': 'Listing', 'data': {'children': [{'kind': 't1', 'data': {
                    'id': 'c3', 'parent_id': 't3_abc123', 'author': 'user_b', 'body': 'Der Hobbit & mehr <3', 'score': 120,
                    'replies': {'kind': 'Listing', 'data': {'children': [{'kind': 't1', 'data': {
                        'id': 'tief', 'parent_id': 't1_c3', 'author': 'u', 'body': 'Tief verschachtelt', 'score': 1, 'replies': ''}}]}}}}]}}]
                return ok(sub)
        result, page = self.fetch(FakePage(thread, respond=respond))
        self.assertEqual([c.comment_id for c in result.comments], ['c1', 'c1a', 'c2a', 'c3', 'tief'])
        self.assertIn(BASE.replace('abc123/', 'abc123/_/') + 'c3.json', ''.join(page.fetched))

    def test_no_such_post_and_private_and_rate_limit(self):
        for status, text, needle in ((404, '{"message": "Not Found", "error": 404}', 'nicht gefunden'),
                                     (403, '{"message": "Forbidden", "error": 403, "reason": "private"}', 'privat'),
                                     (403, '{"reason": "quarantined"}', 'privat'), (429, 'Too Many Requests', 'bremst uns aus')):
            with self.assertRaises(PermanentFetchError) as caught:
                self.fetch(FakePage(respond=lambda path, s=status, t=text: {'status': s, 'text': t}))
            self.assertIn(needle, str(caught.exception))

    def test_other_http_errors_are_retried_then_raised(self):
        page = FakePage(respond=lambda path: {'status': 500, 'text': 'kaputt'})
        with self.assertRaises(RuntimeError):
            self.fetch(page)
        self.assertEqual(len(page.fetched), 3)                         # drei Versuche (max_retries)

    def test_connection_error_inside_the_browser(self):
        with self.assertRaisesRegex(RuntimeError, 'Verbindung'):
            self.fetch(FakePage(respond=lambda path: {'error': 'TypeError: Failed to fetch'}))

    def test_block_page_is_reported_clearly_and_not_retried(self):
        page = FakePage(text="You've been blocked by network security.\nIf you think you've been blocked by mistake, file a ticket below")
        with self.assertRaises(PermanentFetchError) as caught:
            self.fetch(page)
        self.assertEqual(str(caught.exception), reddit.MSG_BLOCKED)
        self.assertIn('keine eindeutige Ursache', str(caught.exception))
        self.assertNotIn('Handy-Hotspot', str(caught.exception))
        self.assertEqual(page.fetched, [])                                # kein Versuch, die Sperre zu umgehen
        self.assertEqual(len(page.gotos), 1)

    def test_human_test_page_text(self):
        page = FakePage(text="Prove your humanity\n\nWe're committed to safety and security. But not for bots.\nComplete the challenge below")
        with self.assertRaisesRegex(PermanentFetchError, 'Menschen-Test'):
            self.fetch(page)
        self.assertEqual(page.fetched, [])

    def test_html_challenge_instead_of_json_is_recognised_even_deep_in_the_page(self):
        css = '<style>' + 'a{color:red}' * 6000 + '</style>'
        page = FakePage(respond=lambda path: {'status': 403, 'text': css + '<h1>Prove your humanity</h1>'})
        with self.assertRaisesRegex(PermanentFetchError, 'Menschen-Test'):
            self.fetch(page)
        page = FakePage(respond=lambda path: {'status': 403, 'text': css + "You've been blocked by network security."})
        with self.assertRaisesRegex(PermanentFetchError, 'gesperrt'):
            self.fetch(page)

    def test_unreadable_json_falls_back_to_the_rendered_page(self):
        dom = {'post': {'id': 't3_abc123', 'title': 'Titel', 'author': 'op', 'score': '10', 'comments': '2', 'created': '2026-10-01T10:00:00Z',
                        'permalink': '/r/AskReddit/comments/abc123/titel/', 'subreddit': 'r/AskReddit', 'text': '', 'content_href': None},
               'comments': [{'id': 'k1', 'parent': '', 'author': 'a', 'score': '3', 'depth': '0', 'created': None, 'text': 'Eins'}]}
        page = FakePage(dom=dom, respond=lambda path: {'status': 200, 'text': '<html>kein json</html>'})
        result, _ = self.fetch(page, max_comments=5)
        self.assertEqual(result.video.uid, 'reddit:abc123')
        self.assertEqual([c.comment_id for c in result.comments], ['k1'])
        self.assertEqual(result.comments_status, 'partial')
        self.assertIn('sichtbaren Seite', result.message)

    def test_nothing_usable_at_all(self):
        page = FakePage(dom=None, respond=lambda path: {'status': 200, 'text': 'x'})
        result, _ = self.fetch(page)
        self.assertEqual((result.video, result.comments_status), (None, 'unavailable'))
        self.assertIn('Reddit', result.message)

    def test_share_link_is_resolved_by_the_browser(self):
        url = 'https://www.reddit.com/r/AskReddit/s/AbCdEf1234'
        result, page = self.fetch(FakePage(url='https://www.reddit.com/r/AskReddit/comments/abc123/welches_buch/?share_id=x'), url=url)
        self.assertEqual(page.gotos, [url])
        self.assertEqual(result.video.uid, 'reddit:abc123')
        with self.assertRaisesRegex(PermanentFetchError, 'kein Link zu einem Reddit-Beitrag'):
            self.fetch(FakePage(url='https://www.reddit.com/r/AskReddit/'), url=url)

    def test_other_hosts_and_variants_open_the_canonical_page(self):
        for url in ('https://old.reddit.com/r/AskReddit/comments/abc123/x/', 'https://redd.it/abc123'):
            page = FakePage(respond=lambda path: ok(load('reddit_thread.json')) if path.startswith(('/comments/abc123/.json', BASE)) else None)
            result, page = self.fetch(page, url=url, max_comments=2)
            self.assertTrue(page.gotos[0].startswith('https://www.reddit.com/'), page.gotos)
            self.assertEqual(result.video.uid, 'reddit:abc123')

    def test_invalid_links_and_missing_session(self):
        for url in ('https://www.reddit.com/r/AskReddit/', 'https://evil.com/?u=reddit.com', 'https://i.redd.it/bild.jpg'):
            with self.assertRaisesRegex(ValueError, 'kein Link zu einem Reddit-Beitrag'):
                self.fetch(url=url)
        ctx = self.ctx(FakePage()); ctx.sessions = None
        with self.assertRaisesRegex(ValueError, 'Browser-Session'):
            RedditAdapter().fetch(POST_URL, ctx)

    def test_reddit_is_allowed_without_account_even_with_tor_and_free_proxies(self):
        for mode in ('tor', 'frei'):
            result, _ = self.fetch(max_comments=2, proxy_mode=mode)
            self.assertEqual(result.video.uid, 'reddit:abc123')

    def test_limit_must_be_valid(self):
        with self.assertRaises(ValueError):
            self.fetch(max_comments=-1)

    def test_errors_of_the_save_callback_are_not_swallowed(self):
        ctx = self.ctx(FakePage())
        calls = []

        def boom(video, comments):
            calls.append(len(comments))
            if comments:
                raise OSError('Speichern kaputt')
        ctx.checkpoint = boom
        with self.assertRaisesRegex(OSError, 'Speichern kaputt'):
            RedditAdapter().fetch(POST_URL, ctx)


class DiscoveryTests(Case):
    def single(self):
        data = load('reddit_listing.json')
        data['data']['after'] = None
        return data

    def listing_page(self, pages):
        queue = list(pages)

        def respond(path):
            return ok(queue.pop(0))
        return FakePage(respond=respond)

    def test_subreddit_value_lists_new_posts(self):
        page = self.listing_page([self.single()])
        urls = RedditAdapter().search_urls('r/python', self.ctx(page), 10)
        self.assertEqual(page.gotos, ['https://www.reddit.com/r/python/new/'])
        self.assertTrue(page.fetched[0].startswith('/r/python/new.json?sort=new&limit='))
        self.assertEqual(urls, ['https://www.reddit.com/r/python/comments/p1/erster_beitrag/',
                                'https://www.reddit.com/r/python/comments/p2/zweiter_beitrag/'])

    def test_search_term_uses_search(self):
        page = self.listing_page([self.single()])
        RedditAdapter().search_urls('lofi musik', self.ctx(page), 5)
        self.assertEqual(page.gotos, ['https://www.reddit.com/search/?q=lofi%20musik&sort=new'])
        self.assertTrue(page.fetched[0].startswith('/search.json?q=lofi%20musik&sort=new&type=link&limit='))

    def test_profile_lists_submitted_posts(self):
        page = self.listing_page([self.single()])
        RedditAdapter().profile_urls('u/spez', self.ctx(page), 5)
        self.assertEqual(page.gotos, ['https://www.reddit.com/user/spez/submitted/?sort=new'])
        self.assertTrue(page.fetched[0].startswith('/user/spez/submitted.json?sort=new&limit='))

    def test_pagination_and_limit(self):
        second = {'kind': 'Listing', 'data': {'after': None, 'children': [
            {'kind': 't3', 'data': {'id': 'p3', 'permalink': '/r/python/comments/p3/dritter/'}},
            {'kind': 't3', 'data': {'id': 'p1', 'permalink': '/r/python/comments/p1/erster_beitrag/'}}]}}
        page = self.listing_page([load('reddit_listing.json'), second])
        urls = RedditAdapter().search_urls('r/python', self.ctx(page), 3)
        self.assertEqual([u.split('/comments/')[1].split('/')[0] for u in urls], ['p1', 'p2', 'p3'])      # ohne Doppelte, bis zum Limit
        self.assertIn('&after=t3_p2', page.fetched[1])
        page = self.listing_page([load('reddit_listing.json'), second])
        self.assertEqual(len(RedditAdapter().search_urls('r/python', self.ctx(page), 1)), 1)
        self.assertEqual(len(page.fetched), 1)

    def test_zero_limit_and_bad_values(self):
        page = self.listing_page([])
        self.assertEqual(RedditAdapter().search_urls('r/python', self.ctx(page), 0), [])
        self.assertEqual(page.gotos, [])
        for call, value in (('profile_urls', 'ab'), ('profile_urls', 'a b'), ('search_urls', ''), ('search_urls', '   ')):
            with self.assertRaises(ValueError):
                getattr(RedditAdapter(), call)(value, self.ctx(page), 5)
        with self.assertRaises(ValueError):
            RedditAdapter().search_urls('r/python', self.ctx(page), -1)
        self.assertFalse(hasattr(RedditAdapter, 'hashtag_urls'))

    def test_blocked_listing(self):
        page = FakePage(text='You\'ve been blocked by network security.')
        with self.assertRaisesRegex(PermanentFetchError, 'gesperrt'):
            RedditAdapter().search_urls('r/python', self.ctx(page), 5)


if __name__ == '__main__':
    unittest.main()
