"""Pure WinAPI fakes: no process, job, window or emulator is started."""
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.android import avd_process as owned


class FakeAPI:
    def __init__(self, fail=None):
        self.events = []
        self.fail = fail
        self.ended = set()
        self.closed = []
        self.sequence = 0
        self.descendants = 0

    def event(self, name, *args):
        self.events.append((name, *args))
        if self.fail == name:
            raise OSError('PRIVATE_FAKE_PROXY_PASSWORD')

    def create_job(self):
        self.event('create_job')
        return 10

    def configure_job(self, handle):
        self.event('configure_job', handle)

    def create_process(self, args, env, cwd, std_handles=None):
        self.event('create_process')
        self.sequence += 1
        return 100 + self.sequence, 200 + self.sequence, 300 + self.sequence, 400 + self.sequence

    def assign(self, job, process):
        self.event('assign', job, process)

    def resume(self, thread):
        self.event('resume', thread)

    def terminate(self, handle):
        self.event('terminate', handle)
        self.ended.add(handle)

    def terminate_job(self, handle):
        self.event('terminate_job', handle)
        self.ended.update(range(101, 101 + self.sequence))

    def active_processes(self, handle):
        self.event('active_processes', handle)
        return len(set(range(101, 101 + self.sequence)) - self.ended) + self.descendants

    def process_handles(self, handle):
        self.event('process_handles', handle)
        return []

    def close_handle(self, handle):
        self.closed.append(handle)
        self.event('close_handle', handle)

    def wait(self, handle, milliseconds):
        self.event('wait', handle, milliseconds)
        return owned.WAIT_OBJECT_0 if handle in self.ended else owned.WAIT_TIMEOUT

    def exit_code(self, handle):
        self.event('exit_code', handle)
        return 7


class AVDProcessOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd().anchor
        self.args = [str(Path(self.root) / 'owned-sdk/emulator.exe'), '-avd', 'owned-test']
        self.env = {'HTTP_PROXY': 'http://private-test:PRIVATE_FAKE_PROXY_PASSWORD@127.0.0.1:1'}

    def job(self, api):
        with patch.object(owned, '_WindowsAPI', return_value=api):
            return owned.WindowsOwnedJob()

    def start(self, job):
        return job.start(self.args, env=self.env, cwd=self.root)

    def test_process_is_assigned_before_resume_and_thread_handle_is_closed(self):
        api = FakeAPI()
        job = self.job(api)
        try:
            process = self.start(job)
            names = [event[0] for event in api.events]
            self.assertLess(names.index('configure_job'), names.index('create_process'))
            self.assertLess(names.index('create_process'), names.index('assign'))
            self.assertLess(names.index('assign'), names.index('resume'))
            self.assertEqual(api.closed, [201])
            self.assertEqual(process.pid, 301)
            self.assertIsNone(process.poll())
        finally:
            job.close()
        self.assertCountEqual(api.closed, [201, 10, 101])
        self.assertEqual(process.poll(), 7)

    def test_native_create_uses_suspended_hidden_no_inheritance_and_environment_only_proxy(self):
        api = object.__new__(owned._WindowsAPI)
        api.winapi = SimpleNamespace(CreateProcess=MagicMock(return_value=(1, 2, 3, 4)))
        startup = SimpleNamespace()
        with patch.object(subprocess, 'STARTUPINFO', return_value=startup, create=True), \
                patch.object(subprocess, 'STARTF_USESHOWWINDOW', 1, create=True), \
                patch.object(subprocess, 'SW_HIDE', 0, create=True):
            self.assertEqual(api.create_process(self.args, self.env, self.root), (1, 2, 3, 4))
        call = api.winapi.CreateProcess.call_args.args
        self.assertFalse(call[4])
        self.assertTrue(call[5] & 0x00000004)  # CREATE_SUSPENDED
        self.assertTrue(call[5] & 0x08000000)  # CREATE_NO_WINDOW
        self.assertFalse(call[5] & 0x01000000)  # No CREATE_BREAKAWAY_FROM_JOB.
        self.assertEqual(call[6], self.env)
        self.assertNotIn('PRIVATE_FAKE_PROXY_PASSWORD', call[1])
        self.assertEqual(startup.wShowWindow, 0)

    def test_job_limits_enable_kill_on_close_without_breakaway(self):
        import ctypes
        kernel = MagicMock()
        with patch.object(ctypes, 'WinDLL', return_value=kernel):
            api = owned._WindowsAPI()
        captured = []
        def configure(handle, information_class, address, size):
            captured.append((handle, information_class,
                             ctypes.cast(address, ctypes.POINTER(api.Extended)).contents.basic.flags))
            return 1
        kernel.SetInformationJobObject.side_effect = configure
        api.configure_job(10)
        self.assertEqual(captured, [(10, 9, 0x2000)])
        self.assertFalse(captured[0][2] & (0x800 | 0x1000))  # Neither breakaway option.

    def test_resume_requires_exactly_one_existing_suspension(self):
        api = object.__new__(owned._WindowsAPI)
        api.kernel = MagicMock()
        for previous_count in (0, 2, 0xFFFFFFFF):
            api.kernel.ResumeThread.return_value = previous_count
            with self.subTest(previous_count=previous_count), self.assertRaises(owned.OwnedProcessError):
                api.resume(201)
        api.kernel.ResumeThread.return_value = 1
        api.resume(201)

    def test_native_job_query_returns_active_descendant_count_not_total_processes(self):
        import ctypes
        kernel = MagicMock()
        with patch.object(ctypes, 'WinDLL', return_value=kernel):
            api = owned._WindowsAPI()
        def query(handle, information_class, address, size, returned):
            self.assertEqual((handle, information_class), (10, 1))
            self.assertEqual(size, ctypes.sizeof(api.Accounting))
            record = ctypes.cast(address, ctypes.POINTER(api.Accounting)).contents
            record.total_processes = 20
            record.terminated_processes = 17
            record.active_processes = 3
            return 1
        kernel.QueryInformationJobObject.side_effect = query
        self.assertEqual(api.active_processes(10), 3)
        kernel.QueryInformationJobObject.side_effect = None
        kernel.QueryInformationJobObject.return_value = 0
        with self.assertRaises(owned.OwnedProcessError):
            api.active_processes(10)

    def test_failed_configuration_closes_only_its_created_job(self):
        api = FakeAPI('configure_job')
        with self.assertRaises(owned.OwnedProcessError) as error:
            self.job(api)
        self.assertEqual(api.closed, [10])
        self.assertNotIn('PRIVATE_FAKE', str(error.exception))
        self.assertNotIn('create_process', [event[0] for event in api.events])

    def test_assignment_or_resume_failure_terminates_only_new_process_and_closes_both_handles(self):
        for stage in ('assign', 'resume'):
            with self.subTest(stage=stage):
                api = FakeAPI(stage)
                job = self.job(api)
                try:
                    with self.assertRaises(owned.OwnedProcessError) as error:
                        self.start(job)
                    self.assertNotIn('PRIVATE_FAKE', str(error.exception))
                    self.assertEqual([e for e in api.events if e[0] == 'terminate'], [('terminate', 101)])
                    self.assertCountEqual(api.closed, [201, 101])
                    if stage == 'assign':
                        self.assertFalse(any(e[0] == 'resume' for e in api.events))
                finally:
                    job.close()

    def test_create_failure_has_no_fabricated_handle_cleanup_and_no_secret_in_traceback(self):
        import traceback
        api = FakeAPI('create_process')
        job = self.job(api)
        try:
            try:
                self.start(job)
            except owned.OwnedProcessError:
                self.assertNotIn('PRIVATE_FAKE_PROXY_PASSWORD', traceback.format_exc())
            else:
                self.fail('Expected sanitized startup failure')
            self.assertEqual(api.closed, [])
            self.assertFalse(any(e[0] == 'terminate' for e in api.events))
        finally:
            job.close()

    def test_poll_wait_timeout_and_termination_use_only_owned_handle(self):
        api = FakeAPI()
        with self.job(api) as job:
            process = self.start(job)
            with self.assertRaises(subprocess.TimeoutExpired) as error:
                process.wait(timeout=0)
            self.assertNotIn('PRIVATE_FAKE', str(error.exception))
            process.terminate()
            self.assertEqual(process.wait(timeout=1), 7)
            process.terminate()
            self.assertEqual([e for e in api.events if e[0] == 'terminate'], [('terminate', 101)])
            self.assertTrue(all(e[1] == 101 for e in api.events if e[0] in ('wait', 'exit_code')))

    def test_close_is_idempotent_and_rejects_new_children(self):
        api = FakeAPI()
        job = self.job(api)
        first = self.start(job)
        second = self.start(job)
        job.close()
        events = list(api.events)
        job.close()
        first.close()
        second.close()
        self.assertEqual(api.events, events)
        self.assertCountEqual(api.closed, [201, 202, 10, 101, 102])
        with self.assertRaises(owned.OwnedProcessError):
            self.start(job)

    def test_cleanup_failure_retains_handles_until_empty_job_is_confirmed(self):
        api = FakeAPI()
        job = self.job(api)
        self.start(job)
        api.fail = 'terminate_job'
        api.descendants = 1
        with self.assertRaises(owned.OwnedProcessError) as error:
            job.close()
        self.assertNotIn('PRIVATE_FAKE', str(error.exception))
        self.assertEqual(api.closed, [201])
        self.assertEqual(job._handle, 10)
        api.fail = None
        api.descendants = 0
        job.close()
        self.assertCountEqual(api.closed, [201, 10, 101])

    def test_close_waits_for_untracked_descendants_after_direct_children_exit(self):
        api = FakeAPI()
        job = self.job(api)
        self.start(job)
        counts = iter([2, 1, 0])
        def query(handle):
            self.assertEqual(handle, 10)
            self.assertIn(101, api.ended)
            self.assertEqual(api.closed, [201])
            return next(counts)
        clock = [0.0]
        with patch.object(api, 'active_processes', side_effect=query) as observe, \
                patch.object(owned.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(owned.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            job.close(timeout=1)
        self.assertEqual(observe.call_count, 3)
        self.assertGreater(clock[0], 0)
        self.assertCountEqual(api.closed, [201, 101, 10])

    def test_shutdown_timeout_preserves_ownership_and_blocks_reuse_until_retry(self):
        api = FakeAPI()
        job = self.job(api)
        process = self.start(job)
        api.descendants = 1
        clock = [0.0]
        with patch.object(owned.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(owned.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)):
            with self.assertRaises(owned.OwnedProcessError):
                job.close(timeout=.2)
        self.assertLessEqual(clock[0], .2)
        self.assertEqual(api.closed, [201])
        self.assertEqual((job._handle, process._handle), (10, 101))
        with self.assertRaises(owned.OwnedProcessError):
            self.start(job)
        api.descendants = 0
        job.close(timeout=0)
        self.assertCountEqual(api.closed, [201, 101, 10])

    def test_zero_job_count_does_not_close_unsignaled_process_handle(self):
        api = FakeAPI()
        job = self.job(api)
        process = self.start(job)
        original_wait = api.wait
        observed = []
        def delayed_signal(handle, milliseconds):
            observed.append(milliseconds)
            if len(observed) == 1:
                return owned.WAIT_TIMEOUT
            return original_wait(handle, milliseconds)
        with patch.object(api, 'wait', side_effect=delayed_signal):
            job.close(timeout=1)
        self.assertEqual(process.poll(), 7)
        self.assertTrue(any(milliseconds > 0 for milliseconds in observed))

    def test_unsignaled_handle_timeout_retains_job_and_all_process_handles(self):
        api = FakeAPI()
        job = self.job(api)
        process = self.start(job)
        with patch.object(api, 'wait', return_value=owned.WAIT_TIMEOUT):
            with self.assertRaises(owned.OwnedProcessError):
                job.close(timeout=0)
        self.assertEqual((job._handle, process._handle), (10, 101))
        self.assertEqual(api.closed, [201])
        job.close(timeout=0)

    def test_snapshot_rechecks_job_membership_and_discards_reused_pid(self):
        import ctypes
        from ctypes import wintypes as w
        kernel = MagicMock()
        with patch.object(ctypes, 'WinDLL', return_value=kernel):
            api = owned._WindowsAPI()
        missing = OSError('already exited')
        missing.winerror = 87
        api.winapi = SimpleNamespace(OpenProcess=MagicMock(side_effect=[1001, 1002, missing]), CloseHandle=MagicMock())
        def query(handle, information_class, address, size, returned):
            self.assertEqual((handle, information_class), (10, 3))
            header = ctypes.cast(address, ctypes.POINTER(w.DWORD * 2)).contents
            header[0] = header[1] = 3
            pids = ctypes.cast(ctypes.addressof(address._obj) + 8, ctypes.POINTER(ctypes.c_size_t * 3)).contents
            pids[:] = [111, 222, 333]
            return 1
        def membership(process, job, belongs):
            self.assertEqual(job, 10)
            ctypes.cast(belongs, ctypes.POINTER(w.BOOL)).contents.value = process == 1001
            return 1
        kernel.QueryInformationJobObject.side_effect = query
        kernel.IsProcessInJob.side_effect = membership
        self.assertEqual(api.process_handles(10), [(111, 1001)])
        api.winapi.CloseHandle.assert_called_once_with(1002)
        kernel.TerminateJobObject.assert_not_called()

    def test_snapshot_child_handle_must_signal_before_ownership_is_released(self):
        api = FakeAPI()
        job = self.job(api)
        self.start(job)
        with patch.object(api, 'process_handles', side_effect=[[(900, 501)], []]):
            with self.assertRaises(owned.OwnedProcessError):
                job.close(timeout=0)
        self.assertEqual(job._observed_handles, {900: 501})
        self.assertEqual(api.closed, [201])
        api.ended.add(501)
        job.close(timeout=0)
        self.assertCountEqual(api.closed, [201, 101, 501, 10])

    def test_failed_assignment_retains_unsignaled_owned_handle_for_cleanup(self):
        api = FakeAPI('assign')
        job = self.job(api)
        clock = [0]
        def advance():
            clock[0] += 6
            return clock[0]
        with patch.object(api, 'wait', return_value=owned.WAIT_TIMEOUT), \
                patch.object(owned.time, 'monotonic', side_effect=advance):
            with self.assertRaises(owned.OwnedProcessError):
                self.start(job)
        self.assertEqual(len(job._processes), 1)
        self.assertEqual(job._processes[0]._handle, 101)
        self.assertEqual(api.closed, [201])
        with self.assertRaises(owned.OwnedProcessError):
            self.start(job)
        api.fail = None
        job.close(timeout=0)
        self.assertCountEqual(api.closed, [201, 101, 10])

    def test_capture_process_is_assigned_to_session_and_nested_job_before_resume(self):
        api = FakeAPI()
        with self.job(api) as job:
            job._start(self.args, env=self.env, cwd=self.root, parent_job=20, std_handles=(31, 32, 33))
            calls = [event for event in api.events if event[0] in ('assign', 'resume')]
            self.assertEqual(calls, [('assign', 20, 101), ('assign', 10, 101), ('resume', 201)])

    def test_capture_inherits_only_explicit_standard_pipe_handles(self):
        api = object.__new__(owned._WindowsAPI)
        api.winapi = SimpleNamespace(CreateProcess=MagicMock(return_value=(1, 2, 3, 4)))
        startup = SimpleNamespace()
        with patch.object(subprocess, 'STARTUPINFO', return_value=startup, create=True), \
                patch.object(subprocess, 'STARTF_USESHOWWINDOW', 1, create=True), \
                patch.object(subprocess, 'STARTF_USESTDHANDLES', 256, create=True), \
                patch.object(subprocess, 'SW_HIDE', 0, create=True):
            api.create_process(self.args, self.env, self.root, (31, 32, 33))
        self.assertTrue(api.winapi.CreateProcess.call_args.args[4])
        self.assertEqual(startup.lpAttributeList, {'handle_list': [31, 32, 33]})
        self.assertEqual((startup.hStdInput, startup.hStdOutput, startup.hStdError), (31, 32, 33))
        self.assertTrue(api.winapi.CreateProcess.call_args.args[5] & owned.CREATE_SUSPENDED)

    def test_capture_size_limit_applies_to_combined_output_before_return(self):
        capture = object.__new__(owned._Capture)
        capture.api = SimpleNamespace(winapi=SimpleNamespace(
            PeekNamedPipe=MagicMock(return_value=(100, 0)),
            ReadFile=MagicMock(return_value=(b'ab', 0))))
        capture.read_handles = [31]
        capture.eof = [False]
        capture.total = owned.CAPTURE_LIMIT - 1
        capture.chunks = [[b'prior output']]
        with self.assertRaises(owned.OwnedProcessError):
            capture.drain()
        capture.api.winapi.ReadFile.assert_called_once_with(31, 2)

    def test_owned_capture_returns_bytes_and_reaps_nested_job_without_closing_session(self):
        api = FakeAPI()
        with self.job(api) as job:
            process = SimpleNamespace(poll=lambda: 3)
            child = SimpleNamespace(_handle=20, _api=object(), _start=MagicMock(return_value=process))
            child.close = MagicMock(side_effect=lambda **kwargs: setattr(child, '_handle', None))
            capture = MagicMock(handles=(31, 32, 33), complete=True)
            capture.result.return_value = (b'stdout\0bytes', b'stderr bytes')
            with patch.object(owned, 'WindowsOwnedJob', return_value=child), patch.object(owned, '_Capture', return_value=capture):
                result = job.run(self.args, env=self.env, cwd=self.root, timeout=1)
            self.assertIsInstance(result, subprocess.CompletedProcess)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (3, b'stdout\0bytes', b'stderr bytes'))
            self.assertEqual(result.args, 'owned Android process')
            self.assertEqual(job._handle, 10)
            self.assertEqual(job._capture_jobs, [])
            self.assertTrue(child.close.called)
            capture.close.assert_called_once()

    def test_owned_capture_timeout_kills_only_nested_job_and_redacts_command(self):
        api = FakeAPI()
        with self.job(api) as job:
            child = SimpleNamespace(_handle=20, _api=object(), _start=MagicMock(return_value=SimpleNamespace(poll=lambda: None)))
            child.close = MagicMock(side_effect=lambda **kwargs: setattr(child, '_handle', None))
            capture = MagicMock(handles=(31, 32, 33), complete=False)
            with patch.object(owned, 'WindowsOwnedJob', return_value=child), \
                    patch.object(owned, '_Capture', return_value=capture), \
                    patch.object(owned.time, 'monotonic', side_effect=[0, 2]):
                with self.assertRaises(subprocess.TimeoutExpired) as error:
                    job.run(self.args + ['PRIVATE_FAKE_PROXY_PASSWORD'], env=self.env, cwd=self.root, timeout=1)
            self.assertEqual(error.exception.cmd, 'owned Android process')
            self.assertNotIn('PRIVATE_FAKE', str(error.exception))
            self.assertIsNone(error.exception.output)
            child.close.assert_called_once_with(timeout=10)
            capture.close.assert_called_once()
            self.assertEqual(job._handle, 10)
            self.assertFalse(any(event[0] == 'terminate_job' for event in api.events))

    def test_failed_capture_cleanup_stays_owned_by_session(self):
        api = FakeAPI()
        job = self.job(api)
        child = SimpleNamespace(_handle=20, _api=object(), _start=MagicMock(return_value=SimpleNamespace(poll=lambda: 0)))
        child.close = MagicMock(side_effect=OSError('PRIVATE_FAKE_PROXY_PASSWORD'))
        capture = MagicMock(handles=(31, 32, 33), complete=True)
        with patch.object(owned, 'WindowsOwnedJob', return_value=child), patch.object(owned, '_Capture', return_value=capture):
            with self.assertRaises(owned.OwnedProcessError) as error:
                job.run(self.args, env=self.env, cwd=self.root, timeout=1)
        self.assertNotIn('PRIVATE_FAKE', str(error.exception))
        self.assertEqual(job._capture_jobs, [child])
        capture.close.assert_called_once()
        child.close = lambda **kwargs: setattr(child, '_handle', None)
        job.close()

    def test_unknown_job_state_preserves_ownership_without_exposing_native_error(self):
        api = FakeAPI()
        job = self.job(api)
        self.start(job)
        api.fail = 'active_processes'
        with self.assertRaises(owned.OwnedProcessError) as error:
            job.close()
        self.assertNotIn('PRIVATE_FAKE', str(error.exception))
        self.assertEqual(api.closed, [201])
        self.assertEqual(job._handle, 10)
        api.fail = None
        job.close()

    def test_context_preserves_original_failure_when_cleanup_also_fails(self):
        api = FakeAPI('terminate_job')
        api.descendants = 1
        original = ValueError('registration failed')
        job = self.job(api)
        with self.assertRaises(ValueError) as raised:
            with job:
                raise original
        self.assertIs(raised.exception, original)
        self.assertEqual(api.closed, [])
        self.assertTrue(getattr(original, '__notes__', []))
        self.assertNotIn('PRIVATE_FAKE', str(getattr(original, '__notes__', [])))
        api.fail = None
        api.descendants = 0
        job.close()

    def test_proxy_arguments_and_relative_executable_are_rejected_before_create(self):
        api = FakeAPI()
        with self.job(api) as job:
            for args in (['emulator.exe'], self.args + ['-http-proxy', '127.0.0.1:1'],
                         self.args + ['http://user:PRIVATE_FAKE_PROXY_PASSWORD@127.0.0.1:1'],
                         self.args + ['--proxy-server=127.0.0.1:1']):
                with self.subTest(kind=args[0]), self.assertRaises(owned.OwnedProcessError):
                    job.start(args, env=self.env, cwd=self.root)
            self.assertFalse(any(e[0] == 'create_process' for e in api.events))

    def test_only_exact_credential_free_loopback_http_bridge_is_allowed_in_arguments(self):
        api = FakeAPI()
        with self.job(api) as job:
            for bridge in ('http://127.0.0.1:34213', 'http://[::1]:34213'):
                args = self.args + ['-http-proxy', bridge]
                with patch.object(api, 'create_process', wraps=api.create_process) as create:
                    job.start(args, env=self.env, cwd=self.root)
                    create.assert_called_once_with(args, self.env, self.root)
            count = api.sequence
            for bridge in ('http://external.example:34213', 'http://127.0.0.1:34213/',
                           'http://127.0.0.1:34213?q=1', 'http://127.0.0.1:34213#f',
                           'http://user:PRIVATE_FAKE_PROXY_PASSWORD@127.0.0.1:34213',
                           'https://127.0.0.1:34213', 'http://127.0.0.1:0',
                           ' http://127.0.0.1:34213', 'http://127.0.0.1:34213\n'):
                with self.assertRaises(owned.OwnedProcessError):
                    job.start(self.args + ['-http-proxy', bridge], env=self.env, cwd=self.root)
            self.assertEqual(api.sequence, count)

    def test_invalid_timeouts_never_call_native_wait(self):
        api = FakeAPI()
        with self.job(api) as job:
            process = self.start(job)
            for value in (True, -1, float('nan'), float('inf'), '1'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    process.wait(value)
            self.assertFalse(any(e[0] == 'wait' for e in api.events))

    def test_poll_and_close_errors_do_not_echo_native_details(self):
        api = FakeAPI()
        with self.job(api) as job:
            process = self.start(job)
            api.fail = 'wait'
            with self.assertRaises(owned.OwnedProcessError) as error:
                process.poll()
            self.assertNotIn('PRIVATE_FAKE', str(error.exception))
            with self.assertRaises(owned.OwnedProcessError) as error:
                process.close()
            self.assertNotIn('PRIVATE_FAKE', str(error.exception))
            self.assertIn(101, api.closed)
            api.fail = None


if __name__ == '__main__':
    unittest.main()
