"""R43: real channel-list callers must propagate cancellation into humanize."""
import contextlib
import os
import random
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from socialscraper import channel_list
from socialscraper.adapters import tiktok, x
from socialscraper.net import humanize


class _Page:
    def __init__(self):
        self.viewport_size = {'width': 320, 'height': 480}
        self.urls, self.waits, self.moves, self.wheels, self.reads = [], [], [], [], []
        self.links = []
        self.on_goto = self.on_wait = self.on_wheel = self.on_read = None
        self.mouse = SimpleNamespace(move=self.move, wheel=self.wheel)

    def goto(self, url, **_kwargs):
        if len(self.urls) >= 5:
            raise AssertionError('Unexpected navigation loop')
        self.urls.append(url)
        if self.on_goto:
            self.on_goto()

    def wait_for_timeout(self, milliseconds):
        if len(self.waits) >= 100:
            raise AssertionError('Unexpected wait loop')
        self.waits.append(milliseconds)
        if self.on_wait:
            self.on_wait()

    def move(self, px, py):
        if len(self.moves) >= 70:
            raise AssertionError('Unexpected movement loop')
        self.moves.append((px, py))

    def wheel(self, px, py):
        if len(self.wheels) >= 30:
            raise AssertionError('Unexpected scroll loop')
        self.wheels.append((px, py))
        if self.on_wheel:
            self.on_wheel()

    def evaluate(self, script):
        if len(self.reads) >= 30:
            raise AssertionError('Unexpected page-read loop')
        self.reads.append(script)
        if self.on_read:
            self.on_read(script)
        if script == tiktok.TikTokAdapter.STATS_JS:
            return {}
        if script == tiktok.TikTokAdapter.PROFILE_JS:
            return self.links
        return ''


