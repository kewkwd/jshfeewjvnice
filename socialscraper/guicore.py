"""Logik der Desktop-Oberfläche ohne Tk: Befehle, Links, Status, Einstellungen, Datenbank, Prozesslauf.

Die Oberfläche startet das Werkzeug immer als eigenen Prozess (python -m socialscraper ...).
So können Browserfehler oder Abstürze das Fenster nie mitreißen, und jeder Lauf lässt sich abbrechen.
"""
import contextlib
import csv
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from . import people_repo, platformlinks, sentiment_repo
from .util import format_duration, matches_host
from .settings_text import (EDITABLE, _NONEMPTY, _KEY_LINE, _toml_string, _toml_value,
                            _check_setting, _value_end, _first_table_line, render_settings)

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLATFORMS = {
    'google': {'name': 'Google', 'identity': 'google_main', 'login': True, 'manual_login': True},
    'trustpilot': {'name': 'Trustpilot', 'identity': 'trustpilot_main', 'login': True, 'manual_login': True},
    'tiktok': {'name': 'TikTok', 'identity': 'tiktok_main', 'login': True},
    'instagram': {'name': 'Instagram', 'identity': 'instagram_main', 'login': True},
    'youtube': {'name': 'YouTube', 'identity': 'youtube_main', 'login': False, 'account_login': True, 'manual_login': True},
    # Reddit: nur im Browser; die Anmeldung ist freiwillig (ohne Konto geht es, wenn Reddit die Verbindung zulässt).
    'reddit': {'name': 'Reddit', 'identity': 'reddit_main', 'login': True, 'optional_login': True, 'manual_login': True,
               'note': 'Reddit geht auch ohne Anmeldung. Manche Netze sperrt Reddit ganz – dann kann ein anderes Netz oder eine Anmeldung helfen.'},
    # X: Beiträge mit Antworten nur mit Konto (Browser); einzelne Beiträge ohne Antworten gehen auch ohne.
    'x': {'name': 'X', 'identity': 'x_main', 'login': True, 'manual_login': True,
          'note': 'Achtung: Das Abrufen mit einem X-Konto verstößt gegen die Regeln von X. Das Konto kann gesperrt werden. '
                  'Nimm am besten ein Zweitkonto, kein wichtiges.'},
}
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
NEW_GROUP = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)


def plural(n, one, many):
    """„1 Video“ / „2 Videos“ – Zahl und richtige Einzahl/Mehrzahl."""
    return f'{n} {one if n == 1 else many}'


# ---------- Links ----------

_CONTROL = re.compile(r'[\x00-\x1f\x7f]')   # Steuerzeichen haben in Links nichts verloren

def normalize_link(text):
    """Bereinigt einen eingefügten Link; gibt '' zurück, wenn die Zeile leer ist."""
    link = (text or '').strip().strip('<>"\'')
    if not link:
        return ''
    if '://' not in link and re.match(r'^(www\.)?[\w.-]+\.[a-z]{2,}(/|$)', link, re.I):
        link = 'https://' + link
    return link


def detect_platform(link):
    """'tiktok' | 'instagram' | 'youtube' | 'reddit' | 'x' oder None (Host wird echt geprüft, nicht der Text)."""
    if not isinstance(link, str) or _CONTROL.search(link):
        return None
    try:
        parts = urlsplit(link)
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or parts.username or parts.password:
        return None
    # Dieselbe strenge Prüfung wie überall (util.matches_host): kein Backslash im Host, gültiger Port …
    if matches_host(link, 'tiktok.com'): return 'tiktok'
    if matches_host(link, 'instagram.com'): return 'instagram'
    if matches_host(link, 'youtube.com', 'youtu.be'): return 'youtube'
    if matches_host(link, 'reddit.com', 'redd.it'): return 'reddit'
    if matches_host(link, 'x.com', 'twitter.com', 't.co'): return 'x'   # t.co nur erkannt, nie verfolgt
    return None


_VIDEO_ID = r'[A-Za-z0-9_-]{6,20}'
_INSTA_CODE = r'[A-Za-z0-9_-]{5,40}'


def canonical_video(link):
    """(Schlüssel, einheitlicher Link) eines Video-Links.

    Dasselbe Video in verschiedenen Schreibweisen ergibt denselben Schlüssel: youtube.com/watch?v=ID, youtu.be/ID,
    m.youtube.com, Shorts-Links, Zusätze wie &t=30s oder ?si=… (YouTube); /@name/video/ID mit Verfolgungsparametern
    (TikTok); /p/, /reel/, /tv/ mit Code (Instagram); /r/<name>/comments/<id>, redd.it/<id> und old./np./m.reddit.com
    (Reddit); /<name>/status/<id> auf x.com, twitter.com, mobile. und /i/web/status/<id> (X). Was sich nicht erkennen
    lässt (Kurzlinks wie vm.tiktok.com, t.co), bleibt unverändert (Schlüssel = Link)."""
    plain = (link, link)
    try:
        parts = urlsplit(link)
    except ValueError:
        return plain
    host = (parts.hostname or '').lower()
    path = parts.path or ''
    if host == 'youtu.be':
        m = re.fullmatch(r'/(' + _VIDEO_ID + r')/?', path)
        vid = m.group(1) if m else ''
    elif host == 'youtube.com' or host.endswith('.youtube.com'):
        if path == '/watch':
            vid = (parse_qs(parts.query).get('v') or [''])[0]
        else:
            m = re.fullmatch(r'/(?:shorts|live|embed|v)/(' + _VIDEO_ID + r')/?', path)
            vid = m.group(1) if m else ''
        if not re.fullmatch(_VIDEO_ID, vid or ''):
            vid = ''
    elif host == 'tiktok.com' or host.endswith('.tiktok.com'):
        m = re.fullmatch(r'/(@[^/]+)/(video|photo)/(\d+)/?', path)
        if m:
            return f'tiktok:{m.group(3)}', f'https://www.tiktok.com/{m.group(1)}/{m.group(2)}/{m.group(3)}'
        return plain
    elif host in ('reddit.com', 'redd.it') or host.endswith(('.reddit.com', '.redd.it')):
        ref = platformlinks.reddit_post(link)
        return (f'reddit:{ref[0]}', ref[2]) if ref else plain
    elif host in ('x.com', 'twitter.com') or host.endswith(('.x.com', '.twitter.com')):
        ref = platformlinks.x_status(link)
        return (f'x:{ref[0]}', ref[2]) if ref else plain
    elif host == 'instagram.com' or host.endswith('.instagram.com'):
        m = re.fullmatch(r'(?:/[A-Za-z0-9._]+)?/(p|reel|reels|tv)/(' + _INSTA_CODE + r')/?', path)
        if m:
            kind = 'reel' if m.group(1) == 'reels' else m.group(1)
            return f'instagram:{m.group(2)}', f'https://www.instagram.com/{kind}/{m.group(2)}/'
        return plain
    else:
        return plain
    if not vid:
        return plain
    return f'youtube:{vid}', f'https://www.youtube.com/watch?v={vid}'


