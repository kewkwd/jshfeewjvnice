"""Fenster der Übersicht, Hilfe, Probleme, Lösungsknöpfe, Zwischenablage-Hinweis und Merkzettel.
Echte, verborgene Tk-Fenster (FakeApp) mit Wegwerf-Ordnern; keine echten Daten, keine Netzzugriffe, keine Prozesse."""
import datetime
import gc
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import channels, guicore as core, homelogic, proxylogic, repo, uistate
from socialscraper.models import Comment, Video
from socialscraper.store import Store

try:
    import tkinter
    from tkinter import ttk
    from ui_fake import FakeApp
except Exception:  # pragma: no cover
    FakeApp = None

CLIP = 'https://www.youtube.com/watch?v=abc123XYZ99'


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def texts(widget):
    out = []
    for w in walk(widget):
        try:
            out.append(str(w.cget('text')))
        except tkinter.TclError:
            pass
    return out


def find_button(widget, text):
    for w in walk(widget):
        try:
            if w.winfo_class() in ('TButton', 'Button') and str(w.cget('text')) == text:
                return w
        except tkinter.TclError:
            pass
    return None


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class Base(unittest.TestCase):
    seed_data = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / 'data' / 'scrape.db'
        os.environ.pop('SS_PROXY_MODE', None)
        for p in (patch.dict(os.environ, {'SS_DB_PATH': str(self.db)}), patch.object(core, 'ROOT', self.root)):
            p.start()
            self.addCleanup(p.stop)
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self._close)
        self.app.ui_state = uistate.UiState(self.root / 'data' / 'ui_state.json')
        self.msgs = {}
        for name in ('showinfo', 'showwarning', 'showerror', 'askyesno'):
            p = patch(f'tkinter.messagebox.{name}', **({'return_value': True} if name == 'askyesno' else {}))
            self.msgs[name] = p.start()
            self.addCleanup(p.stop)
        if self.seed_data:
            self.seed()
        self.settings_md5 = self.md5(self.root / 'settings.toml')

    def _close(self):
        for tab in self.app.tabs.values():
            if hasattr(tab, '_loading'):
                self.app.pump(lambda t=tab: not t._loading, 5)
            if hasattr(tab, 'loading'):
                self.app.pump(lambda t=tab: not t.loading, 5)
        self.app.destroy()
        self.app = None
        gc.collect()

    @staticmethod
    def md5(path):
        return hashlib.md5(pathlib.Path(path).read_bytes()).hexdigest()

    def mode(self, mode):
        core.write_settings({'proxy_mode': mode}, self.root)
        self.settings_md5 = self.md5(self.root / 'settings.toml')

    def seed(self):
        store = Store(str(self.db))
        db = store.db
        repo.ensure_default_accounts(db)
        v = Video(platform='youtube', video_id='abcdefghijk', url='https://example.test/v', author='autor', author_id='a1')
        comments = [Comment(platform='youtube', video_id=v.video_id, comment_id=f'k{j}', text='Hallo', author=f's{j}', author_id=f's{j}')
                    for j in range(3)]
        store.save_result(v, comments, repo.iso(repo.now()))
        self.target = repo.add_target(db, 'hashtag', 'tiktok', 'reisen', interval_min=60, max_comments=5)
        db.execute('UPDATE targets SET next_run_at=? WHERE id=?', (repo.iso(repo.now() - datetime.timedelta(minutes=5)), self.target['id']))
        ref = channels.parse_channel_link('https://www.youtube.com/@beispiel')
        self.channel = channels.create_channel(db, ref)
        repo.add_channel_items(db, self.channel['id'], 'youtube', [{'url': f'https://example.test/c/{k}', 'uid': f'youtube:x{k}'} for k in range(10)])
        db.execute("UPDATE channel_items SET status='done' WHERE id<=4")
        db.execute("UPDATE channel_items SET status='failed', last_error='x' WHERE id=5")
        repo.update_channel(db, self.channel['id'], backfill_state='fetching')
        db.commit()
        store.db.close()

    def pump_home(self, home):
        home.refresh()
        self.assertTrue(self.app.pump(lambda: home.snap is not None and not home._loading, 15))


