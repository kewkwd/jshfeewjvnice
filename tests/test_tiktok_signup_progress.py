"""Round 36: continue signup after a manually resolved CAPTCHA, entirely offline."""
import contextlib
import re
import socket
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import signup
from socialscraper.net.errors import FinalError
from socialscraper.signup import tiktok


class _Clock:
    def __init__(self):
        self.now = 100.0
        self.reads = 0

    def monotonic(self):
        self.reads += 1
        if self.reads > 2000:
            raise AssertionError('Unbounded signup polling')
        return self.now

    def advance(self, milliseconds):
        if not 0 <= milliseconds <= 30000:
            raise AssertionError('Unexpected browser wait')
        self.now += milliseconds / 1000
        if self.now > 200:
            raise AssertionError('Signup exceeded the fixture deadline')


class _Element:
    def __init__(self, page, tag, *, visible=True, enabled=True, value='', **attributes):
        self.page, self.tag = page, tag
        self.visible, self.enabled, self.value = visible, enabled, value
        self.attributes = attributes

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def is_editable(self):
        return self.enabled and self.visible and self.tag == 'input'

    def get_attribute(self, name):
        return self.attributes.get(name)

    def input_value(self):
        return self.value

    def evaluate(self, expression):
        if 'tagName' in expression:
            return self.tag.upper()
        raise AssertionError('Unexpected element script')

    def fill(self, value, **_kwargs):
        if not self.is_editable():
            raise RuntimeError('Field is hidden or disabled')
        self.value = str(value)
        self.page.fills.append((self.attributes.get('name'), self.value, self.page.clock.now))

    def click(self, **_kwargs):
        if not self.visible or not self.enabled or self.tag != 'button':
            raise AssertionError('Unexpected automated field or CAPTCHA action')
        self.page.submit()


class _Locator:
    def __init__(self, page, selector, index=0):
        self.page, self.selector, self.index = page, selector, index

    @property
    def first(self):
        return self.nth(0)

    def nth(self, index):
        return _Locator(self.page, self.selector, index)

    def count(self):
        return len(self.page.matching(self.selector))

    def __getattr__(self, name):
        matches = self.page.matching(self.selector)
        if self.index >= len(matches):
            raise RuntimeError('No matching field')
        return getattr(matches[self.index], name)


class _Page:
    url = 'https://www.tiktok.com/signup/email'

    def __init__(self, clock, *, captcha_seconds=0, otp=True, visible=True, enabled=True,
                 numeric_year=False, success=True):
        self.clock, self.captcha_seconds, self.otp, self.success = clock, captcha_seconds, otp, success
        self.numeric_year = numeric_year
        self.started = None
        self.verified = False
        self.submits, self.fills = [], []
        self.mouse = MagicMock()
        self.email = _Element(self, 'input', name='email', type='email')
        self.password = _Element(self, 'input', name='password', type='password')
        self.code = _Element(self, 'input', visible=visible, enabled=enabled,
                             name='code', type='text', autocomplete='one-time-code', inputmode='numeric')
        self.year = _Element(self, 'input', name='birthYear', type='text', inputmode='numeric', value='1995')
        self.button = _Element(self, 'button', type='submit')
        self.captcha = _Element(self, 'div', id='captcha-verify-image', **{'class': 'captcha_verify_container'})

    def elements(self):
        if self.started is None:
            return [self.email, self.password, self.button]
        if self.clock.now < self.started + self.captcha_seconds:
            return [self.captcha]
        return [self.button] + ([self.code] if self.otp else []) + ([self.year] if self.numeric_year else [])

    def matching(self, selector):
        # A deliberately small CSS fixture: standard attribute selectors and
        # visible controls, sufficient for the real Ctx fill/click boundary.
        if ',' in selector:
            return [element for element in self.elements()
                    if any(element in self.matching(part.strip()) for part in selector.split(','))]
        tag = re.match(r'^([a-z]+)', selector)
        attributes = re.findall(r'\[([\w-]+)(\*?=)["\']([^"\']+)["\'](?:\s+i)?\]', selector)
        result = []
        for element in self.elements():
            if tag and element.tag != tag.group(1):
                continue
            if selector.startswith('#') and element.attributes.get('id') != selector[1:]:
                continue
            if selector.startswith('.') and selector[1:] not in element.attributes.get('class', '').split():
                continue
            if ':visible' in selector and not element.visible:
                continue
            if any((element.attributes.get(key) != value if operator == '=' else
                    value not in element.attributes.get(key, '')) for key, operator, value in attributes):
                continue
            result.append(element)
        return result

    def locator(self, selector):
        return _Locator(self, selector)

    def query_selector(self, selector):
        matches = self.matching(selector)
        return matches[0] if matches else None

    def click(self, selector, **kwargs):
        element = self.query_selector(selector)
        if element is None:
            raise RuntimeError('Button not present')
        element.click(**kwargs)

    def select_option(self, *_args, **_kwargs):
        raise RuntimeError('No select in this signup fixture')

    def evaluate(self, script, _argument=None):
        if script == signup._CLICK_TEXT_JS:
            return None  # No cookie/consent dialog is part of this fixture.
        raise AssertionError('Unexpected page script')

    def wait_for_timeout(self, milliseconds):
        self.clock.advance(milliseconds)

    def submit(self):
        self.submits.append(self.clock.now)
        if self.started is None:
            self.started = self.clock.now
        elif self.clock.now >= self.started + self.captcha_seconds and self.code.value == '123456':
            self.verified = True


