"""Observed WEB.DE labels with a different internal radio name; no provider requests."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from socialscraper.mailanbieter import unitedinternet as provider
from socialscraper.net.errors import FinalError


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM')=='1','offline browser fixtures disabled')
class SalutationOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        cls.pw=sync_playwright().start();proxy={'server':'http://127.0.0.1:9'}
        cls.browser=cls.pw.chromium.launch(channel='chrome',headless=True,proxy=proxy,
            args=LocalBackend._proxy_args(proxy)+['--disable-background-networking'])

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()

    def fixture(self,*,hidden=False):
        context=self.browser.new_context(service_workers='block');self.addCleanup(context.close)
        context.route('**/*',lambda route:route.abort())
        page=context.new_page();page.set_default_timeout(1500)
        fields=''.join('<label><input type="radio" name="sex" value="'+value+'">'+label+'</label>'
                       for value,label in (('f','weiblich'),('m','männlich'),('d','divers')))
        if hidden:fields='<label style="display:none"><input type="radio" name="old" value="m">männlich</label>'+fields
        page.set_content('<form>'+fields+'<button type="submit">Weiter</button></form>'
            '<script>document.body.dataset.submits="0";document.addEventListener("submit",e=>{e.preventDefault();document.body.dataset.submits=String(Number(document.body.dataset.submits)+1)})</script>')
        return page

    def test_saved_choices_select_corresponding_visible_radio_with_any_field_name(self):
        for value,expected in (('Herr','m'),('Frau','f'),('Divers','d')):
            page=self.fixture(hidden=True)
            self.assertTrue(provider._has_salutation(page))
            self.assertTrue(provider._salutation(page,value,lambda:None))
            self.assertEqual(page.locator('[name="sex"]:checked').input_value(),expected)
            self.assertEqual(page.locator('[name="old"]:checked').count(),0)

    def test_unavailable_option_never_invents_replacement_or_claims_preselection_as_manual(self):
        page=self.fixture()
        self.assertFalse(provider._salutation(page,'Keine Angabe',lambda:None))
        self.assertEqual(page.locator(':checked').count(),0)
        page.get_by_role('radio',name='divers',exact=True).check()
        self.assertFalse(provider._salutation(page,'Keine Angabe',lambda:None))
        self.assertFalse(provider._salutation(page,'',lambda:None))
        self.assertEqual(page.locator(':checked').input_value(),'d')

    def test_unknown_radio_is_not_personal_details(self):
        page=self.fixture();page.set_content('<label><input type="radio">Newsletter abonnieren</label>')
        self.assertFalse(provider._has_salutation(page))

    def test_disabled_preselected_radio_is_not_a_manual_completion(self):
        page=self.fixture()
        page.locator('[value="d"]').evaluate('(el)=>{el.checked=true;el.disabled=true}')
        self.assertFalse(provider._salutation(page,'Keine Angabe',lambda:None))

    def test_cancellation_prevents_selection(self):
        page=self.fixture()
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):
            provider._salutation(page,'Herr',Mock(side_effect=FinalError('Abgebrochen.')))
        self.assertEqual(page.locator(':checked').count(),0)

    def test_provider_reports_missing_choice_once_then_continues_after_manual_selection(self):
        for initial in (None,'d'):
            with self.subTest(initial=initial):self.verify_manual_transition(initial)

    def verify_manual_transition(self,initial):
        page=self.fixture()
        if initial:page.locator('[value="'+initial+'"]').check()
        class RegistrationPage:
            url='https://register.web.de/'
            def __getattr__(self,key):return getattr(page,key)
        from socialscraper.signup import _click
        ctx=SimpleNamespace(anbieter='web.de',domaene='web.de',adresse='fixture@web.de',
            vorname='Fixture',nachname='Person',passwort='FAKE_ONLY',stop=None,
            registrierungsdaten={'anrede':'Keine Angabe'},hinweis=Mock(),warte=lambda ms:None,
            dismiss=lambda:None,fill=Mock(),click=lambda selectors:_click(page,selectors))
        def waiting(_page,_ctx,*,tick):
            for _ in range(3):tick()
            self.assertEqual(page.locator('body').get_attribute('data-submits'),'0')
            notices=[call.args[0] for call in ctx.hinweis.call_args_list if 'Keine automatische Ersatzwahl' in call.args[0]]
            self.assertEqual(len(notices),1)
            page.get_by_role('radio',name='männlich',exact=True).check()
            tick();tick()
            self.assertEqual(page.locator('body').get_attribute('data-submits'),'1')
            self.assertEqual(page.locator(':checked').input_value(),'m')
        with patch.object(provider,'_warte_bis_fertig',side_effect=waiting):provider.run(RegistrationPage(),ctx)
        ctx.fill.assert_not_called()
