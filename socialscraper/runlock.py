"""Ausführungssperre: es läuft immer nur ein Browserauftrag gleichzeitig (Chrome-Profil-Sperre).

Die Sperre ist eine Betriebssystem-Sperre (Windows: msvcrt.locking, sonst fcntl.flock) auf der Datei
`data/run.lock`. Stirbt der Besitzer (Absturz, Prozess beendet), gibt das Betriebssystem die Sperre
selbst frei – es gibt also keine „hängenden“ Sperren, die man von Hand löschen müsste. Besitzerinfo
(PID, Name, Startzeit) steht als JSON hinter den ersten 16 Bytes der Datei und ist nur gültig, solange
die Sperre gehalten wird.

    with runlock.acquire(root, 'daemon'):     # wirft Busy, wenn schon jemand arbeitet
        ...
    runlock.holder(root)                       # None oder {'owner','pid','since'} für die Oberfläche
"""
import contextlib
import datetime
import json
import os
import pathlib
import time

_INFO_OFFSET = 16
_NAMES = {'daemon': 'der Hintergrunddienst', 'gui': 'das Fenster', 'cli': 'ein Befehl in der Eingabeaufforderung'}


class Busy(RuntimeError):
    """Die Sperre gehört gerade jemand anderem. `info` beschreibt den Besitzer."""

    def __init__(self, info=None):
        self.info = info or {}
        super().__init__(describe(self.info))


def describe(info):
    """Verständlicher deutscher Satz für die Meldung „es läuft schon etwas“."""
    info = info or {}
    who = _NAMES.get(info.get('owner'), info.get('owner') or 'ein anderer Auftrag')
    since = info.get('since')
    when = f' seit {since[11:16]} Uhr (UTC)' if isinstance(since, str) and len(since) >= 16 else ''
    pid = f', PID {info["pid"]}' if info.get('pid') else ''
    return (f'Es läuft gerade schon ein Browserauftrag ({who}{when}{pid}). '
            'Bitte warten, bis er fertig ist, und es dann noch einmal versuchen.')


def data_dir(root):
    return pathlib.Path(root) / 'data'


def lock_path(root):
    return data_dir(root) / 'run.lock'


def root_for_db(db_path):
    """Projektordner zu einem Datenbankpfad: `x/data/scrape.db` -> `x`, sonst der Ordner der Datenbank."""
    parent = pathlib.Path(db_path).resolve().parent
    return parent.parent if parent.name == 'data' else parent


class OsLock:
    """Eine Betriebssystem-Sperre auf Byte 0 einer Datei; Besitzerinfo ab Byte 16."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self._fd = None

    @staticmethod
    def _try_lock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Byte 0 darf auch jenseits des Dateiende gesperrt werden. Erst nach
        # erfolgreicher Sperre schreiben: ein anderer Prozess kann sie schon halten.
        return os.open(str(self.path), os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0))

    def try_acquire(self, info, tries=1, delay=0.05):
        """Sperre nehmen; mit `tries` > 1 wird kurz wiederholt (ein gleichzeitiges Nachsehen anderer sperrt Byte 0 für Millisekunden)."""
        if self._fd is not None:
            return False
        fd = self._open()
        for attempt in range(max(1, tries)):
            try:
                self._try_lock(fd)
                break
            except OSError:
                if attempt + 1 >= tries:
                    os.close(fd)
                    return False
                time.sleep(delay)
        self._fd = fd
        try:
            if os.fstat(fd).st_size < _INFO_OFFSET:
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, b'\0' * _INFO_OFFSET)
            data = json.dumps(info, ensure_ascii=False).encode('utf-8')
            os.lseek(fd, _INFO_OFFSET, os.SEEK_SET)
            os.write(fd, data)
            os.ftruncate(fd, _INFO_OFFSET + len(data))
        except OSError:
            pass  # Die Info ist nur Anzeige; die Sperre gilt trotzdem.
        return True

    def release(self):
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.ftruncate(fd, _INFO_OFFSET)
        except OSError:
            pass
        try:
            self._unlock(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    @staticmethod
    def _read_owner(fd):
        """Besitzerinfo ab Byte 16; {} wenn leer oder unlesbar."""
        try:
            os.lseek(fd, _INFO_OFFSET, os.SEEK_SET)
            raw = os.read(fd, 4096)
            info = json.loads(raw.decode('utf-8')) if raw.strip(b'\0') else {}
            return info if isinstance(info, dict) else {}
        except (OSError, ValueError):
            return {}

    def read_info(self, tries=5, delay=0.02):
        """(gehalten?, Besitzerinfo oder {}) – prüft kurz, ob die Sperre frei wäre.

        Ein gleichzeitiges Nachsehen (anderer Thread oder Prozess) sperrt Byte 0 nur für Millisekunden und
        hinterlässt keine Besitzerinfo. Scheitert der Versuch ohne Info, wird deshalb ein paar Mal kurz wiederholt,
        bevor „gehalten“ gemeldet wird; ein echter Besitzer hat seine Info geschrieben und wird sofort gemeldet."""
        try:
            fd = self._open()
        except OSError:
            return False, {}
        try:
            for attempt in range(max(1, tries)):
                try:
                    self._try_lock(fd)
                except OSError:
                    pass
                else:
                    self._unlock(fd)
                    return False, {}
                info = self._read_owner(fd)
                if info or attempt + 1 >= tries:
                    return True, info
                time.sleep(delay)
            return True, {}
        finally:
            os.close(fd)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def holder(root):
    """Wer hält die Sperre gerade? `None`, wenn niemand; sonst {'owner','pid','since'}."""
    held, info = OsLock(lock_path(root)).read_info()
    if not held:
        return None
    return {'owner': info.get('owner') or 'unbekannt', 'pid': info.get('pid'), 'since': info.get('since')}


@contextlib.contextmanager
def acquire(root, owner, wait=False, timeout=None, poll=0.5):
    """Sperre nehmen; Kontextmanager. `wait=False`: sofort `Busy`. `wait=True`: warten (höchstens `timeout` Sekunden)."""
    lock = OsLock(lock_path(root))
    info = {'owner': owner, 'pid': os.getpid(), 'since': _now()}
    deadline = None if timeout is None else time.monotonic() + timeout
    # Ein gleichzeitiges `holder()` prüft die Sperre kurz selbst; deshalb jeweils ein paar schnelle Versuche.
    while not lock.try_acquire(info, tries=4):
        if not wait or (deadline is not None and time.monotonic() >= deadline):
            raise Busy(holder(root))
        time.sleep(poll)
    try:
        yield info
    finally:
        lock.release()
