"""Offline Chrome-version lifecycle checks using owned driver/context fakes."""
import contextlib
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.backends.local import LocalBackend
from socialscraper.net.fingerprint import Fingerprint, build


class BrowserProfileConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))
        self.stack.enter_context(patch('socialscraper.backends.base.CookieJar.load',
                                      side_effect=AssertionError('Real cookies must not be read')))
        self.stack.enter_context(patch('socialscraper.net.browser_window.announce'))
        self.stack.enter_context(patch('socialscraper.net.browser_window.closed'))
        self.stack.enter_context(patch('socialscraper.window_notification.window_opened'))
        self.proxy = SimpleNamespace(anonymous=True)
        self.stack.enter_context(patch('socialscraper.net.browserproxy.options',
                                      return_value=({'server': 'http://127.0.0.1:9'}, None)))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))

    def driver(self, version):
        driver = MagicMock()
        driver.chromium.launch.return_value.version = version

        def context(**kwargs):
            result = MagicMock()
            result.pages = [MagicMock()]
            return result

        driver.chromium.launch_persistent_context.side_effect = context
        return driver

    @contextlib.contextmanager
    def drivers(self, *drivers):
        starter = MagicMock(side_effect=drivers)
        with patch('patchright.sync_api.sync_playwright',
                   return_value=SimpleNamespace(start=starter)):
            yield starter

    def profile(self, mobile):
        stored = build('anon_public', 'de', mobile=mobile).to_dict()
        stored['user_agent'] = stored['user_agent'].replace('{chrome}', '99.0.0.0')
        return SimpleNamespace(name='anon_public', fp=Fingerprint.from_dict(stored))

    def assert_version(self, driver, handle, version, mobile):
        major = version.split('.')[0]
        options = driver.chromium.launch_persistent_context.call_args.kwargs
        agent = next(argument for argument in options['args'] if argument.startswith('--user-agent='))
        self.assertIn('Chrome/' + major + '.0.0.0', agent)
        self.assertNotIn('Chrome/99.', agent)
        if mobile:
            method, override = handle._context.new_cdp_session.return_value.send.call_args.args
            self.assertEqual(method, 'Emulation.setUserAgentOverride')
            self.assertEqual(override['userAgent'], agent.removeprefix('--user-agent='))
            actual = [brand['version'] for brand in override['userAgentMetadata']['fullVersionList']
                      if brand['brand'] == 'Google Chrome']
            self.assertEqual(actual, [version])
        else:
            self.assertEqual(options['user_agent'], agent.removeprefix('--user-agent='))

    def test_restart_after_last_close_uses_the_new_chrome_version(self):
        for mobile in (False, True):
            with self.subTest(mobile=mobile):
                old, new = self.driver('141.0.1000.1'), self.driver('142.0.2000.2')
                backend = LocalBackend(profiles_dir=self.root / str(mobile))
                profile = self.profile(mobile)
                saved = profile.fp.to_dict()
                with self.drivers(old, new) as starter:
                    first = backend.open(profile, self.proxy, headless=True)
                    self.assert_version(old, first, '141.0.1000.1', mobile)
                    backend.close(first)
                    second = backend.open(profile, self.proxy, headless=True)
                    try:
                        self.assert_version(new, second, '142.0.2000.2', mobile)
                    finally:
                        backend.close(second)
                self.assertEqual(starter.call_count, 2)
                old.stop.assert_called_once()
                new.stop.assert_called_once()
                self.assertEqual(profile.fp.to_dict(), saved)

    def test_dead_driver_cleanup_does_not_reuse_its_version_on_restart(self):
        old, new = self.driver('141.0.1000.1'), self.driver('143.0.3000.3')
        backend = LocalBackend(profiles_dir=self.root)
        profile = self.profile(True)
        with self.drivers(old, new):
            first = backend.open(profile, self.proxy, headless=True)
            first._context.close.side_effect = RuntimeError('Connection closed while reading from the driver')
            with self.assertRaisesRegex(RuntimeError, 'closed while reading from the driver'):
                backend.close(first)
            second = backend.open(profile, self.proxy, headless=True)
            try:
                self.assert_version(new, second, '143.0.3000.3', True)
            finally:
                backend.close(second)
        old.stop.assert_called_once()
        new.stop.assert_called_once()

    def test_live_sessions_keep_the_same_driver_version_until_the_last_close(self):
        driver = self.driver('142.0.2000.2')
        backend = LocalBackend(profiles_dir=self.root)
        profile = self.profile(True)
        with self.drivers(driver) as starter:
            first = backend.open(profile, self.proxy, headless=True, platform='tiktok')
            second = backend.open(profile, self.proxy, headless=True, platform='instagram')
            backend.close(first)
            driver.stop.assert_not_called()
            third = backend.open(profile, self.proxy, headless=True, platform='reddit')
            try:
                self.assert_version(driver, third, '142.0.2000.2', True)
                driver.chromium.launch.assert_called_once()
                starter.assert_called_once()
            finally:
                backend.close(second)
                backend.close(third)
        driver.stop.assert_called_once()

    def test_late_close_of_an_old_handle_preserves_the_new_driver_cache(self):
        old, new = self.driver('141.0.1000.1'), self.driver('144.0.4000.4')
        backend = LocalBackend(profiles_dir=self.root)
        profile = self.profile(True)
        with self.drivers(old, new):
            old_handle = backend.open(profile, self.proxy, headless=True)
            backend._reset_driver(old)
            current = backend.open(profile, self.proxy, headless=True)
            backend.close(old_handle)
            later = backend.open(profile, self.proxy, headless=True, platform='instagram')
            try:
                self.assert_version(new, later, '144.0.4000.4', True)
                new.chromium.launch.assert_called_once()
                new.stop.assert_not_called()
            finally:
                backend.close(current)
                backend.close(later)
        new.stop.assert_called_once()

    def test_unknown_version_fails_mobile_before_context_and_keeps_desktop_native(self):
        for mobile in (False, True):
            with self.subTest(mobile=mobile):
                driver = self.driver('unknown-version')
                backend = LocalBackend(profiles_dir=self.root / str(mobile))
                with self.drivers(driver):
                    if mobile:
                        with self.assertRaises(ValueError):
                            backend.open(self.profile(True), self.proxy, headless=True)
                        driver.chromium.launch_persistent_context.assert_not_called()
                    else:
                        handle = backend.open(self.profile(False), self.proxy, headless=True)
                        try:
                            options = driver.chromium.launch_persistent_context.call_args.kwargs
                            self.assertNotIn('user_agent', options)
                            self.assertFalse(any(argument.startswith('--user-agent=') for argument in options['args']))
                        finally:
                            backend.close(handle)
                driver.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
