"""Tor erkennen und prüfen. Es wird nichts heruntergeladen oder gestartet.

Tor muss schon laufen: der Tor Browser (SOCKS-Port 9150, Fenster offen lassen) oder der Tor-Dienst (Port 9050).
Die Namensauflösung läuft immer über Tor (`socks5h`).
"""
import json
import logging
import socket

from .proxy import Proxy
from . import tunnel

log = logging.getLogger(__name__)

HOST = '127.0.0.1'
PORTS = (9150, 9050)            # Tor Browser, Tor-Dienst
CONTROL_PORTS = (9151, 9051)    # passende Steuerports (nur ohne Passwort nutzbar)
CHECK_URL = 'https://check.torproject.org/api/ip'

NOT_RUNNING = ('Tor läuft nicht. Bitte den Tor Browser starten und offen lassen '
               '(er öffnet den Tor-Anschluss 127.0.0.1:9150), dann noch einmal versuchen. '
               'Es wird nichts automatisch heruntergeladen.')


class TorUnavailable(RuntimeError):
    retryable = False


def is_socks5(port, host=HOST, timeout=1.5):
    """Hört dort ein SOCKS5-Server? (Nur lokale Verbindung, es wird nichts gesendet außer dem Gruß.)"""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b'\x05\x01\x00')
            answer = sock.recv(2)
            return len(answer) == 2 and answer[0] == 5
    except OSError:
        return False


def detect(host=HOST, ports=PORTS, timeout=1.5):
    """Erster Port mit SOCKS5-Server oder None."""
    for port in ports:
        if is_socks5(port, host, timeout):
            return port
    return None


def proxy(port):
    p = Proxy('socks5', HOST, int(port))
    p.anonymous = True
    return p


def ytdlp_url(port):
    """Adresse für yt-dlp: `socks5h` = Namensauflösung über Tor (kein DNS-Leck)."""
    return f'socks5h://{HOST}:{int(port)}'


def check(port, timeout=25.0):
    """Verbindungstest durch Tor: ruft check.torproject.org auf und liest `IsTor`.

    Gibt dict(ok, is_tor, port, message) zurück; wirft nie.
    """
    try:
        status, body, seconds = tunnel.https_get(proxy(port), CHECK_URL, timeout=timeout, max_bytes=2048)
        data = json.loads(body)
    except tunnel.TlsBroken:
        return {'ok': False, 'is_tor': False, 'port': port,
                'message': 'Zertifikatsfehler beim Tor-Test – dem Tor-Ausgang wird nicht vertraut.'}
    except Exception:
        return {'ok': False, 'is_tor': False, 'port': port,
                'message': 'Tor ist da, aber der Verbindungstest ist fehlgeschlagen. '
                           'Im Tor Browser warten, bis „Mit Tor verbunden“ erscheint, und erneut versuchen.'}
    is_tor = bool(isinstance(data, dict) and data.get('IsTor') is True)
    if status != 200 or not is_tor:
        return {'ok': False, 'is_tor': False, 'port': port,
                'message': 'Der Anschluss ist kein Tor-Ausgang (check.torproject.org meldet „kein Tor“).'}
    return {'ok': True, 'is_tor': True, 'port': port, 'seconds': round(seconds, 1),
            'message': f'Tor funktioniert (Anschluss {port}, {seconds:.1f} s).'}


def find(verify=True, timeout=25.0):
    """Tor suchen und (bei `verify`) prüfen. Gibt dict(ok, port, message) zurück; wirft nie."""
    port = detect()
    if port is None:
        return {'ok': False, 'port': None, 'message': NOT_RUNNING}
    if not verify:
        return {'ok': True, 'port': port, 'message': f'Tor-Anschluss gefunden ({port}), noch nicht geprüft.'}
    result = check(port, timeout)
    return {'ok': result['ok'], 'port': port, 'message': result['message']}


def new_circuit(host=HOST, ports=CONTROL_PORTS, timeout=3.0):
    """Neuen Tor-Schaltkreis anfordern (NEWNYM) – nur wenn der Steuerport OHNE Passwort erreichbar ist.

    Der Tor Browser verlangt normalerweise ein Cookie; dann gibt es hier False (und nichts wird versucht zu umgehen).
    """
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(b'AUTHENTICATE ""\r\n')
                if not sock.recv(256).startswith(b'250'):
                    continue
                sock.sendall(b'SIGNAL NEWNYM\r\n')
                if sock.recv(256).startswith(b'250'):
                    return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------- Mitgeliefertes Tor starten/beenden
# Das Tor Expert Bundle liegt in tools\tor (Herkunft: tools\tor\HERKUNFT.txt). Gestartet wird es nur auf Anforderung,
# versteckt, mit Arbeitsordner = Projektordner. Beendet wird nur der selbst gestartete Prozess (PID aus tor.pid).
import os
import pathlib
import subprocess
import time

TOR_DIR = pathlib.Path('tools') / 'tor'
DEFAULT_PORT = 9050
BOOT_TIMEOUT = 90
_children = []   # gestartete Popen-Objekte (damit sie nicht vorzeitig freigegeben werden)


