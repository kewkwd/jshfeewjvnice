"""Oberfläche: Rotation und fester Proxy je Konto (Konten, Abrufen, Automatik, Einstellungen). Echte, verborgene Tk-Fenster,
Wegwerf-Ordner, keine echten Daten."""
import datetime
import gc
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import autologic as al
from socialscraper import guicore as core
from socialscraper import repo, rotation
from socialscraper.net import accountproxy
from socialscraper.store import Store
from socialscraper.ui import logic

PW = 'Fenster-Geheim-4711'
SECRET_URL = f'http://proxyuser:{PW}@203.0.113.5:8080'

try:
    import tkinter
    from ui_fake import FakeApp
except Exception:  # pragma: no cover
    FakeApp = None


def fake_form(values, record=None):
    def fake(parent, title, fields, intro='', ok_text='OK', validator=None):
        problem = validator(values) if validator else None
        if record is not None:
            record.append(problem)
        return None if problem else values
    return fake


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class WindowBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / 'data' / 'scrape.db'
        for p in (patch.dict(os.environ, {'SS_DB_PATH': str(self.db)}), patch.object(core, 'ROOT', self.root),
                  patch.object(core, 'write_settings')):
            p.start(); self.addCleanup(p.stop)
        with Store(self.db) as store:
            repo.ensure_default_accounts(store.db)
            self.a = repo.default_account(store.db, 'tiktok')
            self.b = repo.add_account(store.db, 'tiktok', 'Zweit')
            self.c = repo.add_account(store.db, 'tiktok', 'Dritt')
        for acc in (self.a, self.b, self.c):
            self.cookie(acc['identity'])
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self._close)
        for name in ('showinfo', 'showwarning', 'showerror'):
            p = patch(f'tkinter.messagebox.{name}'); setattr(self, name, p.start()); self.addCleanup(p.stop)
        p = patch('tkinter.messagebox.askyesno', return_value=True); self.ask = p.start(); self.addCleanup(p.stop)

    def _close(self):
        self.app.destroy()
        self.app = None
        gc.collect()

    def cookie(self, identity, platform='tiktok'):
        d = self.root / 'data' / 'cookies'; d.mkdir(parents=True, exist_ok=True)
        (d / f'{identity}.{platform}.json').write_text(json.dumps([{'name': 'sessionid', 'value': 'x'}]), encoding='utf-8')

    def row(self, account_id):
        with Store(self.db) as store:
            return repo.get_account(store.db, account_id)

    def scan_for_secret(self):
        """Das Proxy-Passwort darf in keiner Anzeige, keinem Protokoll und keiner Statuszeile vorkommen."""
        def widgets(w):
            yield w
            for child in w.winfo_children():
                yield from widgets(child)
        seen = []
        for w in widgets(self.app):
            for option in ('text',):
                try:
                    seen.append(str(w.cget(option)))
                except Exception:
                    pass
            try:
                seen.extend(' '.join(map(str, w.item(i, 'values'))) for i in w.get_children())
            except Exception:
                pass
        seen.extend(line for line, _tag in self.app.logs)
        seen.extend(text for text, _kind in self.app.statuses)
        for call in (self.showinfo, self.showwarning, self.showerror, self.ask):
            seen.extend(str(a) for c in call.call_args_list for a in c.args)
        return [text for text in seen if PW in text]


class AccountsRotationTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_accounts import AccountsTab
        with Store(self.db) as store:
            rotation.mark_failure(store.db, self.b['id'], 'x', 'captcha')
            repo.set_account_daily_limit(store.db, self.c['id'], 3)
            rotation.mark_used(store.db, self.c['id'])
            rotation.set_account_proxy(store.db, self.a['id'], SECRET_URL)
        self.tab = self.app.add('Konten', AccountsTab); self.tab.refresh(); self.app.update()

    def values(self, account):
        return self.tab.tree.item(str(account['id']), 'values')

    def pick(self, account):
        self.tab.tree.selection_set(str(account['id'])); self.app.update()

    def test_columns_show_rotation_proxy_status_and_use_without_password(self):
        a, b, c = self.values(self.a), self.values(self.b), self.values(self.c)
        self.assertEqual((a[5], a[6], a[7], a[8]), ('☑', 'http://proxyuser:*****@203.0.113.5:8080', 'bereit', '0'))
        self.assertEqual((b[5], b[6]), ('☑', 'keiner'))
        self.assertTrue(b[7].startswith('Abkühlung bis ') and b[7].endswith('wegen CAPTCHA'), b[7])
        self.assertEqual((c[7], c[8]), ('bereit', '1'))
        self.assertEqual(self.scan_for_secret(), [])

    def test_header_line_per_platform_summarizes_the_rotation(self):
        text = self.tab.overview_labels['tiktok'].cget('text')
        self.assertTrue(text.startswith('TikTok: 3 Konten · 2 bereit · 1 in Abkühlung bis '), text)
        self.assertEqual(self.tab.overview_labels['reddit'].cget('text'), 'Reddit: 1 Konto · 1 bereit')

    def test_checkbox_saves_at_once_and_updates_overview(self):
        self.pick(self.a)
        self.assertTrue(self.tab.rot_var.get())
        self.tab.rot_var.set(False); self.tab.toggle_rotation()
        self.assertEqual(self.row(self.a['id'])['rotation_enabled'], 0)
        self.assertEqual(self.values(self.a)[5], '☐')
        self.assertIn('1 nicht in Rotation', self.tab.overview_labels['tiktok'].cget('text'))
        self.tab.rot_var.set(True); self.tab.toggle_rotation()
        self.assertEqual(self.row(self.a['id'])['rotation_enabled'], 1)

    def test_click_on_the_rotation_cell_toggles_but_other_cells_do_not(self):
        class Event:
            x, y = 5, 5
        iid = str(self.c['id'])
        with patch.object(self.tab.tree, 'identify_region', return_value='cell'), patch.object(self.tab.tree, 'identify_row', return_value=iid), \
                patch.object(self.tab.tree, 'identify_column', return_value='#2'):
            self.tab._click_cell(Event())
            self.assertEqual(self.row(self.c['id'])['rotation_enabled'], 1)
        with patch.object(self.tab.tree, 'identify_region', return_value='cell'), patch.object(self.tab.tree, 'identify_row', return_value=iid), \
                patch.object(self.tab.tree, 'identify_column', return_value=self.tab.rot_column):
            self.tab._click_cell(Event())
        self.assertEqual(self.row(self.c['id'])['rotation_enabled'], 0)

    def test_detail_shows_state_use_proxy_and_ip_hint(self):
        self.pick(self.c)
        self.assertIn('Heute benutzt: 1 von 3 (Tageslimit)', self.tab.rot_state.cget('text'))
        self.assertIn('Kein fester Proxy', self.tab.proxy_line.cget('text'))
        self.assertIn('keinen eigenen Proxy', self.tab.hint_line.cget('text'))      # Konto a hat einen, c nicht
        self.pick(self.a)
        self.assertEqual(self.tab.proxy_line.cget('text'), 'Fester Proxy: http://proxyuser:*****@203.0.113.5:8080')
        self.pick(self.b)
        self.assertIn('Abkühlung bis', self.tab.rot_state.cget('text'))

    def test_clear_cooldown_button(self):
        self.pick(self.a)
        self.tab.clear_cooldown()
        self.showinfo.assert_called_once()                    # nicht in Abkühlung: nur ein Hinweis
        self.showinfo.reset_mock()
        self.pick(self.b)
        self.tab.clear_cooldown()
        self.assertIsNone(self.row(self.b['id'])['cooldown_until'])
        self.assertEqual(self.values(self.b)[7], 'bereit')
        self.assertIn('wieder dran', self.app.statuses[-1][0])

    def test_daily_limit_dialog_saves_validates_and_clears(self):
        self.pick(self.a)
        rec = []
        with patch('socialscraper.ui.tab_accounts.ask_form', side_effect=fake_form({'limit': 'viele'}, rec)):
            self.tab.set_limit()
        self.assertIn('ganze Zahl', rec[-1])
        self.assertIsNone(self.row(self.a['id'])['daily_limit'])
        with patch('socialscraper.ui.tab_accounts.ask_form', side_effect=fake_form({'limit': '12'}, rec)):
            self.tab.set_limit()
        self.assertEqual(self.row(self.a['id'])['daily_limit'], 12)
        with patch('socialscraper.ui.tab_accounts.ask_form', side_effect=fake_form({'limit': ' '}, rec)):
            self.tab.set_limit()
        self.assertIsNone(self.row(self.a['id'])['daily_limit'])

    def test_set_and_remove_proxy_through_the_dialog_never_shows_the_password(self):
        self.pick(self.b)
        raw = f'http://neuuser:{PW}@198.51.100.7:3128'
        with patch('socialscraper.ui.tab_accounts.ProxyDialog') as dialog:
            dialog.return_value.run.return_value = ('save', raw)
            self.tab.set_proxy()
            self.assertIsNone(dialog.call_args.args[2])                         # noch kein Proxy
        self.assertEqual(self.row(self.b['id'])['proxy'], 'http://neuuser:*****@198.51.100.7:3128')
        self.assertEqual(accountproxy.load(self.db)[self.b['identity']], raw)
        self.assertEqual(self.values(self.b)[6], 'http://neuuser:*****@198.51.100.7:3128')
        self.assertEqual(self.scan_for_secret(), [])
        with patch('socialscraper.ui.tab_accounts.ProxyDialog') as dialog:
            dialog.return_value.run.return_value = ('remove', None)
            self.tab.set_proxy()
            self.assertEqual(dialog.call_args.args[2], 'http://neuuser:*****@198.51.100.7:3128')   # nur maskiert an den Dialog
        self.assertIsNone(self.row(self.b['id'])['proxy'])
        self.assertNotIn(self.b['identity'], accountproxy.load(self.db))
        self.assertEqual(self.scan_for_secret(), [])

    def test_cancel_and_invalid_proxy_change_nothing(self):
        self.pick(self.b)
        with patch('socialscraper.ui.tab_accounts.ProxyDialog') as dialog:
            dialog.return_value.run.return_value = None
            self.tab.set_proxy()
        self.assertIsNone(self.row(self.b['id'])['proxy'])
        with patch('socialscraper.ui.tab_accounts.ProxyDialog') as dialog:
            dialog.return_value.run.return_value = ('save', 'kein proxy')
            self.tab.set_proxy()
        self.showwarning.assert_called()
        self.assertIsNone(self.row(self.b['id'])['proxy'])

    def test_proxy_note_in_free_and_tor_mode(self):
        self.tab.mode_banner.update_mode('tor')
        self.pick(self.a)
        self.assertIn('gilt nicht in diesem Verbindungsmodus', self.tab.proxy_line.cget('text'))
        self.tab.mode_banner.update_mode('aus')

    def test_unreadable_rotation_does_not_break_the_list(self):
        with patch.object(logic, 'load_rotation', side_effect=__import__('sqlite3').OperationalError('database is locked')):
            self.tab.refresh()
        self.assertEqual(len(self.tab.tree.get_children()), 6)
        self.assertTrue(any('Rotationsstatus' in line for line, _t in self.app.logs))

    def test_buttons_follow_the_selection(self):
        for key in ('proxy', 'cooldown', 'limit'):
            self.assertEqual(str(self.tab.btn[key]['state']), 'disabled')
        self.pick(self.a)
        for key in ('proxy', 'cooldown', 'limit'):
            self.assertEqual(str(self.tab.btn[key]['state']), 'normal')


