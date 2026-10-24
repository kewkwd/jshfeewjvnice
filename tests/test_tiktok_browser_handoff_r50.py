"""Android-to-desktop handoff: temporary identities and fully mocked browsers."""
import contextlib
import datetime
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from socialscraper import repo, warmup
from socialscraper.backends import firefox
from socialscraper.net import factory
from socialscraper.net.cookies import CookieJar
from socialscraper.net.errors import FinalError
from socialscraper.net.identity import IdentityManager
from socialscraper.net.proxy import Proxy
from socialscraper.signup import tiktok
from socialscraper.store import Store


class HandoffFixture(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='handoff-r50-')))
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network forbidden')))
        for method in ('connect', 'connect_ex', 'bind', 'sendto'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('Socket forbidden')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process forbidden')))
        self.stack.enter_context(patch.object(os, 'system', side_effect=AssertionError('Shell forbidden')))
        self.stack.enter_context(patch.object(tiktok.time, 'sleep', side_effect=AssertionError('Real sleep forbidden')))
        self.cfg = SimpleNamespace(db_path=self.root / 'fixture.sqlite', profiles_dir=self.root / 'profiles',
                                   engine='firefox', backend='local', headless=True)
        self.proxy = Proxy.parse('http://fixture:offline@proxy.example.test:18080')
        self.pool = SimpleNamespace(anonymous=False, acquire=Mock(side_effect=AssertionError('Account route required')))
        self.im = IdentityManager(self.pool, self.cfg.profiles_dir)
        self.mobile = self.im.get('handoff_fixture', 'de', mobile=True)
        self.original = self.im.base / 'handoff_fixture.json'
        self.original_bytes = self.original.read_bytes()
        self.sidecar = self.im.base / '_browser' / 'firefox' / 'handoff_fixture.json'
        self.route = SimpleNamespace(proxy_for=Mock(return_value=self.proxy))
        self.im.account_proxies = self.route


class FirefoxHandoffContextTests(HandoffFixture):
    def test_context_is_explicit_persistent_and_keeps_original_account_and_mobile_data(self):
        self.assertIsNone(self.im.browser_context(self.mobile, engine='firefox'))
        self.assertFalse(self.sidecar.exists())
        desktop = self.im.browser_context(self.mobile, engine='firefox', create=True)
        self.assertEqual((desktop.name, desktop.country, desktop.mobile), ('handoff_fixture', 'de', False))
        self.assertFalse(desktop.fp.is_mobile)
        self.assertEqual(self.original.read_bytes(), self.original_bytes)
        saved = self.sidecar.read_bytes()
        another = IdentityManager(self.pool, self.cfg.profiles_dir)
        restored = another.browser_context(another.get(desktop.name, 'de', mobile=True), engine='firefox')
        self.assertEqual(restored, desktop)
        self.assertEqual(self.sidecar.read_bytes(), saved)
        self.assertEqual(another.stored(desktop.name), ('de', True))
        self.assertIs(self.im.proxy_for(desktop), self.proxy)
        self.route.proxy_for.assert_called_once_with('handoff_fixture')
        self.pool.acquire.assert_not_called()

    def test_factory_passes_manager_and_rejects_mobile_without_explicit_handoff(self):
        backend = factory.build(self.cfg, self.im)
        self.assertIs(backend.identity_manager, self.im)
        with patch.object(firefox, 'installed_binary') as binary, patch.object(firefox, 'BrowserProxy') as bridge:
            with self.assertRaisesRegex(ValueError, 'Desktop'):
                backend.open(self.mobile, self.proxy, platform='tiktok')
            binary.assert_not_called()
            bridge.assert_not_called()
        self.assertFalse(self.sidecar.exists())

    def test_later_browser_runtime_reuses_desktop_context_without_ua_override(self):
        desktop = self.im.browser_context(self.mobile, engine='firefox', create=True)
        context_bytes = self.sidecar.read_bytes()
        new_manager = IdentityManager(self.pool, self.cfg.profiles_dir)
        backend = factory.build(self.cfg, new_manager)
        driver, browser, bridge = MagicMock(), MagicMock(), MagicMock()
        browser.pages = [MagicMock()]
        driver.firefox.launch_persistent_context.return_value = browser
        bridge.playwright = {'server': 'http://127.0.0.1:19001'}
        package, api = ModuleType('playwright'), ModuleType('playwright.sync_api')
        package.__path__ = []
        api.sync_playwright = Mock(return_value=SimpleNamespace(start=Mock(return_value=driver)))
        package.sync_api = api
        with patch.dict(sys.modules, {'playwright': package, 'playwright.sync_api': api}), \
                patch.object(firefox, 'installed_binary', return_value=self.root / 'fixture.exe'), \
                patch.object(firefox, 'BrowserProxy', return_value=bridge), \
                patch.object(CookieJar, 'load', return_value=[]), \
                patch('socialscraper.net.browser_window.announce'), \
                patch('socialscraper.net.browser_window.closed'), \
                patch('socialscraper.window_notification.window_opened'):
            with backend.session(self.mobile, self.proxy, headless=True, platform='tiktok') as handle:
                self.assertEqual(handle.identity, desktop)
                self.assertIs(handle.proxy, self.proxy)
            options = driver.firefox.launch_persistent_context.call_args.kwargs
            self.assertEqual(Path(options['user_data_dir']),
                             (self.cfg.profiles_dir / '_firefox' / desktop.name / 'tiktok').resolve())
            self.assertNotIn('user_agent', options)
            self.assertNotIn('is_mobile', options)
            browser.add_init_script.assert_not_called()
            browser.close.assert_called_once()
            bridge.close.assert_called_once()
        self.assertEqual(self.sidecar.read_bytes(), context_bytes)
        self.assertEqual(self.original.read_bytes(), self.original_bytes)


class TikTokBrowserHandoffTests(HandoffFixture):
    def setUp(self):
        super().setUp()
        self.cancelled = threading.Event()
        self.konto = {'identity': self.mobile.name, 'country': 'de', 'phase': 'app'}
        self.credentials = {'email': ' fixture@example.test ', 'passwort': 'offline-fixture'}
        self.stack.enter_context(patch('socialscraper.accountvault.get', return_value=self.credentials))
        self.stack.enter_context(patch('socialscraper.net.proxymode.make_pool', return_value=self.pool))
        self.stack.enter_context(patch('socialscraper.net.proxymode.is_anonymous', return_value=False))
        self.stack.enter_context(patch('socialscraper.net.accountproxy.Resolver', return_value=self.route))
        # Preserve real IdentityManager creation inside browser_context().
        manager_class = IdentityManager
        self.stack.enter_context(patch('socialscraper.net.identity.IdentityManager',
            side_effect=lambda pool, profiles_dir=None: self.im if profiles_dir is not None else manager_class(pool)))
        self.backend = MagicMock()
        self.page = self.backend.page.return_value
        self.values = {}
        self.page.fill.side_effect = lambda selector, value: self.values.update({selector: value})
        self.page.evaluate.side_effect = lambda script, selector: self.values.get(selector)
        self.build = self.stack.enter_context(patch.object(factory, 'build', return_value=self.backend))
        self.stack.enter_context(patch.object(tiktok, '_dismiss'))
        self.shutdown = self.stack.enter_context(patch('socialscraper.cli.shutdown_runtime'))
        self.save = self.stack.enter_context(patch.object(CookieJar, 'save'))
        self.signup = self.stack.enter_context(patch('socialscraper.signup.create',
                                                    side_effect=AssertionError('No signup fallback')))
        self.notify = Mock()
        self.clock = 0.0
        self.stack.enter_context(patch.object(tiktok.time, 'monotonic', side_effect=lambda: self.clock))
        self.page.wait_for_timeout.side_effect = self.advance
        self.old = self.cookie('old-session')
        self.fresh = self.cookie('new-session')
        self.backend.read_cookies.side_effect = [self.old, self.fresh]

    def advance(self, ms):
        self.clock += ms / 1000
        if self.clock > 30:
            raise AssertionError('Bounded fake clock exhausted')

    @staticmethod
    def cookie(value, name='sessionid'):
        return [{'name': name, 'value': value, 'domain': '.tiktok.com', 'path': '/'}]

    def login(self):
        return tiktok.web_anmelden(self.cfg, self.konto, stop=self.cancelled.is_set,
                                   melden=self.notify, timeout=1)

    def test_rotated_session_after_verified_credentials_uses_original_proxy_and_identity(self):
        self.assertTrue(self.login())
        person, proxy = self.backend.session.call_args.args
        self.assertEqual((person.name, person.mobile, person.country), (self.mobile.name, False, 'de'))
        self.assertIs(proxy, self.proxy)
        self.route.proxy_for.assert_called_once_with(self.mobile.name)
        self.page.fill.assert_any_call(tiktok.LOGIN_SEL['email'][0], 'fixture@example.test')
        self.page.fill.assert_any_call(tiktok.LOGIN_SEL['pass'][0], 'offline-fixture')
        self.page.click.assert_called_once_with(tiktok.LOGIN_SEL['senden'][0])
        self.save.assert_called_once_with(self.mobile.name, 'tiktok', CookieJar.for_platform(self.fresh, 'tiktok'))
        self.assertEqual(self.original.read_bytes(), self.original_bytes)
        self.assertEqual(self.konto['phase'], 'app')
        self.signup.assert_not_called()
        self.shutdown.assert_called_once_with(self.backend, None)

    def test_existing_cookie_or_alias_of_same_token_does_not_confirm_login(self):
        for after in (self.old, self.cookie('old-session', 'sessionid_ss')):
            with self.subTest(cookie_name=after[0]['name']):
                self.backend.read_cookies.side_effect = [self.old, after]
                self.assertFalse(self.login())
                self.save.assert_not_called()
        self.signup.assert_not_called()

    def test_fill_failure_or_readback_mismatch_never_submits_or_saves(self):
        for failure in ('fill', 'readback'):
            with self.subTest(failure=failure):
                self.page.fill.side_effect = (RuntimeError('Missing field') if failure == 'fill'
                                             else lambda selector, value: self.values.update({selector: value}))
                self.page.evaluate.side_effect = lambda *args: None
                self.assertFalse(self.login())
                self.page.click.assert_not_called()
                self.backend.read_cookies.assert_not_called()
                self.save.assert_not_called()

    def test_user_changed_credential_before_submit_is_not_overwritten(self):
        reads = 0
        def changed(script, selector):
            nonlocal reads
            reads += 1
            if reads >= 3:
                return 'changed-by-human'
            return self.values.get(selector)
        self.page.evaluate.side_effect = changed
        self.assertFalse(self.login())
        self.assertEqual(self.page.fill.call_count, 2)
        self.page.click.assert_not_called()
        self.save.assert_not_called()

    def test_unsuccessful_submit_cannot_promote_a_fresh_cookie(self):
        self.page.click.side_effect = RuntimeError('No submit button')
        self.assertFalse(self.login())
        self.assertEqual(self.page.click.call_count, len(tiktok.LOGIN_SEL['senden']))
        self.assertEqual(self.backend.read_cookies.call_count, 1)
        self.save.assert_not_called()

    def test_missing_proxy_or_cancelled_proxy_lookup_prevents_context_and_runtime(self):
        self.route.proxy_for.return_value = None
        self.pool.acquire.side_effect = None
        self.pool.acquire.return_value = None
        with self.assertRaisesRegex(FinalError, 'Proxy'):
            self.login()
        self.route.proxy_for.side_effect = lambda name: (self.cancelled.set(), self.proxy)[1]
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.login()
        self.assertFalse(self.sidecar.exists())
        self.build.assert_not_called()
        self.save.assert_not_called()

    def test_cancellation_during_verified_readback_prevents_submit(self):
        self.page.evaluate.side_effect = lambda script, selector: (self.cancelled.set(), self.values.get(selector))[1]
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.login()
        self.page.fill.assert_called_once()
        self.page.click.assert_not_called()
        self.save.assert_not_called()

    def test_cancellation_during_submit_or_fresh_cookie_read_prevents_save(self):
        for stage in ('submit', 'cookies'):
            with self.subTest(stage=stage):
                self.cancelled.clear()
                self.page.click.side_effect = lambda *args: self.cancelled.set() if stage == 'submit' else None
                reads = 0
                def read(*args):
                    nonlocal reads
                    reads += 1
                    if reads == 1:
                        return self.old
                    self.cancelled.set()
                    return self.fresh
                self.backend.read_cookies.side_effect = read
                with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                    self.login()
                self.save.assert_not_called()

    def test_failed_handoff_keeps_persisted_app_phase_and_never_registers(self):
        now = datetime.datetime(2026, 10, 15, 8, tzinfo=datetime.timezone.utc)
        with Store(self.cfg.db_path) as store:
            account = repo.add_account(store.db, 'tiktok', 'Offline fixture')
            store.db.execute('UPDATE accounts SET identity=?, phase=?, app_bis=? WHERE id=?',
                             (self.mobile.name, 'app', repo.iso(now - datetime.timedelta(seconds=1)), account['id']))
            store.db.commit()
        login = tiktok.web_anmelden
        with patch.object(tiktok, 'web_anmelden', side_effect=lambda *args, **kwargs: login(*args, timeout=1, **kwargs)):
            for cancelled in (False, True):
                with self.subTest(cancelled=cancelled):
                    self.page.click.reset_mock()
                    self.backend.read_cookies.side_effect = None
                    self.backend.read_cookies.return_value = self.old
                    self.page.click.side_effect = lambda *args: self.cancelled.set() if cancelled else None
                    warmup._wechsel_faellige(self.cfg, self.cfg.db_path, konto_id=account['id'], now=now,
                                            melden=self.notify, stop=self.cancelled.is_set)
                    self.page.click.assert_called_once()
                    with Store(self.cfg.db_path) as store:
                        self.assertEqual(repo.get_account(store.db, account['id'])['phase'], 'app')
                    self.save.assert_not_called()
        self.signup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
