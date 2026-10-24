"""Hintergrunddienst: arbeitet fällige Ziele der Automatik nacheinander ab.

Dienst (läuft im eigenen Prozess):   `python -m socialscraper daemon [--once]`
Schnittstelle für das Fenster:      status(root), start_detached(root), stop(root),
                                    set_autostart(enabled), autostart_enabled()

Dateien in `<root>/data/`:
    daemon.json   {pid, started_at, last_beat, current} – alle ~10 s erneuert
    daemon.lock   Betriebssystem-Sperre: genau eine Instanz (fällt bei Absturz von selbst weg)
    daemon.stop   Wunschzettel „bitte beenden“ (von stop() geschrieben)
    daemon.log    Protokoll des Dienstes (vom Befehl `daemon` eingerichtet)
    run.lock      Ausführungssperre für Browseraufträge (siehe runlock.py; bei eigenem db_path am Ort der Datenbank)
"""
import json
import logging
import os
import pathlib
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from . import repo, runlock, scheduler
from .runlock import OsLock
from .store import Store

log = logging.getLogger(__name__)

BEAT_SECONDS = 10
STALE_AFTER = 60          # ohne Herzschlag so lange: „hängt“
POLL_SECONDS = 30
DB_BACKOFF_MIN = 2.0      # Wartezeit nach dem ersten Datenbankfehler, verdoppelt sich bis DB_BACKOFF_MAX
DB_BACKOFF_MAX = 60.0
RUNS_KEEP = 5000          # beim Start bleiben nur die letzten so vielen Läufe in `runs`
RETRY_AFTER_ERROR = 300   # nach einem Absturz eines Ziels so lange in Ruhe lassen
from .branding import NAME, AUTOSTART_NAME
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


class AlreadyRunning(RuntimeError):
    def __init__(self, pid=None):
        self.pid = pid
        super().__init__('Der Hintergrunddienst läuft schon' + (f' (PID {pid}).' if pid else '.'))


def _data(root):
    return pathlib.Path(root) / 'data'


def state_path(root):
    return _data(root) / 'daemon.json'


def _lock_path(root):
    return _data(root) / 'daemon.lock'


def stop_flag(root):
    return _data(root) / 'daemon.stop'


def log_path(root):
    return _data(root) / 'daemon.log'


def _now():
    return repo.iso()


def _write_state(root, state):
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'daemon.{os.getpid()}.tmp')
    for _ in range(5):
        try:
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, path)
            return True
        except OSError:
            time.sleep(0.05)
    try:
        tmp.unlink()
    except OSError:
        pass
    return False


