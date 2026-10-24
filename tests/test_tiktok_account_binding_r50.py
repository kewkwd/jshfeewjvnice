"""Selected-account binding without any UI, provider, or emulator operation."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import accountvault
from socialscraper.ui import tab_accounts
import test_avd_lifecycle_r49 as lifecycle


class AccountBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)/'fixture.sqlite'
        self.account = {'identity': 'local_account_key', 'platform': 'tiktok'}

    def tab(self):
        return SimpleNamespace(_need_account=lambda: self.account, _db=lambda: self.db,
            app=SimpleNamespace(set_status=MagicMock()),
            attempt=lambda _label, function, *args, **kwargs: (True, function(*args, **kwargs)))

    def test_provider_handle_is_saved_separately_without_overwriting_credentials(self):
        accountvault.put(self.db, 'local_account_key', email='fixture@example.test', passwort='fake-password')
        accountvault.put(self.db, 'local_account_key', tiktok_username=' @Actual.Handle_1 ')
        saved = accountvault.get(self.db, 'local_account_key')
        self.assertEqual(saved['tiktok_username'], 'actual.handle_1')
        self.assertEqual(saved['passwort'], 'fake-password')
        self.assertEqual(saved['email'], 'fixture@example.test')
        self.assertNotEqual(saved['tiktok_username'], 'local_account_key')

    def test_invalid_handle_does_not_change_existing_vault_or_echo_input(self):
        accountvault.put(self.db, 'local_account_key', tiktok_username='known.account')
        before = accountvault.path_for(self.db).read_bytes()
        for bad in ('https://example.test/@private', 'private@example.test', '@@private',
                    'private.', 'Private Name', 'x'*129, 17):
            with self.subTest(kind=type(bad).__name__), self.assertRaises(ValueError) as error:
                accountvault.put(self.db, 'local_account_key', tiktok_username=bad)
            self.assertNotIn(str(bad), str(error.exception))
            self.assertEqual(accountvault.path_for(self.db).read_bytes(), before)

    def test_required_handle_does_not_infer_local_account_name(self):
        for missing in (None, '', '   '):
            with self.assertRaisesRegex(ValueError, 'TikTok-Benutzername'):
                accountvault.normalize_tiktok_username(missing, required=True)

    def test_dialog_saves_only_selected_account_after_validation(self):
        accountvault.put(self.db, 'other_account', tiktok_username='other.provider')
        def form(_parent, _title, fields, **options):
            before = accountvault.path_for(self.db).read_bytes()
            self.assertIsNotNone(options['validator']({'username': 'Display Name'}))
            self.assertIsNone(options['validator']({'username': '@Selected.Provider'}))
            self.assertEqual(accountvault.path_for(self.db).read_bytes(), before)
            self.assertEqual(fields[0]['value'], '')
            return {'username': '@Selected.Provider'}
        with patch.object(tab_accounts, 'app_busy', return_value=False), \
                patch.object(tab_accounts, 'ask_form', side_effect=form):
            tab_accounts.AccountsTab.set_tiktok_username(self.tab())
        self.assertEqual(accountvault.get(self.db, 'local_account_key')['tiktok_username'], 'selected.provider')
        self.assertEqual(accountvault.get(self.db, 'other_account')['tiktok_username'], 'other.provider')

    def test_cancel_or_new_busy_state_does_not_save(self):
        for answer, states in ((None, [False]), ({'username': 'fixture'}, [False, True])):
            with patch.object(tab_accounts, 'app_busy', side_effect=states), \
                    patch.object(tab_accounts, 'ask_form', return_value=answer), \
                    patch.object(accountvault, 'put') as save:
                tab_accounts.AccountsTab.set_tiktok_username(self.tab())
            save.assert_not_called()

    def test_other_platform_or_busy_account_does_not_open_dialog(self):
        for platform, busy in (('reddit', False), ('tiktok', True)):
            self.account['platform'] = platform
            with patch.object(tab_accounts, 'app_busy', return_value=busy), \
                    patch.object(tab_accounts, 'ask_form') as form:
                tab_accounts.AccountsTab.set_tiktok_username(self.tab())
            form.assert_not_called()


class WarmupBindingTests(unittest.TestCase):
    def fixture(self):
        fixture = lifecycle.AvdLifecycleTests('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        return fixture

    def test_saved_provider_handle_reaches_app_context(self):
        fixture = self.fixture()
        account = fixture.account(expired=False)
        fixture.credentials.return_value['tiktok_username'] = '@Known.Provider'
        result = fixture.run_warmup(konto_id=account['id'])
        self.assertEqual(result['sitzungen'], 1)
        ctx = fixture.app_login.call_args.args[1]
        self.assertEqual(ctx.tiktok_username, 'known.provider')
        self.assertNotEqual(ctx.tiktok_username, ctx.identity)

    def test_missing_handle_stops_before_emulator_and_mailbox(self):
        fixture = self.fixture()
        account = fixture.account(expired=False)
        fixture.credentials.return_value.pop('tiktok_username')
        result = fixture.run_warmup(konto_id=account['id'])
        self.assertEqual(result['fehler'], 1)
        fixture.emulator.assert_not_called()
        fixture.mailbox_build.assert_not_called()
        fixture.app_session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
