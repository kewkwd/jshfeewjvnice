"""Registration helpers against real, entirely local Chrome DOM fixtures."""
import unittest
from unittest.mock import MagicMock
from socialscraper.signup import _fill, _passwords, _select

class SignupDOM(unittest.TestCase):
    def test_current_fields_password_confirmation_and_calendar_values(self):
        from patchright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True,
                proxy={'server':'http://127.0.0.1:9'},args=['--disable-quic',
                  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'])
            try:
                page=browser.new_page();page.route('**/*',lambda route:route.abort())
                page.set_content('<label>Handynummer oder E-Mail-Adresse<input id="mail"></label>'
                    '<input name="Passwd" type="password"><input name="PasswdAgain" type="password">'
                    '<select id="year"><option value="">Choose</option><option value="1995">1995</option></select>')
                self.assertTrue(_fill(page,['label:has-text("Handynummer oder E-Mail-Adresse") input'],'fake@example.test'))
                ctx=MagicMock();ctx.fill.side_effect=lambda selectors,value:_fill(page,selectors,value)
                self.assertTrue(_passwords(ctx,['input[name="Passwd"]','input[name="PasswdAgain"]'],'FAKE'))
                self.assertEqual(page.locator('input[type=password]').evaluate_all('(xs)=>xs.map(x=>x.value)'),['FAKE','FAKE'])
                self.assertIsNone(_fill(page,['#year'],'1995'))
                self.assertTrue(_select(page,['#year'],1995))
                self.assertEqual(page.locator('#year').input_value(),'1995')
                page.set_content('''<div id="month" role="combobox" onclick="document.querySelector('#options').hidden=false">Monat</div>
                    <div id="options" role="listbox" hidden>
                    <div role="option" data-value="1" onclick="document.querySelector('#month').textContent=this.textContent;this.parentNode.hidden=true">Januar</div>
                    <div role="option" data-value="2" onclick="document.querySelector('#month').textContent=this.textContent;this.parentNode.hidden=true">Februar</div></div>''')
                self.assertTrue(_select(page, ['select#month', '#month[role="combobox"]'], 2))
                self.assertEqual(page.locator('#month').inner_text(), 'Februar')
                self.assertFalse(_select(page, ['#month'], 99))
                self.assertEqual(page.locator('#month').inner_text(), 'Februar')
                from socialscraper.mailanbieter.gmail import _credentials, _erstes, SEL
                page.set_content('<input id="phoneNumberId" hidden><input name="Username"><button>Weiter</button>')
                self.assertIsNone(_erstes(page, SEL['handy']))
                ctx.adresse='expected@gmail.com';ctx.passwort='FAKE';ctx.password_filled=False
                ctx.click.side_effect=lambda selectors:page.locator(selectors[0]).click() or selectors[0]
                state={'username_submitted':False, 'choice_selected':False}
                # A local URL fixture distinguishes existing-address verification
                # from creation without ever contacting Google.
                page.route('https://local.invalid/**',lambda route:route.fulfill(content_type='text/html',body='<input name="Username"><button>Sie haben keine E‑Mail-Adresse oder Telefonnummer?</button>'))
                page.goto('https://local.invalid/collectemailphone')
                _credentials(page,ctx,state)
                self.assertEqual(page.locator('input[name=Username]').input_value(),'')
                self.assertTrue(state['email_route_selected'])
                self.assertFalse(state['username_submitted'])
                page.goto('about:blank')
                page.set_content('''<label onclick="document.querySelector('input').hidden=false">Gmail-Adresse erstellen</label>
                    <input name="Username" hidden><button>Weiter</button>''')
                _credentials(page,ctx,state)
                self.assertTrue(state['choice_selected'])
                _credentials(page,ctx,state)
                self.assertEqual(page.locator('input[name=Username]').input_value(),'expected')
                self.assertTrue(state['username_submitted'])
                page.set_content('<input name="Passwd" type="password"><input name="PasswdAgain" type="password"><button>Weiter</button>')
                _credentials(page,ctx,state)
                self.assertEqual(page.locator('input[type=password]').evaluate_all('(xs)=>xs.map(x=>x.value)'),['FAKE','FAKE'])
            finally:
                browser.close()