class _Backend:
    def __init__(self, page):
        self.current_page = page

    def page(self, _handle):
        return self.current_page

    def read_cookies(self, _handle):
        if self.current_page.verified and self.current_page.success:
            return [{'name': 'sessionid', 'value': 'OFFLINE_TEST_ONLY', 'domain': '.tiktok.com', 'path': '/'}]
        return []


class _Mailbox:
    def __init__(self, clock):
        self.read_at = []

        def code(**_kwargs):
            self.read_at.append(clock.now)
            return '123456', {'stamp': clock.now}

        self.wait_for_code = MagicMock(side_effect=code)


class TikTokSignupProgressTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))
        self.clock = _Clock()
        stack.enter_context(patch.object(signup.time, 'monotonic', side_effect=self.clock.monotonic))
        stack.enter_context(patch.object(signup.time, 'time', side_effect=lambda: self.clock.now))
        stack.enter_context(patch.object(signup.time, 'sleep', side_effect=AssertionError('Real sleep blocked')))

    def flow(self, **kwargs):
        page = _Page(self.clock, **kwargs)
        mailbox = _Mailbox(self.clock)
        ctx = signup.Ctx(platform='tiktok', email='test@example.test', password='FAKE_PASSWORD',
                         username='test', mode='auto', country='de', mobile=True, timeout=12,
                         backend=_Backend(page), box=mailbox, melden=MagicMock())
        return page, ctx, mailbox

    def assert_code_submitted_once(self, page, mailbox):
        mailbox.wait_for_code.assert_called_once()
        self.assertEqual([value for name, value, _time in page.fills if name == 'code'], ['123456'])
        self.assertEqual(len(page.submits), 2)
        self.assertEqual(page.mouse.mock_calls, [])
        self.assertTrue(page.verified)

    def test_code_appearing_after_manual_captcha_is_completed_without_restart(self):
        page, ctx, mailbox = self.flow(captcha_seconds=6)
        cookies = tiktok.run(page, ctx)
        self.assertEqual(cookies[0]['name'], 'sessionid')
        self.assert_code_submitted_once(page, mailbox)
        self.assertGreaterEqual(mailbox.read_at[0], page.started + 6)
        self.assertTrue(any('CAPTCHA' in str(call.args[0]) for call in ctx.melden.call_args_list))

    def test_immediately_available_code_is_filled_and_submitted_once(self):
        page, ctx, mailbox = self.flow()
        cookies = tiktok.run(page, ctx)
        self.assertEqual(cookies[0]['value'], 'OFFLINE_TEST_ONLY')
        self.assert_code_submitted_once(page, mailbox)

    def test_immediate_success_after_code_submit_is_kept_with_short_timeout(self):
        page, ctx, mailbox = self.flow()
        ctx.timeout = 1
        cookies = tiktok.run(page, ctx)
        self.assertEqual(cookies[0]['value'], 'OFFLINE_TEST_ONLY')
        self.assert_code_submitted_once(page, mailbox)

    def test_hidden_or_disabled_code_field_never_reads_mail_or_submits(self):
        for visible, enabled in ((False, True), (True, False)):
            with self.subTest(visible=visible, enabled=enabled):
                self.clock.now = 100.0
                page, ctx, mailbox = self.flow(visible=visible, enabled=enabled)
                with self.assertRaises(TimeoutError):
                    tiktok.run(page, ctx)
                mailbox.wait_for_code.assert_not_called()
                self.assertFalse(any(name == 'code' for name, _value, _time in page.fills))
                self.assertEqual(len(page.submits), 1)
                self.assertEqual(page.mouse.mock_calls, [])

    def test_numeric_birth_year_is_not_a_verification_code(self):
        page, ctx, mailbox = self.flow(otp=False, numeric_year=True)
        with self.assertRaises(TimeoutError):
            tiktok.run(page, ctx)
        mailbox.wait_for_code.assert_not_called()
        self.assertEqual(page.year.value, '1995')
        self.assertEqual(len(page.submits), 1)

    def test_code_submission_without_authenticated_cookies_times_out(self):
        page, ctx, mailbox = self.flow(success=False)
        with self.assertRaises(TimeoutError):
            tiktok.run(page, ctx)
        self.assert_code_submitted_once(page, mailbox)

    def test_cancellation_during_mail_retrieval_prevents_code_fill_and_submit(self):
        page, ctx, mailbox = self.flow()
        stopped = [False]
        ctx.stop = lambda: stopped[0]
        retrieve = mailbox.wait_for_code.side_effect

        def cancel_during_retrieval(**kwargs):
            stopped[0] = True
            return retrieve(**kwargs)

        mailbox.wait_for_code.side_effect = cancel_during_retrieval
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            tiktok.run(page, ctx)
        mailbox.wait_for_code.assert_called_once()
        self.assertFalse(any(name == 'code' for name, _value, _time in page.fills))
        self.assertEqual(len(page.submits), 1)
        self.assertEqual(page.mouse.mock_calls, [])

    def test_captcha_appearing_during_mail_retrieval_defers_cached_code_until_resolved(self):
        page, ctx, mailbox = self.flow()
        retrieve = mailbox.wait_for_code.side_effect
        manually_resolved_at = []

        def captcha_during_retrieval(**kwargs):
            manually_resolved_at.append(self.clock.now + 4)
            page.captcha_seconds = manually_resolved_at[0] - page.started
            return retrieve(**kwargs)

        mailbox.wait_for_code.side_effect = captcha_during_retrieval
        cookies = tiktok.run(page, ctx)
        self.assertEqual(cookies[0]['name'], 'sessionid')
        self.assert_code_submitted_once(page, mailbox)
        code_filled_at = [at for name, _value, at in page.fills if name == 'code']
        self.assertGreaterEqual(code_filled_at[0], manually_resolved_at[0])
        self.assertGreaterEqual(page.submits[-1], manually_resolved_at[0])
        self.assertTrue(any('CAPTCHA' in str(call.args[0]) for call in ctx.melden.call_args_list))

    def test_failed_native_fill_retries_cached_code_before_submitting(self):
        page, ctx, mailbox = self.flow()
        original_fill = page.code.fill
        attempts = []

        def transient_fill_failure(value, **kwargs):
            attempts.append(self.clock.now)
            self.assertEqual(len(page.submits), 1, 'Code was submitted before a successful native fill')
            if len(attempts) == 1:
                raise RuntimeError('Field was replaced during native input')
            return original_fill(value, **kwargs)

        with patch.object(page.code, 'fill', side_effect=transient_fill_failure):
            cookies = tiktok.run(page, ctx)
        self.assertEqual(cookies[0]['name'], 'sessionid')
        self.assert_code_submitted_once(page, mailbox)
        self.assertEqual(len(attempts), 2)
        self.assertGreater(attempts[1], attempts[0])

    def test_mail_retrieval_consuming_remaining_time_cannot_fill_or_submit_after_deadline(self):
        page, ctx, mailbox = self.flow()
        retrieve = mailbox.wait_for_code.side_effect

        def retrieval_until_deadline(**kwargs):
            self.assertGreater(kwargs['timeout'], 0)
            self.assertLessEqual(kwargs['timeout'], ctx.timeout)
            self.clock.advance(kwargs['timeout'] * 1000)
            return retrieve(**kwargs)

        mailbox.wait_for_code.side_effect = retrieval_until_deadline
        with self.assertRaises(TimeoutError):
            tiktok.run(page, ctx)
        mailbox.wait_for_code.assert_called_once()
        self.assertFalse(any(name == 'code' for name, _value, _time in page.fills))
        self.assertEqual(len(page.submits), 1)
        self.assertFalse(page.verified)
        self.assertEqual(page.mouse.mock_calls, [])


if __name__ == '__main__':
    unittest.main()
