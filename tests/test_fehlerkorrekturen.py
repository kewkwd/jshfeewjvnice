"""Rückfalltests zu den Fehlerkorrekturen vom 13.10.2026, die in keine bestehende Testdatei passen."""
import json
import logging
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import aiconfig, api, guicore, integration, people_repo, repo, sentiment, util
from socialscraper.models import Comment, Video
from socialscraper.store import Store

TOKEN = 'test-token-1234567890-abcdefghij'
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class AiConfigTests(unittest.TestCase):
    def test_file_in_other_encoding_counts_as_corrupt_not_as_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            p = aiconfig.path(root)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({'api_key': 'sk-ant-test-1234567890'}), encoding='utf-16')
            cfg, problem = aiconfig.load_with_status(root)
            self.assertIn('beschädigt', problem)
            self.assertEqual(cfg['api_key'], aiconfig.default_config()['api_key'])

    @unittest.skipUnless(os.name == 'nt', 'Tkinter-Modul nur unter Windows geprüft')
    def test_price_text_tolerates_hand_edited_values(self):
        from socialscraper.ui.tab_settings import SettingsTab
        self.assertEqual(SettingsTab._price_text(3.0), '3')
        self.assertEqual(SettingsTab._price_text(0.25), '0,25')
        self.assertEqual(SettingsTab._price_text(None), '')
        self.assertEqual(SettingsTab._price_text('3,00'), '3,00')     # Text aus ai.json: kein Absturz beim Programmstart
        self.assertEqual(SettingsTab._price_text(True), 'True')


class DaemonLogTests(unittest.TestCase):
    def test_daemon_file_log_has_the_password_filter(self):
        from socialscraper.commands import daemon as cmd
        from socialscraper.net.accountproxy import _ScrubFilter
        with tempfile.TemporaryDirectory() as tmp:
            handler = cmd._setup_file_log(tmp)
            try:
                self.assertTrue(any(isinstance(f, _ScrubFilter) for f in handler.filters))
            finally:
                logging.getLogger().removeHandler(handler)
                handler.close()


class AccountStatusTests(unittest.TestCase):
    """guicore.account_status liest nur die Cookie-Datei; abgelaufene Cookies sind keine Anmeldung."""

    def write(self, root, expires):
        path = root / 'data' / 'cookies' / 'tiktok_main.tiktok.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        cookie = {'name': 'sessionid', 'value': 'wert', 'domain': '.tiktok.com', 'path': '/'}
        if expires is not None:
            cookie['expires'] = expires
        path.write_text(json.dumps([cookie]), encoding='utf-8')

    def test_expired_cookie_is_reported_as_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.write(root, time.time() - 3600)
            self.assertEqual(guicore.account_status('tiktok', root, 'tiktok_main')[0], 'expired')
            self.write(root, time.time() + 3600)
            self.assertEqual(guicore.account_status('tiktok', root, 'tiktok_main')[0], 'saved')
            self.write(root, None)                                          # Sitzungscookie ohne Ablauf
            self.assertEqual(guicore.account_status('tiktok', root, 'tiktok_main')[0], 'saved')
            self.write(root, -1)                                            # Browser-Schreibweise für „kein Ablauf“
            self.assertEqual(guicore.account_status('tiktok', root, 'tiktok_main')[0], 'saved')