def parse_links_detailed(text):
    """Zerlegt das Eingabefeld in (gültige Links nach Plattform, Fehlerzeilen, Zahl zusammengefasster Doppelter).

    Jeder Link steht in einheitlicher Form nur einmal da, auch wenn er in mehreren Schreibweisen eingefügt wurde."""
    groups, bad, seen, merged = {}, [], set(), 0
    for raw in re.split(r'[\r\n]+', text or ''):
        if _CONTROL.search(raw.strip()):
            bad.append(_CONTROL.sub('', raw).strip() or 'Zeile mit Steuerzeichen')
            continue
        link = normalize_link(raw)
        if not link:
            continue
        platform = detect_platform(link)
        if platform is None:
            bad.append(raw.strip())
            continue
        key, link = canonical_video(link)
        if key in seen:
            merged += 1
            continue
        seen.add(key)
        groups.setdefault(platform, []).append(link)
    return groups, bad, merged


def parse_links(text):
    """Zerlegt das Eingabefeld in (gültige Links nach Plattform, Fehlerzeilen). Doppelte entfallen."""
    groups, bad, _merged = parse_links_detailed(text)
    return groups, bad


# ---------- Befehle ----------

def python_exe():
    """Konsolen-Python derselben Umgebung (die Oberfläche läuft selbst unter pythonw)."""
    exe = pathlib.Path(sys.executable)
    if exe.name.lower() == 'pythonw.exe' and (exe.with_name('python.exe')).is_file():
        return str(exe.with_name('python.exe'))
    return str(exe)


_IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
_COUNTRY = re.compile(r'[A-Za-z]{2}')


def _identity(platform, identity):
    """Schlüssel des Kontos (Dateiname-sicher); ohne Angabe das alte Hauptkonto der Plattform."""
    identity = PLATFORMS[platform]['identity'] if identity in (None, '') else identity
    if not _IDENTITY.fullmatch(identity):
        raise ValueError('Ungültiger Kontoschlüssel')
    return identity


def _country(country):
    country = 'de' if country in (None, '') else country
    if not _COUNTRY.fullmatch(country):
        raise ValueError('Das Land muss aus zwei Buchstaben bestehen, z. B. de')
    return country.lower()


def _base(platform, identity=None, country=None):
    return ['--backend', 'local',
            '--identity', _identity(platform, identity), '--country', _country(country)]


def _rotation_options(rotate, rotation_every):
    """`--rotate [--rotation-every N]`: die Konten der Plattform abwechselnd benutzen (Auswahl trifft die Befehlszeile)."""
    if not rotate:
        return []
    cmd = ['--rotate']
    if rotation_every is not None:
        number = int(rotation_every)
        if not 1 <= number <= 100:
            raise ValueError('Konto wechseln nach: bitte eine Zahl von 1 bis 100')
        cmd += ['--rotation-every', str(number)]
    return cmd


def scrape_command(platform, links, max_comments, require_auth=False, export=None, identity=None, country=None,
                   rotate=False, rotation_every=None):
    cmd = ['scrape', *links, *_base(platform, identity, country), '--max-comments', str(int(max_comments))]
    if require_auth:
        cmd.append('--require-auth')
    if export:
        cmd += ['--export', str(export)]
    return cmd + _rotation_options(rotate, rotation_every)


def profile_command(username, limit, max_comments, require_auth=False, identity=None, country=None,
                    rotate=False, rotation_every=None):
    username = username.strip().lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9._]{1,64}', username):
        raise ValueError('Ungültiger TikTok-Benutzername')
    cmd = ['profile', username, *_base('tiktok', identity, country), '--limit', str(int(limit)),
           '--max-comments', str(int(max_comments))]
    if require_auth:
        cmd.append('--require-auth')
    return cmd + _rotation_options(rotate, rotation_every)


def _needs_login(platform):
    if not PLATFORMS[platform]['login'] and not PLATFORMS[platform].get('account_login'):
        raise ValueError('Für diese Plattform ist keine Anmeldung nötig')


def login_command(platform, mode, identity=None, country=None):
    _needs_login(platform)
    if mode not in ('qr', 'password'):
        raise ValueError('Unbekannte Anmeldemethode')
    if mode == 'password' and PLATFORMS[platform].get('manual_login'):
        raise ValueError(f'Bei {PLATFORMS[platform]["name"]} meldest du dich im Browserfenster selbst an.')
    return ['login', *_base(platform, identity, country), '--platform', platform, '--mode', mode, '--timeout', '600']


def check_command(platform, identity=None, country=None):
    _needs_login(platform)
    return ['check', *_base(platform, identity, country), '--platform', platform]


def logout_command(platform, identity=None):
    _needs_login(platform)
    return ['logout', '--platform', platform, '--identity', _identity(platform, identity)]


def import_cookies_command(platform, identity, file, country=None):
    """Anmeldung aus einer Cookie-Datei übernehmen (für Server ohne Browserfenster)."""
    _needs_login(platform)
    if not str(file or '').strip():
        raise ValueError('Bitte eine Datei wählen')
    return ['import-cookies', '--platform', platform, '--identity', _identity(platform, identity),
            '--country', _country(country), '--file', str(file)]


USED_ACCOUNTS_PREFIX = 'Benutzte Konten: '


def parse_used_accounts(output_lines):
    """Namen der Konten aus der Zeile „Benutzte Konten: A, B“ der Befehlszeile (ohne Doppelte, in Reihenfolge)."""
    names = []
    for line in output_lines:
        text = line.strip()
        if text.startswith(USED_ACCOUNTS_PREFIX):
            for name in text[len(USED_ACCOUNTS_PREFIX):].split(', '):
                if name.strip() and name.strip() not in names:
                    names.append(name.strip())
    return names


