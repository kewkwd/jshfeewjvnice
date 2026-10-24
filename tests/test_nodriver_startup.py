"""Offline startup regressions and an explicit local-only real browser check."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from socialscraper.backends.base import SessionHandle
from socialscraper.backends.nodriver_shim import TabShim
from socialscraper.net.fingerprint import build


class ImmediateRunner:
    def run(self, coroutine, timeout=180):
        return asyncio.run(coroutine)


class NodriverStartupTests(unittest.TestCase):
    def setUp(self):
        self.uc = types.ModuleType("nodriver")
        self.modules = patch.dict(sys.modules, {"nodriver": self.uc})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.engine = importlib.import_module("socialscraper.backends.nodriver_engine")
        uc_patch = patch.object(self.engine, "uc", self.uc)
        uc_patch.start()
        self.addCleanup(uc_patch.stop)
        self.backend = object.__new__(self.engine.NodriverBackend)
        self.backend.runner = ImmediateRunner()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend.profiles = Path(self.tmp.name) / 'profiles'
        self.backend._apply_cookies = Mock()
        self.identity = SimpleNamespace(name="startup_regression", fp=build("startup_regression", "de"))

    def fake_emulation(self):
        command = lambda name: (lambda **kwargs: (name, kwargs))
        self.uc.cdp = SimpleNamespace(
            emulation=SimpleNamespace(set_timezone_override=command("timezone"),
                                      set_locale_override=command("locale"),
                                      set_geolocation_override=command("geolocation")),
            browser=SimpleNamespace(grant_permissions=command("grant"),
                                    PermissionType=SimpleNamespace(GEOLOCATION="geolocation")))

    def test_relative_profile_is_resolved_before_chrome_start(self):
        self.fake_emulation()
        tab = SimpleNamespace(send=AsyncMock())
        browser = SimpleNamespace(get=AsyncMock(return_value=tab))
        self.uc.start = AsyncMock(return_value=browser)
        handle = self.backend.open(self.identity, None, headless=True, platform="tiktok")
        sent = {name: options for name, options in (call.args[0] for call in tab.send.call_args_list)}
        self.assertEqual(sent["timezone"], {"timezone_id": "Europe/Berlin"})
        self.assertEqual(sent["locale"], {"locale": "de-DE"})
        self.assertEqual(sent["grant"], {"permissions": []})
        self.assertNotIn("geolocation", sent)
        options = self.uc.start.call_args.kwargs
        self.assertTrue(Path(options["user_data_dir"]).is_absolute())
        self.assertEqual(Path(options["user_data_dir"]),
                         (self.backend.profiles / "startup_regression" / "tiktok").resolve())
        self.assertTrue(options["headless"])
        self.assertIs(handle._browser, browser)

    def test_failed_launch_retries_in_new_directory_even_without_browser_handle(self):
        self.uc.start = AsyncMock(side_effect=RuntimeError('fixture-launch'))
        with self.assertRaisesRegex(RuntimeError, 'fixture-launch'):
            self.backend.open(self.identity, None, platform='tiktok')
        first = self.uc.start.call_args.kwargs['user_data_dir']
        self.fake_emulation()
        tab = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
        browser = SimpleNamespace(get=AsyncMock(return_value=tab), stop=Mock())
        self.uc.start.side_effect = None; self.uc.start.return_value = browser
        handle = self.backend.open(self.identity, None, platform='tiktok')
        self.assertNotEqual(first, self.uc.start.call_args.kwargs['user_data_dir'])
        self.backend.close(handle)

    def test_mobile_identity_is_rejected_before_chrome_start(self):
        self.uc.start = AsyncMock()
        mobile = SimpleNamespace(name="m", fp=build("m", "de", mobile=True))
        with self.assertRaisesRegex(ValueError, "patchright"):
            self.backend.open(mobile, None, platform="tiktok")
        self.uc.start.assert_not_called()

    def test_close_waits_for_owned_process_and_drains_pipes(self):
        process = SimpleNamespace(communicate=AsyncMock(return_value=(b"", b"")))
        browser = SimpleNamespace(_process=process, stop=Mock())
        tab = SimpleNamespace(close=AsyncMock())
        handle = SessionHandle(self.identity, None, "nodriver", _context=tab, _browser=browser)
        self.backend.close(handle)
        tab.close.assert_awaited_once()
        browser.stop.assert_called_once()
        process.communicate.assert_awaited_once()
        self.backend.close(handle)
        browser.stop.assert_called_once()

    def test_fill_updates_existing_element_with_serializable_argument(self):
        tab = object()
        shim = TabShim(ImmediateRunner(), tab)
        shim.wait_for_selector = Mock()
        shim.evaluate = Mock(return_value=True)
        shim.fill("#input", 'Grüße "quoted"', timeout=500)
        shim.wait_for_selector.assert_called_once_with("#input", timeout=500)
        self.assertEqual(shim.evaluate.call_args.args[1], ["#input", 'Grüße "quoted"'])

    def test_fill_failure_does_not_report_success(self):
        shim = TabShim(ImmediateRunner(), object())
        shim.wait_for_selector = Mock()
        shim.evaluate = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "Eingabefeld"):
            shim.fill("#missing-input", "example")


@unittest.skipUnless(os.environ.get("SS_RUN_LOCAL_BROWSER_TESTS") == "1",
                     "Real Chrome check is opt-in and uses only a local test page")
class RealNodriverStartupTests(unittest.TestCase):
    def test_installed_nodriver_starts_and_closes_against_local_page(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-B", "checks/runtime_smoke.py", "--engine", "nodriver"],
                                cwd=root, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(report["passed"], report)
        self.assertFalse(report["live_accounts_used"])
        self.assertEqual(report["browser"]["actions"],
                         ["navigate", "evaluate", "fill", "click", "cookies", "mouse"])
        self.assertNotIn("Exception ignored", result.stderr)
        self.assertNotIn("ResourceWarning", result.stderr)


if __name__ == "__main__":
    unittest.main()
