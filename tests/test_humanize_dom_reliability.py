"""Opt-in R42 mouse regressions on synthetic documents in headless Chrome."""
import contextlib
import json
import os
import random
import unittest
from unittest.mock import patch

from socialscraper.net import humanize


@unittest.skipUnless(os.environ.get('SS_RUN_HUMANIZE_DOM') == '1', 'local browser fixture disabled')
class HumanizeReliabilityDOM(unittest.TestCase):
    @contextlib.contextmanager
    def page(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend

        blocked = {'server': 'http://127.0.0.1:9'}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chrome', proxy=blocked,
                args=LocalBackend._proxy_args(blocked) + ['--disable-background-networking'])
            try:
                context = browser.new_context(viewport={'width': 320, 'height': 480},
                    service_workers='block', accept_downloads=False)
                try:
                    context.route('**/*', lambda route: route.abort())
                    page = context.new_page()
                    page.set_default_timeout(2000)
                    page.set_content('''<style>
                        html,body{margin:0;width:100%;height:100%;overflow:hidden}
                        </style><input id="untouched" value="UNCHANGED_FAKE"><script>
                        document.body.dataset.motionEvents = '[]';
                        document.body.dataset.foreignInputs = '0';
                        document.addEventListener('mousemove', event => {
                            const samples = JSON.parse(document.body.dataset.motionEvents);
                            samples.push({x:event.clientX,y:event.clientY,
                                width:innerWidth,height:innerHeight,trusted:event.isTrusted});
                            document.body.dataset.motionEvents = JSON.stringify(samples);
                        });
                        for (const type of ['click','mousedown','mouseup','keydown','input']) {
                            document.addEventListener(type, () => {
                                document.body.dataset.foreignInputs = String(
                                    Number(document.body.dataset.foreignInputs) + 1);
                            });
                        }
                        </script>''')
                    with contextlib.ExitStack() as guards:
                        for owner, names in ((page.mouse, ('click', 'dblclick', 'down', 'up', 'wheel')),
                                (page.keyboard, ('down', 'up', 'press', 'type', 'insert_text'))):
                            for name in names:
                                guards.enter_context(patch.object(owner, name,
                                    side_effect=AssertionError('Only mouse movement is permitted')))
                        yield page
                        self.assertEqual(page.locator('#untouched').input_value(), 'UNCHANGED_FAKE')
                        self.assertEqual(page.locator('body').get_attribute('data-foreign-inputs'), '0')
                finally:
                    context.close()
            finally:
                browser.close()

    def samples(self, page):
        return json.loads(page.locator('body').get_attribute('data-motion-events'))

    def test_move_respects_a_viewport_reduced_after_its_first_wait(self):
        with self.page() as page:
            original_wait = page.wait_for_timeout
            waits = []

            def wait_then_resize(milliseconds):
                waits.append(milliseconds)
                original_wait(milliseconds)
                if len(waits) == 1:
                    page.set_viewport_size({'width': 80, 'height': 120})

            with patch.object(page, 'wait_for_timeout', side_effect=wait_then_resize):
                self.assertTrue(humanize.move(page, 300, 400, duration=350, rng=random.Random(2)))
            samples = self.samples(page)
            self.assertGreater(len(samples), 1)
            self.assertGreater(len(waits), 1)
            self.assertTrue(any(sample['width'] == 320 for sample in samples))
            self.assertTrue(any(sample['width'] == 80 for sample in samples))
            for sample in samples:
                self.assertTrue(sample['trusted'])
                self.assertGreaterEqual(sample['x'], 0)
                self.assertGreaterEqual(sample['y'], 0)
                self.assertLess(sample['x'], sample['width'])
                self.assertLess(sample['y'], sample['height'])
            self.assertEqual((samples[-1]['x'], samples[-1]['y']), (79, 119))

    def test_once_cancel_after_real_mouse_move_prevents_further_motion_and_wait(self):
        with self.page() as page:
            original_move = page.mouse.move
            pending = [False]
            observed = []

            def stop_once():
                value, pending[0] = pending[0], False
                if value:
                    observed.append(value)
                return value

            def move_then_cancel(x, y, **kwargs):
                original_move(x, y, **kwargs)
                pending[0] = True

            with patch.object(page.mouse, 'move', side_effect=move_then_cancel) as move, \
                    patch.object(page, 'wait_for_timeout', wraps=page.wait_for_timeout) as wait:
                self.assertFalse(humanize.move(page, 300, 400, duration=350,
                    rng=random.Random(2), stop=stop_once))
                self.assertEqual(move.call_count, 1)
                wait.assert_not_called()
            self.assertEqual(observed, [True])
            self.assertFalse(stop_once())
            samples = self.samples(page)
            self.assertEqual(len(samples), 1)
            self.assertTrue(samples[0]['trusted'])


if __name__ == '__main__':
    unittest.main()