class ApiGroupTests(unittest.TestCase):
    """Gruppenangaben in der Adresse: Ziffernfolgen, die keine Nummer sind, und Namen aus Ziffern."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.db = cls.root / 'data' / 'scrape.db'
        with Store(str(cls.db)) as store:
            repo.ensure_default_accounts(store.db)
            store.save_result(Video('youtube', 'a1abcdefghi', 'https://youtu.be/a1abcdefghi', author='X', comment_count=0), [], None)
            group = repo.create_group(store.db, '2026')
            repo.add_videos_to_group(store.db, group['id'], ['youtube:a1abcdefghi'])
        cfg = integration.default_config()
        cfg['api'].update(token=TOKEN)
        cls.handle = api.start(cls.root, db_path=cls.db, port=0, cfg=cfg)

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls._tmp.cleanup()

    def get(self, path, expect=200):
        request = urllib.request.Request(self.handle.url + path, headers={'Authorization': 'Bearer ' + TOKEN})
        try:
            with _OPENER.open(request, timeout=10) as response:
                self.assertEqual(response.status, expect)
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            body = error.read()
            error.close()
            self.assertEqual(error.code, expect, body)
            return json.loads(body or b'null')

    def test_digit_like_and_overlong_group_values_are_not_a_server_error(self):
        self.assertEqual(self.get('/v1/videos?group=99999999999999999999')['items'], [])
        self.assertEqual(self.get('/v1/videos?group=%C2%B2')['items'], [])
        self.assertEqual(self.get('/v1/sentiment?group=%C2%B2')['schema_version'], 1)
        self.get('/v1/groups/99999999999999999999/people', 404)

    def test_group_named_with_digits_is_found_by_name(self):
        self.assertEqual(self.get('/v1/people?group=2026')['total'], 0)          # kein Fehler, Gruppe bekannt
        self.assertEqual(self.get('/v1/groups/2026/people')['group']['name'], '2026')


class ReadOnlyConnectionTests(unittest.TestCase):
    def test_local_file_is_opened_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'a.db'
            with Store(str(path)):
                pass
            db = util.ro_connect(path)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("INSERT INTO groups (name, created_at) VALUES ('x', 'y')")
            finally:
                db.close()
            with self.assertRaises(sqlite3.OperationalError):              # fehlende Datei wird nicht angelegt
                util.ro_connect(pathlib.Path(tmp) / 'fehlt.db')

    def test_network_path_avoids_the_uri_form(self):
        """SQLite lehnt file://server/… ab; ein Netzwerkpfad bekommt eine normale Verbindung mit query_only."""
        recorded = {}

        class FakeDb:
            def execute(self, sql):
                recorded['pragma'] = sql
        with patch.object(pathlib.Path, 'is_file', return_value=True), \
             patch('sqlite3.connect', side_effect=lambda *a, **k: recorded.update(args=a, kwargs=k) or FakeDb()):
            util.ro_connect(r'\\nas\freigabe\scrape.db', timeout=7)
        self.assertEqual(recorded['args'], (r'\\nas\freigabe\scrape.db',))
        self.assertEqual(recorded['kwargs'], {'timeout': 7})
        self.assertEqual(recorded['pragma'], 'PRAGMA query_only=ON')


class RepoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(str(pathlib.Path(self.tmp.name) / 'data' / 'scrape.db')); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)

    def test_missing_account_or_group_is_named_not_called_a_duplicate(self):
        with self.assertRaisesRegex(ValueError, 'Konto oder die Gruppe'):
            repo.add_target(self.db, 'video', 'youtube', 'https://youtu.be/a1abcdefghi', account_id=999, account_mode='fixed')
        t = repo.add_target(self.db, 'video', 'youtube', 'https://youtu.be/a1abcdefghi')
        with self.assertRaisesRegex(ValueError, 'Konto oder die Gruppe'):
            repo.update_target(self.db, t['id'], group_id=999)
        with self.assertRaisesRegex(ValueError, 'gibt es schon'):            # ein echtes Doppel heißt weiter so
            repo.add_target(self.db, 'video', 'youtube', 'https://youtu.be/a1abcdefghi')

    def test_delete_account_keeps_the_deletion_when_the_proxy_file_fails(self):
        acc = repo.add_account(self.db, 'tiktok', 'Zweit')
        repo.set_account_proxy_ref(self.db, acc['id'], 'http://user:*****@host:1')
        with patch('socialscraper.net.accountproxy.remove_entry', side_effect=ValueError('Datei gesperrt')), \
             self.assertLogs('socialscraper.repo', level='WARNING') as logs:
            repo.delete_account(self.db, acc['id'])
        self.assertIsNone(repo.get_account(self.db, acc['id']))
        self.assertIn('Zweit', logs.output[0])
        self.assertNotIn('*****', logs.output[0])


class ChannelListTests(unittest.TestCase):
    def test_locked_database_is_reported_not_treated_as_no_channels(self):
        import contextlib
        from socialscraper import autologic, chanlogic

        @contextlib.contextmanager
        def locked(db_path):
            raise sqlite3.OperationalError('database is locked')
            yield
        with patch.object(autologic, '_read', locked):
            self.assertEqual(chanlogic.load('x.db'), [])                        # Fenster-Tab: weiter tolerant
            with self.assertRaisesRegex(ValueError, 'belegt'):
                chanlogic.load('x.db', strict=True)                              # Befehlszeile, Einzelheiten: Meldung


class SentimentMessageTests(unittest.TestCase):
    def summary(self, **values):
        r = {'analyzed': 0, 'skipped': 0, 'remaining': 0, 'status': 'ok', 'planned': 3, 'input_tokens': 0, 'output_tokens': 0}
        r.update(values)
        return sentiment._summary(r)

    def test_unreadable_entries_do_not_claim_a_reached_limit(self):
        self.assertNotIn('Obergrenze', self.summary(analyzed=2, skipped=1, remaining=1, status='partial'))
        self.assertIn('nicht lesbar', self.summary(analyzed=2, skipped=1, remaining=1, status='partial'))
        self.assertIn('noch 4 offen (Obergrenze', self.summary(analyzed=3, skipped=1, remaining=5))


