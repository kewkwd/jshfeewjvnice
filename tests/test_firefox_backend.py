"""Firefox startup and runtime checks with only temporary files and mocked drivers."""
import contextlib
import ctypes
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from socialscraper.backends import firefox
from socialscraper.net import firefox_runtime as runtime
from socialscraper.net.fingerprint import build


class OfflineFixture(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, name, side_effect=AssertionError('Network blocked')))
        for name in ('connect', 'connect_ex', 'sendto', 'bind'):
            self.stack.enter_context(patch.object(socket.socket, name, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch.object(ctypes, 'WinDLL', create=True,
                                             side_effect=AssertionError('Native API blocked')))
        self.stack.enter_context(patch.object(time, 'sleep', side_effect=AssertionError('Real sleep blocked')))
        self.stack.enter_context(patch('socialscraper.backends.base.CookieJar.load',
                                      side_effect=AssertionError('Private cookies must not be read')))


class FirefoxBackendTests(OfflineFixture):
    def setUp(self):
        super().setUp()
        self.preflight = self.stack.enter_context(patch.object(firefox, 'installed_binary',
                                                              return_value=self.root / 'fixture.exe'))
        self.announce = self.stack.enter_context(patch('socialscraper.net.browser_window.announce'))
        self.stack.enter_context(patch('socialscraper.net.browser_window.closed'))
        self.notification = self.stack.enter_context(patch('socialscraper.window_notification.window_opened'))
        self.bridges = []
        self.contexts = []
        self.driver = MagicMock()
        self.driver.chromium.launch.side_effect = AssertionError('No Chrome version probe')
        self.driver.chromium.launch_persistent_context.side_effect = AssertionError('No Chrome launch')
        self.driver.firefox.launch_persistent_context.side_effect = self.launch
        self.start = Mock(return_value=self.driver)
        self.sync = Mock(return_value=SimpleNamespace(start=self.start))
        package = ModuleType('playwright')
        package.__path__ = []
        api = ModuleType('playwright.sync_api')
        api.sync_playwright = self.sync
        package.sync_api = api
        self.stack.enter_context(patch.dict(sys.modules, {'playwright': package, 'playwright.sync_api': api}))
        self.bridge_factory = self.stack.enter_context(patch.object(firefox, 'BrowserProxy', side_effect=self.bridge))
        self.backend = firefox.FirefoxBackend(profiles_dir=self.root / 'profiles')
        self.identity = SimpleNamespace(name='offline_fixture', fp=build('offline_fixture', 'de'))
        self.proxy = SimpleNamespace(scheme='socks5', host='fixture.invalid', port=1000,
                                     user='fixture-user', password='fixture-password', anonymous=True)

    def bridge(self, proxy, **options):
        bridge = Mock()
        bridge.playwright = {'server': 'http://127.0.0.1:19001'}
        self.bridges.append(bridge)
        return bridge

    def launch(self, **options):
        context = MagicMock()
        context.pages = [MagicMock()]
        context.new_cdp_session.side_effect = AssertionError('Firefox cannot use Chrome CDP')
        self.contexts.append(context)
        return context

    def opened(self, backend=None, **options):
        backend = backend or self.backend
        handle = backend.open(self.identity, self.proxy, **options)
        self.stack.callback(backend.close, handle)
        return handle

    def assert_no_start(self):
        self.preflight.assert_not_called()
        self.bridge_factory.assert_not_called()
        self.start.assert_not_called()
        self.assertFalse((self.backend.profiles / '_firefox').exists())

    def test_startup_policy_exists_before_launch_and_keeps_native_ua(self):
        stored = self.identity.fp.to_dict()
        original_launch = self.launch

        def inspect_launch(**options):
            source = (Path(options['user_data_dir']) / 'user.js').read_text(encoding='utf8')
            written = dict(json.loads('[' + line[len('user_pref('):-2] + ']')
                           for line in source.splitlines() if line.startswith('user_pref('))
            self.assertEqual(written, options['firefox_user_prefs'])
            for key, expected in {'network.proxy.type': 1, 'network.proxy.no_proxies_on': '',
                                  'network.proxy.failover_direct': False, 'network.dns.disabled': True,
                                  'network.trr.mode': 5, 'network.http.http3.enable': False,
                                  'media.peerconnection.enabled': False}.items():
                self.assertEqual(written[key], expected)
            self.assertEqual(written['network.proxy.http_port'], 19001)
            self.assertEqual(written['network.proxy.ssl_port'], 19001)
            for secret in ('fixture.invalid', 'fixture-user', 'fixture-password'):
                self.assertNotIn(secret, source)
            self.assertEqual(options['proxy'], {'server': 'http://127.0.0.1:19001'})
            self.assertEqual(options['env']['MOZ_DISABLE_NONLOCAL_CONNECTIONS'], '1')
            self.assertEqual(options['env']['MOZ_DISABLE_NONLOCAL_CONNECTIONS_ALLOW'], '')
            self.assertNotIn('user_agent', options)
            self.assertNotIn('is_mobile', options)
            self.assertNotIn('args', options)
            self.assertEqual(options['permissions'], [])
            self.assertFalse(options['accept_downloads'])
            self.assertEqual(options['service_workers'], 'block')
            self.assertEqual(options['locale'], stored['locale'])
            self.assertEqual(options['timezone_id'], stored['timezone'])
            return original_launch(**options)

        self.driver.firefox.launch_persistent_context.side_effect = inspect_launch
        with patch.dict(os.environ, {'MOZ_DISABLE_NONLOCAL_CONNECTIONS': '0',
                                     'MOZ_DISABLE_NONLOCAL_CONNECTIONS_ALLOW': 'fixture.invalid'}):
            handle = self.opened(headless=True, platform='tiktok')
        self.assertEqual(handle.backend, 'firefox')
        self.assertEqual(self.identity.fp.to_dict(), stored)
        self.bridge_factory.assert_called_once_with(self.proxy, allow_no_auth=True)
        self.contexts[0].new_cdp_session.assert_not_called()
        self.contexts[0].add_init_script.assert_not_called()
        self.driver.chromium.launch.assert_not_called()
        self.announce.assert_called_once_with(handle, True)
        self.notification.assert_called_once_with(handle, headless=True)

    def test_missing_or_unsupported_proxy_fails_before_runtime_and_profile_changes(self):
        for proxy in (None, SimpleNamespace(scheme='https'), SimpleNamespace(scheme='socks4')):
            with self.subTest(proxy=proxy):
                with self.assertRaises(ValueError):
                    self.backend.open(self.identity, proxy)
                self.assert_no_start()

    def test_redirected_bundle_uses_resolved_binary_path_for_native_dll_lookup(self):
        physical = self.root / 'package-cache' / 'firefox.exe'
        logical = Mock()
        logical.resolve.return_value = physical
        self.preflight.return_value = logical
        self.opened(headless=True, platform='mail')
        logical.resolve.assert_called_once_with()
        options = self.driver.firefox.launch_persistent_context.call_args.kwargs
        self.assertEqual(options['executable_path'], str(physical))
        self.assertNotIn('channel', options)

    def test_mobile_profile_is_rejected_before_start(self):
        self.identity.fp = build(self.identity.name, 'de', mobile=True)
        with self.assertRaisesRegex(ValueError, 'Desktop'):
            self.backend.open(self.identity, self.proxy)
        self.assert_no_start()

    def test_invalid_identity_or_platform_cannot_escape_profile_root(self):
        for name, platform in (('../escape', None), ('CON', None), ('safe', '../escape'),
                               ('safe', '/absolute'), ('safe', ''), ('safe', 12)):
            with self.subTest(name=name, platform=platform):
                identity = SimpleNamespace(name=name, fp=self.identity.fp)
                with self.assertRaises(ValueError):
                    self.backend.open(identity, self.proxy, platform=platform)
                self.assert_no_start()

    def test_missing_runtime_is_read_only_and_prevents_bridge_or_driver(self):
        self.preflight.side_effect = FileNotFoundError('Runtime missing')
        with self.assertRaisesRegex(FileNotFoundError, 'Runtime missing'):
            self.backend.open(self.identity, self.proxy)
        self.bridge_factory.assert_not_called()
        self.start.assert_not_called()
        self.assertFalse((self.backend.profiles / '_firefox').exists())

    def test_profiles_are_separate_from_chrome_and_other_platforms(self):
        chrome_profile = self.backend.profiles / self.identity.name / 'mail'
        chrome_profile.mkdir(parents=True)
        marker = chrome_profile / 'user.js'
        marker.write_text('chrome fixture', encoding='utf8')
        handles = [self.opened(platform='mail'), self.opened(platform='tiktok'), self.opened()]
        paths = [Path(call.kwargs['user_data_dir'])
                 for call in self.driver.firefox.launch_persistent_context.call_args_list]
        self.assertEqual(len(set(paths)), 3)
        for path in paths:
            self.assertTrue(path.is_relative_to((self.backend.profiles / '_firefox').resolve()))
            self.assertNotEqual(path, chrome_profile.resolve())
        self.assertEqual(marker.read_text(encoding='utf8'), 'chrome fixture')
        for handle in handles:
            self.backend.close(handle)
        self.driver.stop.assert_called_once()

    def test_profile_lock_blocks_second_backend_and_releases_after_close(self):
        first = self.opened(platform='mail')
        second_backend = firefox.FirefoxBackend(profiles_dir=self.backend.profiles)
        with self.assertRaisesRegex(RuntimeError, 'bereits verwendet'):
            second_backend.open(self.identity, self.proxy, platform='mail')
        self.assertEqual(len(self.bridges), 1)
        self.assertEqual(self.start.call_count, 1)
        self.backend.close(first)
        second = self.opened(second_backend, platform='mail')
        self.assertIsNotNone(second._context)
        self.assertEqual(len(self.bridges), 2)

    def test_launch_failure_closes_bridge_driver_and_unlocks_profile(self):
        original = RuntimeError('Fixture launch failure')
        self.driver.firefox.launch_persistent_context.side_effect = original
        with self.assertRaises(RuntimeError) as caught:
            self.backend.open(self.identity, self.proxy, platform='mail')
        self.assertIs(caught.exception, original)
        self.bridges[0].close.assert_called_once()
        self.driver.stop.assert_called_once()
        self.assertIsNone(self.backend._pw)
        self.assertEqual(self.backend._users, 0)
        self.driver.firefox.launch_persistent_context.side_effect = self.launch
        self.opened(platform='mail')

    def test_failed_launch_retries_with_a_new_profile_directory(self):
        self.driver.firefox.launch_persistent_context.side_effect = RuntimeError('fixture-launch')
        with self.assertRaisesRegex(RuntimeError, 'fixture-launch'):
            self.backend.open(self.identity, self.proxy, platform='tiktok')
        first = self.driver.firefox.launch_persistent_context.call_args.kwargs['user_data_dir']
        self.driver.firefox.launch_persistent_context.side_effect = self.launch
        handle = self.opened(platform='tiktok')
        self.assertNotEqual(first, self.driver.firefox.launch_persistent_context.call_args.kwargs['user_data_dir'])
        self.assertTrue(handle._profile_selection.isolated)

    def test_bridge_failure_releases_profile_without_starting_driver(self):
        self.bridge_factory.side_effect = OSError('Fixture bridge failure')
        with self.assertRaisesRegex(OSError, 'Fixture bridge failure'):
            self.backend.open(self.identity, self.proxy)
        self.start.assert_not_called()
        self.bridge_factory.side_effect = self.bridge
        self.opened()

    def test_preferences_failure_closes_bridge_and_unlocks_without_driver(self):
        with patch.object(firefox, 'write_startup_preferences', side_effect=OSError('Fixture preference failure')):
            with self.assertRaisesRegex(OSError, 'Fixture preference failure'):
                self.backend.open(self.identity, self.proxy)
        self.bridges[0].close.assert_called_once()
        self.start.assert_not_called()
        self.opened()

    def test_driver_start_failure_closes_bridge_and_unlocks_profile(self):
        self.start.side_effect = RuntimeError('Fixture driver start failure')
        with self.assertRaisesRegex(RuntimeError, 'Fixture driver start failure'):
            self.backend.open(self.identity, self.proxy)
        self.bridges[0].close.assert_called_once()
        self.driver.firefox.launch_persistent_context.assert_not_called()
        self.start.side_effect = None
        self.opened()

    def test_cookie_failure_closes_all_resources_and_releases_profile(self):
        with patch.object(self.backend, '_apply_cookies', side_effect=RuntimeError('Fixture cookie failure')):
            with self.assertRaisesRegex(RuntimeError, 'Fixture cookie failure'):
                self.backend.open(self.identity, self.proxy)
        self.contexts[0].close.assert_called_once()
        self.bridges[0].close.assert_called_once()
        self.driver.stop.assert_called_once()
        self.opened()

    def test_context_close_failure_still_releases_bridge_driver_and_lock(self):
        handle = self.opened()
        handle._context.close.side_effect = RuntimeError('Fixture close failure')
        with self.assertRaisesRegex(RuntimeError, 'Fixture close failure'):
            self.backend.close(handle)
        self.bridges[0].close.assert_called_once()
        self.driver.stop.assert_called_once()
        self.assertIsNone(handle._profile_lock)
        self.opened()

    def test_embedding_failure_keeps_context_usable_and_notifies_user(self):
        self.announce.side_effect = RuntimeError('Fixture embedding failure')
        handle = self.opened()
        self.assertIs(handle._context, self.contexts[0])
        self.contexts[0].close.assert_not_called()
        self.notification.assert_called_once_with(handle, headless=False)

    def test_proxy_preferences_refuse_nonlocal_or_invalid_routes(self):
        for route in ('http://fixture.invalid:80', 'socks5://127.0.0.1:80',
                      'http://localhost:80', 'http://127.0.0.1', 'http://127.0.0.1:0',
                      'http://127.0.0.1:wrong', 'http://127.0.0.1:65536'):
            with self.subTest(route=route):
                with self.assertRaises(ValueError):
                    firefox.proxy_preferences(route)

    def test_atomic_startup_preferences_preserve_previous_file_on_io_failure(self):
        profile = self.root / 'policy'
        profile.mkdir()
        target = profile / 'user.js'
        neighbour = profile / 'user.js.foreign.tmp'
        original = b'previous complete policy\n'
        neighbour.write_bytes(b'other writer')
        for operation in ('fsync', 'replace'):
            with self.subTest(operation=operation):
                target.write_bytes(original)
                with patch.object(firefox.os, operation, side_effect=OSError('Fixture disk failure')):
                    with self.assertRaisesRegex(OSError, 'Fixture disk failure'):
                        firefox.write_startup_preferences(profile, {'network.proxy.type': 1})
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(neighbour.read_bytes(), b'other writer')
                self.assertEqual({path.name for path in profile.iterdir()}, {'user.js', neighbour.name})


class FirefoxRuntimeTests(OfflineFixture):
    def setUp(self):
        super().setUp()
        self.package = self.root / 'package'
        self.package.mkdir()
        self.cache = self.root / 'cache'
        self.dist = SimpleNamespace(version=runtime.PLAYWRIGHT_VERSION,
                                    locate_file=Mock(return_value=self.package))
        self.distribution = self.stack.enter_context(patch.object(importlib.metadata, 'distribution',
                                                                  return_value=self.dist))
        self.stack.enter_context(patch.dict(os.environ, {'PLAYWRIGHT_BROWSERS_PATH': str(self.cache)}))
        self.stack.enter_context(patch.object(runtime.sys, 'platform', 'win32'))
        self.manifest = {'browsers': [{'name': 'firefox', 'revision': runtime.FIREFOX_REVISION,
                                      'browserVersion': runtime.FIREFOX_VERSION}]}
        self.write_manifest(self.manifest)

    def write_manifest(self, value):
        (self.package / 'browsers.json').write_text(json.dumps(value), encoding='utf8')

    def binary(self, cache):
        target = cache / ('firefox-' + runtime.FIREFOX_REVISION) / 'firefox/firefox.exe'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'offline fixture; never executable')
        return target

    def test_pinned_custom_runtime_path_is_found_without_launch_or_download(self):
        expected = self.binary(self.cache)
        self.assertEqual(runtime.installed_binary(), expected)
        self.distribution.assert_called_once_with('playwright')
        self.dist.locate_file.assert_called_once_with('playwright/driver/package')

    def test_local_and_default_cache_stay_with_the_application_runtime(self):
        for override, cache in [('0', self.package / '.local-browsers'),
                                 ('', self.package / '.local-browsers')]:
            with self.subTest(override=override):
                expected = self.binary(cache)
                with patch.dict(os.environ, {'PLAYWRIGHT_BROWSERS_PATH': override,
                                             'LOCALAPPDATA': str(self.root / 'localappdata')}):
                    self.assertEqual(runtime.installed_binary(), expected)

    def test_missing_playwright_distribution_is_reported_without_importing_driver(self):
        self.distribution.side_effect = importlib.metadata.PackageNotFoundError('playwright')
        with self.assertRaises(FileNotFoundError):
            runtime.installed_binary()
        valid, message = runtime.installation_status()
        self.assertFalse(valid)
        self.assertIn(runtime.PLAYWRIGHT_VERSION, message)

    def test_wrong_playwright_version_is_rejected(self):
        self.dist.version = '0.0.0'
        with self.assertRaises(ValueError):
            runtime.installed_binary()
        self.dist.locate_file.assert_not_called()
        self.assertFalse(runtime.installation_status()[0])

    def test_wrong_firefox_revision_or_version_is_rejected(self):
        self.binary(self.cache)
        for key, value in [('revision', '0'), ('browserVersion', '0.0')]:
            with self.subTest(field=key):
                entry = {**self.manifest['browsers'][0], key: value}
                self.write_manifest({'browsers': [entry]})
                with self.assertRaises(ValueError):
                    runtime.installed_binary()
                self.assertFalse(runtime.installation_status()[0])

    def test_missing_binary_has_actionable_non_success_status(self):
        with self.assertRaises(FileNotFoundError):
            runtime.installed_binary()
        valid, message = runtime.installation_status()
        self.assertFalse(valid)
        self.assertIn('Proxy', message)

    def test_incomplete_or_corrupted_manifest_returns_status_instead_of_crashing(self):
        path = self.package / 'browsers.json'
        contents = ['not json', 'null', '[]', '{}', '{"browsers": null}',
                    '{"browsers": [null]}', '{"browsers": []}',
                    '{"browsers": [{"name": "firefox"}]}']
        for content in contents:
            with self.subTest(content=content):
                path.write_text(content, encoding='utf8')
                valid, message = runtime.installation_status()
                self.assertFalse(valid)
                self.assertTrue(message)
        path.unlink()
        valid, message = runtime.installation_status()
        self.assertFalse(valid)
        self.assertTrue(message)

    def test_complete_runtime_status_reports_pinned_versions(self):
        self.binary(self.cache)
        valid, message = runtime.installation_status()
        self.assertTrue(valid)
        self.assertIn(runtime.FIREFOX_VERSION, message)
        self.assertIn(runtime.PLAYWRIGHT_VERSION, message)


if __name__ == '__main__':
    unittest.main()