class HomeTabTests(Base):
    def make(self):
        from socialscraper.ui.tab_home import HomeTab
        home = self.app.add('Übersicht', HomeTab)
        self.app.update()
        self.pump_home(home)
        return home

    def test_shows_attention_items_with_solution_buttons(self):
        self.mode('tor')
        home = self.make()
        self.assertTrue(any(t.startswith('Das braucht deine Aufmerksamkeit (') for t in texts(home)))
        button = find_button(home.attn_box, 'Auf „Aus“ stellen')
        self.assertIsNotNone(button)
        button.invoke()
        self.assertIn({'id': 'set_mode', 'label': 'Auf „Aus“ stellen', 'value': 'aus', 'from': 'tor'}, self.app.actions)

    def test_every_attention_row_has_a_button(self):
        self.mode('tor')
        home = self.make()
        for item in home.items:
            self.assertIsNotNone(find_button(home.attn_box, item['action']['label']), item['title'])

    def test_quiet_state_says_so(self):
        import sqlite3
        con = sqlite3.connect(self.db)
        con.execute('DELETE FROM targets')
        con.execute("UPDATE channel_items SET status='done'")
        con.commit()
        con.close()
        home = self.make()
        (self.root / 'data' / 'backups').mkdir(exist_ok=True)
        (self.root / 'data' / 'backups' / 'x.db').write_bytes(b'1')
        from socialscraper import aiconfig
        with patch.object(aiconfig, 'has_key', return_value=True), patch.object(homelogic.al, 'service_status',
                                                                                  return_value={'running': True, 'stale': False, 'current': None}):
            self.pump_home(home)
        self.assertTrue(any('Alles in Ordnung' in t for t in texts(home.attn_box)), texts(home.attn_box))

    def test_quick_buttons_send_their_actions(self):
        home = self.make()
        for label, action in (('Video-Link abrufen', 'goto_fetch'), ('Kanal hinzufügen', 'add_channel'),
                              ('Stimmung bestimmen', 'mood'), ('Daten ansehen', 'goto_data')):
            button = find_button(home.quick, label)
            self.assertIsNotNone(button, label)
            button.invoke()
            self.assertEqual(self.app.actions[-1], {'id': action})

    def test_cards_show_service_proxy_accounts_data_and_channels(self):
        self.mode('tor')
        home = self.make()
        all_text = ' | '.join(texts(home.cards_box))
        for expected in ('HINTERGRUNDDIENST', 'Aus', 'VERBINDUNG (PROXY-MODUS)', 'Tor', 'KONTEN', 'LETZTER LAUF', 'DATEN',
                         '1 Videos · 3 Kommentare', 'NEU IN DEN LETZTEN 24 STUNDEN', 'KANÄLE IN ARBEIT', '4 von 10'):
            self.assertIn(expected, all_text)
        bars = [w for w in walk(home.cards_box) if isinstance(w, ttk.Progressbar)]
        self.assertEqual(len(bars), 1)
        self.assertEqual(int(float(bars[0]['value'])), 40)

    def test_service_card_button_starts_the_service(self):
        home = self.make()
        find_button(home.cards_box, 'Starten').invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'start_service'})

    def test_checklist_is_shown_and_can_be_hidden(self):
        home = self.make()
        self.assertTrue(any(t.startswith('Erste Schritte –') for t in texts(home.check_box)))
        self.assertEqual(len(home.steps), 5)
        find_button(home.check_box, 'Ausblenden').invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'hide_checklist'})
        self.app.ui_state.update_in('home', checklist_hidden=True)
        self.pump_home(home)
        self.assertFalse(home.check_box.winfo_manager())

    def test_checklist_is_compact_in_a_low_window_and_can_be_expanded(self):
        home = self.make()
        self.assertTrue(home._check_compact())                      # das Prüffenster ist niedrig
        step_rows = [t for t in texts(home.check_box) if t[:2] in ('1.', '2.', '3.', '4.', '5.')]
        self.assertEqual(len(step_rows), 1)                         # nur der nächste offene Schritt
        self.assertTrue(step_rows[0].startswith('2.'))
        find_button(home.check_box, 'Alle Schritte zeigen').invoke()
        step_rows = [t for t in texts(home.check_box) if t[:2] in ('1.', '2.', '3.', '4.', '5.')]
        self.assertEqual(len(step_rows), 5)
        find_button(home.check_box, 'Nur den nächsten zeigen').invoke()
        self.assertEqual(len([t for t in texts(home.check_box) if t[:2] in ('1.', '2.', '3.', '4.', '5.')]), 1)

    def test_checklist_buttons(self):
        home = self.make()
        home._check_expanded = True
        home._draw_checklist(home.state())
        find_button(home.check_box, 'So lassen').invoke()
        self.assertEqual(self.app.actions[-1]['id'], 'proxy_ok')
        find_button(home.check_box, 'Brauche ich nicht').invoke()
        self.assertEqual(self.app.actions[-1]['id'], 'skip_account')

    def test_same_data_does_not_rebuild_the_widgets(self):
        home = self.make()
        before = [id(w) for w in home.attn_box.winfo_children()] + [id(w) for w in home.cards_box.winfo_children()]
        self.pump_home(home)
        after = [id(w) for w in home.attn_box.winfo_children()] + [id(w) for w in home.cards_box.winfo_children()]
        self.assertEqual(before, after)

    def test_slow_work_runs_outside_the_window_thread(self):
        from socialscraper.ui.tab_home import HomeTab
        home = self.app.add('Übersicht', HomeTab)
        main = threading.get_ident()
        seen = []
        real = homelogic.collect

        def spy(*a, **kw):
            seen.append(threading.get_ident())
            return real(*a, **kw)
        with patch.object(homelogic, 'collect', spy), patch('socialscraper.ui.tab_home.system_check', lambda cfg=None: (['x'], True)):
            self.pump_home(home)
        self.assertTrue(seen)
        self.assertNotIn(main, seen)

    def test_refresh_only_while_visible(self):
        home = self.make()
        with patch.object(home, 'refresh') as refresh:
            home._auto()                       # das Fenster ist verborgen
        refresh.assert_not_called()
        self.app.deiconify()
        self.app.update()
        self.app.nb.select(home)
        self.app.update()
        with patch.object(home, 'refresh') as refresh:
            home._auto()
        refresh.assert_called_once()
        self.app.withdraw()

    def test_narrow_window_uses_two_rows_of_quick_buttons_and_one_column(self):
        home = self.make()
        home._on_width(700)
        self.app.update()
        rows = {int(b.grid_info()['row']) for b in home.quick_btns}
        self.assertEqual(rows, {0, 1})
        manager = {w.grid_info().get('column') for w in home.cards_box.winfo_children()}
        self.assertEqual(manager, {0})
        home._on_width(1000)
        self.app.update()
        self.assertEqual({int(b.grid_info()['row']) for b in home.quick_btns}, {0})
        self.assertEqual({int(w.grid_info()['column']) for w in home.cards_box.winfo_children()}, {0, 1})

    def test_empty_database_is_friendly(self):
        self.db.unlink()
        home = self.make()
        all_text = ' | '.join(texts(home))
        self.assertIn('0 Videos · 0 Kommentare', all_text)
        self.assertIn('Kein Kanal in Arbeit.', all_text)
        self.assertIn('noch keiner', all_text)
        self.assertNotIn('Traceback', all_text)

    def test_broken_database_shows_an_error_item_not_a_crash(self):
        self.db.write_bytes(b'kaputt' * 100)
        home = self.make()
        self.assertTrue(any('Datenbank' in t for t in texts(home.attn_box)))

    def test_real_files_are_not_changed(self):
        self.make()
        self.assertEqual(self.md5(self.root / 'settings.toml'), self.settings_md5)


