"""X-Adapter: Auswertung der GraphQL-Beispielstruktur, Einbettungsdienst und Abruf mit simulierten Seiten.

WICHTIG: Die GraphQL-Fixtures (tests/fixtures/x_tweetdetail*.json) sind selbst gebaut und NICHT gegen echtes X geprüft.
Die Einbettungs-Fixtures (x_syndication_*.json) haben die Struktur einer echten Antwort, mit erfundenen Inhalten.
"""
import copy
import json
import pathlib
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from socialscraper.adapters import x as xmod
from socialscraper.adapters.base import ScrapeContext
from socialscraper.adapters.x import XAdapter
from socialscraper.config import Settings
from socialscraper.net import proxymode
from socialscraper.net.retry import PermanentFetchError
from socialscraper.net.session_guard import SessionExpired

FIXTURES = pathlib.Path(__file__).parent / 'fixtures'
URL = 'https://x.com/beispiel_konto/status/1000'


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


class FakeResponse:
    def __init__(self, op, payload, broken=False):
        self.url = f'https://x.com/i/api/graphql/AbCdEf/{op}?variables=%7B%7D'
        self.payload, self.broken = payload, broken

    def json(self):
        if self.broken:
            raise ValueError('kein JSON')
        return self.payload


class FakePage:
    """Jedes wait_for_timeout liefert die nächste Gruppe angefangener Antworten aus (wie die Seite beim Laden/Scrollen)."""

    def __init__(self, batches=(), logged_out=False, text='', dom=None, links=None, url=URL):
        self.batches, self.logged_out, self.text, self.dom, self.links = list(batches), logged_out, text, dom or [], links or []
        self.url = url
        self.handlers, self.gotos, self.removed, self.clicks = [], [], 0, 0

    def on(self, event, handler):
        assert event == 'response'
        self.handlers.append(handler)

    def remove_listener(self, event, handler):
        self.removed += 1
        self.handlers.remove(handler)

    def goto(self, url, **kwargs):
        self.gotos.append(url)

    def wait_for_timeout(self, ms):
        if self.batches:
            for response in self.batches.pop(0):
                for handler in list(self.handlers):
                    handler(response)

    def evaluate(self, script, arg=None):
        if script == xmod.LOGGED_OUT_JS:
            return self.logged_out
        if script == xmod.TEXT_JS:
            return self.text
        if script == xmod.DOM_TWEETS_JS:
            return self.dom
        if script == xmod.LINKS_JS:
            return self.links
        if script == xmod.CLICK_MORE_JS:
            self.clicks += 1
            return False
        raise AssertionError('Unerwartetes Skript: ' + str(script)[:60])


class FakePool:
    def __init__(self, page):
        self.page, self.calls = page, []

    @contextmanager
    def acquire(self, *args):
        self.calls.append(args)
        yield self.page


def detail(name='x_tweetdetail.json', op='TweetDetail'):
    return FakeResponse(op, load(name))


class Case(unittest.TestCase):
    def setUp(self):
        for target in ('socialscraper.adapters.x.humanize.scroll', 'socialscraper.net.retry.time.sleep', 'socialscraper.net.notify.notify'):
            p = patch(target); p.start(); self.addCleanup(p.stop)
        self.saved = []

    def ctx(self, page=None, max_comments=50, authenticated=True, **kw):
        require_auth = kw.pop('require_auth', False)
        ctx = ScrapeContext(cfg=Settings(max_comments=max_comments, **kw), store=None, im=None,
                            sessions=FakePool(page) if page is not None else None,
                            identity='x_main', country='de', authenticated=authenticated, require_auth=require_auth)
        ctx.checkpoint = lambda video, comments: self.saved.append((video.uid, len(comments)))
        return ctx

    def fetch(self, page, url=URL, **kw):
        return XAdapter().fetch(url, self.ctx(page, **kw))


