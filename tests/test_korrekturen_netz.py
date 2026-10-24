"""Korrekturen aus der Fundliste „netz“ (13.10.2026): Reddit-Zwischenstand, TikTok-CAPTCHA-Wörter, toter Browser-Treiber,
Anmeldefehler mit Grund, kaputte Identitätsdatei, YouTube mit abgeschalteten Kommentaren, YouTube-Anmeldeadresse,
Grund einer fehlgeschlagenen Anmeldeprüfung im Protokoll, Tor-Meldung. Ohne Netz, ohne Browser, ohne echte Daten."""
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('SS_PACE', 'off')   # keine Pausen zwischen Seiten

from socialscraper import login as login_module
from socialscraper.adapters.base import ScrapeContext
from socialscraper.adapters.youtube import YouTubeAdapter
from socialscraper.backends.local import LocalBackend
from socialscraper.config import Settings
from socialscraper.net import cookies, notify, session_guard, tor
from socialscraper.net.accountproxy import AccountProxyError
from socialscraper.net.cookies import CookieJar
from socialscraper.net.fingerprint import build
from socialscraper.net.identity import IdentityManager
from socialscraper.net.retry import PermanentFetchError
from socialscraper.net.session_guard import State


# ---------------------------------------------------------------- Fund 1: Reddit-Kanalliste

class RedditCursorTests(unittest.TestCase):
    """Pakete mitten in einer Seite dürfen nicht schon die Marke der NÄCHSTEN Seite tragen: Bricht der Lauf nach dem
    ersten Paket ab, fehlten sonst die Beiträge 31–100 der angefangenen Seite für immer."""

    @staticmethod
    def page(after, start, count=100):
        return {'data': {'after': after, 'children': [
            {'kind': 't3', 'data': {'id': f'p{start + n:05d}', 'title': 't', 'permalink': f'/r/a/comments/p{start + n:05d}/t/',
                                    'created_utc': 1790000000 - (start + n)}} for n in range(count)]}}

    def run_listing(self, responses, cursor=None):
        from socialscraper import channel_list as cl
        from socialscraper.adapters.reddit import RedditAdapter
        batches = []
        opts = cl.ListOptions(on_items=lambda items, c: batches.append((len(items), c)), cursor=cursor)
        opts.sleep = lambda s: None
        sessions = Mock()
        sessions.acquire.return_value.__enter__ = lambda s: Mock(url='', evaluate=Mock(return_value=''), goto=Mock(),
                                                                 wait_for_timeout=Mock())
        sessions.acquire.return_value.__exit__ = lambda *a: False
        ctx = Mock(sessions=sessions, identity='reddit_main', country='de', mobile=False)
        pending = list(responses)
        with patch.object(RedditAdapter, 'fetch_json', side_effect=lambda page_, path: pending.pop(0)), \
             patch.object(RedditAdapter, '_pause'):
            result = RedditAdapter().channel_items('r/test', ctx, opts)
        return result, batches

    def test_mid_page_batches_carry_the_cursor_of_the_fetched_page(self):
        result, batches = self.run_listing([self.page('t3_a', 0), self.page('t3_b', 100), self.page(None, 200)])
        self.assertEqual((result.found, result.exhausted), (300, True))
        # Seite 1 wurde ohne Marke geholt: ihre Pakete tragen None, erst die fertige Seite zeigt auf Seite 2 (t3_a) usw.
        self.assertEqual([c for _, c in batches],
                         [None, None, None, 't3_a', 't3_a', 't3_a', 't3_a', 't3_b', 't3_b', 't3_b', 't3_b', None])
        self.assertEqual([n for n, _ in batches], [30, 30, 30, 10] * 3)

    def test_resumed_listing_keeps_its_start_cursor_until_the_page_is_done(self):
        result, batches = self.run_listing([self.page('t3_b', 100), self.page(None, 200)], cursor='t3_a')
        self.assertEqual([c for _, c in batches], ['t3_a', 't3_a', 't3_a', 't3_b', 't3_b', 't3_b', 't3_b', None])
        self.assertEqual(result.found, 200)


