"""Regressions found while starting the authorized single-profile live flow."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from socialscraper import repo
from socialscraper.store import Store
from socialscraper.ui.forms import ScrollFrame


class ProfileConsistency(unittest.TestCase):
    def test_deleted_default_accounts_do_not_reappear_on_refresh(self):
        with Store(':memory:') as store:
            repo.ensure_default_accounts(store.db)
            accounts = repo.list_accounts(store.db)
            self.assertTrue(accounts)
            for account in accounts:repo.delete_account(store.db, account['id'])
            repo.ensure_default_accounts(store.db)
            self.assertEqual(repo.list_accounts(store.db), [])

    def test_hidden_scrollbar_is_not_regridded_when_already_managed(self):
        bar=Mock();bar.winfo_manager.return_value='grid';bar.winfo_ismapped.return_value=False
        subject=SimpleNamespace(body=SimpleNamespace(winfo_reqheight=lambda:600),
            canvas=SimpleNamespace(winfo_height=lambda:300),bar=bar)
        ScrollFrame._toggle_bar(subject)
        bar.grid.assert_not_called();bar.grid_remove.assert_not_called()

    def test_unneeded_hidden_scrollbar_is_removed_from_geometry(self):
        bar=Mock();bar.winfo_manager.return_value='grid';bar.winfo_ismapped.return_value=False
        subject=SimpleNamespace(body=SimpleNamespace(winfo_reqheight=lambda:100),
            canvas=SimpleNamespace(winfo_height=lambda:300,yview_moveto=Mock()),bar=bar)
        ScrollFrame._toggle_bar(subject)
        bar.grid_remove.assert_called_once()
