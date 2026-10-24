"""Anti-Detect: konsistente Identitaeten, echte Chrome-Werte statt JS-Ueberschreibungen, menschliches Tempo."""
import http.server
import json
import os
import random
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from socialscraper.backends.local import LocalBackend
from socialscraper.net import humanize
from socialscraper.net.fingerprint import (GEO, OS_PROFILES, Fingerprint, accept_language, build,
                                           desktop_user_agent, host_os, mobile_user_agent)
from socialscraper.net.identity import IdentityManager

OLD_FILE = {  # so sahen gespeicherte Identitaeten vor der Umstellung aus (Chrome/133, Windows, erfundene GPU)
    "name": "alt", "country": "de", "mobile": False,
    "fp": {"identity": "alt", "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36", "platform": "Win32",
           "viewport": {"width": 1920, "height": 1080}, "screen": {"width": 1920, "height": 1120},
           "timezone": "Europe/Berlin", "locale": "de-DE", "languages": ["de-DE", "de", "en"],
           "geolocation": {"latitude": 52.6, "longitude": 13.5, "accuracy": 50},
           "hardware_concurrency": 8, "device_memory": 8,
           "webgl_vendor": "Google Inc. (NVIDIA)", "webgl_renderer": "ANGLE (NVIDIA, RTX 3060)",
           "canvas_seed": 12345, "color_depth": 24, "device_scale": 1.0, "is_mobile": False}}


