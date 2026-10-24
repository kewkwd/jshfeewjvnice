"""Assisted search-result clicks must obey the same destination checks."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from socialscraper import entry_rules
from socialscraper.net.errors import FinalError
from test_entry_frame_changes_r62 import Page


class ManualNavigation(unittest.TestCase):
    def run_case(self, destination, *, before_search=False, success=False, module=entry_rules):
        page = Page()
        original_goto = page.goto
        if before_search:
            page.goto = lambda url, **kw: original_goto(destination)
        def wait(ms):
            page.clock += ms / 1000
            if page.searches:original_goto(destination)
        ctx = SimpleNamespace(timeout=3, check_cancelled=Mock(), hinweis=Mock(), warte=wait)
        with patch.object(module.time, 'monotonic', side_effect=lambda:page.clock):
            if success:
                module.visit_home(page, 'web.de', ctx)
                self.assertLess(ctx.timeout, 3)
            else:
                with self.assertRaises(FinalError):module.visit_home(page, 'web.de', ctx)
        self.assertEqual(page.clicks, 0)
        self.assertEqual(page.searches, 0 if before_search else 1)

    def test_user_clicked_official_homepage_after_search(self):
        for url in ('https://web.de/', 'https://www.web.de/'):
            with self.subTest(url=url):self.run_case(url, success=True)

    def test_manual_click_does_not_allow_foreign_hosts(self):
        for url in ('https://web.de.evil.invalid/', 'https://evil.invalid/'):
            with self.subTest(url=url):self.run_case(url)

    def test_manual_click_does_not_allow_insecure_or_credential_urls(self):
        for url in ('http://web.de/', 'https://web.de:444/', 'https://user:fixture@web.de/'):
            with self.subTest(url=url):self.run_case(url)

    def test_signup_and_deep_links_still_cannot_skip_homepage(self):
        for url in ('https://register.web.de/', 'https://web.de/email/tarifvergleich/'):
            with self.subTest(url=url):self.run_case(url)

    def test_homepage_before_search_is_not_accepted(self):
        self.run_case('https://web.de/', before_search=True)
