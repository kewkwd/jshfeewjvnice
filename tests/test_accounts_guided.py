"""Geführte Kontenseite: echte Widgets, Wegwerf-Daten und simulierte Prüfantworten."""
import contextlib
import io
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_ui_accounts import WindowBase
from socialscraper import cli, login, guicore as core, proxylogic
from socialscraper.config import Settings
from socialscraper.net.session_guard import State
from socialscraper.ui import logic


class GuidedAccountsTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_accounts import AccountsTab
        self.tab = self.app.add('Konten', AccountsTab)
        self.tab.refresh()
        self.app.update()

    def pick(self, platform='tiktok'):
        identity = core.PLATFORMS[platform]['identity']
        iid = next(i for i, acc in self.tab.accounts.items() if acc['identity'] == identity)
        self.tab.tree.selection_set(iid)
        self.app.update()
        return self.tab.accounts[iid]

    def done(self, acc, action, code=0, cancelled=False):
        with patch('tkinter.messagebox.showinfo'), patch('tkinter.messagebox.showwarning'):
            self.tab._done(acc, action, [('Auftrag', code, [])], cancelled)
        self.app.update()

    def test_initial_view_explains_selection_without_exposing_extra_controls(self):
        self.assertIn('Klicke oben ein Konto an', self.tab.next_step['text'])
        self.assertEqual(str(self.tab.btn['primary']['state']), 'disabled')
        self.assertFalse(self.tab.more.winfo_manager())

    def test_missing_login_primary_starts_the_selected_account(self):
        self.pick('instagram')
        self.tab.btn['primary'].invoke()
        command = self.app.steps[-1][0][0][1]
        self.assertEqual(command[0], 'login')
        self.assertEqual(command[command.index('--identity') + 1], 'instagram_main')
        self.assertEqual(command[command.index('--mode') + 1], 'qr')

    def test_saved_login_is_neutral_and_primary_checks_it(self):
        self.cookie('tiktok_main')
        self.tab.refresh()
        self.pick()
        self.assertIn('noch nicht geprüft', self.tab.account_state['text'])
        self.assertEqual(self.tab.tree.item(self.tab.tree.selection()[0], 'tags'), ('saved',))
        self.tab.btn['primary'].invoke()
        self.assertEqual(self.app.steps[-1][0][0][1][0], 'check')

    def test_confirmed_login_leads_to_fetching(self):
        self.cookie('tiktok_main')
        acc = self.pick()
        self.done(acc, 'check')
        self.assertIn('bestätigt', self.tab.account_state['text'])
        self.assertIn('1 bestätigt', self.tab.info['text'])
        self.tab.btn['primary'].invoke()
        self.assertEqual(self.app.selected[-1], 'Abrufen')

    def test_changed_cookie_file_requires_a_new_check(self):
        self.cookie('tiktok_main')
        acc = self.pick()
        self.done(acc, 'check')
        self.cookie('tiktok_main', value='changed-cookie-content')
        self.tab.refresh()
        self.assertIn('noch nicht geprüft', self.tab.account_state['text'])
        self.assertEqual(self.tab.btn['primary']['text'], 'Anmeldung prüfen')
        self.assertNotIn(acc['identity'], self.tab.verified)
        self.assertNotIn('ist gültig', self.tab.next_step['text'])

    def test_network_check_failure_keeps_login_and_offers_another_check(self):
        self.cookie('tiktok_main')
        acc = self.pick()
        path = core.cookie_file(acc['platform'], acc['identity'])
        original = path.read_bytes()
        self.done(acc, 'check', 1)
        self.assertIn('konnte nicht geprüft', self.tab.next_step['text'])
        self.assertNotIn('nicht mehr gültig', self.tab.next_step['text'])
        self.assertEqual(self.tab.btn['primary']['text'], 'Anmeldung prüfen')
        self.assertEqual(path.read_bytes(), original)

    def test_expired_login_offers_login_and_changed_file_clears_old_failure(self):
        self.cookie('tiktok_main')
        acc = self.pick()
        self.done(acc, 'check', 2)
        self.assertEqual(self.tab.btn['primary']['text'], 'Im Browser anmelden')
        self.assertIn('Erneut anmelden', self.tab.account_state['text'])
        self.cookie('tiktok_main', value='replacement-cookie-content')
        self.tab.refresh()
        self.assertEqual(self.tab.btn['primary']['text'], 'Anmeldung prüfen')
        self.assertNotIn('nicht mehr gültig', self.tab.next_step['text'])

    def test_import_is_not_confirmation_and_cancellation_does_not_add_confirmation(self):
        self.cookie('tiktok_main')
        acc = self.pick()
        self.done(acc, 'check')
        self.done(acc, 'import')
        self.assertNotIn(acc['identity'], self.tab.verified)
        self.assertEqual(self.tab.btn['primary']['text'], 'Anmeldung prüfen')
        self.done(acc, 'check', cancelled=True)
        self.assertNotIn(acc['identity'], self.tab.verified)
        self.assertIn('Abgebrochen', self.tab.next_step['text'])

    def test_selection_changes_do_not_confirm_another_account(self):
        self.cookie('tiktok_main')
        self.cookie('instagram_main', 'instagram')
        acc = self.pick()
        self.tab.primary()
        self.pick('instagram')
        with patch('tkinter.messagebox.showinfo'):
            self.finish(0)
        self.app.update()
        self.assertIn(acc['identity'], self.tab.verified)
        self.assertNotIn('instagram_main', self.tab.verified)
        self.assertIn('noch nicht geprüft', self.tab.account_state['text'])

    def test_more_options_are_available_and_primary_does_not_start_during_a_job(self):
        self.pick()
        self.tab.more_btn.invoke()
        self.assertEqual(self.tab.more.winfo_manager(), 'grid')
        for key in ('rename', 'default', 'remove', 'login', 'check', 'logout', 'login_pw', 'import', 'creds'):
            self.assertEqual(str(self.tab.btn[key]['state']), 'normal')
        self.app.job = object()
        self.tab.primary()
        self.assertEqual(self.app.steps, [])
        self.app.job = None

    def test_anonymous_mode_explains_blocked_login(self):
        with patch.object(proxylogic, 'current_mode', return_value='tor'):
            self.tab.refresh()
            self.pick()
        self.assertEqual(str(self.tab.btn['primary']['state']), 'disabled')
        self.assertIn('Einstellungen', self.tab.next_step['text'])
        self.assertIn('keine Konten benutzt', self.tab.next_step['text'])

    def test_long_account_name_wraps_at_the_minimum_desktop_width(self):
        account = logic.add_account(self.db, 'tiktok', 'W' * 60)
        self.tab.refresh()
        self.tab.tree.selection_set(str(account['id']))
        self.app.geometry('860x620')
        self.app.update()
        self.tab.toggle_more()
        self.app.update()
        self.assertLessEqual(self.tab.account_name.winfo_reqwidth(), self.tab.winfo_width())
        self.assertLessEqual(self.tab.more.winfo_reqwidth(), self.tab.winfo_width())