def _read_state(root):
    for _ in range(3):
        try:
            data = json.loads(state_path(root).read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            time.sleep(0.05)
    return {}


# ---------- Schnittstelle für das Fenster ----------

def status(root, stale_after=STALE_AFTER):
    """Zustand des Dienstes: {'running','pid','started_at','last_beat','current','stale','age_s'}.

    `running` kommt aus der Betriebssystem-Sperre (stimmt auch nach einem Absturz sofort);
    `stale` heißt: der Dienst lebt, meldet sich aber seit `stale_after` Sekunden nicht mehr.
    """
    held, info = OsLock(_lock_path(root)).read_info()
    out = {'running': bool(held), 'pid': None, 'started_at': None, 'last_beat': None,
           'current': None, 'stale': False, 'age_s': None}
    if not held:
        return out
    state = _read_state(root)
    out['pid'] = info.get('pid') or state.get('pid')
    out['started_at'] = state.get('started_at') or info.get('since')
    out['last_beat'] = state.get('last_beat')
    out['current'] = state.get('current')
    if out['last_beat']:
        try:
            out['age_s'] = int((repo.now() - repo.parse(out['last_beat'])).total_seconds())
            out['stale'] = out['age_s'] > stale_after
        except ValueError:
            pass
    return out


def _python_for_service():
    exe = sys.executable
    if os.name == 'nt' and os.path.basename(exe).lower() == 'python.exe':
        w = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        if os.path.exists(w):
            return w
    return exe


def _service_command(extra=()):
    if getattr(sys, 'frozen', False):  # gepacktes Startprogramm
        return [sys.executable, 'daemon', *extra]
    return [_python_for_service(), '-m', 'socialscraper', 'daemon', *extra]


def start_detached(root, wait=15.0):
    """Startet den Dienst unsichtbar und getrennt vom Fenster. Gibt status(root) zurück.

    Läuft er schon, passiert nichts (genau eine Instanz). Wirft RuntimeError, wenn er nicht hochkommt.
    """
    root = pathlib.Path(root)
    current = status(root)
    if current['running']:
        return current
    _data(root).mkdir(parents=True, exist_ok=True)
    kwargs = {}
    if os.name == 'nt':
        kwargs['creationflags'] = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    try:
        proc = subprocess.Popen(_service_command(['--root', str(root)]), cwd=str(root), close_fds=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                **kwargs)
    except OSError as error:
        raise RuntimeError(f'Der Hintergrunddienst konnte nicht gestartet werden: {scheduler.clean_error(error)}') from None
    deadline = time.monotonic() + wait
    try:
        while time.monotonic() < deadline:
            current = status(root)
            if current['running']:
                return current
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    finally:
        if proc.returncode is None:
            proc.returncode = 0  # bewusst nicht abwarten: der Dienst läuft weiter; verhindert eine ResourceWarning
    raise RuntimeError('Der Hintergrunddienst ist nicht angelaufen. Mehr steht in data/daemon.log.')


def _kill(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'], capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        else:
            os.kill(int(pid), signal.SIGTERM)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass


def _recover_after_kill(root):
    """Nach einem erzwungenen Beenden: der unterbrochene Lauf darf nicht bis zum nächsten Dienststart als „Läuft …“
    stehen bleiben. Wie beim Start des Dienstes gilt: hält niemand die Ausführungssperre, ist jeder „running“-Lauf
    ein Überbleibsel. Fehler sind nie fatal."""
    try:
        cfg = scheduler.load_config(root)
        if runlock.holder(runlock.root_for_db(cfg.db_path)) is not None:
            return
        with Store(cfg.db_path) as store:
            repo.recover_stale_runs(store.db, 0)
            repo.recover_channel_items(store.db)
    except Exception as error:
        log.warning('Unterbrochener Lauf konnte nicht aufgeräumt werden: %s', scheduler.clean_error(error))


def stop(root, timeout=30.0, force=True):
    """Bittet den Dienst, sich zu beenden (er bricht das laufende Ziel sauber ab). True, wenn er danach nicht mehr läuft.

    Reagiert er innerhalb von `timeout` Sekunden nicht und ist `force` gesetzt, wird der Prozess beendet;
    ein dabei unterbrochener Lauf wird sofort als „unterbrochen“ markiert (nicht erst beim nächsten Start).
    """
    root = pathlib.Path(root)
    current = status(root)
    if not current['running']:
        return True
    _data(root).mkdir(parents=True, exist_ok=True)
    stop_flag(root).write_text(_now(), encoding='utf-8')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not status(root)['running']:
            return True
        time.sleep(0.3)
    if force and current['pid']:
        _kill(current['pid'])
        for _ in range(30):
            if not status(root)['running']:
                break
            time.sleep(0.2)
        if not status(root)['running']:
            _recover_after_kill(root)
    try:
        stop_flag(root).unlink()
    except OSError:
        pass
    return not status(root)['running']


# ---------- Autostart (Verknüpfung im Autostart-Ordner des Nutzers) ----------

def _startup_dir():
    override = os.environ.get('SS_AUTOSTART_DIR')
    if override:
        return pathlib.Path(override)
    appdata = os.environ.get('APPDATA')
    if not appdata:
        raise RuntimeError('Der Autostart-Ordner wurde nicht gefunden.')
    return pathlib.Path(appdata) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup'


def autostart_enabled():
    try:
        return (_startup_dir() / AUTOSTART_NAME).exists()
    except RuntimeError:
        return False


_LNK_PS = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:SS_LNK);'
           '$s.TargetPath=$env:SS_TARGET;$s.Arguments=$env:SS_ARGS;$s.WorkingDirectory=$env:SS_CWD;'
           '$s.WindowStyle=7;$s.Description=$env:SS_DESC;$s.Save()')