class ParsingTests(unittest.TestCase):
    def tweets(self):
        return {t['rest_id']: xmod.extract_tweet(t) for t in xmod.iter_tweets(load('x_tweetdetail.json'))}

    def test_walker_finds_tweets_in_document_order_through_wrappers_and_modules(self):
        ids = [t['rest_id'] for t in xmod.iter_tweets(load('x_tweetdetail.json'))]
        self.assertEqual(ids, ['900', '1000', '1001', '1002', '1003', '1004', '5000'])       # 777 (zitiert) und der Tombstone sind keine eigenen Treffer
        self.assertEqual(list(xmod.iter_tweets({'a': [1, None, {'b': 'c'}]})), [])
        self.assertEqual(list(xmod.iter_tweets(None)), [])

    def test_walker_is_safe_against_very_deep_structures(self):
        node = {}
        for _ in range(500):
            node = {'x': node}
        self.assertEqual(list(xmod.iter_tweets(node)), [])

    def test_long_text_media_counts_and_entities_of_the_main_tweet(self):
        info = self.tweets()['1000']
        self.assertTrue(info['text'].startswith('Dies ist ein sehr langer Beitrag'))                # note_tweet statt abgeschnittenem Text
        self.assertIn('https://example.org/lang', info['text']); self.assertNotIn('t.co', info['text'])
        self.assertEqual((info['handle'], info['name'], info['author_id']), ('beispiel_konto', 'Beispiel Konto', '42'))
        self.assertEqual((info['likes'], info['replies'], info['retweets'], info['quotes'], info['bookmarks'], info['views']),
                         (250, 6, 30, 4, 12, 12345))
        self.assertEqual(info['created_at'], '2026-10-07T12:30:15+00:00')
        self.assertEqual((info['hashtags'], info['mentions']), (['Beispiel'], ['vorgaenger']))
        self.assertEqual(info['media_types'], ['photo', 'video'])
        self.assertEqual(info['thumbnail'], 'https://pbs.twimg.com/media/beispiel1.jpg')
        self.assertEqual(info['duration'], 15.5)
        self.assertEqual(info['quoted'], {'id': '777', 'author': 'zitiert'})
        self.assertEqual((info['reply_to'], info['conversation_id'], info['language']), ('900', '900', 'de'))

    def test_old_and_new_user_layout(self):
        tweets = self.tweets()
        self.assertEqual(tweets['1001']['handle'], 'antwort_eins')            # legacy.screen_name
        self.assertEqual(tweets['1000']['handle'], 'beispiel_konto')          # core.screen_name

    def test_reply_text_drops_leading_names_and_trailing_links(self):
        tweets = self.tweets()
        self.assertEqual(tweets['1001']['text'], 'Erste Antwort')
        self.assertEqual(tweets['1002']['text'], 'Verschachtelte Antwort')
        self.assertEqual(tweets['1004']['text'], 'Ohne führende Namen, aber mit #Tag')

    def test_text_helper_edge_cases(self):
        clean = xmod._clean_text
        self.assertEqual(clean('Hallo https://t.co/a', {'urls': [{'url': 'https://t.co/a', 'expanded_url': 'https://example.org'}]}),
                         'Hallo https://example.org')
        self.assertEqual(clean('Bild https://t.co/m', {'media': [{'url': 'https://t.co/m'}]}), 'Bild')
        self.assertEqual(clean('abc', None, [5, 2]), 'abc')                                  # unsinniger Bereich wird ignoriert
        self.assertEqual(clean('abc', None, [0, 99]), 'abc')
        self.assertEqual(clean(None, {}), '')
        self.assertEqual(clean('a', {'urls': ['kein dict', {'url': None}], 'media': 7}), 'a')

    def test_times(self):
        self.assertEqual(xmod.to_iso('Wed Oct 10 20:19:24 +0000 2018'), '2018-10-10T20:19:24+00:00')
        self.assertEqual(xmod.to_iso('Wed Oct 10 20:19:24 +0200 2018'), '2018-10-10T18:19:24+00:00')
        self.assertEqual(xmod.to_iso('2006-03-21T20:50:14.000Z'), '2006-03-21T20:50:14+00:00')
        self.assertEqual(xmod.to_iso(1760000000), '2025-10-09T08:53:20+00:00')
        for bad in (None, '', 'gestern', 'Wed Foo 10 20:19:24 +0000 2018', True):
            self.assertIsNone(xmod.to_iso(bad), bad)

    def test_thread_excludes_ancestors_and_foreign_conversations(self):
        main, replies = xmod.thread_from_payloads([load('x_tweetdetail.json')], '1000')
        self.assertEqual(main['id'], '1000')
        self.assertEqual([r['id'] for r in replies], ['1001', '1002', '1003', '1004'])        # weder 900 (davor) noch 5000 (Empfehlung)
        self.assertEqual(xmod.thread_from_payloads([load('x_tweetdetail.json')], '4711'), (None, []))
        self.assertEqual(xmod.thread_from_payloads([], '1000'), (None, []))

    def test_thread_merges_pages_without_duplicates(self):
        main, replies = xmod.thread_from_payloads([load('x_tweetdetail.json'), load('x_tweetdetail_more.json')], '1000')
        self.assertEqual([r['id'] for r in replies], ['1001', '1002', '1003', '1004', '1006', '1007'])

    def test_video_mapping(self):
        main, _ = xmod.thread_from_payloads([load('x_tweetdetail.json')], '1000')
        v = xmod.video_from_info(main)
        self.assertEqual((v.platform, v.video_id, v.uid, v.url), ('x', '1000', 'x:1000', 'https://x.com/beispiel_konto/status/1000'))
        self.assertEqual((v.author, v.author_id, v.language), ('beispiel_konto', '42', 'de'))
        self.assertEqual((v.view_count, v.like_count, v.comment_count, v.share_count, v.save_count), (12345, 250, 6, 34, 12))
        self.assertEqual((v.hashtags, v.mentions, v.duration_s), (['Beispiel'], ['vorgaenger'], 15.5))
        self.assertEqual(v.thumbnail, 'https://pbs.twimg.com/media/beispiel1.jpg')
        self.assertEqual((v.raw['retweet_count'], v.raw['quote_count']), (30, 4))              # Aufteilung von share_count
        self.assertEqual(v.raw['media'], ['photo', 'video']); self.assertIs(v.raw['is_reply'], True)
        self.assertEqual(v.raw['quoted'], {'id': '777', 'author': 'zitiert'}); self.assertEqual(v.raw['source'], 'browser')

    def test_comment_mapping(self):
        main, replies = xmod.thread_from_payloads([load('x_tweetdetail.json')], '1000')
        comments = {r['id']: xmod.comment_from_info(r, main) for r in replies}
        c = comments['1001']
        self.assertEqual((c.platform, c.uid, c.video_id, c.text, c.author, c.author_id), ('x', 'x:1001', '1000', 'Erste Antwort', 'antwort_eins', '51'))
        self.assertEqual((c.parent_id, c.like_count, c.reply_count, c.language), (None, 9, 1, 'de'))       # direkte Antwort -> kein Elternteil
        self.assertEqual(comments['1002'].parent_id, '1001')
        self.assertEqual(comments['1003'].parent_id, '1002')
        self.assertTrue(comments['1003'].is_author_reply)                                                  # Autorin selbst (Schreibweise egal)
        self.assertFalse(comments['1001'].is_author_reply)
        self.assertEqual(comments['1001'].created_at, '2026-10-07T12:35:00+00:00')

    def test_video_without_counts_has_no_share_count(self):
        v = xmod.video_from_info({'id': '5', 'handle': None, 'text': 'x'}, 'fallback')
        self.assertIsNone(v.share_count); self.assertEqual((v.author, v.url), ('fallback', 'https://x.com/fallback/status/5'))
        self.assertEqual(xmod.video_from_info({'id': '5'}).url, 'https://x.com/i/status/5')
        with self.assertRaises(ValueError):
            xmod.video_from_info({'text': 'ohne id'})

    def test_tombstone_detection(self):
        self.assertEqual(xmod.find_tombstone(load('x_tweetdetail.json')), 'Dieser Beitrag ist nicht verfügbar.')
        self.assertIsNone(xmod.find_tombstone({'a': [{'b': 1}]}))
        self.assertEqual(xmod.find_tombstone({'__typename': 'TweetUnavailable', 'reason': 'Protected'}), 'Protected')

    def test_weird_tweet_objects_do_not_crash(self):
        weird = {'rest_id': '9', 'legacy': {'full_text': None, 'favorite_count': 'viele', 'created_at': 5, 'entities': 'x', 'extended_entities': {'media': ['x', None]}},
                 'core': {'user_results': {'result': {'tweet': {'a': 1}}}}, 'views': {'count': 'abc'}, 'quoted_status_result': 'nope'}
        info = xmod.extract_tweet(weird)
        self.assertEqual((info['id'], info['text'], info['likes'], info['views'], info['handle']), ('9', '', None, None, None))


