import json
from pathlib import Path
import sqlite3
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from socialscraper.profile_inventory import inventory
from socialscraper.android.avd import name_for
from socialscraper.ui.profiles import ProfilesTab


class InventoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'profiles'
        self.db = self.base / 'accounts.sqlite'
        self.android = self.base / 'android'

    def read(self):return inventory(self.root, self.db, self.android)

    def state(self, engine='chromium', identity='one', generation='', status='clean'):
        path = self.root / '_profile_status' / engine / identity / 'tiktok.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(version=1, generation=generation, status=status)))
        return path

    def accounts(self, with_android=True):
        db = sqlite3.connect(self.db)
        try:
            db.execute('CREATE TABLE accounts(identity TEXT, platform TEXT, label TEXT, password TEXT' + (', app_avd TEXT' if with_android else '') + ')')
            columns = ['one', 'tiktok', 'Testkonto', 'secret-never-display']
            if with_android:columns.append('ss_explicit')
            db.execute('INSERT INTO accounts VALUES (' + ','.join('?' for _ in columns) + ')', columns)
            db.commit()
        finally:db.close()

    def test_missing_paths_are_not_created(self):
        before = list(self.base.iterdir())
        rows, warnings = self.read()
        self.assertEqual(rows, []); self.assertTrue(warnings)
        self.assertEqual(list(self.base.iterdir()), before)

    def test_current_failed_retry_archive_and_accounts(self):
        self.accounts(); self.state(generation='a'*16, status='failed')
        current = self.root / '_retries/chromium/one/tiktok' / ('a'*16)
        old = current.parent / ('b'*16)
        current.mkdir(parents=True); old.mkdir()
        (self.android / 'ss_explicit.avd').mkdir(parents=True)
        rows, warnings = self.read()
        self.assertFalse(warnings); self.assertEqual(len(rows), 2)
        active = next(r for r in rows if r.path == str(current))
        self.assertEqual(active.status, 'Fehlgeschlagen')
        self.assertIn('Testkonto', active.accounts); self.assertIn('(zugeordnet)', active.android)
        self.assertNotIn('secret-never-display', repr(rows))

    def test_old_schema_and_derived_device_are_labelled(self):
        self.accounts(with_android=False)
        (self.android / (name_for('one')+'.avd')).mkdir(parents=True)
        rows, warnings = self.read()
        self.assertFalse(warnings); self.assertEqual(len(rows), 1)
        self.assertIn('abgeleitet', rows[0].android)
        self.assertEqual(rows[0].path, '')

    def test_orphan_android_and_missing_browser_are_not_success(self):
        self.state(status='in_use')
        (self.android / 'ss_orphan.avd').mkdir(parents=True)
        rows, _ = self.read()
        self.assertEqual(len(rows), 2)
        self.assertIn('Laufstatus ungeprüft', rows[0].status)
        self.assertIn('Ordner fehlt', rows[0].status)
        self.assertEqual(rows[1].identity, 'Nicht zugeordnet')

    def test_corrupt_state_does_not_hide_other_engine(self):
        self.state().write_text('not-json')
        self.state(engine='firefox')
        rows, warnings = self.read()
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0].browser, 'Firefox')
        self.assertIn('beschädigt', ' '.join(warnings))

    def test_legacy_and_current_paths_are_deduplicated(self):
        self.state()
        (self.root / 'one/tiktok/Default').mkdir(parents=True)
        rows, _ = self.read()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, 'Sauber beendet')

    def test_redirected_paths_are_not_enumerated(self):
        self.state()
        with patch.object(Path, 'is_junction', return_value=True):
            rows, warnings = self.read()
        self.assertEqual(rows, []); self.assertTrue(warnings)

    def test_hidden_ui_refresh_does_not_accumulate_rows(self):
        self.state()
        rows, warnings = self.read()
        window = tk.Tk(); window.withdraw(); self.addCleanup(window.destroy)
        page = ProfilesTab(SimpleNamespace(nb=window, cfg=SimpleNamespace(profiles_dir=self.root, db_path=self.db)))
        with patch('socialscraper.ui.profiles.inventory', return_value=(rows, warnings)):
            page.refresh(); page.refresh()
        keys = page.table.get_children()
        self.assertEqual(len(keys), 1)
        page.table.selection_set(keys[0]); page.selected()
        self.assertIn(str(self.root), page.detail.cget('text'))
