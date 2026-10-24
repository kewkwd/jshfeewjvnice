"""Opt-in DOM tests for manual edits after MailCtx fills a generated password."""
import contextlib
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from socialscraper.mailanbieter import MailCtx
from socialscraper.net.errors import FinalError


PASSWORD_FORM = '''<form>
    <input name="password" type="password">
    <input name="passwordConfirmation" type="password">
    </form>'''


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM') == '1', 'local browser fixture disabled')
class MailPasswordTrackingDOM(unittest.TestCase):
    @contextlib.contextmanager
    def browser_page(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        blocked = {'server': 'http://127.0.0.1:9'}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chrome', proxy=blocked,
                args=LocalBackend._proxy_args(blocked) + ['--disable-background-networking'])
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.abort())
                yield page
            finally:
                browser.close()

    def filled_context(self, page, *, replace_document=True):
        if replace_document:
            page.set_content(PASSWORD_FORM)
        ctx = MailCtx(anbieter='web.de', vorname='Social', nachname='Scraper',
            adresse='test123@web.de', passwort='GENERATED_FAKE_PASSWORD', mode='assisted',
            land='de', mobile=False, timeout=30, backend=SimpleNamespace(page=lambda _handle: page),
            domaene='web.de', handle=object(), identity='password-dom-test', melden=MagicMock())
        ctx.fill(['input[name="password"]'], ctx.passwort)
        ctx.fill(['input[name="passwordConfirmation"]'], ctx.passwort)
        self.assertEqual(page.locator('input[name="password"]').input_value(), ctx.passwort)
        self.assertEqual(page.locator('input[name="passwordConfirmation"]').input_value(), ctx.passwort)
        self.assertTrue(ctx.password_filled)
        return ctx

    def assert_unknown_password_without_secret(self, ctx, manual_secret=None):
        with self.assertRaises(FinalError) as failure:
            ctx.require_known_password()
        messages = str(failure.exception) + str(ctx.notizen) + str(ctx.melden.call_args_list)
        self.assertNotIn(ctx.passwort, messages)
        if manual_secret:
            self.assertNotIn(manual_secret, messages)

    def test_manual_password_change_invalidates_tracking_and_survives_retry(self):
        with self.browser_page() as page:
            for name in ('password', 'passwordConfirmation'):
                with self.subTest(field=name):
                    ctx = self.filled_context(page)
                    selector = f'input[name="{name}"]'
                    page.locator(selector).fill('MANUAL_FAKE')
                    ctx.fill([selector], ctx.passwort)
                    self.assertEqual(page.locator(selector).input_value(), 'MANUAL_FAKE')
                    self.assertFalse(ctx.password_filled)
                    self.assert_unknown_password_without_secret(ctx, 'MANUAL_FAKE')

    def test_manual_password_clear_invalidates_tracking_and_survives_retry(self):
        with self.browser_page() as page:
            for name in ('password', 'passwordConfirmation'):
                with self.subTest(field=name):
                    ctx = self.filled_context(page)
                    selector = f'input[name="{name}"]'
                    page.locator(selector).fill('')
                    ctx.fill([selector], ctx.passwort)
                    self.assertEqual(page.locator(selector).input_value(), '')
                    self.assertFalse(ctx.password_filled)
                    self.assert_unknown_password_without_secret(ctx)

    def test_unchanged_generated_password_remains_known_after_local_navigation(self):
        with self.browser_page() as page:
            ctx = self.filled_context(page)
            ctx.fill(['input[name="password"]'], ctx.passwort)
            ctx.fill(['input[name="passwordConfirmation"]'], ctx.passwort)
            self.assertTrue(ctx.password_filled)
            self.assertEqual(page.locator('input[name="password"]').input_value(), ctx.passwort)
            self.assertEqual(page.locator('input[name="passwordConfirmation"]').input_value(), ctx.passwort)
            ctx.require_known_password()
            # Inline-only transition: there is no provider request or account.
            page.set_content('<main><h1>Testabschluss</h1></main>')
            self.assertTrue(ctx.password_filled)
            ctx.require_known_password()

    def test_password_edits_after_real_document_navigation_are_still_tracked(self):
        first = 'https://registration.example.test/form-1'
        second = 'https://registration.example.test/form-2'
        documents = {first: '<main data-stage="1">' + PASSWORD_FORM + '</main>',
                     second: '<main data-stage="2">' + PASSWORD_FORM + '</main>'}
        fulfilled = []

        def local_document(route):
            url = route.request.url
            if route.request.resource_type != 'document' or url not in documents:
                route.abort()
                return
            fulfilled.append(url)
            route.fulfill(status=200, content_type='text/html; charset=utf-8', body=documents[url])

        with self.browser_page() as page:
            # Newest matching route supplies only these two local documents;
            # the existing catch-all route still aborts every other request.
            page.route('https://registration.example.test/form-*', local_document)
            page.goto(first, wait_until='domcontentloaded', timeout=5000)
            ctx = self.filled_context(page, replace_document=False)
            ctx.require_known_password()
            page.goto(second, wait_until='domcontentloaded', timeout=5000)
            self.assertEqual(page.locator('main').get_attribute('data-stage'), '2')
            self.assertTrue(ctx.password_filled)
            page.locator('input[name="password"]').fill('MANUAL_SECOND_DOCUMENT_FAKE')
            self.assert_unknown_password_without_secret(ctx, 'MANUAL_SECOND_DOCUMENT_FAKE')
            self.assertFalse(ctx.password_filled)
            ctx.fill(['input[name="password"]'], ctx.passwort)
            self.assertEqual(page.locator('input[name="password"]').input_value(), 'MANUAL_SECOND_DOCUMENT_FAKE')
            self.assertFalse(ctx.password_filled)
            self.assertEqual(fulfilled, [first, second])


if __name__ == '__main__':
    unittest.main()