def parse_summary(output_lines):
    """Liest die gespeicherten/fehlgeschlagenen Videos, auch bei angehängter Teilabruf-Zahl."""
    for line in reversed(output_lines):
        m = re.search(r'fertig:\s*(\d+)\s+ok,\s*(\d+)\s+fehler(?:,\s*\d+\s+teilabrufe)?', line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def log_level(line):
    m = re.match(r'^\d\d:\d\d:\d\d\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b', line)
    return m.group(1) if m else ''


_COOKIE_FILE_PROBLEMS = ('Ungültiger Cookie-Header', 'Keine Cookies in der Importdatei', 'Cookie-JSON', 'Ungültige Netscape-Cookie-Zeile',
                         'Cookie-Import muss Text sein')
_LOG_LINE = re.compile(r'^(\d\d:\d\d:\d\d\s+[A-Z]+\s+[\w.]+\s*\|\s*)(.*)$', re.S)


def friendly_message(text):
    """Technische Meldung des Programms in einfache Worte (für die Anzeige im Fenster; die CLI bleibt unverändert)."""
    if any(text.startswith(p) for p in _COOKIE_FILE_PROBLEMS):
        return 'Die Datei enthält keine passenden Cookies. Bitte eine Cookie-Datei aus einem angemeldeten Browser wählen.'
    m = re.match(r'^\[Errno (\d+)\]', text)
    if m:
        if m.group(1) == '2':
            return 'Die Datei wurde nicht gefunden. Bitte Pfad und Dateiname prüfen.'
        if m.group(1) == '13':
            return 'Auf die Datei darf nicht zugegriffen werden (gesperrt oder fehlende Rechte).'
        return 'Die Datei lässt sich nicht lesen.'
    if "codec can't decode" in text or 'UnicodeDecodeError' in text:
        return 'Die Datei ist keine Textdatei. Bitte eine Cookie-Datei (JSON oder Text) wählen.'
    return text


_TARGET_LOG = re.compile(r'^(?P<head>\d\d:\d\d:\d\d\s+[A-Z]+\s+[\w.]+\s*\|\s*)Ziel (?P<id>\d+): (?P<status>ok|partial|failed|cancelled) '
                         r'\((?P<seen>\d+) Videos, (?P<new>\d+) neu, (?P<comments>\d+) neue Kommentare\)$')
_TARGET_FAIL = re.compile(r'^(?P<head>\d\d:\d\d:\d\d\s+[A-Z]+\s+[\w.]+\s*\|\s*)Ziel (?P<id>\d+) fehlgeschlagen: (?P<rest>.*)$', re.S)
_RUN_STATUS = {'ok': 'fertig', 'partial': 'teilweise fertig', 'failed': 'fehlgeschlagen', 'cancelled': 'abgebrochen'}


def display_line(line, target_name=None):
    """Eine Zeile der Programmausgabe, wie sie im Protokoll des Fensters stehen soll; None = nicht anzeigen.

    Leere Zeilen entfallen (sonst steht dort nur ein Zeitstempel). Die Gesamtsummen am Ende eines Abrufs
    (`{'videos': {…}, 'comments': {…}}`) und die Zeile „fertig: N ok, M fehler“ werden lesbar, Fehlermeldungen
    der Cookie-Übernahme verständlich. Die Ausgabe des Programms selbst (cli.py) bleibt unverändert."""
    if not line or not line.strip():
        return None
    text = line.strip()
    if text.startswith('[SS_FETCH]'):
        try:
            event = json.loads(text[len('[SS_FETCH]'):].strip())
            if event.get('version') != 1 or event.get('stage') not in ('checkpoint', 'result'):
                return None
            platform = PLATFORMS.get(event.get('platform'), {}).get('name', 'Abruf')
            uid = event.get('uid') or ''
            label = f'{platform} {uid.split(":", 1)[-1]}' if uid else platform
            saved = bool(event.get('video_saved'))
            if event['stage'] == 'checkpoint':
                return f'{label}: Zwischenstand gespeichert ({plural(int(event.get("comments_saved", 0)), "Kommentar", "Kommentare")}).'
            status = event.get('comments_status')
            if not saved:
                return f'{label}: Nicht abgerufen. {event.get("message") or "Bitte erneut versuchen."}'
            detail = {'not_requested': 'Nur Videodaten angefordert.',
                      'empty': 'Die Plattform meldet keine Kommentare.',
                      'received': plural(int(event.get('comments_saved', 0)), 'Kommentar', 'Kommentare') + ' gespeichert.',
                      'unavailable': 'Kommentare nicht abrufbar.',
                      'partial': 'Kommentarabruf unvollständig.'}.get(status, 'Abruf unvollständig.')
            message = event.get('message') or ''
            return f'{label}: Video gespeichert. {detail}' + (f' {message}' if message and message != detail else '')
        except (ValueError, TypeError, AttributeError):
            return None
    if text.startswith('{') and text.endswith('}'):
        import ast
        try:
            data = ast.literal_eval(text)
            if isinstance(data, dict) and set(data) <= {'videos', 'comments'} and data:
                videos = sum(int(n) for n in (data.get('videos') or {}).values())
                comments = sum(int(n) for n in (data.get('comments') or {}).values())
                return f'Gespeichert insgesamt: {plural(videos, "Video", "Videos")}, {plural(comments, "Kommentar", "Kommentare")}'
        except (ValueError, SyntaxError, TypeError, AttributeError):
            pass
    m = re.fullmatch(r'fertig:\s*(\d+)\s+ok,\s*(\d+)\s+fehler(?:,\s*(\d+)\s+teilabrufe)?', text)
    if m:
        ok, fail = int(m.group(1)), int(m.group(2))
        partial = int(m.group(3) or 0)
        return (('Teilweise fertig' if partial else 'Fertig') + f': {ok} abgerufen' +
                (f', {fail} fehlgeschlagen' if fail else '') + (f', {partial} unvollständig' if partial else ''))
    m = _TARGET_LOG.match(text)
    if m:   # „Ziel 1: ok (1 Videos, …)“ – mit Namen des Ziels und richtiger Einzahl/Mehrzahl
        seen, comments = int(m.group('seen')), int(m.group('comments'))
        return (f"{m.group('head')}{_target_text(m.group('id'), target_name)}: {_RUN_STATUS[m.group('status')]} - "
                f"{plural(seen, 'Video', 'Videos')}, {m.group('new')} neu, {plural(comments, 'neuer Kommentar', 'neue Kommentare')}")
    m = _TARGET_FAIL.match(text)
    if m:
        return f"{m.group('head')}{_target_text(m.group('id'), target_name)} fehlgeschlagen: {m.group('rest')}"
    m = _LOG_LINE.match(line)
    if m:
        return m.group(1) + friendly_message(m.group(2).strip())
    return friendly_message(line)


def _target_text(target_id, target_name):
    """Name des Ziels, wenn er sich finden lässt, sonst „Ziel 5“."""
    if target_name is not None:
        try:
            name = target_name(int(target_id))
        except Exception:   # reine Schönheit: nie die Anzeige stören
            name = None
        if name:
            return name
    return f'Ziel {target_id}'


EXIT_TEXT = {
    0: 'Fertig.',
    1: 'Nicht alles hat geklappt – Details im Protokoll.',
    2: 'Anmeldung nicht bestätigt.',
    3: 'Eine bestätigte Anmeldung fehlt oder ist abgelaufen – bitte unter „Konten“ anmelden.',
    4: 'Es läuft gerade ein anderer Browserauftrag (zum Beispiel der Hintergrunddienst). Bitte kurz warten und noch einmal versuchen.',
}


# ---------- Konto-Status (ohne Browser) ----------

def cookie_file(platform, identity=None, root=None):
    return pathlib.Path(ROOT if root is None else root) / 'data' / 'cookies' / f'{_identity(platform, identity)}.{platform}.json'


def account_status(platform, root=None, identity=None):
    """Nur Dateien lesen: gibt (Schlüssel, Text) zurück. Kein Netzwerk, kein Browser.

    `identity` ist der Schlüssel des Kontos; ohne Angabe gilt das alte Hauptkonto (tiktok_main / instagram_main)."""
    info = PLATFORMS[platform]
    if not info['login'] and not (info.get('account_login') and identity and cookie_file(platform, identity, root).exists()):
        return 'none', 'Keine Anmeldung nötig'
    try:
        path = cookie_file(platform, identity, root)
    except ValueError:
        return 'error', 'Ungültiger Kontoschlüssel'
    optional = ' (bei Reddit freiwillig)' if info.get('optional_login') else ''
    if not path.is_file():
        return 'missing', 'Nicht angemeldet' + optional
    now = time.time()

    def alive(cookie):   # wie CookieJar.for_platform: eine Ablaufzeit in der Vergangenheit zählt nicht mehr
        expires = cookie.get('expires')
        return isinstance(expires, bool) or not isinstance(expires, (int, float)) or expires <= 0 or expires > now
    try:
        cookies = json.loads(path.read_text(encoding='utf-8'))
        names = {c.get('name') for c in cookies if c.get('value')}
        live = {c.get('name') for c in cookies if c.get('value') and alive(c)}
    except (OSError, ValueError, AttributeError, TypeError):
        return 'error', 'Anmeldedatei unlesbar – neu anmelden'
    from .net.cookies import AUTH, AUTH_ALL
    wanted = set(AUTH[platform])
    if not (wanted <= names if platform in AUTH_ALL else names & wanted):
        return 'missing', 'Nicht angemeldet' + optional
    if not (wanted <= live if platform in AUTH_ALL else live & wanted):
        return 'expired', 'Anmeldung abgelaufen – neu anmelden'
    try:
        age_h = (time.time() - path.stat().st_mtime) / 3600
    except OSError:
        return 'error', 'Anmeldedatei unlesbar – neu anmelden'
    when = f'vor {int(age_h)} Std.' if age_h >= 1 else 'vor weniger als 1 Std.'
    if age_h >= 24 * 3:
        when = f'vor {int(age_h // 24)} Tagen'
    return 'saved', f'Anmeldung gespeichert ({when})'


# ---------- Zugangsdaten-Datei (data/credentials.json) ----------

def credentials_file(root=None):
    return pathlib.Path(ROOT if root is None else root) / 'data' / 'credentials.json'


def _read_credentials(path):
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig') or '{}')
    except (OSError, ValueError):
        raise ValueError('Die Datei credentials.json ist beschädigt (kein gültiges JSON). '
                         'Bitte im Editor reparieren – es wurde nichts verändert.') from None
    if not isinstance(data, dict):
        raise ValueError('Die Datei credentials.json hat ein unerwartetes Format – es wurde nichts verändert.')
    return data


def credentials_state(platform, identity, root=None):
    """'missing' (kein Eintrag) | 'empty' (Eintrag leer) | 'ready'. Zeigt nie Werte."""
    try:
        node = (_read_credentials(credentials_file(root)).get(platform) or {}).get(_identity(platform, identity))
    except (ValueError, AttributeError):
        return 'missing'
    if not isinstance(node, dict):
        return 'missing'
    user, password = node.get('user'), node.get('password')
    return 'ready' if isinstance(user, str) and user.strip() and isinstance(password, str) and password else 'empty'


def ensure_credentials_entry(platform, identity, root=None):
    """Legt für das Konto einen leeren Eintrag an. Bestehende Einträge bleiben unberührt.
    Gibt True zurück, wenn etwas angelegt wurde. Wirft ValueError, wenn die Datei beschädigt ist."""
    _needs_login(platform)
    if PLATFORMS[platform].get('account_login'):
        raise ValueError('Für diese Plattform wird die manuelle Anmeldung verwendet; neue Konto-Geheimnisse liegen in der Geheimnisdatei.')
    identity = _identity(platform, identity)
    path = credentials_file(root)
    data = _read_credentials(path)
    node = data.get(platform)
    if node is None:
        node = data[platform] = {}
    if not isinstance(node, dict):
        raise ValueError(f'In credentials.json hat „{platform}“ ein unerwartetes Format – es wurde nichts verändert.')
    if identity in node:
        return False
    node[identity] = {'user': '', 'password': ''}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)
    return True


