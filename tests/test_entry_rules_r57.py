import os
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
from socialscraper import entry_rules
from socialscraper.mailanbieter import webde
from socialscraper.net.errors import FinalError


class EntryValidation(unittest.TestCase):
    def test_unsupported_browser_stops_before_any_external_page(self):
        page = SimpleNamespace(goto=Mock())
        with self.assertRaisesRegex(FinalError, 'unterstützt'):
            entry_rules.visit_home(page, 'web.de', SimpleNamespace())
        page.goto.assert_not_called()

    def test_social_signup_uses_common_entry_and_compatible_context(self):
        from socialscraper import signup
        backend = MagicMock()
        ctx = signup.Ctx('instagram', 'fixture@example.test', 'FAKE', 'fixture', 'assisted', 'de', False, 60, backend)
        provider = SimpleNamespace(REGISTER_URL='https://www.instagram.com/accounts/emailsignup/', run=Mock())
        with patch.object(entry_rules, 'open_registration') as entry, patch.object(signup, '_dismiss'):
            signup._browser_lauf(provider, ctx, object(), None, True)
            entry.assert_called_once_with(backend.page.return_value, 'instagram', provider.REGISTER_URL, ctx)
            self.assertTrue(callable(ctx.hinweis))
            backend.page.return_value.goto.assert_not_called()

    def test_only_official_https_homepage_results(self):
        for url in ('https://web.de/', 'https://www.web.de/', 'https://www.google.de/url?q=https%3A%2F%2Fweb.de%2F'):
            self.assertTrue(entry_rules.homepage_result(url, 'web.de'), url)
        for url in ('http://web.de/', 'https://web.de.evil.invalid/', 'https://evil.invalid/?q=https://web.de/',
                    'https://user:PRIVATE@web.de/', 'https://web.de:444/', 'https://register.web.de/',
                    'https://web.de/email/tarifvergleich/', 'https://web.de/?target=other', None):
            self.assertFalse(entry_rules.homepage_result(url, 'web.de'), str(url))


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM') == '1', 'offline browser fixtures disabled')
class EntryBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        blocked = {'server':'http://127.0.0.1:9'}
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, channel='chrome', proxy=blocked,
            args=LocalBackend._proxy_args(blocked) + ['--disable-background-networking'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()

    def fixture(self, *, privacy=False, result=True, late=False, timeout=5, signup_captcha=False, signup_link=False, home_consent=False, consent_path='/consent-management/', home_button=False, comparison_url='https://web.de/email/tarifvergleich/'):
        context = self.browser.new_context(service_workers='block', accept_downloads=False)
        self.addCleanup(context.close)
        visits = []
        def route_handler(route):
            url = route.request.url
            visits.append(url)
            if url == entry_rules.SEARCH_HOME:
                html = '<form action="/search"><input name="q"><button>Search</button></form>'
                if privacy: html += '<button>Alle akzeptieren</button>'
                if late:html += '<script>document.querySelector("input").oninput=()=>{let b=document.createElement("button");b.textContent="Alle akzeptieren";document.body.append(b)}</script>'
            elif url == 'https://www.google.de/search?q=web.de':
                html = '<a href="https://web.de.evil.invalid/"><h3>WEB.DE</h3></a>'
                if result: html += '<a href="https://web.de/"><h3>WEB.DE Homepage</h3></a>'
            elif url == 'https://web.de/' and home_consent:
                # A client navigation is intercepted before Chromium contacts
                # the deliberately dead upstream proxy used by this fixture.
                html = '<script>location.replace('+json.dumps(consent_path)+')</script>'
            elif url == 'https://web.de'+consent_path:
                html = "<p>Wir verwenden Cookies.</p><button onclick=\"location.href='/?consent=ok'\">Alle akzeptieren</button>"
            elif url in ('https://web.de/','https://web.de/?consent=ok'):
                html = ("<button onclick='location.href="+json.dumps(comparison_url)+"'>Registrieren</button>" if home_button else
                        '<a href="https://register.web.de/">Registrieren</a>' if signup_link else '<a href="/email/tarifvergleich/">Registrieren</a>')
            elif url == comparison_url.split('#',1)[0]:html = '<a href="https://register.web.de/?edition=paid">Paid</a><a href="https://register.web.de/">FreeMail</a>'
            elif url == 'https://register.web.de/':html = '<input name="firstName">' + ('<input name="captcha">' if signup_captcha else '')
            else:
                route.abort(); return
            route.fulfill(status=200, content_type='text/html', body=html)
        context.route('**/*', route_handler)
        page = context.new_page(); page.set_default_timeout(1000)
        ctx = SimpleNamespace(timeout=timeout, hinweis=Mock(), check_cancelled=Mock(), warte=page.wait_for_timeout)
        return page, ctx, visits

    def test_search_home_tariff_registration_are_actual_navigation_in_order(self):
        page, ctx, visits = self.fixture()
        webde.open_signup(page, ctx)
        self.assertEqual(visits, [entry_rules.SEARCH_HOME, 'https://www.google.de/search?q=web.de',
            'https://web.de/', 'https://web.de/email/tarifvergleich/', 'https://register.web.de/'])
        self.assertLess(ctx.timeout, 5)

    def test_home_consent_redirect_returns_to_home_then_registration(self):
        page, ctx, visits = self.fixture(home_consent=True)
        ctx.accept_privacy = True
        try:
            webde.open_signup(page, ctx)
        except Exception as error:
            self.fail(f'Fixture entry failed: {type(error).__name__}; URL={page.url}; visits={visits}')
        self.assertEqual(visits, [entry_rules.SEARCH_HOME, 'https://www.google.de/search?q=web.de',
            'https://web.de/', 'https://web.de/consent-management/', 'https://web.de/?consent=ok',
            'https://web.de/email/tarifvergleich/', 'https://register.web.de/'])
        self.assertEqual(page.url, 'https://register.web.de/')

    def test_mobile_magazine_consent_redirect_returns_to_home(self):
        page, ctx, visits = self.fixture(home_consent=True, consent_path='/magazine/consent-management/')
        ctx.accept_privacy = True
        webde.open_signup(page, ctx)
        self.assertIn('https://web.de/magazine/consent-management/', visits)
        self.assertEqual(page.url, 'https://register.web.de/')

    def test_mobile_homepage_registration_button_follows_observed_navigation(self):
        page, ctx, visits = self.fixture(home_button=True)
        webde.open_signup(page, ctx)
        self.assertEqual(visits, [entry_rules.SEARCH_HOME, 'https://www.google.de/search?q=web.de',
            'https://web.de/', 'https://web.de/email/tarifvergleich/', 'https://register.web.de/'])
        self.assertEqual(page.url, 'https://register.web.de/')

    def test_mobile_comparison_is_classified_after_header_button_with_tracking(self):
        comparison = 'https://produkte.web.de/premium/tarifevergleich-mobile/?useCase=standalone&mc#.pc_page.homepage.mobil.button.registrierung'
        page, ctx, visits = self.fixture(home_button=True, comparison_url=comparison)
        webde.open_signup(page, ctx)
        self.assertIn(comparison.split('#',1)[0], visits)
        self.assertEqual(page.url, 'https://register.web.de/')
        self.assertFalse(any('edition=paid' in url for url in visits))

    def test_missing_result_does_not_directly_request_provider(self):
        page, ctx, visits = self.fixture(result=False, timeout=1.5)
        with self.assertRaises(FinalError): entry_rules.visit_home(page, 'web.de', ctx)
        self.assertEqual(visits, [entry_rules.SEARCH_HOME, 'https://www.google.de/search?q=web.de'])

    def test_signup_captcha_hands_form_to_provider_without_solving_it(self):
        page, ctx, visits = self.fixture(signup_captcha=True, timeout=3)
        webde.open_signup(page, ctx)
        self.assertEqual(visits[-1], 'https://register.web.de/')
        self.assertEqual(page.locator('[name="captcha"]').input_value(), '')

    def test_consent_and_late_consent_are_never_clicked_or_submitted(self):
        for late in (False, True):
            with self.subTest(late=late):
                page, ctx, visits = self.fixture(privacy=not late, late=late, timeout=1)
                with self.assertRaises(FinalError): entry_rules.visit_home(page, 'web.de', ctx)
                self.assertEqual(visits, [entry_rules.SEARCH_HOME])
                self.assertTrue(page.get_by_role('button', name='Alle akzeptieren').is_visible())

    def test_cancel_after_search_prevents_target_click(self):
        page, ctx, visits = self.fixture()
        def cancel():
            if len(visits) > 1: raise FinalError('Abgebrochen.')
        ctx.check_cancelled.side_effect = cancel
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'): entry_rules.visit_home(page, 'web.de', ctx)
        self.assertEqual(len(visits), 2)

    def test_generic_registration_does_not_guess_missing_link(self):
        page, ctx, visits = self.fixture(timeout=1.5)
        with self.assertRaises(FinalError): entry_rules.open_registration(page, 'web.de', 'https://register.web.de/', ctx)
        self.assertEqual(visits[-1], 'https://web.de/')

    def test_generic_registration_follows_existing_official_link(self):
        page, ctx, visits = self.fixture(signup_link=True)
        entry_rules.open_registration(page, 'web.de', 'https://register.web.de/', ctx)
        self.assertEqual(visits, [entry_rules.SEARCH_HOME, 'https://www.google.de/search?q=web.de',
                                 'https://web.de/', 'https://register.web.de/'])
