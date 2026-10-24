"""R42: cancellation and viewport robustness, using only an inert fake page."""
import contextlib
import os
import random
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from socialscraper.net import humanize


class _OnceStop:
    """An edge-triggered cancellation callback, armed by an actual fake action."""

    def __init__(self):
        self.pending = False
        self.seen = 0

    def arm(self):
        self.pending = True

    def __call__(self):
        value, self.pending = self.pending, False
        self.seen += int(value)
        return value


class _Page:
    def __init__(self, width=320, height=480):
        self.viewport_size = {'width': width, 'height': height}
        self.evaluated_viewport = None
        self.moves, self.wheels, self.waits, self.urls = [], [], [], []
        self.move_bounds = []
        self.on_move = self.on_wheel = self.on_wait = self.on_goto = None
        self.mouse = SimpleNamespace(move=self._move, wheel=self._wheel)

    def _move(self, x, y):
        if len(self.moves) >= 100:
            raise AssertionError('Unexpected unbounded mouse movement')
        self.moves.append((x, y))
        viewport = self.viewport_size or self.evaluated_viewport
        self.move_bounds.append((x, y, dict(viewport)))
        if self.on_move:
            self.on_move()

    def _wheel(self, x, y):
        if len(self.wheels) >= 100:
            raise AssertionError('Unexpected unbounded scrolling')
        self.wheels.append((x, y))
        if self.on_wheel:
            self.on_wheel()

    def wait_for_timeout(self, milliseconds):
        if len(self.waits) >= 1000:
            raise AssertionError('Unexpected unbounded waiting')
        self.waits.append(milliseconds)
        if self.on_wait:
            self.on_wait()

    def goto(self, url, **_kwargs):
        self.urls.append(url)
        if self.on_goto:
            self.on_goto()

    def evaluate(self, expression):
        if expression != '({width:innerWidth,height:innerHeight})':
            raise AssertionError('Only a read-only viewport query is allowed')
        return self.evaluated_viewport


class HumanizeReliabilityTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for method in ('socket', 'create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))
        stack.enter_context(patch.object(humanize.time, 'sleep', side_effect=AssertionError('Real wait blocked')))
        stack.enter_context(patch.dict(os.environ, {'SS_PACE': 'on', 'SS_NO_PACING': ''}))
        humanize._last_fetch.clear()
        humanize._pos.clear()
        self.addCleanup(humanize._last_fetch.clear)
        self.addCleanup(humanize._pos.clear)

    def test_once_cancel_during_pace_preserves_previous_fetch_time(self):
        stop = _OnceStop()
        sleeps = []
        humanize._last_fetch['tiktok'] = 10.0

        def sleep(seconds):
            sleeps.append(seconds)
            stop.arm()

        waited = humanize.pace('tiktok', rng=random.Random(2), sleep=sleep,
                               clock=lambda: 20.0, stop=stop)
        self.assertEqual(sleeps, [.1])
        self.assertEqual(waited, .1)
        self.assertEqual(stop.seen, 1)
        self.assertEqual(humanize._last_fetch['tiktok'], 10.0)

    def test_once_cancel_during_mouse_move_prevents_further_movement(self):
        page, stop = _Page(), _OnceStop()
        page.on_move = lambda: stop.arm() if len(page.moves) == 1 else None
        result = humanize.move(page, 300, 400, duration=350,
                               rng=random.Random(2), stop=stop)
        self.assertEqual(stop.seen, 1)
        self.assertFalse(result)
        self.assertEqual(len(page.moves), 1)
        self.assertEqual(page.waits, [])
        self.assertEqual(humanize._pos[page], page.moves[0])

    def test_once_cancel_during_wheel_prevents_further_scroll(self):
        page, stop = _Page(), _OnceStop()
        page.on_wheel = lambda: stop.arm() if len(page.wheels) == 1 else None
        result = humanize.scroll(page, 1200, rng=random.Random(2), stop=stop)
        self.assertEqual(stop.seen, 1)
        self.assertFalse(result)
        self.assertEqual(len(page.wheels), 1)
        self.assertEqual(page.waits, [])

    def test_once_cancel_during_navigation_prevents_warmup_interaction(self):
        page, stop = _Page(), _OnceStop()
        page.on_goto = stop.arm
        result = humanize.warmup(page, 'about:blank', dwell=2500,
                                 rng=random.Random(2), stop=stop)
        self.assertEqual(page.urls, ['about:blank'])
        self.assertEqual(stop.seen, 1)
        self.assertFalse(result)
        self.assertEqual((page.moves, page.wheels, page.waits), ([], [], []))

    def test_resize_during_move_keeps_each_new_position_in_current_viewport(self):
        page = _Page()

        def resize():
            page.viewport_size = {'width': 80, 'height': 120}

        page.on_wait = resize
        self.assertTrue(humanize.move(page, 300, 400, duration=350, rng=random.Random(2)))
        self.assertGreater(len(page.moves), 1)
        for x, y, viewport in page.move_bounds:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLess(x, viewport['width'])
            self.assertLess(y, viewport['height'])

    def test_missing_configured_viewport_uses_read_only_actual_dimensions(self):
        page = _Page()
        page.viewport_size = None
        page.evaluated_viewport = {'width': 20, 'height': 30}
        self.assertTrue(humanize.move(page, 400, -10, duration=0, rng=random.Random(2)))
        self.assertTrue(all(0 <= x < 20 and 0 <= y < 30 for x, y in page.moves))
        self.assertEqual(page.moves[-1], (19, 0))
        self.assertEqual((page.urls, page.wheels), ([], []))


if __name__ == '__main__':
    unittest.main()
