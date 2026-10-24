import contextlib
import io
import logging
import os
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from socialscraper import cli, daemon, repo, runlock, scheduler
from socialscraper.config import Settings
from socialscraper.store import Store


class AutoCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        # Unabhängig von der Umgebung: ein gesetztes SS_DB_PATH darf den Test nicht auf eine fremde Datenbank lenken
        env = patch.dict(os.environ); env.start(); self.addCleanup(env.stop)
        os.environ.pop('SS_DB_PATH', None)
        self.cfg = Settings(db_path=str(self.root / 'data' / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'data' / 'profiles'))
        cwd = os.getcwd(); self.addCleanup(os.chdir, cwd)
        # cli.main richtet die Protokollierung ein; danach den Zustand von vorher wiederherstellen
        root_logger = logging.getLogger()
        before, level = list(root_logger.handlers), root_logger.level
        def restore():
            for h in list(root_logger.handlers):
                if h not in before:
                    root_logger.removeHandler(h); h.close()
            root_logger.setLevel(level)
        self.addCleanup(restore)

    def main(self, argv):
        out = io.StringIO()
        with patch.object(cli, 'load', return_value=self.cfg), contextlib.redirect_stdout(out):
            return cli.main(argv), out.getvalue()

    def result(self, status, error=None):
        return {'run_id': 1, 'target_id': 5, 'status': status, 'error': error,
                'videos_seen': 2, 'videos_new': 1, 'comments_new': 3}

    def test_run_target_exit_codes_and_summary(self):
        for status, code in (('ok', 0), ('partial', 1), ('failed', 1), ('cancelled', 130)):
            with patch.object(scheduler, 'run_target', return_value=self.result(status, 'Hinweis' if status != 'ok' else None)) as run:
                got, out = self.main(['run-target', '5', '--root', str(self.root)])
            self.assertEqual(got, code, status)
            self.assertEqual(run.call_args.args[1:3], (5, 'manual'))
            self.assertIn('2 Videos, 1 neu, 3 neue Kommentare', out)

    def test_run_target_names_the_target_and_uses_singular(self):
        with Store(self.cfg.db_path) as s:
            repo.ensure_default_accounts(s.db)
            t = repo.add_target(s.db, 'hashtag', 'youtube', 'lofi')
        res = dict(self.result('ok'), videos_seen=1, videos_new=1, comments_new=1)
        with patch.object(scheduler, 'run_target', return_value=res):
            got, out = self.main(['run-target', str(t['id']), '--root', str(self.root)])
        self.assertEqual(got, 0)
        self.assertIn('Hashtag #lofi (YouTube): fertig - 1 Video, 1 neu, 1 neuer Kommentar', out)
        self.assertNotIn(f"Ziel {t['id']}", out)

    def test_run_target_busy_exits_4(self):
        with runlock.acquire(self.root, 'daemon'), patch.object(scheduler, 'run_target') as run, \
             self.assertLogs('cli', 'ERROR') as logs:
            got, _ = self.main(['run-target', '5', '--root', str(self.root)])
        self.assertEqual(got, 4); run.assert_not_called()
        self.assertIn('Es läuft gerade schon', logs.output[0])

    def test_run_target_unknown_id_is_a_clean_error(self):
        with self.assertLogs('cli', 'ERROR') as logs:
            got, _ = self.main(['run-target', '99', '--root', str(self.root)])
        self.assertEqual(got, 1); self.assertIn('Ziel nicht gefunden', logs.output[0])

    def test_daemon_once_runs_due_targets_and_logs_to_file(self):
        with Store(self.cfg.db_path) as s:
            repo.ensure_default_accounts(s.db)
            t = repo.add_target(s.db, 'video', 'youtube', 'https://www.youtube.com/watch?v=aaaaaaaaaaa')
        with patch.object(scheduler, 'run_target', return_value=self.result('ok')) as run:
            got, out = self.main(['daemon', '--once', '--root', str(self.root)])
        self.assertEqual(got, 0); self.assertIn('1 Ziele', out)
        self.assertEqual(run.call_args.args[1], t['id'])
        self.assertTrue((self.root / 'data' / 'daemon.log').exists())

    def test_daemon_second_instance_exits_quietly_with_message(self):
        guard = runlock.OsLock(daemon._lock_path(self.root))
        self.assertTrue(guard.try_acquire({'owner': 'daemon', 'pid': 4242}))
        self.addCleanup(guard.release)
        got, out = self.main(['daemon', '--once', '--root', str(self.root)])
        self.assertEqual(got, 0); self.assertIn('läuft schon', out); self.assertIn('4242', out)

    def test_discover_prints_links_and_refuses_instagram(self):
        with patch('socialscraper.discovery.discover', return_value=['https://www.youtube.com/watch?v=aaaaaaaaaaa']) as d:
            got, out = self.main(['discover', 'hashtag', '#Lofi', '--limit', '3'])
        self.assertEqual((got, out.strip()), (0, 'https://www.youtube.com/watch?v=aaaaaaaaaaa'))
        self.assertEqual(d.call_args.args[:3], ('hashtag', 'youtube', 'lofi'))  # normalisiert
        self.assertEqual(d.call_args.args[4], 3)
        with self.assertLogs('cli', 'ERROR') as logs:
            got, _ = self.main(['discover', 'search', 'abc', '--platform', 'instagram'])
        self.assertEqual(got, 1); self.assertIn('Instagram', logs.output[0])

    def test_discover_tiktok_takes_the_browser_lock(self):
        with runlock.acquire(runlock.root_for_db(self.cfg.db_path), 'daemon'), \
             patch.object(cli, 'make_runtime', return_value=(Mock(), Mock(), Mock())), \
             patch.object(cli, 'shutdown_runtime'), \
             patch('socialscraper.discovery.discover') as d, self.assertLogs('cli', 'ERROR'):
            got, _ = self.main(['discover', 'profile', 'someone', '--platform', 'tiktok'])
        self.assertEqual(got, 4); d.assert_not_called()


if __name__ == '__main__':
    unittest.main()