# ---------------------------------------------------------------- Fund 2: TikTok-CAPTCHA-Wörter

class _TikTokPage:
    """Profil- oder Suchseite: Links und sichtbarer Seitentext."""
    url = ''

    def __init__(self, text, links=()):
        self.text, self.links = text, list(links)

    def evaluate(self, js, *args):
        if 'a[href*="/video/"]' in js:
            return self.links
        if 'innerText' in js:
            return self.text
        return ''

    def goto(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, *args):
        pass


class TikTokCaptchaWordsTests(unittest.TestCase):
    """Einzelne Wörter wie „puzzle“ oder „captcha“ stehen in Kommentaren und Beschreibungen; nur die Sätze der
    TikTok-Prüfseite zählen als Sicherheitsabfrage."""

    def test_words_in_comments_and_captions_are_not_a_captcha(self):
        from socialscraper.adapters.tiktok import TikTokAdapter
        text = 'Rätsel des Tages\nthis puzzle is hard lol\nI hate captcha on other sites\n#puzzle #brainteaser'
        self.assertEqual(TikTokAdapter._blocked_message(_TikTokPage(text)), '')

    def test_real_check_page_is_still_detected(self):
        from socialscraper.adapters.tiktok import TikTokAdapter
        for text in ('Verify to continue\nDrag the slider to fit the puzzle',
                     'Ziehe den Schieberegler, um das Puzzle zu vervollständigen',
                     'Verify to continue\nSelect 2 objects that are the same shape'):
            with self.subTest(text=text):
                self.assertIn('CAPTCHA', TikTokAdapter._blocked_message(_TikTokPage(text)))

    def collect(self, text):
        from socialscraper.adapters.tiktok import TikTokAdapter
        page = _TikTokPage(text)
        sessions = Mock()
        sessions.acquire.return_value.__enter__ = lambda s: page
        sessions.acquire.return_value.__exit__ = lambda *a: False
        ctx = Mock(sessions=sessions, identity='t', country='de', mobile=False, authenticated=False, require_auth=False,
                   cfg=Settings())
        with patch('socialscraper.adapters.tiktok.humanize.warmup'), patch('socialscraper.adapters.tiktok.humanize.scroll'):
            return TikTokAdapter()._collect_links(ctx, 'https://www.tiktok.com/tag/puzzle', 5)

    def test_empty_hashtag_page_about_puzzles_is_not_a_captcha_error(self):
        # Vorher: PermanentFetchError „Sicherheitsrätsel (CAPTCHA)“ -> Kontofehler „captcha“, 6 h Abkühlung
        self.assertEqual(self.collect('#puzzle\nNo videos yet\nTry a captcha-free puzzle game'), [])

    def test_empty_page_with_the_check_text_still_raises(self):
        with self.assertRaisesRegex(PermanentFetchError, 'CAPTCHA'):
            self.collect('Verify to continue\nDrag the slider to fit the puzzle')


# ---------------------------------------------------------------- Fund 3: toter Browser-Treiber

