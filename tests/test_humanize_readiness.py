"""Bounded state polling and delayed TikTok hydration, entirely offline."""
import contextlib
import socket
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.adapters.base import ScrapeContext
from socialscraper.adapters.tiktok import TikTokAdapter, VIDEO_JS
from socialscraper.config import Settings
from socialscraper.net import humanize


class _Page:
    def __init__(self, *, clock_advances=True):
        self.now = 100.0
        self.clock_advances = clock_advances
        self.waits = []
        self.on_wait = None

    def clock(self):
        return self.now

    def wait_for_timeout(self, milliseconds):
        if len(self.waits) >= 100:
            raise AssertionError('Readiness wait exceeded the offline fixture limit')
        self.waits.append(milliseconds)
        if self.clock_advances:
            self.now += milliseconds / 1000
        if self.on_wait:
            self.on_wait()


class _TikTokPage(_Page):
    def __init__(self, values, *, body=''):
        super().__init__()
        self.values = iter(values)
        self.body = body
        self.url = 'about:blank'
        self.navigations = []
        self.metadata_reads = 0
        self.body_reads = 0

    def goto(self, url, **options):
        self.navigations.append((url, options))
        self.url = url

    def evaluate(self, expression):
        if expression == VIDEO_JS:
            self.metadata_reads += 1
            if self.metadata_reads > 25:
                raise AssertionError('Hydration polling exceeded its bounded fixture limit')
            return next(self.values, None)
        if expression == "() => (document.body && document.body.innerText) || ''":
            self.body_reads += 1
            return self.body
        raise AssertionError('Unexpected browser query in metadata-only fixture')


class _Sessions:
    def __init__(self, page):
        self.page = page
        self.acquired = self.closed = 0

    @contextlib.contextmanager
    def acquire(self, *args):
        self.acquired += 1
        try:
            yield self.page
        finally:
            self.closed += 1


