"""Tests für den Tab „Automatik“: Logik (ohne Tk) und Fenstertests in einem Wegwerf-Ordner."""
import contextlib
import gc
import datetime
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import autologic as al
from socialscraper import repo
from socialscraper.models import Comment, Video
from socialscraper.store import Store

UTC = datetime.timezone.utc


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'data').mkdir()
        self.db = self.root / 'data' / 'scrape.db'
        env = patch.dict(os.environ, {'SS_DB_PATH': str(self.db)})
        env.start()
        self.addCleanup(env.stop)

    def seed(self):
        with Store(self.db) as s:
            repo.ensure_default_accounts(s.db)
            g = repo.create_group(s.db, 'Tanz')
            t1 = repo.add_target(s.db, 'hashtag', 'tiktok', 'tanzen', group_id=g['id'], interval_min=360)
            t2 = repo.add_target(s.db, 'video', 'youtube', 'https://youtu.be/abcdefghijk', interval_min=60, enabled=False)
            rid = repo.start_run(s.db, t1['id'], 'x', 'daemon')
            repo.finish_run(s.db, rid, 'failed', error='Anmeldung abgelaufen')
            s.save_result(Video('tiktok', 'v1', 'https://www.tiktok.com/@a/video/v1', author='anna'),
                          [Comment('tiktok', 'v1', 'c1', 'Hallo Welt', author='bob')], repo.iso())
        return t1, t2