# ---------- Einstellungen (settings.toml, Kommentare bleiben erhalten) ----------

# TOML-Textbearbeitung und EDITABLE liegen in settings_text; Datei-I/O bleibt hier.
SETTINGS_BACKUP = 'settings.toml.bak'
_LOCKED_TEXT = 'Die Datei settings.toml ist gerade gesperrt (ein anderes Programm hat sie offen). Bitte schließen und noch einmal versuchen.'


def read_settings(root=ROOT):
    from .config import load
    return load(str(pathlib.Path(root) / 'settings.toml'))


def write_settings(values, root=ROOT):
    """Schreibt Einstellungen nach settings.toml: nur der Kopfteil, Kommentare und Zeilenenden bleiben, atomar.

    Die fertige Datei wird mit tomllib und config.load gegengelesen, bevor sie die alte ersetzt;
    ungültige Werte schreiben nichts (ValueError mit deutscher Meldung). Die vorige Fassung bleibt
    als settings.toml.bak liegen (siehe restore_settings_backup)."""
    for key, value in values.items():
        if key not in EDITABLE:
            raise ValueError(f'Einstellung nicht änderbar: {key}')
        _check_setting(key, value)
    path = pathlib.Path(root) / 'settings.toml'
    try:
        raw = path.read_bytes().decode('utf-8-sig') if path.exists() else ''
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f'settings.toml lässt sich nicht lesen ({getattr(error, "strerror", None) or "Fehler"}).') from None
    import tomllib
    try:
        tomllib.loads(raw)  # eine schon kaputte Datei nicht „zufällig“ überschreiben
        text = render_settings(raw, values)
        tomllib.loads(text)  # nie eine kaputte Datei schreiben
    except tomllib.TOMLDecodeError:
        raise ValueError('settings.toml ist beschädigt und kann so nicht geändert werden. '
                         'Unter Einstellungen → System lässt sie sich zurücksetzen.') from None
    tmp = path.with_name(path.name + '.tmp')
    try:
        tmp.write_text(text, encoding='utf-8', newline='')
        try:
            from .config import load
            load(str(tmp))
        except ValueError as error:
            raise ValueError(f'Diese Werte sind nicht erlaubt: {error}') from None
        if path.exists():
            _copy_retry(path, path.with_name(SETTINGS_BACKUP))
        _replace_retry(tmp, path)
    except PermissionError:
        raise ValueError(_LOCKED_TEXT) from None
    except OSError as error:
        raise ValueError(f'settings.toml konnte nicht gespeichert werden ({error.strerror or "Fehler"}).') from None
    finally:
        try: tmp.unlink()
        except OSError: pass


