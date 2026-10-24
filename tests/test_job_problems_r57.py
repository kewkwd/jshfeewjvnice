"""Local failed commands must be visible even without a database run record."""
import datetime as dt
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from socialscraper import problemlogic, repo
from socialscraper.ui.app import App
from socialscraper.ui import common, problems_window


class JobProblemTests(unittest.TestCase):
    def notify(self, app, results, cancelled=False):
        with patch.object(common, 'window_in_front', return_value=True):
            App._notify_finished(app, results, cancelled)

    def test_failed_job_is_visible_with_an_empty_database_list(self):
        root = tk.Tk(); root.withdraw(); self.addCleanup(root.destroy)
        root.set_status = Mock()
        self.notify(root, [('Postfach erzeugen', 1, ['ERROR mail | Registrierung fehlgeschlagen'])])
        with patch.object(problems_window, 'show_centered'):
            window = problems_window.ProblemsWindow(root, [])
        self.assertEqual(len(window.items), 1)
        self.assertIn('Postfach erzeugen', window.items[0]['what'])
        self.assertIn('Ursache ist unbekannt', window.items[0]['cause'])
        self.assertNotIn('nichts Auffälliges', window.info.cget('text'))
        window.show([], RuntimeError('PRIVATE_DATABASE_ERROR'))
        self.assertEqual(len(window.items), 1)
        self.assertIn('nicht lesen', window.info.cget('text'))
        self.assertNotIn('PRIVATE', window.info.cget('text'))

    def test_cancel_or_success_does_not_create_failure(self):
        for result, cancelled in (([('ok', 0, [])], False), ([('cancel', 1, [])], True)):
            app = SimpleNamespace(set_status=Mock())
            self.notify(app, result, cancelled)
            self.assertFalse(getattr(app, '_job_problems', []))

    def test_progress_hints_and_unknown_secret_errors_are_not_exposed(self):
        items = problemlogic.from_job_results([('Postfach erzeugen', 1,
            ['INFO CAPTCHA und Proxy', 'ERROR unrecognized token=PRIVATE_SECRET'])], repo.now())
        self.assertEqual(len(items), 1)
        self.assertNotIn('PRIVATE', str(items)); self.assertNotIn('CAPTCHA', items[0]['cause'])
        self.assertNotIn('Proxy', items[0]['cause'])

    def test_session_list_is_bounded_and_deduplicates_refreshes(self):
        now = repo.now()
        items = []
        for i in range(80):
            items += problemlogic.from_job_results([(str(i), 1, [])], now-dt.timedelta(seconds=i))
        old = problemlogic.from_job_results([('old', 1, [])], now-dt.timedelta(days=8))
        merged = problemlogic.with_session_failures(items, items+old)
        self.assertEqual(len(merged), problemlogic.MAX_ITEMS)
        self.assertTrue(merged[0]['what'].startswith('0 '))
        self.assertFalse(any(item['what'].startswith('old') for item in merged))


if __name__ == '__main__': unittest.main()
