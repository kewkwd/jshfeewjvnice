"""Offline regressions for imported cookies, login, and session validation."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from socialscraper import login as login_module
from socialscraper.net import cookies, credentials, notify, session_guard
from socialscraper.net.cookies import CookieJar, parse_any
from socialscraper.net.session_guard import State


def auth_cookie(platform="tiktok", **extra):
    return {"name": "sessionid", "value": "offline-secret", "domain": "." + cookies.DOMAINS[platform], **extra}


class FakePage:
    def __init__(self, state=State.OK):
        self.state = state
        self.urls = []
        self.fill_values = []
        self.fail_goto = False
        self.fail_submit = False
        self.fail_eval = False

    def goto(self, url, **kwargs):
        self.urls.append(url)
        if self.fail_goto:
            raise RuntimeError("password=do-not-log-me")

    def wait_for_timeout(self, timeout):
        pass

    def evaluate(self, script, args=None):
        if self.fail_eval:
            raise RuntimeError("sessionid=do-not-log-me")
        if script in session_guard.PROBE_JS.values():
            return self.state
        if script == login_module._FILL_JS:
            self.fill_values.append(args[1])
            return args[0][0]
        return None

    def click(self, selector, **kwargs):
        self.click_kwargs = kwargs
        if self.fail_submit:
            raise RuntimeError("password=do-not-log-me")


class FakeBackend:
    def __init__(self, platform="tiktok"):
        self.page_object = FakePage()
        self.cookies = [auth_cookie(platform)]
        self.sessions = []
        self.fail_close = False

    @contextmanager
    def session(self, identity, proxy, **kwargs):
        self.sessions.append(kwargs)
        yield SimpleNamespace(identity=identity)
        if self.fail_close:
            raise RuntimeError("sessionid=do-not-log-me")

    def page(self, handle):
        return self.page_object

    def read_cookies(self, handle):
        return self.cookies


class FakeIdentityManager:
    def get(self, name, country, mobile):
        return SimpleNamespace(name=name)

    def proxy_for(self, identity):
        return None


class ScopedCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base_patch = patch.object(cookies, "BASE", self.root / "cookies")
        self.base_patch.start()
        session_guard._cache.clear()
        notify.configure(None)
        self.im = FakeIdentityManager()
        self.backend = FakeBackend()

    def tearDown(self):
        self.base_patch.stop()
        session_guard._cache.clear()
        notify.configure(None)
        self.temporary.cleanup()


class CookieTests(ScopedCase):
    def test_json_forms_and_real_boolean_normalization(self):
        for data in ([auth_cookie()], {"cookies": [auth_cookie()]}, auth_cookie()):
            imported = parse_any(json.dumps(data))
            self.assertEqual(imported[0]["value"], "offline-secret")
        imported = parse_any('[{"name":"sessionid","value":"abc","secure":"false","httpOnly":"true"}]')
        self.assertFalse(imported[0]["secure"])
        self.assertTrue(imported[0]["httpOnly"])

    def test_netscape_http_only_and_session_cookie(self):
        text = "# Netscape HTTP Cookie File\n#HttpOnly_.tiktok.com\tTRUE\t/\tFALSE\t0\tsessionid\tsecret"
        imported = parse_any(text)
        self.assertEqual(len(imported), 1)
        self.assertTrue(imported[0]["httpOnly"])
        self.assertFalse(imported[0]["secure"])
        self.assertNotIn("expires", imported[0])

    def test_header_assigns_domain_only_when_platform_is_known(self):
        imported = parse_any("Cookie: sessionid=abc=def; preference=yes")
        self.assertEqual(imported[0]["value"], "abc=def")
        self.assertEqual(imported[0]["domain"], "")
        CookieJar.save("alice", "instagram", imported)
        self.assertEqual(CookieJar.load("alice", "instagram")[0]["domain"], ".instagram.com")
        self.assertEqual(CookieJar.load("alice", "tiktok"), [])

    def test_type_errors_are_sanitized(self):
        invalid = ['{"cookies": "secret"}', '["secret"]', '[{"name":"sid","value":7}]',
                   '[{"name":"sid","expires":"secret"}]', '[{"name":"sid","expires":true}]',
                   '[{"name":"sid","expires":NaN}]', '[{"name":"sid","secure":"secret"}]']
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError) as error:
                parse_any(text)
            self.assertNotIn("secret", str(error.exception))

    def test_domain_expiry_and_identity_isolation(self):
        with patch.object(cookies.time, "time", return_value=1000):
            CookieJar.save("alice", "tiktok", [auth_cookie(expires="1500"),
                            auth_cookie(domain="evil-tiktok.com"), auth_cookie(domain=".instagram.com"),
                            auth_cookie(expires=999), {"name": "pref", "value": "yes", "domain": "www.tiktok.com"}])
            self.assertEqual(len(CookieJar.load("alice", "tiktok")), 2)
            self.assertTrue(CookieJar.is_authenticated("alice", "tiktok"))
        with patch.object(cookies.time, "time", return_value=1500):
            self.assertFalse(CookieJar.is_authenticated("alice", "tiktok"))
        self.assertEqual(CookieJar.load("bob", "tiktok"), [])
        self.assertEqual(CookieJar.load("alice", "instagram"), [])

    def test_path_traversal_and_windows_reserved_names_rejected(self):
        for identity in ("../alice", "..", "alice/x", "alice\\x", "C:alice", "alice.", "CON", "NUL.foo"):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                CookieJar.path(identity, "tiktok")
        with self.assertRaises(ValueError):
            CookieJar.path("alice", "../tiktok")

    def test_corrupt_cookie_file_does_not_leak_contents(self):
        path = CookieJar.path("alice", "tiktok")
        path.parent.mkdir()
        path.write_text('[{"value":"do-not-log-me"}', encoding="utf-8")
        with self.assertLogs(cookies.log, "WARNING") as logs:
            self.assertEqual(CookieJar.load("alice", "tiktok"), [])
        self.assertNotIn("do-not-log-me", "".join(logs.output))
        path.write_text('{"cookies":"bad-type"}', encoding="utf-8")
        with self.assertLogs(cookies.log, "WARNING"):
            self.assertEqual(CookieJar.load("alice", "tiktok"), [])

    def test_failed_atomic_write_preserves_previous_session(self):
        CookieJar.save("alice", "tiktok", [auth_cookie(value="previous")])
        with patch.object(cookies.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                CookieJar.save("alice", "tiktok", [auth_cookie(value="replacement")])
        self.assertEqual(CookieJar.load("alice", "tiktok")[0]["value"], "previous")
        self.assertFalse(list(CookieJar.path("alice", "tiktok").parent.glob("*.tmp")))


class GuardTests(ScopedCase):
    def test_platform_specific_probe_and_expired_state(self):
        CookieJar.save("alice", "instagram", [auth_cookie("instagram")])
        self.backend = FakeBackend("instagram")
        self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", "instagram"), State.OK)
        self.assertEqual(self.backend.page_object.urls, ["https://www.instagram.com/"])
        self.assertEqual(self.backend.sessions, [{"headless": True, "platform": "instagram"}])
        self.backend.page_object.state = State.EXPIRED
        self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", "instagram", force=True), State.EXPIRED)

    def test_browser_error_and_unknown_dom_never_report_ok(self):
        CookieJar.save("alice", "tiktok", [auth_cookie()])
        self.backend.page_object.fail_goto = True
        with self.assertLogs(session_guard.log, "WARNING") as logs:
            state = session_guard.ensure(self.backend, self.im, "alice")
        self.assertEqual(state, State.ERROR)
        self.assertNotIn("do-not-log-me", "".join(logs.output))
        self.backend.page_object.fail_goto = False
        self.backend.page_object.state = None
        self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.ERROR)
        self.backend.page_object.state = State.OK
        self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.OK)
        self.assertEqual(len(self.backend.sessions), 3)

    def test_missing_cookies_do_not_launch_browser(self):
        self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.MISSING)
        self.assertEqual(self.backend.sessions, [])

    def aged_file(self, mtime=10000):
        CookieJar.save("alice", "tiktok", [auth_cookie()])
        path = CookieJar.path("alice", "tiktok")
        os.utime(path, (mtime, mtime))
        return path

    def test_max_age_is_in_hours_and_only_triggers_a_new_probe_at_boundary(self):
        path = self.aged_file()
        with patch.object(session_guard.time, "time", return_value=13600):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=2), State.OK)
        self.assertEqual(len(self.backend.sessions), 1)
        self.assertEqual(path.stat().st_mtime, 10000)            # noch nicht alt genug: Datei unverändert
        with patch.object(session_guard.time, "time", return_value=17200):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=2), State.OK)
        self.assertEqual(len(self.backend.sessions), 2)          # Alter erreicht: Browser wurde erneut gefragt
        self.assertGreater(path.stat().st_mtime, 10000)          # bestätigt: Datei aufgefrischt, Alter beginnt von vorn

    def test_aged_file_with_confirmed_probe_saves_fresh_browser_cookies(self):
        path = self.aged_file()
        self.backend.cookies = [auth_cookie(value="fresh-from-browser")]
        with patch.object(session_guard.time, "time", return_value=50000):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=2), State.OK)
        self.assertEqual(CookieJar.load("alice", "tiktok")[0]["value"], "fresh-from-browser")
        self.assertGreater(path.stat().st_mtime, 10000)
        # Das Alter zählt jetzt von vorn: gleich danach kein weiterer Browserstart (Zwischenspeicher bleibt gültig).
        with patch.object(session_guard.time, "time", return_value=50010):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=2), State.OK)
        self.assertEqual(len(self.backend.sessions), 1)

    def test_aged_file_without_usable_browser_cookies_still_resets_age(self):
        path = self.aged_file()
        self.backend.cookies = []                                # Sitzung bestätigt, aber keine Cookies lesbar
        with patch.object(session_guard.time, "time", return_value=50000):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=2), State.OK)
        self.assertEqual(CookieJar.load("alice", "tiktok")[0]["value"], "offline-secret")   # alte Cookies bleiben
        self.assertGreater(path.stat().st_mtime, 10000)

    def test_aged_file_with_logged_out_probe_is_expired(self):
        path = self.aged_file()
        self.backend.page_object.state = State.EXPIRED
        with patch.object(session_guard.time, "time", return_value=50000):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=2), State.EXPIRED)
        self.assertEqual(len(self.backend.sessions), 1)
        self.assertEqual(path.stat().st_mtime, 10000)            # nicht aufgefrischt

    def test_aged_file_with_failing_probe_is_error_not_ok(self):
        self.aged_file()
        self.backend.page_object.fail_goto = True
        with patch.object(session_guard.time, "time", return_value=50000), self.assertLogs(session_guard.log, "WARNING"):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=2), State.ERROR)

    def test_aged_file_ignores_a_cached_ok_from_before_it_aged(self):
        path = self.aged_file()
        with patch.object(session_guard.time, "time", return_value=10100):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=2), State.OK)
        self.assertEqual(len(self.backend.sessions), 1)
        os.utime(path, (10000, 10000))                           # Datei erneut "alt" machen, Stempel wie im Cache
        self.backend.page_object.state = State.EXPIRED
        with patch.object(session_guard.time, "time", return_value=10300), \
             patch.object(session_guard, "TTL", 100000):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice", max_age=0.05), State.EXPIRED)

    def test_cache_invalidates_when_auth_cookie_expires_or_file_changes(self):
        with patch.object(cookies.time, "time", return_value=1000):
            CookieJar.save("alice", "tiktok", [auth_cookie(expires=1100)])
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.OK)
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.OK)
            self.assertEqual(len(self.backend.sessions), 1)
        with patch.object(cookies.time, "time", return_value=1200):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.MISSING)
            CookieJar.save("alice", "tiktok", [auth_cookie(value="fresh")])
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.OK)
        self.assertEqual(len(self.backend.sessions), 2)

    def test_probe_close_error_is_not_authenticated(self):
        CookieJar.save("alice", "tiktok", [auth_cookie()])
        self.backend.fail_close = True
        with self.assertLogs(session_guard.log, "WARNING"):
            self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.ERROR)

    def test_probe_waits_for_late_navigation(self):
        CookieJar.save("alice", "tiktok", [auth_cookie()])
        page = self.backend.page_object
        answers = iter([None, RuntimeError("navigating"), State.OK])
        def evaluate(script, args=None):
            value = next(answers)
            if isinstance(value, Exception):
                raise value
            return value
        page.evaluate = evaluate
        self.assertEqual(session_guard.ensure(self.backend, self.im, "alice"), State.OK)

    def test_expired_error_is_not_retryable(self):
        self.assertFalse(session_guard.SessionExpired.retryable)


class LoginTests(ScopedCase):
    def test_configured_credentials_file_is_used(self):
        path = self.root / "custom-credentials.json"
        path.write_text(json.dumps({"tiktok": {"alice": {"user": "offline-user", "password": "offline-password"}}}), encoding="utf-8")
        cfg = SimpleNamespace(credentials_file=str(path))
        with patch.object(login_module, "notify") as signal:
            result = login_module.login(self.backend, self.im, "alice", cfg=cfg, mode="auto")
        self.assertEqual(self.backend.page_object.fill_values, ["offline-user", "offline-password"])
        self.assertTrue(result)
        self.assertTrue(CookieJar.is_authenticated("alice", "tiktok"))
        self.assertEqual(signal.call_args.args[0], "login_ok")

    def test_browser_shutdown_failure_does_not_save_or_signal_success(self):
        self.backend.fail_close = True
        with patch.object(CookieJar, "save") as save, patch.object(login_module, "notify") as signal:
            with self.assertRaises(login_module.LoginError) as error:
                login_module.login(self.backend, self.im, "alice", mode="qr")
        self.assertNotIn("do-not-log-me", str(error.exception))
        save.assert_not_called()
        signal.assert_not_called()

    def test_autofill_browser_error_is_sanitized(self):
        self.backend.page_object.fail_eval = True
        with patch.object(login_module, "get_creds", return_value=credentials.Credentials("name", "offline-password")), \
             patch.object(login_module, "notify") as signal:
            with self.assertRaises(login_module.LoginError) as error:
                login_module.login(self.backend, self.im, "alice", mode="password")
        self.assertNotIn("do-not-log-me", str(error.exception))
        signal.assert_not_called()

    def test_failed_submit_is_reported_without_auth_success(self):
        self.backend.page_object.fail_submit = True
        with patch.object(login_module, "get_creds", return_value=credentials.Credentials("name", "secret")), \
             patch.object(login_module, "notify") as signal:
            with self.assertRaisesRegex(login_module.LoginError, "Anmelde-Schaltfläche"):
                login_module.login(self.backend, self.im, "alice", mode="password")
        signal.assert_not_called()

    def test_expired_cookie_cannot_confirm_login(self):
        self.assertFalse(login_module._authed([auth_cookie(expires=1)], "tiktok"))
        self.assertFalse(login_module._authed([auth_cookie(domain=".instagram.com")], "tiktok"))

    def test_unknown_dom_does_not_confirm_existing_cookie(self):
        self.backend.page_object.state = None
        with patch.object(login_module.time, "monotonic", side_effect=[0, 0, 0.5, 2]):
            result = login_module._wait_for_auth(self.backend.page_object, self.backend, None, "tiktok", 1)
        self.assertIsNone(result)

    def test_navigation_during_login_does_not_abort(self):
        page = self.backend.page_object
        results = iter([RuntimeError("Execution context was destroyed"), State.OK])
        def evaluate(script, args=None):
            if script in session_guard.PROBE_JS.values():
                value = next(results)
                if isinstance(value, Exception):
                    raise value
                return value
            raise RuntimeError("Execution context was destroyed")
        page.evaluate = evaluate
        with patch.object(login_module.time, "monotonic", side_effect=[0, 0, 0.1, 0.2, 0.3]):
            result = login_module._wait_for_auth(page, self.backend, None, "tiktok", 1)
        self.assertTrue(result)

    def test_dismiss_never_accepts_on_users_behalf(self):
        for texts in login_module.DISMISS.values():
            for text in texts:
                self.assertNotIn("allow all", text)
                self.assertNotIn("accept", text)
                self.assertNotIn("alle", text)

    def test_submit_click_has_short_timeout(self):
        with patch.object(login_module, "get_creds", return_value=credentials.Credentials("n", "p")),              patch.object(login_module, "notify"):
            login_module.login(self.backend, self.im, "alice", mode="password")
        self.assertEqual(self.backend.page_object.click_kwargs, {"timeout": login_module.SUBMIT_TIMEOUT_MS})

    def test_check_with_aged_file_asks_the_browser(self):
        CookieJar.save("alice", "tiktok", [auth_cookie()])
        os.utime(CookieJar.path("alice", "tiktok"), (10000, 10000))
        with patch.object(session_guard.time, "time", return_value=17200):
            self.assertTrue(login_module.check(self.backend, self.im, "alice", cfg=SimpleNamespace(session_max_age=2)))
        self.assertEqual(len(self.backend.sessions), 1)
        os.utime(CookieJar.path("alice", "tiktok"), (10000, 10000))
        self.backend.page_object.state = State.EXPIRED
        with patch.object(session_guard.time, "time", return_value=17200):
            self.assertFalse(login_module.check(self.backend, self.im, "alice", cfg=SimpleNamespace(session_max_age=2)))


class CredentialNotificationTests(ScopedCase):
    def test_malformed_credentials_and_env_fallback(self):
        path = self.root / "credentials.json"
        path.write_text('["secret"]', encoding="utf-8")
        with patch.dict(os.environ, {"SS_TIKTOK_ALICE_USER": "env-user", "SS_TIKTOK_ALICE_PASS": "env-secret"}):
            result = credentials.get("tiktok", "alice", path)
        self.assertEqual(result.user, "env-user")
        self.assertNotIn("env-secret", repr(result))
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(credentials.MissingCreds) as error:
            credentials.get("tiktok", "alice", path)
        self.assertNotIn("secret", str(error.exception))

    def test_notification_file_redacts_sensitive_extra_and_handles_io_error(self):
        path = self.root / "alerts.jsonl"
        notify.configure(SimpleNamespace(notify_file=str(path), notify_webhook=""))
        notify.notify("offline_test", "Test", password="secret", details={"token": "secret", "count": 1})
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["password"], "[redacted]")
        self.assertEqual(payload["details"], {"token": "[redacted]", "count": 1})
        notify.configure(SimpleNamespace(notify_file=str(self.root), notify_webhook=""))
        with self.assertLogs(notify.log, "WARNING"):
            notify.notify("offline_test", "Test")

    def test_webhook_error_does_not_expose_url_or_credentials(self):
        notify.configure(SimpleNamespace(notify_file="", notify_webhook="https://example.invalid/secret-token"))
        with patch.object(notify.urllib.request, "urlopen", side_effect=OSError("secret-token")) as request, \
             self.assertLogs(notify.log, "WARNING") as logs:
            notify.notify("offline_test", "Test")
        request.assert_called_once()
        self.assertNotIn("secret-token", "".join(logs.output))


if __name__ == "__main__":
    unittest.main()