def _replace_retry(src, dst):
    for attempt in range(6):
        try:
            os.replace(src, dst); return
        except PermissionError:
            if attempt == 5: raise
            time.sleep(0.1 * (attempt + 1))


def _copy_retry(src, dst):
    import shutil
    for attempt in range(4):
        try:
            shutil.copyfile(src, dst); return
        except PermissionError:
            if attempt == 3: raise
            time.sleep(0.1)


def restore_settings_backup(root=ROOT):
    """Holt die vorige Fassung (settings.toml.bak) zurück; die jetzige wird dabei zur neuen Sicherung."""
    path = pathlib.Path(root) / 'settings.toml'
    bak = path.with_name(SETTINGS_BACKUP)
    if not bak.is_file():
        raise ValueError('Es gibt noch keine Sicherung der Einstellungen.')
    try:
        from .config import load
        load(str(bak))
    except ValueError as error:
        raise ValueError(f'Die Sicherung ist selbst nicht brauchbar: {error}') from None
    try:
        current = path.read_bytes() if path.exists() else None
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_bytes(bak.read_bytes())
        _replace_retry(tmp, path)
        if current is not None:
            bak.write_bytes(current)
    except PermissionError:
        raise ValueError(_LOCKED_TEXT) from None
    except OSError as error:
        raise ValueError(f'Wiederherstellen fehlgeschlagen ({error.strerror or "Fehler"}).') from None


def reset_settings(template_text, root=ROOT):
    """Ersetzt eine unbrauchbare settings.toml durch `template_text`; die alte bleibt als settings.toml.defekt."""
    path = pathlib.Path(root) / 'settings.toml'
    try:
        if path.exists():
            _copy_retry(path, path.with_name('settings.toml.defekt'))
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(template_text, encoding='utf-8', newline='')
        _replace_retry(tmp, path)
    except PermissionError:
        raise ValueError(_LOCKED_TEXT) from None
    except OSError as error:
        raise ValueError(f'Zurücksetzen fehlgeschlagen ({error.strerror or "Fehler"}).') from None


# ---------- Datenbank (nur lesend) ----------

def _casefold(value):
    return value.casefold() if isinstance(value, str) else value


def search_pattern(search):
    """LIKE-Muster für eine Sucheingabe: Groß-/Kleinschreibung egal (auch Umlaute), %, _ und \\ wörtlich."""
    text = _casefold((search or '').strip())
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def _ro(db_path):
    """Schreibgeschützte Verbindung, die beim Verlassen wirklich geschlossen wird (sonst bleibt die Datei gesperrt).

    Die SQL-Funktion `cf(text)` (casefold) ist angemeldet: SQLites LIKE kennt Groß-/Kleinschreibung nur für ASCII,
    „MÜLLER“ fände „müller“ sonst nicht."""
    from .util import ro_connect
    db = ro_connect(db_path, timeout=5)
    db.create_function('cf', 1, _casefold, deterministic=True)
    return contextlib.closing(db)


def db_path(root=None):
    """Datenbank des Programms. Gleiche Regel wie im Rest (config.py): SS_DB_PATH vor settings.toml vor Standard."""
    root = pathlib.Path(ROOT if root is None else root)
    raw = os.environ.get('SS_DB_PATH')
    if not raw:
        try:
            raw = read_settings(root).db_path
        except Exception:  # kaputte settings.toml darf das Lesen der Daten nicht verhindern
            raw = 'data/scrape.db'
    path = pathlib.Path(raw)
    return path if path.is_absolute() else root / path


def list_videos(path, search='', platform='', limit=2000):
    if not pathlib.Path(path).is_file():
        return []
    sql = ('SELECT v.uid, v.platform, v.video_id, v.url, v.author, v.caption, v.view_count, v.like_count, '
           'v.comment_count, v.fetched_at, '
           '(SELECT COUNT(*) FROM comments c WHERE c.platform=v.platform AND c.video_id=v.video_id) '
           'FROM videos v WHERE 1=1')
    args = []
    if platform:
        sql += ' AND v.platform=?'; args.append(platform)
    if search.strip():
        sql += " AND (cf(v.author) LIKE ? ESCAPE '\\' OR cf(v.caption) LIKE ? ESCAPE '\\' OR cf(v.url) LIKE ? ESCAPE '\\')"
        pat = search_pattern(search)
        args += [pat, pat, pat]
    sql += " ORDER BY substr(replace(v.fetched_at,' ','T'),1,19) DESC, v.uid LIMIT ?"   # Cloud-Zeilen haben ein T im Wert
    args.append(int(limit))
    with _ro(path) as db:
        return db.execute(sql, args).fetchall()


def list_comments(path, platform, video_id, limit=5000):
    if not pathlib.Path(path).is_file():
        return []
    with _ro(path) as db:
        return db.execute(
            'SELECT author, text, like_count, created_at, parent_id FROM comments '
            'WHERE platform=? AND video_id=? ORDER BY COALESCE(like_count,0) DESC, rowid LIMIT ?',
            (platform, video_id, int(limit))).fetchall()


def counts(path):
    """(Videos, Kommentare); (0, 0) ohne Datenbank, (None, None) wenn sie gerade nicht lesbar ist."""
    if not pathlib.Path(path).is_file():
        return 0, 0
    try:
        with _ro(path) as db:
            return (db.execute('SELECT COUNT(*) FROM videos').fetchone()[0],
                    db.execute('SELECT COUNT(*) FROM comments').fetchone()[0])
    except sqlite3.Error:   # gesperrt, offenes Journal, beschädigt: nicht werfen; (None, None) = „gerade nicht lesbar“
        return None, None


EXPORT_FORMATS = {
    'csv-excel': 'CSV für Excel (Semikolon)',
    'csv': 'CSV (Komma)',
    'jsonl': 'JSONL (für Programme)',
}

# Lesbare deutsche Spalten der CSV-Dateien: (Überschrift, Quelle). Rohspalten (uid, raw, …) gibt es nur im JSONL.
_VIDEO_COLUMNS = (('Plattform', 'platform'), ('Autor', 'author'), ('Beschreibung', 'caption'), ('Link', 'url'),
                  ('Aufrufe', 'view_count'), ('Likes', 'like_count'), ('Kommentare', 'comment_count'),
                  ('Länge (m:ss)', 'duration_text'), ('Länge (Sekunden)', 'duration_s'),
                  ('Veröffentlicht', 'created_at'), ('Zuerst gesehen', 'first_seen_at'), ('Zuletzt abgerufen', 'fetched_at'),
                  ('Gruppen', 'groups'))
_COMMENT_COLUMNS = (('Plattform', 'platform'), ('Video-Link', 'video_url'), ('Autor', 'author'), ('Text', 'text'),
                    ('Likes', 'like_count'), ('Geschrieben', 'created_at'), ('Zuerst gesehen', 'first_seen_at'),
                    ('Antwort auf', 'reply_to'), ('Stimmung (KI-Einschätzung)', 'mood'),
                    ('Stimmungswert (KI-Einschätzung)', 'mood_score'))