def set_autostart(enabled, root=None):
    """Schaltet den Start mit Windows ein/aus. Es wird nur eine Verknüpfung im Autostart-Ordner des
    Nutzers angelegt bzw. gelöscht – keine Registry, keine Systemeinstellung. Gibt den neuen Zustand zurück."""
    if os.name != 'nt':
        raise RuntimeError('Der Start mit Windows gilt nur unter Windows.')
    lnk = _startup_dir() / AUTOSTART_NAME
    if not enabled:
        try:
            lnk.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise RuntimeError(f'Die Verknüpfung konnte nicht entfernt werden: {scheduler.clean_error(error)}') from None
        return False
    root = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    cmd = _service_command(['--root', str(root)])
    lnk.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, SS_LNK=str(lnk), SS_TARGET=cmd[0], SS_CWD=str(root),
               SS_ARGS=subprocess.list2cmdline(cmd[1:]), SS_DESC=f'{NAME} Hintergrunddienst (automatische Abrufe)')
    try:
        done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', _LNK_PS], env=env,
                              capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f'Die Verknüpfung konnte nicht angelegt werden: {scheduler.clean_error(error)}') from None
    if done.returncode != 0 or not lnk.exists():
        raise RuntimeError('Die Verknüpfung im Autostart-Ordner konnte nicht angelegt werden.')
    return True


# ---------- Der Dienst selbst ----------

def recover_at_start(db):
    """Beim Start des Dienstes: Kanal-Videos, die nach einem Absturz auf „running“ stehen, kommen zurück in die Warteschlange."""
    try:
        n = repo.recover_channel_items(db)
        if n:
            log.info('%d Kanal-Videos aus einem unterbrochenen Lauf zurück in die Warteschlange', n)
        return n
    except sqlite3.Error as error:
        log.warning('Kanal-Warteschlange konnte nicht aufgeräumt werden: %s', error)
        return 0


def _push(root):
    """Sync nach jedem Lauf, falls das Modul da und eingerichtet ist; Fehler sind nie fatal."""
    try:
        from . import sync
    except ImportError:
        return
    try:
        sync.push_if_configured(root)
    except Exception as error:
        log.warning('Sync nach dem Lauf fehlgeschlagen: %s', scheduler.clean_error(error))


def _sentiment(root, db_path, result, cancel):
    """Nach einem Lauf (nur wenn Marc den Schalter eingeschaltet hat): die seit dem Lauf neuen Kommentare einstufen.
    Eine KI-Einschätzung mit Obergrenze pro Lauf; Fehler sind nie fatal und bringen den Lauf nie zum Scheitern."""
    try:
        from . import aiconfig, sentiment
        if not result.get('comments_new'):
            return
        cfg = aiconfig.load(root)
        if not cfg.get('auto_after_runs') or not aiconfig.has_key(cfg):
            return
        with Store(db_path) as store:
            row = store.db.execute('SELECT started_at FROM runs WHERE id=?', (result['run_id'],)).fetchone()
        if row and row[0]:
            sentiment.auto_for_run(root, db_path, row[0], cancel=cancel, cfg=cfg)
    except Exception as error:
        log.warning('Automatische Stimmung nach dem Lauf fehlgeschlagen: %s', type(error).__name__)


def _start_api(root, db_path):
    """Lokale Schnittstelle für PULSE mitstarten, falls aktiviert (Modul api.py); Fehler sind nie fatal."""
    try:
        from . import api
    except ImportError:
        return None
    try:
        handle, problem = api.start_if_enabled(root, db_path=db_path)
    except Exception as error:
        log.warning('Schnittstelle nicht gestartet: %s', scheduler.clean_error(error))
        return None
    if problem:
        log.warning('Schnittstelle nicht gestartet: %s', problem)
    return handle