class ProxyDialogTests(WindowBase):
    def dialog(self, current=None, tester=None):
        from socialscraper.ui.proxy_dialog import ProxyDialog
        dlg = ProxyDialog(self.app, 'TikTok „Zweit“', current, tester=tester)
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        return dlg

    def test_entry_is_masked_and_format_is_checked_before_everything(self):
        dlg = self.dialog()
        self.assertEqual(dlg.entry.cget('show'), '•')
        dlg.toggle_show(); self.assertEqual(dlg.entry.cget('show'), '')
        dlg.var.set('')
        dlg.save(); self.assertIn('Bitte eine Proxy-Adresse', dlg.message.cget('text')); self.assertIsNone(dlg.result)
        for bad in ('quatsch quatsch', 'ftp://1.2.3.4:21', 'http://1.2.3.4'):
            dlg.var.set(bad); dlg.save()
            self.assertIsNone(dlg.result, bad)
        self.assertIn('Port', dlg.message.cget('text'))
        self.assertEqual(str(dlg.remove_btn['state']), 'disabled')

    def test_test_button_runs_in_background_and_shows_exit_ip(self):
        calls = []

        def tester(raw):
            calls.append(raw)
            return True, 'Der Proxy funktioniert. Ausgangs-IP: 198.51.100.9 (120 ms).', '198.51.100.9'
        dlg = self.dialog(tester=tester)
        dlg.var.set(SECRET_URL)
        dlg.test()
        self.assertEqual(str(dlg.save_btn['state']), 'disabled')               # während des Tests gesperrt
        self.assertIn('Teste', dlg.message.cget('text'))
        self.assertTrue(self.app.pump(lambda: 'Ausgangs-IP' in dlg.message.cget('text'), 5))
        self.assertIn('198.51.100.9', dlg.message.cget('text'))
        self.assertEqual(str(dlg.save_btn['state']), 'normal')
        self.assertEqual(calls, [SECRET_URL])
        self.assertNotIn(PW, dlg.message.cget('text'))

    def test_failed_test_is_shown_in_red_and_exception_is_caught(self):
        dlg = self.dialog(tester=lambda raw: (False, 'Der Proxy 1.2.3.4:80 ist nicht erreichbar.', None))
        dlg.var.set('http://1.2.3.4:80'); dlg.test()
        self.assertTrue(self.app.pump(lambda: 'nicht erreichbar' in dlg.message.cget('text'), 5))
        self.assertTrue(dlg.message.cget('text').startswith('✗'))

        def boom(raw):
            raise RuntimeError('kaputt')
        dlg2 = self.dialog(tester=boom)
        dlg2.var.set('http://1.2.3.4:80'); dlg2.test()
        self.assertTrue(self.app.pump(lambda: 'hat nicht geklappt' in dlg2.message.cget('text'), 5))

    def test_save_remove_and_saved_proxy_shown_masked_only(self):
        dlg = self.dialog(current='http://u:*****@1.2.3.4:80')
        self.assertIn('http://u:*****@1.2.3.4:80', dlg.current_label.cget('text'))
        self.assertEqual(dlg.var.get(), '')                       # das gespeicherte Passwort steht nie im Feld
        dlg.save(); self.assertIsNone(dlg.result)                 # leer = nichts zu speichern
        dlg.var.set(SECRET_URL); dlg.save()
        self.assertEqual(dlg.result, ('save', SECRET_URL))
        dlg2 = self.dialog(current='http://u:*****@1.2.3.4:80')
        dlg2.remove()
        self.assertEqual(dlg2.result, ('remove', None))

    def test_empty_test_with_saved_proxy_tests_the_saved_one(self):
        seen = []
        dlg = self.dialog(current='http://u:*****@1.2.3.4:80', tester=lambda raw: (seen.append(raw), (True, 'ok', '1.1.1.1'))[1])
        dlg.test()
        self.assertTrue(self.app.pump(lambda: seen != [], 5))
        self.assertEqual(seen, [None])

    def test_logic_test_saved_proxy_uses_the_file(self):
        with Store(self.db) as store:
            rotation.set_account_proxy(store.db, self.b['id'], SECRET_URL)
        with patch.object(rotation, 'test_proxy', return_value=(True, 'ok', '1.1.1.1')) as test:
            self.assertEqual(logic.test_saved_proxy(self.db, self.b['identity']), (True, 'ok', '1.1.1.1'))
        self.assertEqual(test.call_args.args[0], SECRET_URL)
        ok, message, _ip = logic.test_saved_proxy(self.db, self.c['identity'])
        self.assertFalse(ok)
        self.assertIn('kein Proxy gespeichert', message)


class FetchTabRotationTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_data import DataTab
        from socialscraper.ui.tab_fetch import FetchTab
        self.data = self.app.add('Gespeicherte Daten', DataTab)
        self.tab = self.app.add('Abrufen', FetchTab); self.tab.refresh(); self.app.update()
        self.links = ['https://www.tiktok.com/@u/video/1', 'https://www.tiktok.com/@u/video/2']

    def set_links(self, *links):
        self.tab.links.delete('1.0', 'end'); self.tab.links.insert('1.0', '\n'.join(links))

    def opt(self, cmd, name):
        return cmd[cmd.index(name) + 1]

    def test_dropdown_lists_default_accounts_and_rotation(self):
        values = list(self.tab.acc_boxes['tiktok']['values'])
        self.assertEqual(values, ['Standardkonto', 'Hauptkonto  (Standard)', 'Dritt', 'Zweit', '🔄 Automatisch wechseln (Rotation)'])
        self.assertEqual(self.tab.acc_vars['tiktok'].get(), 'Standardkonto')
        self.assertEqual(self.tab.acc_vars['reddit'].get(), 'Standardkonto')

    def test_default_choice_and_specific_account_commands_do_not_rotate(self):
        self.set_links(*self.links); self.tab.start_fetch()
        (_l, cmd), = self.app.steps[-1][0]
        self.assertNotIn('--rotate', cmd)
        self.assertEqual(self.opt(cmd, '--identity'), self.a['identity'])
        self.app.steps.clear(); self.app.job = None
        self.tab.acc_vars['tiktok'].set('Dritt'); self.set_links(*self.links); self.tab.start_fetch()
        (_l, cmd), = self.app.steps[-1][0]
        self.assertEqual(self.opt(cmd, '--identity'), self.c['identity'])
        self.assertNotIn('--rotate', cmd)

    def test_rotation_choice_adds_rotate_and_every_to_the_command(self):
        self.tab.acc_vars['tiktok'].set('🔄 Automatisch wechseln (Rotation)')
        self.set_links(*self.links); self.tab.start_fetch()
        (label, cmd), = self.app.steps[-1][0]
        self.assertIn('--rotate', cmd)
        self.assertEqual(self.opt(cmd, '--rotation-every'), '5')
        self.assertIn('Konto-Rotation', label)
        self.assertEqual(self.tab._pending['accounts']['tiktok'], 'rotate')

    def test_rotation_every_comes_from_the_settings(self):
        with patch.object(core, 'read_settings', return_value=__import__('types').SimpleNamespace(rotation_every=9, max_comments=20)):
            self.tab.acc_vars['tiktok'].set('🔄 Automatisch wechseln (Rotation)')
            self.set_links(*self.links); self.tab.start_fetch()
        (_l, cmd), = self.app.steps[-1][0]
        self.assertEqual(self.opt(cmd, '--rotation-every'), '9')

    def test_profile_with_rotation(self):
        self.tab.acc_vars['tiktok'].set('🔄 Automatisch wechseln (Rotation)')
        self.tab.profile_user.set('anna'); self.tab.start_profile()
        (_l, cmd), = self.app.steps[-1][0]
        self.assertEqual(cmd[0], 'profile')
        self.assertIn('--rotate', cmd)

    def test_result_text_names_the_used_accounts(self):
        self.tab.acc_vars['tiktok'].set('🔄 Automatisch wechseln (Rotation)')
        self.set_links(*self.links); self.tab.start_fetch()
        steps, on_finish = self.app.steps.pop()
        self.app.job = None
        lines = ['Benutzte Konten: Hauptkonto, Zweit', 'fertig: 2 ok, 0 fehler']
        on_finish([(label, 0, lines) for label, _ in steps], False)
        self.assertIn('Benutzte Konten: Hauptkonto, Zweit', self.tab.result.cget('text'))

    def test_no_account_available_is_refused_with_time_before_start(self):
        with Store(self.db) as store:
            for acc in (self.a, self.b, self.c):
                rotation.mark_failure(store.db, acc['id'], 'x', 'captcha')
        self.tab.acc_vars['tiktok'].set('🔄 Automatisch wechseln (Rotation)')
        self.set_links(*self.links); self.tab.start_fetch()
        self.assertEqual(self.app.steps, [])
        text = self.showwarning.call_args.args[1]
        self.assertIn('wieder frei ab', text)
        self.assertIn('„Hauptkonto“', text)

    def test_rotation_is_refused_in_free_and_tor_mode(self):
        self.tab.acc_vars['tiktok'].set('🔄 Automatisch wechseln (Rotation)')
        self.tab.max_comments.set(0)                       # ohne Kommentare ginge ein öffentliches TikTok-Video auch in diesem Modus
        for mode in ('frei', 'tor'):
            with patch('socialscraper.proxylogic.current_mode', return_value=mode):
                self.set_links(*self.links); self.tab.start_fetch()
        self.assertEqual(self.app.steps, [])
        self.assertIn('gesperrt', self.showwarning.call_args.args[1])

    def test_selection_survives_a_reload_and_follows_renamed_accounts(self):
        self.tab.acc_vars['tiktok'].set('Dritt'); self.tab.refresh()
        self.assertEqual(self.tab.acc_vars['tiktok'].get(), 'Dritt')
        import sqlite3
        from unittest.mock import patch as _patch
        with _patch('socialscraper.ui.logic.load_accounts', side_effect=sqlite3.OperationalError('database is locked')):
            self.tab._load_accounts()                                   # Lesefehler: Auswahl bleibt, kein Sprung auf Rotation
        self.assertEqual(self.tab.acc_vars['tiktok'].get(), 'Dritt')
        self.tab.acc_vars['tiktok'].set('🔄 Automatisch wechseln (Rotation)'); self.tab.refresh()
        self.assertEqual(self.tab.acc_vars['tiktok'].get(), '🔄 Automatisch wechseln (Rotation)')
        self.tab.acc_vars['tiktok'].set('Dritt'); self.tab.refresh()
        with Store(self.db) as store:
            repo.rename_account(store.db, self.c['id'], 'Neu')
        self.tab.refresh()
        self.assertEqual(self.tab.acc_vars['tiktok'].get(), 'Neu')               # das umbenannte Konto bleibt gewählt
        with Store(self.db) as store:
            repo.delete_account(store.db, self.c['id'])
        self.tab.refresh()
        self.assertEqual(self.tab.acc_vars['tiktok'].get(), 'Standardkonto')       # gelöscht: zurück auf den Standard

    def test_track_with_rotation_creates_rotating_targets(self):
        with Store(self.db) as store:
            store.save_video(__import__('socialscraper.models', fromlist=['Video']).Video(
                'tiktok', '1', self.links[0], author='anna'))
        out = logic.track_videos(self.db, ['tiktok:1'], 360, {'tiktok': 'rotate'})
        self.assertEqual(out['added'], 1)
        with Store(self.db) as store:
            target = repo.list_targets(store.db)[0]
        self.assertEqual((target['account_mode'], target['account_id']), ('rotate', None))


class AutomationRotationTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_auto import AutoTab
        self.tab = self.app.add('Automatik', AutoTab)
        self.addCleanup(lambda: self.app.pump(lambda: not self.tab.loading))
        self.load()

    def load(self):
        self.tab.refresh()
        self.assertTrue(self.app.pump(lambda: not self.tab.loading))

    def rows(self):
        return [self.tab.tree.item(i, 'values') for i in self.tab.tree.get_children()]

    def test_dialog_offers_default_specific_and_rotation(self):
        dlg = self.tab.add_target(modal=False)
        dlg.platform_var.set('TikTok'); dlg._platform_changed()
        self.assertEqual(list(dlg.account_cb['values']),
                         ['Standardkonto der Plattform', 'Hauptkonto', 'Dritt', 'Zweit', '🔄 Automatisch wechseln (Rotation)'])
        dlg.platform_var.set('YouTube'); dlg._platform_changed()
        self.assertEqual(str(dlg.account_cb['state']), 'disabled')
        dlg.destroy()

    def test_rotation_target_is_saved_and_listed_as_rotation(self):
        dlg = self.tab.add_target(modal=False)
        dlg.value_var.set('katzen'); dlg.platform_var.set('TikTok'); dlg._platform_changed()
        dlg.account_var.set('🔄 Automatisch wechseln (Rotation)'); dlg._account_changed()
        self.assertIn('abwechselnd', dlg.account_hint['text'])
        self.assertTrue(dlg.save())
        self.load()
        row = [r for r in self.rows() if r[2] == '#katzen'][0]
        self.assertEqual(row[4], 'Rotation')
        t = al.load_snapshot(self.db)['targets'][0]
        self.assertEqual((t['account_mode'], t['account_id']), ('rotate', None))

    def test_specific_account_target_and_edit_changes_the_mode(self):
        dlg = self.tab.add_target(modal=False)
        dlg.value_var.set('hunde'); dlg.platform_var.set('TikTok'); dlg._platform_changed()
        dlg.account_var.set('Zweit')
        self.assertTrue(dlg.save()); self.load()
        t = al.load_snapshot(self.db)['targets'][0]
        self.assertEqual((t['account_mode'], t['account_id']), ('fixed', self.b['id']))
        self.assertEqual([r for r in self.rows() if r[2] == '#hunde'][0][4], 'Zweit')
        self.tab.tree.selection_set(f"t{t['id']}"); self.app.update()
        edit = self.tab.edit_target(modal=False)
        self.assertEqual(edit.account_var.get(), 'Zweit')
        edit.account_var.set('🔄 Automatisch wechseln (Rotation)')
        self.assertTrue(edit.save()); self.load()
        t = al.load_snapshot(self.db)['targets'][0]
        self.assertEqual((t['account_mode'], t['account_id']), ('rotate', None))
        self.tab.tree.selection_set(f"t{t['id']}"); self.app.update()
        edit2 = self.tab.edit_target(modal=False)
        self.assertEqual(edit2.account_var.get(), '🔄 Automatisch wechseln (Rotation)')
        edit2.account_var.set('Standardkonto der Plattform')
        self.assertTrue(edit2.save()); self.load()
        t = al.load_snapshot(self.db)['targets'][0]
        self.assertEqual((t['account_mode'], t['account_id']), ('default', None))

    def test_run_log_shows_used_accounts(self):
        with Store(self.db) as store:
            t = repo.add_target(store.db, 'hashtag', 'tiktok', 'katzen', account_mode='rotate')
            run = repo.start_run(store.db, t['id'], 'x', 'manual')
            repo.finish_run(store.db, run, 'ok', videos_seen=2, accounts_used=['Hauptkonto', 'Zweit'])
        self.load()
        values = self.tab.runs_tree.item(self.tab.runs_tree.get_children()[0], 'values')
        self.assertEqual(values[8], 'Hauptkonto, Zweit')
        self.tab.runs_tree.selection_set(self.tab.runs_tree.get_children()[0])
        self.tab.show_run_details()
        self.assertIn('Benutzte Konten: Hauptkonto, Zweit', self.showinfo.call_args.args[1])


class SettingsRotationTests(WindowBase):
    def test_form_validation_and_roundtrip(self):
        from socialscraper import formlogic as fl
        raw = fl.default_raw()
        self.assertEqual(raw['rotation_every'], '5')
        settings, _integration, errors = fl.validate_form(dict(raw, rotation_every='12'))
        self.assertEqual((settings['rotation_every'], errors), (12, {}))
        for bad in ('0', '101', 'x', '', '2,5'):
            _s, _i, errors = fl.validate_form(dict(raw, rotation_every=bad))
            self.assertIn('rotation_every', errors, bad)
            self.assertIn('1 bis 100', errors['rotation_every'])

    def test_settings_field_is_shown_and_saved(self):
        from socialscraper.ui.tab_settings import SettingsTab
        tab = self.app.add('Einstellungen', SettingsTab)
        self.app.update()
        self.assertIn('rotation_every', tab.vars)
        tab.vars['rotation_every'].set('7')
        self.assertEqual(tab.collect()['rotation_every'], '7')
        self.assertIn('rotation_every', core.EDITABLE)


if __name__ == '__main__':
    unittest.main()
