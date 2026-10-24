"""Real controls on inline fixtures; all external browser requests blocked."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.mailanbieter import unitedinternet as provider
from socialscraper.signup import _fill, _click


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM')=='1','inline browser fixtures disabled')
class RegistrationDetailsDOM(unittest.TestCase):
    def test_dates_radio_selects_address_and_rejected_input(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        blocked={'server':'http://127.0.0.1:9'}
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,channel='chrome',proxy=blocked,
                args=LocalBackend._proxy_args(blocked)+['--disable-background-networking'])
            try:
                for radio in (False,True):
                    for reject in (False,True):
                        with self.subTest(radio=radio,rejected=reject):
                            context=browser.new_context(service_workers='block',accept_downloads=False)
                            try:
                                context.route('**/*',lambda route:route.abort())
                                page=context.new_page();page.set_default_timeout(2000)
                                choice=('<input name="gender" type="radio" id="none"><label for="none">Keine Angabe</label>' if radio else
                                    '<select name="salutation"><option value="">Auswählen</option><option value="NONE">Keine Angabe</option></select>')
                                page.set_content('<form><input name="firstName"><input name="lastName">'
                                    '<input name="birthDay"><select name="birthMonth"><option value="1">Januar</option><option value="2">Februar</option></select>'
                                    '<input name="birthYear"><input name="postalCode"><input name="city"><input name="street"><input name="houseNumber">'
                                    '<select name="country"><option value="">Auswählen</option><option value="DEU">Deutschland</option></select>'+choice+
                                    '<button type="submit">Weiter</button></form><script>document.body.dataset.submits="0";'
                                    'document.addEventListener("submit",e=>{e.preventDefault();document.body.dataset.submits=String(Number(document.body.dataset.submits)+1)});</script>')
                                if reject:page.locator('[name="postalCode"]').evaluate('(el)=>el.addEventListener("input",()=>el.value="rejected")')
                                class RegistrationPage:
                                    url='https://register.web.de/'
                                    def __getattr__(self,key):return getattr(page,key)
                                details={'geburtsdatum':'1996-02-29','plz':'00000','ort':'Fixturestadt','strasse':'Fixtureweg',
                                         'hausnummer':'12','land':'DE','anrede':'Keine Angabe'}
                                ctx=SimpleNamespace(anbieter='web.de',domaene='web.de',adresse='fixture@web.de',
                                    vorname='Offline',nachname='Fixture',passwort='FAKE_ONLY',registrierungsdaten=details,
                                    stop=None,hinweis=MagicMock(),warte=lambda _ms:None,dismiss=lambda:None,
                                    fill=lambda selectors,value:_fill(page,selectors,value),click=lambda selectors:_click(page,selectors))
                                def wait(_page,_ctx,*,tick):
                                    self.assertEqual(page.locator('[name="birthDay"]').input_value(),'29')
                                    self.assertEqual(page.locator('[name="birthMonth"]').input_value(),'2')
                                    self.assertEqual(page.locator('[name="birthYear"]').input_value(),'1996')
                                    self.assertEqual(page.locator('[name="country"]').input_value(),'DEU')
                                    self.assertEqual(page.locator('[name="street"]').input_value(),'Fixtureweg')
                                    self.assertEqual(page.locator('[name="houseNumber"]').input_value(),'12')
                                    if radio:self.assertTrue(page.locator('#none').is_checked())
                                    else:self.assertEqual(page.locator('[name="salutation"]').input_value(),'NONE')
                                    self.assertEqual(page.locator('body').get_attribute('data-submits'),'0' if reject else '1')
                                    page.locator('[name="city"]').fill('Manual fixture')
                                    tick()
                                    self.assertEqual(page.locator('[name="city"]').input_value(),'Manual fixture')
                                    self.assertEqual(page.locator('body').get_attribute('data-submits'),'0' if reject else '1')
                                with patch.object(provider,'_warte_bis_fertig',side_effect=wait):provider.run(RegistrationPage(),ctx)
                            finally:context.close()
            finally:browser.close()
