"""Opt-in inline DOM regressions for duplicate password controls and tracking."""
import contextlib
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from socialscraper.mailanbieter import MailCtx
from socialscraper.net.errors import FinalError


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM') == '1', 'local browser fixture disabled')
class SharedFormPasswordDOM(unittest.TestCase):
    @contextlib.contextmanager
    def browser(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        blocked = {'server': 'http://127.0.0.1:9'}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chrome', proxy=blocked,
                args=LocalBackend._proxy_args(blocked) + ['--disable-background-networking'])
            try:
                yield browser
            finally:
                browser.close()

    @contextlib.contextmanager
    def form(self, browser, first_state):
        context = browser.new_context(service_workers='block', accept_downloads=False)
        try:
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(2000)
            attributes = {'hidden': 'style="display:none"', 'disabled': 'disabled', 'readonly': 'readonly'}
            page.set_content('''<form id="registration">
                <input id="first" name="password" type="password" ''' + attributes[first_state] + '''>
                <input id="selected" name="password" type="password">
                <button type="submit">Weiter</button></form><script>
                document.body.dataset.submits = '0';
                document.addEventListener('submit', event => {
                    event.preventDefault();
                    document.body.dataset.submits = String(Number(document.body.dataset.submits) + 1);
                });
                </script>''')
            ctx = MailCtx(anbieter='web.de', vorname='Social', nachname='Scraper',
                adresse='test123@web.de', passwort='GENERATED_FAKE_PASSWORD', mode='assisted',
                land='de', mobile=False, timeout=30, backend=SimpleNamespace(page=lambda _handle: page),
                domaene='web.de', handle=object(), identity='shared-password-dom-test', melden=MagicMock())
            result = ctx.fill(['input[name="password"]'], ctx.passwort)
            self.assertIsInstance(result, str)
            self.assertEqual(page.locator(result).count(), 1)
            self.assertEqual(page.locator(result).get_attribute('id'), 'selected')
            self.assertEqual(page.locator('#first').input_value(), '')
            self.assertEqual(page.locator('#selected').input_value(), ctx.passwort)
            self.assertTrue(ctx.password_filled)
            ctx.require_known_password()
            yield page, ctx
        finally:
            context.close()

    def assert_unknown_without_secret(self, ctx, replacement):
        with self.assertRaises(FinalError) as error:
            ctx.require_known_password()
        messages = str(error.exception) + str(ctx.notizen) + str(ctx.melden.call_args_list)
        self.assertNotIn(ctx.passwort, messages)
        self.assertNotIn(replacement, messages)

    def test_second_password_control_is_tracked_and_manual_edits_are_preserved(self):
        with self.browser() as browser:
            for state in ('hidden', 'disabled', 'readonly'):
                with self.subTest(first=state), self.form(browser, state) as (page, ctx):
                    # The empty first match must not invalidate a correct submit
                    # of the second field that the shared helper actually filled.
                    page.locator('#registration').dispatch_event('submit')
                    self.assertEqual(page.locator('body').get_attribute('data-submits'), '1')
                    ctx.require_known_password()
                    self.assertTrue(ctx.password_filled)

                    page.locator('#selected').fill('MANUAL_FAKE_PASSWORD')
                    # The public fill boundary flushes pending binding callbacks.
                    ctx.fill(['input[name="password"]'], ctx.passwort)
                    self.assertEqual(page.locator('#selected').input_value(), 'MANUAL_FAKE_PASSWORD')
                    self.assertEqual(page.locator('#first').input_value(), '')
                    self.assertFalse(ctx.password_filled)
                    self.assert_unknown_without_secret(ctx, 'MANUAL_FAKE_PASSWORD')

    def test_submit_detects_silent_change_of_the_selected_password_control(self):
        with self.browser() as browser:
            for state in ('hidden', 'disabled', 'readonly'):
                with self.subTest(first=state), self.form(browser, state) as (page, ctx):
                    # No input/change events: only the guard's submit check can
                    # observe this local fixture mutation before accepting proof.
                    page.locator('#selected').evaluate("element => { element.value = 'SILENT_FAKE_PASSWORD'; }")
                    page.locator('#registration').dispatch_event('submit')
                    self.assertEqual(page.locator('body').get_attribute('data-submits'), '1')
                    self.assert_unknown_without_secret(ctx, 'SILENT_FAKE_PASSWORD')
                    self.assertFalse(ctx.password_filled)
                    ctx.fill(['input[name="password"]'], ctx.passwort)
                    self.assertEqual(page.locator('#selected').input_value(), 'SILENT_FAKE_PASSWORD')
                    self.assertEqual(page.locator('#first').input_value(), '')


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM') == '1', 'local browser fixture disabled')
class SharedClickWaitingDOM(unittest.TestCase):
    def test_click_waits_for_an_appearing_or_newly_enabled_button(self):
        from socialscraper.signup import _click

        # Reuse the same headless Chrome and dead-proxy boundary as the other
        # inline fixtures; neither case loads an external document or resource.
        with SharedFormPasswordDOM.browser(self) as browser:
            for initial_state in ('absent', 'disabled'):
                with self.subTest(initial_state=initial_state):
                    context = browser.new_context(service_workers='block', accept_downloads=False)
                    try:
                        context.route('**/*', lambda route: route.abort())
                        page = context.new_page()
                        page.set_default_timeout(2000)
                        button = '' if initial_state == 'absent' else '<button id="next" disabled>Weiter</button>'
                        page.set_content('<main>' + button + '''</main><script>
                            document.body.dataset.clicks = '0';
                            document.addEventListener('click', event => {
                                if (event.target.id !== 'next') return;
                                document.body.dataset.clicks = String(Number(document.body.dataset.clicks) + 1);
                                document.body.dataset.trustedClick = String(event.isTrusted);
                            });
                            </script>''')
                        page.evaluate('''() => { setTimeout(() => {
                            const existing = document.getElementById('next');
                            if (existing) existing.disabled = false;
                            else {
                                const button = document.createElement('button');
                                button.id = 'next'; button.textContent = 'Weiter';
                                document.querySelector('main').appendChild(button);
                            }
                        }, 100); }''')
                        # No sleep or locator wait: the shared click owns waiting.
                        self.assertEqual(_click(page, ['#next']), '#next')
                        self.assertEqual(page.locator('body').get_attribute('data-clicks'), '1')
                        self.assertEqual(page.locator('body').get_attribute('data-trusted-click'), 'true')
                    finally:
                        context.close()


if __name__ == '__main__':
    unittest.main()
