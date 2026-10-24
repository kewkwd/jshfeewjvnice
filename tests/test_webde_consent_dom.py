"""Opt-in inline DOM regression: WEB.DE consent steps remain entirely manual."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.mailanbieter import unitedinternet as provider
from socialscraper.signup import _click, _fill


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM') == '1', 'local browser fixture disabled')
class WebdeConsentDOM(unittest.TestCase):
    def test_consent_remains_manual_until_the_next_form_section(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend

        blocked = {'server': 'http://127.0.0.1:9'}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chrome', proxy=blocked,
                args=LocalBackend._proxy_args(blocked) + ['--disable-background-networking'])
            try:
                for prechecked in (False, True):
                    with self.subTest(prechecked=prechecked):
                        context = browser.new_context(service_workers='block', accept_downloads=False)
                        try:
                            context.route('**/*', lambda route: route.abort())
                            page = context.new_page()
                            page.set_default_timeout(2000)
                            self.check_form(page, prechecked)
                        finally:
                            context.close()
            finally:
                browser.close()

    def check_form(self, page, prechecked):
        checked = ' checked' if prechecked else ''
        page.set_content('''<main id="step"><form id="person-step">
            <input id="firstName" name="firstName">
            <input id="lastName" name="lastName">
            <span id="consent-control"><input id="agb" name="agb" type="checkbox"'''
            + checked + '''><label for="agb">Testbedingungen selbst prüfen</label></span>
            <button type="submit">Weiter</button></form></main>
            <script>
            for (const key of ['submits', 'consentClicks', 'consentChanges', 'nameInputs']) {
                document.body.dataset[key] = '0';
            }
            function increment(key) {
                document.body.dataset[key] = String(Number(document.body.dataset[key]) + 1);
            }
            document.body.addEventListener('submit', event => {
                event.preventDefault(); increment('submits');
            });
            document.getElementById('consent-control').addEventListener('click', () => increment('consentClicks'));
            document.getElementById('agb').addEventListener('change', () => increment('consentChanges'));
            for (const name of ['firstName', 'lastName']) {
                document.getElementById(name).addEventListener('input', () => increment('nameInputs'));
            }
            </script>''')

        class RegistrationPage:
            # Routing metadata for the real provider; no navigation is performed.
            url = 'https://register.web.de/'

            def __getattr__(self, key):
                return getattr(page, key)

        ctx = SimpleNamespace(anbieter='web.de', domaene='web.de', adresse='test123@web.de',
            vorname='Social', nachname='Scraper', passwort='FAKE_PASSWORD', stop=None,
            hinweis=MagicMock(), warte=page.wait_for_timeout, dismiss=lambda: None,
            fill=lambda selectors, value: _fill(page, selectors, value),
            click=lambda selectors: _click(page, selectors))

        def count(key):
            return page.locator('body').get_attribute('data-' + key)

        def assert_no_consent_action():
            self.assertEqual(count('submits'), '0')
            self.assertEqual(count('consent-clicks'), '0')
            self.assertEqual(count('consent-changes'), '0')

        def guided_wait(_page, _ctx, *, tick):
            self.assertEqual(page.locator('#firstName').input_value(), 'Social')
            self.assertEqual(page.locator('#lastName').input_value(), 'Scraper')
            self.assertTrue(page.locator('#agb').is_visible())
            self.assertTrue(page.locator('label[for="agb"]').is_visible())
            self.assertEqual(page.locator('#agb').is_checked(), prechecked)
            assert_no_consent_action()
            for _ in range(3):
                tick()
                assert_no_consent_action()
                self.assertEqual(page.locator('#agb').is_checked(), prechecked)
            hints = [call.args[0] for call in ctx.hinweis.call_args_list if 'Einwilligung' in call.args[0]]
            self.assertEqual(len(hints), 1)

            # Simulated user edits are confined to this local fixture.
            page.locator('#firstName').fill('Manual correction')
            name_inputs = count('name-inputs')
            page.locator('#agb').evaluate('(element) => { element.checked = true; }')
            tick()
            assert_no_consent_action()
            self.assertEqual(page.locator('#firstName').input_value(), 'Manual correction')
            self.assertEqual(count('name-inputs'), name_inputs)

            # Even removal of both checkbox and label leaves the same section
            # under human control; it must not trigger a deferred auto-submit.
            page.locator('#consent-control').evaluate('(element) => element.remove()')
            for _ in range(3):
                tick()
                assert_no_consent_action()
                self.assertEqual(page.locator('#firstName').input_value(), 'Manual correction')
                self.assertEqual(count('name-inputs'), name_inputs)

            page.locator('#step').evaluate('''element => {
                element.innerHTML = '<form id="password-step">'
                    + '<input id="password" name="password" type="password">'
                    + '<input id="passwordConfirmation" name="passwordConfirmation" type="password">'
                    + '<button type="submit">Weiter</button></form>';
            }''')
            tick()
            self.assertEqual(page.locator('#password').input_value(), 'FAKE_PASSWORD')
            self.assertEqual(page.locator('#passwordConfirmation').input_value(), 'FAKE_PASSWORD')
            self.assertEqual(count('submits'), '1')
            for _ in range(3):
                tick()
                self.assertEqual(count('submits'), '1')
            self.assertEqual(count('consent-clicks'), '0')
            self.assertEqual(count('consent-changes'), '0')

        with patch.object(provider, '_warte_bis_fertig', side_effect=guided_wait):
            result = provider.run(RegistrationPage(), ctx)
        self.assertEqual(result['adresse'], 'test123@web.de')


if __name__ == '__main__':
    unittest.main()
