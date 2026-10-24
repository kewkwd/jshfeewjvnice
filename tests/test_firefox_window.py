"""Owned Firefox/Chrome windows and cleanup, using no native UI or processes."""
import contextlib
import ctypes
import io
import json
import os
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from socialscraper.net import browser_window as windows
from socialscraper.net import deadline


def native_windows(entries):
    """Run the real matcher/enumerator against an in-memory Win32 boundary."""
    native = object.__new__(windows.Windows)
    native.c = SimpleNamespace(create_unicode_buffer=ctypes.create_unicode_buffer,
                               WINFUNCTYPE=lambda *args: lambda callback: callback)
    native.w = SimpleNamespace(BOOL=int, HWND=int, LPARAM=int)
    native.api = Mock()
    native.api.IsWindow.side_effect = lambda handle: handle in entries
    native.api.IsWindowVisible.side_effect = lambda handle: entries[handle][2]

    def class_name(handle, buffer, size):
        buffer.value = entries[handle][1]
        return len(buffer.value)

    def enumerate_windows(callback, parameter):
        for handle in entries:
            callback(handle, parameter)
        return True

    native.api.GetClassNameW.side_effect = class_name
    native.api.EnumWindows.side_effect = enumerate_windows
    native.owner = Mock(side_effect=lambda handle: entries[handle][0])
    return native


