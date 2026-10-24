"""Tests für Konten (Befehle, Status, Zugangsdaten, Verwaltung) und den Abrufen-Tab (Konto, Gruppe, Verlauf, Sperre)."""
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import cli, guicore as core, repo
from socialscraper.models import Video
from socialscraper.store import Store
from socialscraper.ui import logic


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / 'data' / 'scrape.db'
        for p in (patch.dict(os.environ, {'SS_DB_PATH': str(self.db)}), patch.object(core, 'ROOT', self.root),
                  patch.object(core, 'write_settings')):   # nie die echte settings.toml verändern
            p.start(); self.addCleanup(p.stop)

    def cookie(self, identity, platform='tiktok', names=('sessionid',), value='x'):
        d = self.root / 'data' / 'cookies'; d.mkdir(parents=True, exist_ok=True)
        (d / f'{identity}.{platform}.json').write_text(json.dumps([{'name': n, 'value': value} for n in names]), encoding='utf-8')

    def writer(self):
        return logic.writer(self.db)


class CommandTests(unittest.TestCase):
    def opt(self, cmd, name):
        return cmd[cmd.index(name) + 1]

    def test_defaults_stay_the_old_main_account(self):
        cmd = core.scrape_command('instagram', ['https://www.instagram.com/p/a/'], 5)
        self.assertEqual((self.opt(cmd, '--identity'), self.opt(cmd, '--country')), ('instagram_main', 'de'))
        self.assertEqual(self.opt(core.login_command('tiktok', 'qr'), '--identity'), 'tiktok_main')
        self.assertEqual(self.opt(core.logout_command('tiktok'), '--identity'), 'tiktok_main')

    def test_free_identity_and_country_flow_into_every_command(self):
        for cmd in (core.scrape_command('tiktok', ['https://www.tiktok.com/@u/video/1'], 5, identity='tiktok_zweit', country='AT'),
                    core.profile_command('user', 3, 5, identity='tiktok_zweit', country='AT'),
                    core.login_command('tiktok', 'password', 'tiktok_zweit', 'at'),
                    core.check_command('tiktok', 'tiktok_zweit', 'at')):
            self.assertEqual(self.opt(cmd, '--identity'), 'tiktok_zweit'); self.assertEqual(self.opt(cmd, '--country'), 'at')
        self.assertEqual(self.opt(core.logout_command('instagram', 'instagram_x'), '--identity'), 'instagram_x')

    def test_identity_and_country_cannot_smuggle_options_or_paths(self):
        for bad in ('--evil', '-x', '../x', 'a/b', 'a b', '', 'x' * 81, '.versteckt'):
            if bad == '':
                continue   # leer = Standardkonto
            with self.assertRaises(ValueError, msg=bad): core.scrape_command('tiktok', ['https://www.tiktok.com/@u/video/1'], 1, identity=bad)
            with self.assertRaises(ValueError, msg=bad): core.logout_command('tiktok', bad)
        for bad in ('deu', 'd', '1a', '--x', 'd e'):
            with self.assertRaises(ValueError, msg=bad): core.check_command('tiktok', 'tiktok_main', bad)

    def test_import_cookies_command_and_real_parser(self):
        cmd = core.import_cookies_command('tiktok', 'tiktok_zweit', r'C:\Users\x\cookies.json', 'de')
        self.assertEqual(cmd[0], 'import-cookies'); self.assertEqual(self.opt(cmd, '--file'), r'C:\Users\x\cookies.json')
        with self.assertRaises(ValueError): core.import_cookies_command('tiktok', 'tiktok_main', '  ')
        self.assertIn('youtube', core.import_cookies_command('youtube', 'x', 'f.json'))
        ap = cli.parser()
        for c in (cmd, core.scrape_command('tiktok', ['https://www.tiktok.com/@u/video/1'], 5, True, None, 'tiktok_zweit', 'at'),
                  core.profile_command('user', 3, 5, False, 'tiktok_zweit', 'at'), core.login_command('instagram', 'qr', 'instagram_m', 'de'),
                  core.check_command('tiktok', 'tiktok_zweit', 'at'), core.logout_command('tiktok', 'tiktok_zweit')):
            ap.parse_args(c)

    def test_youtube_still_needs_no_account(self):
        self.assertEqual(core.account_status('youtube')[0], 'none')
        for fn in (core.check_command, core.logout_command):
            self.assertIn('youtube', fn('youtube', 'youtube_main'))
        self.assertIn(4, core.EXIT_TEXT)