_PERSON_COLUMNS = (('Rolle', 'role'), ('Plattform', 'platform'), ('Name', 'display_name'), ('Kennung', 'author_key'),
                   ('Zuerst gesehen', 'first_seen_at'), ('Videos', 'videos'), ('Aufrufe', 'views'), ('Likes', 'likes'), ('Kommentare', 'comments'),
                   ('Erste Aktivität', 'first_activity'), ('Letzte Aktivität', 'last_activity'),
                   ('Positiv (KI-Einschätzung)', 'positive'), ('Negativ (KI-Einschätzung)', 'negative'),
                   ('Eingestufte Kommentare', 'analyzed'), ('Gruppen', 'group_names'))


def _stamp(text):
    """Zeitangabe aus der Datenbank (UTC) als „JJJJ-MM-TT HH:MM“ in Ortszeit; nur-Datum und Unlesbares bleiben unverändert."""
    import datetime
    if not text:
        return ''
    text = str(text).strip()
    if len(text) <= 10:
        return text
    try:
        moment = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return text
    moment = moment.replace(tzinfo=moment.tzinfo or datetime.timezone.utc).astimezone()
    return moment.strftime('%Y-%m-%d %H:%M')


def _platform_name(key):
    return PLATFORMS.get(key, {}).get('name', key or '')


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _dict_rows(db, sql, args=()):
    cur = db.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return cols, [dict(zip(cols, r)) for r in cur.fetchall()]


