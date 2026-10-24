"""Offline native-control fixtures for the shared provider form helpers."""
import contextlib
import re
import socket
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import signup
from socialscraper.net.errors import FinalError
from socialscraper.signup import _click, _fill, _select


class _Control:
    def __init__(self, *, tag='INPUT', visible=True, enabled=True, readonly=False,
                 value='original', role=None, options=(), native_failure=False):
        self.tag, self.visible, self.enabled, self.readonly = tag, visible, enabled, readonly
        self.value, self.role, self.options = value, role, list(options)
        self.native_failure = native_failure
        self.page = self.owner = None
        self.clicked = 0

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def is_editable(self):
        return self.visible and self.enabled and not self.readonly and self.tag in ('INPUT', 'TEXTAREA')

    def get_attribute(self, name):
        return {'role': self.role, 'value': self.value, 'data-value': self.value,
                'readonly': '' if self.readonly else None,
                'aria-readonly': 'true' if self.readonly else None,
                'disabled': '' if not self.enabled else None}.get(name)

    def evaluate(self, expression):
        if 'tagName' in expression:
            return self.tag
        raise AssertionError('Control mutation through JavaScript is forbidden')

    def _actionable(self, kwargs):
        if kwargs.get('force'):
            raise AssertionError('Forced browser actions are forbidden')
        if not self.visible or not self.enabled or self.native_failure:
            raise RuntimeError('Control is not natively actionable')

    def fill(self, value, **kwargs):
        self._actionable(kwargs)
        if not self.is_editable():
            raise RuntimeError('Control is not editable')
        self.value = value

    def select_option(self, *, value=None, label=None, **kwargs):
        self._actionable(kwargs)
        if self.tag != 'SELECT':
            raise RuntimeError('Control is not a native select')
        selected = value if value is not None else label
        if selected not in self.options:
            raise RuntimeError('Requested option does not exist')
        self.value = selected
        return [selected]

    def click(self, **kwargs):
        self._actionable(kwargs)
        self.clicked += 1
        if self.role == 'combobox':
            self.page.open_combobox = self
        if self.role == 'option':
            self.owner.value = self.value

    def wait_for(self, *, state, **_kwargs):
        if state != 'visible' or not self.visible:
            raise RuntimeError('Control did not become visible')

    def inner_text(self):
        return self.value

    def input_value(self):
        return self.value

    def press(self, key, **_kwargs):
        if key != 'Escape':
            raise AssertionError('Unexpected keyboard action')
        self.page.open_combobox = None


class _Locator:
    def __init__(self, controls):
        self.controls = list(controls)

    @property
    def first(self):
        return self.nth(0)

    def nth(self, index):
        return _Locator(self.controls[index:index + 1])

    def count(self):
        return len(self.controls)

    def all(self):
        return [self.nth(index) for index in range(self.count())]

    def __getattr__(self, name):
        if len(self.controls) != 1:
            raise RuntimeError('Native action requires a specific control')
        return getattr(self.controls[0], name)


class _Page:
    def __init__(self, selectors):
        self.selectors = selectors
        self.open_combobox = None
        self.evaluate = MagicMock(side_effect=AssertionError('Page JavaScript fallback is forbidden'))
        self.select_option = MagicMock(side_effect=self._select_first)
        self.click = MagicMock(side_effect=self._click_first)
        for controls in selectors.values():
            for control in controls:
                control.page = self
                if control.role == 'combobox':
                    for option in control.options:
                        option.page, option.owner = self, control

    def locator(self, selector):
        parent, delimiter, index = selector.rpartition(' >> nth=')
        if delimiter:
            return self.locator(parent).nth(int(index))
        nth_match = re.fullmatch(r':nth-match\((.+),\s*(\d+)\)', selector)
        if nth_match:
            return self.locator(nth_match.group(1)).nth(int(nth_match.group(2)) - 1)
        if selector == '[role="option"]:visible':
            options = self.open_combobox.options if self.open_combobox else []
            return _Locator(option for option in options if option.visible)
        return _Locator(self.selectors.get(selector, []))

    def _select_first(self, selector, **kwargs):
        return self.locator(selector).first.select_option(**kwargs)

    def _click_first(self, selector, **kwargs):
        return self.locator(selector).first.click(**kwargs)


class SharedFormControlsTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process start blocked')))

    def test_fill_uses_later_editable_match_of_the_same_selector(self):
        for attributes in ({'visible': False}, {'enabled': False}, {'readonly': True}):
            with self.subTest(first=attributes):
                blocked, editable = _Control(**attributes), _Control()
                page = _Page({'.password': [blocked, editable]})
                result = _fill(page, ['.password'], 'GENERATED_FAKE_PASSWORD')
                self.assertIsInstance(result, str)
                # MailCtx uses this address to attach its password-change
                # tracker; a selector matching both nodes would watch the wrong one.
                filled = page.locator(result)
                self.assertEqual(filled.count(), 1)
                self.assertEqual(filled.input_value(), 'GENERATED_FAKE_PASSWORD')
                self.assertIs(filled.controls[0], editable)
                self.assertEqual(blocked.value, 'original')
                self.assertEqual(editable.value, 'GENERATED_FAKE_PASSWORD')
                page.evaluate.assert_not_called()

    def test_native_select_uses_later_visible_enabled_match(self):
        for attributes in ({'visible': False}, {'enabled': False}):
            with self.subTest(first=attributes):
                blocked = _Control(tag='SELECT', options=['1995'], **attributes)
                usable = _Control(tag='SELECT', options=['1995'])
                page = _Page({'.birthYear': [blocked, usable]})
                self.assertTrue(_select(page, ['.birthYear'], 1995))
                self.assertEqual(blocked.value, 'original')
                self.assertEqual(usable.value, '1995')
                page.evaluate.assert_not_called()

    def test_combobox_uses_later_visible_enabled_match(self):
        for attributes in ({'visible': False}, {'enabled': False}):
            with self.subTest(first=attributes):
                blocked = _Control(tag='BUTTON', role='combobox', **attributes)
                option = _Control(tag='DIV', role='option', value='12')
                usable = _Control(tag='BUTTON', role='combobox', options=[option])
                page = _Page({'.birthMonth': [blocked, usable]})
                self.assertTrue(_select(page, ['.birthMonth'], 12))
                self.assertEqual(blocked.clicked, 0)
                self.assertEqual(usable.value, '12')
                self.assertEqual(option.clicked, 1)
                page.evaluate.assert_not_called()

    def test_click_uses_later_visible_enabled_button_of_the_same_selector(self):
        for attributes in ({'visible': False}, {'enabled': False}):
            with self.subTest(first=attributes):
                blocked, usable = _Control(tag='BUTTON', **attributes), _Control(tag='BUTTON')
                page = _Page({'.next': [blocked, usable]})
                self.assertEqual(_click(page, ['.next']), '.next')
                self.assertEqual(blocked.clicked, 0)
                self.assertEqual(usable.clicked, 1)
                page.evaluate.assert_not_called()

    def test_native_input_failure_never_falls_back_to_javascript(self):
        field = _Control(native_failure=True)
        page = _Page({'.email': [field]})
        self.assertIsNone(_fill(page, ['.email'], 'test@example.test'))
        self.assertEqual(field.value, 'original')
        page.evaluate.assert_not_called()

    def test_only_blocked_controls_never_receive_values_or_clicks(self):
        inputs = [_Control(visible=False), _Control(enabled=False), _Control(readonly=True)]
        selects = [_Control(tag='SELECT', enabled=False, options=['1995'])]
        buttons = [_Control(tag='BUTTON', visible=False), _Control(tag='BUTTON', enabled=False)]
        page = _Page({'.email': inputs, '.birthYear': selects, '.next': buttons})
        self.assertIsNone(_fill(page, ['.email'], 'test@example.test'))
        self.assertFalse(_select(page, ['.birthYear'], 1995))
        self.assertIsNone(_click(page, ['.next']))
        self.assertTrue(all(control.value == 'original' for control in inputs + selects))
        self.assertTrue(all(control.clicked == 0 for control in buttons))
        page.evaluate.assert_not_called()

    def test_alternative_selectors_still_work_after_native_action_failure(self):
        first, later = _Control(native_failure=True), _Control()
        blocked_button, later_button = _Control(tag='BUTTON', native_failure=True), _Control(tag='BUTTON')
        page = _Page({'.old-email': [first], '.new-email': [later],
                      '.old-next': [blocked_button], '.new-next': [later_button]})
        self.assertEqual(_fill(page, ['.old-email', '.new-email'], 'test@example.test'), '.new-email')
        self.assertEqual(_click(page, ['.old-next', '.new-next']), '.new-next')
        self.assertEqual(first.value, 'original')
        self.assertEqual(later.value, 'test@example.test')
        self.assertEqual(blocked_button.clicked, 0)
        self.assertEqual(later_button.clicked, 1)
        page.evaluate.assert_not_called()

    def test_click_wait_for_permanently_disabled_button_is_bounded(self):
        button = _Control(tag='BUTTON', enabled=False)
        now = [100.0]
        waits = []
        test = self

        class TimedPage(_Page):
            def wait_for_timeout(self, milliseconds):
                test.assertGreater(milliseconds, 0)
                test.assertLessEqual(milliseconds, 100)
                waits.append(milliseconds)
                test.assertLessEqual(len(waits), 51, 'Click wait exceeded its bounded polling budget')
                now[0] += milliseconds / 1000

        page = TimedPage({'.next': [button]})
        with patch.object(signup.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(signup.time, 'sleep', side_effect=AssertionError('Real sleep blocked')):
            self.assertIsNone(_click(page, ['.next']))
        self.assertTrue(waits)
        self.assertAlmostEqual(now[0] - 100.0, 5.0, places=6)
        self.assertAlmostEqual(sum(waits), 5000.0, places=5)
        self.assertEqual(button.clicked, 0)
        page.click.assert_not_called()
        page.evaluate.assert_not_called()

    def test_cancellation_during_click_wait_stops_polling_and_newly_enabled_click(self):
        button = _Control(tag='BUTTON', enabled=False)
        now = [100.0]
        cancelled = [False]
        waits = []
        polls = []
        test = self

        class TimedPage(_Page):
            def locator(self, selector):
                polls.append(selector)
                return super().locator(selector)

            def wait_for_timeout(self, milliseconds):
                test.assertGreater(milliseconds, 0)
                test.assertLessEqual(milliseconds, 100)
                waits.append(milliseconds)
                test.assertEqual(len(waits), 1, 'Cancellation must stop the first polling wait')
                now[0] += milliseconds / 1000
                cancelled[0] = True
                button.enabled = True

        def check():
            if cancelled[0]:
                raise FinalError('Abgebrochen.')

        page = TimedPage({'.next': [button]})
        with patch.object(signup.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(signup.time, 'sleep', side_effect=AssertionError('Real sleep blocked')):
            with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                _click(page, ['.next'], check=check)
        self.assertEqual(len(waits), 1)
        self.assertEqual(polls, ['.next'])
        self.assertEqual(button.clicked, 0)
        page.click.assert_not_called()
        page.evaluate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
