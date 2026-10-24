"""Logik der Einstellungs-Oberfläche ohne Tk: Prüfen und Umwandeln der Eingaben, Proxy-Adressen,
Datensicherung, Portprüfung. Alles hier ist ohne Fenster testbar.

Die Oberfläche arbeitet mit einem „rohen“ Wörterbuch (`raw`) aus Texten, Ja/Nein-Werten und Listen,
so wie die Eingabefelder sie liefern. `validate_form(raw)` macht daraus geprüfte Werte für
settings.toml und data/integration.json – oder Fehlermeldungen je Feld. Geheimnisse (Proxy-Passwörter,
Token, Cloud-Schlüssel, Webhook) kommen in keine Meldung.
"""
import math
import os
import pathlib
import re
import socket
import sqlite3
from urllib.parse import urlsplit

from . import proxylogic

# ---------- Auswahllisten ----------

LOG_LEVELS = [('Nur Fehler', 'ERROR'), ('Warnungen und Fehler', 'WARNING'),
              ('Normal', 'INFO'), ('Ausführlich (zur Fehlersuche)', 'DEBUG')]
ENGINES = [('Chrome / Patchright (empfohlen)', 'patchright'),
           ('Chrome / Nodriver', 'nodriver'), ('Firefox / Playwright (Desktop)', 'firefox')]
SYNC_KINDS = [('Aus', 'none'), ('Supabase', 'supabase')]
DEFAULT_PORT = 8765
MIN_TOKEN_LENGTH = 24

SETTINGS_KEYS = ('max_comments', 'max_retries', 'request_timeout', 'rotation_every', 'length_short_s', 'length_long_s', 'snapshot',
                 'log_level', 'headless', 'engine',
                 'session_max_age', 'require_auth', 'proxy_mode', 'proxies', 'notify_file', 'notify_webhook')
INTEGRATION_KEYS = ('api_enabled', 'api_port', 'api_token', 'api_origins',
                    'sync_kind', 'sync_url', 'sync_key', 'sync_auto')
ALL_KEYS = SETTINGS_KEYS + INTEGRATION_KEYS

SECTION_OF = {
    'max_comments': 'abruf', 'max_retries': 'abruf', 'request_timeout': 'abruf', 'rotation_every': 'abruf', 'snapshot': 'abruf',
    'length_short_s': 'abruf', 'length_long_s': 'abruf',
    'log_level': 'abruf', 'headless': 'abruf', 'engine': 'abruf', 'session_max_age': 'abruf', 'require_auth': 'abruf',
    'proxy_mode': 'proxys', 'proxies': 'proxys', 'notify_file': 'meldungen', 'notify_webhook': 'meldungen',
    'api_enabled': 'pulse', 'api_port': 'pulse', 'api_token': 'pulse', 'api_origins': 'pulse',
    'sync_kind': 'cloud', 'sync_url': 'cloud', 'sync_key': 'cloud', 'sync_auto': 'cloud',
}

DEFAULT_SETTINGS_TEXT = '''backend = "local"
engine = "patchright"
headless = true
pool_size = 4
session_ttl = 1800
proxy_sticky_ttl = 86400
profiles_dir = "data/profiles"
db_path = "data/scrape.db"
log_level = "INFO"
max_comments = 500
max_retries = 3
rotation_every = 5   # Konten-Rotation: nach so vielen Videos zum nächsten Konto wechseln (1-100)
snapshot = true
sphere_api_port = 40080

proxy_mode = "aus"   # aus | liste | frei | tor (frei und tor nur für Abrufe ohne Konto)
proxies = []
# Standard-Proxy: Benutzername wird unverändert verwendet.
# proxies = ["http://user:pw@gw.provider.io:8000?country=us"]
# Falls der Anbieter Sessionvorlagen unterstützt: user-session-{identity}-country-{country}

ms_token = ""
ig_session_user = ""
ig_session_file = "ig_session"
notify_file = "data/alerts.log"
notify_webhook = ""
credentials_file = "data/credentials.json"
require_auth = false
session_max_age = 24.0

[sphere_presets]
tiktok = ""
instagram = ""
'''


