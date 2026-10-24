"""R46: fail safely at helper boundaries; no claim of anti-bot bypass."""
import contextlib
import os
import random
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from socialscraper.net import humanize


class _Page:
    def __init__(self):
        self.viewport_size = {'width': 320, 'height': 480}
        self.actual_viewport = {'width': 20, 'height': 30}
        self.viewport_error = None
        self.reads, self.moves, self.wheels, self.waits = [], [], [], []
        self.on_read = self.on_wait = None
        self.now = 100.0
        self.mouse = SimpleNamespace(move=self.move, wheel=self.wheel)

    def evaluate(self, script):
        if script != '({width:innerWidth,height:innerHeight})':
            raise AssertionError('Only a read-only viewport query is permitted')
        if len(self.reads) >= 80:
            raise AssertionError('Viewport read bound exceeded')
        self.reads.append(script)
        if self.on_read:
            self.on_read()
        if self.viewport_error:
            raise self.viewport_error
        return self.actual_viewport

    def move(self, x, y):
        if len(self.moves) >= 40:
            raise AssertionError('Mouse movement bound exceeded')
        self.moves.append((x, y))

    def wheel(self, x, y):
        # This is the test's emergency stop, not an assumed product limit.
        if len(self.wheels) >= 8:
            raise AssertionError('Scroll did not reject an unusable distance before repeated actions')
        self.wheels.append((x, y))

    def wait_for_timeout(self, milliseconds):
        if len(self.waits) >= 200:
            raise AssertionError('Fake wait bound exceeded')
        self.waits.append(milliseconds)
        self.now += milliseconds / 1000
        if self.on_wait:
            self.on_wait()


class HumanizeOperationBoundaries(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        for method in ('connect', 'connect_ex', 'sendto', 'bind'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch.object(humanize.time, 'sleep', side_effect=AssertionError('Real sleep blocked')))
        self.stack.enter_context(patch.dict(os.environ, {'SS_PACE': 'on', 'SS_NO_PACING': ''}))
        humanize._pos.clear()
        self.addCleanup(humanize._pos.clear)

    def test_unusable_finite_scroll_distance_is_rejected_before_mouse_actions(self):
        # Existing providers use small fixed distances. This protects the public
        # helper API: subtracting 180..420 from 1e20 makes no float progress.
        for total in (1e20, -1e20):
            with self.subTest(total=total):
                page = _Page()
                with self.assertRaises(ValueError):
                    humanize.scroll(page, total, rng=random.Random(2))
                self.assertEqual((page.wheels, page.waits), ([], []))

    def test_finite_pacing_components_cannot_overflow_to_an_infinite_delay(self):
        bounds = {'low': 1e308, 'high': 1e308, 'break_chance': 1,
                  'break_range': (1e308, 1e308)}
        with self.assertRaises(ValueError):
            humanize.pace_delay(random.Random(2), bounds)

    def test_cancellation_during_first_viewport_read_prevents_a_second_browser_command(self):
        page, stopped = _Page(), threading.Event()
        page.viewport_size = None
        page.on_read = stopped.set
        self.assertFalse(humanize.move(page, 10, 20, duration=350,
            rng=random.Random(2), stop=stopped.is_set))
        self.assertEqual(len(page.reads), 1)
        self.assertEqual((page.moves, page.waits), ([], []))

    def test_invalid_configured_viewport_falls_back_to_valid_actual_dimensions(self):
        invalid_sizes = ({}, {'width': 0, 'height': 30},
                         {'width': float('nan'), 'height': 30}, {'height': 30})
        for invalid in invalid_sizes:
            with self.subTest(metadata=invalid):
                page = _Page()
                page.viewport_size = invalid
                self.assertTrue(humanize.move(page, 500, 600, duration=0, rng=random.Random(2)))
                self.assertGreater(len(page.reads), 0)
                self.assertTrue(all(0 <= x < 20 and 0 <= y < 30 for x, y in page.moves))
                self.assertEqual(page.moves[-1], (19, 29))

    def test_unreadable_viewport_prevents_unbounded_mouse_coordinates(self):
        for metadata in (None, {}, {'width': 0, 'height': 0}):
            with self.subTest(metadata=metadata):
                page = _Page()
                page.viewport_size = metadata
                page.viewport_error = RuntimeError('Offline viewport unavailable')
                self.assertFalse(humanize.move(page, 500, 600, duration=0, rng=random.Random(2)))
                self.assertEqual((page.moves, page.waits), ([], []))

    def test_viewport_loss_during_motion_stops_before_another_mouse_command(self):
        page = _Page()

        def lose_viewport():
            page.viewport_size = None
            page.viewport_error = RuntimeError('Offline navigation removed viewport access')

        page.on_wait = lose_viewport
        self.assertFalse(humanize.move(page, 300, 400, duration=350, rng=random.Random(2)))
        self.assertEqual(len(page.moves), 1)
        self.assertEqual(len(page.waits), 1)


if __name__ == '__main__':
    unittest.main()