class LogicTests(Base):
    def test_resolve_db_prefers_env_then_settings_then_default(self):
        self.assertEqual(al.resolve_db(self.root), self.db)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SS_DB_PATH')
            self.assertEqual(al.resolve_db(self.root), self.root / 'data' / 'scrape.db')
            (self.root / 'settings.toml').write_text('db_path = "x/y.db"\n', encoding='utf-8')
            self.assertEqual(al.resolve_db(self.root), self.root / 'x' / 'y.db')
            (self.root / 'settings.toml').write_text('kaputt [[', encoding='utf-8')
            self.assertEqual(al.resolve_db(self.root), self.root / 'data' / 'scrape.db')

    def test_time_texts(self):
        now = datetime.datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        self.assertEqual(al.next_text({'enabled': 0}), 'pausiert')
        self.assertEqual(al.next_text({'enabled': 1, 'next_run_at': None}), 'sobald möglich')
        self.assertEqual(al.next_text({'enabled': 1, 'next_run_at': '2026-10-11T11:00:00Z'}, now), 'fällig')
        self.assertEqual(al.next_text({'enabled': 1, 'next_run_at': '2026-10-11T12:12:00Z'}, now), 'in 12 Min.')
        self.assertEqual(al.next_text({'enabled': 1, 'next_run_at': '2026-10-11T14:30:00Z'}, now), 'in 2 Std. 30 Min.')
        self.assertEqual(al.next_text({'enabled': 1, 'next_run_at': '2026-10-13T12:00:00Z'}, now), 'in 2 Tage')
        self.assertEqual(al.local_text(None), '–')
        self.assertEqual(al.local_text('quatsch'), '–')
        self.assertNotEqual(al.local_text('2026-10-11 09:00:00'), '–')   # älteres Format aus CURRENT_TIMESTAMP
        self.assertEqual(al.duration_text(30), 'weniger als 1 Min.')

    def test_interval_labels(self):
        self.assertEqual(al.interval_label(15), '15 Minuten')
        self.assertEqual(al.interval_label(10080), '1 Woche')
        self.assertEqual(al.interval_label(90), '90 Minuten')
        self.assertEqual(al.interval_label(300), '5 Stunden')
        self.assertEqual(al.interval_label(4320), '3 Tage')

    def test_target_title(self):
        self.assertEqual(al.target_title({'kind': 'hashtag', 'platform': 'tiktok', 'value': 'tanzen'}), 'Hashtag #tanzen (TikTok)')
        self.assertEqual(al.target_title({'kind': 'profile', 'platform': 'tiktok', 'value': 'x.y'}), 'Profil @x.y (TikTok)')
        self.assertTrue(al.target_title({'kind': 'video', 'platform': 'youtube', 'value': 'https://youtu.be/' + 'a' * 100}).count('…'))

    def test_service_status_never_creates_files(self):
        status = al.service_status(self.root)
        self.assertFalse(status['running'])
        self.assertEqual(list((self.root / 'data').iterdir()), [])

    def test_describe_service_variants(self):
        self.assertEqual(al.describe_service(dict(al.NOT_RUNNING))[0], 'muted')
        self.assertIn('Aus', al.describe_service(dict(al.NOT_RUNNING))[1])
        running = dict(al.NOT_RUNNING, running=True)
        self.assertEqual(al.describe_service(running, [])[2], 'Noch kein aktives Ziel.')
        busy = dict(running, current={'kind': 'hashtag', 'platform': 'tiktok', 'value': 'tanzen'})
        self.assertIn('Hashtag #tanzen', al.describe_service(busy)[2])
        stale = dict(running, stale=True, age_s=300)
        self.assertEqual(al.describe_service(stale)[0], 'warn')
        self.assertIn('5 Min.', al.describe_service(stale)[2])
        self.assertIn('nicht verfügbar', al.describe_service(dict(al.NOT_RUNNING, unavailable=True))[1])
        soon = [{'kind': 'hashtag', 'platform': 'tiktok', 'value': 'x', 'enabled': 1, 'next_run_at': '2026-10-11T12:10:00Z'}]
        now = datetime.datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
        self.assertIn('in 10 Min.', al.describe_service(running, soon, now)[2])

    def test_check_target_accepts_and_cleans(self):
        c = al.check_target('hashtag', 'tiktok', '#Tanzen ', 360, '20', '20')
        self.assertEqual((c['value'], c['interval_min']), ('tanzen', 360))
        c = al.check_target('video', 'tiktok', 'www.tiktok.com/@a/video/1', 60, 0, 1)
        self.assertEqual(c['value'], 'https://www.tiktok.com/@a/video/1')
        self.assertEqual(al.check_target('profile', 'tiktok', '@a.b', 5, 0, 1)['value'], 'a.b')

    def test_check_target_rejects(self):
        bad = [('hashtag', 'instagram', 'x', 60, 1, 1), ('profile', 'youtube', 'x', 60, 1, 1), ('foo', 'tiktok', 'x', 60, 1, 1),
               ('hashtag', 'tiktok', '', 60, 1, 1), ('hashtag', 'tiktok', 'zwei worte', 60, 1, 1),
               ('search', 'youtube', 'x', 60, 1, 1), ('video', 'tiktok', '', 60, 1, 1),
               ('video', 'tiktok', 'https://example.org/x', 60, 1, 1), ('video', 'tiktok', 'https://youtu.be/abc', 60, 1, 1),
               ('hashtag', 'tiktok', 'x', 4, 1, 1), ('hashtag', 'tiktok', 'x', 99999, 1, 1), ('hashtag', 'tiktok', 'x', 'abc', 1, 1),
               ('hashtag', 'tiktok', 'x', 60, 5001, 1), ('hashtag', 'tiktok', 'x', 60, -1, 1), ('hashtag', 'tiktok', 'x', 60, 1, 0),
               ('hashtag', 'tiktok', 'x', 60, 1, 201), ('profile', 'tiktok', 'a b', 60, 1, 1)]
        for args in bad:
            with self.assertRaises(ValueError, msg=str(args)):
                al.check_target(*args)

    def test_load_snapshot_missing_db_is_empty_and_creates_nothing(self):
        snap = al.load_snapshot(self.db)
        self.assertEqual((snap['targets'], snap['runs'], snap['comments'], snap['error']), ([], [], [], None))
        self.assertFalse(self.db.exists())

    def test_load_snapshot_with_data_and_old_database(self):
        self.seed()
        snap = al.load_snapshot(self.db)
        self.assertEqual(len(snap['targets']), 2)
        self.assertEqual(snap['runs'][0]['target_value'], 'tanzen')
        self.assertEqual(snap['comments'][0]['video_author'], 'anna')
        self.assertEqual(snap['groups'][0]['name'], 'Tanz')
        old = self.root / 'old.db'
        sqlite3.connect(old).close()   # leere, alte Datenbank ohne Tabellen
        self.assertEqual(al.load_snapshot(old)['targets'], [])
        junk = self.root / 'junk.db'
        junk.write_bytes(b'kein sqlite' * 100)
        self.assertTrue(al.load_snapshot(junk)['error'])

    def test_feed_range(self):
        self.seed()
        self.assertEqual(len(al.load_snapshot(self.db, feed_hours=1)['comments']), 1)
        with Store(self.db) as s:
            s.db.execute("UPDATE comments SET first_seen_at='2020-01-01T00:00:00Z'"); s.db.commit()
        self.assertEqual(al.load_snapshot(self.db, feed_hours=24 * 7)['comments'], [])

    def test_write_helpers_and_duplicate_and_missing(self):
        clean = al.check_target('hashtag', 'tiktok', 'tanzen', 60, 10, 10)
        t = al.add_target(self.db, clean)
        with self.assertRaises(ValueError) as ctx:
            al.add_target(self.db, clean)
        self.assertIn('gibt es schon', str(ctx.exception))
        al.update_target(self.db, t['id'], enabled=0, interval_min=30)
        self.assertEqual(al.load_snapshot(self.db)['targets'][0]['interval_min'], 30)
        al.request_run_now(self.db, t['id'])
        al.delete_target(self.db, t['id'])
        self.assertEqual(al.load_snapshot(self.db)['targets'], [])
        with self.assertRaises(ValueError):
            al.update_target(self.db, 999, enabled=1)
        with self.assertRaises(ValueError):
            al.request_run_now(self.db, 999)
        with self.assertRaises(ValueError):
            al.update_target(self.db, 1, interval_min=1)

    def test_write_on_locked_database_gives_clear_message(self):
        self.seed()
        locker = sqlite3.connect(self.db, timeout=1)
        locker.execute('BEGIN EXCLUSIVE')
        try:
            clean = al.check_target('hashtag', 'tiktok', 'neu', 60, 10, 10)
            with self.assertRaises(ValueError) as ctx:
                _with_short_timeout(lambda: al.add_target(self.db, clean))
            self.assertIn('belegt', str(ctx.exception))
            self.assertNotIn('Traceback', str(ctx.exception))
        finally:
            locker.rollback(); locker.close()

    def test_target_title_shortens_video_links_and_names_deleted_targets(self):
        title = al.target_title({'kind': 'video', 'platform': 'youtube', 'value': 'https://www.youtube.com/watch?v=abc00000001'})
        self.assertEqual(title, 'Video-Link youtube.com/watch?v=abc00000001 (YouTube)')
        self.assertEqual(al.deleted_title('hashtag youtube altesziel'), 'Gelöschtes Ziel: Hashtag #altesziel (YouTube)')
        self.assertEqual(al.deleted_title('profile tiktok lena.tanzt'), 'Gelöschtes Ziel: Profil @lena.tanzt (TikTok)')
        self.assertEqual(al.deleted_title('Ziel 7'), 'Gelöschtes Ziel: Ziel 7')
        self.assertEqual(al.deleted_title(None), 'Gelöschtes Ziel'); self.assertEqual(al.deleted_title(''), 'Gelöschtes Ziel')

    def test_run_target_message_with_target_name(self):
        line = 'Hashtag #lofi (YouTube): fertig - 1 Video, 1 neu, 1 neuer Kommentar'
        self.assertEqual(al.run_target_message(0, ['x', line]), ('ok', line))
        failed = 'Hashtag #lofi (YouTube): fehlgeschlagen - 0 Videos, 0 neu, 0 neue Kommentare'
        self.assertEqual(al.run_target_message(1, [failed, 'Anmeldung fehlt für Konto „A“.'])[1], 'Anmeldung fehlt für Konto „A“.')

    def test_run_target_message(self):
        self.assertEqual(al.run_target_message(0, ['Ziel 3: fertig - 2 Videos, 1 neu, 4 neue Kommentare'])[0], 'ok')
        self.assertIn('4 neue Kommentare', al.run_target_message(0, ['Ziel 3: fertig - 2 Videos, 1 neu, 4 neue Kommentare'])[1])
        self.assertEqual(al.run_target_message(4, [])[0], 'warn')
        self.assertEqual(al.run_target_message(1, ['Ziel 3: fehlgeschlagen - 0 Videos, 0 neu, 0 neue Kommentare'])[0], 'err')
        self.assertEqual(al.run_target_message(130, [])[1], 'Abgebrochen.')
        self.assertEqual(al.run_target_message(1, [])[0], 'err')