class SyndicationTests(unittest.TestCase):
    def test_token_is_derived_from_the_id(self):
        self.assertEqual(xmod.syndication_token('1585841080431321088'), '3ue2efeb2fpvq')
        self.assertRegex(xmod.syndication_token('20'), r'^[0-9a-z]+$')
        self.assertTrue(xmod.syndication_token('1'))

    def test_photo_tweet(self):
        info = xmod.extract_syndication(load('x_syndication_photo.json'))
        self.assertEqual((info['id'], info['handle'], info['author_id'], info['text']), ('1500000000000000001', 'beispiel_konto', '4242', 'Beispieltext mit Bild.'))
        self.assertEqual((info['likes'], info['replies']), (1234, 56))
        self.assertEqual(info['media_types'], ['photo']); self.assertTrue(info['thumbnail'].endswith('beispielbild.jpg'))
        self.assertEqual(info['created_at'], '2012-11-07T04:16:18+00:00')
        v = xmod.video_from_info(info)
        self.assertEqual((v.url, v.share_count, v.view_count, v.raw['source']), ('https://x.com/beispiel_konto/status/1500000000000000001', None, None, 'syndication'))

    def test_text_tweet_with_entities(self):
        info = xmod.extract_syndication(load('x_syndication_text.json'))
        self.assertEqual(info['text'], 'Das ist #Beispiel @jemand https://example.org/x')
        self.assertEqual((info['hashtags'], info['mentions']), (['Beispiel'], ['jemand']))
        self.assertEqual((info['likes'], info['replies'], info['language']), (77, 3, 'de'))