# ---------- Einzelne Werte ----------

def parse_int(text, low, high, what='Der Wert'):
    """(Zahl, Fehlertext). Leere Eingabe und Kommazahlen sind Fehler."""
    s = str(text).strip()
    if not re.fullmatch(r'[0-9]{1,9}', s):
        return None, f'{what}: bitte eine ganze Zahl von {low} bis {high} eingeben.'
    n = int(s)
    if not low <= n <= high:
        return None, f'{what}: bitte eine Zahl von {low} bis {high} eingeben.'
    return n, None


def parse_float(text, low, high, what='Der Wert'):
    s = str(text).strip().replace(',', '.')
    if not re.fullmatch(r'[0-9]{1,6}(\.[0-9]{1,3})?', s):
        return None, f'{what}: bitte eine Zahl von {low} bis {high} eingeben (z. B. 24).'
    n = float(s)
    if not math.isfinite(n) or not low <= n <= high:
        return None, f'{what}: bitte eine Zahl von {low} bis {high} eingeben.'
    return n, None


_PROXY = re.compile(
    r'^(?P<scheme>https?|socks4|socks5h?)://(?:(?P<user>[^:@/\s]*)(?::(?P<pw>[^@/\s]*))?@)?'
    r'(?P<host>[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:]+\]):(?P<port>[0-9]{1,5})(?P<rest>[/?#]\S*)?$')
_PROXY_EXAMPLE = 'Beispiel: http://benutzer:passwort@host.beispiel.de:8000'


def parse_proxy(text):
    """(bereinigte Adresse, Fehlertext). Erlaubt: http, https, socks4, socks5, socks5h mit Port."""
    s = (text or '').strip()
    if not s:
        return None, 'Bitte eine Proxy-Adresse eingeben. ' + _PROXY_EXAMPLE
    if re.search(r'\s', s):
        return None, 'Die Adresse darf keine Leerzeichen enthalten.'
    if '://' not in s:
        return None, 'Die Adresse muss mit http://, https://, socks5:// o. ä. beginnen. ' + _PROXY_EXAMPLE
    m = _PROXY.match(s)
    if not m:
        if not re.match(r'^(https?|socks4|socks5h?)://', s):
            return None, 'Unbekannte Art – erlaubt sind http, https, socks4, socks5 und socks5h.'
        if not re.search(r':[0-9]+(?:[/?#]|$)', s.split('@')[-1]):
            return None, 'Es fehlt der Port (z. B. :8000 hinter dem Servernamen).'
        return None, 'Die Adresse hat nicht das richtige Format. ' + _PROXY_EXAMPLE
    if not 1 <= int(m.group('port')) <= 65535:
        return None, 'Der Port muss zwischen 1 und 65535 liegen.'
    return s, None


def mask_proxy(text):
    """Proxy-Adresse für die Anzeige: das Passwort wird durch ***** ersetzt."""
    m = _PROXY.match((text or '').strip())
    if not m or m.group('pw') is None:
        return text
    start = m.start('pw')
    return text[:start] + '*****' + text[m.end('pw'):]


def validate_webhook(text):
    s = (text or '').strip()
    if not s:
        return '', None
    if re.search(r'[\s\x00-\x1f\x7f]', s):
        return None, 'Die Adresse darf keine Leerzeichen enthalten.'
    try:
        parts = urlsplit(s)
        parts.port
    except ValueError:
        return None, 'Die Adresse ist ungültig.'
    local = (parts.hostname or '') in ('localhost', '127.0.0.1', '::1')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and local):
        return None, 'Die Adresse muss mit https:// beginnen.'
    if not parts.hostname or parts.username or parts.password:
        return None, 'Die Adresse ist ungültig (Servername fehlt oder Zugangsdaten in der Adresse).'
    return s, None


