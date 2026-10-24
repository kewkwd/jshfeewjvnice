"""Die Browser-Skripte für Reddit und X gegen lokale Seiten in einem echten (unsichtbaren) Chrome.

Nur mit SS_RUN_LOCAL_BROWSER_TESTS=1. Alle Anfragen werden lokal beantwortet (page.route); es gibt kein Netz und keine Anmeldung.
Die Seitenstrukturen sind nach dem Wissen über Reddit (Shreddit) und X gebaut, NICHT gegen die echten Seiten geprüft.
"""
import json
import os
import pathlib
import unittest

from socialscraper.adapters import reddit, x
from socialscraper.adapters.reddit import RedditAdapter
from socialscraper.net import session_guard
from socialscraper.net.retry import PermanentFetchError

FIXTURES = pathlib.Path(__file__).parent / 'fixtures'


@unittest.skipUnless(os.environ.get('SS_RUN_LOCAL_BROWSER_TESTS') == '1', 'SS_RUN_LOCAL_BROWSER_TESTS=1 nicht gesetzt')
class BrowserBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Lokale Browserprüfung braucht patchright')
        cls.engine = sync_playwright().start()
        try:
            cls.browser = cls.engine.chromium.launch(channel='chrome', headless=True)      # unsichtbar
        except Exception as error:
            cls.engine.stop()
            raise unittest.SkipTest('Lokales Chrome ist nicht verfügbar') from error
        cls.context = cls.browser.new_context()

    @classmethod
    def tearDownClass(cls):
        cls.context.close(); cls.browser.close(); cls.engine.stop()

    def setUp(self):
        self.page = self.context.new_page()
        self.addCleanup(self.page.close)
        self.routes = {}

        def handle(route):
            url = route.request.url
            for needle in sorted(self.routes, key=len, reverse=True):      # der genaueste Pfad gewinnt
                if needle in url:
                    status, content_type, body = self.routes[needle]
                    return route.fulfill(status=status, content_type=content_type, body=body)
            route.fulfill(status=200, content_type='text/html', body='<html><body><p>leer</p></body></html>')
        self.page.route('**/*', handle)

    def html(self, url, body):
        self.routes[url.split('//', 1)[-1]] = (200, 'text/html', f'<html><body>{body}</body></html>')