def export_data(path, folder, fmt, group_id=None, uids=None):
    """Schreibt Videos, Kommentare, Gruppen und den Verlauf der Zahlen als CSV oder JSONL in `folder`. Gibt die Dateien zurück.

    fmt: 'csv-excel' (Semikolon, UTF-8 mit BOM, öffnet in deutschem Excel sofort sauber), 'csv' (Komma) oder 'jsonl'.
    CSV hat lesbare deutsche Spalten (videos, kommentare, gruppen, gruppen_zuordnung, verlauf); JSONL enthält die
    Rohspalten (videos, comments, groups, group_videos, video_snapshots).
    group_id: nur diese Gruppe – der Filter gilt für alle Dateien. uids: nur diese Videos (Auswahl).
    Beides zusammen: nur Videos, die in beidem vorkommen."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError('Format muss csv-excel, csv oder jsonl sein')
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with _ro(path) as db:
        have = _tables(db)
        people_repo.prepare(db)
        keep = None
        if group_id is not None:
            keep = {r[0] for r in db.execute('SELECT video_uid FROM group_videos WHERE group_id=?', (int(group_id),))}                 if 'group_videos' in have else set()
            if 'group_people' in have and 'videos' in have:   # auch Videos der Ersteller, die zur Gruppe gehören
                keep |= {r[0] for r in db.execute(
                    'SELECT v.uid FROM videos v WHERE ' + people_repo.creator_member_sql('v'), (int(group_id),))}
        if uids is not None:
            keep = set(uids) if keep is None else keep & set(uids)

        def read(table, order=''):
            if table not in have:
                return [], []
            return _dict_rows(db, f'SELECT * FROM {table} {order}')

        vcols, videos = read('videos', "ORDER BY substr(replace(fetched_at,' ','T'),1,19) DESC, uid")
        ccols, comments = read('comments', 'ORDER BY platform, video_id, rowid')
        scols, snaps = read('video_snapshots', 'ORDER BY captured_at, video_uid')
        gcols, groups = read('groups', 'ORDER BY name COLLATE NOCASE')
        mcols, members = read('group_videos', 'ORDER BY group_id, added_at, video_uid')
        _pc, group_people = read('group_people', 'ORDER BY group_id, role, platform, author_key')
        _sc, sentiments = read('comment_sentiment', 'ORDER BY comment_uid')
        channel_rows = _channel_rows(db, have, group_id, uids)
        people = []
        if 'videos' in have and 'comments' in have:
            for role in people_repo.ROLES:
                people += [dict(p, role=people_repo.ROLE_NAMES[role], _role=role) for p in people_repo.list_people(db, role, limit=10 ** 9)[0]]
        member_comments = set()
        if group_id is not None and 'group_people' in have and 'comments' in have:   # Kommentare der Kommentarschreiber der Gruppe
            member_comments = {r[0] for r in db.execute('SELECT c.uid FROM comments c WHERE ' + people_repo.commenter_member_sql('c'),
                                                        (int(group_id),))}
    # Die Gruppen-Spalte der Videos nennt immer alle Gruppen des Videos, auch beim Export einer einzelnen Gruppe.
    all_members, all_groups = members, {g['id']: g['name'] for g in groups}
    all_videos = videos
    if keep is not None:
        videos = [v for v in videos if v['uid'] in keep]
        comments = [c for c in comments if f'{c["platform"]}:{c["video_id"]}' in keep or c['uid'] in member_comments]
        snaps = [s for s in snaps if s['video_uid'] in keep]
        members = [m for m in members if m['video_uid'] in keep and (group_id is None or m['group_id'] == int(group_id))]
        wanted = {m['group_id'] for m in members} | ({int(group_id)} if group_id is not None else set())
        if group_id is not None:
            group_people = [m for m in group_people if m['group_id'] == int(group_id)]
        elif uids is not None:
            group_people = []
        wanted |= {m['group_id'] for m in group_people}
        groups = [g for g in groups if g['id'] in wanted]
    # Personen: bei „nur diese Gruppe“ die Mitglieder, bei einer Auswahl die Ersteller und Kommentarschreiber der Auswahl
    if group_id is not None:
        member_keys = {(m['role'], m['platform'], m['author_key']) for m in group_people}
        people = [p for p in people if (p['_role'], p['platform'], p['author_key']) in member_keys]
    elif uids is not None:
        creators = {(v['platform'], people_repo.author_key(v.get('author_id'), v.get('author'))) for v in videos}
        writers = {(c['platform'], people_repo.author_key(c.get('author_id'), c.get('author'))) for c in comments}
        people = [p for p in people if (p['platform'], p['author_key']) in (creators if p['_role'] == 'creator' else writers)]
    kept = {c['uid']: c for c in comments}
    sentiments = [s for s in sentiments if s['comment_uid'] in kept]

    if fmt == 'jsonl':
        written = _write_jsonl(folder, (('videos', videos), ('comments', comments), ('groups', groups),
                                        ('group_videos', members), ('group_people', group_people),
                                        ('comment_sentiment', sentiments), ('video_snapshots', snaps)))
        return written + _write_channels(folder, fmt, channel_rows)
    shown = {v['uid'] for v in videos}
    written = _write_csv(folder, ';' if fmt == 'csv-excel' else ',', videos, comments, snaps, groups, members,
                         [m for m in all_members if m['video_uid'] in shown], all_groups, all_videos=all_videos,
                         sentiments=sentiments, people=people, group_people=group_people)
    return written + _write_channels(folder, fmt, channel_rows)


_CHANNEL_COLUMNS = (('Plattform', 'platform'), ('Kanal', 'channel'), ('Name', 'name'), ('Zustand', 'state'),
                    ('Videos gefunden', 'total'), ('Abgerufen', 'done'), ('Übersprungen', 'skipped'), ('Offen', 'pending'),
                    ('Fehlgeschlagen', 'failed'), ('Gruppe', 'group'), ('Tempo', 'pace'), ('Hinweis zur Liste', 'note'))


def _channel_rows(db, have, group_id, uids):
    """Kanäle (ganze Kanäle der Automatik) mit Fortschritt; nur wenn es welche gibt und kein Auswahl-Export läuft."""
    if uids is not None or 'targets' not in have or 'channel_items' not in have:
        return []
    names = {'queued': 'wartet', 'listing': 'holt die Videoliste', 'fetching': 'ruft Videos ab', 'paused': 'pausiert',
             'done': 'fertig', 'watching': 'beobachtet neue Videos'}
    pace = {'careful': 'Vorsichtig', 'normal': 'Normal', 'fast': 'Schnell'}
    _cols, rows = _dict_rows(db, "SELECT t.*, g.name AS group_name FROM targets t LEFT JOIN groups g ON g.id=t.group_id "
                                 "WHERE t.kind='channel' ORDER BY t.platform, t.value")
    counts = {}
    for target_id, status, n in db.execute('SELECT target_id, status, COUNT(*) FROM channel_items GROUP BY target_id, status'):
        counts.setdefault(target_id, {})[status] = n
    out = []
    for r in rows:
        if group_id is not None and r['group_id'] != int(group_id):
            continue
        c = counts.get(r['id'], {})
        out.append({'platform': r['platform'], 'channel': r['value'], 'name': r.get('display_name') or '',
                    'state': names.get(r.get('backfill_state'), r.get('backfill_state') or ''), 'total': sum(c.values()),
                    'done': c.get('done', 0), 'skipped': c.get('skipped', 0), 'pending': c.get('pending', 0) + c.get('running', 0),
                    'failed': c.get('failed', 0), 'group': r.get('group_name') or '', 'pace': pace.get(r.get('pace_profile'), ''),
                    'note': r.get('list_note') or ''})
    return out


def _write_channels(folder, fmt, rows):
    if not rows:
        return []
    if fmt == 'jsonl':
        return _write_jsonl(folder, (('channels', rows),))
    target = folder / 'kanaele.csv'
    with target.open('w', encoding='utf-8-sig', newline='') as out:
        w = csv.writer(out, delimiter=';' if fmt == 'csv-excel' else ',', quoting=csv.QUOTE_MINIMAL)
        w.writerow([title for title, _key in _CHANNEL_COLUMNS])
        for row in rows:
            w.writerow([_csv_safe(_platform_name(row[key]) if key == 'platform' else row.get(key, '')) for _title, key in _CHANNEL_COLUMNS])
    return [target]


def _write_jsonl(folder, tables):
    written = []
    for name, rows in tables:
        target = folder / f'{name}.jsonl'
        with target.open('w', encoding='utf-8', newline='') as out:
            for rec in rows:
                rec = dict(rec)
                for k in ('hashtags', 'mentions', 'raw'):
                    if isinstance(rec.get(k), str):
                        try: rec[k] = json.loads(rec[k])
                        except ValueError: pass
                out.write(json.dumps(rec, ensure_ascii=False) + '\n')
        written.append(target)
    return written


def _write_csv(folder, delimiter, videos, comments, snaps, groups, members, video_members, group_names, all_videos=None,
               sentiments=(), people=(), group_people=()):
    by_uid = {v['uid']: v for v in videos}
    mood_of = {s['comment_uid']: s for s in sentiments}
    gname = {g['id']: g['name'] for g in groups}
    names_of = {}
    for m in video_members:
        names_of.setdefault(m['video_uid'], []).append(group_names.get(m['group_id'], ''))
    url_of = {f'{v["platform"]}:{v["video_id"]}': v.get('url') or '' for v in (all_videos if all_videos is not None else videos)}
    author_of = {(c['platform'], c['comment_id']): c.get('author') or '' for c in comments}

    video_rows = [dict(v, groups=', '.join(sorted(n for n in names_of.get(v['uid'], []) if n)),
                       duration_text=format_duration(v.get('duration_s'))) for v in videos]
    comment_rows = []
    for c in comments:
        parent = c.get('parent_id')
        reply = '' if not parent else (author_of.get((c['platform'], parent)) or 'anderen Kommentar')
        s = mood_of.get(c['uid'])   # nur gültige Einstufungen: der Hash gehört zum heutigen Text
        valid = s is not None and s.get('text_hash') == sentiment_repo.text_hash(c.get('text'))
        comment_rows.append(dict(c, video_url=url_of.get(f'{c["platform"]}:{c["video_id"]}', ''), reply_to=reply,
                                 mood=s['label'] if valid else '', mood_score=s['score'] if valid else ''))
    snap_rows = []
    for s in snaps:
        v = by_uid.get(s['video_uid'])
        snap_rows.append({'time': _stamp(s['captured_at']), 'platform': _platform_name((v or {}).get('platform') or s['video_uid'].split(':')[0]),
                          'link': (v or {}).get('url') or s['video_uid'], 'views': s['view_count'], 'likes': s['like_count'],
                          'comments': s['comment_count'], 'shares': s['share_count']})
    counts = {}
    for m in members:
        counts[m['group_id']] = counts.get(m['group_id'], 0) + 1
    group_rows = [{'name': g['name'], 'color': g['color'], 'videos': counts.get(g['id'], 0), 'created': _stamp(g['created_at'])} for g in groups]
    member_rows = []
    for m in members:
        v = by_uid.get(m['video_uid']) or {}
        member_rows.append({'group': gname.get(m['group_id'], ''), 'platform': _platform_name(v.get('platform')),
                            'link': v.get('url') or m['video_uid'], 'author': v.get('author') or '', 'added': _stamp(m['added_at'])})

    person_rows = [{'group': gname.get(m['group_id'], ''), 'role': people_repo.ROLE_NAMES.get(m['role'], m['role']),
                    'platform': _platform_name(m['platform']), 'display_name': m.get('display_name') or '',
                    'author_key': m['author_key'], 'added': _stamp(m.get('added_at'))} for m in group_people]

    def cell(key, value):
        if key == 'platform':
            value = _platform_name(value)
        elif key in ('created_at', 'first_seen_at', 'fetched_at'):
            value = _stamp(value)
        elif key in ('caption', 'text'):
            value = ' '.join(str(value).split()) if value else ''    # Zeilenumbrüche stören in Tabellen
        return '' if value is None else value

    specs = (
        ('videos', _VIDEO_COLUMNS, video_rows),
        ('kommentare', _COMMENT_COLUMNS, comment_rows),
        ('gruppen', (('Gruppe', 'name'), ('Farbe', 'color'), ('Videos', 'videos'), ('Angelegt', 'created')), group_rows),
        ('gruppen_zuordnung', (('Gruppe', 'group'), ('Plattform', 'platform'), ('Video-Link', 'link'), ('Autor', 'author'),
                               ('Hinzugefügt', 'added')), member_rows),
        ('verlauf', (('Zeit', 'time'), ('Plattform', 'platform'), ('Video-Link', 'link'), ('Aufrufe', 'views'), ('Likes', 'likes'),
                     ('Kommentare', 'comments'), ('Shares', 'shares')), snap_rows),
        ('personen', _PERSON_COLUMNS, list(people)),
        ('gruppen_personen', (('Gruppe', 'group'), ('Rolle', 'role'), ('Plattform', 'platform'), ('Name', 'display_name'),
                              ('Kennung', 'author_key'), ('Hinzugefügt', 'added')), person_rows),
    )
    written = []
    for name, columns, rows in specs:
        target = folder / f'{name}.csv'
        with target.open('w', encoding='utf-8-sig', newline='') as out:
            w = csv.writer(out, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
            w.writerow([title for title, _key in columns])
            for row in rows:
                w.writerow([_csv_safe(cell(key, row.get(key))) for _title, key in columns])
        written.append(target)
    return written


def _csv_safe(value):
    """Verhindert Formel-Einschleusung, wenn die CSV in Excel geöffnet wird."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


