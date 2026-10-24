"""Konfiguration der Anbindung an PULSE: lokale Schnittstelle (api) und gehosteter Sync (sync).

Die Datei `data/integration.json` enthält Geheimnisse (Token, Cloud-Schlüssel). Sie wird nur hier
gelesen und geschrieben, atomar und mit eingeschränkten Rechten. Geheimnisse gelangen weder in
Fehlermeldungen noch ins Protokoll; für Anzeige und Protokoll gibt es `redacted(cfg)`.

Alle Funktionen haben einen Parameter `root` (Programmordner, in dem `data/` liegt; Standard: aktueller Ordner),
damit Tests mit einem Wegwerf-Ordner arbeiten können.
"""
import copy
import ipaddress
import json
import logging
import os
import pathlib
import re
import secrets
import threading
import time
import tomllib
from urllib.parse import urlsplit

log = logging.getLogger('integration')

SCHEMA_VERSION = 1
FILE_NAME = 'integration.json'
DEFAULT_PORT = 8765
MIN_TOKEN_LENGTH = 24
SYNC_KINDS = ('none', 'supabase')
MASK = '********'
READ_RETRIES = 10         # Lesen der Datei: so oft versuchen ...
READ_WAIT = 0.02          # ... mit so vielen Sekunden dazwischen

DEFAULTS = {
    'api': {'enabled': False, 'host': '127.0.0.1', 'port': DEFAULT_PORT, 'token': '', 'cors_origins': []},
    'sync': {'kind': 'none', 'url': '', 'key': '', 'auto': True, 'last_push': None, 'last_pull': None,
             'last_error': None},
}
_SECRET_KEYS = {('api', 'token'), ('sync', 'key')}
_LOCK = threading.RLock()


class IntegrationError(ValueError):
    """Verständliche deutsche Meldung für Anwender."""


def resolve_root(root=None):
    return pathlib.Path(root) if root is not None else pathlib.Path.cwd()


def path(root=None):
    return resolve_root(root) / 'data' / FILE_NAME


def db_path(root=None):
    """Ort der Datenbank: Umgebungsvariable SS_DB_PATH, sonst db_path aus settings.toml, sonst data/scrape.db."""
    base = resolve_root(root)
    value = os.getenv('SS_DB_PATH')
    if not value:
        try:
            settings = base / 'settings.toml'
            if settings.exists():
                value = tomllib.loads(settings.read_text(encoding='utf-8-sig')).get('db_path')
        except (OSError, ValueError):
            value = None
    p = pathlib.Path(value or 'data/scrape.db')
    return p if p.is_absolute() else base / p


# ---------- Laden / Prüfen ----------

def default_config():
    return copy.deepcopy(DEFAULTS)


def _merge(raw):
    """Bekannte Felder aus `raw` über die Standardwerte legen; Unbekanntes wird ignoriert."""
    cfg = default_config()
    if not isinstance(raw, dict):
        return cfg
    for section in ('api', 'sync'):
        part = raw.get(section)
        if isinstance(part, dict):
            for key in cfg[section]:
                if key in part:
                    cfg[section][key] = copy.deepcopy(part[key])
    return cfg


def load_with_status(root=None):
    """Gibt (Konfiguration, Problem) zurück. Problem ist None oder eine deutsche Meldung.

    Bei fehlender Datei: Standardwerte ohne Problem. Bei kaputter Datei: Standardwerte (Sync und
    Schnittstelle bleiben aus) und eine Meldung – nie eine Ausnahme.
    """
    p = path(root)
    # Unter Windows scheitert das Lesen kurz (PermissionError), während eine andere Stelle die Datei ersetzt:
    # darum ein paar kurze Wiederholungen, bevor es als Problem gilt.
    for attempt in range(READ_RETRIES):
        try:
            text = p.read_text(encoding='utf-8-sig')
            break
        except FileNotFoundError:
            return default_config(), None
        except UnicodeDecodeError:   # z. B. als UTF-16 gespeichert: beschädigt, kein Absturz
            return default_config(), (f'Die Datei {FILE_NAME} ist beschädigt und wird ignoriert. '
                                      'Beim nächsten Speichern wird sie ersetzt (eine Kopie bleibt als .defekt erhalten).')
        except OSError as error:
            if attempt == READ_RETRIES - 1:
                return default_config(), f'Die Datei {FILE_NAME} lässt sich nicht lesen ({error.strerror or "Fehler"}).'
            time.sleep(READ_WAIT)
    try:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError('kein Objekt')
    except ValueError:
        return default_config(), (f'Die Datei {FILE_NAME} ist beschädigt und wird ignoriert. '
                                  'Beim nächsten Speichern wird sie ersetzt (eine Kopie bleibt als .defekt erhalten).')
    return _merge(raw), None