class CheckClassificationTests(unittest.TestCase):
    def test_unknown_probe_is_a_check_error_not_an_expired_login(self):
        with patch('socialscraper.net.session_guard.ensure', return_value=State.ERROR):
            with self.assertRaises(login.LoginError) as caught:
                login.check(Mock(), Mock(), 'synthetic')
        self.assertIn('konnte nicht geprüft', str(caught.exception))
        self.assertNotIn('abgelaufen', str(caught.exception))
        for state in (State.EXPIRED, State.MISSING):
            with patch('socialscraper.net.session_guard.ensure', return_value=state):
                self.assertFalse(login.check(Mock(), Mock(), 'synthetic'))

    def test_cli_distinguishes_check_failure_from_an_invalid_session(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            cfg = Settings(db_path=str(pathlib.Path(folder) / 'scrape.db'), notify_file='')
            for state, expected in ((State.ERROR, 1), (State.EXPIRED, 2), (State.MISSING, 2), (State.OK, 0)):
                with patch.object(cli, 'load', return_value=cfg), \
                     patch.object(cli, '_ctx_and_backend', return_value=(Mock(), Mock(), None)), \
                     patch('socialscraper.net.session_guard.ensure', return_value=state), \
                     contextlib.redirect_stdout(io.StringIO()):
                    code = cli.main(['check', '--platform', 'tiktok', '--identity', 'synthetic'])
                self.assertEqual(code, expected, state)
