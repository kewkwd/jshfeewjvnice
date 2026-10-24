"""Document replacement after a manual CAPTCHA must preserve one entry attempt."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from socialscraper import entry_rules
from socialscraper.net.errors import FinalError
from test_entry_frame_changes_r62 import Page

DESTROYED='Locator.count: Execution context was destroyed, most likely because of a navigation'

class ContextChanges(unittest.TestCase):
    def run_entry(self,page,*,cancel=False,timeout=2):
        def check():
            if cancel and page.clock:raise FinalError('Abgebrochen.')
        ctx=SimpleNamespace(timeout=timeout,hinweis=Mock(),check_cancelled=check,
            warte=lambda ms:setattr(page,'clock',page.clock+ms/1000))
        with patch.object(entry_rules.time,'monotonic',side_effect=lambda:page.clock):
            entry_rules.visit_home(page,'web.de',ctx)
        return ctx

    def broken_results(self,*,permanent=False,error=DESTROYED):
        page=Page();original=page.locator;broken=[True]
        def locator(selector):
            nodes=original(selector)
            if selector=='a:has(h3)' and (broken[0] or permanent):
                def count():broken[0]=False;raise RuntimeError(error)
                nodes.count=count
            return nodes
        page.locator=locator
        return page

    def test_context_replacement_keeps_manual_gate_closed(self):
        for error in (DESTROYED,'Cannot find context with specified id'):
            page=Page();page.get_by_role=Mock(side_effect=RuntimeError(error))
            self.assertTrue(entry_rules.manual_gate(page))

    def test_result_scan_reobserves_same_search_after_document_replacement(self):
        page=self.broken_results();self.run_entry(page)
        self.assertEqual(page.searches,1);self.assertEqual(page.clicks,1)
        self.assertEqual(page.visits[-1],'https://web.de/')

    def test_persistent_context_replacement_is_bounded_without_target_click(self):
        page=self.broken_results(permanent=True)
        with self.assertRaises(FinalError):self.run_entry(page,timeout=1)
        self.assertEqual(page.searches,1);self.assertEqual(page.clicks,0)
        self.assertLessEqual(page.clock,1)

    def test_cancellation_during_reobservation_never_clicks_homepage(self):
        page=self.broken_results()
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):self.run_entry(page,cancel=True)
        self.assertEqual(page.clicks,0)

    def test_unknown_browser_error_is_not_retried(self):
        page=self.broken_results(error='Target page, context or browser has been closed')
        with self.assertRaisesRegex(RuntimeError,'closed'):self.run_entry(page)
        self.assertEqual(page.clicks,0)

    def test_completed_search_is_never_resubmitted_after_navigation_error(self):
        page=Page();original=page.locator
        def locator(selector):
            nodes=original(selector)
            if selector.startswith('textarea'):
                node=nodes.nth(0);press=node.press
                def submit(*args,**kwargs):press(*args,**kwargs);raise RuntimeError(DESTROYED)
                node.press=submit;nodes.nth=lambda i:node
            return nodes
        page.locator=locator;self.run_entry(page)
        self.assertEqual(page.searches,1);self.assertEqual(page.clicks,1)

    def test_completed_search_timeout_reobserves_without_resubmission(self):
        from patchright.sync_api import TimeoutError as BrowserTimeout
        page=Page();original=page.locator
        def locator(selector):
            nodes=original(selector)
            if selector.startswith('textarea'):
                node=nodes.nth(0);press=node.press
                def submit(*args,**kwargs):
                    press(*args,**kwargs)
                    raise BrowserTimeout('Navigation response delayed')
                node.press=submit;nodes.nth=lambda i:node
            return nodes
        page.locator=locator;ctx=self.run_entry(page)
        self.assertEqual(page.searches,1);self.assertEqual(page.clicks,1)
        self.assertTrue(any('nicht wiederholt' in call.args[0] for call in ctx.hinweis.call_args_list))

    def test_completed_homepage_click_is_not_repeated_after_navigation_error(self):
        page=Page();original=page.locator
        def locator(selector):
            nodes=original(selector)
            if selector=='a:has(h3)':
                node=nodes.nth(0);click=node.click
                def navigate(**kwargs):click(**kwargs);raise RuntimeError(DESTROYED)
                node.click=navigate;nodes.nth=lambda i:node
            return nodes
        page.locator=locator;self.run_entry(page)
        self.assertEqual(page.clicks,1);self.assertEqual(page.searches,1)

    def test_unexpected_destination_still_stops_after_transition(self):
        page=self.broken_results();original=page.locator
        def locator(selector):
            nodes=original(selector)
            if selector=='a:has(h3)':
                count=nodes.count
                def moved_count():
                    try:return count()
                    except RuntimeError:page.url='https://foreign.example/';raise
                nodes.count=moved_count
            return nodes
        page.locator=locator
        with self.assertRaisesRegex(FinalError,'HTTPS'):self.run_entry(page)
        self.assertEqual(page.clicks,0)