def validate_notify_file(text):
    s = (text or '').strip()
    if not s:
        return None, 'Bitte einen Dateinamen angeben (z. B. data/alerts.log).'
    if len(s) > 240:
        return None, 'Der Pfad ist zu lang.'
    if re.search(r'[<>"|?*\x00-\x1f\x7f\x85\u2028\u2029]', s) or ':' in s[2:] or (len(s) > 1 and s[1] == ':' and not s[0].isalpha()):
        return None, 'Der Pfad enthält Zeichen, die in Dateinamen nicht erlaubt sind.'
    if s.endswith(('/', '\\')):
        return None, 'Das ist ein Ordner. Bitte einen Dateinamen angeben (z. B. data/alerts.log).'
    return s, None


_ORIGIN = re.compile(r'^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?$|^https?://\[[0-9A-Fa-f:]+\](:[0-9]{1,5})?$')


def validate_origin(text):
    s = (text or '').strip().rstrip('/')
    if not s:
        return None, 'Bitte eine Adresse eingeben.'
    if s == '*':
        return None, '„*“ ist nicht erlaubt. Trage die genaue Adresse deiner PULSE-Seite ein.'
    if not _ORIGIN.fullmatch(s):
        return None, 'Ungültige Adresse. Beispiel: http://localhost:5173 (ohne Pfad, ohne / am Ende).'
    port = re.search(r':([0-9]{1,5})$', s)
    if port and not 1 <= int(port.group(1)) <= 65535:
        return None, 'Der Port in der Adresse ist ungültig.'
    return s, None


def validate_cloud_url(text):
    """Prüft und bereinigt die Adresse der Cloud-Datenbank. Gibt (Adresse, Fehlertext) zurück.

    Erlaubt ist nur https (http nur für localhost). Leerzeichen am Rand, ein Schrägstrich am Ende, der Pfad
    /rest/v1 (samt allem dahinter), Anfrageteile (?…) und Ankerteile (#…) werden entfernt – das Programm
    hängt /rest/v1 selbst an. So wird aus „https://abc.supabase.co/rest/v1/“ die Adresse „https://abc.supabase.co“."""
    s = (text or '').strip()
    if not s:
        return None, 'Bitte die Adresse (URL) der Cloud-Datenbank eintragen.'
    if re.search(r'\s', s):
        return None, 'Die Adresse darf keine Leerzeichen oder Zeilenumbrüche enthalten.'
    try:
        parts = urlsplit(s)
        parts.port
    except ValueError:
        return None, 'Die Adresse ist ungültig.'
    local = (parts.hostname or '') in ('localhost', '127.0.0.1', '::1')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and local):
        if parts.scheme == 'http':
            return None, 'Die Adresse muss mit https:// beginnen (http ist nur für localhost erlaubt).'
        return None, 'Die Adresse muss mit https:// beginnen, zum Beispiel https://abcdefgh.supabase.co'
    host = parts.hostname or ''
    if not host or parts.username or parts.password:
        return None, 'Die Adresse ist ungültig (Servername fehlt oder Zugangsdaten in der Adresse). Beispiel: https://abcdefgh.supabase.co'
    if not local and not re.fullmatch(r'[0-9A-Za-z.\-]+', host) and ':' not in host:
        return None, 'Der Servername in der Adresse enthält ungültige Zeichen.'
    if not local and '.' not in host and ':' not in host:
        return None, 'Der Servername sieht unvollständig aus. Beispiel: https://abcdefgh.supabase.co'
    path = parts.path
    cut = path.lower().find('/rest/v1')
    if cut >= 0 and (path[cut + 8:cut + 9] in ('', '/')):
        path = path[:cut]
    path = path.rstrip('/')
    netloc = parts.netloc.lower()
    return f'{parts.scheme}://{netloc}{path}', None