class StatusTests(Base):
    def test_any_identity_and_legacy_files(self):
        self.assertEqual(core.account_status('tiktok', self.root, 'tiktok_zweit')[0], 'missing')
        self.cookie('tiktok_zweit'); self.assertEqual(core.account_status('tiktok', self.root, 'tiktok_zweit')[0], 'saved')
        self.assertEqual(core.account_status('tiktok', self.root)[0], 'missing')                 # Hauptkonto ist ein anderes
        self.cookie('tiktok_main', names=('sessionid_ss',)); self.assertEqual(core.account_status('tiktok', self.root)[0], 'saved')   # Altdatei
        self.cookie('instagram_main', 'instagram'); self.assertEqual(core.account_status('instagram', self.root)[0], 'saved')
        self.cookie('instagram_main', 'instagram', names=('csrftoken',)); self.assertEqual(core.account_status('instagram', self.root)[0], 'missing')
        self.assertEqual(core.account_status('tiktok', self.root, '../boese')[0], 'error')       # kein Dateizugriff außerhalb
        self.assertEqual(core.account_status('youtube', self.root)[0], 'none')

    def test_uses_core_root_at_call_time(self):
        self.cookie('tiktok_main'); self.assertEqual(core.account_status('tiktok')[0], 'saved')   # ROOT ist per patch der Wegwerf-Ordner


class CredentialsTests(Base):
    def path(self):
        return core.credentials_file(self.root)

    def test_entry_is_created_and_existing_never_overwritten(self):
        self.assertEqual(core.credentials_state('tiktok', 'tiktok_zweit', self.root), 'missing')
        self.assertTrue(core.ensure_credentials_entry('tiktok', 'tiktok_zweit', self.root))
        self.assertEqual(json.loads(self.path().read_text(encoding='utf-8')), {'tiktok': {'tiktok_zweit': {'user': '', 'password': ''}}})
        self.assertEqual(core.credentials_state('tiktok', 'tiktok_zweit', self.root), 'empty')
        data = {'tiktok': {'tiktok_zweit': {'user': 'u', 'password': 'p'}, 'tiktok_main': {'user': 'm', 'password': 'q'}}, 'instagram': {}, 'extra': 1}
        self.path().write_text(json.dumps(data), encoding='utf-8')
        self.assertFalse(core.ensure_credentials_entry('tiktok', 'tiktok_zweit', self.root))      # nicht überschrieben
        self.assertEqual(json.loads(self.path().read_text(encoding='utf-8')), data)
        self.assertTrue(core.ensure_credentials_entry('instagram', 'instagram_neu', self.root))
        merged = json.loads(self.path().read_text(encoding='utf-8'))
        self.assertEqual(merged['tiktok'], data['tiktok']); self.assertEqual(merged['extra'], 1)   # nichts anderes verändert
        self.assertEqual(core.credentials_state('tiktok', 'tiktok_zweit', self.root), 'ready')

    def test_damaged_file_is_never_rewritten(self):
        self.path().parent.mkdir(parents=True, exist_ok=True)
        for text in ('{kaputt', '[1, 2]'):
            self.path().write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError) as ctx: core.ensure_credentials_entry('tiktok', 'tiktok_main', self.root)
            self.assertIn('nichts verändert', str(ctx.exception)); self.assertEqual(self.path().read_text(encoding='utf-8'), text)
        self.path().write_text('{"tiktok": []}', encoding='utf-8')
        with self.assertRaises(ValueError): core.ensure_credentials_entry('tiktok', 'tiktok_main', self.root)
        self.assertEqual(core.credentials_state('tiktok', 'tiktok_main', self.root), 'missing')

    def test_no_secret_in_results_or_errors(self):
        self.path().parent.mkdir(parents=True, exist_ok=True)
        self.path().write_text(json.dumps({'tiktok': {'tiktok_main': {'user': 'geheimuser', 'password': 'geheimpasswort'}}}), encoding='utf-8')
        self.assertEqual(core.credentials_state('tiktok', 'tiktok_main', self.root), 'ready')
        self.assertNotIn('geheim', repr(core.credentials_state('tiktok', 'tiktok_main', self.root)))
        with self.assertRaises(ValueError): core.ensure_credentials_entry('youtube', 'x', self.root)

    def test_bad_identity_is_refused(self):
        with self.assertRaises(ValueError): core.ensure_credentials_entry('tiktok', '../x', self.root)
        self.assertFalse(self.path().exists())


