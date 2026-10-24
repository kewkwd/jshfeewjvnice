"""R46: real headless events and delayed content on isolated inline documents."""
import contextlib
import os
import random
import threading
import time
import unittest
from unittest.mock import patch

from socialscraper.net import humanize


@unittest.skipUnless(os.environ.get('SS_RUN_HUMANIZE_DOM') == '1', 'local browser fixture disabled')
class HumanizeReadinessDOM(unittest.TestCase):
    @contextlib.contextmanager
    def page(self, content):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend

        proxy = {'server': 'http://127.0.0.1:9'}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chrome', proxy=proxy,
                args=LocalBackend._proxy_args(proxy) + ['--disable-background-networking'])
            try:
                context = browser.new_context(viewport={'width': 320, 'height': 480},
                    service_workers='block', accept_downloads=False)
                try:
                    context.route('**/*', lambda route: route.abort())
                    page = context.new_page()
                    page.set_default_timeout(2000)
                    page.set_content(content)
                    with contextlib.ExitStack() as guards:
                        for owner, names in ((page.mouse, ('click', 'dblclick', 'down', 'up')),
                                (page.keyboard, ('down', 'up', 'press', 'type', 'insert_text'))):
                            for name in names:
                                guards.enter_context(patch.object(owner, name,
                                    side_effect=AssertionError('No clicks or text entry in this fixture')))
                        yield page
                finally:
                    context.close()
            finally:
                browser.close()

    def test_delayed_content_is_returned_after_it_is_actually_available(self):
        with self.page('<div id="result"></div>') as page:
            # The browser runs this timer in the real DOM, without navigation.
            page.locator('#result').evaluate('el => setTimeout(() => {el.textContent="READY_FAKE"}, 150)')
            value = humanize.wait_for(page, lambda: page.locator('#result').text_content(),
                timeout=2, interval=.05)
            self.assertEqual(value, 'READY_FAKE')
            self.assertEqual(page.url, 'about:blank')

    def test_missing_content_has_a_finite_wait_and_is_not_reported_as_success(self):
        with self.page('<div id="result"></div>') as page:
            calls = []

            def read():
                calls.append(True)
                return page.locator('#result').text_content()

            started = time.monotonic()
            self.assertIsNone(humanize.wait_for(page, read, timeout=.25, interval=.05))
            self.assertLess(time.monotonic() - started, 3)
            self.assertGreaterEqual(len(calls), 1)
            self.assertLessEqual(len(calls), 7)
            self.assertEqual(page.url, 'about:blank')

    def test_cancellation_during_real_wait_prevents_the_next_dom_read(self):
        with self.page('<div id="result"></div>') as page:
            stopped = threading.Event()
            calls = []
            original = page.wait_for_timeout

            def wait_then_stop(milliseconds):
                original(milliseconds)
                stopped.set()

            def read():
                calls.append(True)
                return page.locator('#result').text_content()

            with patch.object(page, 'wait_for_timeout', side_effect=wait_then_stop):
                self.assertIsNone(humanize.wait_for(page, read, timeout=2, interval=.2,
                    stop=stopped.is_set))
            self.assertEqual(calls, [True])

    def test_wheel_dispatch_is_distinct_from_scroll_completion(self):
        with self.page('''<style>body{margin:0;height:3000px}</style><body><script>
            document.body.dataset.delta = '0';
            document.addEventListener('wheel', event => {
                document.body.dataset.delta = String(Number(document.body.dataset.delta) + event.deltaY);
                event.preventDefault();
            }, {passive:false});
            </script>''') as page:
            page.mouse.move(100, 100)
            self.assertTrue(humanize.scroll(page, 300, rng=random.Random(3)))
            value = humanize.wait_for(page, lambda: page.locator('body').get_attribute('data-delta') == '300',
                timeout=2)
            self.assertTrue(value)
            self.assertEqual(page.evaluate('scrollY'), 0)
            self.assertEqual(page.url, 'about:blank')


if __name__ == '__main__':
    unittest.main()