def validate_token(text, required):
    s = (text or '').strip()
    if not s:
        return ('', 'Es fehlt ein Token. Klicke auf „Neu erzeugen“.') if required else ('', None)
    if re.search(r'\s', s):
        return None, 'Der Token darf keine Leerzeichen enthalten. Klicke auf „Neu erzeugen“.'
    if len(s) < MIN_TOKEN_LENGTH:
        return None, f'Der Token ist zu kurz (mindestens {MIN_TOKEN_LENGTH} Zeichen). Klicke auf „Neu erzeugen“.'
    return s, None


def dedupe(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------- Das ganze Formular ----------

def validate_form(raw):
    """Prüft alle Eingaben. Gibt (settings, integration, errors) zurück.

    settings     - fertige Werte für guicore.write_settings
    integration  - {'api': {...}, 'sync': {...}} zum Einsetzen in die geladene integration.json
    errors       - {Feldname: Meldung}; ist es nicht leer, darf nichts geschrieben werden
    """
    errors, s, api, sync = {}, {}, {}, {}

    def put(target, key, result, name=None):
        value, error = result
        if error:
            errors[name or key] = error
        else:
            target[key] = value

    put(s, 'max_comments', parse_int(raw['max_comments'], 0, 5000, 'Kommentare pro Video'))
    put(s, 'max_retries', parse_int(raw['max_retries'], 1, 10, 'Wiederholungen'))
    put(s, 'request_timeout', parse_int(raw['request_timeout'], 5, 300, 'Zeitlimit'))
    put(s, 'rotation_every', parse_int(raw.get('rotation_every', '5'), 1, 100, 'Konto wechseln nach'))
    put(s, 'length_short_s', parse_int(raw.get('length_short_s', '60'), 1, 86400, 'Kurze Videos bis'))
    put(s, 'length_long_s', parse_int(raw.get('length_long_s', '600'), 1, 86400, 'Lange Videos ab'))
    if s.get('length_short_s') is not None and s.get('length_long_s') is not None and s['length_short_s'] >= s['length_long_s']:
        errors['length_short_s'] = 'Die Grenze für kurze Videos muss kleiner sein als die für lange.'
    put(s, 'session_max_age', parse_float(raw['session_max_age'], 1, 720, 'Gültigkeit der Anmeldung'))
    s['snapshot'] = bool(raw['snapshot'])
    s['headless'] = bool(raw['headless'])
    s['require_auth'] = bool(raw['require_auth'])
    if raw['log_level'] in [v for _l, v in LOG_LEVELS]:
        s['log_level'] = raw['log_level']
    else:
        errors['log_level'] = 'Bitte eine Stufe aus der Liste wählen.'
    if raw['engine'] in [v for _l, v in ENGINES]:
        s['engine'] = raw['engine']
    else:
        errors['engine'] = 'Bitte eine Engine aus der Liste wählen.'

    mode, error = proxylogic.validate_mode(raw.get('proxy_mode', 'aus'))
    if error:
        errors['proxy_mode'] = error
    else:
        s['proxy_mode'] = mode
    proxies = []
    for p in raw['proxies']:
        value, error = parse_proxy(p)
        if error:
            errors['proxies'] = 'Eine Proxy-Adresse ist ungültig: ' + error
            break
        proxies.append(value)
    s['proxies'] = dedupe(proxies)

    put(s, 'notify_file', validate_notify_file(raw['notify_file']))
    put(s, 'notify_webhook', validate_webhook(raw['notify_webhook']))

    enabled = bool(raw['api_enabled'])
    api['enabled'] = enabled
    port, error = parse_int(raw['api_port'], 1024, 65535, 'Port')
    if error:
        errors['api_port'] = error + ' (Ports unter 1024 sind für das System reserviert.)'
    else:
        api['port'] = port
    put(api, 'token', validate_token(raw['api_token'], enabled), 'api_token')
    origins = []
    for o in raw['api_origins']:
        value, error = validate_origin(o)
        if error:
            errors['api_origins'] = f'Herkunftsadresse „{str(o)[:60]}“: {error}'
            break
        origins.append(value)
    api['cors_origins'] = dedupe(origins)

    kind = raw['sync_kind']
    if kind not in [v for _l, v in SYNC_KINDS]:
        errors['sync_kind'] = 'Bitte eine Art aus der Liste wählen.'
    sync['kind'] = kind
    sync['auto'] = bool(raw['sync_auto'])
    url, key = str(raw['sync_url']).strip(), str(raw['sync_key']).strip()
    if kind == 'supabase':
        put(sync, 'url', validate_cloud_url(url), 'sync_url')
        if not key:
            errors['sync_key'] = 'Bitte den Schlüssel der Cloud-Datenbank eintragen.'
        elif re.search(r'\s', key):
            errors['sync_key'] = 'Der Schlüssel enthält Leerzeichen oder Zeilenumbrüche – bitte erneut einfügen.'
        else:
            sync['key'] = key
    else:
        sync['url'] = url.rstrip('/')
        sync['key'] = key
    return s, {'api': api, 'sync': sync}, errors


# ---------- Laden: Einstellungen -> Formular ----------

def _num_text(value):
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def settings_to_raw(cfg):
    return {
        'max_comments': str(cfg.max_comments), 'max_retries': str(cfg.max_retries),
        'request_timeout': str(cfg.request_timeout), 'rotation_every': str(cfg.rotation_every), 'snapshot': bool(cfg.snapshot),
        'length_short_s': str(cfg.length_short_s), 'length_long_s': str(cfg.length_long_s),
        'log_level': str(cfg.log_level).upper(), 'headless': bool(cfg.headless), 'engine': cfg.engine,
        'session_max_age': _num_text(cfg.session_max_age), 'require_auth': bool(cfg.require_auth),
        'proxy_mode': cfg.proxy_mode, 'proxies': list(cfg.proxies), 'notify_file': cfg.notify_file, 'notify_webhook': cfg.notify_webhook,
    }


def integration_to_raw(cfg):
    api, sync = cfg['api'], cfg['sync']
    return {
        'api_enabled': bool(api['enabled']), 'api_port': str(api['port']), 'api_token': api['token'] or '',
        'api_origins': list(api['cors_origins']),
        'sync_kind': sync['kind'], 'sync_url': sync['url'] or '', 'sync_key': sync['key'] or '',
        'sync_auto': bool(sync['auto']),
    }


def default_raw():
    from .config import Settings
    raw = settings_to_raw(Settings())
    raw.update({'api_enabled': False, 'api_port': str(DEFAULT_PORT), 'api_token': '', 'api_origins': [],
                'sync_kind': 'none', 'sync_url': '', 'sync_key': '', 'sync_auto': True})
    return raw


def changed_keys(a, b):
    return [k for k in ALL_KEYS if a.get(k) != b.get(k)]


def changed_sections(a, b):
    return {SECTION_OF[k] for k in changed_keys(a, b)}


def describe_changes(keys):
    """Kurzer Text fürs Protokoll – nennt nur Bereiche, nie Werte."""
    names = {'abruf': 'Abruf', 'proxys': 'Proxys', 'meldungen': 'Meldungen', 'pulse': 'PULSE-Anbindung', 'cloud': 'Cloud'}
    out = []
    for k in keys:
        n = names[SECTION_OF[k]]
        if n not in out:
            out.append(n)
    return ', '.join(out)


# ---------- Hilfen mit Betriebssystem ----------

def port_in_use(port, host='127.0.0.1'):
    """True, wenn auf diesem Rechner schon jemand den Port belegt."""
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if os.name == 'nt':
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_EXCLUSIVEADDRUSE', socket.SO_REUSEADDR), 1)
        sock.bind((host, int(port)))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def example_url(port, host='127.0.0.1'):
    host = f'[{host}]' if ':' in host else host
    return f'http://{host}:{int(port)}/v1/stats'


def health_url(port, host='127.0.0.1'):
    host = f'[{host}]' if ':' in host else host
    return f'http://{host}:{int(port)}/v1/health'


def check_api(port, token, host='127.0.0.1', timeout=5):
    """Ruft /v1/health der lokalen Schnittstelle auf. Gibt (ok, Meldung) zurück; nie eine Ausnahme."""
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(health_url(port, host), headers={'Authorization': f'Bearer {token}'} if token else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # nie über einen Proxy
    try:
        with opener.open(req, timeout=timeout) as response:
            data = json.loads(response.read(65536).decode('utf-8'))
    except urllib.error.HTTPError as error:
        return False, f'Die Schnittstelle antwortet mit Fehler {error.code}.'
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        reason = getattr(error, 'reason', error)
        if isinstance(reason, (ConnectionRefusedError, socket.timeout, TimeoutError)) or getattr(reason, 'winerror', 0) in (10061, 10060):
            return False, ('Niemand antwortet auf diesem Port. Die Schnittstelle läuft zusammen mit dem '
                           'Hintergrunddienst – ist er gestartet, und sind die Einstellungen gespeichert?')
        return False, 'Die Schnittstelle ist nicht erreichbar.'
    except ValueError:
        return False, 'Auf diesem Port antwortet ein anderes Programm (keine Socialscraper-Schnittstelle).'
    if not isinstance(data, dict) or data.get('service') != 'socialscraper':
        return False, 'Auf diesem Port antwortet ein anderes Programm (keine Socialscraper-Schnittstelle).'
    if token and not data.get('authenticated'):
        return False, 'Die Schnittstelle läuft, lehnt aber den Token ab. Speichere die Einstellungen und starte den Dienst neu.'
    return True, 'Die Schnittstelle läuft' + (' und der Token passt.' if token else '.')


def backup_database(src, dest):
    """Sichert die Datenbank mit der sqlite3-Sicherungsfunktion (auch während sie benutzt wird).

    Gibt {'bytes', 'videos', 'comments'} zurück; wirft ValueError mit deutschem Text."""
    src, dest = pathlib.Path(src), pathlib.Path(dest)
    if not src.is_file():
        raise ValueError('Es gibt noch keine Datenbank – rufe zuerst ein Video ab.')
    if dest.exists() and dest.resolve() == src.resolve():
        raise ValueError('Bitte eine andere Datei als die Datenbank selbst wählen.')
    if not dest.parent.is_dir():
        raise ValueError('Der Zielordner existiert nicht.')
    tmp = dest.with_name(dest.name + '.tmp')
    source = target = None
    try:
        from .util import ro_connect
        source = ro_connect(src, timeout=15)
        target = sqlite3.connect(str(tmp))
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Die Sicherungskopie ist nicht in Ordnung und wurde verworfen.')

        def count(table):
            try:
                return target.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            except sqlite3.Error:
                return 0
        result = {'videos': count('videos'), 'comments': count('comments')}
        target.close(); target = None
        source.close(); source = None
        os.replace(tmp, dest)
    except sqlite3.OperationalError as error:
        text = str(error).lower()
        if 'locked' in text or 'busy' in text:
            raise ValueError('Die Datenbank ist gerade in Gebrauch. Bitte gleich noch einmal versuchen.') from None
        raise ValueError('Die Sicherung ist fehlgeschlagen (Datenbank nicht lesbar).') from None
    except sqlite3.DatabaseError:
        raise ValueError('Die Datenbank ist beschädigt und lässt sich nicht sichern.') from None
    except PermissionError:
        raise ValueError('Die Zieldatei lässt sich nicht schreiben (geöffnet oder kein Zugriff).') from None
    except OSError as error:
        raise ValueError(f'Die Sicherung konnte nicht geschrieben werden ({error.strerror or "Fehler"}).') from None
    finally:
        for c in (source, target):
            if c is not None:
                try: c.close()
                except sqlite3.Error: pass
        try: tmp.unlink()
        except OSError: pass
    result['bytes'] = dest.stat().st_size
    return result


def human_size(n):
    for unit in ('Byte', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n} Byte' if unit == 'Byte' else f'{n:.1f} {unit}'.replace('.', ',')
        n /= 1024