class FakeHttp:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def get(self, url, params=None, headers=None, timeout=None, proxies=None):
        self.calls.append((url, dict(params), proxies))
        status, body = self.answers.pop(0)
        return type('R', (), {'status_code': status, 'json': lambda s: body if not isinstance(body, Exception) else (_ for _ in ()).throw(body)})()


class PublicFetchTests(Case):
    def fetch_public(self, http, max_comments=50, url=URL, **kw):
        with patch('requests.get', http.get):
            return XAdapter().fetch(url, self.ctx(None, max_comments, authenticated=False, **kw))

    def test_without_login_only_the_post_without_replies(self):
        http = FakeHttp((200, load('x_syndication_photo.json')))
        result = self.fetch_public(http, url='https://twitter.com/i/web/status/1500000000000000001?s=20&t=abc')
        self.assertEqual(result.video.uid, 'x:1500000000000000001')
        self.assertEqual((result.comments_status, result.comments), ('unavailable', []))
        self.assertIn('Antworten brauchen eine Anmeldung', result.message)
        self.assertIn('ohne Antworten', result.message)
        self.assertEqual(self.saved, [('x:1500000000000000001', 0)])
        url, params, proxies = http.calls[0]
        self.assertEqual(url, 'https://cdn.syndication.twimg.com/tweet-result')
        self.assertEqual((params['id'], params['lang']), ('1500000000000000001', 'de')); self.assertTrue(params['token'])

    def test_only_video_data_requested(self):
        result = self.fetch_public(FakeHttp((200, load('x_syndication_text.json'))), max_comments=0)
        self.assertEqual(result.comments_status, 'not_requested')
        self.assertIn('ohne Anmeldung', result.message)

    def test_second_token_is_tried_when_the_first_is_refused(self):
        http = FakeHttp((403, {}), (200, load('x_syndication_text.json')))
        result = self.fetch_public(http)
        self.assertEqual(result.video.uid, 'x:1500000000000000002')
        self.assertEqual([c[1]['token'] for c in http.calls][1], 'a')

    def test_tombstone_and_not_found(self):
        result = self.fetch_public(FakeHttp((200, load('x_syndication_tombstone.json'))))
        self.assertEqual((result.video, result.comments_status), (None, 'unavailable'))
        self.assertIn('nicht abrufbar', result.message)
        with self.assertRaisesRegex(PermanentFetchError, 'nicht abrufbar'):
            self.fetch_public(FakeHttp((404, {})))

    def test_rate_limit_and_garbage(self):
        with self.assertRaisesRegex(PermanentFetchError, 'bremst uns aus'):
            self.fetch_public(FakeHttp((429, {})))
        with self.assertRaisesRegex(PermanentFetchError, 'keine lesbaren Daten'):
            self.fetch_public(FakeHttp((200, ValueError('x')), (200, ValueError('x'))))
        with self.assertRaisesRegex(PermanentFetchError, 'abgelehnt \\(HTTP 500\\)'):
            self.fetch_public(FakeHttp((500, {}), (500, {})))

    def test_require_auth_refuses_the_public_path(self):
        http = FakeHttp()
        with patch('requests.get', http.get), self.assertRaisesRegex(SessionExpired, 'bestätigte Anmeldung'):
            XAdapter().fetch(URL, self.ctx(None, authenticated=False, require_auth=True))       # --require-auth des Abrufs
        ctx = self.ctx(None, authenticated=False)
        ctx.cfg.require_auth = True                                                              # Einstellung require_auth
        with patch('requests.get', http.get), self.assertRaises(SessionExpired):
            XAdapter().fetch(URL, ctx)
        self.assertEqual(http.calls, [])

    def test_free_proxy_and_tor_always_refuse_x(self):
        for mode in ('frei', 'tor'):
            with self.assertRaisesRegex(proxymode.AccountsForbidden, 'X braucht immer ein Konto'):
                XAdapter().fetch(URL, self.ctx(None, authenticated=False, proxy_mode=mode))

    def test_bad_links(self):
        with self.assertRaisesRegex(PermanentFetchError, 't.co'):
            XAdapter().fetch('https://t.co/AbCdEf12', self.ctx(None, authenticated=False))
        for url in ('https://x.com/beispiel', 'https://fxtwitter.com/a/status/1', 'https://evil.com/?u=x.com'):
            with self.assertRaisesRegex(ValueError, 'kein Link zu einem Beitrag auf X'):
                XAdapter().fetch(url, self.ctx(None, authenticated=False))


