"""Hartes Zeitlimit für Abrufe, die ein Browser ausführt.

    with deadline(120, 'Der Video-Abruf') as dl:
        adapter.fetch(...)
    dl.fired    # True, wenn das Limit erreicht wurde

Ein Wächter-Thread beendet bei Ablauf die Unterprozesse dieses Programms (Browser, Treiber) – nur per PID, nie per
Programmname, nur Browser und Treiber; Tor und der Hintergrunddienst bleiben unberührt. Der blockierte Aufruf bricht dadurch mit einem Fehler ab,
der hier in `FetchTimeout` mit klarer deutscher Meldung umgewandelt wird. Die Ausführungssperre gibt danach der Aufrufer
wie bei jedem anderen Fehler frei.
"""
import contextlib
import ctypes
import logging
import os
import threading

log = logging.getLogger(__name__)
# Nur diese Unterprozesse werden beendet (Browser und Treiber); Tor, Hintergrunddienst u. a. bleiben unberührt.
BROWSER = {'chrome.exe', 'chromium.exe', 'msedge.exe', 'firefox.exe', 'chrome_proxy.exe', 'headless_shell.exe', 'node.exe',
           'playwright.exe', 'conhost.exe'}


_local = threading.local()


class FetchTimeout(RuntimeError):
    retryable = False


def message(seconds, what):
    return (f'Zeitlimit erreicht: {what} hat länger als {int(seconds)} Sekunden gedauert und wurde abgebrochen '
            '(Browser geschlossen). Später noch einmal versuchen.')


def _kernel32():
    from ctypes import wintypes as w
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    declarations = {
        'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
        'Process32FirstW': ([w.HANDLE, ctypes.c_void_p], w.BOOL),
        'Process32NextW': ([w.HANDLE, ctypes.c_void_p], w.BOOL),
        'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        'GetProcessTimes': ([w.HANDLE, *[ctypes.POINTER(w.FILETIME)] * 4], w.BOOL),
        'GetSystemTimeAsFileTime': ([ctypes.POINTER(w.FILETIME)], None),
        'TerminateProcess': ([w.HANDLE, w.UINT], w.BOOL),
        'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
        'CloseHandle': ([w.HANDLE], w.BOOL),
    }
    for name, (args, result) in declarations.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def _filetime(value):
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def _read_creation(api, handle):
    from ctypes.wintypes import FILETIME
    created, exited, kernel, user = (FILETIME() for _ in range(4))
    if api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                           ctypes.byref(kernel), ctypes.byref(user)):
        return _filetime(created)
    return None


def _walk_children(table, parent, started, cutoff):
    root_created = started(parent)
    if root_created is None or root_created > cutoff:
        return []
    found, seen, todo = [], {parent}, [(parent, root_created)]
    while todo:
        current, parent_created = todo.pop()
        for pid, (ppid, name) in table.items():
            if ppid != current or pid in seen or pid == os.getpid():
                continue
            seen.add(pid)
            created = started(pid)
            # An old child of a reused parent PID is not ours. A PID reused
            # after the snapshot started is also ambiguous: fail closed.
            if created is None or not parent_created <= created <= cutoff:
                continue
            found.append((pid, name, created))
            todo.append((pid, created))
    return found


def _owned_windows(parent):
    from ctypes.wintypes import FILETIME
    class ENTRY(ctypes.Structure):
        _fields_ = [('dwSize', ctypes.c_ulong), ('cntUsage', ctypes.c_ulong), ('th32ProcessID', ctypes.c_ulong),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', ctypes.c_ulong),
                    ('cntThreads', ctypes.c_ulong), ('th32ParentProcessID', ctypes.c_ulong),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', ctypes.c_ulong), ('szExeFile', ctypes.c_wchar * 260)]
    k32 = _kernel32()
    root_handle = k32.OpenProcess(0x1000, False, parent)
    if not root_handle:
        return []
    try:
        root_created = _read_creation(k32, root_handle)
        if root_created is None:
            return []
        cutoff = FILETIME()
        k32.GetSystemTimeAsFileTime(ctypes.byref(cutoff))
        snapshot = k32.CreateToolhelp32Snapshot(0x2, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return []
        table = {}
        try:
            entry = ENTRY()
            entry.dwSize = ctypes.sizeof(ENTRY)
            ok = k32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile.lower())
                ok = k32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snapshot)

        def started(pid):
            if pid == parent:
                return root_created
            handle = k32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                return _read_creation(k32, handle)
            finally:
                k32.CloseHandle(handle)
        return _walk_children(table, parent, started, _filetime(cutoff))
    finally:
        k32.CloseHandle(root_handle)


def descendants(pid=None):
    """(PID, Name) aller Unterprozesse dieses Programms (nur Windows; sonst leer)."""
    if os.name != 'nt':
        return []
    try:
        return [(child, name) for child, name, _created in _owned_windows(pid or os.getpid())]
    except Exception:
        log.debug('Unterprozesse konnten nicht ermittelt werden', exc_info=True)
        return []


def terminate_children(parent, *, browser_only=False, wait_ms=0):
    """Terminate verified children, rechecking their identity on the actual handle."""
    if os.name != 'nt':
        return 0
    k32 = _kernel32()
    count = 0
    for pid, name, created in reversed(_owned_windows(parent)):
        if browser_only and (name not in BROWSER or name == 'conhost.exe'):
            continue
        access = 0x1001 | (0x00100000 if wait_ms else 0)  # QUERY | TERMINATE, optionally SYNCHRONIZE
        handle = k32.OpenProcess(access, False, pid)
        if handle:
            try:
                if _read_creation(k32, handle) != created:
                    continue
                ended = bool(k32.TerminateProcess(handle, 1))
                count += ended
                if ended and wait_ms:
                    k32.WaitForSingleObject(handle, wait_ms)
            finally:
                k32.CloseHandle(handle)
    return count


def kill_children():
    """Beendet nur verifizierte Browser-Unterprozesse dieses Programms."""
    return terminate_children(os.getpid(), browser_only=True)


def expired():
    """Ist in diesem Thread ein laufendes Zeitlimit abgelaufen? (Wiederholungen müssen dann enden.)"""
    return any(dl.fired for dl in getattr(_local, 'stack', ()))


class _Deadline:
    def __init__(self, seconds, what, kill):
        self.seconds, self.what, self.kill = seconds, what, kill
        self.fired = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def _fire(self):
        self.fired = True
        log.warning('%s: Zeitlimit von %s s erreicht', self.what, int(self.seconds))
        if self.kill:
            kill_children()

    @property
    def message(self):
        return message(self.seconds, self.what)


@contextlib.contextmanager
def deadline(seconds, what='Der Abruf', kill_browser=True):
    """Zeitlimit; ist es abgelaufen, wird jeder Fehler im Block zu `FetchTimeout` mit klarer Meldung."""
    dl = _Deadline(seconds, what, kill_browser)
    dl._timer.start()
    stack = _local.__dict__.setdefault('stack', [])
    stack.append(dl)
    try:
        yield dl
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        if dl.fired:
            raise FetchTimeout(dl.message) from error
        raise
    finally:
        dl._timer.cancel()
        stack.remove(dl)