def run(root, once=False, poll=POLL_SECONDS, stop_event=None, cfg=None, install_signals=True):
    """Hauptschleife. Gibt die Anzahl ausgeführter Ziele zurück. Wirft AlreadyRunning, wenn schon einer läuft."""
    root = pathlib.Path(root)
    _data(root).mkdir(parents=True, exist_ok=True)
    guard = OsLock(_lock_path(root))
    if not guard.try_acquire({'owner': 'daemon', 'pid': os.getpid(), 'since': _now()}, tries=8, delay=0.1):
        raise AlreadyRunning(OsLock(_lock_path(root)).read_info()[1].get('pid'))
    stop_ev = stop_event or threading.Event()
    try:
        stop_flag(root).unlink()
    except OSError:
        pass

    def stopping():
        if stop_ev.is_set():
            return True
        if stop_flag(root).exists():
            stop_ev.set()
        return stop_ev.is_set()

    if install_signals and threading.current_thread() is threading.main_thread():
        for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
            if hasattr(signal, name):
                try:
                    signal.signal(getattr(signal, name), lambda *_: stop_ev.set())
                except (ValueError, OSError):
                    pass

    state = {'pid': os.getpid(), 'started_at': _now(), 'last_beat': _now(), 'current': None}
    state_lock = threading.Lock()

    def beat():
        with state_lock:
            state['last_beat'] = _now()
            _write_state(root, dict(state))

    beat_stop = threading.Event()

    def beat_loop():
        while not beat_stop.wait(BEAT_SECONDS):
            beat()

    beat()
    thread = threading.Thread(target=beat_loop, name='daemon-beat', daemon=True)
    thread.start()
    ran = 0
    api_handle = None
    skip = {}  # Ziel-ID -> frühester Wiederversuch (monotonic), nach unerwarteten Fehlern
    db_backoff = 0.0  # Sekunden Wartezeit nach einem Datenbankfehler (wächst bis DB_BACKOFF_MAX)

    def pause(seconds):
        waited = 0.0
        while waited < seconds and not stopping():
            time.sleep(min(1.0, seconds - waited))
            waited += 1.0

    def db_problem(error):
        """Datenbank gesperrt/unlesbar: ins Protokoll, kurz warten (mit Rückfall), weitermachen."""
        nonlocal db_backoff
        db_backoff = min(DB_BACKOFF_MAX, max(DB_BACKOFF_MIN, db_backoff * 2))
        log.error('Datenbank gerade nicht nutzbar (%s: %s). Neuer Versuch in %d s.',
                  type(error).__name__, scheduler.clean_error(error), int(db_backoff))
        pause(db_backoff)

    try:
        cfg_db = cfg or scheduler.load_config(root)
        # Dieselbe Ausführungssperre wie die Befehle (cli.py, run-target, discover): sie liegt am Ort der Datenbank.
        # Sonst sperren Dienst und Fenster-Abruf bei eigenem db_path (SS_DB_PATH, settings.toml) verschiedene Dateien.
        lock_root = runlock.root_for_db(cfg_db.db_path)
        while not stopping():
            try:
                with Store(cfg_db.db_path) as store:
                    # Gehört niemand die Ausführungssperre, ist jeder „running“-Lauf ein Überbleibsel eines Absturzes.
                    repo.recover_stale_runs(store.db, 180 if runlock.holder(lock_root) else 0)
                    if not runlock.holder(lock_root):
                        recover_at_start(store.db)
                    repo.trim_runs(store.db, RUNS_KEEP)
                break
            except sqlite3.Error as error:
                if once:
                    raise
                db_problem(error)
        log.info('Hintergrunddienst gestartet (PID %s)', os.getpid())
        if not once:
            api_handle = _start_api(root, cfg_db.db_path)
        while not stopping():
            try:
                with Store(cfg_db.db_path) as store:
                    due = [t for t in repo.due_targets(store.db) if skip.get(t['id'], 0) <= time.monotonic()]
                db_backoff = 0.0
            except sqlite3.Error as error:
                if once:
                    raise
                db_problem(error)
                continue
            for target in due:
                if stopping():
                    break
                try:
                    with runlock.acquire(lock_root, 'daemon'):
                        with state_lock:
                            state['current'] = {'target_id': target['id'], 'kind': target['kind'],
                                                'platform': target['platform'], 'value': target['value'],
                                                'since': _now()}
                        beat()
                        result = scheduler.run_target(root, target['id'], 'daemon', cfg=cfg, cancel=stopping)
                    ran += 1
                    log.info('Ziel %s: %s', target['id'], result['status'])
                    _push(root)
                    _sentiment(root, cfg_db.db_path, result, stopping)
                except runlock.Busy as busy_error:
                    log.info('Übersprungen, Browser belegt: %s', busy_error)
                    break
                except Exception as error:
                    log.exception('Ziel %s: unerwarteter Fehler', target['id'])
                    skip[target['id']] = time.monotonic() + RETRY_AFTER_ERROR
                    log.error('%s', scheduler.clean_error(error))
                finally:
                    with state_lock:
                        state['current'] = None
                    beat()
            if once:
                break
            waited = 0.0
            while waited < poll and not stopping():
                time.sleep(1.0)
                waited += 1.0
        return ran
    finally:
        if api_handle is not None:
            try:
                api_handle.stop()
            except Exception:
                log.warning('Schnittstelle konnte nicht sauber beendet werden')
        beat_stop.set()
        thread.join(timeout=2)
        with state_lock:
            state['current'] = None
            state['stopped_at'] = _now()
            _write_state(root, dict(state))
        guard.release()
        try:
            stop_flag(root).unlink()
        except OSError:
            pass
        log.info('Hintergrunddienst beendet')
