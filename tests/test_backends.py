import asyncio
import importlib
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from socialscraper.backends.async_runner import AsyncRunner
from socialscraper.backends.base import BrowserBackend, SessionHandle
from socialscraper.backends.local import LocalBackend
from socialscraper.backends.nodriver_shim import TabShim
from socialscraper.backends.sphere import SphereBackend
from socialscraper.net.fingerprint import build
from socialscraper.net.pool import SessionPool


class FakeBackend(BrowserBackend):
    def __init__(self):
        self.opened = []
        self.closed = []
        self.prepared = []

    def prepare(self, platform):
        self.prepared.append(platform)

    def open(self, identity, proxy, *, headless=False, platform=None):
        handle = SessionHandle(identity, proxy, "fake", platform=platform)
        handle.page = object()
        self.opened.append((handle, headless, platform))
        return handle

    def close(self, handle):
        self.closed.append(handle)

    def page(self, handle):
        return handle.page


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.im = SimpleNamespace(get=lambda name, country, mobile: SimpleNamespace(name=name),
                                  proxy_for=lambda identity: None)

    def test_same_identity_reuses_only_same_platform_and_passes_headless(self):
        pool = SessionPool(self.backend, self.im, headless=True)
        with pool.acquire("same", "tiktok") as first:
            pass
        with pool.acquire("same", "tiktok") as second:
            self.assertIs(first, second)
        with pool.acquire("same", "instagram") as third:
            self.assertIsNot(first, third)
        self.assertEqual([(headless, platform) for _, headless, platform in self.backend.opened],
                         [(True, "tiktok"), (True, "instagram")])
        pool.shutdown()
        self.assertEqual(len(self.backend.closed), 2)

    def test_expired_same_identity_is_closed_before_reopen(self):
        clock = [0]
        with patch("socialscraper.net.pool.time.monotonic", side_effect=lambda: clock[0]):
            pool = SessionPool(self.backend, self.im, ttl=10)
            with pool.acquire("one", "tiktok"):
                pass
            clock[0] = 10
            with pool.acquire("one", "tiktok"):
                pass
        self.assertEqual(len(self.backend.opened), 2)
        self.assertEqual(self.backend.closed, [self.backend.opened[0][0]])
        pool.shutdown()

    def test_capacity_keeps_recent_session_and_active_handle(self):
        pool = SessionPool(self.backend, self.im, size=2)
        for name in ("old", "other", "old", "new"):
            with pool.acquire(name, "tiktok"):
                pass
        self.assertEqual([handle.identity.name for handle in self.backend.closed], ["other"])
        pool.shutdown()
        pool = SessionPool(self.backend, self.im, size=1)
        with pool.acquire("active", "tiktok"):
            closed = len(self.backend.closed)
            with self.assertRaises(RuntimeError):
                with pool.acquire("next", "tiktok"):
                    self.fail("Pool must not evict an active session")
            self.assertEqual(len(self.backend.closed), closed)
        pool.shutdown()

    def test_adapter_exception_discards_handle_and_preserves_error(self):
        pool = SessionPool(self.backend, self.im)
        with self.assertRaisesRegex(ValueError, "adapter failure"):
            with pool.acquire("one", "tiktok"):
                raise ValueError("adapter failure")
        self.assertFalse(pool._live)
        self.assertEqual(len(self.backend.closed), 1)
        with pool.acquire("one", "tiktok"):
            pass
        self.assertEqual(len(self.backend.opened), 2)
        pool.drop("one", "tiktok")
        self.assertFalse(pool._live)

    def test_rejects_invalid_capacity_and_lifetime(self):
        for size, ttl in ((0, 10), (1, 0), (-1, 10), (1, -1)):
            with self.assertRaises(ValueError):
                SessionPool(self.backend, self.im, size=size, ttl=ttl)


class BackendLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.identity = SimpleNamespace(name="example", fp=build("example", "us"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def playwright_module(self, package, pw):
        module = types.ModuleType(f"{package}.sync_api")
        module.sync_playwright = lambda: SimpleNamespace(start=lambda: pw)
        return patch.dict(sys.modules, {package: types.ModuleType(package),
                                       f"{package}.sync_api": module})

    def test_local_initialization_accepts_script_and_applies_cookies(self):
        scripts = []
        context = Mock()
        context.add_init_script = lambda script: scripts.append(script)
        pw = Mock()
        pw.chromium.launch.return_value.version = "154.0.8037.93"
        pw.chromium.launch_persistent_context.return_value = context
        backend = LocalBackend(profiles_dir=self.tmp.name)
        cookies = [{"name": "sessionid", "value": "fake", "domain": ".tiktok.com", "path": "/"}]
        with self.playwright_module("patchright", pw), \
                patch("socialscraper.backends.base.CookieJar.load", return_value=cookies):
            handle = backend.open(self.identity, None, platform="tiktok", headless=True)
        self.assertEqual(len(scripts), 1)  # ein Init-Skript setzt Hardware, GPU, Canvas und Audio je Identity
        self.assertEqual(Path(pw.chromium.launch_persistent_context.call_args.kwargs["user_data_dir"]),
                         Path(self.tmp.name) / "example" / "tiktok")
        context.add_cookies.assert_called_once_with(cookies)
        backend.close(handle)
        backend.close(handle)
        context.close.assert_called_once()
        pw.stop.assert_called_once()

    def test_desktop_uses_installed_chrome_user_agent(self):
        pw = Mock()
        backend = LocalBackend(profiles_dir=self.tmp.name)
        with self.playwright_module("patchright", pw):
            backend.close(backend.open(self.identity, None, platform="tiktok"))
        self.assertNotIn("user_agent", pw.chromium.launch_persistent_context.call_args.kwargs)
        pw.chromium.launch.assert_not_called()

    def test_mobile_user_agent_follows_installed_chrome_version(self):
        pw = Mock()
        pw.chromium.launch.return_value.version = "141.0.7390.55"
        pw.chromium.launch_persistent_context.return_value.pages = []
        mobile = SimpleNamespace(name="phone", fp=build("phone", "de", mobile=True))
        backend = LocalBackend(profiles_dir=self.tmp.name)
        with self.playwright_module("patchright", pw):
            backend.close(backend.open(mobile, None, platform="tiktok"))
            backend.close(backend.open(mobile, None, platform="tiktok"))
        args = pw.chromium.launch_persistent_context.call_args.kwargs["args"]
        agent = next(a for a in args if a.startswith("--user-agent="))
        self.assertIn("Chrome/141.0.0.0 Mobile", agent)
        self.assertNotIn("133", agent)
        self.assertEqual(pw.chromium.launch.call_count, 2)  # neuer Treiber nach jedem letzten close

    def test_local_launch_failure_stops_playwright(self):
        pw = Mock()
        pw.chromium.launch_persistent_context.side_effect = RuntimeError("launch failure")
        with self.playwright_module("patchright", pw):
            with self.assertRaisesRegex(RuntimeError, "launch failure"):
                LocalBackend(profiles_dir=self.tmp.name).open(self.identity, None)
        pw.stop.assert_called_once()

    def test_local_multiple_sessions_share_playwright_until_last_close(self):
        pw = Mock()
        contexts = [Mock(), Mock()]
        pw.chromium.launch_persistent_context.side_effect = contexts
        module = types.ModuleType("patchright.sync_api")
        starter = Mock(return_value=pw)
        module.sync_playwright = lambda: SimpleNamespace(start=starter)
        backend = LocalBackend(profiles_dir=self.tmp.name)
        with patch.dict(sys.modules, {"patchright": types.ModuleType("patchright"),
                                     "patchright.sync_api": module}), \
                patch("socialscraper.backends.base.CookieJar.load", return_value=[]):
            first = backend.open(self.identity, None, platform="tiktok")
            second = backend.open(self.identity, None, platform="instagram")
        starter.assert_called_once()
        backend.close(first)
        pw.stop.assert_not_called()
        contexts[1].close.assert_not_called()
        backend.close(second)
        pw.stop.assert_called_once()

    def test_sphere_multiple_sessions_share_playwright_until_last_close(self):
        ls, pw = Mock(), Mock()
        ls.create.side_effect = ["one", "two"]
        browsers = [Mock(contexts=[Mock()]), Mock(contexts=[Mock()])]
        pw.chromium.connect_over_cdp.side_effect = browsers
        module = types.ModuleType("rebrowser_playwright.sync_api")
        starter = Mock(return_value=pw)
        module.sync_playwright = lambda: SimpleNamespace(start=starter)
        backend = SphereBackend(ls)
        with patch.dict(sys.modules, {"rebrowser_playwright": types.ModuleType("rebrowser_playwright"),
                                     "rebrowser_playwright.sync_api": module}), \
                patch("socialscraper.backends.base.CookieJar.load", return_value=[]):
            first = backend.open(self.identity, None, platform="tiktok")
            second = backend.open(self.identity, None, platform="instagram")
        starter.assert_called_once()
        backend.close(first)
        pw.stop.assert_not_called()
        browsers[1].close.assert_not_called()
        backend.close(second)
        pw.stop.assert_called_once()
        self.assertEqual(ls.remove.call_count, 2)

    def test_local_cookie_failure_closes_context_and_preserves_failure(self):
        pw, context = Mock(), Mock()
        pw.chromium.launch_persistent_context.return_value = context
        context.add_cookies.side_effect = ValueError("invalid cookie")
        with self.playwright_module("patchright", pw), \
                patch("socialscraper.backends.base.CookieJar.load", return_value=[{"name": "x"}]):
            with self.assertRaisesRegex(ValueError, "invalid cookie"):
                LocalBackend(profiles_dir=self.tmp.name).open(self.identity, None, platform="tiktok")
        context.close.assert_called_once()
        pw.stop.assert_called_once()

    def test_sphere_connect_failure_stops_and_removes_created_session(self):
        ls, pw = Mock(), Mock()
        ls.create.return_value, ls.start.return_value = "new-session", 1234
        pw.chromium.connect_over_cdp.side_effect = RuntimeError("connect failure")
        with self.playwright_module("rebrowser_playwright", pw):
            with self.assertRaisesRegex(RuntimeError, "connect failure"):
                SphereBackend(ls).open(self.identity, None, platform="instagram", headless=True)
        ls.start.assert_called_once_with("new-session", headless=True)
        ls.stop.assert_called_once_with("new-session")
        ls.remove.assert_called_once_with("new-session")
        pw.stop.assert_called_once()

    def test_sphere_all_cleanup_runs_even_if_browser_and_stop_fail(self):
        ls, pw, browser = Mock(), Mock(), Mock()
        browser.close.side_effect = RuntimeError("browser close")
        ls.stop.side_effect = RuntimeError("sphere stop")
        handle = SessionHandle(self.identity, None, "sphere", _pw=pw, _browser=browser, uuid="owned")
        with self.assertRaises(RuntimeError):
            SphereBackend(ls).close(handle)
        pw.stop.assert_called_once()
        ls.remove.assert_called_once_with("owned")

    def test_session_preserves_body_error_if_cleanup_also_fails(self):
        backend = FakeBackend()
        backend.close = Mock(side_effect=RuntimeError("cleanup failure"))
        with self.assertLogs("socialscraper.backends.base", level="WARNING"):
            with self.assertRaisesRegex(ValueError, "body failure"):
                with backend.session(self.identity, None, platform="instagram"):
                    raise ValueError("body failure")
        self.assertEqual(backend.prepared, ["instagram"])


class RunnerTests(unittest.TestCase):
    def test_runner_timeout_cancels_coroutine_and_stop_closes_loop(self):
        runner = AsyncRunner()
        cancelled = threading.Event()

        async def slow():
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        try:
            with self.assertRaises(TimeoutError):
                runner.run(slow(), timeout=0.03)
            self.assertTrue(cancelled.wait(1))
        finally:
            runner.stop()
        self.assertFalse(runner._t.is_alive())
        self.assertTrue(runner.loop.is_closed())
        runner.stop()
        with self.assertRaises(RuntimeError):
            runner.run(asyncio.sleep(0))


class ImmediateRunner:
    def run(self, coroutine, timeout=180):
        return asyncio.run(coroutine)


def fake_nodriver():
    module = types.ModuleType("nodriver")

    def command(name):
        return lambda **kwargs: (name, kwargs)

    module.cdp = SimpleNamespace(
        runtime=SimpleNamespace(evaluate=command("evaluate")),
        input_=SimpleNamespace(dispatch_mouse_event=command("mouse"), MouseButton=SimpleNamespace(LEFT="left")),
        network=SimpleNamespace(set_cookie=command("set_cookie"), get_all_cookies=lambda: ("cookies", {}),
                                TimeSinceEpoch=float, CookieSameSite=lambda value: value),
        fetch=SimpleNamespace(enable=command("enable"), continue_request=command("continue_request"),
                              continue_with_auth=command("continue_auth"), AuthChallengeResponse=SimpleNamespace,
                              AuthRequired="AuthRequired", RequestPaused="RequestPaused"))
    return module


class NodriverTests(unittest.TestCase):
    def setUp(self):
        self.uc = fake_nodriver()
        self.modules = patch.dict(sys.modules, {"nodriver": self.uc})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.engine = importlib.import_module("socialscraper.backends.nodriver_engine")
        uc_patch = patch.object(self.engine, "uc", self.uc)
        uc_patch.start()
        self.addCleanup(uc_patch.stop)
        self.runner = ImmediateRunner()

    def test_shim_invokes_function_argument_and_preserves_falsey_values(self):
        for value in (None, False, 0, "", [], {}, {"id": "123"}):
            tab = SimpleNamespace(send=AsyncMock(return_value=(SimpleNamespace(value=value), None)))
            result = TabShim(self.runner, tab).evaluate("([name, value]) => value", ["x", "y"])
            self.assertEqual(result, value)
            command, options = tab.send.call_args.args[0]
            self.assertEqual(command, "evaluate")
            self.assertTrue(options["return_by_value"])
            self.assertIn('value(["x", "y"])', options["expression"])

    def test_shim_propagates_javascript_error(self):
        tab = SimpleNamespace(send=AsyncMock(return_value=(None, object())))
        with self.assertRaises(RuntimeError):
            TabShim(self.runner, tab).evaluate("() => 1")

    def test_shim_timeout_waits_and_close_awaits(self):
        tab = SimpleNamespace(close=AsyncMock())
        shim = TabShim(self.runner, tab)
        start = time.monotonic()
        shim.wait_for_timeout(25)
        self.assertGreaterEqual(time.monotonic() - start, 0.02)
        shim.close()
        tab.close.assert_awaited_once()

    def test_shim_mouse_uses_pixel_deltas_and_existing_down_up_methods(self):
        tab = SimpleNamespace(send=AsyncMock())
        shim = TabShim(self.runner, tab)
        shim.mouse.move(10, 20)
        shim.mouse.down()
        shim.mouse.up()
        shim.mouse.wheel(-2, 300)
        events = [call.args[0][1] for call in tab.send.call_args_list]
        self.assertEqual([item["type_"] for item in events],
                         ["mouseMoved", "mousePressed", "mouseReleased", "mouseWheel"])
        self.assertEqual(events[-1]["delta_y"], 300)
        self.assertEqual(events[-1]["delta_x"], -2)
        self.assertEqual((events[-1]["x"], events[-1]["y"]), (10, 20))

    def test_cookie_roundtrip_preserves_expiry_and_same_site(self):
        backend = object.__new__(self.engine.NodriverBackend)
        backend.runner = self.runner
        tab = SimpleNamespace(send=AsyncMock(return_value=True))
        handle = SessionHandle(SimpleNamespace(name="example"), None, "nodriver",
                               _context=tab, platform="tiktok")
        backend.inject_cookies(handle, [{"name": "sessionid", "value": "fake", "expires": 123456.0,
                                         "sameSite": "Lax", "httpOnly": True}])
        options = tab.send.call_args.args[0][1]
        self.assertEqual(options["url"], "https://www.tiktok.com/")
        self.assertEqual(options["expires"], 123456.0)
        self.assertEqual(options["same_site"], "Lax")
        tab.send.return_value = [SimpleNamespace(name="sessionid", value="fake", domain=".tiktok.com",
                                                 path="/", secure=True, http_only=True, expires=123456.0,
                                                 same_site=SimpleNamespace(value="Lax"))]
        result = backend.read_cookies(handle)[0]
        self.assertEqual(result["expires"], 123456.0)
        self.assertEqual(result["sameSite"], "Lax")

    def test_proxy_auth_continues_paused_requests_and_does_not_send_credentials_to_origin(self):
        backend = object.__new__(self.engine.NodriverBackend)
        handlers = {}
        tab = SimpleNamespace(send=AsyncMock(), add_handler=lambda event, callback: handlers.update({event: callback}))
        proxy = SimpleNamespace(user="fake", password="fake")
        asyncio.run(backend._auth(tab, proxy))
        asyncio.run(handlers["RequestPaused"](SimpleNamespace(request_id="request")))
        self.assertEqual(tab.send.call_args.args[0][0], "continue_request")
        event = SimpleNamespace(request_id="request", auth_challenge=SimpleNamespace(source="Server"))
        asyncio.run(handlers["AuthRequired"](event))
        response = tab.send.call_args.args[0][1]["auth_challenge_response"]
        self.assertEqual(response.response, "Default")
        self.assertIsNone(response.username)
        event.auth_challenge.source = "Proxy"
        asyncio.run(handlers["AuthRequired"](event))
        self.assertEqual(tab.send.call_args.args[0][1]["auth_challenge_response"].response, "ProvideCredentials")
        asyncio.run(handlers["AuthRequired"](event))
        self.assertEqual(tab.send.call_args.args[0][1]["auth_challenge_response"].response, "CancelAuth")

    def test_nodriver_initialization_failure_stops_browser(self):
        backend = object.__new__(self.engine.NodriverBackend)
        backend.runner, backend.profiles = self.runner, Path("unused")
        browser = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("tab failure")), stop=Mock())
        self.uc.start = AsyncMock(return_value=browser)
        identity = SimpleNamespace(name="example", fp=build("example", "us"))
        with self.assertRaisesRegex(RuntimeError, "tab failure"):
            backend.open(identity, None, platform="tiktok")
        browser.stop.assert_called_once()

    def test_nodriver_close_awaits_tab_and_stops_browser_even_after_close_failure(self):
        backend = object.__new__(self.engine.NodriverBackend)
        backend.runner = self.runner
        tab = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("tab close")))
        browser = SimpleNamespace(stop=Mock())
        handle = SessionHandle(None, None, "nodriver", _context=tab, _browser=browser)
        with self.assertRaisesRegex(RuntimeError, "tab close"):
            backend.close(handle)
        tab.close.assert_awaited_once()
        browser.stop.assert_called_once()
        backend.close(handle)


if __name__ == "__main__":
    unittest.main()