class BrowserFetchTests(Case):
    def test_thread_with_replies_from_intercepted_graphql(self):
        page = FakePage([[detail()]])
        result = self.fetch(page)
        self.assertEqual(page.gotos, [URL])
        self.assertEqual(result.video.uid, 'x:1000')
        self.assertEqual([c.comment_id for c in result.comments], ['1001', '1002', '1003', '1004'])
        self.assertEqual(result.comments_status, 'partial')                                 # X meldet 6 Antworten, 4 sind sichtbar
        self.assertEqual(self.saved[0], ('x:1000', 0)); self.assertEqual(self.saved[-1], ('x:1000', 4))
        self.assertEqual(page.removed, 1)                                                   # Lauscher wird immer entfernt
        self.assertEqual(page.handlers, [])

    def test_scrolling_loads_more_replies_until_the_limit(self):
        page = FakePage([[detail()], [detail('x_tweetdetail_more.json')]])
        result = self.fetch(page)
        self.assertEqual([c.comment_id for c in result.comments], ['1001', '1002', '1003', '1004', '1006', '1007'])
        self.assertEqual(result.comments_status, 'received')                                # 6 von 6 gemeldeten Antworten
        page = FakePage([[detail()], [detail('x_tweetdetail_more.json')]])
        result = self.fetch(page, max_comments=5)
        self.assertEqual(len(result.comments), 5); self.assertEqual(result.comments_status, 'received')

    def test_only_video_data(self):
        page = FakePage([[detail()]])
        result = self.fetch(page, max_comments=0)
        self.assertEqual((result.comments_status, result.comments), ('not_requested', []))
        self.assertEqual(self.saved, [('x:1000', 0)])

    def test_main_tweet_from_single_result_when_thread_is_missing(self):
        single = FakeResponse('TweetResultByRestId', {'data': {'tweetResult': {'result': next(
            t for t in xmod.iter_tweets(load('x_tweetdetail.json')) if t['rest_id'] == '1000')}}})
        result = self.fetch(FakePage([[single]]), max_comments=0)
        self.assertEqual(result.video.uid, 'x:1000')

    def test_stalls_after_three_empty_scrolls(self):
        page = FakePage([[detail()]])
        result = self.fetch(page)
        self.assertGreaterEqual(page.clicks, 3); self.assertLessEqual(page.clicks, 5)

    def test_unreadable_response_is_skipped(self):
        page = FakePage([[FakeResponse('TweetDetail', None, broken=True), detail()]])
        self.assertEqual(self.fetch(page).video.uid, 'x:1000')

    def test_other_graphql_calls_are_ignored(self):
        other = FakeResponse('HomeTimeline', load('x_tweetdetail.json'))
        page = FakePage([[other]], dom=[])
        result = self.fetch(page)
        self.assertEqual((result.video, result.comments_status), (None, 'unavailable'))

    def test_logged_out_page_raises_session_expired(self):
        page = FakePage([[detail()]], logged_out=True)
        ctx = self.ctx(page)
        with patch('socialscraper.net.session_guard.invalidate') as invalidate, self.assertRaises(SessionExpired):
            XAdapter().fetch(URL, ctx)
        invalidate.assert_called_with('x_main', 'x')
        self.assertFalse(ctx.authenticated)
        self.assertEqual(page.handlers, [])

    def test_tombstone_means_not_available(self):
        payload = {'data': {'x': {'result': {'__typename': 'TweetTombstone', 'tombstone': {'text': {'text': 'Dieser Beitrag wurde gelöscht.'}}}}}}
        result = self.fetch(FakePage([[FakeResponse('TweetDetail', payload)]]))
        self.assertEqual((result.video, result.comments_status), (None, 'unavailable'))
        self.assertIn('gelöscht', result.message); self.assertIn('nicht abrufbar', result.message)

    def test_locked_account_rate_limit_and_challenge_messages(self):
        for text, needle in (('Your account has been locked. Unlock', 'eingeschränkt'), ('Rate limit exceeded', 'bremst uns aus'),
                             ('Bitte löse diese Aufgabe: arkose labs', 'Sicherheitsabfrage'), ('Konto gesperrt', 'eingeschränkt')):
            with self.assertRaises(PermanentFetchError) as caught:
                self.fetch(FakePage([], text=text))
            self.assertIn(needle, str(caught.exception))

    def test_problem_texts_are_ignored_when_data_arrived(self):
        page = FakePage([[detail()]], text='Rate limit exceeded')                         # steht nur zufällig im Text eines Beitrags
        self.assertEqual(self.fetch(page).video.uid, 'x:1000')

    def test_dom_fallback_reads_visible_posts(self):
        dom = [{'id': '1000', 'handle': 'beispiel_konto', 'name': 'Beispiel Konto', 'text': 'Hauptbeitrag #Test', 'time': '2026-10-07T12:30:15.000Z',
                'replies': '2', 'retweets': '1,2 Tsd.', 'likes': '15', 'views': '3,4 Mio.', 'media': True},
               {'id': '1001', 'handle': 'antwort_eins', 'name': 'A', 'text': 'Erste', 'time': '2026-10-07T12:35:00.000Z', 'replies': '', 'retweets': '', 'likes': '1', 'views': '', 'media': False},
               {'id': '1002', 'handle': 'Beispiel_Konto', 'name': 'B', 'text': 'Autorin selbst', 'time': None, 'replies': '', 'retweets': '', 'likes': '', 'views': '', 'media': False},
               {'id': '1003', 'handle': 'leer', 'name': 'C', 'text': '', 'time': None, 'replies': '', 'retweets': '', 'likes': '', 'views': '', 'media': False}]
        result = self.fetch(FakePage([], dom=dom))
        self.assertEqual((result.video.uid, result.video.like_count, result.video.view_count, result.video.hashtags), ('x:1000', 15, 3400000, ['Test']))
        self.assertEqual(result.video.raw['source'], 'dom')
        self.assertEqual([(c.comment_id, c.is_author_reply, c.parent_id) for c in result.comments], [('1001', False, None), ('1002', True, None)])
        self.assertIn('sichtbaren Seite', result.message)

    def test_pool_is_asked_for_the_x_session(self):
        page = FakePage([[detail()]])
        ctx = self.ctx(page)
        XAdapter().fetch(URL, ctx)
        self.assertEqual(ctx.sessions.calls, [('x_main', 'x', 'de', False)])

    def test_missing_session_pool(self):
        with self.assertRaisesRegex(ValueError, 'Browser-Session'):
            XAdapter().fetch(URL, self.ctx(None))

    def test_errors_of_the_save_callback_are_not_swallowed(self):
        ctx = self.ctx(FakePage([[detail()] for _ in range(10)]))      # genug Antworten auch für die Wiederholungen

        def boom(video, comments):
            if comments:
                raise OSError('Speichern kaputt')
        ctx.checkpoint = boom
        with self.assertRaisesRegex(OSError, 'Speichern kaputt'):
            XAdapter().fetch(URL, ctx)

    def test_engine_without_response_events_is_refused_with_a_clear_message(self):
        class Shim:   # Nodriver-Seite: kein page.on
            url = URL

            def goto(self, *args, **kwargs):
                pass

            def wait_for_timeout(self, ms):
                pass

            def evaluate(self, *args, **kwargs):
                return False
        from socialscraper.net.retry import PermanentFetchError
        with self.assertRaises(PermanentFetchError) as ctx:
            self.fetch(Shim())
        self.assertIn('Patchright', str(ctx.exception))


