"""PID reuse must never adopt or terminate another application's processes."""
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from socialscraper.net import deadline as module


class ProcessOwnership(unittest.TestCase):
    def test_reused_parent_does_not_adopt_older_children_or_their_descendants(self):
        table = {20: (10, 'chrome.exe'), 30: (20, 'chrome.exe'),
                 40: (10, 'pythonw.exe'), 50: (40, 'chrome.exe')}
        starts = {10: 100, 20: 10, 30: 110, 40: 110, 50: 120}
        self.assertEqual(module._walk_children(table, 10, starts.get, 200),
                         [(40, 'pythonw.exe', 110), (50, 'chrome.exe', 120)])

    def test_checks_every_generation_and_rejects_inaccessible_or_newly_reused_pids(self):
        table = {20: (10, 'pythonw.exe'), 30: (20, 'chrome.exe'),
                 40: (20, 'chrome.exe'), 50: (10, 'chrome.exe')}
        starts = {10: 100, 20: 120, 30: 110, 40: 201, 50: None}
        self.assertEqual(module._walk_children(table, 10, starts.get, 200),
                         [(20, 'pythonw.exe', 120)])
        self.assertEqual(module._walk_children(table, 99, starts.get, 200), [])

    def test_same_timestamp_is_valid_and_cycles_cannot_duplicate_processes(self):
        table = {10: (20, 'python.exe'), 20: (10, 'chrome.exe')}
        self.assertEqual(module._walk_children(table, 10, lambda _pid: 100, 100),
                         [(20, 'chrome.exe', 100)])

    def test_termination_rechecks_creation_time_on_the_opened_handle(self):
        api = Mock()
        api.OpenProcess.return_value = 123456789012
        api.TerminateProcess.return_value = 1
        for observed, expected_count in ((101, 0), (100, 1), (None, 0)):
            api.reset_mock()
            with patch.object(module, '_kernel32', return_value=api), \
                 patch.object(module, '_owned_windows', return_value=[(20, 'chrome.exe', 100)]), \
                 patch.object(module, '_read_creation', return_value=observed):
                self.assertEqual(module.terminate_children(10), expected_count)
            self.assertEqual(api.TerminateProcess.call_count, expected_count)
            api.CloseHandle.assert_called_once_with(123456789012)

    def test_browser_cleanup_excludes_python_and_console_hosts(self):
        api = Mock()
        api.OpenProcess.return_value = 77
        api.TerminateProcess.return_value = 1
        children = [(20, 'python.exe', 100), (30, 'conhost.exe', 100),
                    (40, 'chrome.exe', 100)]
        with patch.object(module, '_kernel32', return_value=api), \
             patch.object(module, '_owned_windows', return_value=children), \
             patch.object(module, '_read_creation', return_value=100):
            self.assertEqual(module.terminate_children(10, browser_only=True), 1)
        api.OpenProcess.assert_called_once_with(0x1001, False, 40)

    def test_waiting_requires_synchronize_access_and_closes_handle_after_failure(self):
        api = Mock()
        api.OpenProcess.return_value = 77
        with patch.object(module, '_kernel32', return_value=api), \
             patch.object(module, '_owned_windows', return_value=[(20, 'chrome.exe', 100)]), \
             patch.object(module, '_read_creation', return_value=100):
            self.assertEqual(module.terminate_children(10, wait_ms=5000), 1)
        api.OpenProcess.assert_called_once_with(0x101001, False, 20)
        api.WaitForSingleObject.assert_called_once_with(77, 5000)
        api.reset_mock()
        with patch.object(module, '_kernel32', return_value=api), \
             patch.object(module, '_owned_windows', return_value=[(20, 'chrome.exe', 100)]), \
             patch.object(module, '_read_creation', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                module.terminate_children(10)
        api.CloseHandle.assert_called_once_with(77)

    def test_job_fallback_uses_verified_tree_and_preserves_finished_job(self):
        from socialscraper.guicore import Job
        job = object.__new__(Job)
        job.proc = Mock(pid=123)
        job.proc.poll.return_value = None
        with patch.object(module, 'terminate_children', return_value=1) as terminate:
            self.assertTrue(job._force_tree())
        terminate.assert_called_once_with(123, wait_ms=5000)
        job.proc.kill.assert_called_once_with()
        job.proc.poll.return_value = 0
        with patch.object(module, 'terminate_children') as terminate:
            self.assertFalse(job._force_tree())
        terminate.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows process handles')
    def test_native_termination_ends_only_a_verified_owned_grandchild(self):
        script = ('import subprocess,sys,time; '
                  'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],'
                  'creationflags=subprocess.CREATE_NO_WINDOW); '
                  'print(p.pid,flush=True); time.sleep(30)')
        # The venv executable is a redirector on Windows and adds another
        # interpreter per generation. Use the actual interpreter for this tree.
        parent = subprocess.Popen([sys._base_executable, '-c', script], stdout=subprocess.PIPE,
                                  text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        sibling = subprocess.Popen([sys._base_executable, '-c', 'import time;time.sleep(30)'],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        api, handle = module._kernel32(), None
        try:
            child_pid = int(parent.stdout.readline())
            # Hold the fixture handle so this PID cannot be reused during cleanup.
            handle = api.OpenProcess(0x101001, False, child_pid)
            self.assertTrue(handle)
            self.assertGreaterEqual(module.terminate_children(parent.pid, wait_ms=3000), 1)
            self.assertEqual(api.WaitForSingleObject(handle, 3000), 0)
            self.assertIsNone(parent.poll())
            self.assertIsNone(sibling.poll())
        finally:
            if handle:
                if api.WaitForSingleObject(handle, 0) != 0:
                    api.TerminateProcess(handle, 1)
                api.CloseHandle(handle)
            parent.kill()
            parent.wait()
            parent.stdout.close()
            sibling.kill()
            sibling.wait()


if __name__ == '__main__':
    unittest.main()