class RedditScriptTests(BrowserBase):
    def test_fetch_script_returns_json_with_status(self):
        self.html('www.reddit.com/r/AskReddit/comments/abc123/', '<p>Seite</p>')
        self.routes['www.reddit.com/r/AskReddit/comments/abc123/.json'] = (200, 'application/json', (FIXTURES / 'reddit_thread.json').read_text(encoding='utf-8'))
        self.page.goto('https://www.reddit.com/r/AskReddit/comments/abc123/')
        data = RedditAdapter.fetch_json(self.page, '/r/AskReddit/comments/abc123/.json?limit=5&sort=top&raw_json=1')
        post, listing = reddit.parse_thread_json(data)
        self.assertEqual(post['id'], 'abc123')
        self.assertEqual(len(listing['data']['children']), 4)

    def test_fetch_script_statuses_and_block_pages(self):
        self.html('www.reddit.com/start', '<p>Seite</p>')
        self.page.goto('https://www.reddit.com/start')
        for status, body, needle in ((404, '{"error": 404}', 'nicht gefunden'), (429, 'Too Many Requests', 'bremst uns aus'),
                                     (403, '<html>' + 'x' * 50000 + 'Prove your humanity</html>', 'Menschen-Test'),
                                     (403, '<html>You\'ve been blocked by network security.</html>', 'gesperrt'),
                                     (403, '{"reason": "private"}', 'privat')):
            self.routes['www.reddit.com/api/test'] = (status, 'text/html', body)
            with self.assertRaises(PermanentFetchError, msg=status) as caught:
                RedditAdapter.fetch_json(self.page, '/api/test')
            self.assertIn(needle, str(caught.exception))

    def test_fetch_script_reports_network_errors(self):
        self.html('www.reddit.com/start', '<p>Seite</p>')
        self.page.goto('https://www.reddit.com/start')
        self.page.route('**/api/kaputt', lambda route: route.abort())
        with self.assertRaisesRegex(RuntimeError, 'Verbindung'):
            RedditAdapter.fetch_json(self.page, '/api/kaputt')

    def test_text_script_and_block_detection(self):
        self.html('www.reddit.com/blocked', '<h1>You\'ve been blocked by network security.</h1>')
        self.page.goto('https://www.reddit.com/blocked')
        self.assertEqual(RedditAdapter.blocked_message(self.page), reddit.MSG_BLOCKED)
        self.html('www.reddit.com/human', '<h1>Prove your humanity</h1><p>Complete the challenge below</p>')
        self.page.goto('https://www.reddit.com/human')
        self.assertEqual(RedditAdapter.blocked_message(self.page), reddit.MSG_CAPTCHA)
        self.html('www.reddit.com/normal', '<h1>Ein Beitrag über Captchas?</h1>')
        self.page.goto('https://www.reddit.com/normal')
        self.assertEqual(RedditAdapter.blocked_message(self.page), '')                           # ein Wort wie „Captchas“ im Titel sperrt nichts

    def test_dom_script_reads_shreddit_elements(self):
        self.html('www.reddit.com/r/test/comments/zzz999/', (
            '<shreddit-post id="t3_zzz999" post-title="Titel" author="op" score="1200" comment-count="2" created-timestamp="2026-10-01T10:00:00.000Z" '
            'permalink="/r/test/comments/zzz999/titel/" subreddit-name="test"><div slot="text-body">Text des Beitrags</div></shreddit-post>'
            '<shreddit-comment thingid="t1_k1" parentid="" author="a" score="3" depth="0"><div slot="comment">Eins</div><time datetime="2026-10-01T10:05:00.000Z"></time></shreddit-comment>'
            '<shreddit-comment thingid="t1_k2" parentid="t1_k1" author="op" score="1" depth="1"><div slot="comment">Zwei</div></shreddit-comment>'))
        self.page.goto('https://www.reddit.com/r/test/comments/zzz999/')
        dom = self.page.evaluate(reddit.DOM_JS)
        video, thread = reddit.thread_from_dom(dom, None)
        self.assertEqual((video.uid, video.author, video.like_count, video.comment_count, video.caption), ('reddit:zzz999', 'op', 1200, 2, 'Titel\n\nText des Beitrags'))
        self.assertEqual([(c.comment_id, c.parent_id, c.is_author_reply, c.text) for c in thread.comments], [('k1', None, False, 'Eins'), ('k2', 'k1', True, 'Zwei')])

    def test_dom_script_without_post(self):
        self.html('www.reddit.com/leer', '<p>nichts</p>')
        self.page.goto('https://www.reddit.com/leer')
        self.assertIsNone(self.page.evaluate(reddit.DOM_JS))

    def test_probe_script(self):
        script = session_guard.PROBE_JS['reddit']
        self.html('www.reddit.com/', '<p>Start</p>')
        self.page.goto('https://www.reddit.com/')
        self.routes['www.reddit.com/api/me.json'] = (200, 'application/json', json.dumps({'kind': 't2', 'data': {'name': 'beispiel'}}))
        self.assertEqual(self.page.evaluate(script), 'ok')
        self.routes['www.reddit.com/api/me.json'] = (200, 'application/json', '{}')
        self.assertEqual(self.page.evaluate(script), 'expired')
        self.routes['www.reddit.com/api/me.json'] = (403, 'text/html', '<html>blocked</html>')
        self.assertEqual(self.page.evaluate(script), 'error')
        self.routes['www.reddit.com/api/me.json'] = (200, 'text/html', '<html>kein json</html>')
        self.assertEqual(self.page.evaluate(script), 'error')
        self.routes['www.reddit.com/api/me.json'] = (200, 'application/json', '{"error": 403}')
        self.assertEqual(self.page.evaluate(script), 'error')


