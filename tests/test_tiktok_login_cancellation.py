"""TikTok web login must stop before further input or persisting a late session."""
import contextlib
import socket
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from socialscraper.net.cookies import CookieJar
from socialscraper.signup import tiktok


class TikTokLoginCancellationTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))
        stack.enter_context(patch.object(tiktok.time, 'sleep', side_effect=AssertionError('Real sleep blocked')))
        self.cancelled = threading.Event()
        self.cfg = SimpleNamespace(db_path=Path('unused-test-db.sqlite'),
                                   profiles_dir=Path('unused-test-profiles'), headless=True)
        self.konto = {'identity': 'test-login', 'country': 'de'}
        self.credentials = {'email': 'fixture@example.test', 'passwort': 'offline-fixture'}
        self.cookies = [
            {'name': 'sessionid', 'value': 'offline-session', 'domain': '.tiktok.com', 'path': '/'},
            {'name': 'other', 'value': 'foreign-fixture', 'domain': '.example.test', 'path': '/'},
        ]
        self.vault = stack.enter_context(patch('socialscraper.accountvault.get', return_value=self.credentials))
        stack.enter_context(patch('socialscraper.net.proxymode.make_pool'))
        stack.enter_context(patch('socialscraper.net.proxymode.is_anonymous', return_value=True))
        self.identities = stack.enter_context(patch('socialscraper.net.identity.IdentityManager'))
        self.identities.return_value.stored.return_value = (None, False)
        self.backend = MagicMock()
        self.handle = self.backend.session.return_value.__enter__.return_value
        self.page = self.backend.page.return_value
        self.values = {}
        self.page.fill.side_effect = lambda selector, value: self.values.update({selector: value})
        self.page.evaluate.side_effect = lambda script, selector: self.values.get(selector)
        self.backend.read_cookies.side_effect = [[], self.cookies]
        self.build = stack.enter_context(patch('socialscraper.net.factory.build', return_value=self.backend))
        self.dismiss = stack.enter_context(patch.object(tiktok, '_dismiss'))
        self.save = stack.enter_context(patch.object(CookieJar, 'save'))
        self.shutdown = stack.enter_context(patch('socialscraper.cli.shutdown_runtime'))
        self.notify = MagicMock()

    def login(self):
        return tiktok.web_anmelden(self.cfg, self.konto, melden=self.notify,
                                   stop=self.cancelled.is_set, timeout=1)

    def assert_closed(self):
        self.backend.session.return_value.__exit__.assert_called_once()
        self.shutdown.assert_called_once_with(self.backend, None)

    def assert_no_success(self):
        self.save.assert_not_called()
        self.notify.assert_not_called()

    def test_existing_cancellation_avoids_credentials_and_runtime(self):
        self.cancelled.set()
        with self.assertRaisesRegex(RuntimeError, 'Abgebrochen'):
            self.login()
        self.vault.assert_not_called()
        self.identities.assert_not_called()
        self.build.assert_not_called()
        self.assert_no_success()

    def test_cancellation_during_navigation_prevents_any_form_input(self):
        self.page.goto.side_effect = lambda *args, **kwargs: self.cancelled.set()
        with self.assertRaisesRegex(RuntimeError, 'Abgebrochen'):
            self.login()
        self.page.fill.assert_not_called()
        self.page.click.assert_not_called()
        self.assert_no_success()
        self.assert_closed()

    def test_cancellation_during_loading_wait_prevents_any_form_input(self):
        self.page.wait_for_timeout.side_effect = lambda *args, **kwargs: self.cancelled.set()
        with self.assertRaisesRegex(RuntimeError, 'Abgebrochen'):
            self.login()
        self.page.fill.assert_not_called()
        self.page.click.assert_not_called()
        self.assert_no_success()
        self.assert_closed()

    def test_cancellation_during_email_fill_prevents_password_and_submit(self):
        self.page.fill.side_effect = lambda *args, **kwargs: self.cancelled.set()
        with self.assertRaisesRegex(RuntimeError, 'Abgebrochen'):
            self.login()
        self.page.fill.assert_called_once_with(tiktok.LOGIN_SEL['email'][0], self.credentials['email'])
        self.page.click.assert_not_called()
        self.assert_no_success()
        self.assert_closed()

    def test_cancellation_during_failed_fill_prevents_trying_another_selector(self):
        def cancelled_fill(*args, **kwargs):
            self.cancelled.set()
            raise RuntimeError('Offline selector failure')

        self.page.fill.side_effect = cancelled_fill
        with self.assertRaisesRegex(RuntimeError, 'Abgebrochen'):
            self.login()
        self.page.fill.assert_called_once()
        self.page.click.assert_not_called()
        self.assert_no_success()
        self.assert_closed()

    def test_cancellation_during_cookie_read_rejects_late_authenticated_session(self):
        def cancelled_read(*args, **kwargs):
            self.cancelled.set()
            return self.cookies

        self.backend.read_cookies.side_effect = cancelled_read
        with self.assertRaisesRegex(RuntimeError, 'Abgebrochen'):
            self.login()
        self.backend.read_cookies.assert_called_once_with(self.handle)
        self.assert_no_success()
        self.assert_closed()

    def test_uncancelled_login_saves_only_tiktok_cookies_and_closes_runtime(self):
        self.assertTrue(self.login())
        expected = CookieJar.for_platform(self.cookies, 'tiktok')
        self.assertEqual([cookie['name'] for cookie in expected], ['sessionid'])
        self.save.assert_called_once_with(self.konto['identity'], 'tiktok', expected)
        self.notify.assert_called_once_with('Web-Anmeldung steht.')
        self.assert_closed()


if __name__ == '__main__':
    unittest.main()
