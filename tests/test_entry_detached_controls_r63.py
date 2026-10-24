"""Frame replacement during control inspection leaves the navigation gate shut."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from socialscraper import entry_rules
from socialscraper.net.errors import FinalError
from test_entry_frame_changes_r62 import Page


class DetachedControls(unittest.TestCase):
    def test_detached_role_count_keeps_gate_closed_then_reobserves(self):
        page = Page()
        page.get_by_role = Mock(return_value=SimpleNamespace(count=Mock(
            side_effect=RuntimeError('Locator.count: Frame was detached'))))
        self.assertTrue(entry_rules.manual_gate(page))
        page.get_by_role = lambda *a, **kw: SimpleNamespace(count=lambda: 0)
        self.assertFalse(entry_rules.manual_gate(page))

    def test_detached_visibility_keeps_gate_closed(self):
        page = Page()
        node = SimpleNamespace(is_visible=Mock(side_effect=RuntimeError('Frame has been detached')))
        page.get_by_role = lambda *a, **kw: SimpleNamespace(count=lambda: 1, nth=lambda i: node)
        self.assertTrue(entry_rules.manual_gate(page, consent_only=True))

    def test_detached_captcha_count_keeps_gate_closed(self):
        page = Page()
        page.locator = lambda *a: SimpleNamespace(count=Mock(side_effect=RuntimeError('Frame was detached')))
        self.assertTrue(entry_rules.manual_gate(page))

    def test_unrelated_errors_and_cancellation_are_not_swallowed(self):
        for error in (ValueError('bad selector'), FinalError('Abgebrochen.')):
            page = Page()
            page.get_by_role = Mock(side_effect=error)
            with self.assertRaises(type(error)):entry_rules.manual_gate(page)

    def run_entry(self, *, permanent=False, cancel=False):
        page = Page()
        broken = [False]
        page.on_search = lambda: broken.__setitem__(0, True)
        def roles(*a, **kw):
            if broken[0]:raise RuntimeError('Locator.count: Frame was detached')
            return SimpleNamespace(count=lambda: 0)
        page.get_by_role = roles
        def wait(ms):
            page.clock += ms / 1000
            if page.searches and not permanent:broken[0] = False
        def check():
            if cancel and page.searches:raise FinalError('Abgebrochen.')
        ctx = SimpleNamespace(timeout=1,check_cancelled=check,hinweis=Mock(),warte=wait)
        with patch.object(entry_rules.time,'monotonic',side_effect=lambda:page.clock):
            if permanent or cancel:
                with self.assertRaises(FinalError):entry_rules.visit_home(page,'web.de',ctx)
                self.assertEqual(page.clicks,0)
            else:
                entry_rules.visit_home(page,'web.de',ctx)
                self.assertEqual(page.clicks,1)
            self.assertEqual(page.searches,1)
            self.assertLessEqual(page.clock,1)

    def test_replaced_controls_continue_same_search(self):self.run_entry()
    def test_persistent_detachment_times_out_without_provider_click(self):self.run_entry(permanent=True)
    def test_cancellation_stops_without_provider_click(self):self.run_entry(cancel=True)