class FingerprintTests(unittest.TestCase):
    def test_country_is_consistent(self):
        for country, (tz, locale, langs, _) in GEO.items():
            fp = build("id-" + country, country)
            self.assertEqual((fp.timezone, fp.locale, fp.languages), (tz, locale, langs))
            self.assertEqual(fp.languages[0], fp.locale)
            self.assertTrue(accept_language(fp).startswith(locale))

    def test_deterministic_per_identity(self):
        self.assertEqual(build("x", "de").to_dict(), build("x", "de").to_dict())
        self.assertNotEqual(build("x", "de").viewport == build("y", "de").viewport
                            and build("x", "de").geolocation, build("y", "de").geolocation)

    def test_desktop_platform_follows_real_os(self):
        for sys_platform, name in (("win32", "windows"), ("linux", "linux"), ("darwin", "macos")):
            self.assertEqual(host_os(sys_platform), name)
            with patch("socialscraper.net.fingerprint.sys.platform", sys_platform):
                fp = build("os-" + name, "us")
            self.assertEqual(fp.platform, OS_PROFILES[name][1])
            self.assertIn(OS_PROFILES[name][0], fp.user_agent)

    def test_no_hardcoded_chrome_version(self):
        for mobile in (False, True):
            fp = build("v", "us", mobile=mobile)
            self.assertNotIn("133", fp.user_agent)
            self.assertNotRegex(fp.user_agent, r"Chrome/\d")
        self.assertIn("Chrome/141.0.0.0 Mobile", mobile_user_agent(build("v", "us", mobile=True), "141.0.7390.55"))

    def test_desktop_user_agent_from_installed_version(self):
        ua = desktop_user_agent("154.0.8037.93", "win32")
        self.assertIn("Chrome/154.0.0.0 Safari/537.36", ua)
        self.assertNotIn("Headless", ua)
        self.assertIn("X11; Linux x86_64", desktop_user_agent("154.0.8037.93", "linux"))
        self.assertIn("Macintosh", desktop_user_agent("154.0.8037.93", "darwin"))
        with self.assertRaises(ValueError):
            desktop_user_agent("unbekannt")

    def test_mobile_user_agent_also_converts_old_stored_value(self):
        old = Fingerprint.from_dict(dict(OLD_FILE["fp"], user_agent="Mozilla/5.0 (Linux; Android 14; SM-S911B) "
                                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Mobile Safari/537.36"))
        self.assertIn("Chrome/150.0.0.0", mobile_user_agent(old, "150.0.1.2"))


class IdentityCompatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profiles = Path(self.tmp.name) / "profiles"
        self.ids = Path(self.tmp.name) / "identities"
        self.ids.mkdir()

    def test_old_identity_file_still_loads(self):
        (self.ids / "alt.json").write_text(json.dumps(OLD_FILE), encoding="utf-8")
        im = IdentityManager(None, profiles_dir=str(self.profiles))
        self.assertEqual(im.stored("alt"), ("de", False))
        ident = im.get("alt", "de", False)
        self.assertEqual(ident.fp.canvas_seed, 12345)
        with self.assertRaises(ValueError):          # Land/Geraet-Pruefung bleibt
            im.get("alt", "us", False)
        with self.assertRaises(ValueError):
            im.get("alt", "de", True)

    def test_unknown_future_fields_are_ignored(self):
        data = json.loads(json.dumps(OLD_FILE))
        data["fp"]["neues_feld"] = 1
        (self.ids / "alt.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(IdentityManager(None, profiles_dir=str(self.profiles)).get("alt", "de").fp.locale, "de-DE")

    def test_new_identity_round_trips(self):
        im = IdentityManager(None, profiles_dir=str(self.profiles))
        first = im.get("neu", "fr")
        again = IdentityManager(None, profiles_dir=str(self.profiles)).get("neu", "fr")
        self.assertEqual(first.fp.to_dict(), again.fp.to_dict())


class LocalBackendLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.identity = SimpleNamespace(name="ex", fp=Fingerprint.from_dict(OLD_FILE["fp"]))

    def launch(self, identity, headless):
        pw = Mock()
        pw.chromium.launch.return_value.version = "154.0.8037.93"
        module = types.ModuleType("patchright.sync_api")
        module.sync_playwright = lambda: Mock(start=Mock(return_value=pw))
        with patch.dict(sys.modules, {"patchright": types.ModuleType("patchright"), "patchright.sync_api": module}):
            backend = LocalBackend(profiles_dir=self.tmp.name)
            backend.close(backend.open(identity, None, headless=headless))
        return pw.chromium.launch_persistent_context

    def test_headless_desktop_removes_headless_marker_everywhere(self):
        launch = self.launch(self.identity, True)
        kwargs = launch.call_args.kwargs
        self.assertIn("Chrome/154.0.0.0", kwargs["user_agent"])
        self.assertNotIn("Headless", kwargs["user_agent"])
        self.assertIn(f"--user-agent={kwargs['user_agent']}", kwargs["args"])   # deckt Service-/Shared-Worker ab
        self.assertIn("--accept-lang=de-DE,de,en", kwargs["args"])
        self.assertNotIn("locale", kwargs)       # navigator.languages kommt natuerlich aus --accept-lang

    def test_visible_desktop_keeps_native_user_agent(self):
        kwargs = self.launch(self.identity, False).call_args.kwargs
        self.assertNotIn("user_agent", kwargs)
        self.assertFalse([a for a in kwargs["args"] if a.startswith("--user-agent")])

    def test_country_settings_are_still_applied(self):
        kwargs = self.launch(self.identity, True).call_args.kwargs
        self.assertEqual(kwargs["timezone_id"], "Europe/Berlin")
        self.assertNotIn("geolocation", kwargs)
        self.assertEqual(kwargs["permissions"], [])
        self.assertEqual(kwargs["viewport"], {"width": 1920, "height": 1080})

    def test_no_js_init_script_and_no_fake_hardware(self):
        text = (Path(__file__).parent.parent / "socialscraper" / "backends" / "local.py").read_text(encoding="utf-8")
        self.assertNotIn("add_init_script", text)
        self.assertFalse((Path(__file__).parent.parent / "socialscraper" / "net" / "stealth_js.py").exists())


class PaceTests(unittest.TestCase):
    def setUp(self):
        humanize._last_fetch.clear()
        self.addCleanup(humanize._last_fetch.clear)
        patcher = patch.dict(os.environ, {humanize.PACE_OFF_ENV: ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = [1000.0]
        self.sleeps = []

    def pace(self, platform="tiktok", seed=1):
        return humanize.pace(platform, rng=random.Random(seed), sleep=self.sleeps.append, clock=lambda: self.now[0])

    def test_delay_range_and_spread(self):
        rng = random.Random(7)
        delays = [humanize.pace_delay(rng) for _ in range(500)]
        self.assertGreaterEqual(min(delays), humanize.PACE_MIN)
        self.assertLessEqual(max(delays), humanize.PACE_MAX + humanize.BREAK_RANGE[1])
        self.assertGreater(len({round(d, 1) for d in delays}), 100)           # gestreut, nicht konstant
        self.assertGreater(sum(d > humanize.PACE_MAX for d in delays), 0)     # Lesepausen kommen vor
        self.assertEqual([humanize.pace_delay(random.Random(3)) for _ in range(2)],
                         [humanize.pace_delay(random.Random(3)) for _ in range(2)])   # deterministisch bei gleichem Zufall

    def test_first_fetch_free_then_pause(self):
        self.assertEqual(self.pace(), 0.0)
        self.assertEqual(self.sleeps, [])
        waited = self.pace(seed=2)
        self.assertEqual(self.sleeps, [waited])
        self.assertGreaterEqual(waited, humanize.PACE_MIN)

    def test_platforms_are_independent(self):
        self.pace("tiktok")
        self.assertEqual(self.pace("instagram"), 0.0)
        self.assertEqual(self.sleeps, [])

    def test_no_pause_after_long_rest(self):
        self.pace()
        self.now[0] += humanize.PACE_WINDOW + 1
        self.assertEqual(self.pace(), 0.0)

    def test_can_be_switched_off_for_tests(self):
        with patch.dict(os.environ, {humanize.PACE_OFF_ENV: "off"}):
            self.pace(); self.assertEqual(self.pace(), 0.0)
        self.assertEqual(self.sleeps, [])

    def test_loops_call_pace(self):
        for name in ("cli.py", "scheduler.py"):
            text = (Path(__file__).parent.parent / "socialscraper" / name).read_text(encoding="utf-8")
            self.assertIn("humanize.pace(adapter.platform", text)


@unittest.skipUnless(os.environ.get("SS_RUN_LOCAL_BROWSER_TESTS") == "1", "SS_RUN_LOCAL_BROWSER_TESTS=1 nicht gesetzt")
class RealHeadlessTests(unittest.TestCase):
    """Startet das echte Chrome unsichtbar (headless) mit Wegwerf-Profil; kein Netz noetig (lokaler Testserver)."""

    def run_probe(self, country, mobile=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        im = IdentityManager(None, profiles_dir=str(Path(tmp.name) / "profiles"))
        ident = im.get("probe-" + country, country, mobile)
        backend = LocalBackend("patchright", str(Path(tmp.name) / "profiles"))
        site = Path(tmp.name) / "site"
        site.mkdir()
        (site / "index.html").write_text("<html><body>x</body></html>", encoding="utf-8")
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
        handler = lambda *a, **k: Quiet(*a, directory=str(site), **k)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)   # 127.0.0.1 ist ein "sicherer Kontext" (userAgentData)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        handle = backend.open(ident, None, headless=True)
        try:
            page = backend.page(handle)
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html")
            return ident, page.evaluate("""async () => {
              const w = new Worker(URL.createObjectURL(new Blob(['postMessage([navigator.userAgent,navigator.platform,navigator.hardwareConcurrency])'])));
              const worker = await new Promise(r => { w.onmessage = e => r(e.data); });
              return {ua: navigator.userAgent, platform: navigator.platform, cores: navigator.hardwareConcurrency,
                      langs: navigator.languages, tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
                      webdriver: navigator.webdriver, hints: navigator.userAgentData.platform,
                      brands: navigator.userAgentData.brands.map(b => b.version), worker,
                      own: ['platform','hardwareConcurrency','webdriver','languages'].filter(k => Object.prototype.hasOwnProperty.call(navigator, k)),
                      getParam: /native code/.test(Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter)),
                      toDataURL: /native code/.test(Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL))}; }""",
                                          isolated_context=False)
        finally:
            backend.close(handle)

    def test_headless_desktop_looks_like_the_installed_chrome(self):
        ident, r = self.run_probe("de")
        self.assertNotIn("Headless", r["ua"])
        self.assertIn(r["brands"][1], r["ua"].split("Chrome/")[1])            # UA-Version = Client-Hints-Version
        self.assertEqual(r["platform"], OS_PROFILES[host_os()][1])
        self.assertEqual(r["hints"], OS_PROFILES[host_os()][2])
        self.assertEqual(r["tz"], "Europe/Berlin")
        self.assertEqual(r["langs"], ["de-DE", "de", "en"])
        self.assertIn(r["webdriver"], (False, None))
        self.assertEqual(r["own"], [])                                        # keine Eigenschaften auf navigator ueberschrieben
        self.assertTrue(r["getParam"] and r["toDataURL"])                     # nichts gepatcht
        self.assertEqual(r["worker"], [r["ua"], r["platform"], r["cores"]])   # Worker widerspricht nicht


if __name__ == "__main__":
    unittest.main()
