"""Own Windows Android child processes before their first instruction runs.

Only a credential-free loopback HTTP bridge may be supplied as -http-proxy.
Callers provide an explicit environment and absolute executable/cwd; errors
never echo either.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
KILL_ON_JOB_CLOSE = 0x00002000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
INFINITE = 0xFFFFFFFF
CAPTURE_LIMIT = 8 * 1024 * 1024
_CAPTURE_START_LOCK = threading.Lock()


class OwnedProcessError(RuntimeError):
    """A sanitized failure of the Android process-ownership boundary."""


def _loopback_proxy(value):
    try:
        parsed = urlsplit(value)
        if not parsed.port or parsed.hostname not in ('127.0.0.1', '::1'):
            return False
        host = '[::1]' if parsed.hostname == '::1' else '127.0.0.1'
        return value == f'http://{host}:{parsed.port}'
    except (ValueError, TypeError):
        return False


class _WindowsAPI:
    def __init__(self):
        if os.name != 'nt':
            raise OwnedProcessError('Android-Prozessbesitz benötigt Windows.')
        import ctypes
        from ctypes import wintypes as w
        import _winapi
        self.ctypes, self.winapi = ctypes, _winapi
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)

        class Limits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active_limit', w.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Limits), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [('user_time', ctypes.c_longlong), ('kernel_time', ctypes.c_longlong),
                        ('period_user_time', ctypes.c_longlong), ('period_kernel_time', ctypes.c_longlong),
                        ('page_faults', w.DWORD), ('total_processes', w.DWORD),
                        ('active_processes', w.DWORD), ('terminated_processes', w.DWORD)]

        self.Extended = Extended
        self.Accounting = Accounting
        for name, args, result in (
            ('CreateJobObjectW', [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            ('SetInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
            ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
            ('QueryInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL),
            ('IsProcessInJob', [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
            ('ResumeThread', [w.HANDLE], w.DWORD),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result

    def create_job(self):
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OwnedProcessError('Android-Prozessgruppe konnte nicht erstellt werden.')
        return handle

    def configure_job(self, handle):
        limits = self.Extended()
        limits.basic.flags = KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(handle, 9, self.ctypes.byref(limits), self.ctypes.sizeof(limits)):
            raise OwnedProcessError('Android-Prozessgruppe konnte nicht abgesichert werden.')

    def create_process(self, args, env, cwd, std_handles=None):
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        if std_handles is not None:
            startup.dwFlags |= subprocess.STARTF_USESTDHANDLES
            startup.hStdInput, startup.hStdOutput, startup.hStdError = std_handles
            startup.lpAttributeList = {'handle_list': list(std_handles)}
        return self.winapi.CreateProcess(
            args[0], subprocess.list2cmdline(args), None, None, std_handles is not None,
            CREATE_SUSPENDED | CREATE_NO_WINDOW, env, cwd, startup)

    def assign(self, job, process):
        if not self.kernel.AssignProcessToJobObject(job, process):
            raise OwnedProcessError('Android-Prozess konnte nicht sicher zugeordnet werden.')

    def resume(self, thread):
        if self.kernel.ResumeThread(thread) != 1:
            raise OwnedProcessError('Android-Prozess konnte nicht kontrolliert gestartet werden.')

    def wait(self, handle, milliseconds):
        return self.winapi.WaitForSingleObject(handle, milliseconds)

    def exit_code(self, handle):
        return self.winapi.GetExitCodeProcess(handle)

    def terminate(self, handle):
        self.winapi.TerminateProcess(handle, 1)

    def terminate_job(self, handle):
        if not self.kernel.TerminateJobObject(handle, 1):
            raise OwnedProcessError('Android-Prozessgruppe konnte nicht beendet werden.')

    def active_processes(self, handle):
        accounting = self.Accounting()
        if not self.kernel.QueryInformationJobObject(
                handle, 1, self.ctypes.byref(accounting), self.ctypes.sizeof(accounting), None):
            raise OwnedProcessError('Ende der Android-Prozessgruppe konnte nicht bestätigt werden.')
        return accounting.active_processes

    def process_handles(self, job):
        """Snapshot read/synchronize handles, validating membership after open."""
        from ctypes import wintypes as w
        capacity = 16
        while capacity <= 16384:
            class ProcessList(self.ctypes.Structure):
                _fields_ = [('assigned', w.DWORD), ('count', w.DWORD),
                            ('pids', self.ctypes.c_size_t * capacity)]
            listing = ProcessList()
            if self.kernel.QueryInformationJobObject(
                    job, 3, self.ctypes.byref(listing), self.ctypes.sizeof(listing), None):
                break
            if self.ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
                raise OwnedProcessError('Eigene Android-Kindprozesse konnten nicht erfasst werden.')
            capacity = max(capacity * 2, listing.assigned)
        else:
            raise OwnedProcessError('Android-Prozessliste überschreitet die sichere Grenze.')
        if listing.count > capacity:
            raise OwnedProcessError('Ungültige Android-Prozessliste.')
        handles = []
        try:
            for pid in listing.pids[:listing.count]:
                try:
                    process = self.winapi.OpenProcess(0x00100000 | 0x00001000, False, pid)
                except OSError as error:
                    if getattr(error, 'winerror', None) == 87:  # Already gone.
                        continue
                    raise OwnedProcessError('Android-Kindprozessende konnte nicht abgesichert werden.') from None
                belongs = w.BOOL()
                if not self.kernel.IsProcessInJob(process, job, self.ctypes.byref(belongs)):
                    self.close_handle(process)
                    raise OwnedProcessError('Android-Prozessbesitz konnte nicht erneut bestätigt werden.')
                if not belongs.value:  # PID reused or no longer in this job.
                    self.close_handle(process)
                    continue
                handles.append((int(pid), process))
            return handles
        except BaseException:
            for _, process in handles:
                self.close_handle(process)
            raise

    def close_handle(self, handle):
        self.winapi.CloseHandle(handle)


class _Capture:
    """Anonymous pipes polled without blocking reader threads or disk output."""
    def __init__(self, api):
        import msvcrt
        self.api = api
        self.fds = set()
        self.chunks = [[], []]
        self.total = 0
        self.eof = [False, False]
        try:
            pairs = []
            for _ in range(3):
                pair = os.pipe()
                self.fds.update(pair)
                pairs.append(pair)
            self.stdin_read, self.stdin_write = pairs[0]
            self.reads = [pairs[1][0], pairs[2][0]]
            self.child_fds = [self.stdin_read, pairs[1][1], pairs[2][1]]
            self.handles = tuple(msvcrt.get_osfhandle(fd) for fd in self.child_fds)
            self.read_handles = [msvcrt.get_osfhandle(fd) for fd in self.reads]
        except BaseException:
            self.close()
            raise

    def arm(self):
        for fd in self.child_fds:
            os.set_inheritable(fd, True)

    def spawned(self):
        # No input is supplied. Closing the writer gives the child immediate EOF.
        for fd in self.child_fds + [self.stdin_write]:
            os.close(fd)
            self.fds.remove(fd)

    @property
    def complete(self):
        return all(self.eof)

    def drain(self):
        for index, handle in enumerate(self.read_handles):
            if self.eof[index]:
                continue
            try:
                available = self.api.winapi.PeekNamedPipe(handle)[0]
                if not available:
                    continue
                data, code = self.api.winapi.ReadFile(handle, min(available, 65536, CAPTURE_LIMIT - self.total + 1))
                if code:
                    raise OwnedProcessError('Android-Prozessausgabe konnte nicht gelesen werden.')
            except OSError as error:
                if getattr(error, 'winerror', None) in (109, 232):
                    self.eof[index] = True
                    continue
                raise OwnedProcessError('Android-Prozessausgabe konnte nicht gelesen werden.') from None
            self.total += len(data)
            if self.total > CAPTURE_LIMIT:
                raise OwnedProcessError('Android-Prozessausgabe überschreitet die Größenbegrenzung.')
            self.chunks[index].append(data)

    def result(self):
        return tuple(b''.join(chunks) for chunks in self.chunks)

    def close(self):
        failed = False
        for fd in tuple(self.fds):
            try:
                os.close(fd)
            except OSError:
                failed = True
            else:
                self.fds.remove(fd)
        if failed:
            raise OwnedProcessError('Android-Ausgabehandles konnten nicht geschlossen werden.')


class OwnedProcess:
    def __init__(self, api, handle, pid):
        self._api, self._handle, self.pid = api, handle, pid
        self.returncode = None
        self._lock = threading.RLock()

    def poll(self):
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            if self._handle is None:
                raise OwnedProcessError('Android-Prozesshandle ist geschlossen.')
            try:
                state = self._api.wait(self._handle, 0)
                if state == WAIT_OBJECT_0:
                    self.returncode = self._api.exit_code(self._handle)
                elif state != WAIT_TIMEOUT:
                    raise OwnedProcessError('Android-Prozessstatus konnte nicht gelesen werden.')
            except Exception:
                raise OwnedProcessError('Android-Prozessstatus konnte nicht gelesen werden.') from None
            return self.returncode

    def wait(self, timeout=None):
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or not math.isfinite(timeout) or timeout < 0):
            raise ValueError('Ungültige Wartezeit für Android-Prozess.')
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # Release the lock at least every 100 ms so cancellation can stop a
            # waiting process. Never close a handle while a native wait uses it.
            milliseconds = 100 if deadline is None else math.ceil(min(0.1, max(0, deadline - time.monotonic())) * 1000)
            with self._lock:
                if self.returncode is not None:
                    return self.returncode
                if self._handle is None:
                    raise OwnedProcessError('Android-Prozesshandle ist geschlossen.')
                try:
                    state = self._api.wait(self._handle, milliseconds)
                    if state == WAIT_OBJECT_0:
                        self.returncode = self._api.exit_code(self._handle)
                        return self.returncode
                    if state != WAIT_TIMEOUT:
                        raise OwnedProcessError('Android-Prozessstatus konnte nicht gelesen werden.')
                except Exception:
                    raise OwnedProcessError('Android-Prozessstatus konnte nicht gelesen werden.') from None
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('owned Android process', timeout)

    def terminate(self):
        with self._lock:
            if self.poll() is not None:
                return
            try:
                self._api.terminate(self._handle)
            except Exception:
                # The process may have exited between poll and TerminateProcess.
                if self.poll() is None:
                    raise OwnedProcessError('Eigener Android-Prozess konnte nicht beendet werden.') from None

    def close(self):
        """Release this handle; the owning job still contains the process."""
        with self._lock:
            if self._handle is None:
                return
            handle, self._handle = self._handle, None
            failed = False
            try:
                if self._api.wait(handle, 0) == WAIT_OBJECT_0:
                    self.returncode = self._api.exit_code(handle)
            except Exception:
                failed = True
            finally:
                try:
                    self._api.close_handle(handle)
                except Exception:
                    failed = True
            if failed:
                raise OwnedProcessError('Android-Prozesshandle konnte nicht geschlossen werden.') from None


class WindowsOwnedJob:
    """One non-breakaway, kill-on-close job containing only children we create."""
    def __init__(self):
        self._lock = threading.RLock()
        self._handle = None
        self._processes = []
        self._capture_jobs = []
        self._observed_handles = {}
        self._closing = False
        try:
            self._api = _WindowsAPI()
            self._handle = self._api.create_job()
            self._api.configure_job(self._handle)
        except Exception:
            if self._handle is not None:
                handle, self._handle = self._handle, None
                try:
                    self._api.close_handle(handle)
                except Exception:
                    pass
            raise OwnedProcessError('Android-Prozessgruppe konnte nicht abgesichert werden.') from None

    def start(self, args, *, env, cwd):
        return self._start(args, env=env, cwd=cwd)

    def _start(self, args, *, env, cwd, parent_job=None, std_handles=None):
        if (not isinstance(args, (list, tuple)) or not args
                or any(not isinstance(arg, str) or '\0' in arg for arg in args)
                or not Path(args[0]).is_absolute()
                or not isinstance(env, dict)
                or any(not isinstance(key, str) or not isinstance(value, str)
                       or '\0' in key or '\0' in value for key, value in env.items())
                or not isinstance(cwd, (str, os.PathLike)) or not Path(cwd).is_absolute()):
            raise OwnedProcessError('Ungültige Android-Prozessparameter.')
        allowed_proxy = set()
        if args.count('-http-proxy') == 1:
            position = args.index('-http-proxy')
            if position + 1 < len(args) and _loopback_proxy(args[position + 1]):
                allowed_proxy.update((position, position + 1))
        if any(index not in allowed_proxy and
               (re.search(r'(?i)(?:https?|socks4a?|socks5h?)://', arg)
                or arg.split('=', 1)[0].lower() in
                ('-http-proxy', '--http-proxy', '--https-proxy', '--proxy', '--proxy-server', '--proxy-pac-url'))
               for index, arg in enumerate(args)):
            raise OwnedProcessError('Android-Prozessargumente erlauben nur die lokale Proxy-Brücke ohne Zugangsdaten.')
        with self._lock:
            if self._handle is None or self._closing:
                raise OwnedProcessError('Android-Prozessgruppe ist geschlossen.')
            process = thread = owned = None
            try:
                if std_handles is None:
                    process, thread, pid, _tid = self._api.create_process(list(args), dict(env), os.fspath(cwd))
                else:
                    process, thread, pid, _tid = self._api.create_process(list(args), dict(env), os.fspath(cwd), std_handles)
                # Track even a failed assignment: a suspended process can exist
                # outside this job until its individual exit is confirmed.
                owned = OwnedProcess(self._api, process, pid)
                self._processes.append(owned)
                if parent_job is not None:
                    self._api.assign(parent_job, process)
                self._api.assign(self._handle, process)
                self._api.resume(thread)
                self._api.close_handle(thread)
                thread = None
                return owned
            except BaseException as error:
                cleanup_failed = False
                if process is not None:
                    try:
                        self._api.terminate(process)
                    except Exception:
                        cleanup_failed = True
                    if owned is not None:
                        try:
                            owned.wait(timeout=5)
                            owned.close()
                            self._processes.remove(owned)
                        except Exception:
                            cleanup_failed = True
                if thread is not None:
                    try:
                        self._api.close_handle(thread)
                    except Exception:
                        cleanup_failed = True
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                message = 'Android-Prozess konnte nicht sicher gestartet werden.'
                if cleanup_failed:
                    self._closing = True
                    message += ' Die Bereinigung konnte nicht vollständig bestätigt werden.'
                raise OwnedProcessError(message) from None

    def run(self, args, *, env, cwd, timeout):
        """Capture one command in a nested job; its descendants cannot outlive it.

        Timeout bounds command execution; confirmed cleanup has a separate
        ten-second budget. Returned args and timeout errors contain no command.
        """
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError('Ungültige Wartezeit für Android-Aufruf.')
        deadline = time.monotonic() + timeout
        with self._lock:
            if self._handle is None or self._closing:
                raise OwnedProcessError('Android-Prozessgruppe ist geschlossen.')
            child = WindowsOwnedJob()
            self._capture_jobs.append(child)
        capture = None
        try:
            capture = _Capture(child._api)
            with _CAPTURE_START_LOCK, self._lock:
                if self._handle is None or self._closing:
                    raise OwnedProcessError('Android-Prozessgruppe ist geschlossen.')
                capture.arm()
                process = child._start(args, env=env, cwd=cwd,
                                       parent_job=self._handle, std_handles=capture.handles)
                capture.spawned()
            while True:
                capture.drain()
                returncode = process.poll()
                if returncode is not None:
                    break
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired('owned Android process', timeout)
                time.sleep(min(.005, max(0, deadline - time.monotonic())))
            child.close(timeout=10)
            drain_deadline = time.monotonic() + 1
            while not capture.complete:
                capture.drain()
                if time.monotonic() >= drain_deadline:
                    raise OwnedProcessError('Ende der Android-Prozessausgabe konnte nicht bestätigt werden.')
            stdout, stderr = capture.result()
            return subprocess.CompletedProcess('owned Android process', returncode, stdout, stderr)
        except (subprocess.TimeoutExpired, OwnedProcessError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise OwnedProcessError('Android-Aufruf konnte nicht abgeschlossen werden.') from None
        finally:
            failed = False
            try:
                child.close(timeout=10)
            except Exception:
                failed = True
            if child._handle is None:
                with self._lock:
                    if child in self._capture_jobs:
                        self._capture_jobs.remove(child)
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    failed = True
            if failed:
                raise OwnedProcessError('Android-Aufruf wurde nicht vollständig bereinigt; Sitzungsbesitz bleibt erhalten.') from None

    def close(self, timeout=10):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout < 0):
            raise ValueError('Ungültige Wartezeit für Android-Prozessgruppe.')
        with self._lock:
            if self._handle is None:
                return
            self._closing = True
            handle = self._handle
            terminate_failed = False
            deadline = time.monotonic() + timeout
            def observe():
                for pid, process_handle in self._api.process_handles(handle):
                    if pid in self._observed_handles:
                        self._api.close_handle(process_handle)
                    else:
                        self._observed_handles[pid] = process_handle
            observation_failed = False
            try:
                observe()
            except Exception:
                observation_failed = True
            for process in self._processes:
                if process._handle is not None:
                    try:
                        process.terminate()
                    except Exception:
                        pass  # The job fallback and exit proof below still run.
            try:
                self._api.terminate_job(handle)
            except Exception:
                terminate_failed = True
            while True:
                try:
                    observe()
                    active = self._api.active_processes(handle)
                except Exception:
                    raise OwnedProcessError('Ende der Android-Prozessgruppe konnte nicht bestätigt werden; Besitz bleibt erhalten.') from None
                if not isinstance(active, int) or isinstance(active, bool) or active < 0:
                    raise OwnedProcessError('Ungültiger Status der Android-Prozessgruppe; Besitz bleibt erhalten.')
                if active == 0:
                    break
                if terminate_failed or time.monotonic() >= deadline:
                    raise OwnedProcessError('Android-Prozessgruppe enthält noch aktive Prozesse; Besitz bleibt erhalten.') from None
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
            if observation_failed:
                raise OwnedProcessError('Android-Kindprozessbesitz nicht vollständig bestätigt; Besitz bleibt erhalten.')
            # A successful termination request is asynchronous. Only the job's
            # zero-active count proves that termination has reached every child.
            # Windows may decrement it before individual handles are signaled.
            # Keep all handles until their actual exit is confirmed too.
            try:
                for process in self._processes:
                    if process._handle is not None:
                        process.wait(timeout=max(0, deadline - time.monotonic()))
                for child in self._capture_jobs:
                    child.close(timeout=max(0, deadline - time.monotonic()))
                for process_handle in self._observed_handles.values():
                    remaining_ms = min(INFINITE - 1, math.ceil(max(0, deadline - time.monotonic()) * 1000))
                    if self._api.wait(process_handle, remaining_ms) != WAIT_OBJECT_0:
                        raise OwnedProcessError('Android-Kindprozess ist noch nicht beendet.')
            except Exception:
                raise OwnedProcessError('Prozessende der Android-Prozessgruppe noch nicht bestätigt; Besitz bleibt erhalten.') from None
            failed = False
            for process in self._processes:
                try:
                    process.close()
                except Exception:
                    failed = True
            for pid, process_handle in tuple(self._observed_handles.items()):
                try:
                    self._api.close_handle(process_handle)
                except Exception:
                    failed = True
                else:
                    del self._observed_handles[pid]
            try:
                self._api.close_handle(handle)
            except Exception:
                failed = True
            else:
                self._handle = None
                self._processes.clear()
                self._capture_jobs.clear()
            if failed:
                raise OwnedProcessError('Android-Prozessgruppe wurde nicht vollständig bereinigt.') from None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except OwnedProcessError as error:
            if exc is None:
                raise
            if hasattr(exc, 'add_note'):
                exc.add_note(str(error))
        return False
