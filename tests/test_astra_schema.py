"""Regression tests for the persona/FYP database transaction boundaries."""

import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from socialscraper import fyp, personas, repo
from socialscraper.store import Store


class AstraSchemaTests(unittest.TestCase):
    def test_schema_helpers_preserve_caller_transaction(self):
        for helper in (personas.ensure_schema, fyp.ensure_schema):
            with self.subTest(helper=helper.__module__), Store(':memory:') as store:
                db = store.db
                db.execute('CREATE TABLE marker (value INTEGER)')
                db.execute('INSERT INTO marker VALUES (1)')
                helper(db)
                self.assertTrue(db.in_transaction)
                db.rollback()
                self.assertEqual(db.execute('SELECT COUNT(*) FROM marker').fetchone()[0], 0)

    def test_schema_failure_leaves_no_partial_tables_or_columns(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE accounts (id INTEGER PRIMARY KEY)')
        db.commit()
        alters = 0

        def reject_second_alter(action, _one, _two, _database, _source):
            nonlocal alters
            if action == sqlite3.SQLITE_ALTER_TABLE:
                alters += 1
                if alters == 2:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(reject_second_alter)
        with self.assertRaises(sqlite3.DatabaseError):
            personas.ensure_schema(db)
        db.set_authorizer(None)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in db.execute('PRAGMA table_info(accounts)')}
        self.assertNotIn('personas', tables)
        self.assertNotIn('persona_id', columns)

        def reject_treffer(action, name, _two, _database, _source):
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'fyp_treffer':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(reject_treffer)
        with self.assertRaises(sqlite3.DatabaseError):
            fyp.ensure_schema(db)
        db.set_authorizer(None)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('fyp_videos', tables)

    def test_store_migrates_persona_and_fyp_schemas_together(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'store.db'
            with patch.object(fyp, 'ensure_schema', side_effect=RuntimeError('injected failure')):
                with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                    Store(path)
            with closing(sqlite3.connect(path)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                columns = {row[1] for row in db.execute('PRAGMA table_info(accounts)')}
                self.assertNotIn('personas', tables)
                self.assertNotIn('persona_id', columns)
                self.assertNotIn('fyp_videos', tables)
            with Store(path) as store:
                columns = {row[1] for row in store.db.execute('PRAGMA table_info(accounts)')}
                self.assertIn('persona_id', columns)
                self.assertIsNotNone(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='fyp_videos'").fetchone())

    def test_parallel_persona_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'store.db'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE accounts (id INTEGER PRIMARY KEY)')
            start = threading.Barrier(2)
            failures = []

            def migrate():
                try:
                    with closing(sqlite3.connect(path, timeout=5)) as db:
                        start.wait(timeout=5)
                        personas.ensure_schema(db)
                except BaseException as exc:
                    failures.append(exc)

            threads = [threading.Thread(target=migrate) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            with closing(sqlite3.connect(path)) as db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(accounts)')}
                self.assertTrue(personas.SPALTEN.keys() <= columns)

    def test_anlegen_rolls_back_persona_and_channels(self):
        with Store(':memory:') as store:
            db = store.db
            db.execute("CREATE TRIGGER reject_channel BEFORE INSERT ON persona_kanaele "
                       "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                personas.anlegen(db, 'Test', influencer='star', kanaele=['channel'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM personas').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM persona_kanaele').fetchone()[0], 0)

    def test_verbuche_requires_existing_account_and_helper_does_not_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'store.db'
            with self.assertRaisesRegex(ValueError, 'Konto nicht gefunden'):
                fyp.verbuche(path, 'missing', schritte=2, treffer=1)
            with Store(path) as store:
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM konsum_log').fetchone()[0], 0)
                account = repo.add_account(store.db, 'tiktok', 'Test')
                fyp._verbuche_in_db(store.db, account['identity'], schritte=2, treffer=1)
                self.assertTrue(store.db.in_transaction)
                store.db.rollback()
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM konsum_log').fetchone()[0], 0)
                self.assertEqual(store.db.execute('SELECT konsum_sitzungen FROM accounts WHERE id=?',
                                                  (account['id'],)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