class AccountLogicTests(Base):
    def test_defaults_then_add_rename_default_remove(self):
        accs = logic.load_accounts(self.db)
        self.assertEqual({(a['platform'], a['label'], a['identity'], a['is_default']) for a in accs},
                         {('tiktok', 'Hauptkonto', 'tiktok_main', 1), ('instagram', 'Hauptkonto', 'instagram_main', 1),
                          ('reddit', 'Hauptkonto', 'reddit_main', 1), ('x', 'Hauptkonto', 'x_main', 1)})
        a = logic.add_account(self.db, 'tiktok', 'Zweit Konto ä', 'AT')
        self.assertEqual((a['identity'], a['country'], a['is_default']), ('tiktok_zweit_konto_ae', 'at', 0))
        logic.rename_account(self.db, a['id'], 'Recherche')
        logic.set_default(self.db, a['id'])
        by = {x['label']: x for x in logic.load_accounts(self.db, ) if x['platform'] == 'tiktok'}
        self.assertEqual((by['Recherche']['is_default'], by['Hauptkonto']['is_default']), (1, 0))
        logic.remove_account(self.db, a['id'])
        self.assertEqual([x['is_default'] for x in logic.load_accounts(self.db) if x['platform'] == 'tiktok'], [1])   # Standard rückt nach

    def test_input_checks(self):
        logic.load_accounts(self.db)
        for args in (('tiktok', '', 'de'), ('tiktok', '   ', 'de'), ('tiktok', 'x' * 61, 'de'), ('youtube', 'Y', 'de'),
                     ('tiktok', 'Neu', 'deu'), ('tiktok', 'Neu', ''), ('tiktok', ' hauptkonto ', 'de')):
            with self.assertRaises(ValueError, msg=str(args)): logic.add_account(self.db, *args)
        logic.add_account(self.db, 'instagram', 'Hauptkonto2')
        a = logic.add_account(self.db, 'tiktok', 'B')
        with self.assertRaises(ValueError): logic.rename_account(self.db, a['id'], 'hauptkonto')      # doppelt (ohne Groß/klein)
        logic.rename_account(self.db, a['id'], 'B')                                                   # eigener Name ist erlaubt
        with self.assertRaises(ValueError): logic.rename_account(self.db, 9999, 'x')
        # gleicher Name bei anderer Plattform ist erlaubt
        logic.add_account(self.db, 'instagram', 'B')

    def test_last_account_of_a_platform_stays(self):
        accs = logic.load_accounts(self.db)
        tk = next(a for a in accs if a['platform'] == 'tiktok')
        with self.assertRaises(ValueError) as ctx: logic.remove_account(self.db, tk['id'])
        self.assertIn('mindestens ein Konto', str(ctx.exception))
        self.assertEqual(len(logic.load_accounts(self.db)), 4)

    def test_target_count_and_removal_keep_targets(self):
        accs = logic.load_accounts(self.db); tk = next(a for a in accs if a['platform'] == 'tiktok')
        b = logic.add_account(self.db, 'tiktok', 'B')
        with self.writer() as db:
            repo.add_target(db, 'hashtag', 'tiktok', 'katzen', account_id=b['id'])
        self.assertEqual(logic.account_target_count(self.db, b['id']), 1)
        logic.remove_account(self.db, b['id'])
        with self.writer() as db:
            self.assertIsNone(repo.list_targets(db)[0]['account_id'])


class BusyNoteTests(Base):
    def test_none_without_lock_and_text_with_daemon(self):
        from socialscraper import runlock
        self.assertIsNone(logic.describe_busy(self.root))
        with runlock.acquire(self.root, 'daemon'):
            self.assertIn('Hintergrunddienst arbeitet gerade', logic.describe_busy(self.root))
        self.assertIsNone(logic.describe_busy(self.root))                                            # wieder frei
        with runlock.acquire(self.root, 'cli'):
            self.assertIn('Browserauftrag', logic.describe_busy(self.root))

    def test_missing_module_or_errors_never_block(self):
        with patch.dict(sys.modules, {'socialscraper.runlock': None}):
            self.assertIsNone(logic.describe_busy(self.root))
        (self.root / 'data').mkdir(parents=True, exist_ok=True); (self.root / 'data' / 'run.lock').write_bytes(b'')
        with patch('socialscraper.runlock.holder', side_effect=OSError('boom')):
            self.assertIsNone(logic.describe_busy(self.root))


# ---------- Fenster ----------

def fake_form(values, record=None):
    def fake(parent, title, fields, intro='', ok_text='OK', validator=None):
        problem = validator(values) if validator else None
        if record is not None: record.append(problem)
        return None if problem else values
    return fake


@unittest.skipUnless(os.name == 'nt', 'Tk-Fenster nur unter Windows')
class WindowBase(Base):
    def setUp(self):
        super().setUp()
        import tkinter
        from ui_fake import FakeApp
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self.app.destroy)

    def finish(self, code=0, lines=()):
        """Den letzten aufgezeichneten Auftrag abschließen, als wäre er mit `code` beendet worden."""
        steps, on_finish = self.app.steps.pop()
        on_finish([(label, code, list(lines)) for label, _args in steps], False)
        return steps


class AccountsTabTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_accounts import AccountsTab
        self.tab = self.app.add('Konten', AccountsTab); self.tab.refresh(); self.app.update()

    def pick(self, platform='tiktok', label='Hauptkonto'):
        iid = next(i for i, a in self.tab.accounts.items() if a['platform'] == platform and a['label'] == label)
        self.tab.tree.selection_set(iid); self.app.update(); return self.tab.accounts[iid]

    def test_default_accounts_shown_with_status_and_buttons_follow_selection(self):
        self.cookie('tiktok_main')
        self.tab.refresh()
        rows = [self.tab.tree.item(i, 'values') for i in self.tab.tree.get_children()]
        self.assertEqual([(r[0], r[1], r[3]) for r in rows], [('TikTok', 'Hauptkonto', '★ Standard'), ('Instagram', 'Hauptkonto', '★ Standard'),
                                                                ('Reddit', 'Hauptkonto', '★ Standard'), ('X', 'Hauptkonto', '★ Standard')])
        self.assertIn('Anmeldung gespeichert', rows[0][4]); self.assertIn('Nicht angemeldet', rows[1][4])
        self.assertEqual(str(self.tab.btn['login']['state']), 'disabled'); self.assertEqual(str(self.tab.btn['add']['state']), 'normal')
        self.pick(); self.assertEqual(str(self.tab.btn['login']['state']), 'normal')

    def test_add_account_shows_duplicate_inline_and_selects_new_one(self):
        rec = []
        with patch('socialscraper.ui.tab_accounts.ask_form', side_effect=fake_form({'name': 'Hauptkonto', 'platform': 'tiktok', 'country': 'de'}, rec)):
            self.tab.add()
        self.assertIn('gibt es bei TikTok schon', rec[-1]); self.assertEqual(len(self.tab.accounts), 4)
        with patch('socialscraper.ui.tab_accounts.ask_form', side_effect=fake_form({'name': 'Zweit', 'platform': 'tiktok', 'country': 'at'}, rec)):
            self.tab.add()
        self.assertIsNone(rec[-1]); self.assertEqual(len(self.tab.accounts), 5)
        self.assertEqual(self.tab.current()['label'], 'Zweit')
        self.assertIn('angelegt', self.app.statuses[-1][0])

    def test_rename_default_and_actions_without_selection(self):
        with patch('tkinter.messagebox.showinfo') as info:
            self.tab.rename(); self.tab.make_default(); self.tab.check(); self.tab.logout(); self.tab.open_credentials()
        self.assertEqual(info.call_count, 5)
        b = logic.add_account(self.db, 'tiktok', 'B'); self.tab.refresh()
        self.tab.tree.selection_set(str(b['id'])); self.app.update()
        with patch('socialscraper.ui.tab_accounts.ask_form', side_effect=fake_form({'name': 'Bee'})):
            self.tab.rename()
        self.tab.make_default()
        self.assertEqual({a['label']: a['is_default'] for a in logic.load_accounts(self.db) if a['platform'] == 'tiktok'}, {'Hauptkonto': 0, 'Bee': 1})

    def test_login_password_without_credentials_offers_the_file_and_starts_nothing(self):
        acc = self.pick()
        with patch('tkinter.messagebox.askyesno', return_value=False) as ask:
            self.tab.login('password')
        self.assertIn('noch keine Zugangsdaten', ask.call_args[0][1]); self.assertEqual(self.app.steps, [])
        with patch('tkinter.messagebox.askyesno', return_value=True):
            self.tab.login('password')
        self.assertEqual(self.app.opened[-1], (core.credentials_file(self.root), True))
        self.assertEqual(core.credentials_state('tiktok', acc['identity'], self.root), 'empty')       # leerer Eintrag angelegt

    def test_login_in_browser_uses_this_accounts_identity_and_country(self):
        b = logic.add_account(self.db, 'tiktok', 'Zweit', 'at'); self.tab.refresh()
        self.tab.tree.selection_set(str(b['id'])); self.app.update()
        self.tab.login('qr')
        (label, cmd), = self.app.steps[-1][0]
        self.assertEqual((cmd[cmd.index('--identity') + 1], cmd[cmd.index('--country') + 1], cmd[cmd.index('--mode') + 1]), ('tiktok_zweit', 'at', 'qr'))
        with patch('tkinter.messagebox.showinfo') as info:
            self.cookie('tiktok_zweit'); self.finish(0)
        self.assertIn('Anmeldung bestätigt', info.call_args[0][1])
        self.assertIn('bestätigt', self.tab.tree.item(str(b['id']), 'values')[4])

    def test_login_refuses_politely_while_background_service_works(self):
        self.cookie('tiktok_main'); self.pick()
        with patch.object(logic, 'describe_busy', return_value='Der Hintergrunddienst arbeitet gerade.'), patch('tkinter.messagebox.showinfo') as info:
            self.tab.login('qr'); self.tab.check()
        self.assertEqual(self.app.steps, []); self.assertIn('Hintergrunddienst', info.call_args[0][1])

    def test_check_needs_saved_login_then_reports_result(self):
        self.pick()
        with patch('tkinter.messagebox.showinfo') as info:
            self.tab.check()
        self.assertIn('keine Anmeldung gespeichert', info.call_args[0][1]); self.assertEqual(self.app.steps, [])
        self.cookie('tiktok_main'); self.tab.check()
        with patch('tkinter.messagebox.showwarning') as warn:
            self.finish(2)
        self.assertIn('nicht mehr gültig', warn.call_args[0][1]); self.assertEqual(self.app.statuses[-1][1], 'err')

    def test_busy_exit_code_gets_the_friendly_text(self):
        self.cookie('tiktok_main'); self.pick(); self.tab.check()
        with patch('tkinter.messagebox.showwarning') as warn:
            self.finish(4)
        self.assertIn('anderer Browserauftrag', warn.call_args[0][1])

    def test_logout_nothing_to_delete_and_confirm(self):
        self.pick()
        with patch('tkinter.messagebox.showinfo') as info:
            self.tab.logout()
        self.assertIn('nichts zu löschen', info.call_args[0][1])
        self.cookie('tiktok_main')
        with patch('tkinter.messagebox.askyesno', return_value=False):
            self.tab.logout()
        self.assertEqual(self.app.steps, [])
        with patch('tkinter.messagebox.askyesno', return_value=True):
            self.tab.logout()
        (_l, cmd), = self.app.steps[-1][0]; self.assertEqual(cmd[0], 'logout'); self.assertEqual(cmd[cmd.index('--identity') + 1], 'tiktok_main')

    def test_remove_last_account_is_blocked_and_others_log_out_first(self):
        self.pick()
        with patch('tkinter.messagebox.showinfo') as info:
            self.tab.remove()
        self.assertIn('mindestens ein Konto', info.call_args[0][1])
        b = logic.add_account(self.db, 'tiktok', 'B'); self.cookie(b['identity']); self.tab.refresh()
        self.tab.tree.selection_set(str(b['id'])); self.app.update()
        with patch('tkinter.messagebox.askyesno', return_value=False):
            self.tab.remove()
        self.assertEqual(len(self.tab.accounts), 5)
        with patch('tkinter.messagebox.askyesno', return_value=True) as ask:
            self.tab.remove()
        self.assertIn('Videos und Kommentare bleiben', ask.call_args[0][1])
        (_l, cmd), = self.app.steps[-1][0]; self.assertEqual(cmd[0], 'logout')
        self.assertEqual(len(self.tab.accounts), 5)                                                    # erst nach erfolgreichem Abmelden
        self.finish(0)
        self.assertEqual(len(self.tab.accounts), 4)

    def test_remove_stays_when_logout_fails(self):
        b = logic.add_account(self.db, 'tiktok', 'B'); self.cookie(b['identity']); self.tab.refresh()
        self.tab.tree.selection_set(str(b['id'])); self.app.update()
        with patch('tkinter.messagebox.askyesno', return_value=True):
            self.tab.remove()
        with patch('tkinter.messagebox.showwarning') as warn:
            self.finish(1)
        self.assertIn('nicht entfernt', warn.call_args[0][1]); self.assertEqual(len(logic.load_accounts(self.db)), 5)

    def test_remove_without_login_data_deletes_directly(self):
        b = logic.add_account(self.db, 'instagram', 'B'); self.tab.refresh()
        self.tab.tree.selection_set(str(b['id'])); self.app.update()
        with patch('tkinter.messagebox.askyesno', return_value=True):
            self.tab.remove()
        self.assertEqual(self.app.steps, []); self.assertEqual(len(logic.load_accounts(self.db)), 4)

    def test_import_from_file_builds_the_cli_command(self):
        acc = self.pick()
        with patch('tkinter.filedialog.askopenfilename', return_value=''):
            self.tab.import_file()
        self.assertEqual(self.app.steps, [])
        with patch('tkinter.filedialog.askopenfilename', return_value=r'C:\x\c.json'), patch('tkinter.messagebox.askyesno', return_value=True):
            self.tab.import_file()
        (_l, cmd), = self.app.steps[-1][0]
        self.assertEqual(cmd[:1] + cmd[cmd.index('--file'):], ['import-cookies', '--file', r'C:\x\c.json'])
        self.assertEqual(cmd[cmd.index('--identity') + 1], acc['identity'])
        with patch('tkinter.messagebox.showwarning') as warn:
            self.finish(1)
        self.assertIn('nicht übernommen', warn.call_args[0][1])

    def test_credentials_button_creates_entry_opens_editor_and_survives_damaged_file(self):
        acc = self.pick()
        self.tab.open_credentials()
        self.assertEqual(self.app.opened[-1], (core.credentials_file(self.root), True))
        before = core.credentials_file(self.root).read_text(encoding='utf-8')
        self.tab.open_credentials(); self.assertEqual(core.credentials_file(self.root).read_text(encoding='utf-8'), before)
        core.credentials_file(self.root).write_text('{kaputt', encoding='utf-8')
        with patch('tkinter.messagebox.showwarning') as warn:
            self.tab.open_credentials()
        self.assertIn('beschädigt', warn.call_args[0][1]); self.assertEqual(core.credentials_file(self.root).read_text(encoding='utf-8'), '{kaputt')
        self.assertTrue(any('credentials' in l and tag == 'WARNING' for l, tag in self.app.logs))
        self.assertFalse(any('password' in l.lower() for l, _t in self.app.logs))

    def test_locked_database_shows_a_message_not_a_crash(self):
        with patch.object(logic, 'load_accounts', side_effect=__import__('sqlite3').OperationalError('database is locked')):
            self.tab.refresh()
        self.assertIn('nicht lesbar', self.tab.info.cget('text'))
        self.assertTrue(any('gesperrt' in l for l, _t in self.app.logs))


class FetchTabTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_data import DataTab
        from socialscraper.ui.tab_fetch import FetchTab
        self.data = self.app.add('Gespeicherte Daten', DataTab)
        self.tab = self.app.add('Abrufen', FetchTab); self.tab.refresh(); self.app.update()

    def set_links(self, text):
        self.tab.links.delete('1.0', 'end'); self.tab.links.insert('1.0', text)

    def opt(self, cmd, name):
        return cmd[cmd.index(name) + 1]

    def test_account_dropdowns_list_accounts_default_preselected(self):
        logic.add_account(self.db, 'tiktok', 'Zweit', 'at'); self.tab.refresh()
        self.assertEqual(list(self.tab.acc_boxes['tiktok']['values']),
                         ['Standardkonto', 'Hauptkonto  (Standard)', 'Zweit', '🔄 Automatisch wechseln (Rotation)'])
        self.assertEqual(self.tab.acc_vars['tiktok'].get(), 'Standardkonto')

    def test_chosen_account_goes_into_the_command(self):
        logic.add_account(self.db, 'tiktok', 'Zweit', 'at'); self.tab.refresh()
        self.tab.acc_vars['tiktok'].set('Zweit')
        self.set_links('https://www.tiktok.com/@u/video/123'); self.tab.start_fetch()
        (_l, cmd), = self.app.steps[-1][0]
        self.assertEqual((self.opt(cmd, '--identity'), self.opt(cmd, '--country')), ('tiktok_zweit', 'at'))
        self.assertIn('Zweit', _l)
        self.app.steps.clear()
        self.tab.profile_user.set('@jane'); self.tab.start_profile()
        (_l, cmd), = self.app.steps[-1][0]; self.assertEqual((cmd[0], self.opt(cmd, '--identity')), ('profile', 'tiktok_zweit'))

    def test_instagram_needs_the_chosen_account_to_be_logged_in(self):
        self.set_links('https://www.instagram.com/p/abc/')
        with patch('tkinter.messagebox.askyesno', return_value=True) as ask:
            self.tab.start_fetch()
        self.assertIn('nicht angemeldet', ask.call_args[0][1]); self.assertEqual(self.app.selected[-1], 'Konten'); self.assertEqual(self.app.steps, [])
        self.cookie('instagram_main', 'instagram'); self.tab.start_fetch()
        (_l, cmd), = self.app.steps[-1][0]; self.assertIn('--require-auth', cmd); self.assertEqual(self.opt(cmd, '--identity'), 'instagram_main')

    def test_background_service_working_gives_friendly_message_and_no_job(self):
        self.set_links('https://www.tiktok.com/@u/video/123')
        with patch.object(logic, 'describe_busy', return_value='Der Hintergrunddienst arbeitet gerade.'), patch('tkinter.messagebox.showinfo') as info:
            self.tab.start_fetch()
        self.assertEqual(self.app.steps, []); self.assertIn('Hintergrunddienst arbeitet gerade', info.call_args[0][1])
        self.assertIn('Hintergrunddienst', self.tab.result.cget('text'))
        with patch.object(logic, 'describe_busy', return_value='X') as busy:
            self.set_links('https://youtu.be/abc'); self.tab.start_fetch()      # YouTube braucht keinen Browser und keine Sperre
        busy.assert_not_called(); self.assertEqual(len(self.app.steps), 1)

    def test_real_lock_blocks_tiktok_start(self):
        from socialscraper import runlock
        self.set_links('https://www.tiktok.com/@u/video/123')
        with runlock.acquire(self.root, 'daemon'), patch('tkinter.messagebox.showinfo'):
            self.tab.start_fetch()
        self.assertEqual(self.app.steps, [])

    def test_new_group_from_dropdown_and_cancel_resets(self):
        self.tab.group_var.set('Neue Gruppe …')
        with patch('socialscraper.ui.tab_fetch.ask_form', side_effect=fake_form({'name': 'Ernte'})):
            self.tab._group_chosen()
        self.assertEqual(self.tab.group_var.get(), 'Ernte'); self.assertIn('Ernte', self.tab.group_box['values'])
        self.tab.group_var.set('Neue Gruppe …')
        with patch('socialscraper.ui.tab_fetch.ask_form', side_effect=fake_form({'name': 'ernte'})):    # doppelt
            self.tab._group_chosen()
        self.assertEqual(self.tab.group_var.get(), '(keine Gruppe)')

    def fetched(self, *videos):
        with Store(self.db) as s:
            for v in videos: s.save_result(v, [], None)

    def test_after_fetch_group_and_tracking_use_only_the_new_videos(self):
        old = Video('youtube', 'alt00000000', 'https://youtu.be/alt00000000')
        self.fetched(old)
        with self.writer() as db:
            db.execute("UPDATE videos SET fetched_at='2020-01-01 00:00:00'"); db.commit()
        g = logic.create_group(self.db, 'Ernte'); self.tab.refresh()
        self.tab.group_var.set('Ernte'); self.tab.track_var.set(True); self.tab._toggle_track(); self.tab.interval_var.set('Jede Stunde')
        self.set_links('https://youtu.be/neu00000000'); self.tab.start_fetch()
        self.fetched(Video('youtube', 'neu00000000', 'https://youtu.be/neu00000000', author='Neu'))
        self.finish(0, ['fertig: 1 ok, 0 fehler'])
        text = self.tab.result.cget('text')
        self.assertIn('In Gruppe „Ernte“ einsortiert: 1', text); self.assertIn('Verlauf wird verfolgt: 1', text)
        self.assertEqual([r['video_id'] for r in logic.query_videos(self.db, group=g['id'])], ['neu00000000'])
        with self.writer() as db:
            (t,) = repo.list_targets(db)
        self.assertEqual((t['kind'], t['value'], t['interval_min']), ('video', 'https://youtu.be/neu00000000', 60))
        self.assertEqual(self.app.selected[-1], 'Gespeicherte Daten'); self.assertEqual(self.data.view, g['id'])
        self.assertEqual(self.data.tree.selection(), ('youtube:neu00000000',))

    def test_after_fetch_without_options_changes_nothing_and_failures_are_reported(self):
        self.set_links('https://youtu.be/neu00000000'); self.tab.start_fetch()
        self.fetched(Video('youtube', 'neu00000000', 'https://youtu.be/neu00000000'))
        self.finish(0, ['fertig: 1 ok, 0 fehler'])
        self.assertEqual(logic.group_overview(self.db)['groups'], []); self.assertIn('✔ 1 Video abgerufen', self.tab.result.cget('text'))
        # Lauf ohne Ergebnis: keine Einsortierung, klare Meldung
        g = logic.create_group(self.db, 'G'); self.tab.refresh(); self.tab.group_var.set('G')
        self.set_links('https://youtu.be/zzz00000000'); self.tab.start_fetch()
        self.finish(1, ['fertig: 0 ok, 1 fehler', '04:00:00 ERROR   cli | youtube: Video nicht verfügbar'])
        self.assertIn('Nichts abgerufen', self.tab.result.cget('text')); self.assertEqual(logic.query_videos(self.db, group=g['id']), [])

    def test_same_video_in_different_spellings_is_fetched_once_and_counted_once(self):
        ident = 'dQw4w9WgXcQ'
        self.set_links(f'https://www.youtube.com/watch?v={ident}\nhttps://youtu.be/{ident}\nhttps://m.youtube.com/watch?v={ident}&t=5\n'
                       f'https://www.youtube.com/shorts/{ident}')
        self.tab.start_fetch()
        (label, cmd), = self.app.steps[-1][0]
        self.assertEqual(cmd[1:cmd.index('--backend')], [f'https://www.youtube.com/watch?v={ident}'])
        self.assertIn('1 Link abrufen', label)
        self.fetched(Video('youtube', ident, f'https://www.youtube.com/watch?v={ident}'))
        self.finish(0, ['fertig: 1 ok, 0 fehler'])
        text = self.tab.result.cget('text')
        self.assertIn('✔ 1 Video abgerufen', text); self.assertIn('3 doppelte Links wurden zusammengefasst', text)

    def test_comment_count_applies_to_this_fetch_only_and_never_overwrites_the_default(self):
        (self.root / 'settings.toml').write_text('max_comments = 77\n', encoding='utf-8')
        self.tab.refresh()
        self.assertEqual(self.tab.max_comments.get(), 77)                       # Startwert = Standard aus den Einstellungen
        self.assertIn('Nur für diesen Abruf', self.tab.comments_hint.cget('text'))
        self.assertIn('Einstellungen', self.tab.comments_hint.cget('text'))
        self.tab.max_comments.set(5)
        self.set_links('https://youtu.be/neu00000000'); self.tab.start_fetch()
        (_l, cmd), = self.app.steps[-1][0]
        self.assertEqual(self.opt(cmd, '--max-comments'), '5')
        self.tab.profile_user.set('jane'); self.app.steps.clear(); self.tab.start_profile()
        self.assertEqual(self.app.steps[-1][0][0][1][self.app.steps[-1][0][0][1].index('--max-comments') + 1], '5')
        core.write_settings.assert_not_called()                                  # nichts wird gespeichert
        self.assertEqual((self.root / 'settings.toml').read_text(encoding='utf-8'), 'max_comments = 77\n')
        self.assertFalse(hasattr(self.tab, '_persist_max_comments'))
        self.tab.refresh(); self.assertEqual(self.tab.max_comments.get(), 5)    # eigene Eingabe bleibt, bis das Fenster neu startet

    def test_untouched_comment_count_follows_a_changed_default(self):
        (self.root / 'settings.toml').write_text('max_comments = 10\n', encoding='utf-8'); self.tab.refresh()
        self.assertEqual(self.tab.max_comments.get(), 10)
        (self.root / 'settings.toml').write_text('max_comments = 40\n', encoding='utf-8'); self.tab.refresh()
        self.assertEqual(self.tab.max_comments.get(), 40)

    def test_group_missing_after_the_run_is_reported_not_swallowed(self):
        g = logic.create_group(self.db, 'Weg'); self.tab.refresh(); self.tab.group_var.set('Weg')
        self.set_links('https://youtu.be/neu00000000'); self.tab.start_fetch()
        self.fetched(Video('youtube', 'neu00000000', 'https://youtu.be/neu00000000'))
        logic.delete_group(self.db, g['id'])                                                         # während des Laufs gelöscht
        self.finish(0, ['fertig: 1 ok, 0 fehler'])
        self.assertIn('Einsortieren in die Gruppe hat nicht geklappt', self.tab.result.cget('text'))
        self.assertTrue(any(tag == 'ERROR' and 'Einsortieren' in l for l, tag in self.app.logs))

    def test_profile_fetch_collects_by_time_window(self):
        g = logic.create_group(self.db, 'P'); self.tab.refresh(); self.tab.group_var.set('P')
        self.tab.profile_user.set('jane'); self.tab.start_profile()
        self.fetched(Video('tiktok', '111', 'https://www.tiktok.com/@jane/video/111'), Video('tiktok', '222', 'https://www.tiktok.com/@jane/video/222'))
        self.finish(0, ['fertig: 2 ok, 0 fehler'])
        self.assertEqual(len(logic.query_videos(self.db, group=g['id'])), 2)

    def test_cancelled_run_does_nothing_extra(self):
        self.set_links('https://youtu.be/abc'); self.tab.start_fetch()
        steps, on_finish = self.app.steps.pop(); on_finish([], True)
        self.assertEqual(self.tab.result.cget('text'), 'Abgebrochen.')

    def test_invalid_input_never_starts_a_job_and_settings_are_not_written_then(self):
        with patch('tkinter.messagebox.showwarning') as warn, patch('tkinter.messagebox.showinfo') as info:
            self.set_links(''); self.tab.start_fetch(); info.assert_called()
            self.set_links('quatsch'); self.tab.start_fetch(); warn.assert_called()
            self.set_links('https://youtu.be/a'); self.tab.max_comments.set(-1); self.tab.start_fetch()
            self.tab.profile_user.set('--limit'); self.tab.start_profile()
        self.assertEqual(self.app.steps, []); core.write_settings.assert_not_called()


if __name__ == '__main__':
    unittest.main()
