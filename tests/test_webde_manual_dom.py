"""Opt-in local DOM regression for mixed manual WEB.DE registration steps."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.mailanbieter import unitedinternet as provider
from socialscraper.signup import _click, _fill


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM') == '1', 'local browser fixture disabled')
class WebdeManualDetailsDOM(unittest.TestCase):
    def test_mixed_personal_form_stays_manual_then_password_step_submits_once(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        blocked = {'server': 'http://127.0.0.1:9'}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chrome', proxy=blocked,
                args=LocalBackend._proxy_args(blocked) + ['--disable-background-networking'])
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.abort())
                page.set_content('''<main id="step"><form id="personal-step">
                    <input id="firstName" name="firstName">
                    <input id="lastName" name="lastName">
                    <input id="postalCode" name="postalCode" value="10115">
                    <select id="salutation" name="salutation">
                        <option value="manual" selected>Keine Angabe</option>
                        <option value="1">Frau</option><option value="2">Herr</option>
                    </select>
                    <button type="submit">Weiter</button>
                    </form></main><script>
                    document.body.dataset.submits='0';
                    document.body.addEventListener('submit', event => {
                        event.preventDefault();
                        document.body.dataset.submits=String(Number(document.body.dataset.submits)+1);
                    });
                    </script>''')

                class RegistrationPage:
                    # Provider routing metadata only; the browser stays on inline HTML.
                    url = 'https://register.web.de/'

                    def __getattr__(self, key):
                        return getattr(page, key)

                ctx = SimpleNamespace(anbieter='web.de', domaene='web.de', adresse='test123@web.de',
                    vorname='Social', nachname='Scraper', passwort='FAKE_PASSWORD', stop=None,
                    hinweis=MagicMock(), warte=page.wait_for_timeout, dismiss=lambda: None,
                    fill=lambda selectors, value: _fill(page, selectors, value),
                    click=lambda selectors: _click(page, selectors))

                def assert_manual_form():
                    self.assertEqual(page.locator('#firstName').input_value(), 'Social')
                    self.assertEqual(page.locator('#lastName').input_value(), 'Scraper')
                    self.assertEqual(page.locator('#postalCode').input_value(), '10115')
                    self.assertEqual(page.locator('#salutation').input_value(), 'manual')
                    self.assertEqual(page.locator('body').get_attribute('data-submits'), '0')
                    hints = [call.args[0] for call in ctx.hinweis.call_args_list if 'Anschrift' in call.args[0]]
                    self.assertEqual(len(hints), 1)
                    self.assertIn('im Fenster', hints[0])

                def guided_wait(_page, _ctx, **kwargs):
                    assert_manual_form()
                    for _ in range(3):
                        kwargs['tick']()
                        assert_manual_form()
                    page.locator('#step').evaluate('''node => {
                        node.innerHTML='<form id="password-step">'
                            + '<input id="password" name="password" type="password">'
                            + '<input id="passwordConfirmation" name="passwordConfirmation" type="password">'
                            + '<button type="submit">Weiter</button></form>';
                    }''')
                    kwargs['tick']()
                    self.assertEqual(page.locator('#password').input_value(), 'FAKE_PASSWORD')
                    self.assertEqual(page.locator('#passwordConfirmation').input_value(), 'FAKE_PASSWORD')
                    self.assertEqual(page.locator('body').get_attribute('data-submits'), '1')
                    for _ in range(3):
                        kwargs['tick']()
                        self.assertEqual(page.locator('body').get_attribute('data-submits'), '1')

                with patch.object(provider, '_warte_bis_fertig', side_effect=guided_wait):
                    provider.run(RegistrationPage(), ctx)
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