def load(root=None):
    return load_with_status(root)[0]


def load_for_update(root=None):
    """Wie load, aber für Vorgänge, die danach speichern: lässt sich die Datei nicht lesen, gibt es eine Ausnahme
    (IntegrationError) statt der Standardwerte – sonst würden Token und Schlüssel mit Standardwerten überschrieben.
    Eine beschädigte Datei liefert weiter Standardwerte (sie wird beim Speichern als .defekt gesichert)."""
    cfg, problem = load_with_status(root)
    if problem and 'beschädigt' not in problem:
        raise IntegrationError(problem + ' Es wurde nichts verändert; bitte gleich noch einmal versuchen.')
    return cfg


_ORIGIN = re.compile(r'^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$|^https?://\[[0-9A-Fa-f:]+\](:\d{1,5})?$')
_HOST = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$')


def is_loopback(host):
    host = (host or '').strip().strip('[]').lower()
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_origin(origin):
    return origin.strip().rstrip('/')


def validate(cfg):
    """Liste verständlicher Fehlermeldungen (leer = in Ordnung)."""
    problems = []
    api, sync = cfg.get('api'), cfg.get('sync')
    if not isinstance(api, dict) or not isinstance(sync, dict):
        return ['Die Konfiguration ist unvollständig.']
    if type(api.get('enabled')) is not bool:
        problems.append('„Schnittstelle aktiv“ muss ein Ja/Nein-Wert sein.')
    host = api.get('host')
    if not isinstance(host, str) or not host.strip():
        problems.append('Bitte eine Adresse für die Schnittstelle angeben (empfohlen: 127.0.0.1).')
    else:
        h = host.strip().strip('[]')
        try:
            ipaddress.ip_address(h)
        except ValueError:
            if not _HOST.fullmatch(h):
                problems.append('Die Adresse der Schnittstelle ist ungültig.')
    port = api.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        problems.append('Der Port muss eine Zahl von 1 bis 65535 sein.')
    token = api.get('token')
    if not isinstance(token, str):
        problems.append('Der Token muss Text sein.')
    elif token and len(token) < MIN_TOKEN_LENGTH:
        problems.append(f'Der Token ist zu kurz (mindestens {MIN_TOKEN_LENGTH} Zeichen). Lass einen neuen erzeugen.')
    elif api.get('enabled') is True and not token:
        problems.append('Zum Aktivieren der Schnittstelle fehlt ein Token. Lass einen erzeugen.')
    origins = api.get('cors_origins')
    if not isinstance(origins, list) or any(not isinstance(o, str) for o in origins):
        problems.append('Die erlaubten Herkunftsadressen müssen eine Liste sein.')
    else:
        for origin in origins:
            if origin.strip() == '*':
                problems.append('„*“ ist als Herkunftsadresse nicht erlaubt. Trage die Adresse deiner PULSE-Seite ein.')
            elif not _ORIGIN.fullmatch(normalize_origin(origin)):
                problems.append(f'Herkunftsadresse „{origin[:80]}“ ist ungültig (Beispiel: http://localhost:5173).')
    if sync.get('kind') not in SYNC_KINDS:
        problems.append('Unbekannte Art der Cloud-Datenbank.')
    if type(sync.get('auto')) is not bool:
        problems.append('„Automatisch senden“ muss ein Ja/Nein-Wert sein.')
    url, key = sync.get('url'), sync.get('key')
    if not isinstance(url, str) or not isinstance(key, str):
        problems.append('Adresse und Schlüssel der Cloud-Datenbank müssen Text sein.')
    elif sync.get('kind') == 'supabase':
        if not url.strip():
            problems.append('Bitte die Adresse (URL) der Cloud-Datenbank eintragen.')
        else:
            try:
                parts = urlsplit(url.strip())
                ok = parts.scheme == 'https' or (parts.scheme == 'http' and is_loopback(parts.hostname))
                if not ok or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                    raise ValueError
                parts.port
            except ValueError:
                problems.append('Die Adresse der Cloud-Datenbank muss mit https:// beginnen (ohne Zugangsdaten, ohne Zusätze).')
        if not key.strip():
            problems.append('Bitte den Schlüssel der Cloud-Datenbank eintragen.')
        elif re.search(r'\s', key.strip()):
            problems.append('Der Schlüssel enthält Leerzeichen oder Zeilenumbrüche – bitte erneut einfügen.')
    return problems