# ---------- Prozess ----------

SOFT_CANCEL_SECONDS = 10   # so lange darf ein `run-target` nach „Abbrechen" selbst zu Ende kommen (Lauf „abgebrochen")
SOFT_CANCEL_COMMANDS = ('run-target','mailkonto')   # Befehle, die SS_CANCEL_FILE beachten
MAIL_CANCEL_SECONDS = 150  # laufender Aufruf (max. 60 s) + gemeinsames SDK-Abschlussbudget (60 s) + Reserve


def recover_interrupted_runs(root=None):
    """Nach einem hart beendeten Hilfsprozess: Läufe, die noch „running" stehen, als unterbrochen vermerken.
    Nur wenn niemand mehr die Ausführungssperre hält – sonst arbeitet der Hintergrunddienst gerade wirklich.
    Gibt die Zahl der aufgeräumten Läufe zurück; scheitert nie laut."""
    try:
        from . import repo, runlock
        from .store import Store
        path = db_path(root)
        if not pathlib.Path(path).is_file():
            return 0
        if runlock.holder(runlock.root_for_db(path)):
            return 0
        with Store(str(path)) as store:
            before = store.db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
            if before:
                repo.recover_stale_runs(store.db, 0)
            return before
    except Exception:
        return 0


class Job:
    """Ein laufender Werkzeug-Prozess; Ausgabe zeilenweise an on_line, danach on_done(exit_code)."""

    def __init__(self, args, on_line, on_done, root=ROOT):
        self.args, self.on_line, self.on_done, self.root = args, on_line, on_done, root
        self.proc = None
        self.lines = []
        self.cancelled = False
        self._lock = threading.Lock()
        self._cancel_pending = False
        self._cancel_finished = threading.Event()
        self._cancel_finished.set()
        # Abbruchdatei: ein `run-target` prüft sie zwischen zwei Videos und beendet den Lauf selbst als „abgebrochen"
        # (dann schreibt der Planer finish_run); erst wenn das nicht hilft, wird der Prozessbaum beendet.
        self.cancel_file = (pathlib.Path(tempfile.gettempdir()) / f'socialscraper-abbruch-{uuid.uuid4().hex}.txt'
                            if args and args[0] in SOFT_CANCEL_COMMANDS else None)

    def start(self):
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        env['SS_EMBED_BROWSER'] = '1'
        env['SS_ANDROID_HANDOFF'] = '1'
        env.pop('PYTHONPATH', None)
        if self.cancel_file is not None:
            env['SS_CANCEL_FILE'] = str(self.cancel_file)
        self.proc = subprocess.Popen(
            [python_exe(), '-m', 'socialscraper', *self.args], cwd=str(self.root), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW | NEW_GROUP)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            for raw in self.proc.stdout:
                line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
                self.lines.append(line)
                self.on_line(line)
        except Exception as error:  # Lesefehler dürfen den Abschluss nicht verhindern
            self.on_line(f'[Oberfläche] Ausgabe konnte nicht gelesen werden: {error}')
        finally:
            self.proc.stdout.close()
        code = self.proc.wait()
        # Erst nachdem die Baum-Beendigung fertig ist darf das Fenster einen neuen Auftrag starten.
        self._cancel_finished.wait()
        with self._lock:
            cancelled = self.cancelled
        if cancelled:
            recover_interrupted_runs(self.root)   # hart beendet: im Kind lief kein finally, Läufe blieben sonst „Läuft …"
        self.on_done(-1 if cancelled else code)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def cancel(self):
        """Fordert das Ende des Prozessbaums an; ein bereits beendeter Lauf bleibt erfolgreich."""
        with self._lock:
            if not self.running() or self._cancel_pending:
                return False
            self._cancel_pending = True
            self._cancel_finished.clear()
        threading.Thread(target=self._terminate, daemon=True).start()
        return True

    def _soft_cancel(self):
        """Abbruchdatei anlegen und kurz warten: ein `run-target` beendet sich dann selbst, der Lauf steht als „abgebrochen"
        in der Datenbank. True, wenn der Prozess von sich aus geendet hat."""
        if self.cancel_file is None:
            return False
        try:
            self.cancel_file.write_text('stop', encoding='utf-8')
        except OSError:
            return False
        seconds=MAIL_CANCEL_SECONDS if tuple(self.args[:2])==('mailkonto','neu') else SOFT_CANCEL_SECONDS
        end = time.monotonic() + seconds
        while self.running() and time.monotonic() < end:
            time.sleep(0.2)
        return not self.running()

    def _terminate(self):
        killed = False
        try:
            if self._soft_cancel():
                killed = True
            elif os.name == 'nt':
                result = subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'],
                                        capture_output=True, creationflags=NO_WINDOW, timeout=15)
                killed = result.returncode == 0
                if not killed and self.running():
                    killed = self._force_tree()
            elif self.running():
                self.proc.terminate()
                killed = True
        except (OSError, subprocess.TimeoutExpired):
            # Wenn taskkill nicht erreichbar ist, gezielt die zuvor erfassten Kinder und den Auftrag beenden.
            if os.name == 'nt':
                killed = self._force_tree()
        finally:
            if self.cancel_file is not None:
                try:
                    self.cancel_file.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self.cancelled = killed
                self._cancel_pending = False
            self._cancel_finished.set()

    def _force_tree(self):
        if not self.running():
            return False
        from .net.deadline import terminate_children
        terminate_children(self.proc.pid, wait_ms=5000)
        if self.running():
            self.proc.kill()
        return True
