"""Account logout removes both browser profiles under a scoped Firefox lock."""
import contextlib
import ctypes
import io
from pathlib import Path
import socket
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from socialscraper import cli, runlock
from socialscraper.config import Settings
from socialscraper.net import cookies


class FirefoxLogoutTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        for method in ('connect', 'connect_ex', 'sendto', 'bind'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch.object(ctypes, 'WinDLL', create=True,
                                             side_effect=AssertionError('Native API blocked')))
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'profiles'))
        self.args = SimpleNamespace(identity='fixture_account', platform='tiktok')
        self.profiles = Path(self.cfg.profiles_dir)
        self.chrome = self.profiles / self.args.identity / self.args.platform
        self.firefox = self.profiles / '_firefox' / self.args.identity / self.args.platform
        self.lock_path = self.profiles / '_firefox' / '_locks' / self.args.identity / 'tiktok.lock'
        self.stack.enter_context(patch.object(cookies, 'BASE', self.root / 'cookies'))
        self.cookie = cookies.CookieJar.path(self.args.identity, self.args.platform)
        self.cookie.parent.mkdir(parents=True, exist_ok=True)
        self.cookie.write_bytes(b'[]')
        self.real_clear = cookies.CookieJar.clear
        self.clear = self.stack.enter_context(patch.object(cli.CookieJar, 'clear', wraps=self.real_clear))
        self.invalidate = self.stack.enter_context(patch.object(cli, 'invalidate'))
        for path in (self.chrome, self.firefox):
            path.mkdir(parents=True)
            (path / 'fixture-session').write_bytes(b'fixture')

    def assert_unchanged(self):
        for path in (self.chrome, self.firefox):
            self.assertEqual((path / 'fixture-session').read_bytes(), b'fixture')
        self.assertEqual(self.cookie.read_bytes(), b'[]')
        self.clear.assert_not_called()
        self.invalidate.assert_not_called()

    def test_logout_removes_both_browser_profiles_and_only_selected_account_platform(self):
        others = [self.profiles / self.args.identity / 'instagram',
                  self.profiles / '_firefox' / self.args.identity / 'instagram',
                  self.profiles / 'other_account' / 'tiktok',
                  self.profiles / '_firefox' / 'other_account' / 'tiktok']
        for path in others:
            path.mkdir(parents=True)
            (path / 'keep').write_bytes(b'other fixture')
        cli._logout(self.cfg, self.args)
        self.assertFalse(self.chrome.exists())
        self.assertFalse(self.firefox.exists())
        self.assertFalse(self.cookie.exists())
        self.clear.assert_called_once_with(self.args.identity, self.args.platform)
        self.invalidate.assert_called_once_with(self.args.identity, self.args.platform)
        for path in others:
            self.assertEqual((path / 'keep').read_bytes(), b'other fixture')

    def test_logout_also_clears_replacement_profiles_and_status_for_selected_account(self):
        from socialscraper.backends.profile_store import begin
        replacements = []
        for firefox in (False, True):
            first = begin(self.profiles, self.args.identity, self.args.platform, firefox=firefox)
            first.finish(success=False)
            next_profile = begin(self.profiles, self.args.identity, self.args.platform, firefox=firefox)
            next_profile.path.mkdir(parents=True)
            (next_profile.path / 'fixture-session').write_bytes(b'new fixture')
            next_profile.finish(success=True)
            replacements.append(next_profile)
        cli._logout(self.cfg, self.args)
        for item in replacements:
            self.assertFalse(item.path.exists())
            self.assertFalse(item.state.exists())
        self.assertFalse(self.cookie.exists())

    def test_busy_firefox_profile_preserves_both_profiles_cookies_and_state(self):
        lock = runlock.OsLock(self.lock_path)
        self.assertTrue(lock.try_acquire({'owner': 'offline fixture'}))
        try:
            with self.assertRaises(OSError):
                cli._logout(self.cfg, self.args)
            self.assert_unchanged()
        finally:
            lock.release()

    def test_lock_is_held_during_profile_and_cookie_deletion(self):
        original_rmtree = cli.shutil.rmtree
        deletions = []

        def assert_locked():
            contender = runlock.OsLock(self.lock_path)
            acquired = contender.try_acquire({'owner': 'offline contender'})
            try:
                self.assertFalse(acquired, 'Deletion began before acquiring the Firefox lock')
            finally:
                contender.release()

        def remove(path, *args, **kwargs):
            assert_locked()
            deletions.append(Path(path))
            return original_rmtree(path, *args, **kwargs)

        def clear(*args, **kwargs):
            assert_locked()
            return self.real_clear(*args, **kwargs)

        self.clear.side_effect = clear
        with patch.object(cli.shutil, 'rmtree', side_effect=remove):
            cli._logout(self.cfg, self.args)
        self.assertEqual(set(deletions), {self.chrome.resolve(), self.firefox.resolve()})
        contender = runlock.OsLock(self.lock_path)
        try:
            self.assertTrue(contender.try_acquire({'owner': 'offline contender'}))
        finally:
            contender.release()

    def test_missing_profiles_can_still_clear_saved_cookies(self):
        cli.shutil.rmtree(self.chrome)
        cli.shutil.rmtree(self.firefox)
        cli._logout(self.cfg, self.args)
        self.assertFalse(self.cookie.exists())
        self.invalidate.assert_called_once_with(self.args.identity, self.args.platform)

    def test_profile_deletion_error_preserves_cookies_and_releases_lock_for_retry(self):
        with patch.object(cli.shutil, 'rmtree', side_effect=PermissionError('fixture profile is busy')):
            with self.assertRaises(OSError):
                cli._logout(self.cfg, self.args)
        self.assert_unchanged()
        cli._logout(self.cfg, self.args)
        self.assertFalse(self.firefox.exists())
        self.assertFalse(self.cookie.exists())

    def test_invalid_identity_or_platform_is_rejected_before_any_deletion(self):
        cases = [('../escape', 'tiktok'), ('CON', 'tiktok'), ('fixture_account', '../escape'),
                 ('fixture_account', 'C:/elsewhere'), ('fixture_account', 'CON'),
                 ('fixture_account', ''), ('fixture_account', 'tik/tok'), ('fixture_account', None)]
        for identity, platform in cases:
            with self.subTest(identity=identity, platform=platform), \
                    patch.object(cli.shutil, 'rmtree') as remove, patch.object(runlock, 'OsLock') as lock:
                with self.assertRaises(ValueError):
                    cli._logout(self.cfg, SimpleNamespace(identity=identity, platform=platform))
                remove.assert_not_called()
                lock.assert_not_called()
                self.assert_unchanged()

    def test_symlink_and_reparse_components_are_rejected_before_any_deletion(self):
        original_lstat = Path.lstat
        cases = [(self.chrome, True), (self.firefox.parent, True),
                 (self.profiles / '_firefox', False), (self.lock_path.parent, False)]
        for suspect, symbolic in cases:
            with self.subTest(suspect=str(suspect), symbolic=symbolic):
                def metadata(path, *args, **kwargs):
                    if path == suspect:
                        return SimpleNamespace(st_mode=stat.S_IFLNK if symbolic else stat.S_IFDIR,
                                               st_file_attributes=0 if symbolic else 0x400)
                    return original_lstat(path, *args, **kwargs)

                with patch.object(Path, 'lstat', autospec=True, side_effect=metadata), \
                        patch.object(cli.shutil, 'rmtree') as remove, patch.object(runlock, 'OsLock') as lock:
                    with self.assertRaises(ValueError):
                        cli._logout(self.cfg, self.args)
                    remove.assert_not_called()
                    lock.assert_not_called()
                self.assert_unchanged()

    def test_resolved_profile_outside_root_is_rejected_before_any_deletion(self):
        outside = self.root / 'other_application'
        outside.mkdir()
        marker = outside / 'keep'
        marker.write_bytes(b'other fixture')
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            return outside if path == self.firefox else original_resolve(path, *args, **kwargs)

        with patch.object(Path, 'resolve', autospec=True, side_effect=resolve), \
                patch.object(cli.shutil, 'rmtree') as remove, patch.object(runlock, 'OsLock') as lock:
            with self.assertRaises(ValueError):
                cli._logout(self.cfg, self.args)
            remove.assert_not_called()
            lock.assert_not_called()
        self.assert_unchanged()
        self.assertEqual(marker.read_bytes(), b'other fixture')

    def test_cli_logout_uses_shared_cleanup_even_when_firefox_engine_is_selected(self):
        with patch.object(cli, 'load', return_value=self.cfg), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(['logout', '--identity', self.args.identity, '--platform', self.args.platform,
                             '--engine', 'firefox'])
        self.assertEqual(code, 0)
        self.assertFalse(self.chrome.exists())
        self.assertFalse(self.firefox.exists())
        self.assertFalse(self.cookie.exists())


if __name__ == '__main__':
    unittest.main()