class XScriptTests(BrowserBase):
    def test_probe_script(self):
        script = session_guard.PROBE_JS['x']
        self.html('x.com/home', '<nav><a data-testid="AppTabBar_Home_Link" href="/home">Start</a></nav>')
        self.page.goto('https://x.com/home')
        self.assertEqual(self.page.evaluate(script), 'ok')
        self.html('x.com/home', '<a data-testid="loginButton" href="/login">Anmelden</a>')
        self.page.goto('https://x.com/home')
        self.assertEqual(self.page.evaluate(script), 'expired')
        self.html('x.com/home', '<div>Unbekanntes Aussehen</div>')
        self.page.goto('https://x.com/home')
        self.assertEqual(self.page.evaluate(script), 'error')
        self.html('x.com/i/flow/login', '<p>Anmelden</p>')
        self.page.goto('https://x.com/i/flow/login')
        self.assertEqual(self.page.evaluate(script), 'expired')
        self.html('x.com/home', '<input name="password" type="password">')
        self.page.goto('https://x.com/home')
        self.assertEqual(self.page.evaluate(script), 'expired')

    def test_logged_out_script_matches_the_probe(self):
        for url, body, expected in (('https://x.com/a/status/1', '<a data-testid="loginButton">Log in</a>', True),
                                    ('https://x.com/a/status/1', '<article data-testid="tweet">x</article>', False),
                                    ('https://x.com/i/flow/login', '<p>Login</p>', True),
                                    ('https://x.com/login', '<p>Login</p>', True)):
            self.html(url, body)
            self.page.goto(url)
            self.assertEqual(self.page.evaluate(x.LOGGED_OUT_JS), expected, url)

    ARTICLES = (
        '<article data-testid="tweet"><div data-testid="User-Name"><div>Beispiel Konto</div><div>@beispiel_konto</div></div>'
        '<div data-testid="tweetText">Hauptbeitrag #Test</div><a href="/beispiel_konto/status/1000"><time datetime="2026-10-07T12:30:15.000Z"></time></a>'
        '<div data-testid="reply">2</div><div data-testid="retweet">1,2 Tsd.</div><div data-testid="like">15</div>'
        '<a href="/beispiel_konto/status/1000/analytics">3,4 Mio.</a><div data-testid="tweetPhoto"></div></article>'
        '<article data-testid="tweet"><div data-testid="User-Name"><div>A</div></div><div data-testid="tweetText">Erste Antwort</div>'
        '<a href="/antwort_eins/status/1001"><time datetime="2026-10-07T12:35:00.000Z"></time></a><div data-testid="like">1</div></article>'
        '<article data-testid="tweet"><div data-testid="tweetText">Ohne Zeit</div></article>'
        '<div data-testid="cellInnerDiv"><div role="button">Show more replies</div></div>')

    def test_dom_script_reads_visible_posts(self):
        self.html('x.com/beispiel_konto/status/1000', self.ARTICLES)
        self.page.goto('https://x.com/beispiel_konto/status/1000')
        items = self.page.evaluate(x.DOM_TWEETS_JS)
        self.assertEqual([i['id'] for i in items], ['1000', '1001'])                 # Beitrag ohne Zeit/Adresse wird übergangen
        main = x.info_from_dom(items[0])
        self.assertEqual((main['handle'], main['name'], main['text'], main['likes'], main['replies'], main['retweets'], main['views']),
                         ('beispiel_konto', 'Beispiel Konto', 'Hauptbeitrag #Test', 15, 2, 1200, 3400000))
        self.assertEqual((main['hashtags'], main['media_types'], main['created_at']), (['Test'], ['media'], '2026-10-07T12:30:15+00:00'))

    def test_click_more_script(self):
        self.html('x.com/beispiel_konto/status/1000', self.ARTICLES + '<script>document.querySelector("[role=button]").onclick=()=>{document.body.setAttribute("data-clicked","1")}</script>')
        self.page.goto('https://x.com/beispiel_konto/status/1000')
        self.assertTrue(self.page.evaluate(x.CLICK_MORE_JS))
        self.assertEqual(self.page.evaluate('document.body.getAttribute("data-clicked")'), '1')
        self.html('x.com/leer', '<div data-testid="cellInnerDiv"><div role="button">Etwas anderes</div></div>')
        self.page.goto('https://x.com/leer')
        self.assertFalse(self.page.evaluate(x.CLICK_MORE_JS))

    def test_links_script(self):
        self.html('x.com/search', self.ARTICLES)
        self.page.goto('https://x.com/search')
        self.assertEqual(self.page.evaluate(x.LINKS_JS), ['/beispiel_konto/status/1000', '/antwort_eins/status/1001'])
        self.assertEqual(x.XAdapter._dom_link('/beispiel_konto/status/1000'), 'https://x.com/beispiel_konto/status/1000')

    def test_text_script(self):
        self.html('x.com/gesperrt', '<p>Your account has been locked.</p>')
        self.page.goto('https://x.com/gesperrt')
        self.assertEqual(x.XAdapter.problem_message(self.page), x.MSG_LOCKED)


if __name__ == '__main__':
    unittest.main()
