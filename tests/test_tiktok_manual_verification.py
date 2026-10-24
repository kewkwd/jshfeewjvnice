"""Round 35: TikTok CAPTCHA handling must remain manual in every mode."""
import contextlib
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.signup import tiktok
from socialscraper.android import tiktok_app
from socialscraper import accountvault, operation, signup, streuung
from socialscraper.config import Settings
from socialscraper.net.errors import FinalError
from socialscraper.store import Store


class TikTokManualVerificationTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for target in ('create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, target, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))

    def context(self, mode):
        from profile_fixture import PERSON
        return SimpleNamespace(mode=mode, email='test@example.test', password='FAKE_PASSWORD',
            person=dict(PERSON),
            identity='manual-verification-test', timeout=1, stop=lambda: False, notizen=[],
            melden=MagicMock(), fill=MagicMock(return_value='field'), click=MagicMock(return_value='button'),
            warte=MagicMock(), dismiss=MagicMock(), code_holen=MagicMock(return_value='123456'),
            warte_auf_cookies=MagicMock(return_value=[{'name': 'sessionid', 'value': 'TEST_ONLY'}]))

    def assert_manual_hint(self, ctx):
        messages = '\n'.join(str(call.args[0]) for call in ctx.melden.call_args_list)
        self.assertRegex(messages.lower(), r'captcha|schieber|rätsel')
        self.assertRegex(messages.lower(), r'manuell|selbst|bitte')

    def assert_web_captcha_is_manual(self, mode):
        page, ctx = MagicMock(), self.context(mode)
        seen = [False]

        def query(selector):
            # The fixture represents a person resolving the challenge between
            # the first detection and a later check; there is no real browser.
            if selector in tiktok.RAETSEL and not seen[0]:
                seen[0] = True
                return SimpleNamespace(is_visible=lambda: True)
            return None

        page.query_selector.side_effect = query
        with patch.object(tiktok, '_waehle'), \
                patch.object(tiktok, '_schieben', create=True, return_value=True) as automatic:
            result = tiktok.run(page, ctx)
        automatic.assert_not_called()
        page.mouse.move.assert_not_called()
        page.mouse.down.assert_not_called()
        page.mouse.up.assert_not_called()
        self.assert_manual_hint(ctx)
        ctx.warte_auf_cookies.assert_called_once()
        ctx.code_holen.assert_not_called()
        self.assertEqual(result, ctx.warte_auf_cookies.return_value)

    def assert_android_captcha_is_manual(self, mode):
        adb, ctx = MagicMock(), self.context(mode)
        adb.warte_auf.return_value = False
        detections = [0]

        def challenge(_adb):
            detections[0] += 1
            return detections[0] == 1

        with patch.object(tiktok_app.time, 'sleep'), \
                patch.object(tiktok_app, '_wegklicken'), \
                patch.object(tiktok_app, '_tippe', return_value=True), \
                patch.object(tiktok_app, '_feld_fuellen'), \
                patch.object(tiktok_app, '_weiter'), \
                patch.object(tiktok_app, '_geburtsdatum'), \
                patch.object(tiktok_app, '_raetsel', side_effect=challenge), \
                patch.object(tiktok_app, '_schieben', create=True) as automatic, \
                patch.object(tiktok_app, '_ist_angemeldet', return_value=True):
            result = tiktok_app.registrieren(adb, ctx)
        automatic.assert_not_called()
        adb.swipe.assert_not_called()
        self.assert_manual_hint(ctx)
        ctx.code_holen.assert_not_called()
        self.assertTrue(result)

    def test_web_auto_mode_does_not_move_the_captcha_slider(self):
        self.assert_web_captcha_is_manual('auto')

    def test_web_assisted_mode_does_not_move_the_captcha_slider(self):
        self.assert_web_captcha_is_manual('assisted')

    def test_android_auto_mode_does_not_move_the_captcha_slider(self):
        self.assert_android_captcha_is_manual('auto')

    def test_android_assisted_mode_does_not_move_the_captcha_slider(self):
        self.assert_android_captcha_is_manual('assisted')

    @contextlib.contextmanager
    def android_wait_fixture(self, outcome):
        adb, ctx = MagicMock(), self.context('auto')
        adb.warte_auf.return_value = False
        ctx.timeout = 2.5
        state = {'now': 100.0, 'cancelled': False, 'visible': True, 'detected': False}
        ctx.stop = lambda: state['cancelled']
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(tiktok_app.time, 'sleep'))
            stack.enter_context(patch.object(tiktok_app.time, 'monotonic', side_effect=lambda: state['now']))
            dismiss = stack.enter_context(patch.object(tiktok_app, '_wegklicken'))
            signed_in = stack.enter_context(patch.object(tiktok_app, '_ist_angemeldet', return_value=True))
            for helper in ('_tippe', '_feld_fuellen', '_weiter', '_geburtsdatum'):
                stack.enter_context(patch.object(tiktok_app, helper, return_value=True))
            automatic = stack.enter_context(patch.object(tiktok_app, '_schieben', create=True))

            def challenge(_adb):
                if not state['detected']:
                    state['detected'] = True
                    # Registration setup legitimately dismisses screens before
                    # the CAPTCHA; assertions below concern only later actions.
                    dismiss.reset_mock()
                    adb.reset_mock()
                return state['visible']

            def wait(seconds, stop=None):
                self.assertGreater(seconds, 0)
                self.assertLessEqual(seconds, 1)
                self.assertLessEqual(waiting.call_count, 4, 'Unbounded CAPTCHA polling')
                state['now'] += seconds
                if outcome == 'cancelled':
                    state['cancelled'] = True
                elif outcome == 'resolved':
                    state['visible'] = False
                return not (stop and stop())

            stack.enter_context(patch.object(tiktok_app, '_raetsel', side_effect=challenge))
            waiting = stack.enter_context(patch.object(operation, 'wait', side_effect=wait))
            yield adb, ctx, state, dismiss, signed_in, automatic, waiting

    def test_android_persistent_captcha_times_out_without_further_input(self):
        with self.android_wait_fixture('timeout') as (adb, ctx, state, dismiss, signed_in, automatic, waiting):
            with self.assertRaisesRegex(FinalError, 'CAPTCHA'):
                tiktok_app.registrieren(adb, ctx)
            self.assertEqual(state['now'], 100.0 + ctx.timeout)
            self.assertEqual(sum(call.args[0] for call in waiting.call_args_list), ctx.timeout)
            dismiss.assert_not_called()
            signed_in.assert_not_called()
            automatic.assert_not_called()
            self.assertEqual(adb.mock_calls, [])
            ctx.code_holen.assert_not_called()
            self.assert_manual_hint(ctx)
            ctx.melden.assert_called_once()

    def test_android_cancellation_during_captcha_wait_stops_without_further_input(self):
        with self.android_wait_fixture('cancelled') as (adb, ctx, state, dismiss, signed_in, automatic, waiting):
            with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                tiktok_app.registrieren(adb, ctx)
            self.assertTrue(state['cancelled'])
            waiting.assert_called_once()
            dismiss.assert_not_called()
            signed_in.assert_not_called()
            automatic.assert_not_called()
            self.assertEqual(adb.mock_calls, [])
            ctx.code_holen.assert_not_called()
            self.assert_manual_hint(ctx)

    def test_android_human_resolution_resumes_after_cancellable_wait(self):
        with self.android_wait_fixture('resolved') as (adb, ctx, state, dismiss, signed_in, automatic, waiting):
            self.assertTrue(tiktok_app.registrieren(adb, ctx))
            self.assertFalse(state['visible'])
            waiting.assert_called_once()
            self.assertTrue(dismiss.called)
            signed_in.assert_called_once_with(adb, ctx)
            automatic.assert_not_called()
            adb.swipe.assert_not_called()
            ctx.code_holen.assert_not_called()
            self.assert_manual_hint(ctx)

    def test_signup_propagates_manual_verification_failure_without_browser_or_account(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SS_REQUIRE_PROXY': '0'}):
            root = Path(directory)
            cfg = Settings(db_path=str(root / 'data/test.sqlite'), profiles_dir=str(root / 'data/profiles'))
            from profile_fixture import prepare
            prepare(cfg, 'manual-captcha-test')
            with Store(cfg.db_path):
                pass
            manager, backend = MagicMock(), MagicMock()
            manager.proxy_for.return_value = None
            for message in ('Abgebrochen.', 'Manuelle CAPTCHA-Bestätigung nicht abgeschlossen.'):
                with self.subTest(reason=message), contextlib.ExitStack() as stack:
                    error = FinalError(message)
                    module = SimpleNamespace(WEG='app', registrieren_app=MagicMock(side_effect=error))
                    stack.enter_context(patch.object(signup, 'MODULE', {'tiktok': module}))
                    stack.enter_context(patch.object(streuung, 'wartezeit_bis_registrierung', return_value=0))
                    stack.enter_context(patch.object(streuung, 'pruefe_neues_konto', return_value=[]))
                    browser = stack.enter_context(patch.object(signup, '_browser_lauf'))
                    create = stack.enter_context(patch.object(signup, '_konto_anlegen'))
                    save = stack.enter_context(patch.object(accountvault, 'put'))
                    cookies = stack.enter_context(patch.object(signup.CookieJar, 'save'))
                    with self.assertRaises(FinalError) as raised:
                        signup.create(cfg, 'tiktok', name='manual-captcha-test', email='test@example.test',
                                      im=manager, backend=backend, warmup='aus')
                    self.assertIs(raised.exception, error)
                    module.registrieren_app.assert_called_once()
                    browser.assert_not_called()
                    backend.session.assert_not_called()
                    create.assert_not_called()
                    save.assert_not_called()
                    cookies.assert_not_called()
                    with Store(cfg.db_path) as store:
                        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
