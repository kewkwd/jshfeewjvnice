import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from socialscraper.working_rules import RULES
from socialscraper.ui.working_rules import WorkingRulesTab
from socialscraper.ui.postfaecher import PostfaecherTab
from socialscraper.ui.app import TABS
from socialscraper.ui.workflow_design import NAV


class WorkingRulesUI(unittest.TestCase):
    def test_shared_rules_are_present_in_navigation_and_render_in_hidden_window(self):
        root = tk.Tk(); root.withdraw(); self.addCleanup(root.destroy)
        app = SimpleNamespace(nb=root)
        page = WorkingRulesTab(app)
        def labels(widget):
            result = []
            for child in widget.winfo_children():
                try:result.append(str(child.cget('text')))
                except tk.TclError:pass
                result.extend(labels(child))
            return result
        text = '\n'.join(labels(page))
        for title, rule, status in RULES:
            self.assertIn(title, text); self.assertIn(rule, text); self.assertIn(status, text)
        self.assertIn(('Arbeitsregeln', WorkingRulesTab), TABS)
        self.assertTrue(any(key == 'Arbeitsregeln' for _, group in NAV for key, _ in group))

    def test_reset_needs_explicit_choice_and_never_starts_a_job(self):
        subject = SimpleNamespace(app=SimpleNamespace(log=Mock(), run_steps=Mock()), provider=SimpleNamespace(get=lambda:'web.de'))
        with patch('socialscraper.ui.workflow_components.busy', return_value=False), \
             patch('socialscraper.ui.postfaecher.messagebox.askyesno', return_value=False), \
             patch('socialscraper.registration_attempts.reset') as reset:
            PostfaecherTab.reset_attempt(subject)
            reset.assert_not_called(); subject.app.run_steps.assert_not_called()
