"""An observed WEB.DE home-consent redirect must return to the real homepage."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from socialscraper import entry_rules
from socialscraper.net.errors import FinalError
from test_entry_frame_changes_r62 import Page

class HomeConsentRoute(unittest.TestCase):
    def fixture(self):
        page=Page();original=page.goto
        def goto(url,**kwargs):
            original(url,**kwargs)
            if url=='https://web.de/':page.url='https://web.de/consent-management/'
        page.goto=goto
        self.accepts=0;self.waits=0
        def wait(ms):
            self.waits+=1;page.clock+=ms/1000
            if self.accepts:page.url='https://web.de/'
        ctx=SimpleNamespace(timeout=3,check_cancelled=Mock(),hinweis=Mock(),warte=wait)
        return page,ctx

    def enter(self,page,ctx,accept):
        with patch.object(entry_rules,'accept_known',side_effect=accept), \
             patch.object(entry_rules.time,'monotonic',side_effect=lambda:page.clock):
            entry_rules.visit_home(page,'web.de',ctx)

    def test_homepage_consent_redirect_continues_after_known_consent(self):
        page,ctx=self.fixture()
        def accept(page,ctx,**kwargs):
            if page.url=='https://web.de/consent-management/':self.accepts+=1;return True
            return False
        self.enter(page,ctx,accept)
        self.assertEqual(page.url,'https://web.de/')
        self.assertEqual(self.accepts,1);self.assertEqual(page.clicks,1);self.assertEqual(page.searches,1)

    def test_unresolved_consent_is_bounded_and_is_not_homepage_success(self):
        page,ctx=self.fixture()
        with self.assertRaises(FinalError):self.enter(page,ctx,lambda *a,**kw:False)
        self.assertGreater(self.waits,1);self.assertLessEqual(page.clock,3)
        self.assertEqual(page.clicks,1);self.assertEqual(page.searches,1)
        self.assertTrue(any('WEB.DE' in str(call) for call in ctx.hinweis.call_args_list))

    def test_external_consent_redirect_is_rejected(self):
        page,ctx=self.fixture()
        def accept(page,ctx,**kwargs):
            if page.url=='https://web.de/consent-management/':page.url='https://foreign.example/';return True
            return False
        with self.assertRaisesRegex(FinalError,'HTTPS'):self.enter(page,ctx,accept)

    def test_cancellation_during_consent_prevents_success(self):
        page,ctx=self.fixture()
        def check():
            if self.accepts:raise FinalError('Abgebrochen.')
        ctx.check_cancelled=check
        def accept(page,ctx,**kwargs):
            if page.url=='https://web.de/consent-management/':self.accepts+=1;return True
            return False
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):self.enter(page,ctx,accept)

    def test_manual_consent_can_finish_without_program_click(self):
        page,ctx=self.fixture();original=ctx.warte
        def wait(ms):
            original(ms)
            if page.url=='https://web.de/consent-management/':page.url='https://web.de/'
        ctx.warte=wait
        self.enter(page,ctx,lambda *a,**kw:False)
        self.assertEqual(page.url,'https://web.de/')

    def test_other_deep_paths_still_fail_without_more_navigation(self):
        for path in ('/email/tarifvergleich/','/registrieren','/consent-management/extra'):
            page,ctx=self.fixture();old_goto=page.goto
            def goto(url,**kwargs):
                old_goto(url,**kwargs)
                if url=='https://web.de/':page.url='https://web.de'+path
            page.goto=goto
            with self.assertRaisesRegex(FinalError,'Homepage'):self.enter(page,ctx,lambda *a,**kw:False)

    def test_destination_validation_uses_one_consistent_url_snapshot(self):
        class ChangingPage(Page):
            @property
            def url(self):
                current=self._url
                if getattr(self,'replace_after_read',False):
                    self._url='https://web.de/consent-management/'
                    self.replace_after_read=False
                return current
            @url.setter
            def url(self,value):self._url=value
            def goto(self,url,**kwargs):
                super().goto(url,**kwargs)
                if url=='https://web.de/':self.replace_after_read=True
        page=ChangingPage()
        ctx=SimpleNamespace(timeout=3,check_cancelled=Mock(),hinweis=Mock(),
            warte=lambda ms:setattr(page,'clock',page.clock+ms/1000))
        self.enter(page,ctx,lambda *a,**kw:False)
        self.assertEqual(page._url,'https://web.de/consent-management/')
        self.assertEqual(page.clicks,1)