def tor_exe(root):
    return pathlib.Path(root) / TOR_DIR / 'tor' / 'tor.exe'


def missing_message(root):
    return (f'Das mitgelieferte Tor fehlt (erwartet in {pathlib.Path(root) / TOR_DIR}). Entweder den Ordner „tools\\tor“ '
            'mit dem Tor Expert Bundle wiederherstellen oder den Tor Browser starten und offen lassen.')


def _data_dir(root, data_dir):
    path = pathlib.Path(data_dir)
    return path if path.is_absolute() else pathlib.Path(root) / path


def write_torrc(root, port=DEFAULT_PORT, data_dir='data/tor', torrc=None):
    """torrc anlegen, falls sie fehlt (gibt den Pfad zurück). Pfade relativ zum Projektordner (= Arbeitsordner)."""
    path = pathlib.Path(torrc) if torrc else pathlib.Path(root) / 'data' / 'torrc'
    if not path.exists():
        data = pathlib.PurePath(data_dir).as_posix()
        text = (f'SocksPort 127.0.0.1:{int(port)}\nDataDirectory {data}\n'
                'GeoIPFile tools/tor/data/geoip\nGeoIPv6File tools/tor/data/geoip6\n'
                f'ClientOnly 1\nLog notice file {data}/tor.log\n')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return path


def _pid_alive_is_tor(pid):
    try:
        out = subprocess.run(['tasklist', '/FI', f'PID eq {int(pid)}', '/FO', 'CSV', '/NH'], capture_output=True,
                             text=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return 'tor.exe' in out.lower()


def ensure_running(root, port=DEFAULT_PORT, data_dir='data/tor', torrc=None, ports=PORTS,
                   timeout=BOOT_TIMEOUT, verify=True):
    """Tor bereitstellen: läuft schon ein Tor-Anschluss, wird er benutzt, sonst wird das mitgelieferte gestartet.

    Gibt dict(ok, port, started, message) zurück; wirft nie.
    """
    existing = detect(ports=ports)
    if existing is not None:
        found = check(existing) if verify else {'ok': True, 'message': f'Tor-Anschluss {existing} gefunden.'}
        return {'ok': found['ok'], 'port': existing, 'started': False, 'message': found['message']}
    exe = tor_exe(root)
    if not exe.exists():
        return {'ok': False, 'port': None, 'started': False, 'message': missing_message(root)}
    try:
        rc = write_torrc(root, port, data_dir, torrc)
        base = _data_dir(root, data_dir)
        base.mkdir(parents=True, exist_ok=True)
        log_path = base / 'tor.log'
        offset = log_path.stat().st_size if log_path.exists() else 0
        flags = (getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
                 | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        proc = subprocess.Popen([str(exe), '-f', str(rc)], cwd=str(root), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except OSError as error:
        return {'ok': False, 'port': None, 'started': False, 'message': f'Tor konnte nicht gestartet werden ({type(error).__name__}).'}
    _children.append(proc)
    (base / 'tor.pid').write_text(str(proc.pid), encoding='ascii')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return {'ok': False, 'port': None, 'started': False,
                    'message': f'Tor hat sich sofort beendet. Einzelheiten stehen in {log_path}.'}
        try:
            with open(log_path, 'rb') as stream:
                stream.seek(offset)
                if b'Bootstrapped 100%' in stream.read():
                    break
        except OSError:
            pass
        time.sleep(0.5)
    else:
        _kill(proc.pid)
        return {'ok': False, 'port': None, 'started': False,
                'message': f'Tor ist nach {timeout} Sekunden nicht fertig verbunden worden (kein Internet oder Tor gesperrt?). '
                           'Es wurde beendet; später noch einmal versuchen.'}
    result = check(port) if verify else {'ok': True, 'message': f'Tor gestartet (Anschluss {port}).'}
    return {'ok': result['ok'], 'port': port, 'started': True, 'message': 'Tor gestartet. ' + result['message']}


def _kill(pid):
    """Genau diesen Prozess (und seine Kinder) beenden – nie per Programmname."""
    if not _pid_alive_is_tor(pid):
        return False
    subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'], capture_output=True, timeout=20,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for proc in list(_children):
        if proc.pid == pid:
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
            _children.remove(proc)
    return True


def stop(root, data_dir='data/tor'):
    """Das von uns gestartete Tor beenden (PID aus tor.pid). Gibt (ok, Meldung) zurück."""
    pid_file = _data_dir(root, data_dir) / 'tor.pid'
    try:
        pid = int(pid_file.read_text(encoding='ascii').strip())
    except (OSError, ValueError):
        return False, 'Es gibt kein von Socialscraper gestartetes Tor (keine tor.pid). Ein anderes Tor wird nicht beendet.'
    stopped = _kill(pid)
    pid_file.unlink(missing_ok=True)
    return stopped, 'Tor beendet.' if stopped else 'Das gemerkte Tor lief schon nicht mehr.'