def _clean(cfg):
    """Schreibfertige Kopie: Texte getrimmt, Herkunftsadressen normalisiert und ohne Doppelte."""
    cfg = _merge(cfg)
    api, sync = cfg['api'], cfg['sync']
    if isinstance(api['host'], str):
        api['host'] = api['host'].strip()
    if isinstance(api['token'], str):
        api['token'] = api['token'].strip()
    if isinstance(api['cors_origins'], list):
        seen = []
        for origin in api['cors_origins']:
            if isinstance(origin, str) and normalize_origin(origin) not in seen:
                seen.append(normalize_origin(origin))
        api['cors_origins'] = seen
    for key in ('url', 'key'):
        if isinstance(sync[key], str):
            sync[key] = sync[key].strip()
    if isinstance(sync['url'], str):
        sync['url'] = sync['url'].rstrip('/')
    return cfg


def save(cfg, root=None):
    """Prüft und speichert. Wirft IntegrationError mit allen Problemen; schreibt dann nichts."""
    cfg = _clean(cfg)
    problems = validate(cfg)
    if problems:
        raise IntegrationError(' '.join(problems))
    _write(cfg, root)
    return cfg


def _write(cfg, root=None):
    p = path(root)
    with _LOCK:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                _, problem = load_with_status(root)
                if problem and 'beschädigt' in problem:
                    try:
                        os.replace(p, p.with_name(FILE_NAME + '.defekt'))
                    except OSError:
                        pass
            tmp = p.with_name(f'{FILE_NAME}.{os.getpid()}.{threading.get_ident()}.tmp')
            data = json.dumps({'schema_version': SCHEMA_VERSION, **cfg}, ensure_ascii=False, indent=2)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                _replace(tmp, p)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        except OSError as error:
            raise IntegrationError(f'Die Einstellungen konnten nicht gespeichert werden ({error.strerror or "Fehler"}).') from None


def _replace(src, dst):
    # Unter Windows scheitert os.replace kurz, wenn eine andere Stelle die Datei gerade liest.
    for attempt in range(6):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


# ---------- Token ----------

def new_token():
    return secrets.token_urlsafe(32)


def ensure_token(root=None):
    """Gibt den vorhandenen Token zurück oder erzeugt und speichert einen neuen."""
    with _LOCK:
        cfg = load_for_update(root)
        token = cfg['api']['token']
        if isinstance(token, str) and len(token) >= MIN_TOKEN_LENGTH:
            return token
        cfg['api']['token'] = new_token()
        _write(_clean(cfg), root)
        return cfg['api']['token']


def regenerate_token(root=None):
    """Ersetzt den Token (alte Anmeldungen von PULSE verlieren damit ihre Gültigkeit)."""
    with _LOCK:
        cfg = load_for_update(root)
        cfg['api']['token'] = new_token()
        _write(_clean(cfg), root)
        return cfg['api']['token']


# ---------- Anzeige ----------

def redacted(cfg):
    """Kopie ohne Geheimnisse – für Anzeige und Protokoll."""
    out = copy.deepcopy(cfg)
    for section, key in _SECRET_KEYS:
        if isinstance(out.get(section), dict) and out[section].get(key):
            out[section][key] = MASK
    return out


def scrub(text, cfg):
    """Entfernt bekannte Geheimnisse aus einem Text (für Fehlermeldungen)."""
    text = str(text)
    for section, key in _SECRET_KEYS:
        secret = (cfg.get(section) or {}).get(key)
        if isinstance(secret, str) and len(secret) >= 6:
            text = text.replace(secret, MASK)
    return text


# ---------- Statusfelder des Sync (ändern nur diese, nie andere Einstellungen) ----------

def update_sync_status(root=None, **fields):
    """Setzt last_push / last_pull / last_error unter Sperre auf dem aktuellen Dateistand."""
    allowed = {'last_push', 'last_pull', 'last_error'}
    if set(fields) - allowed:
        raise ValueError('Unbekanntes Statusfeld')
    with _LOCK:
        cfg, problem = load_with_status(root)
        if problem:
            return False
        cfg['sync'].update(fields)
        try:
            _write(_clean(cfg), root)
        except IntegrationError as error:
            log.warning('%s', error)
            return False
        return True