class HumanizeReadinessTests(unittest.TestCase):
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

    def wait_for(self, page, condition, **options):
        function = getattr(humanize, 'wait_for', None)
        self.assertTrue(callable(function), 'Shared bounded readiness polling is missing')
        return function(page, condition, clock=page.clock, **options)

    def assert_small_waits(self, page):
        self.assertTrue(all(0 < wait <= 100.000001 for wait in page.waits))

    def test_late_value_is_returned_without_losing_the_actual_condition_result(self):
        page = _Page()
        expected = {'ready': 'fixture'}
        condition = MagicMock(side_effect=[None, False, expected])
        self.assertIs(self.wait_for(page, condition, timeout=1, interval=.2), expected)
        self.assertEqual(condition.call_count, 3)
        self.assertTrue(page.waits)
        self.assertLessEqual(sum(page.waits), 400.000001)
        self.assert_small_waits(page)

    def test_immediately_ready_value_needs_no_sleep(self):
        page = _Page()
        expected = object()
        condition = MagicMock(return_value=expected)
        self.assertIs(self.wait_for(page, condition, timeout=4), expected)
        condition.assert_called_once_with()
        self.assertEqual(page.waits, [])

    def test_timeout_is_bounded_with_advancing_and_inert_fake_clocks(self):
        for advances in (True, False):
            with self.subTest(clock_advances=advances):
                page = _Page(clock_advances=advances)
                condition = MagicMock(return_value=None)
                self.assertIsNone(self.wait_for(page, condition, timeout=.45, interval=.2))
                self.assertGreater(condition.call_count, 1)
                self.assertLessEqual(condition.call_count, 4)
                self.assertLessEqual(sum(page.waits), 450.000001)
                self.assertGreater(sum(page.waits), 0)
                self.assert_small_waits(page)

    def test_zero_timeout_performs_exactly_one_read_and_no_wait(self):
        for result in (None, {'ready': True}):
            with self.subTest(result=result):
                page = _Page()
                condition = MagicMock(return_value=result)
                self.assertIs(self.wait_for(page, condition, timeout=0), result)
                condition.assert_called_once_with()
                self.assertEqual(page.waits, [])

    def test_transient_stop_before_poll_prevents_the_condition(self):
        page = _Page()
        signal = [True]

        def stop():
            value, signal[0] = signal[0], False
            return value

        condition = MagicMock(return_value=True)
        self.assertIsNone(self.wait_for(page, condition, stop=stop))
        condition.assert_not_called()
        self.assertEqual(page.waits, [])

    def test_transient_stop_during_predicate_rejects_even_a_ready_value(self):
        for value in (None, {'ready': True}):
            with self.subTest(value=value):
                page = _Page()
                signal = [False]

                def stop():
                    result, signal[0] = signal[0], False
                    return result

                def late_result():
                    signal[0] = True
                    return value

                condition = MagicMock(side_effect=late_result)
                self.assertIsNone(self.wait_for(page, condition, stop=stop))
                condition.assert_called_once_with()
                self.assertEqual(page.waits, [])

    def test_transient_stop_during_wait_prevents_another_poll(self):
        page = _Page()
        signal = [False]

        def stop():
            value, signal[0] = signal[0], False
            return value

        def cancel_during_wait():
            signal[0] = True

        page.on_wait = cancel_during_wait
        condition = MagicMock(side_effect=[None, {'late': True}])
        self.assertIsNone(self.wait_for(page, condition, timeout=1, interval=.3, stop=stop))
        condition.assert_called_once_with()
        self.assertEqual(len(page.waits), 1)
        self.assert_small_waits(page)

    def test_condition_exception_propagates_without_retry_or_wait(self):
        page = _Page()
        original = RuntimeError('Offline readiness failure')
        condition = MagicMock(side_effect=original)
        with self.assertRaises(RuntimeError) as error:
            self.wait_for(page, condition)
        self.assertIs(error.exception, original)
        condition.assert_called_once_with()
        self.assertEqual(page.waits, [])

    def test_invalid_durations_fail_before_page_calls_or_condition(self):
        cases = [('timeout', value) for value in (-1, float('nan'), float('inf'), 'invalid')]
        cases += [('interval', value) for value in (0, -1, float('nan'), float('inf'), 'invalid')]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                page = _Page()
                condition = MagicMock(return_value=True)
                with self.assertRaises(ValueError):
                    self.wait_for(page, condition, **{field: value})
                condition.assert_not_called()
                self.assertEqual(page.waits, [])

    def test_predicate_overhead_consumes_the_monotonic_budget(self):
        page = _Page()

        def slow_condition():
            page.now += .6
            return None

        condition = MagicMock(side_effect=slow_condition)
        self.assertIsNone(self.wait_for(page, condition, timeout=1, interval=.2))
        self.assertEqual(condition.call_count, 2)
        self.assertLessEqual(sum(page.waits), 200.000001)
        self.assertLessEqual(page.now - 100, 1.400001)

    def test_truthy_condition_result_after_deadline_is_not_accepted(self):
        page = _Page()

        def late_ready():
            page.now += .25
            return {'ready': True}

        condition = MagicMock(side_effect=late_ready)
        self.assertIsNone(self.wait_for(page, condition, timeout=.2))
        condition.assert_called_once_with()
        self.assertEqual(page.waits, [])

    def test_oversleep_past_deadline_does_not_evaluate_the_condition_again(self):
        page = _Page()

        def oversleep():
            page.now += 1

        page.on_wait = oversleep
        condition = MagicMock(side_effect=[None, {'ready': True}])
        self.assertIsNone(self.wait_for(page, condition, timeout=.25, interval=.2))
        condition.assert_called_once_with()
        self.assertEqual(len(page.waits), 1)
        self.assert_small_waits(page)

    def fetch(self, page):
        sessions = _Sessions(page)
        ctx = ScrapeContext(Settings(max_comments=0, max_retries=1, require_auth=False), None, None,
                            sessions=sessions, identity='offline-fixture', country='de')
        adapter = TikTokAdapter()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(humanize, 'warmup', return_value=True))
            check = stack.enter_context(patch.object(adapter, '_check_session', wraps=adapter._check_session))
            real_wait = getattr(humanize, 'wait_for', None)
            if callable(real_wait):
                def virtual_wait(page, condition, **options):
                    return real_wait(page, condition, clock=page.clock, **options)
                stack.enter_context(patch.object(humanize, 'wait_for', side_effect=virtual_wait))
            result = adapter.fetch('https://www.tiktok.com/@fixture/video/101', ctx)
            check.assert_called_once_with(page, ctx)
        self.assertEqual((sessions.acquired, sessions.closed), (1, 1))
        self.assertEqual(page.navigations, [('https://www.tiktok.com/@fixture/video/101',
                                            {'wait_until': 'domcontentloaded'})])
        return result

    def test_tiktok_fetch_accepts_late_hydration_without_reloading_or_retrying(self):
        page = _TikTokPage([None, None, {'id': '101', 'author': 'fixture',
                                       'stats': {'playCount': 42, 'commentCount': 0}}])
        result = self.fetch(page)
        self.assertIsNotNone(result.video)
        self.assertEqual((result.video.video_id, result.video.view_count), ('101', 42))
        self.assertEqual(result.comments_status, 'not_requested')
        self.assertEqual(page.metadata_reads, 3)
        self.assertLess(sum(page.waits), 1000)
        self.assert_small_waits(page)

    def test_tiktok_fetch_immediate_hydration_does_not_wait_blindly(self):
        page = _TikTokPage([{'id': '101', 'stats': {'commentCount': 0}}])
        result = self.fetch(page)
        self.assertEqual(result.comments_status, 'not_requested')
        self.assertEqual(page.metadata_reads, 1)
        self.assertEqual(page.waits, [])

    def test_tiktok_blocked_page_returns_unavailable_with_a_bounded_read_budget(self):
        page = _TikTokPage([], body='Drag the slider to verify')
        result = self.fetch(page)
        self.assertIsNone(result.video)
        self.assertEqual(result.comments_status, 'unavailable')
        self.assertIn('CAPTCHA', result.message)
        self.assertEqual(page.body_reads, 1)
        self.assertLessEqual(page.metadata_reads, 25)
        self.assertLessEqual(sum(page.waits), 4000.000001)

    def test_tiktok_transient_login_button_during_hydration_keeps_valid_session(self):
        class HydratingAuthenticatedPage(_TikTokPage):
            hydrated = False
            session_reads = 0

            def evaluate(self, expression):
                if expression == "() => !!document.querySelector('[data-e2e=\"top-login-button\"]')":
                    self.session_reads += 1
                    return not self.hydrated
                value = super().evaluate(expression)
                if expression == VIDEO_JS and value:
                    self.hydrated = True
                return value

        page = HydratingAuthenticatedPage([None, None, {'id': '101', 'stats': {'commentCount': 0}}])
        sessions = _Sessions(page)
        ctx = ScrapeContext(Settings(max_comments=0, max_retries=1, require_auth=False), None, None,
                            sessions=sessions, identity='offline-fixture', country='de', authenticated=True)
        real_wait = humanize.wait_for

        def virtual_wait(page, condition, **options):
            return real_wait(page, condition, clock=page.clock, **options)

        with patch.object(humanize, 'warmup', return_value=True), \
                patch.object(humanize, 'wait_for', side_effect=virtual_wait), \
                patch('socialscraper.net.session_guard.invalidate') as invalidate, \
                patch('socialscraper.net.notify.notify') as notify:
            result = TikTokAdapter().fetch('https://www.tiktok.com/@fixture/video/101', ctx)

        self.assertIsNotNone(result.video)
        self.assertEqual(result.comments_status, 'not_requested')
        self.assertEqual(page.metadata_reads, 3)
        self.assertEqual(page.session_reads, 1)
        self.assertEqual((sessions.acquired, sessions.closed), (1, 1))
        self.assertTrue(ctx.authenticated, 'A transient pre-hydration login button invalidated a valid session')
        invalidate.assert_not_called()
        notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