class HumanizeCallerBoundaries(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for method in ('socket', 'create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))
        stack.enter_context(patch.object(humanize.time, 'sleep', side_effect=AssertionError('Real sleep blocked')))
        stack.enter_context(patch.dict(os.environ, {'SS_PACE': 'on', 'SS_NO_PACING': ''}))
        humanize._pos.clear()
        self.addCleanup(humanize._pos.clear)

    @contextlib.contextmanager
    def case(self, platform, *, real_warmup=False):
        page, stopped, batches = _Page(), threading.Event(), []
        acquire = MagicMock(return_value=contextlib.nullcontext(page))
        ctx = SimpleNamespace(cfg=SimpleNamespace(require_auth=False), sessions=SimpleNamespace(acquire=acquire),
            identity='offline-test', country='de', mobile=False, authenticated=True, require_auth=False)
        opts = channel_list.ListOptions(cancelled=stopped.is_set, rng=random.Random(2),
            sleep=lambda seconds: page.wait_for_timeout(seconds * 1000),
            on_items=lambda items, _cursor: batches.extend(items))
        adapter = tiktok.TikTokAdapter() if platform == 'tiktok' else x.XAdapter()
        capture = MagicMock()
        capture.drain.return_value = []
        with contextlib.ExitStack() as stack:
            check = stack.enter_context(patch.object(adapter, '_check_session', return_value=None))
            capture_factory = stack.enter_context(patch.object(x, '_Capture', return_value=capture))
            if not real_warmup:
                stack.enter_context(patch.object(humanize, 'warmup', return_value=True))
            yield SimpleNamespace(page=page, stopped=stopped, batches=batches, acquire=acquire,
                ctx=ctx, opts=opts, adapter=adapter, check=check, capture=capture,
                capture_factory=capture_factory)

    def test_already_cancelled_channel_list_never_acquires_or_navigates(self):
        for platform in ('tiktok', 'x'):
            with self.subTest(platform=platform), self.case(platform) as case:
                case.stopped.set()
                result = case.adapter.channel_items('someone', case.ctx, case.opts)
                self.assertEqual(result.found, 0)
                self.assertFalse(result.exhausted)
                case.acquire.assert_not_called()
                case.capture_factory.assert_not_called()
                self.assertEqual((case.page.urls, case.page.waits, case.page.reads), ([], [], []))

    def test_cancellation_during_tiktok_warmup_prevents_profile_navigation(self):
        with self.case('tiktok', real_warmup=True) as case:
            case.page.on_wait = case.stopped.set
            result = case.adapter.channel_items('someone', case.ctx, case.opts)
            self.assertEqual(result.found, 0)
            self.assertFalse(result.exhausted)
            self.assertEqual(case.page.urls, ['https://www.tiktok.com/'])
            self.assertEqual((case.page.moves, case.page.wheels, case.page.reads), ([], [], []))
            self.assertLessEqual(sum(case.page.waits), 100.0)
            case.check.assert_not_called()

    def test_cancellation_during_navigation_prevents_following_pause_and_reads(self):
        for platform in ('tiktok', 'x'):
            with self.subTest(platform=platform), self.case(platform) as case:
                case.page.on_goto = case.stopped.set
                result = case.adapter.channel_items('someone', case.ctx, case.opts)
                self.assertEqual(result.found, 0)
                self.assertFalse(result.exhausted)
                self.assertEqual(len(case.page.urls), 1)
                self.assertEqual((case.page.waits, case.page.reads, case.page.wheels), ([], [], []))
                case.check.assert_not_called()
                if platform == 'x':
                    case.capture.close.assert_called_once()

    def test_cancellation_during_first_scroll_step_prevents_more_wheels_or_pause(self):
        for platform in ('tiktok', 'x'):
            with self.subTest(platform=platform), self.case(platform) as case:
                waits_at_cancel = []

                def cancel():
                    if not case.stopped.is_set():
                        waits_at_cancel.append(len(case.page.waits))
                    case.stopped.set()

                case.page.on_wheel = cancel
                result = case.adapter.channel_items('someone', case.ctx, case.opts)
                self.assertEqual(result.found, 0)
                self.assertFalse(result.exhausted)
                self.assertEqual(len(case.page.wheels), 1)
                self.assertEqual(len(case.page.waits), waits_at_cancel[0])
                if platform == 'x':
                    case.capture.close.assert_called_once()

    def test_cancellation_after_emitted_batch_keeps_items_without_starting_scroll(self):
        with self.case('tiktok') as case:
            link = 'https://www.tiktok.com/@someone/video/6718335390845095173'
            case.page.links = [link]

            def emit(items, _cursor):
                case.batches.extend(items)
                case.stopped.set()

            case.opts.on_items = emit
            result = case.adapter.channel_items('someone', case.ctx, case.opts)
            self.assertEqual(result.found, 1)
            self.assertFalse(result.exhausted)
            self.assertEqual([item['url'] for item in case.batches], [link])
            self.assertEqual(case.page.wheels, [])

    def test_uncancelled_list_still_emits_its_requested_item(self):
        with self.case('tiktok') as case:
            link = 'https://www.tiktok.com/@someone/video/6718335390845095173'
            case.page.links = [link]
            case.opts.limit = 1
            result = case.adapter.channel_items('someone', case.ctx, case.opts)
            self.assertEqual(result.found, 1)
            self.assertEqual([item['url'] for item in case.batches], [link])
            self.assertEqual(case.page.wheels, [])

    @staticmethod
    def x_payload(count):
        return {'data': {'timeline': {'entries': [
            {'content': {'itemContent': {'tweet_results': {'result': {
                '__typename': 'Tweet', 'rest_id': str(index + 1),
                'legacy': {'full_text': 'Offline test post ' + str(index + 1),
                    'created_at': 'Wed Oct 07 12:30:15 +0000 2026'},
                'core': {'user_results': {'result': {
                    'rest_id': '42', 'legacy': {'screen_name': 'someone'}}}},
            }}}}} for index in range(count)
        ]}}}

    def test_cancellation_during_session_acquire_prevents_any_page_activity(self):
        for platform in ('tiktok', 'x'):
            with self.subTest(platform=platform), self.case(platform) as case:
                exits = []

                @contextlib.contextmanager
                def acquired():
                    case.stopped.set()
                    try:
                        yield case.page
                    finally:
                        exits.append(True)

                case.acquire.return_value = acquired()
                result = case.adapter.channel_items('someone', case.ctx, case.opts)
                self.assertEqual(result.found, 0)
                self.assertFalse(result.exhausted)
                case.acquire.assert_called_once()
                self.assertEqual(exits, [True])
                self.assertEqual((case.page.urls, case.page.waits, case.page.reads), ([], [], []))
                case.check.assert_not_called()
                case.capture_factory.assert_not_called()

    def test_cancellation_in_session_check_prevents_following_page_reads(self):
        for platform, at_check in (('tiktok', 1), ('tiktok', 2), ('x', 1)):
            with self.subTest(platform=platform, at_check=at_check), self.case(platform) as case:

                def check(*_args):
                    if case.check.call_count == at_check:
                        case.stopped.set()

                case.check.side_effect = check
                result = case.adapter.channel_items('someone', case.ctx, case.opts)
                self.assertTrue(case.stopped.is_set())
                self.assertEqual(result.found, 0)
                self.assertFalse(result.exhausted)
                self.assertEqual(case.check.call_count, at_check)
                expected_reads = [tiktok.TikTokAdapter.STATS_JS] if at_check == 2 else []
                self.assertEqual(case.page.reads, expected_reads)
                self.assertEqual(case.page.wheels, [])
                if platform == 'x':
                    case.capture.drain.assert_not_called()
                    case.capture.close.assert_called_once()

    def test_cancellation_during_tiktok_statistics_prevents_list_evaluation(self):
        with self.case('tiktok') as case:
            case.page.on_read = lambda script: case.stopped.set() if script == tiktok.TikTokAdapter.STATS_JS else None
            result = case.adapter.channel_items('someone', case.ctx, case.opts)
            self.assertTrue(case.stopped.is_set())
            self.assertEqual(result.found, 0)
            self.assertFalse(result.exhausted)
            self.assertEqual(case.page.reads, [tiktok.TikTokAdapter.STATS_JS])
            case.check.assert_called_once()
            self.assertEqual(case.page.wheels, [])

    def test_cancellation_in_automatic_thirty_item_flush_prevents_remaining_items(self):
        for platform in ('tiktok', 'x'):
            with self.subTest(platform=platform), self.case(platform) as case:
                if platform == 'tiktok':
                    case.page.links = [f'https://www.tiktok.com/@someone/video/{6718335390845095173 + index}'
                        for index in range(35)]
                else:
                    case.capture.drain.return_value = [('UserTweets', self.x_payload(35))]
                emitted_sizes = []

                def emit(items, _cursor):
                    emitted_sizes.append(len(items))
                    case.batches.extend(items)
                    case.stopped.set()

                case.opts.on_items = emit
                result = case.adapter.channel_items('someone', case.ctx, case.opts)
                self.assertTrue(case.stopped.is_set())
                self.assertEqual(emitted_sizes, [30])
                self.assertEqual(len(case.batches), 30)
                self.assertEqual(result.found, 30)
                self.assertFalse(result.exhausted)
                self.assertEqual(case.page.wheels, [])
                if platform == 'x':
                    case.capture.close.assert_called_once()

    def test_uncancelled_x_list_parses_and_emits_captured_payload(self):
        with self.case('x') as case:
            case.capture.drain.return_value = [('UserTweets', self.x_payload(2))]
            case.opts.limit = 2
            result = case.adapter.channel_items('someone', case.ctx, case.opts)
            self.assertEqual(result.found, 2)
            self.assertEqual([item['url'] for item in case.batches],
                ['https://x.com/someone/status/1', 'https://x.com/someone/status/2'])
            self.assertEqual([item['title'] for item in case.batches],
                ['Offline test post 1', 'Offline test post 2'])
            self.assertEqual(case.page.wheels, [])
            case.capture.drain.assert_called_once()
            case.capture.close.assert_called_once()

    def test_x_capture_is_closed_when_navigation_check_drain_or_emit_fails(self):
        for phase in ('navigation', 'session_check', 'capture_drain', 'emit'):
            with self.subTest(phase=phase), self.case('x') as case:
                error = RuntimeError('Offline test failure')

                def fail(*_args):
                    raise error

                if phase == 'navigation':
                    case.page.on_goto = fail
                elif phase == 'session_check':
                    case.check.side_effect = fail
                elif phase == 'capture_drain':
                    case.capture.drain.side_effect = fail
                else:
                    case.capture.drain.return_value = [('UserTweets', self.x_payload(1))]
                    case.opts.on_items = fail
                with self.assertRaises(RuntimeError) as raised:
                    case.adapter.channel_items('someone', case.ctx, case.opts)
                self.assertIs(raised.exception, error)
                case.capture.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
