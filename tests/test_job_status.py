"""Completed failures remain visible while the user is looking at the app."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from socialscraper.ui.app import App
from socialscraper.ui import common

class CompletedStatus(unittest.TestCase):
    def test_failure_in_foreground_updates_status_without_flashing(self):
        app=SimpleNamespace(set_status=Mock())
        with patch.object(common,'window_in_front',return_value=True),patch.object(common,'flash_window') as flash:
            App._notify_finished(app,[('Postfach erzeugen',1,[])],False)
        self.assertIn('Postfach erzeugen',app.set_status.call_args.args[0])
        self.assertIn('Probleme',app.set_status.call_args.args[0])
        self.assertEqual(app.set_status.call_args.args[1],'err')
        flash.assert_not_called()

    def test_success_in_foreground_preserves_specific_completion_text(self):
        app=SimpleNamespace(set_status=Mock())
        with patch.object(common,'window_in_front',return_value=True),patch.object(common,'flash_window') as flash:
            App._notify_finished(app,[('Postfach erzeugen',0,[])],False)
        app.set_status.assert_not_called();flash.assert_not_called()