class FirstSeenFormatTests(unittest.TestCase):
    def test_migration_writes_the_agreed_time_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'scrape.db'
            with Store(str(path)) as store:
                store.save_result(Video('youtube', 'a1abcdefghi', 'https://youtu.be/a1abcdefghi'), [], None)
                store.db.execute("UPDATE videos SET first_seen_at=NULL, fetched_at='2026-01-01 23:00:00'")
                store.db.commit()
            with Store(str(path)) as store:                                 # Migration läuft beim Öffnen
                self.assertEqual(store.db.execute('SELECT first_seen_at FROM videos').fetchone()[0], '2026-01-01T23:00:00Z')

    def test_people_first_seen_compares_both_stored_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Store(str(pathlib.Path(tmp) / 'scrape.db')) as store:
                db = store.db
                store.save_result(Video('youtube', 'a1abcdefghi', 'https://youtu.be/a1abcdefghi'),
                                  [Comment('youtube', 'a1abcdefghi', 'k1', 'eins', author='Anna'),
                                   Comment('youtube', 'a1abcdefghi', 'k2', 'zwei', author='Anna')], None)
                db.execute("UPDATE comments SET first_seen_at='2026-01-01 23:00:00' WHERE comment_id='k1'")
                db.execute("UPDATE comments SET first_seen_at='2026-01-01T01:00:00Z' WHERE comment_id='k2'")
                db.commit()
                people_repo.prepare(db)
                rows, total = people_repo.list_people(db, 'commenter')
                self.assertEqual(total, 1)
                self.assertEqual(rows[0]['first_seen_at'], '2026-01-01T01:00:00Z')   # vorher gewann die Leerzeichen-Form


class CancelTests(unittest.TestCase):
    """„Abbrechen" im Fenster: erst die Abbruchdatei (der Lauf endet selbst als „abgebrochen"), dann erst hart."""

    class FakeProc:
        pid = 4711

        def __init__(self, cancel_file):
            self.cancel_file, self.polls = cancel_file, 0

        def poll(self):
            self.polls += 1
            return 130 if self.cancel_file.exists() else None

    def test_run_target_is_asked_to_stop_before_the_tree_is_killed(self):
        job = guicore.Job(['run-target', '1'], lambda line: None, lambda code: None)
        self.assertIsNotNone(job.cancel_file)
        job.proc = self.FakeProc(job.cancel_file)
        with patch.object(guicore.subprocess, 'run', side_effect=AssertionError('taskkill darf hier nicht laufen')):
            self.assertTrue(job.cancel())
            self.assertTrue(job._cancel_finished.wait(5))
        self.assertTrue(job.cancelled)
        self.assertFalse(job.cancel_file.exists())                       # aufgeräumt
        self.assertIsNone(guicore.Job(['scrape', 'https://x'], lambda l: None, lambda c: None).cancel_file)

    def test_run_target_command_passes_the_cancel_file(self):
        from types import SimpleNamespace
        from socialscraper import scheduler
        from socialscraper.commands import run_target as cmd
        from socialscraper.config import Settings
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            flag = root / 'abbruch.txt'
            cfg = Settings(db_path=str(root / 'data' / 'scrape.db'))
            result = {'status': 'cancelled', 'videos_seen': 0, 'videos_new': 0, 'comments_new': 0, 'error': None}
            with patch.dict(os.environ, {'SS_CANCEL_FILE': str(flag)}), \
                 patch.object(scheduler, 'run_target', return_value=result) as run, \
                 patch('socialscraper.daemon._push'), patch('builtins.print'):
                code = cmd.run(SimpleNamespace(id=1, root=None), cfg)   # ohne --root wechselt der Befehl das Verzeichnis nicht
            cancel = run.call_args.kwargs['cancel']
            self.assertEqual(code, 130)
            self.assertFalse(cancel())
            flag.write_text('stop', encoding='utf-8')
            self.assertTrue(cancel())

    def test_interrupted_runs_are_recovered_when_nobody_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            db = root / 'data' / 'scrape.db'
            with Store(str(db)) as store:
                run_id = repo.start_run(store.db, None, 'Test', 'manual')
            with patch.dict(os.environ, {'SS_DB_PATH': str(db)}):
                self.assertEqual(guicore.recover_interrupted_runs(root), 1)
            with Store(str(db)) as store:
                row = store.db.execute('SELECT status, error FROM runs WHERE id=?', (run_id,)).fetchone()
            self.assertEqual(row[0], 'failed')
            self.assertIn('Unterbrochen', row[1])


if __name__ == '__main__':
    unittest.main()