class DiscoveryTests(Case):
    def user_tweets(self):
        def tweet(i, handle, extra=None):
            legacy = {'full_text': f'Text {i}', 'created_at': 'Wed Oct 07 12:30:15 +0000 2026', 'conversation_id_str': str(i), **(extra or {})}
            return {'itemContent': {'tweet_results': {'result': {'__typename': 'Tweet', 'rest_id': str(i), 'legacy': legacy,
                    'core': {'user_results': {'result': {'rest_id': '1', 'legacy': {'screen_name': handle}}}}}}}}
        retweet = tweet(3, 'beispiel_konto', {'retweeted_status_result': {'result': next(
            t for t in xmod.iter_tweets(load('x_tweetdetail.json')) if t['rest_id'] == '1001')}})
        return {'data': {'user': {'result': {'timeline': {'timeline': {'instructions': [{'entries': [
            {'content': tweet(1, 'beispiel_konto')}, {'content': tweet(2, 'Beispiel_Konto')}, {'content': retweet}, {'content': tweet(4, 'anderer')},
            {'content': tweet(1, 'beispiel_konto')}]}]}}}}}}

    def test_profile_collects_only_own_original_posts(self):
        page = FakePage([[FakeResponse('UserTweets', self.user_tweets())]])
        urls = XAdapter().profile_urls('@Beispiel_Konto', self.ctx(page), 10)
        self.assertEqual(page.gotos, ['https://x.com/Beispiel_Konto'])
        self.assertEqual(urls, ['https://x.com/beispiel_konto/status/1', 'https://x.com/Beispiel_Konto/status/2'])
        self.assertEqual(page.handlers, [])

    def test_search_and_hashtag_pages(self):
        page = FakePage([[FakeResponse('SearchTimeline', self.user_tweets())]])
        urls = XAdapter().search_urls('lofi hip hop', self.ctx(page), 2)
        self.assertEqual(page.gotos, ['https://x.com/search?q=lofi%20hip%20hop&src=typed_query&f=live'])
        self.assertEqual(len(urls), 2)
        page = FakePage([[FakeResponse('SearchTimeline', self.user_tweets())]])
        urls = XAdapter().hashtag_urls('#tanzen', self.ctx(page), 10)
        self.assertEqual(page.gotos, ['https://x.com/search?q=%23tanzen&src=hashtag_click&f=live'])
        self.assertIn('https://x.com/anderer/status/4', urls)                              # Suche: auch fremde Konten

    def test_dom_fallback_for_links(self):
        page = FakePage([], links=['/beispiel_konto/status/11', '/anderer/status/12', '/beispiel_konto/status/11', '/i/status/9', None])
        urls = XAdapter().profile_urls('beispiel_konto', self.ctx(page), 10)
        self.assertEqual(urls, ['https://x.com/beispiel_konto/status/11'])

    def test_login_is_required(self):
        page = FakePage([])
        with self.assertRaises(SessionExpired):
            XAdapter().search_urls('lofi', self.ctx(page, authenticated=False), 5)
        self.assertEqual(page.gotos, [])
        with self.assertRaises(SessionExpired):
            XAdapter().search_urls('lofi', self.ctx(FakePage([], logged_out=True)), 5)

    def test_nothing_found_reports_problem_text(self):
        with self.assertRaisesRegex(PermanentFetchError, 'bremst uns aus'):
            XAdapter().search_urls('lofi', self.ctx(FakePage([], text='Rate limit exceeded')), 5)
        self.assertEqual(XAdapter().search_urls('lofi', self.ctx(FakePage([], text='Keine Ergebnisse')), 5), [])

    def test_bad_values_and_limits(self):
        page = FakePage([])
        for call, value in (('profile_urls', 'name.mit.punkt'), ('profile_urls', 'zu_langer_kontoname_x'), ('search_urls', ' '), ('hashtag_urls', 'a b')):
            with self.assertRaises(ValueError):
                getattr(XAdapter(), call)(value, self.ctx(page), 5)
        self.assertEqual(XAdapter().search_urls('lofi', self.ctx(page), 0), [])
        with self.assertRaises(ValueError):
            XAdapter().search_urls('lofi', self.ctx(page), -1)


if __name__ == '__main__':
    unittest.main()
