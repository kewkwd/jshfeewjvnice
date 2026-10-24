"""Fester Proxy je Konto.

Ein Konto kann einen festen Proxy bekommen. Dann läuft alles dieses Kontos (Anmelden, Prüfen, Abrufen,
Hintergrunddienst) NUR über diesen Proxy – nie direkt, nie über einen anderen.

Wo das Passwort steht (Entscheidung):
    Die Zugangsdaten des Proxys liegen NICHT in der Datenbank, sondern in `data/account_proxies.json` neben der
    Datenbank (Datei nur für den Benutzer lesbar, wie `integration.json`). Die Datenbank trägt in `accounts.proxy`
    nur eine MASKIERTE Anzeige (`http://benutzer:*****@1.2.3.4:8080`). Grund: Datenbank, Datensicherung, Export,
    Schnittstelle und Cloud-Abgleich lesen die Datenbank – eine Datei mit Geheimnis daneben wird von keinem davon
    angefasst. Fehlt die Datei oder der Eintrag, obwohl die Datenbank einen Proxy vermerkt, wird NICHT direkt
    abgerufen, sondern mit klarer Meldung abgebrochen.

Passwörter erscheinen nirgends im Klartext: `mask()` für die Anzeige, `scrub()` für Meldungen und Protokolle.
"""
import json
import logging
import os
import pathlib
import re
import socket
import sqlite3
import threading
import time
import urllib.parse

from .errors import NetworkError
from .proxy import Proxy

log = logging.getLogger(__name__)

FILE_NAME = 'account_proxies.json'
MASK = '*****'
SCHEMES = ('http', 'https', 'socks5', 'socks4')
_LOCK = threading.RLock()
_SECRETS = set()          # Passwörter, die dieser Prozess geladen hat (für scrub)
_REACH = {}               # (host, port) -> Zeitpunkt der letzten erfolgreichen Erreichbarkeitsprüfung
REACH_TTL = 20.0
_URLPW = re.compile(r'(://[^/\s:@]+:)[^/\s@]+(@)')


class AccountProxyError(NetworkError):
    """Der feste Proxy eines Kontos fehlt oder ist nicht erreichbar. Wiederholen ist sinnlos; es wird nie ersatzweise
    direkt oder über einen anderen Proxy abgerufen."""
    kind = 'proxy'

    def __init__(self, message):
        super().__init__(message, retryable=False)


# ---------------------------------------------------------------- Eingabe prüfen und anzeigen

def normalize(raw):
    """Prüft einen Proxy und gibt die Adresse mit Protokoll zurück; ValueError mit deutschem Text sonst."""
    text = str(raw or '').strip()
    if not text:
        raise ValueError('Bitte eine Proxy-Adresse eingeben, z. B. http://benutzer:passwort@1.2.3.4:8080')
    if re.search(r'\s|[\x00-\x1f\x7f]', text):
        raise ValueError('Die Adresse darf keine Leerzeichen oder Zeilenumbrüche enthalten.')
    if '://' not in text:
        text = 'http://' + text
    scheme = text.split('://', 1)[0].lower()
    if scheme not in SCHEMES:
        raise ValueError('Unbekannte Art – erlaubt sind http, https, socks5 und socks4.')
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
        proxy = Proxy.parse(text)
    except ValueError:
        raise ValueError('Die Adresse hat nicht das richtige Format. '
                         'Beispiel: http://benutzer:passwort@1.2.3.4:8080') from None
    if port is None:
        raise ValueError('Es fehlt der Port (z. B. :8080 hinter dem Servernamen).')
    if proxy.scheme == 'socks4' and proxy.user is not None:
        raise ValueError('Ein SOCKS4-Proxy mit Anmeldung wird nicht unterstützt. Nimm HTTP oder SOCKS5.')
    return text


def mask(raw):
    """Anzeige ohne Passwort: `http://benutzer:*****@1.2.3.4:8080`. Auch für Proxy-Objekte."""
    if isinstance(raw, Proxy):
        host = f'[{raw.host}]' if ':' in raw.host else raw.host
        auth = ''
        if raw.user is not None:
            auth = urllib.parse.quote(raw.user, safe='') + (':' + MASK if raw.password else '') + '@'
        return f'{raw.scheme}://{auth}{host}:{raw.port}'
    text = str(raw or '').strip()
    if not text:
        return ''
    try:
        return mask(Proxy.parse(text))
    except ValueError:
        return 'ungültige Proxy-Adresse'


def endpoint(raw):
    """(Host, Port) einer (auch maskierten) Proxy-Anzeige; None, wenn nicht lesbar."""
    try:
        parts = urllib.parse.urlsplit(str(raw or '').strip() if '://' in str(raw) else 'http://' + str(raw or ''))
        if parts.hostname and parts.port:
            return parts.hostname.lower(), parts.port
    except ValueError:
        pass
    return None


def _remember(proxy):
    if proxy.password and len(proxy.password) >= 4:
        _SECRETS.add(proxy.password)
        _SECRETS.add(urllib.parse.quote(proxy.password, safe=''))


def scrub(text):
    """Entfernt bekannte Proxy-Passwörter und `://benutzer:passwort@` aus beliebigem Text."""
    if not text:
        return text
    text = str(text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, MASK)
    return _URLPW.sub(r'\1' + MASK + r'\2', text)


