"""R44: no fabricated location or implicit permission in mocked browser starts."""
import contextlib
import importlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from socialscraper.backends.local import LocalBackend
from socialscraper.net.fingerprint import build


class _ImmediateRunner:
    """Complete only fake, immediately ready coroutines; never create an event loop."""
    def run(self, coroutine, **_kwargs):
        try:
            for _ in range(8):
                if coroutine.send(None) is not None:
                    raise AssertionError('An actual asynchronous operation was attempted')
        except StopIteration as result:
            return result.value
        finally:
            coroutine.close()
        raise AssertionError('Fake coroutine did not finish within its fixed bound')


class BrowserPrivacyDefaults(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='privacy-defaults-')))
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        for method in ('connect', 'connect_ex', 'sendto', 'bind'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch.dict(os.environ, {'SS_REQUIRE_PROXY': '', 'SS_EMBED_BROWSER': ''}))
        for name in ('socialscraper.net.browser_window.announce', 'socialscraper.net.browser_window.closed',
                     'socialscraper.window_notification.window_opened'):
            self.stack.enter_context(patch(name))

    @staticmethod
    def identity(*, mobile=False, legacy=False):
        fp = build('privacy_defaults', 'de', mobile=mobile)
        if legacy:
            # A persisted profile value is not a user permission or location input.
            fp.geolocation = {'latitude': 52.6, 'longitude': 13.5, 'accuracy': 50}
        return SimpleNamespace(name='privacy_defaults', fp=fp)

    @contextlib.contextmanager
    def local(self, *, proxy=None, bridge_error=None):
        pw, context = MagicMock(), MagicMock()
        context.pages = []
        pw.chromium.launch.return_value.version = '154.0.8037.93'
        pw.chromium.launch_persistent_context.return_value = context
        starter = MagicMock(return_value=pw)
        module = types.ModuleType('patchright.sync_api')
        module.sync_playwright = lambda: SimpleNamespace(start=starter)
        bridge = MagicMock() if proxy is not None else None
        route = {'server': 'http://127.0.0.1:9'} if proxy is not None else None
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {
                'patchright': types.ModuleType('patchright'), 'patchright.sync_api': module}))
            options = stack.enter_context(patch('socialscraper.net.browserproxy.options',
                return_value=(route, bridge), side_effect=bridge_error))
            backend = LocalBackend(profiles_dir=self.root / 'local')
            yield SimpleNamespace(backend=backend, pw=pw, context=context, starter=starter,
                options=options, route=route, bridge=bridge)

    @contextlib.contextmanager
    def nodriver(self, *, proxy=None, bridge_error=None):
        uc = types.ModuleType('nodriver')
        command = lambda name: lambda **kwargs: (name, kwargs)
        uc.cdp = SimpleNamespace(
            emulation=SimpleNamespace(set_timezone_override=command('timezone'),
                set_locale_override=command('locale'), set_geolocation_override=command('geolocation')),
            browser=SimpleNamespace(grant_permissions=command('grant'),
                PermissionType=SimpleNamespace(GEOLOCATION='geolocation')))
        tab = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
        browser = SimpleNamespace(get=AsyncMock(return_value=tab), stop=MagicMock(), _process=None)
        uc.start = AsyncMock(return_value=browser)
        bridge = MagicMock() if proxy is not None else None
        route = {'server': 'http://127.0.0.1:9'} if proxy is not None else None
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {'nodriver': uc}))
            engine = importlib.import_module('socialscraper.backends.nodriver_engine')
            stack.enter_context(patch.object(engine, 'uc', uc))
            options = stack.enter_context(patch('socialscraper.net.browserproxy.options',
                return_value=(route, bridge), side_effect=bridge_error))
            backend = object.__new__(engine.NodriverBackend)
            backend.profiles = self.root / 'nodriver'
            backend.runner = _ImmediateRunner()
            yield SimpleNamespace(backend=backend, uc=uc, tab=tab, browser=browser,
                options=options, route=route, bridge=bridge)

    def test_local_starts_with_an_explicitly_empty_permission_list(self):
        for mobile, headless in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(mobile=mobile, headless=headless), self.local() as case:
                handle = case.backend.open(self.identity(mobile=mobile), None, headless=headless, platform='mail')
                try:
                    kwargs = case.pw.chromium.launch_persistent_context.call_args.kwargs
                    self.assertEqual(kwargs.get('permissions'), [])
                    case.context.grant_permissions.assert_not_called()
                finally:
                    case.backend.close(handle)

    def test_local_does_not_apply_generated_or_legacy_geolocation(self):
        for mobile, legacy in ((False, False), (False, True), (True, True)):
            with self.subTest(mobile=mobile, legacy=legacy), self.local() as case:
                identity = self.identity(mobile=mobile, legacy=legacy)
                handle = case.backend.open(identity, None, headless=True, platform='mail')
                try:
                    kwargs = case.pw.chromium.launch_persistent_context.call_args.kwargs
                    self.assertNotIn('geolocation', kwargs)
                    case.context.set_geolocation.assert_not_called()
                    self.assertEqual(kwargs['timezone_id'], identity.fp.timezone)
                finally:
                    case.backend.close(handle)

    def test_nodriver_permissions_are_explicitly_empty_on_every_start(self):
        for headless in (False, True):
            with self.subTest(headless=headless), self.nodriver() as case:
                handle = case.backend.open(self.identity(), None, headless=headless, platform='mail')
                try:
                    grants = [options for name, options in (call.args[0] for call in case.tab.send.call_args_list)
                        if name == 'grant']
                    self.assertEqual(grants, [{'permissions': []}])
                finally:
                    case.backend.close(handle)

    def test_nodriver_does_not_send_generated_or_legacy_geolocation(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), self.nodriver() as case:
                handle = case.backend.open(self.identity(legacy=legacy), None, headless=True, platform='mail')
                try:
                    commands = [call.args[0] for call in case.tab.send.call_args_list]
                    self.assertNotIn('geolocation', [name for name, _options in commands])
                    self.assertIn(('timezone', {'timezone_id': 'Europe/Berlin'}), commands)
                    self.assertIn(('locale', {'locale': 'de-DE'}), commands)
                finally:
                    case.backend.close(handle)

    def test_required_missing_proxy_is_rejected_before_runtime_start(self):
        for kind in ('local', 'nodriver'):
            with self.subTest(kind=kind), getattr(self, kind)() as case, \
                    patch.dict(os.environ, {'SS_REQUIRE_PROXY': '1'}):
                with self.assertRaisesRegex(ValueError, 'Proxy|Tor'):
                    case.backend.open(self.identity(), None, headless=True, platform='mail')
                case.options.assert_not_called()
                if kind == 'local':
                    case.starter.assert_not_called()
                else:
                    case.uc.start.assert_not_called()

    def test_privacy_defaults_keep_the_same_proxy_route_and_browser_limits(self):
        proxy = SimpleNamespace(scheme='http', user=None, anonymous=False)
        for kind in ('local', 'nodriver'):
            with self.subTest(kind=kind), getattr(self, kind)(proxy=proxy) as case, \
                    patch.dict(os.environ, {'SS_REQUIRE_PROXY': '1'}):
                handle = case.backend.open(self.identity(), proxy, headless=True, platform='mail')
                try:
                    case.options.assert_called_once_with(proxy)
                    if kind == 'local':
                        launches = [case.pw.chromium.launch.call_args.kwargs,
                            case.pw.chromium.launch_persistent_context.call_args.kwargs]
                        for kwargs in launches:
                            self.assertEqual(kwargs['proxy'], case.route)
                            self.assert_proxy_args(kwargs['args'])
                    else:
                        args = case.uc.start.call_args.kwargs['browser_args']
                        self.assertIn('--proxy-server=http://127.0.0.1:9', args)
                        self.assert_proxy_args(args)
                finally:
                    case.backend.close(handle)
                case.bridge.close.assert_called_once()

    def assert_proxy_args(self, args):
        self.assertIn('--disable-quic', args)
        self.assertIn('--proxy-bypass-list=<-loopback>', args)
        self.assertIn('--force-webrtc-ip-handling-policy=disable_non_proxied_udp', args)
        self.assertTrue(any(arg.startswith('--host-resolver-rules=MAP * ~NOTFOUND') for arg in args))

    def test_proxy_configuration_failure_never_falls_back_to_a_browser_start(self):
        error = ValueError('Offline proxy preparation failure')
        proxy = SimpleNamespace(scheme='http', user=None, anonymous=False)
        for kind in ('local', 'nodriver'):
            with self.subTest(kind=kind), getattr(self, kind)(proxy=proxy, bridge_error=error) as case:
                with self.assertRaises(ValueError) as raised:
                    case.backend.open(self.identity(), proxy, headless=True, platform='mail')
                self.assertIs(raised.exception, error)
                if kind == 'local':
                    case.pw.chromium.launch.assert_not_called()
                    case.pw.chromium.launch_persistent_context.assert_not_called()
                else:
                    case.uc.start.assert_not_called()

    def test_rejected_local_context_closes_version_probe_runtime_and_proxy_without_cookies(self):
        proxy = SimpleNamespace(scheme='http', user=None, anonymous=False)
        error = RuntimeError('Offline browser rejected context initialization')
        with self.local(proxy=proxy) as case, patch.dict(os.environ, {'SS_REQUIRE_PROXY': '1'}):
            case.pw.chromium.launch_persistent_context.side_effect = error
            with patch.object(case.backend, '_apply_cookies') as cookies:
                with self.assertRaises(RuntimeError) as raised:
                    case.backend.open(self.identity(), proxy, headless=True, platform='tiktok')
                self.assertIs(raised.exception, error)
                cookies.assert_not_called()
            kwargs = case.pw.chromium.launch_persistent_context.call_args.kwargs
            self.assertEqual(kwargs['permissions'], [])
            self.assertNotIn('geolocation', kwargs)
            case.pw.chromium.launch.assert_called_once()
            case.pw.chromium.launch.return_value.close.assert_called_once()
            case.pw.stop.assert_called_once()
            case.bridge.close.assert_called_once()
            # No context was returned by the failed launch; none may be used.
            case.context.add_cookies.assert_not_called()
            self.assertIsNone(case.backend._pw)
            self.assertEqual(case.backend._users, 0)

    def test_rejected_nodriver_permissions_close_owned_tab_browser_and_proxy_without_cookies(self):
        proxy = SimpleNamespace(scheme='http', user=None, anonymous=False)
        error = RuntimeError('Offline browser rejected permission initialization')
        with self.nodriver(proxy=proxy) as case, patch.dict(os.environ, {'SS_REQUIRE_PROXY': '1'}):

            async def send(command):
                name, options = command
                if name == 'grant':
                    self.assertEqual(options, {'permissions': []})
                    raise error

            case.tab.send.side_effect = send
            with patch.object(case.backend, '_apply_cookies') as cookies:
                with self.assertRaises(RuntimeError) as raised:
                    case.backend.open(self.identity(), proxy, headless=True, platform='tiktok')
                self.assertIs(raised.exception, error)
                cookies.assert_not_called()
            commands = [call.args[0] for call in case.tab.send.call_args_list]
            self.assertEqual(commands[-1], ('grant', {'permissions': []}))
            self.assertNotIn('geolocation', [name for name, _options in commands])
            case.uc.start.assert_awaited_once()
            case.browser.get.assert_awaited_once_with('about:blank')
            case.tab.close.assert_awaited_once()
            case.browser.stop.assert_called_once()
            case.bridge.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