class DeadDriverTests(unittest.TestCase):
    """Nach dem Zeitlimit beendet deadline.kill_children auch node.exe, den Playwright-Treiber ALLER Sitzungen. Liegt noch
    eine zweite Sitzung im Pool, blieb der tote Treiber stehen, und jeder weitere Abruf scheiterte."""
    DEAD = 'Connection closed while reading from the driver'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.identity = SimpleNamespace(name='example', fp=build('example', 'us'))

    def backend(self, drivers):
        module = types.ModuleType('patchright.sync_api')
        starter = Mock(side_effect=drivers)
        module.sync_playwright = lambda: SimpleNamespace(start=starter)
        patcher = patch.dict(sys.modules, {'patchright': types.ModuleType('patchright'), 'patchright.sync_api': module})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('socialscraper.backends.base.CookieJar.load', return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        return LocalBackend(profiles_dir=self.tmp.name), starter

    def test_dead_driver_is_replaced_even_while_another_session_is_open(self):
        dead, fresh = Mock(), Mock()
        contexts = [Mock(), Mock()]
        for context in contexts:
            context.close.side_effect = Exception(self.DEAD)
        dead.chromium.launch_persistent_context.side_effect = contexts
        backend, starter = self.backend([dead, fresh])
        tiktok = backend.open(self.identity, None, platform='tiktok')
        reddit = backend.open(self.identity, None, platform='reddit')
        with self.assertRaisesRegex(Exception, 'driver'):      # Zeitlimit: Browser und Treiber sind tot
            backend.close(tiktok)
        self.assertIsNone(backend._pw)                           # vorher: blieb stehen, weil Reddit ihn noch „benutzte“
        dead.stop.assert_called_once()
        third = backend.open(self.identity, None, platform='tiktok')
        self.assertEqual(starter.call_count, 2)                  # vorher: 1 – der nächste Abruf lief über den toten Treiber
        self.assertIs(backend._pw, fresh)
        with self.assertRaisesRegex(Exception, 'driver'):      # die verwaiste Reddit-Sitzung reißt den neuen nicht mit
            backend.close(reddit)
        self.assertIs(backend._pw, fresh)
        fresh.stop.assert_not_called()
        backend.close(third)
        fresh.stop.assert_called_once()
        self.assertIsNone(backend._pw)

    def test_launch_over_a_dead_driver_resets_it(self):
        dead, fresh = Mock(), Mock()
        dead.chromium.launch_persistent_context.side_effect = [Mock(), Exception(self.DEAD)]
        backend, starter = self.backend([dead, fresh])
        backend.open(self.identity, None, platform='tiktok')
        with self.assertRaisesRegex(Exception, 'driver'):
            backend.open(self.identity, None, platform='reddit')
        self.assertIsNone(backend._pw)
        backend.open(self.identity, None, platform='reddit')
        self.assertEqual((starter.call_count, backend._pw, backend._users), (2, fresh, 1))

    def test_closed_context_alone_keeps_the_shared_driver(self):
        pw = Mock()
        first, second = Mock(), Mock()
        first.close.side_effect = Exception('Target page, context or browser has been closed')
        pw.chromium.launch_persistent_context.side_effect = [first, second]
        backend, _ = self.backend([pw])
        one = backend.open(self.identity, None, platform='tiktok')
        two = backend.open(self.identity, None, platform='reddit')
        with self.assertRaises(Exception):
            backend.close(one)
        self.assertIs(backend._pw, pw)
        pw.stop.assert_not_called()
        backend.close(two)
        pw.stop.assert_called_once()


# ---------------------------------------------------------------- Fund 4 und 9: Anmeldung und Prüfung

def auth_cookie(platform='tiktok', **extra):
    return {'name': 'sessionid', 'value': 'offline-secret', 'domain': '.' + cookies.DOMAINS[platform], **extra}


class FakePage:
    def __init__(self):
        self.fail_goto = False

    def goto(self, url, **kwargs):
        if self.fail_goto:
            raise RuntimeError('password=do-not-log-me')

    def wait_for_timeout(self, timeout):
        pass

    def evaluate(self, script, args=None):
        if script in session_guard.PROBE_JS.values():
            return State.OK
        return None


class FakeBackend:
    def __init__(self):
        self.page_object = FakePage()
        self.cookies = [auth_cookie()]

    @contextmanager
    def session(self, identity, proxy, **kwargs):
        yield SimpleNamespace(identity=identity)

    def page(self, handle):
        return self.page_object

    def read_cookies(self, handle):
        return self.cookies


class FakeIdentityManager:
    def __init__(self, get_error=None, proxy_error=None):
        self.get_error, self.proxy_error = get_error, proxy_error

    def get(self, name, country, mobile):
        if self.get_error:
            raise self.get_error
        return SimpleNamespace(name=name)

    def proxy_for(self, identity):
        if self.proxy_error:
            raise self.proxy_error
        return None


class ScopedCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        patcher = patch.object(cookies, 'BASE', pathlib.Path(self.temporary.name) / 'cookies')
        patcher.start()
        self.addCleanup(patcher.stop)
        session_guard._cache.clear()
        self.addCleanup(session_guard._cache.clear)
        notify.configure(None)
        self.backend = FakeBackend()


PROXY_DOWN = AccountProxyError('Der feste Proxy dieses Kontos (1.2.3.4:8080) ist nicht erreichbar. Proxy starten oder ändern.')
WRONG_COUNTRY = ValueError('Identity besitzt bereits ein anderes Land oder Gerät; verwende einen anderen Identity-Namen')


class LoginReasonTests(ScopedCase):
    """login() verschluckte jeden Grund hinter „Browser-Anmeldung fehlgeschlagen“ – auch die eigenen, sicheren Meldungen."""

    def test_unreachable_account_proxy_is_reported_as_such(self):
        with self.assertRaises(AccountProxyError) as caught:
            login_module.login(self.backend, FakeIdentityManager(proxy_error=PROXY_DOWN), 'alice', mode='qr')
        self.assertIn('feste Proxy', str(caught.exception))

    def test_identity_country_mismatch_is_reported_as_such(self):
        with self.assertRaisesRegex(ValueError, 'Land oder Gerät'):
            login_module.login(self.backend, FakeIdentityManager(get_error=WRONG_COUNTRY), 'alice', mode='qr')

    def test_browser_error_stays_generic_but_names_its_kind_in_the_log(self):
        self.backend.page_object.fail_goto = True
        with self.assertLogs(login_module.log, 'WARNING') as logs:
            with self.assertRaises(login_module.LoginError) as caught:
                login_module.login(self.backend, FakeIdentityManager(), 'alice', mode='qr')
        text = '\n'.join(logs.output)
        self.assertIn('RuntimeError', text)
        self.assertNotIn('do-not-log-me', text)                  # Browserfehler können Eingaben enthalten: nie wörtlich
        self.assertNotIn('do-not-log-me', str(caught.exception))


class ProbeReasonTests(ScopedCase):
    """Der Grund einer fehlgeschlagenen Anmeldeprüfung stand nirgends im Protokoll."""

    def test_identity_mismatch_is_named_in_the_warning(self):
        CookieJar.save('alice', 'tiktok', [auth_cookie()])
        with self.assertLogs(session_guard.log, 'WARNING') as logs:
            state = session_guard.ensure(self.backend, FakeIdentityManager(get_error=WRONG_COUNTRY), 'alice')
        self.assertEqual(state, State.ERROR)
        self.assertIn('Land oder Gerät', '\n'.join(logs.output))

    def test_account_proxy_error_is_still_passed_through(self):
        CookieJar.save('alice', 'tiktok', [auth_cookie()])
        with self.assertRaises(AccountProxyError):
            session_guard.ensure(self.backend, FakeIdentityManager(proxy_error=PROXY_DOWN), 'alice')


# ---------------------------------------------------------------- Fund 5: kaputte Identitätsdatei

class IdentityFileTests(unittest.TestCase):
    """Eine leere oder halbe Identitätsdatei (Absturz beim Anlegen) legte das Konto dauerhaft lahm."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ids = pathlib.Path(self.tmp.name) / 'identities'
        self.ids.mkdir()
        self.im = IdentityManager(None, profiles_dir=str(pathlib.Path(self.tmp.name) / 'profiles'))

    def check_heals(self, content):
        (self.ids / 'konto.json').write_text(content, encoding='utf-8')
        with self.assertLogs('socialscraper.net.identity', 'WARNING'):
            self.assertEqual(self.im.stored('konto'), (None, None))     # vorher: JSONDecodeError auf Englisch
            ident = self.im.get('konto', 'de', False)                  # vorher: dito, bei jedem Abruf
        self.assertEqual((ident.country, ident.mobile), ('de', False))
        self.assertEqual(ident.fp.to_dict(), build('konto', 'de', False).to_dict())   # dieselbe Identität wie zuvor
        data = json.loads((self.ids / 'konto.json').read_text(encoding='utf-8'))
        self.assertEqual((data['country'], data['mobile']), ('de', False))
        self.assertEqual(self.im.stored('konto'), ('de', False))
        self.assertEqual(list(self.ids.glob('*.tmp')), [])

    def test_empty_file_is_replaced(self):
        self.check_heals('')

    def test_half_written_file_is_replaced(self):
        self.check_heals('{"name": "konto", "coun')

    def test_valid_file_with_missing_fields_still_reports_damage(self):
        (self.ids / 'konto.json').write_text('{"name": "konto"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'beschädigt'):
            self.im.stored('konto')

    def test_new_file_is_written_without_leftover(self):
        ident = self.im.get('neu', 'fr', True)
        self.assertEqual(self.im.stored('neu'), ('fr', True))
        self.assertEqual(ident.fp.to_dict(), self.im.get('neu', 'fr', True).fp.to_dict())
        self.assertEqual(sorted(p.name for p in self.ids.iterdir()), ['neu.json'])


# ---------------------------------------------------------------- Fund 7: YouTube ohne Kommentare

class YouTubeCommentsOffTests(unittest.TestCase):
    """Abgeschaltete Kommentare („Youtube said: Comments are turned off“) sind kein vorübergehendes Problem."""

    def fetch(self, info, warning='', limit=5):
        class FakeYDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def extract_info(self, *args, **kwargs):
                if warning:
                    self.options['logger'].warning(warning)
                return info

            def process_ie_result(self, result, download=False):
                return result
        ctx = ScrapeContext(Settings(max_comments=limit, max_retries=1, impersonate=''), None, None)
        with patch.dict(sys.modules, {'yt_dlp': types.SimpleNamespace(YoutubeDL=FakeYDL)}), \
             patch('socialscraper.adapters.youtube.youtube_runtime_options', return_value={'js_runtimes': {'node': {}}}):
            return YouTubeAdapter().fetch('https://youtu.be/v', ctx)

    def test_turned_off_comments_are_empty_not_a_problem(self):
        result = self.fetch({'id': 'v'}, warning='Youtube said: Comments are turned off. Learn more')
        self.assertEqual(result.comments_status, 'empty')        # vorher: unavailable, „Problem … später erneut“
        self.assertIn('ausgeschaltet', result.message)
        self.assertNotIn('erneut', result.message)

    def test_other_comment_warnings_stay_unavailable(self):
        result = self.fetch({'id': 'v'}, warning='Unable to download comments: timed out')
        self.assertEqual(result.comments_status, 'unavailable')
        self.assertIn('erneut', result.message)


# ---------------------------------------------------------------- Fund 8: YouTube-Anmeldeadresse

class YouTubeLoginUrlTests(unittest.TestCase):
    """Nach der Google-Anmeldung muss das Fenster auf youtube.com landen; sonst findet die Prüfung nie „angemeldet“."""

    def test_login_lands_on_youtube(self):
        url = login_module.LOGIN_URL['youtube']
        self.assertTrue(url.startswith('https://accounts.google.com/'))
        self.assertIn('continue=https://www.youtube.com/', url)
        self.assertEqual(login_module.EMAIL_URL['youtube'], url)


# ---------------------------------------------------------------- Fund 10: Tor-Meldung

class TorMessageTests(unittest.TestCase):
    def test_missing_message_names_the_folder_without_a_tab(self):
        message = tor.missing_message('X')
        self.assertNotIn('\t', message)                          # vorher: „tools<TAB>or“
        self.assertIn('„tools\\tor“', message)


if __name__ == '__main__':
    unittest.main()