class ActionTests(Base):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.actions import Actions
        self.actions = Actions(self.app)
        self.mode('tor')

    def test_unknown_action_is_ignored(self):
        self.assertFalse(self.actions.run({'id': 'gibt_es_nicht'}))
        self.assertFalse(self.actions.run(None))

    def test_set_mode_asks_and_writes_the_setting(self):
        self.assertTrue(self.actions.run({'id': 'set_mode', 'value': 'aus', 'from': 'tor'}))
        text = self.msgs['askyesno'].call_args[0][1]
        self.assertIn('Tor', text)
        self.assertIn('Aus (meine eigene Verbindung)', text)
        self.assertIn('Konten gesperrt', text)
        self.assertEqual(proxylogic.current_mode(self.root), 'aus')
        self.assertTrue((self.root / 'settings.toml.bak').is_file())
        self.assertTrue(any('Proxy-Modus auf' in s[0] and s[1] == 'ok' for s in self.app.statuses))

    def test_set_mode_declined_changes_nothing(self):
        self.msgs['askyesno'].return_value = False
        self.assertFalse(self.actions.run({'id': 'set_mode', 'value': 'aus', 'from': 'tor'}))
        self.assertEqual(proxylogic.current_mode(self.root), 'tor')
        self.assertEqual(self.md5(self.root / 'settings.toml'), self.settings_md5)

    def test_set_mode_same_mode_does_nothing(self):
        self.assertFalse(self.actions.run({'id': 'set_mode', 'value': 'tor', 'from': 'tor'}))
        self.msgs['askyesno'].assert_not_called()

    def test_set_mode_failure_is_explained(self):
        with patch.object(proxylogic, 'set_mode', side_effect=ValueError('Die Datei ist gesperrt.')):
            self.assertFalse(self.actions.run({'id': 'set_mode', 'value': 'aus', 'from': 'tor'}))
        self.assertIn('Die Datei ist gesperrt.', self.msgs['showwarning'].call_args[0][1])
        self.assertEqual(self.md5(self.root / 'settings.toml'), self.settings_md5)

    def test_set_mode_updates_the_settings_form_without_losing_other_edits(self):
        from socialscraper.ui.tab_settings import SettingsTab
        tab = self.app.add('Einstellungen', SettingsTab)
        self.app.update()
        tab.vars['max_comments'].set('77')           # ungespeicherte Änderung des Nutzers
        self.assertTrue(tab.dirty)
        self.actions.run({'id': 'set_mode', 'value': 'aus', 'from': 'tor'})
        self.assertEqual(tab.vars['proxy_mode'].get(), 'aus')
        self.assertEqual(tab.vars['max_comments'].get(), '77')
        self.assertTrue(tab.dirty)
        keys = __import__('socialscraper.formlogic', fromlist=['x']).changed_keys(tab.collect(), tab.baseline)
        self.assertEqual(keys, ['max_comments'])      # der Modus gilt nicht als ungespeicherte Änderung

    def test_goto_selects_the_tab_and_marks_the_account(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        tab = self.app.add('Konten', AccountsTab)
        self.app.update()
        tab.refresh()
        wanted = list(tab.accounts.values())[1]
        self.assertTrue(self.actions.run({'id': 'goto_account', 'account_id': wanted['id']}))
        self.assertEqual(self.app.selected[-1], 'Konten')
        self.assertEqual(tab.tree.selection(), (str(wanted['id']),))

    def test_goto_unknown_account_does_not_crash(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        self.app.add('Konten', AccountsTab)
        self.assertTrue(self.actions.run({'id': 'goto_account', 'account_id': 99999}))

    def test_goto_channel_selects_it_in_the_channel_list(self):
        from socialscraper.ui.tab_auto import AutoTab
        tab = self.app.add('Automatik', AutoTab)
        self.app.update()
        self.app.pump(lambda: not tab.loading, 10)
        self.assertTrue(self.actions.run({'id': 'goto_channel', 'target_id': self.channel['id']}))
        self.app.pump(lambda: tab.ctree.selection() == (f'c{self.channel["id"]}',), 10)
        self.assertEqual(tab.ctree.selection(), (f'c{self.channel["id"]}',))
        self.assertEqual(self.app.selected[-1], 'Automatik')

    def test_goto_settings_sections_and_fields(self):
        from socialscraper.ui.tab_settings import SettingsTab
        tab = self.app.add('Einstellungen', SettingsTab)
        self.app.update()
        self.actions.run({'id': 'goto_ai'})
        self.assertEqual(tab.current, 'ki')
        self.actions.run({'id': 'goto_proxy'})
        self.assertEqual(tab.current, 'proxys')
        self.actions.run({'id': 'goto_system'})
        self.assertEqual(tab.current, 'system')
        self.actions.run({'id': 'goto', 'tab': 'Einstellungen', 'sub': 'proxys'})
        self.assertEqual(tab.current, 'proxys')

    def test_goto_data_marks_the_step_done(self):
        self.actions.run({'id': 'goto_data'})
        self.assertTrue(self.app.ui_state.get('home', 'viewed_data'))
        self.assertEqual(self.app.selected[-1], 'Gespeicherte Daten')

    def test_checklist_actions_are_remembered(self):
        for action, key in (('skip_account', 'skip_account'), ('proxy_ok', 'proxy_ok'), ('hide_checklist', 'checklist_hidden')):
            self.actions.run({'id': action})
            self.assertTrue(self.app.ui_state.get('home', key), key)
        self.actions.run({'id': 'show_checklist'})
        self.assertFalse(self.app.ui_state.get('home', 'checklist_hidden'))
        self.assertEqual(uistate.UiState(self.app.ui_state.path).get('home', 'proxy_ok'), True)      # steht wirklich in der Datei

    def test_start_service_runs_in_the_background(self):
        from socialscraper import daemon
        main = threading.get_ident()
        seen = []
        with patch.object(daemon, 'start_detached', side_effect=lambda root: seen.append(threading.get_ident())):
            self.assertTrue(self.actions.run({'id': 'start_service'}))
            self.app.pump(lambda: seen, 5)
            self.app.pump(lambda: any('läuft jetzt' in s[0] for s in self.app.statuses), 5)
        self.assertNotIn(main, seen)
        self.assertTrue(any('Der Hintergrunddienst läuft jetzt.' in s[0] for s in self.app.statuses))

    def test_start_service_failure_is_reported(self):
        from socialscraper import daemon
        with patch.object(daemon, 'start_detached', side_effect=RuntimeError('Der Dienst ist nicht angelaufen.')):
            self.actions.run({'id': 'start_service'})
            self.app.pump(lambda: self.msgs['showerror'].called, 5)
        self.assertIn('nicht angelaufen', self.msgs['showerror'].call_args[0][1])

    def test_stop_service_asks_first(self):
        from socialscraper import daemon
        self.msgs['askyesno'].return_value = False
        with patch.object(daemon, 'stop') as stop:
            self.assertFalse(self.actions.run({'id': 'stop_service'}))
        stop.assert_not_called()
        self.msgs['askyesno'].return_value = True
        with patch.object(daemon, 'stop', return_value=True) as stop:
            self.assertTrue(self.actions.run({'id': 'stop_service'}))
            self.app.pump(lambda: stop.called, 5)
        stop.assert_called_once()

    def test_backup_writes_a_copy_and_remembers_the_time(self):
        self.assertTrue(self.actions.run({'id': 'backup'}))
        folder = self.root / 'data' / 'backups'
        self.app.pump(lambda: folder.is_dir() and any(p.suffix == '.db' for p in folder.iterdir()) and self.app.ui_state.get('last_backup_at'), 10)
        files = [p for p in folder.iterdir() if p.suffix == '.db']
        self.assertEqual(len(files), 1)
        self.assertTrue(self.app.ui_state.get('last_backup_at'))
        import sqlite3
        con = sqlite3.connect(files[0])
        self.assertEqual(con.execute('SELECT COUNT(*) FROM videos').fetchone()[0], 1)
        con.close()

    def test_backup_without_database_is_a_friendly_error(self):
        self.db.unlink()
        self.actions.run({'id': 'backup'})
        self.app.pump(lambda: self.msgs['showerror'].called, 5)
        self.assertIn('noch keine Datenbank', self.msgs['showerror'].call_args[0][1])
        self.assertIsNone(self.app.ui_state.get('last_backup_at'))

    def test_requeue_and_resume_channel_change_the_database(self):
        import sqlite3
        self.assertTrue(self.actions.run({'id': 'requeue_channel', 'target_id': self.channel['id']}))
        self.app.pump(lambda: any('noch einmal' in s[0] or 'fehlgeschlagene' in s[0] for s in self.app.statuses), 5)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM channel_items WHERE status='failed'").fetchone()[0], 0)
        con.close()
        self.assertTrue(self.actions.run({'id': 'resume_channel', 'target_id': 99999}))
        self.app.pump(lambda: any('gibt es nicht' in s[0] for s in self.app.statuses), 5)

    def test_login_action_opens_the_login_of_the_account(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        self.mode('aus')
        tab = self.app.add('Konten', AccountsTab)
        self.app.update()
        tab.refresh()
        wanted = next(a for a in tab.accounts.values() if a['platform'] == 'tiktok')
        with patch.object(tab, 'login') as login:
            self.actions.run({'id': 'login', 'account_id': wanted['id']})
        login.assert_called_once_with('qr')
        self.assertEqual(tab.current()['id'], wanted['id'])

    def test_problems_action_without_window_support_is_harmless(self):
        self.assertTrue(self.actions.run({'id': 'open_problems'}))


class BannerTests(Base):
    def test_banner_has_a_fix_button_in_every_blocking_tab(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        from socialscraper.ui.tab_auto import AutoTab
        from socialscraper.ui.tab_fetch import FetchTab
        self.mode('tor')
        for title, cls in (('Abrufen', FetchTab), ('Konten', AccountsTab), ('Automatik', AutoTab)):
            tab = self.app.add(title, cls)
            tab.refresh()
            self.app.update()
            banner = tab.mode_banner
            self.assertTrue(banner.winfo_manager(), title)
            self.assertIsNotNone(banner.fix_btn, title)
            self.assertIn('Anmelden, Instagram, X und TikTok-Kommentare gehen so nicht.', banner.label['text'], title)
            banner.fix_btn.invoke()
            self.assertEqual(self.app.actions[-1], {'id': 'set_mode', 'value': 'aus', 'from': 'tor'}, title)

    def test_banner_is_hidden_in_normal_mode(self):
        from socialscraper.ui.tab_fetch import FetchTab
        self.mode('aus')
        tab = self.app.add('Abrufen', FetchTab)
        tab.refresh()
        self.assertFalse(tab.mode_banner.winfo_manager())

    def test_fetch_refusal_offers_the_fix_button(self):
        from socialscraper.ui.tab_fetch import FetchTab
        self.mode('tor')
        tab = self.app.add('Abrufen', FetchTab)
        tab.refresh()
        self.app.update()
        tab.links.insert('1.0', 'https://www.instagram.com/p/ABCDEFGHIJ/')
        tab.start_fetch()
        button = find_button(tab.result_actions, 'Auf „Aus“ stellen')
        self.assertIsNotNone(button)
        self.assertEqual(self.app.steps, [])
        self.assertIn('Klicke unten auf „Auf „Aus“ stellen“', self.msgs['showwarning'].call_args[0][1])
        button.invoke()
        self.assertEqual(self.app.actions[-1]['id'], 'set_mode')

    def test_fetch_busy_service_offers_stop_button(self):
        from socialscraper.ui import logic
        from socialscraper.ui.tab_fetch import FetchTab
        self.mode('aus')
        tab = self.app.add('Abrufen', FetchTab)
        tab.refresh()
        tab.links.insert('1.0', 'https://www.reddit.com/r/a/comments/abcdef/')
        with patch.object(logic, 'describe_busy', return_value='Der Hintergrunddienst arbeitet gerade.'):
            tab.start_fetch()
        self.assertIsNotNone(find_button(tab.result_actions, 'Dienst stoppen'))
        self.assertIsNotNone(find_button(tab.result_actions, 'Zur Automatik'))
        find_button(tab.result_actions, 'Dienst stoppen').invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'stop_service'})

    def test_fix_buttons_vanish_when_a_new_fetch_starts(self):
        from socialscraper.ui.tab_fetch import FetchTab
        self.mode('aus')
        tab = self.app.add('Abrufen', FetchTab)
        tab.refresh()
        tab._show_fixes([('Test', {'id': 'x'})])
        self.assertIsNotNone(find_button(tab.result_actions, 'Test'))
        tab.links.insert('1.0', CLIP)
        tab.start_fetch()
        self.assertIsNone(find_button(tab.result_actions, 'Test'))

    def test_accounts_double_click_runs_the_next_step(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        self.mode('aus')
        tab = self.app.add('Konten', AccountsTab)
        tab.refresh()
        self.app.update()
        self.app.deiconify()
        self.app.update()
        self.app.nb.select(tab)
        self.app.update()
        iid = next(iter(tab.accounts))
        self.app.update()
        x, y, w, h = tab.tree.bbox(iid, '#2')
        from types import SimpleNamespace
        with patch.object(tab, 'primary') as primary:
            tab._double_click(SimpleNamespace(x=x + 3, y=y + 3))
        primary.assert_called_once()
        self.assertEqual(tab.tree.selection(), (iid,))
        with patch.object(tab, 'primary') as primary:                      # auf dem Häkchen schaltet ein Klick um, kein nächster Schritt
            by = y + 3
            bx = next(px for px in range(0, tab.tree.winfo_width()) if tab.tree.identify_column(px) == tab.rot_column)
            event = SimpleNamespace(x=bx + 3, y=by)
            self.assertEqual(tab.tree.identify_column(event.x), tab.rot_column)
            tab._double_click(event)
        primary.assert_not_called()
        self.app.withdraw()

    def test_accounts_busy_service_offers_stop_button(self):
        from socialscraper.ui import logic
        from socialscraper.ui.tab_accounts import AccountsTab
        self.mode('aus')
        tab = self.app.add('Konten', AccountsTab)
        tab.refresh()
        with patch.object(logic, 'describe_busy', return_value='Der Hintergrunddienst arbeitet gerade.'):
            self.assertTrue(tab._busy_note())
        self.assertTrue(tab.fix_row.winfo_manager())
        find_button(tab.fix_row, 'Dienst stoppen').invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'stop_service'})
        find_button(tab.fix_row, 'Zur Automatik').invoke()
        self.assertEqual(self.app.actions[-1]['tab'], 'Automatik')
        with patch.object(logic, 'describe_busy', return_value=None):
            self.assertFalse(tab._busy_note())
        self.assertFalse(tab.fix_row.winfo_manager())

    def test_auto_tab_offers_the_key_button_only_when_the_key_is_missing(self):
        from socialscraper import aiconfig
        from socialscraper.ui.tab_auto import AutoTab
        tab = self.app.add('Automatik', AutoTab)
        self.app.update()
        self.assertFalse(tab.ai_fix_btn.winfo_manager())
        aiconfig.update(self.root, auto_after_runs=True)
        with patch.object(aiconfig, 'has_key', return_value=False):
            tab._load_ai_switch()
        self.assertTrue(tab.ai_fix_btn.winfo_manager())
        tab.ai_fix_btn.invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'goto_ai'})
        with patch.object(aiconfig, 'has_key', return_value=True):
            tab._load_ai_switch()
        self.assertFalse(tab.ai_fix_btn.winfo_manager())

    def test_dialog_without_input_field_focuses_the_ok_button(self):
        from socialscraper.ui.widgets import FormDialog
        dialog = FormDialog(self.app, 'Test', [{'key': 'r', 'kind': 'radio', 'value': 'a', 'choices': [('a', 'A'), ('b', 'B')]}])
        seen = {}

        def look():
            seen['focus'] = dialog.focus_get()
            dialog.destroy()
        self.app.after(300, look)
        dialog.run()
        self.assertIs(seen['focus'], dialog.ok_btn)

    def test_accounts_info_counts_logins_not_confirmations(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        tab = self.app.add('Konten', AccountsTab)
        tab.refresh()
        self.assertIn('0 angemeldet', tab.info['text'])


class SmallWindowTests(Base):
    def test_fetch_tab_scrolls_so_the_result_stays_reachable(self):
        from socialscraper.ui.tab_fetch import FetchTab
        tab = self.app.add('Abrufen', FetchTab)
        self.app.update()
        self.assertTrue(tab.scroll.winfo_exists())
        self.assertEqual(str(tab.result.master), str(tab.scroll.body))
        self.assertEqual(str(tab.result_actions.master), str(tab.scroll.body))
        self.assertEqual(str(tab.links.master.master), str(tab.modes))

    def test_accounts_overview_is_dropped_in_a_low_window(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        tab = self.app.add('Konten', AccountsTab)
        self.app.update()
        with patch.object(tab, 'winfo_height', return_value=440):
            tab._fit_height()
        self.assertFalse(tab.overview.winfo_manager())
        with patch.object(tab, 'winfo_height', return_value=700):
            tab._fit_height()
        self.assertTrue(tab.overview.winfo_manager())

    def test_data_tab_trades_the_mood_bar_for_rows_in_a_low_window(self):
        from socialscraper.ui.tab_data import DataTab
        tab = self.app.add('Gespeicherte Daten', DataTab)
        self.app.update()
        with patch.object(tab, 'winfo_height', return_value=500), patch.object(tab._panes, 'winfo_height', return_value=300):
            tab._keep_sash()
        self.assertFalse(tab.mood_bar.winfo_manager())
        with patch.object(tab, 'winfo_height', return_value=800), patch.object(tab._panes, 'winfo_height', return_value=500):
            tab._keep_sash()
        self.assertTrue(tab.mood_bar.winfo_manager())
        tab._sash_user = True
        with patch.object(tab, 'winfo_height', return_value=500), patch.object(tab._panes, 'winfo_height', return_value=300),                 patch.object(tab._panes, 'sashpos') as sash:
            tab._keep_sash()
        sash.assert_not_called()                      # zieht Marc den Trenner selbst, bleibt seine Wahl

    def test_service_panel_compact_mode(self):
        from socialscraper.ui.forms import ServicePanel
        panel = ServicePanel(self.app, lambda: self.root)
        panel.pack()
        self.app.update()
        self.assertTrue(panel.detail.winfo_manager())
        panel.compact(True)
        self.assertFalse(panel.detail.winfo_manager())
        panel.compact(True)
        panel.compact(False)
        self.assertTrue(panel.detail.winfo_manager())
        panel.destroy()

    def test_automatik_tab_compacts_the_service_panel_in_a_low_window_without_looping(self):
        from socialscraper.ui.tab_auto import AutoTab
        tab = self.app.add('Automatik', AutoTab)
        self.app.update()
        with patch.object(tab, 'winfo_height', return_value=500):
            tab._fit_sash()
            tab._fit_sash()                                       # zweimal: ändert nichts mehr (kein Aufschaukeln)
        self.assertFalse(tab.service.detail.winfo_manager())
        self.assertFalse(tab.explain.winfo_manager())
        with patch.object(tab, 'winfo_height', return_value=700):
            tab._fit_sash()
        self.assertTrue(tab.service.detail.winfo_manager())
        self.assertTrue(tab.explain.winfo_manager())

    def test_ai_note_fits_the_narrow_window(self):
        from socialscraper.ui.tab_auto import AutoTab
        tab = self.app.add('Automatik', AutoTab)
        self.app.update()
        self.assertLessEqual(len(tab.ai_auto_note['text']), 40)


class FetchComfortTests(Base):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_fetch import FetchTab
        self.mode('aus')
        self.tab = self.app.add('Abrufen', FetchTab)
        self.tab.refresh()
        self.app.update()

    def set_clip(self, text):
        self.app.clipboard_clear()
        self.app.clipboard_append(text)
        self.app.update()

    def visible(self):
        return bool(self.tab.clip_bar.winfo_manager())

    def test_hint_for_a_link_in_the_clipboard(self):
        self.set_clip(CLIP)
        self.tab.check_clipboard()
        self.assertTrue(self.visible())
        self.assertIn('YouTube-Link', self.tab.clip_text['text'])
        self.assertIn('Abrufen?', self.tab.clip_text['text'])

    def test_no_hint_for_other_text(self):
        for text in ('hallo', 'schau https://youtu.be/abc123XYZ99 an', ''):
            self.set_clip(text) if text else self.app.clipboard_clear()
            self.tab.check_clipboard()
            self.assertFalse(self.visible(), text)

    def test_dismissed_hint_does_not_come_back_for_the_same_content(self):
        self.set_clip(CLIP)
        self.tab.check_clipboard()
        self.tab.clip_no.invoke()
        self.assertFalse(self.visible())
        self.tab.check_clipboard()
        self.assertFalse(self.visible())
        self.set_clip('https://youtu.be/zzzzzzzzzz1')
        self.tab.check_clipboard()
        self.assertTrue(self.visible())

    def test_no_hint_when_the_link_is_already_in_the_box(self):
        self.tab.links.insert('1.0', CLIP)
        self.set_clip(CLIP)
        self.tab.check_clipboard()
        self.assertFalse(self.visible())

    def test_hint_never_stores_the_clipboard_text(self):
        self.set_clip(CLIP)
        self.tab.check_clipboard()
        self.assertEqual(set(self.tab._clip), {'kind', 'links', 'platforms', 'text', 'fp'})
        self.tab.clip_paste.invoke()
        self.assertIsNone(self.tab._clip)
        self.assertNotIn(CLIP, json.dumps(self.app.ui_state.data))
        self.assertFalse((self.root / 'data' / 'ui_state.json').exists() and CLIP in (self.root / 'data' / 'ui_state.json').read_text(encoding='utf-8'))

    def test_only_paste(self):
        self.set_clip(CLIP)
        self.tab.check_clipboard()
        self.tab.clip_paste.invoke()
        self.assertEqual(self.tab.links.get('1.0', 'end-1c'), CLIP)
        self.assertEqual(self.app.steps, [])
        self.assertFalse(self.visible())

    def test_paste_and_fetch_starts_the_job(self):
        self.set_clip(CLIP)
        self.tab.check_clipboard()
        self.tab.clip_go.invoke()
        self.assertEqual(len(self.app.steps), 1)
        self.assertIn(CLIP, self.app.steps[0][0][0][1])

    def test_channel_link_offers_the_channel_dialog(self):
        self.set_clip('https://www.youtube.com/@beispielkanal')
        self.tab.check_clipboard()
        self.assertIn('Kanal-Link', self.tab.clip_text['text'])
        self.assertEqual(self.tab.clip_go['text'], 'Kanal hinzufügen …')
        with patch.object(self.tab, 'add_channel') as add:
            self.tab.clip_go.invoke()
        add.assert_called_once()
        self.assertEqual(self.tab.channel_link.get(), 'https://www.youtube.com/@beispielkanal')

    def test_paste_puts_each_link_on_its_own_line_without_duplicates(self):
        self.tab.links.insert('1.0', CLIP)
        self.set_clip(f'{CLIP} und https://youtu.be/zzzzzzzzzz1, danke')
        self.assertEqual(self.tab._paste_event(), 'break')
        self.assertEqual(self.tab.links.get('1.0', 'end-1c').splitlines(), [CLIP, 'https://youtu.be/zzzzzzzzzz1'])

    def test_paste_replaces_a_selection_and_keeps_plain_text(self):
        self.tab.links.insert('1.0', 'alt')
        self.tab.links.tag_add('sel', '1.0', 'end-1c')
        self.set_clip('einfach Text')
        self.tab._paste_event()
        self.assertEqual(self.tab.links.get('1.0', 'end-1c'), 'einfach Text')

    def test_paste_button_with_empty_clipboard_says_so(self):
        self.app.clipboard_clear()
        self.tab._paste_links()
        self.msgs['showinfo'].assert_called()

    def test_enter_starts_only_with_valid_links(self):
        with patch.object(self.tab, 'start_fetch') as start:
            self.tab.links.insert('1.0', 'quatsch')
            self.assertIsNone(self.tab._return_pressed())
            start.assert_not_called()
            self.tab.links.delete('1.0', 'end')
            self.tab.links.insert('1.0', CLIP)
            self.assertEqual(self.tab._return_pressed(), 'break')
            start.assert_called_once()
            start.reset_mock()
            self.tab.links.delete('1.0', 'end')
            self.assertIsNone(self.tab._return_pressed())          # leeres Feld: neue Zeile
            start.assert_not_called()

    def test_shift_enter_makes_a_new_line(self):
        self.tab.links.insert('1.0', CLIP)
        self.tab.links.mark_set('insert', 'end-1c')
        self.assertIn('<Shift-Key-Return>', self.tab.links.bind())
        self.assertIn('<Key-Return>', self.tab.links.bind())

    def test_last_account_and_group_are_remembered_but_not_the_comment_count(self):
        with Store(str(self.db)) as store:
            repo.add_account(store.db, 'tiktok', 'Zweitkonto')
            repo.create_group(store.db, 'Reise')
        self.tab.refresh()
        self.tab.acc_vars['tiktok'].set('Zweitkonto')
        self.tab._remember()
        self.tab.group_var.set('Reise')
        self.tab._group_chosen()
        self.tab.max_comments.set(777)
        saved = self.app.ui_state.data['fetch']
        self.assertEqual(saved['accounts']['tiktok'], 'Zweitkonto')
        self.assertEqual(saved['group'], 'Reise')
        self.assertNotIn('777', json.dumps(self.app.ui_state.data))
        self.assertEqual(self.md5(self.root / 'settings.toml'), self.settings_md5)      # nichts in settings.toml
        from socialscraper.ui.tab_fetch import FetchTab
        again = self.app.add('Abrufen2', FetchTab)
        again.refresh()
        self.assertEqual(again.acc_vars['tiktok'].get(), 'Zweitkonto')
        self.assertEqual(again.group_var.get(), 'Reise')
        self.assertNotEqual(again.max_comments.get(), 777)

    def test_remembered_choice_that_no_longer_exists_falls_back(self):
        self.app.ui_state.update_in('fetch', accounts={'tiktok': 'Gelöscht'}, group='Weg')
        from socialscraper.ui.tab_fetch import FetchTab
        again = self.app.add('Abrufen2', FetchTab)
        again.refresh()
        self.assertEqual(again.acc_vars['tiktok'].get(), 'Standardkonto')
        self.assertEqual(again.group_var.get(), '(keine Gruppe)')

    def test_focus_item_puts_the_cursor_in_the_link_box(self):
        self.assertTrue(self.tab.focus_item({'focus': 'links'}))
        self.assertFalse(self.tab.focus_item({'focus': 'nix'}))


class HelpAndProblemsTests(Base):
    def test_help_tab_has_all_sections_and_questions(self):
        from socialscraper import helptexts
        from socialscraper.ui.tab_help import HelpTab
        tab = self.app.add('Hilfe', HelpTab)
        self.app.update()
        all_text = ' | '.join(texts(tab))
        for title, _t in helptexts.FIRST_STEPS:
            self.assertIn(title, all_text)
        for question, _a, _x in helptexts.PROBLEMS:
            self.assertIn(question, all_text)
        for name, _t in helptexts.GLOSSARY.values():
            self.assertIn(name, all_text)
        for keys, _w in helptexts.SHORTCUTS:
            self.assertIn(keys, all_text)
        for key in ('steps', 'problems', 'terms', 'keys'):
            self.assertTrue(tab.show_section(key))
        self.assertFalse(tab.show_section('nix'))

    def test_help_buttons_send_actions(self):
        from socialscraper.ui.tab_help import HelpTab
        tab = self.app.add('Hilfe', HelpTab)
        find_button(tab, 'Zu den Konten').invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'goto', 'tab': 'Konten', 'sub': None})
        find_button(tab, 'Probleme ansehen').invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'open_problems'})
        find_button(tab, 'Zu den Proxy-Einstellungen').invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'goto', 'tab': 'Einstellungen', 'sub': 'proxys'})
        with patch.object(self.app,'navigate',create=True) as navigate:
            find_button(tab, 'Geführten Start öffnen').invoke()
        navigate.assert_called_once_with('Übersicht')

    def test_help_dots_show_the_explanation(self):
        from socialscraper.ui.widgets import HelpDot
        dot = HelpDot(self.app, 'rotation')
        self.assertIn('abwechselnd', dot.tip.text)
        dot._show()
        self.msgs['showinfo'].assert_called_once()
        self.assertEqual(self.msgs['showinfo'].call_args[0][0], 'Rotation')
        self.assertEqual(str(dot.cget('takefocus')), '1')
        dot.destroy()

    def test_help_dots_exist_at_the_hard_words(self):
        from socialscraper.ui.channel_dialog import ChannelDialog
        from socialscraper.ui.tab_accounts import AccountsTab
        from socialscraper.ui.tab_data import DataTab
        from socialscraper.ui.tab_fetch import FetchTab
        from socialscraper.ui.tab_settings import SettingsTab
        from socialscraper.ui.widgets import HelpDot
        for title, cls in (('Abrufen', FetchTab), ('Konten', AccountsTab), ('Gespeicherte Daten', DataTab), ('Einstellungen', SettingsTab)):
            tab = self.app.add(title, cls)
            dots = [w for w in walk(tab) if isinstance(w, HelpDot)]
            self.assertGreaterEqual(len(dots), 2, title)
        names = {w.title_text for t in self.app.tabs.values() for w in walk(t) if isinstance(w, HelpDot)}
        for word in ('Rotation', 'Abkühlung', 'Proxy-Modus', 'Verlauf verfolgen', 'KI-Einschätzung', 'Gruppen',
                     'Ersteller und Kommentarschreiber'):
            self.assertIn(word, names)

    def test_problems_window_lists_items_and_goes_there(self):
        from socialscraper.ui.problems_window import ProblemsWindow
        self.app.deiconify()
        items = [{'time': repo.now(), 'kind': 'run', 'what': 'Profil u/abc (Reddit) – fehlgeschlagen', 'cause': 'Die Plattform sperrt diese Internetverbindung.',
                  'todo': 'Versuche ein anderes Netz.', 'goto': ('Einstellungen', 'proxys'), 'key': 'run:1'},
                 {'time': repo.now(), 'kind': 'service', 'what': 'Hintergrunddienst', 'cause': 'x', 'todo': 'y', 'goto': None, 'key': 'log:1'}]
        window = ProblemsWindow(self.app, items)
        self.app.update()
        all_text = ' | '.join(texts(window))
        self.assertIn('Profil u/abc (Reddit) – fehlgeschlagen', all_text)
        self.assertIn('Was tun: Versuche ein anderes Netz.', all_text)
        self.assertEqual(sum(1 for w in walk(window) if w.winfo_class() == 'TButton' and str(w.cget('text')) == 'Dorthin'), 1)
        find_button(window, 'Dorthin').invoke()
        self.assertEqual(self.app.actions[-1], {'id': 'goto', 'tab': 'Einstellungen', 'sub': 'proxys'})
        self.assertTrue(self.app.ui_state.get('problems_seen_at'))
        window.event_generate('<Escape>')
        self.app.update()
        window.close() if window.winfo_exists() else None
        self.app.withdraw()

    def test_problems_window_empty_state(self):
        from socialscraper.ui.problems_window import ProblemsWindow
        window = ProblemsWindow(self.app, [])
        self.app.update()
        self.assertIn('✔ Keine Probleme.', texts(window))
        self.assertIn('nichts Auffälliges', window.info['text'])
        window.close()

    def test_problems_window_loads_in_the_background(self):
        from socialscraper import problemlogic
        from socialscraper.ui.problems_window import ProblemsWindow
        main = threading.get_ident()
        seen = []
        real = problemlogic.collect

        def spy(*a, **kw):
            seen.append(threading.get_ident())
            return real(*a, **kw)
        with patch.object(problemlogic, 'collect', spy):
            window = ProblemsWindow(self.app)
            self.app.pump(lambda: seen and 'Wird geladen' not in window.info['text'], 10)
        self.assertNotIn(main, seen)
        window.close()

    def test_problems_window_read_error_is_friendly(self):
        from socialscraper.ui.problems_window import ProblemsWindow
        window = ProblemsWindow(self.app, [])
        window.show([], RuntimeError('intern'))
        self.assertIn('ließ sich nicht lesen', window.info['text'])
        self.assertNotIn('intern', window.info['text'])
        window.close()


if __name__ == '__main__':
    unittest.main()
