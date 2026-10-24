"""Offline failure/retry behavior; no network and no actual browser launch."""
import contextlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from socialscraper.backends import profile_store as profiles
from socialscraper.backends.local import LocalBackend
from socialscraper.net.fingerprint import build
from socialscraper.net.pool import SessionPool


class ProfileStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def begin(self, **kwargs):
        return profiles.begin(self.root, 'fixture', 'tiktok', **kwargs)

    def test_healthy_profile_is_reused_but_failed_profile_is_preserved_and_not_copied(self):
        first = self.begin(); first.path.mkdir(parents=True)
        (first.path / 'cookies.fixture').write_text('old-fixture')
        first.finish(success=True)
        second = self.begin(); self.assertEqual(first.path, second.path)
        second.finish(success=False)
        third = self.begin()
        self.assertNotEqual(first.path, third.path); self.assertTrue(third.isolated)
        self.assertFalse(third.path.exists())
        self.assertEqual((first.path / 'cookies.fixture').read_text(), 'old-fixture')
        third.finish(success=True)
        fourth = self.begin(); self.assertEqual(third.path, fourth.path)

    def test_unclean_restart_and_stale_handle_cannot_mark_new_generation_clean(self):
        first = self.begin(); second = self.begin()
        self.assertNotEqual(first.path, second.path)
        first.finish(success=True)
        self.assertEqual(profiles._read(second.state)['status'], 'in_use')
        third = self.begin(); self.assertNotEqual(second.path, third.path)

    def test_firefox_failure_never_changes_chrome_slot(self):
        chrome = self.begin(); chrome.finish(success=True)
        firefox = self.begin(firefox=True); firefox.finish(success=False)
        self.assertNotEqual(firefox.path, self.begin(firefox=True).path)
        self.assertEqual(chrome.path, self.begin().path)

    def test_corrupt_state_fails_closed_and_does_not_replace_state(self):
        first = self.begin(); first.state.write_text('{broken')
        with self.assertRaisesRegex(ValueError, 'Profilstatus'):self.begin()
        self.assertEqual(first.state.read_text(), '{broken')

    def test_status_write_failure_is_not_success(self):
        with patch.object(profiles, '_write', side_effect=OSError('fixture-write')):
            with self.assertRaises(OSError):self.begin()


class ChromeRetryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.backend = LocalBackend(profiles_dir=self.root)
        self.identity = SimpleNamespace(name='fixture', fp=build('fixture', 'de'))
        self.driver = Mock()
        self.driver.chromium.launch_persistent_context.return_value.pages = [Mock()]
        self.start = self.stack.enter_context(patch('patchright.sync_api.sync_playwright'))
        self.start.return_value.start.return_value = self.driver
        self.stack.enter_context(patch('socialscraper.net.browser_window.announce'))
        self.stack.enter_context(patch('socialscraper.net.browser_window.closed'))
        self.stack.enter_context(patch('socialscraper.window_notification.window_opened'))
        self.cookies = self.stack.enter_context(patch('socialscraper.backends.base.CookieJar.load', return_value=[]))
        self.stack.enter_context(patch('socialscraper.net.browserproxy.options', return_value=(None, None)))
        self.stack.enter_context(patch.dict('os.environ', {'SS_REQUIRE_PROXY':'0'}))

    def opened(self):
        return self.backend.open(self.identity, None, platform='tiktok')

    def launch_path(self):
        return Path(self.driver.chromium.launch_persistent_context.call_args.kwargs['user_data_dir'])

    def test_startup_failure_gets_empty_profile_and_does_not_import_old_cookies(self):
        self.driver.chromium.launch_persistent_context.side_effect = RuntimeError('fixture-launch')
        with self.assertRaisesRegex(RuntimeError, 'fixture-launch'):self.opened()
        first = self.launch_path()
        self.driver.chromium.launch_persistent_context.side_effect = None
        self.cookies.return_value = [{'name':'sessionid', 'value':'old'}]
        handle = self.opened()
        self.assertNotEqual(first, self.launch_path()); self.cookies.assert_not_called()
        self.driver.chromium.launch_persistent_context.return_value.add_cookies.assert_not_called()
        self.backend.close(handle)

    def test_driver_failure_before_launch_also_marks_attempt_failed(self):
        self.start.return_value.start.side_effect = RuntimeError('fixture-driver')
        with self.assertRaisesRegex(RuntimeError, 'fixture-driver'):self.opened()
        self.driver.chromium.launch_persistent_context.assert_not_called()
        self.start.return_value.start.side_effect = None
        handle = self.opened(); self.assertTrue(handle._profile_selection.isolated)
        self.backend.close(handle)

    def test_session_failure_marks_profile_before_close_and_preserves_original_error(self):
        primary = ValueError('fixture-form')
        with self.assertRaises(ValueError) as caught:
            with self.backend.session(self.identity, None, platform='tiktok') as first:
                raise primary
        self.assertIs(caught.exception, primary)
        second = self.opened()
        self.assertNotEqual(first._profile_selection.path, second._profile_selection.path)
        self.backend.close(second)

    def test_cleanup_failure_rotates_profile_and_preserves_primary_error_on_write_failure(self):
        first = self.opened()
        primary = RuntimeError('fixture-close')
        first._context.close.side_effect = primary
        with patch.object(profiles, '_write', side_effect=OSError('fixture-write')):
            with self.assertRaises(RuntimeError) as caught:self.backend.close(first)
        self.assertIs(caught.exception, primary)
        self.driver.chromium.launch_persistent_context.return_value.close.side_effect = None
        second = self.opened()
        self.assertNotEqual(first._profile_selection.path, second._profile_selection.path)
        self.backend.close(second)

    def test_pool_error_uses_fresh_profile_for_next_acquisition(self):
        im = SimpleNamespace(get=lambda *args:self.identity, proxy_for=lambda ident:None)
        pool = SessionPool(self.backend, im); self.addCleanup(pool.shutdown)
        with self.assertRaisesRegex(RuntimeError, 'fixture-adapter'):
            with pool.acquire('fixture', 'tiktok'):
                first = self.launch_path()
                raise RuntimeError('fixture-adapter')
        with pool.acquire('fixture', 'tiktok'):
            self.assertNotEqual(first, self.launch_path())

    def test_status_persistence_failure_prevents_driver_start(self):
        with patch.object(profiles, '_write', side_effect=OSError('fixture-write')):
            with self.assertRaises(OSError):self.opened()
        self.start.assert_not_called()
