import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from socialscraper import cli, login, runlock
from socialscraper.config import Settings, load

ROOT = pathlib.Path(__file__).resolve().parent.parent


class RunLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def test_acquire_release_and_holder(self):
        self.assertIsNone(runlock.holder(self.root))
        with runlock.acquire(self.root, 'gui') as info:
            self.assertEqual(info['pid'], os.getpid())
            holder = runlock.holder(self.root)
            self.assertEqual((holder['owner'], holder['pid']), ('gui', os.getpid()))
            self.assertRegex(holder['since'], r'^\d{4}-\d\d-\d\dT')
        self.assertIsNone(runlock.holder(self.root))
        with runlock.acquire(self.root, 'cli'):
            pass  # nach der Freigabe wieder nehmbar

    def test_busy_gives_german_message_with_owner(self):
        with runlock.acquire(self.root, 'daemon'):
            with self.assertRaises(runlock.Busy) as ctx:
                with runlock.acquire(self.root, 'cli'):
                    self.fail('darf nicht betreten werden')
            self.assertIn('Hintergrunddienst', str(ctx.exception))
            self.assertIn('Es läuft gerade schon', str(ctx.exception))
            self.assertEqual(ctx.exception.info['owner'], 'daemon')

    def test_lock_is_released_when_body_raises(self):
        with self.assertRaises(KeyError):
            with runlock.acquire(self.root, 'cli'):
                raise KeyError('x')
        self.assertIsNone(runlock.holder(self.root))

    def test_wait_with_timeout_gives_up(self):
        with runlock.acquire(self.root, 'daemon'):
            with self.assertRaises(runlock.Busy):
                with runlock.acquire(self.root, 'cli', wait=True, timeout=0.3, poll=0.1):
                    pass

    def test_dead_owner_lock_is_taken_over(self):
        code = ('import sys,time;from socialscraper import runlock;'
                'cm=runlock.acquire(sys.argv[1],"fremd");cm.__enter__();print("bereit",flush=True);time.sleep(60)')
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8='1')
        proc = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-c', code, str(self.root)], stdout=subprocess.PIPE, env=env)
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdout.close()))
        self.assertEqual(proc.stdout.readline().strip(), b'bereit')
        holder = runlock.holder(self.root)
        self.assertEqual((holder['owner'], holder['pid']), ('fremd', proc.pid))
        with self.assertRaises(runlock.Busy):
            with runlock.acquire(self.root, 'cli'):
                pass
        proc.kill(); proc.wait()  # Absturz des Besitzers
        self.assertIsNone(runlock.holder(self.root))
        with runlock.acquire(self.root, 'cli'):
            self.assertEqual(runlock.holder(self.root)['owner'], 'cli')

    def test_root_for_db(self):
        self.assertEqual(runlock.root_for_db(self.root / 'data' / 'scrape.db'), self.root.resolve())
        self.assertEqual(runlock.root_for_db(self.root / 'scrape.db'), self.root.resolve())


class CliLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='', profiles_dir=str(self.root / 'p'))

    def run_cli(self, argv):
        with patch.object(cli, 'load', return_value=self.cfg), \
             patch.object(cli, '_ctx_and_backend', return_value=(Mock(), Mock(), Mock())), \
             patch('socialscraper.login.check', return_value=True) as check, \
             patch('socialscraper.login.login', return_value=True) as logn, \
             patch.object(cli, 'ensure', return_value='ok'), \
             self.assertLogs('cli', 'ERROR') as logs, \
             contextlib.redirect_stdout(io.StringIO()):
            # assertLogs braucht mindestens eine Meldung; bei Erfolg schreiben wir selbst eine.
            code = cli.main(argv)
            cli.log.error('ende')
        return code, check, logn, logs.output

    def test_browser_commands_exit_4_when_busy(self):
        with runlock.acquire(runlock.root_for_db(self.cfg.db_path), 'daemon'):
            for argv in (['check'], ['login'], ['scrape', 'https://www.tiktok.com/@u/video/1'], ['profile', 'user']):
                code, check, logn, out = self.run_cli(argv)
                self.assertEqual(code, 4, argv)
                self.assertTrue(any('Es läuft gerade schon' in line for line in out), out)
                check.assert_not_called(); logn.assert_not_called()

    def test_browser_command_runs_and_releases_when_free(self):
        code, check, _, _ = self.run_cli(['check'])
        self.assertEqual(code, 0); check.assert_called_once()
        self.assertIsNone(runlock.holder(runlock.root_for_db(self.cfg.db_path)))


class HeadlessTests(unittest.TestCase):
    def test_defaults_are_headless(self):
        self.assertTrue(Settings().headless)
        self.assertTrue(load('gibt-es-nicht.toml').headless)
        from socialscraper.formlogic import DEFAULT_SETTINGS_TEXT   # mitgelieferte Standardwerte, nicht die echte settings.toml des Nutzers
        self.assertIn('headless = true', DEFAULT_SETTINGS_TEXT)

    def test_login_window_stays_visible(self):
        calls = []

        class Page:
            def goto(self, *a, **k): pass
            def wait_for_timeout(self, ms): pass
            def evaluate(self, *a, **k): return None

        class Backend:
            @contextmanager
            def session(self, ident, proxy, *, headless=False, platform=None):
                calls.append(headless); yield object()
            def page(self, handle): return Page()
            def read_cookies(self, handle): return []

        im = Mock()
        with self.assertRaises(TimeoutError):
            login.login(Backend(), im, 'tiktok_test', 'tiktok', mode='qr', timeout=0.05)
        self.assertEqual(calls, [False])


if __name__ == '__main__':
    unittest.main()