class _ScrubFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = scrub(message)
        if clean != message:
            record.msg, record.args = clean, ()
        return True


def install_log_filter():
    """Schaltet an jeden Ausgabekanal des Protokolls einen Filter, der Proxy-Passwörter unkenntlich macht."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, _ScrubFilter) for f in handler.filters):
            handler.addFilter(_ScrubFilter())


# ---------------------------------------------------------------- Datei mit den Zugangsdaten

def db_file(db):
    """Dateipfad einer offenen SQLite-Verbindung (None bei :memory:)."""
    try:
        for _seq, name, path in db.execute('PRAGMA database_list'):
            if name == 'main' and path:
                return pathlib.Path(path)
    except sqlite3.Error:
        pass
    return None


def path_for(db_path):
    return pathlib.Path(db_path).parent / FILE_NAME


def load(db_path):
    """{Kontoschlüssel: Proxy-Adresse}; fehlende oder defekte Datei = leer. Merkt sich die Passwörter für scrub()."""
    path = path_for(db_path)
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    items = data.get('proxies') if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    out = {}
    for key, value in (items or {}).items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            out[key] = value.strip()
            try:
                _remember(Proxy.parse(value))
            except ValueError:
                pass
    return out


def _write(db_path, items):
    path = path_for(db_path)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f'{FILE_NAME}.{os.getpid()}.{threading.get_ident()}.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(json.dumps({'schema_version': 1, 'proxies': items}, ensure_ascii=False, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            for attempt in range(6):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


def set_entry(db_path, identity, raw):
    """Speichert den Proxy eines Kontos (nach Prüfung). Gibt die maskierte Anzeige zurück."""
    text = normalize(raw)
    with _LOCK:
        items = load(db_path)
        items[identity] = text
        try:
            _write(db_path, items)
        except OSError as error:
            raise ValueError(f'Der Proxy konnte nicht gespeichert werden ({error.strerror or "Fehler"}).') from None
    _remember(Proxy.parse(text))
    return mask(text)


def remove_entry(db_path, identity):
    with _LOCK:
        items = load(db_path)
        if identity in items:
            items.pop(identity)
            try:
                _write(db_path, items)
            except OSError as error:
                raise ValueError(f'Der Proxy konnte nicht entfernt werden ({error.strerror or "Fehler"}).') from None


# ---------------------------------------------------------------- Erreichbarkeit und Benutzung

def check_reachable(proxy, timeout=4.0):
    """Schneller Verbindungsversuch zum Proxy (nur TCP). Wirft AccountProxyError. Kurz zwischengespeichert."""
    key = (proxy.host, proxy.port)
    stamp = _REACH.get(key)
    if stamp is not None and time.monotonic() - stamp < REACH_TTL:
        return
    try:
        socket.create_connection(key, timeout=timeout).close()
    except OSError:
        _REACH.pop(key, None)
        raise AccountProxyError(
            f'Der feste Proxy dieses Kontos ({proxy.host}:{proxy.port}) ist nicht erreichbar. Der Abruf wurde NICHT '
            'ohne Proxy und NICHT über eine andere Verbindung ausgeführt. Bitte den Proxy prüfen oder unter '
            '„Konten“ ändern.') from None
    _REACH[key] = time.monotonic()


class Resolver:
    """Liefert den festen Proxy eines Kontos (nach Kontoschlüssel) für den IdentityManager."""

    def __init__(self, db_path, reach_check=True):
        self.db_path = str(db_path)
        self.reach_check = reach_check

    def _flagged(self, identity):
        """Vermerkt die Datenbank einen festen Proxy für dieses Konto? (nur lesen)"""
        path = pathlib.Path(self.db_path)
        if not path.is_file():
            return False
        try:
            con = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=5)
            try:
                row = con.execute('SELECT proxy FROM accounts WHERE identity=?', (identity,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            raise AccountProxyError('Die Proxy-Vorgabe des Kontos kann gerade nicht gelesen werden. '
                                    'Der Abruf wurde ohne Ersatzverbindung abgebrochen.') from None
        return bool(row and row[0])

    def proxy_for(self, identity):
        """Proxy-Objekt oder None (Konto hat keinen festen Proxy). Fehlt das Geheimnis, obwohl einer vermerkt ist:
        AccountProxyError (nie stillschweigend direkt)."""
        raw = load(self.db_path).get(identity)
        if raw is None:
            if self._flagged(identity):
                raise AccountProxyError('Für dieses Konto ist ein fester Proxy vermerkt, aber seine Zugangsdaten fehlen '
                                        '(Datei account_proxies.json). Der Abruf wurde NICHT ohne Proxy ausgeführt. '
                                        'Bitte den Proxy unter „Konten“ neu festlegen.')
            return None
        try:
            proxy = Proxy.parse(raw)
        except ValueError:
            raise AccountProxyError('Der feste Proxy dieses Kontos ist ungültig gespeichert. Der Abruf wurde NICHT '
                                    'ohne Proxy ausgeführt. Bitte den Proxy unter „Konten“ neu festlegen.') from None
        from types import SimpleNamespace
        from .sticky_sessions import current
        proxy = current(SimpleNamespace(db_path=self.db_path), identity, proxy)
        _remember(proxy)
        if self.reach_check:
            check_reachable(proxy)
        return proxy