def _with_short_timeout(fn):
    import socialscraper.store as store_mod
    real = sqlite3.connect
    with patch.object(store_mod.sqlite3, 'connect', lambda path, *a, **k: real(path, timeout=0.3)):
        return fn()


# ---------- Fenstertests ----------

try:
    import tkinter
    from ui_fake import FakeApp
    from socialscraper.ui.tab_auto import AutoTab
except Exception:  # pragma: no cover
    FakeApp = None


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class AutoTabTests(Base):
    def setUp(self):
        super().setUp()
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self._close)
        for name in ('showinfo', 'showwarning', 'showerror'):
            p = patch(f'tkinter.messagebox.{name}'); setattr(self, name, p.start()); self.addCleanup(p.stop)
        p = patch('tkinter.messagebox.askyesno', return_value=True); self.ask = p.start(); self.addCleanup(p.stop)
        self.tab = self.app.add('Automatik', AutoTab)
        self.addCleanup(lambda: self.app.pump(lambda: not self.tab.loading))
        self.load()

    def _close(self):
        # Tk-Variablen dürfen nicht in einem Hintergrund-Thread aufgeräumt werden: Fenster hier schließen, Müll im Haupt-Thread sammeln.
        tab = getattr(self, 'tab', None)
        if tab is not None and hasattr(tab, 'loading'):
            self.app.pump(lambda: not tab.loading, 5)
        self.app.destroy()
        self.app = self.tab = None
        gc.collect()

    def load(self):
        self.tab.refresh()
        self.assertTrue(self.app.pump(lambda: not self.tab.loading))

    def rows(self):
        return [self.tab.tree.item(i, 'values') for i in self.tab.tree.get_children()]

    def test_empty_state_shows_hint_and_disables_buttons(self):
        self.assertEqual(self.rows(), [])
        self.assertTrue(self.tab.empty_label.winfo_manager())
        self.assertIn('Hinzufügen', self.tab.empty_label['text'])
        for b in (self.tab.edit_btn, self.tab.toggle_btn, self.tab.run_btn, self.tab.delete_btn):
            self.assertEqual(str(b['state']), 'disabled')
        self.assertTrue(self.tab.runs_empty.winfo_manager())
        self.assertIn('Keine neuen Kommentare', self.tab.feed_empty['text'])
        self.assertFalse(self.db.exists())   # Anzeigen legt keine Datenbank an

    def test_shows_targets_runs_and_feed(self):
        self.seed(); self.load()
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        kinds = {r[0]: r for r in rows}
        self.assertEqual(kinds['Hashtag'][2], '#tanzen'); self.assertEqual(kinds['Hashtag'][3], 'Tanz')
        self.assertEqual(kinds['Hashtag'][7], 'Fehler')
        self.assertEqual(kinds['Video-Link'][8], 'pausiert'); self.assertEqual(kinds['Video-Link'][9], 'nein')
        self.assertFalse(self.tab.empty_label.winfo_manager())
        run = self.tab.runs_tree.item(self.tab.runs_tree.get_children()[0], 'values')
        self.assertEqual(run[1], 'Hashtag #tanzen (TikTok)'); self.assertEqual(run[3], 'Fehler'); self.assertIn('Anmeldung', run[7])
        self.assertIn('failed' if False else 'failed', self.tab.runs_tree.item(self.tab.runs_tree.get_children()[0], 'tags'))
        self.assertEqual(len(self.tab.feed_tree.get_children()), 1)
        self.assertEqual(self.tab.feed_count['text'], '1 neuer Kommentar')

    def test_dialog_adds_target_with_group_and_validation(self):
        self.seed(); self.load()
        dlg = self.tab.add_target(modal=False)
        dlg.kind_var.set('Suchbegriff'); dlg._kind_changed()
        self.assertEqual(dlg.platform_cb['values'], ('TikTok', 'YouTube', 'Reddit', 'X'))
        dlg.platform_var.set('YouTube'); dlg._platform_changed()
        self.assertEqual(str(dlg.account_cb['state']), 'disabled')
        dlg.value_var.set('')
        self.assertFalse(dlg.save()); self.assertIn('✖', dlg.error['text'])
        dlg.value_var.set('lofi hip hop'); dlg.interval_var.set('1 Tag'); dlg.group_var.set('Tanz')
        dlg.comments_var.set('7'); dlg.videos_var.set('9')
        self.assertTrue(dlg.save())
        self.load()
        row = [r for r in self.rows() if r[0] == 'Suchbegriff'][0]
        self.assertEqual((row[1], row[2], row[3], row[5]), ('YouTube', 'lofi hip hop', 'Tanz', '1 Tag'))
        snap = al.load_snapshot(self.db)
        t = [x for x in snap['targets'] if x['kind'] == 'search'][0]
        self.assertEqual((t['max_comments'], t['max_videos'], t['interval_min']), (7, 9, 1440))

    def test_dialog_example_text_matches_the_chosen_platform(self):
        dlg = self.tab.add_target(modal=False)
        dlg.kind_var.set('Video-Link'); dlg._kind_changed()
        for platform, needle in (('YouTube', 'youtube.com/watch'), ('TikTok', 'tiktok.com/@'), ('Instagram', 'instagram.com/reel')):
            dlg.platform_var.set(platform); dlg._platform_changed()
            self.assertIn(needle, dlg.value_hint['text'], platform)
            self.assertNotIn('tiktok' if platform != 'TikTok' else 'youtube', dlg.value_hint['text'], platform)
        dlg.kind_var.set('Hashtag'); dlg._kind_changed()
        self.assertEqual(dlg.value_hint['text'], 'z. B. tanzen')              # andere Arten behalten ihr Beispiel
        dlg.destroy()

    def test_runs_of_deleted_targets_show_a_readable_name(self):
        self.seed()
        with Store(self.db) as store:
            db = store.db
            t = repo.add_target(db, 'hashtag', 'youtube', 'altesziel')
            run = repo.start_run(db, t['id'], 'hashtag youtube altesziel'); repo.finish_run(db, run, 'ok', videos_seen=0)
            repo.delete_target(db, t['id'])
        self.load()
        titles = [self.tab.runs_tree.item(i, 'values')[1] for i in self.tab.runs_tree.get_children()]
        self.assertIn('Gelöschtes Ziel: Hashtag #altesziel (YouTube)', titles)
        self.assertFalse(any(t == 'hashtag youtube altesziel' for t in titles))

    def test_dialog_duplicate_stays_open_with_message(self):
        self.seed(); self.load()
        dlg = self.tab.add_target(modal=False)
        dlg.value_var.set('tanzen')
        self.assertFalse(dlg.save())
        self.assertIn('gibt es schon', dlg.error['text'])
        self.assertTrue(dlg.winfo_exists())
        dlg.destroy()

    def test_dialog_video_link_detects_platform_and_rejects_mismatch(self):
        dlg = self.tab.add_target(modal=False)
        dlg.kind_var.set('Video-Link'); dlg._kind_changed()
        dlg.value_var.set('https://www.instagram.com/reel/abc/')
        self.assertEqual(dlg.platform_var.get(), 'Instagram')
        dlg.value_var.set('https://example.org/x')
        self.assertFalse(dlg.save()); self.assertIn('kein Link', dlg.error['text'])
        dlg.destroy()

    def test_dialog_custom_interval_and_bounds(self):
        dlg = self.tab.add_target(modal=False)
        dlg.value_var.set('x1')
        dlg.interval_var.set('Eigene Zeit …'); dlg._interval_changed()
        dlg.custom_var.set('3')
        self.assertFalse(dlg.save()); self.assertIn('5 Minuten', dlg.error['text'])
        dlg.custom_var.set('45')
        self.assertTrue(dlg.save())
        self.load()
        self.assertEqual(self.rows()[0][5], '45 Minuten')

    def test_dialog_new_group_creates_group(self):
        dlg = self.tab.add_target(modal=False)
        with patch('tkinter.simpledialog.askstring', return_value='Neue Gruppe 1'):
            dlg.group_var.set('Neue Gruppe …'); dlg._group_changed()
        self.assertEqual(dlg.group_var.get(), 'Neue Gruppe 1')
        dlg.value_var.set('x1')
        self.assertTrue(dlg.save())
        snap = al.load_snapshot(self.db)
        self.assertEqual(snap['targets'][0]['group_name'], 'Neue Gruppe 1')

    def test_dialog_new_group_cancel_and_duplicate(self):
        self.seed(); self.load()
        dlg = self.tab.add_target(modal=False)
        with patch('tkinter.simpledialog.askstring', return_value=None):
            dlg.group_var.set('Neue Gruppe …'); dlg._group_changed()
        self.assertEqual(dlg.group_var.get(), '(keine Gruppe)')
        with patch('tkinter.simpledialog.askstring', return_value='Tanz'):
            dlg.group_var.set('Neue Gruppe …'); dlg._group_changed()
        self.showwarning.assert_called()
        dlg.destroy()

    def test_edit_changes_interval_and_keeps_kind_value(self):
        t1, _t2 = self.seed(); self.load()
        self.tab.tree.selection_set(f"t{t1['id']}")
        dlg = self.tab.edit_target(modal=False)
        self.assertEqual(str(dlg.kind_cb['state']), 'disabled'); self.assertEqual(str(dlg.value_entry['state']), 'disabled')
        self.assertEqual(dlg.interval_var.get(), '6 Stunden')
        dlg.interval_var.set('15 Minuten'); dlg.comments_var.set('3')
        self.assertTrue(dlg.save())
        self.load()
        snap = al.load_snapshot(self.db)
        t = [x for x in snap['targets'] if x['id'] == t1['id']][0]
        self.assertEqual((t['interval_min'], t['max_comments'], t['value']), (15, 3, 'tanzen'))

    def test_toggle_enabled(self):
        t1, _t2 = self.seed(); self.load()
        self.tab.tree.selection_set(f"t{t1['id']}")
        self.tab._update_buttons()
        self.assertEqual(self.tab.toggle_btn['text'], 'Pausieren')
        self.tab.toggle_target(); self.load()
        self.assertEqual(self.tab.tree.item(f"t{t1['id']}", 'values')[9], 'nein')
        self.assertEqual(self.tab.toggle_btn['text'], 'Aktivieren')
        self.tab.toggle_target(); self.load()
        self.assertEqual(self.tab.tree.item(f"t{t1['id']}", 'values')[9], 'ja')

    def test_delete_asks_and_removes_but_keeps_videos(self):
        t1, _t2 = self.seed(); self.load()
        self.tab.tree.selection_set(f"t{t1['id']}")
        self.ask.return_value = False
        self.tab.delete_target(); self.load(); self.assertEqual(len(self.rows()), 2)
        self.ask.return_value = True
        self.tab.delete_target(); self.load(); self.assertEqual(len(self.rows()), 1)
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM videos').fetchone()[0], 1)

    def test_run_now_without_service_runs_cli_job(self):
        t1, _t2 = self.seed(); self.load()
        self.tab.tree.selection_set(f"t{t1['id']}")
        self.tab.run_now()
        steps, finish = self.app.steps[-1]
        self.assertEqual(steps[0][1], ['run-target', str(t1['id'])])
        finish([('x', 0, ['Ziel 1: fertig - 3 Videos, 1 neu, 5 neue Kommentare'])], False)
        self.assertIn('5 neue Kommentare', self.tab.result_label['text'])
        finish([('x', 4, [])], False)
        self.assertIn('Browserauftrag', self.tab.result_label['text'])
        finish([], True)
        self.assertEqual(self.tab.result_label['text'], 'Abgebrochen.')

    def test_run_now_with_running_service_sets_due_time(self):
        t1, _t2 = self.seed()
        with Store(self.db) as s:
            repo.finish_run(s.db, repo.start_run(s.db, t1['id']), 'ok')   # nächster Lauf in 6 Stunden
        self.load()
        self.tab.service.last_status = dict(al.NOT_RUNNING, running=True)
        self.tab.tree.selection_set(f"t{t1['id']}")
        self.tab.run_now()
        self.assertEqual(self.app.steps, [])
        with Store(self.db) as s:
            self.assertLessEqual(repo.get_target(s.db, t1['id'])['next_run_at'], repo.iso())

    def test_run_now_of_paused_target_uses_job_even_if_service_runs(self):
        _t1, t2 = self.seed(); self.load()
        self.tab.service.last_status = dict(al.NOT_RUNNING, running=True)
        self.tab.tree.selection_set(f"t{t2['id']}")
        self.tab.run_now()
        self.assertEqual(self.app.steps[-1][0][0][1], ['run-target', str(t2['id'])])

    def test_run_now_registered_as_busy_button(self):
        self.assertIn(self.tab.run_btn, self.app.busy)

    def test_range_filter_reloads_feed(self):
        self.seed(); self.load()
        with Store(self.db) as s:
            s.db.execute("UPDATE comments SET first_seen_at=?", (repo.iso(repo.now() - datetime.timedelta(hours=3)),)); s.db.commit()
        self.tab.range_var.set('Letzte Stunde'); self.tab._range_changed()
        self.assertTrue(self.app.pump(lambda: not self.tab.loading))
        self.assertEqual(len(self.tab.feed_tree.get_children()), 0)
        self.tab.range_var.set('Letzte 24 Stunden'); self.tab._range_changed()
        self.assertTrue(self.app.pump(lambda: not self.tab.loading and len(self.tab.feed_tree.get_children()) == 1))

    def test_feed_double_click_opens_video(self):
        self.seed(); self.load()
        item = self.tab.feed_tree.get_children()[0]
        self.tab.feed_tree.selection_set(item)
        with patch('webbrowser.open') as opened:
            self.tab.open_feed_video()
        opened.assert_called_once_with('https://www.tiktok.com/@a/video/v1')

    def test_feed_never_opens_non_http_links(self):
        self.seed(); self.load()
        item = self.tab.feed_tree.get_children()[0]
        self.tab.feed_urls[item] = 'file:///C:/Windows/System32/calc.exe'
        self.tab.feed_tree.selection_set(item)
        with patch('webbrowser.open') as opened:
            self.tab.open_feed_video()
        opened.assert_not_called()

    def test_feed_never_opens_a_backslash_host_link(self):
        self.seed(); self.load()
        item = self.tab.feed_tree.get_children()[0]
        self.tab.feed_urls[item] = 'https://evil.com\\.tiktok.com/x'
        self.tab.feed_tree.selection_set(item)
        with patch('webbrowser.open') as opened:
            self.tab.open_feed_video()
        opened.assert_not_called()

    def test_broken_database_does_not_crash_view(self):
        self.db.write_bytes(b'kein sqlite' * 100)
        self.load()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.app.statuses[-1][1], 'warn')

    def test_database_locked_during_write_shows_message(self):
        self.seed(); self.load()
        with patch('socialscraper.autologic.update_target', side_effect=ValueError('Die Datenbank ist gerade belegt (ein Abruf läuft?). Bitte gleich noch einmal versuchen.')):
            self.tab.tree.selection_set(self.tab.tree.get_children()[0])
            self.tab.toggle_target()
        self.showwarning.assert_called()
        self.assertIn('belegt', self.showwarning.call_args[0][1])

    def test_unexpected_error_shows_friendly_text_not_traceback(self):
        self.seed(); self.load()
        with patch('socialscraper.autologic.update_target', side_effect=KeyError('geheim')):
            self.tab.tree.selection_set(self.tab.tree.get_children()[0])
            self.tab.toggle_target()
        text = self.showwarning.call_args[0][1]
        self.assertNotIn('KeyError', text); self.assertNotIn('geheim', text)

    def test_auto_refresh_only_while_visible(self):
        calls = []
        self.tab.reload = lambda: calls.append(1)
        self.tab._auto()   # Fenster ist unsichtbar (withdrawn)
        self.assertEqual(calls, [])
        self.app.deiconify(); self.app.nb.select(self.tab); self.app.update()
        self.tab._auto()
        self.assertEqual(calls, [1])

    def test_service_panel_in_tab_shows_next_run(self):
        self.seed(); self.load()
        with patch('socialscraper.autologic.service_status', return_value=dict(al.NOT_RUNNING, running=True)):
            self.tab.service.refresh()
        self.assertIn('Läuft', self.tab.service.state_label['text'])
        self.assertIn('Nächster Lauf', self.tab.service.detail['text'])

    def test_keyboard_delete_and_enter_bindings_exist(self):
        binds = self.tab.tree.bind()
        self.assertIn('<Key-Delete>', binds); self.assertIn('<Key-Return>', binds); self.assertIn('<Double-Button-1>', binds)


if __name__ == '__main__':
    unittest.main()