class FirefoxWindows(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(ctypes, 'WinDLL', create=True,
                                             side_effect=AssertionError('Native Windows API blocked')))
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        for method in ('connect', 'connect_ex', 'sendto', 'bind'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))

    def test_window_class_is_specific_to_backend_and_existing_window(self):
        native = native_windows({100: (20, 'MozillaWindowClass', True),
                                 200: (30, 'Chrome_WidgetWin_1', True),
                                 300: (20, 'MozillaDialogClass', True)})
        self.assertTrue(native.browser(100, 'firefox'))
        self.assertFalse(native.browser(200, 'firefox'))
        self.assertFalse(native.browser(300, 'firefox'))
        self.assertFalse(native.browser(999, 'firefox'))
        self.assertTrue(native.browser(200))
        self.assertFalse(native.browser(100))
        self.assertFalse(native.browser(200, 'unknown'))

    def test_visible_filters_class_visibility_and_owned_processes_together(self):
        native = native_windows({100: (20, 'MozillaWindowClass', True),
                                 101: (20, 'MozillaWindowClass', False),
                                 102: (99, 'MozillaWindowClass', True),
                                 103: (20, 'MozillaDialogClass', True),
                                 200: (30, 'Chrome_WidgetWin_1', True)})
        self.assertEqual(native.visible({20, 30}, 'firefox'), [100])
        self.assertEqual(native.visible({20, 30}), [200])
        self.assertEqual(native.visible(set(), 'firefox'), [])

    def test_announce_chooses_matching_owned_backend_and_preserves_legacy_chrome_event(self):
        entries = {100: (20, 'MozillaWindowClass', True), 200: (30, 'Chrome_WidgetWin_1', True)}
        for backend, expected_handle, expected_pid in [('firefox', 100, 20), ('local', 200, 30)]:
            with self.subTest(backend=backend):
                native = native_windows(entries)
                handle = SimpleNamespace(backend=backend, proxy=object())
                output = io.StringIO()
                with patch.object(windows.os, 'name', 'nt'), \
                        patch.dict(os.environ, {'SS_EMBED_BROWSER': '1'}), \
                        patch.object(windows, 'Windows', return_value=native), \
                        patch.object(deadline, 'descendants', return_value=[(20, 'firefox.exe'), (30, 'chrome.exe')]), \
                        contextlib.redirect_stdout(output):
                    windows.announce(handle, False)
                event = windows.parse(output.getvalue().strip())
                expected = {'version': 1, 'state': 'open', 'hwnd': expected_handle,
                            'pid': expected_pid, 'proxy': True}
                if backend == 'firefox':
                    expected['backend'] = backend
                self.assertEqual(event, expected)
                self.assertEqual(handle._native_window, expected_handle)

    def test_announce_refuses_ambiguous_firefox_windows(self):
        native = native_windows({100: (20, 'MozillaWindowClass', True),
                                 101: (20, 'MozillaWindowClass', True)})
        handle = SimpleNamespace(backend='firefox', proxy=object())
        output = io.StringIO()
        with patch.object(windows.os, 'name', 'nt'), patch.dict(os.environ, {'SS_EMBED_BROWSER': '1'}), \
                patch.object(windows, 'Windows', return_value=native), \
                patch.object(deadline, 'descendants', return_value=[(20, 'firefox.exe')]), \
                contextlib.redirect_stdout(output):
            windows.announce(handle, False)
        self.assertEqual(output.getvalue(), '')
        self.assertFalse(hasattr(handle, '_native_window'))

    def test_announce_rechecks_owner_after_enumeration(self):
        native = native_windows({100: (20, 'MozillaWindowClass', True)})
        native.owner.side_effect = [20, 99]
        handle = SimpleNamespace(backend='firefox', proxy=object())
        output = io.StringIO()
        with patch.object(windows.os, 'name', 'nt'), patch.dict(os.environ, {'SS_EMBED_BROWSER': '1'}), \
                patch.object(windows, 'Windows', return_value=native), \
                patch.object(deadline, 'descendants', return_value=[(20, 'firefox.exe')]), \
                contextlib.redirect_stdout(output):
            windows.announce(handle, False)
        self.assertEqual(output.getvalue(), '')
        self.assertFalse(hasattr(handle, '_native_window'))

    def test_headless_and_disabled_embedding_do_not_inspect_native_windows(self):
        handle = SimpleNamespace(backend='firefox', proxy=object())
        with patch.object(windows.os, 'name', 'nt'), patch.object(windows, 'Windows') as native:
            with patch.dict(os.environ, {'SS_EMBED_BROWSER': '1'}):
                windows.announce(handle, True)
            with patch.dict(os.environ, {'SS_EMBED_BROWSER': '0'}):
                windows.announce(handle, False)
            native.assert_not_called()

    def test_parse_accepts_legacy_and_firefox_events_and_closed_notification(self):
        legacy = {'version': 1, 'state': 'open', 'hwnd': 100, 'pid': 20, 'proxy': True}
        for event in (legacy, {**legacy, 'backend': 'firefox'},
                      {'version': 1, 'state': 'closed', 'hwnd': 100}):
            with self.subTest(event=event):
                self.assertEqual(windows.parse(windows.PREFIX + json.dumps(event)), event)
        handle = SimpleNamespace(backend='firefox', _native_window=100)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            windows.closed(handle)
            windows.closed(handle)
        self.assertEqual(windows.parse(output.getvalue().strip()),
                         {'version': 1, 'state': 'closed', 'hwnd': 100})
        self.assertIsNone(handle._native_window)

    def test_unknown_or_malformed_backend_never_falls_back_to_chrome(self):
        for backend in ('unknown', '', None, True, [], {}):
            with self.subTest(backend=backend):
                event = {'version': 1, 'state': 'open', 'hwnd': 100, 'pid': 20,
                         'proxy': True, 'backend': backend}
                self.assertIsNone(windows.parse(windows.PREFIX + json.dumps(event)))
                with patch.object(deadline, 'descendants') as descendants, \
                        patch.object(windows, 'Windows') as native, \
                        patch.object(windows.os, 'name', 'nt'), \
                        patch.dict(os.environ, {'SS_EMBED_BROWSER': '1'}):
                    self.assertFalse(windows.validate(event, 10))
                    windows.announce(SimpleNamespace(backend=backend, proxy=object()), False)
                    descendants.assert_not_called()
                    native.assert_not_called()

    def test_validate_requires_backend_process_class_and_current_job_ownership(self):
        cases = [
            ('firefox', 'firefox.exe', 'MozillaWindowClass', 20, True),
            ('local', 'chrome.exe', 'Chrome_WidgetWin_1', 20, True),
            ('firefox', 'chrome.exe', 'MozillaWindowClass', 20, False),
            ('local', 'firefox.exe', 'Chrome_WidgetWin_1', 20, False),
            ('firefox', 'firefox.exe', 'Chrome_WidgetWin_1', 20, False),
            ('local', 'chrome.exe', 'MozillaWindowClass', 20, False),
            ('firefox', 'firefox.exe', 'MozillaWindowClass', 99, False),
            ('local', 'chrome.exe', 'Chrome_WidgetWin_1', 99, False),
            ('firefox', 'notepad.exe', 'MozillaWindowClass', 20, False),
        ]
        for backend, process, window_class, owner, accepted in cases:
            with self.subTest(backend=backend, process=process, window_class=window_class, owner=owner):
                native = native_windows({100: (owner, window_class, True)})
                event = {'state': 'open', 'hwnd': 100, 'pid': 20}
                if backend == 'firefox':
                    event['backend'] = backend
                with patch.object(deadline, 'descendants', return_value=[(20, process)]) as children:
                    self.assertEqual(windows.validate(event, 10, native), accepted)
                    children.assert_called_once_with(10)
                with patch.object(deadline, 'descendants', return_value=[]):
                    self.assertFalse(windows.validate(event, 10, native))

    def test_cleanup_targets_owned_firefox_and_chrome_but_no_other_apps(self):
        api = Mock()
        api.OpenProcess.side_effect = lambda access, inherit, pid: pid + 1000
        api.TerminateProcess.return_value = 1
        owned = [(20, 'firefox.exe', 100), (30, 'chrome.exe', 100),
                 (40, 'notepad.exe', 100), (50, 'conhost.exe', 100)]
        with patch.object(deadline.os, 'name', 'nt'), patch.object(deadline, '_kernel32', return_value=api), \
                patch.object(deadline, '_owned_windows', return_value=owned) as children, \
                patch.object(deadline, '_read_creation', return_value=100):
            self.assertEqual(deadline.terminate_children(10, browser_only=True), 2)
        children.assert_called_once_with(10)
        self.assertEqual({call.args[2] for call in api.OpenProcess.call_args_list}, {20, 30})
        self.assertEqual({call.args[0] for call in api.TerminateProcess.call_args_list}, {1020, 1030})
        self.assertEqual(api.CloseHandle.call_count, 2)

    def test_cleanup_refuses_firefox_pid_reused_after_ownership_snapshot(self):
        for observed in (None, 101):
            with self.subTest(observed_creation=observed):
                api = Mock()
                api.OpenProcess.return_value = 1020
                with patch.object(deadline.os, 'name', 'nt'), \
                        patch.object(deadline, '_kernel32', return_value=api), \
                        patch.object(deadline, '_owned_windows', return_value=[(20, 'firefox.exe', 100)]), \
                        patch.object(deadline, '_read_creation', return_value=observed):
                    self.assertEqual(deadline.terminate_children(10, browser_only=True), 0)
                api.TerminateProcess.assert_not_called()
                api.CloseHandle.assert_called_once_with(1020)


if __name__ == '__main__':
    unittest.main()
