"""Provider entry navigation without network, form filling or consent clicks."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from socialscraper.mailanbieter import webde
from socialscraper.net.errors import FinalError


class EntryPage:
    def __init__(self):
        self.url = 'about:blank'; self.parent_frame = None; self.frames = [self]
        self.privacy = False; self.error = False; self.form = True; self.redirect = {}
        self.visited = []; self.links = ['https://register.web.de/']
        self.tariffs = ['https://web.de/email/tarifvergleich/']
        self.clock = 0

    def goto(self, url, **_kwargs):
        self.visited.append(url); self.url = self.redirect.get(url, url); self.clock += .2

    def get_by_text(self, _text): return self.group(self.error)
    def get_by_role(self, role, **_kwargs): return self.group(self.privacy and role == 'button')
    def group(self, shown):
        return SimpleNamespace(count=lambda: int(shown), nth=lambda _i: SimpleNamespace(is_visible=lambda: True))
    def locator(self, selector):
        urls = self.tariffs if 'tarifvergleich' in selector else self.links if selector.startswith('a[') else []
        return SimpleNamespace(count=lambda:len(urls), nth=lambda i: SimpleNamespace(
            is_visible=lambda:True, evaluate=lambda _script:urls[i], click=lambda **_kwargs:self.goto(urls[i])))
    def query_selector(self, _selector):
        if self.url == 'https://register.web.de/' and self.form:
            return SimpleNamespace(is_visible=lambda: True)


class WebdeEntryTests(unittest.TestCase):
    def fixture(self, timeout=90):
        page = EntryPage()
        def wait(ms): page.clock += ms / 1000
        ctx = SimpleNamespace(timeout=timeout, check_cancelled=Mock(), hinweis=Mock(), warte=Mock(side_effect=wait),
                              fill=Mock(), click=Mock(), dismiss=Mock())
        self.addCleanup(patch.stopall)
        patch.object(webde.time, 'monotonic', side_effect=lambda: page.clock).start()
        patch.object(webde.entry_rules, 'visit_home', side_effect=lambda p, domain, c:p.goto('https://web.de/')).start()
        return page, ctx

    def test_public_links_are_followed_once_in_order_and_budget_is_preserved(self):
        page, ctx = self.fixture()
        webde.open_signup(page, ctx)
        self.assertEqual(page.visited, ['https://web.de/', 'https://web.de/email/tarifvergleich/', 'https://register.web.de/'])
        self.assertLess(ctx.timeout, 90); self.assertGreater(ctx.timeout, 89)
        ctx.fill.assert_not_called(); ctx.click.assert_not_called(); ctx.dismiss.assert_not_called()

    def test_privacy_waits_for_user_then_navigation_continues_without_click(self):
        page, ctx = self.fixture(); page.privacy = True
        def user_choice(ms):
            self.assertEqual(page.visited, ['https://web.de/'])
            page.clock += ms / 1000; page.privacy = False
        ctx.warte.side_effect = user_choice
        webde.open_signup(page, ctx)
        ctx.click.assert_not_called(); ctx.dismiss.assert_not_called()
        self.assertTrue(any('wartet auf deine Datenschutzauswahl' in c.args[0] for c in ctx.hinweis.call_args_list))

    def test_missing_links_times_out_without_direct_registration_fallback(self):
        page, ctx = self.fixture(timeout=2); page.tariffs = []; page.links = []
        with self.assertRaises(FinalError): webde.open_signup(page, ctx)
        self.assertEqual(page.visited, ['https://web.de/'])

    def test_visible_free_registration_link_on_homepage_is_followed(self):
        page, ctx = self.fixture(); page.tariffs = []
        webde.open_signup(page, ctx)
        self.assertEqual(page.visited, ['https://web.de/', 'https://register.web.de/'])

    def test_manual_comparison_navigation_continues_to_registration(self):
        page, ctx = self.fixture(); page.tariffs = []; page.links = []
        def manual_navigation(ms):
            page.clock += ms / 1000
            page.goto('https://produkte.web.de/premium/tarifevergleich/')
            page.links = ['https://register.web.de/']
        ctx.warte.side_effect = manual_navigation
        webde.open_signup(page, ctx)
        self.assertEqual(page.visited[-1], 'https://register.web.de/')

    def test_missing_link_handoff_keeps_waiting_for_manual_navigation(self):
        page, ctx = self.fixture(timeout=60); page.tariffs = []; page.links = []
        def manual_navigation(ms):
            page.clock += ms / 1000
            if page.clock >= 48:page.goto('https://register.web.de/')
        ctx.warte.side_effect = manual_navigation
        webde.open_signup(page, ctx)
        self.assertEqual(page.visited, ['https://web.de/', 'https://register.web.de/'])
        self.assertTrue(any('wartet weiter' in call.args[0] for call in ctx.hinweis.call_args_list))

    def test_new_owned_intermediate_page_waits_then_recognizes_mobile_comparison(self):
        page, ctx = self.fixture(); page.tariffs = []; page.links = []
        intermediate = 'https://produkte.web.de/premium/new-intermediate/'
        mobile = 'https://produkte.web.de/premium/tarifevergleich-mobile/?useCase=standalone&mc#tracking'
        def manual_navigation(ms):
            page.clock += ms / 1000
            if page.url == 'https://web.de/':page.goto(intermediate)
            elif page.url == intermediate:
                page.goto(mobile);page.links = ['https://register.web.de/']
        ctx.warte.side_effect = manual_navigation
        webde.open_signup(page, ctx)
        self.assertEqual(page.visited, ['https://web.de/', intermediate, mobile, 'https://register.web.de/'])
        self.assertTrue(any('Zwischenseite' in call.args[0] for call in ctx.hinweis.call_args_list))
        ctx.fill.assert_not_called()

    def test_wait_does_not_run_forever_when_no_registration_form_appears(self):
        page, ctx = self.fixture(timeout=2); page.form = False
        with self.assertRaises(FinalError): webde.open_signup(page, ctx)
        self.assertEqual(len(page.visited), 3)
        self.assertLess(page.clock, 3)

    def test_provider_rejection_stops_without_retry(self):
        page, ctx = self.fixture(); page.redirect['https://web.de/'] = 'https://hilfe.web.de/kundenservice/flexreg.html'
        with self.assertRaisesRegex(FinalError, 'Registrierung fehlgeschlagen'): webde.open_signup(page, ctx)
        self.assertEqual(len(page.visited), 1); ctx.warte.assert_not_called()

    def test_unexpected_redirect_stops_before_links_or_form(self):
        page, ctx = self.fixture(); page.redirect['https://web.de/'] = 'https://web.de.evil.invalid/'
        with self.assertRaisesRegex(FinalError, 'HTTPS-Seite'): webde.open_signup(page, ctx)
        self.assertEqual(len(page.visited), 1); ctx.warte.assert_not_called()

    def test_unsafe_or_paid_links_are_not_selected(self):
        page, _ = self.fixture()
        for url in ('http://register.web.de/', 'https://register.web.de.evil.invalid/',
                    'https://user:PRIVATE@register.web.de/', 'https://register.web.de:444/',
                    'https://register.web.de/?edition=paid', 'https://register.web.de/paid'):
            with self.subTest(url=url):
                page.links = [url]
                self.assertIsNone(webde._entry_link(page))

    def test_cancellation_after_navigation_prevents_next_request(self):
        page, ctx = self.fixture()
        def cancel():
            if page.visited: raise FinalError('Abgebrochen.')
        ctx.check_cancelled.side_effect = cancel
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'): webde.open_signup(page, ctx)
        self.assertEqual(len(page.visited), 1); ctx.warte.assert_not_called()

    def test_hidden_privacy_frame_does_not_block_visible_homepage(self):
        page, ctx = self.fixture()
        hidden = EntryPage(); hidden.privacy = True; hidden.parent_frame = page
        hidden.frame_element = lambda: SimpleNamespace(is_visible=lambda: False)
        page.frames.append(hidden)
        webde.open_signup(page, ctx)
        ctx.warte.assert_not_called()


if __name__ == '__main__': unittest.main()


class ProductsEntryValidation(unittest.TestCase):
    def test_mobile_comparison_tracking_does_not_change_page_classification(self):
        url = 'https://produkte.web.de/premium/tarifevergleich-mobile/?useCase=standalone&mc#.pc_page.homepage.mobil.button.registrierung'
        parts = webde._location(url)
        self.assertTrue(webde._comparison(parts))
        page = EntryPage();page.tariffs = [url]
        self.assertIsNotNone(webde._entry_link(page, tariff=True))

    def test_observed_official_product_comparison_is_allowed(self):
        self.assertEqual(webde._location('https://produkte.web.de/premium/tarifevergleich/').hostname,'produkte.web.de')
    def test_other_product_paths_and_unsafe_destinations_are_rejected(self):
        for url in ('https://produkte.web.de/premium/bestellen/','http://produkte.web.de/premium/tarifevergleich/',
                    'https://produkte.web.de.evil.invalid/premium/tarifevergleich/',
                    'https://user:PRIVATE@produkte.web.de/premium/tarifevergleich/',
                    'https://produkte.web.de:444/premium/tarifevergleich/'):
            with self.subTest(url=url),self.assertRaises(FinalError):webde._location(url)
    def test_visible_current_comparison_link_is_followed_without_guessing_url(self):
        page=EntryPage();page.tariffs=['https://produkte.web.de/premium/tarifevergleich/']
        self.assertIsNotNone(webde._entry_link(page,tariff=True))
    def test_product_pages_do_not_make_paid_registration_links_eligible(self):
        page=EntryPage();page.links=['https://register.web.de/?edition=paid']
        self.assertIsNone(webde._entry_link(page))
