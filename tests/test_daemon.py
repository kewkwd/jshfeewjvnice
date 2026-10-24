import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from socialscraper import daemon, repo, runlock, scheduler
from socialscraper.config import Settings
from socialscraper.store import Store

ROOT = pathlib.Path(__file__).resolve().parent.parent
YT = 'https://www.youtube.com/watch?v='


def fake_result(target_id, status='ok'):
    return {'run_id': 1, 'target_id': target_id, 'status': status, 'error': None,
            'videos_seen': 0, 'videos_new': 0, 'comments_new': 0}


class DaemonBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'data' / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'data' / 'profiles'))
        self.store = Store(self.cfg.db_path); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)

    def add(self, value='aaaaaaaaaaa'):
        return repo.add_target(self.db, 'video', 'youtube', YT + value)

    def run_in_thread(self, **kw):
        stop = threading.Event()
        result = {}

        def work():
            try:
                result['ran'] = daemon.run(self.root, stop_event=stop, cfg=self.cfg, install_signals=False, **kw)
            except BaseException as error:  # noqa
                result['error'] = error
        thread = threading.Thread(target=work, daemon=True); thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(5)))
        return stop, thread, result

    def wait_for(self, condition, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            time.sleep(0.05)
        return False


class StatusAndLoopTests(DaemonBase):
    def test_status_when_not_running(self):
        s = daemon.status(self.root)
        self.assertEqual((s['running'], s['pid'], s['current'], s['stale']), (False, None, None, False))

    def test_once_runs_all_due_targets_one_after_another_and_survives_errors(self):
        a, b, c = self.add('aaaaaaaaaaa'), self.add('bbbbbbbbbbb'), self.add('ccccccccccc')
        later = repo.add_target(self.db, 'video', 'youtube', YT + 'ddddddddddd')
        self.db.execute('UPDATE targets SET next_run_at=? WHERE id=?', ('2999-01-01T00:00:00Z', later['id'])); self.db.commit()
        order, holders = [], []

        def fake(root, tid, source, **kw):
            order.append((tid, source)); holders.append(runlock.holder(root)['owner'])
            if tid == b['id']:
                raise RuntimeError('boom')
            return fake_result(tid)

        with patch.object(scheduler, 'run_target', side_effect=fake):
            ran = daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        self.assertEqual([t for t, _ in order], [a['id'], b['id'], c['id']])  # der Fehler bei b stoppt c nicht
        self.assertEqual({s for _, s in order}, {'daemon'})
        self.assertEqual(set(holders), {'daemon'})  # jedes Ziel unter der Sperre
        self.assertEqual(ran, 2)
        self.assertIsNone(runlock.holder(self.root))
        self.assertFalse(daemon.status(self.root)['running'])

    def test_heartbeat_file_and_current_target_while_running(self):
        t = self.add()
        gate, started = threading.Event(), threading.Event()

        def slow(root, tid, source, **kw):
            started.set(); gate.wait(10)
            return fake_result(tid)

        with patch.object(scheduler, 'run_target', side_effect=slow):
            stop, thread, result = self.run_in_thread(poll=1)
            self.assertTrue(started.wait(10))
            s = daemon.status(self.root)
            self.assertTrue(s['running'])
            self.assertEqual(s['pid'], os.getpid())
            self.assertEqual(s['current']['target_id'], t['id'])
            self.assertFalse(s['stale'])
            data = json.loads(daemon.state_path(self.root).read_text(encoding='utf-8'))
            self.assertEqual(set(data) & {'pid', 'started_at', 'last_beat', 'current'}, {'pid', 'started_at', 'last_beat', 'current'})
            self.assertEqual(runlock.holder(self.root)['owner'], 'daemon')  # Fenster sieht „Dienst arbeitet“
            gate.set()
            self.assertTrue(self.wait_for(lambda: daemon.status(self.root)['current'] is None))
            stop.set(); thread.join(5)
        self.assertFalse(daemon.status(self.root)['running'])
        self.assertNotIn('error', result)

    def test_only_one_instance(self):
        stop, thread, result = self.run_in_thread(poll=1)
        self.assertTrue(self.wait_for(lambda: daemon.status(self.root)['running']))
        with self.assertRaises(daemon.AlreadyRunning) as ctx:
            daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        self.assertEqual(ctx.exception.pid, os.getpid())
        self.assertIn('läuft schon', str(ctx.exception))
        stop.set(); thread.join(5)

    def test_stale_heartbeat_is_reported(self):
        stop, thread, _ = self.run_in_thread(poll=1)
        self.assertTrue(self.wait_for(lambda: daemon.status(self.root)['running']))
        self.assertFalse(daemon.status(self.root)['stale'])
        self.assertTrue(daemon.status(self.root, stale_after=-1)['stale'])
        stop.set(); thread.join(5)

    def test_stop_asks_politely_and_cancels_running_target(self):
        self.add()
        started = threading.Event()
        seen = {}

        def long_run(root, tid, source, cancel=None, **kw):
            started.set()
            for _ in range(200):
                if cancel():
                    seen['cancelled'] = True
                    return fake_result(tid, 'cancelled')
                time.sleep(0.05)
            return fake_result(tid)

        with patch.object(scheduler, 'run_target', side_effect=long_run):
            stop, thread, result = self.run_in_thread(poll=1)
            self.assertTrue(started.wait(10))
            self.assertTrue(daemon.stop(self.root, timeout=10))
            thread.join(5)
        self.assertTrue(seen.get('cancelled'))
        self.assertFalse(daemon.status(self.root)['running'])
        self.assertFalse(daemon.stop_flag(self.root).exists())

    def test_stop_without_running_daemon_is_true(self):
        self.assertTrue(daemon.stop(self.root))

    def test_busy_browser_skips_targets_without_running_them(self):
        self.add()
        with patch.object(scheduler, 'run_target') as run, runlock.acquire(self.root, 'gui'):
            ran = daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        self.assertEqual(ran, 0); run.assert_not_called()
        self.assertEqual(len(repo.due_targets(self.db)), 1)  # bleibt fällig

    def test_crash_recovery_marks_old_running_runs_failed(self):
        t = self.add()
        rid = repo.start_run(self.db, t['id'], 'x', 'daemon')  # „Absturz“: Lauf blieb auf running stehen
        with patch.object(scheduler, 'run_target', return_value=fake_result(t['id'])):
            daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        row = repo._one(self.db, 'SELECT * FROM runs WHERE id=?', (rid,))
        self.assertEqual((row['status'], row['error']), ('failed', 'Unterbrochen (Programm beendet)'))
        self.assertIsNotNone(row['finished_at'])

    def test_running_run_is_kept_while_someone_else_holds_the_lock(self):
        t = self.add()
        rid = repo.start_run(self.db, t['id'], 'x', 'manual')
        with runlock.acquire(self.root, 'gui'), patch.object(scheduler, 'run_target'):
            daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        self.assertEqual(repo._one(self.db, 'SELECT status FROM runs WHERE id=?', (rid,))['status'], 'running')

    def test_real_scheduler_run_survives_hard_stop(self):
        """Abbruch mitten im Ziel (KeyboardInterrupt) lässt Lauf und Ziel in einem sauberen Zustand."""
        t = self.add()

        class Boom:
            platform = 'youtube'; needs_browser = False; needs_auth = False
            def fetch(self, url, ctx): raise KeyboardInterrupt()

        with patch.object(scheduler, 'resolve', return_value=Boom()):
            with self.assertRaises(KeyboardInterrupt):
                daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        row = repo.list_runs(self.db)[0]
        self.assertEqual((row['status'], row['target_id']), ('cancelled', t['id']))
        self.assertFalse(daemon.status(self.root)['running'])  # Sperre frei
        self.assertIsNone(runlock.holder(self.root))
        self.assertEqual(daemon.status(self.root)['current'], None)

    def test_sync_is_called_after_run_and_failures_are_not_fatal(self):
        t = self.add(); self.add('bbbbbbbbbbb')
        calls = []

        def push(root):
            calls.append(root); raise RuntimeError('Cloud kaputt')

        fake_sync = types.SimpleNamespace(push_if_configured=push)
        with patch.dict(sys.modules, {'socialscraper.sync': fake_sync}), \
             patch.object(sys.modules['socialscraper'], 'sync', fake_sync, create=True), \
             patch.object(scheduler, 'run_target', side_effect=lambda r, tid, s, **kw: fake_result(tid)):
            ran = daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        self.assertEqual(ran, 2); self.assertEqual(len(calls), 2)

    def test_local_api_is_started_with_daemon_and_stopped_at_exit(self):
        from unittest.mock import Mock
        handle = Mock()
        with patch('socialscraper.api.start_if_enabled', return_value=(handle, None)) as start:
            stop, thread, _ = self.run_in_thread(poll=1)
            self.assertTrue(self.wait_for(lambda: start.called))
            handle.stop.assert_not_called()
            stop.set(); thread.join(5)
        self.assertEqual(start.call_args.args[0], self.root)
        handle.stop.assert_called_once()

    def test_api_failure_is_not_fatal(self):
        with patch('socialscraper.api.start_if_enabled', side_effect=RuntimeError('x')):
            stop, thread, result = self.run_in_thread(poll=1)
            self.assertTrue(self.wait_for(lambda: daemon.status(self.root)['running']))
            stop.set(); thread.join(5)
        self.assertNotIn('error', result)

    def test_missing_sync_module_is_fine(self):
        with patch.dict(sys.modules, {'socialscraper.sync': None}):
            daemon._push(self.root)


class DetachedServiceTests(unittest.TestCase):
    def test_start_status_stop_of_a_real_background_process(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # der Prozess braucht kurz zum Beenden
            root = pathlib.Path(tmp)
            env = {'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1', 'SS_DB_PATH': str(root / 'data' / 'scrape.db')}
            with patch.dict(os.environ, env):
                self.addCleanup(daemon.stop, root, 5)
                first = daemon.start_detached(root)
                self.assertTrue(first['running'])
                self.assertNotEqual(first['pid'], os.getpid())
                self.assertEqual(daemon.start_detached(root)['pid'], first['pid'])  # nicht doppelt
                s = daemon.status(root)
                self.assertTrue(s['running'] and not s['stale'] and s['last_beat'])
                self.assertTrue((root / 'data' / 'daemon.log').exists())
                self.assertTrue(daemon.stop(root, timeout=20))
                self.assertFalse(daemon.status(root)['running'])

    def test_force_kill_leaves_no_lock_behind(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # der Prozess braucht kurz zum Beenden
            root = pathlib.Path(tmp)
            env = {'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1', 'SS_DB_PATH': str(root / 'data' / 'scrape.db')}
            with patch.dict(os.environ, env):
                self.addCleanup(daemon.stop, root, 5)
                pid = daemon.start_detached(root)['pid']
                daemon._kill(pid)
                end = time.monotonic() + 15
                while daemon.status(root)['running'] and time.monotonic() < end:
                    time.sleep(0.2)
                self.assertFalse(daemon.status(root)['running'])
                self.assertIsNone(runlock.holder(root))
                again = daemon.start_detached(root)  # Neustart nach hartem Abbruch klappt
                self.assertTrue(again['running'])
                self.assertTrue(daemon.stop(root, timeout=20))

    def test_service_command_is_windowless_python_on_windows(self):
        cmd = daemon._service_command(['--root', 'x'])
        self.assertEqual(cmd[1:], ['-m', 'socialscraper', 'daemon', '--root', 'x'])
        if os.name == 'nt':
            self.assertTrue(cmd[0].lower().endswith(('pythonw.exe', 'python.exe')))
            if (pathlib.Path(sys.executable).parent / 'pythonw.exe').exists():
                self.assertTrue(cmd[0].lower().endswith('pythonw.exe'))


@unittest.skipUnless(os.name == 'nt', 'Autostart-Verknüpfung nur unter Windows')
class AutostartTests(unittest.TestCase):
    def test_shortcut_is_created_and_removed_in_given_folder_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            startup = pathlib.Path(tmp) / 'Startup'
            with patch.dict(os.environ, {'SS_AUTOSTART_DIR': str(startup)}):
                self.assertFalse(daemon.autostart_enabled())
                self.assertTrue(daemon.set_autostart(True, root=ROOT))
                lnk = startup / daemon.AUTOSTART_NAME
                self.assertTrue(lnk.exists() and daemon.autostart_enabled())
                ps = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:L);'
                      'Write-Output $s.TargetPath;Write-Output $s.Arguments;Write-Output $s.WorkingDirectory')
                out = subprocess.run(['powershell', '-NoProfile', '-Command', ps], env=dict(os.environ, L=str(lnk)),
                                     capture_output=True, text=True, timeout=60).stdout.splitlines()
                self.assertTrue(out[0].lower().endswith(('pythonw.exe', 'python.exe')), out)
                self.assertIn('-m socialscraper daemon', out[1])
                self.assertEqual(pathlib.Path(out[2]).resolve(), ROOT.resolve())
                self.assertTrue(daemon.set_autostart(True, root=ROOT))  # zweimal ist harmlos
                self.assertFalse(daemon.set_autostart(False))
                self.assertFalse(lnk.exists() or daemon.autostart_enabled())
                self.assertFalse(daemon.set_autostart(False))


if __name__ == '__main__':
    unittest.main()
