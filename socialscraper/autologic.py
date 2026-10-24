"""Logik des Tabs „Automatik“ ohne Tk: Lesen der Ziele/Läufe/Kommentare, Texte, Zeitangaben, Schreibzugriffe.

Gelesen wird schreibgeschützt (die Datenbank wird dabei nicht angelegt oder verändert); geschrieben wird über
`Store` und `repo`. Datenbankfehler werden zu verständlichen ValueError-Meldungen.
"""
import contextlib
import datetime
import os
import pathlib
import re
import sqlite3

from . import guicore, repo
from .platforms import PLATFORM_NAMES

KIND_LABELS = {'video': 'Video-Link', 'hashtag': 'Hashtag', 'search': 'Suchbegriff', 'profile': 'Profil', 'channel': 'Kanal'}
KIND_ORDER = ('video', 'hashtag', 'search', 'profile')
# Welche Plattform zu welcher Art passt (wie repo.add_target und discovery es erlauben).
PLATFORMS_FOR_KIND = {
    'video': ('tiktok', 'instagram', 'youtube', 'reddit', 'x'),
    'hashtag': ('tiktok', 'youtube', 'x'),
    'search': ('tiktok', 'youtube', 'reddit', 'x'),
    'profile': ('tiktok', 'reddit', 'x'),
    'channel': ('tiktok', 'instagram', 'youtube', 'reddit', 'x'),
}
KIND_HELP = {
    'video': 'Holt neue Kommentare und einen neuen Verlaufspunkt (Aufrufe, Likes) für dieses Video.',
    'hashtag': 'Findet neue Videos oder Beiträge zu diesem Hashtag und ruft sie ab.',
    'search': 'Findet neue Videos oder Beiträge zu diesem Suchbegriff und ruft sie ab. Bei Reddit holt „r/name“ die neuen Beiträge eines Subreddits.',
    'profile': 'Findet neue Videos oder Beiträge dieses Profils und ruft sie ab.',
    'channel': 'Holt alle Videos eines ganzen Kanals in kleinen Paketen und beobachtet danach neue.',
}
EXPLAIN = ('Hashtag, Suchbegriff und Profil finden neue Videos oder Beiträge (Reddit und X). '
           'Ein Link holt immer wieder neue Kommentare oder Antworten und den Verlauf der Zahlen.')
INTERVALS = [(15, '15 Minuten'), (30, '30 Minuten'), (60, '1 Stunde'), (120, '2 Stunden'), (180, '3 Stunden'),
             (360, '6 Stunden'), (720, '12 Stunden'), (1440, '1 Tag'), (2880, '2 Tage'), (10080, '1 Woche')]
STATUS_TEXT = {'ok': 'OK', 'partial': 'Teilweise', 'failed': 'Fehler', 'cancelled': 'Abgebrochen', 'running': 'Läuft …'}
STATUS_COLOR = {'ok': 'ok', 'partial': 'warn', 'failed': 'err', 'cancelled': 'muted', 'running': 'warn'}
FEED_RANGES = [('Letzte Stunde', 1), ('Letzte 24 Stunden', 24), ('Letzte 7 Tage', 24 * 7)]


# ---------- Orte ----------

def resolve_db(root):
    """Datenbankdatei wie das Programm sie benutzt: SS_DB_PATH, sonst db_path aus settings.toml, sonst data/scrape.db.

    Liest die Einstellungen wie guicore.db_path über config.load. So gilt auch die Rettung für Schlüssel, die in
    älteren Dateien versehentlich hinter [sphere_presets] stehen – sonst zeigten Übersicht und Automatik auf eine
    andere Datenbank als Befehlszeile, Dienst und Daten-Tab."""
    return guicore.db_path(root)


# ---------- Zeit ----------

def parse_utc(text):
    """UTC-Zeitstempel (auch das ältere Format 'JJJJ-MM-TT HH:MM:SS' aus CURRENT_TIMESTAMP) oder None."""
    if not text:
        return None
    for fmt in (repo._FMT, '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.datetime.strptime(text, fmt).replace(tzinfo=datetime.timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def local_text(text, now=None):
    """UTC-Zeitstempel als lokale Zeit: „14:05“ (heute), „gestern 14:05“, sonst „12.10. 14:05“."""
    moment = parse_utc(text)
    if moment is None:
        return '–'
    local = moment.astimezone()
    today = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone().date()
    if local.date() == today:
        return local.strftime('%H:%M')
    if local.date() == today - datetime.timedelta(days=1):
        return local.strftime('gestern %H:%M')
    return local.strftime('%d.%m. %H:%M')


def duration_text(seconds):
    seconds = int(abs(seconds))
    if seconds < 60:
        return 'weniger als 1 Min.'
    if seconds < 3600:
        return f'{seconds // 60} Min.'
    if seconds < 86400:
        h, m = divmod(seconds // 60, 60)
        return f'{h} Std.' + (f' {m} Min.' if m else '')
    d, rest = divmod(seconds, 86400)
    return f'{d} Tag' + ('e' if d != 1 else '') + (f' {rest // 3600} Std.' if rest // 3600 else '')


def next_text(target, now=None):
    """Spalte „Nächster Lauf“."""
    if not target.get('enabled'):
        return 'pausiert'
    moment = parse_utc(target.get('next_run_at'))
    if moment is None:
        return 'sobald möglich'
    delta = (moment - (now or repo.now())).total_seconds()
    return 'fällig' if delta <= 0 else 'in ' + duration_text(delta)


def interval_label(minutes):
    for m, label in INTERVALS:
        if m == minutes:
            return label
    if minutes % 1440 == 0:
        d = minutes // 1440
        return f'{d} Tage'
    if minutes % 60 == 0:
        return f'{minutes // 60} Stunden'
    return f'{minutes} Minuten'


def value_text(kind, platform, value):
    """Wert eines Ziels, wie man ihn schreibt: #tanzen, @name, bei Reddit u/name (Subreddits stehen schon als r/name da)."""
    if kind == 'hashtag':
        return '#' + value
    if kind == 'channel':
        from .channels import value_display
        return value_display(platform, value)
    if kind == 'profile':
        return ('u/' if platform == 'reddit' else '@') + value
    return value


def target_title(t):
    """Kurzbeschreibung eines Ziels für Meldungen: „Hashtag #tanzen (TikTok)“."""
    value = value_text(t['kind'], t['platform'], t['value'])
    if t['kind'] == 'video':
        value = re.sub(r'^https?://(www\.)?', '', value)   # der Anfang des Links sagt nichts, Platz für die Kennung
        if len(value) > 46:
            value = value[:43] + '…'
    return f"{KIND_LABELS[t['kind']]} {value} ({PLATFORM_NAMES.get(t['platform'], t['platform'])})"


def deleted_title(label):
    """Name eines gelöschten Ziels für das Laufprotokoll: aus dem Merkzettel „hashtag youtube lofi“ wird
    „Gelöschtes Ziel: Hashtag #lofi (YouTube)“ – nie das rohe Label."""
    label = (label or '').strip()
    if not label:
        return 'Gelöschtes Ziel'
    parts = label.split(' ', 2)
    if len(parts) == 3 and parts[0] in KIND_LABELS:
        return 'Gelöschtes Ziel: ' + target_title({'kind': parts[0], 'platform': parts[1], 'value': parts[2]})
    return 'Gelöschtes Ziel: ' + label


# ---------- Hintergrunddienst ----------

NOT_RUNNING = {'running': False, 'pid': None, 'started_at': None, 'last_beat': None, 'current': None,
               'stale': False, 'age_s': None}


def service_status(root):
    """Zustand des Hintergrunddienstes; legt nie Dateien an. `unavailable` heißt: Modul fehlt."""
    root = pathlib.Path(root)
    if not (root / 'data' / 'daemon.lock').exists():
        return dict(NOT_RUNNING)
    try:
        from . import daemon
    except ImportError:
        return dict(NOT_RUNNING, unavailable=True)
    try:
        return daemon.status(root)
    except Exception:
        return dict(NOT_RUNNING, error=True)


def describe_service(status, targets=None, now=None):
    """(Farbe, Haupttext, Zusatztext) für die Statuszeile."""
    if status.get('unavailable'):
        return 'muted', 'Funktion noch nicht verfügbar', 'Der Hintergrunddienst ist in dieser Installation nicht vorhanden.'
    if status.get('error'):
        return 'warn', 'Zustand unbekannt', 'Der Dienst lässt sich gerade nicht abfragen.'
    if not status.get('running'):
        return 'muted', '○ Aus', 'Automatische Abrufe laufen nur, solange der Dienst läuft.'
    cur = status.get('current')
    if status.get('stale'):
        mins = max(1, int((status.get('age_s') or 0) // 60))
        return 'warn', '● Läuft, meldet sich aber nicht mehr', f'Seit {mins} Min. kein Lebenszeichen – eventuell hängt ein Abruf. „Stoppen“ und neu starten hilft.'
    if cur:
        what = target_title({'kind': cur.get('kind', 'video'), 'platform': cur.get('platform', ''), 'value': cur.get('value', '')}) \
            if cur.get('kind') in KIND_LABELS else 'ein Ziel'
        return 'ok', '● Läuft – arbeitet gerade', f'Aktuelles Ziel: {what}'
    return 'ok', '● Läuft', next_run_summary(targets or [], now)


def next_run_summary(targets, now=None):
    active = [t for t in targets if t.get('enabled')]
    if not active:
        return 'Noch kein aktives Ziel.'
    times = [(parse_utc(t.get('next_run_at')) or repo.now(), t) for t in active]
    when, target = min(times, key=lambda x: x[0])
    delta = (when - (now or repo.now())).total_seconds()
    return f'Nächster Lauf: {target_title(target)} – ' + ('jetzt fällig' if delta <= 0 else 'in ' + duration_text(delta))


# ---------- Lesen ----------

@contextlib.contextmanager
def _read(db_path):
    path = pathlib.Path(db_path)
    if not path.is_file():
        yield None
        return
    from .util import ro_connect
    db = ro_connect(path, timeout=5)   # auch auf Netzwerkpfaden
    try:
        yield db
    finally:
        db.close()


def load_snapshot(db_path, feed_hours=24, run_limit=100):
    """Alles, was der Tab zeigt, in einem Rutsch (läuft im Hintergrund-Thread). Fehlt etwas: leere Listen."""
    out = {'targets': [], 'runs': [], 'comments': [], 'groups': [], 'accounts': [], 'error': None}
    try:
        with _read(db_path) as db:
            if db is None:
                return out
            since = repo.iso(repo.now() - datetime.timedelta(hours=feed_hours))
            for key, call in (('targets', lambda: repo.list_targets(db)),
                              ('runs', lambda: repo.list_runs(db, run_limit)),
                              ('comments', lambda: repo.new_comments_since(db, since, 300)),
                              ('groups', lambda: repo.list_groups(db)),
                              ('accounts', lambda: repo.list_accounts(db))):
                try:
                    out[key] = call()
                except sqlite3.OperationalError as error:
                    if 'no such' not in str(error).lower():
                        raise
    except sqlite3.OperationalError as error:
        text = str(error).lower()
        out['error'] = ('Die Datenbank ist gerade beschäftigt.' if 'locked' in text or 'busy' in text
                        else 'Die Datenbank lässt sich nicht lesen.')
    except sqlite3.DatabaseError:
        out['error'] = 'Die Datenbank ist beschädigt oder keine Datenbank.'
    return out


# ---------- Schreiben ----------

@contextlib.contextmanager
def _write(db_path):
    from .store import Store
    try:
        store = Store(db_path)
    except sqlite3.OperationalError as error:
        raise ValueError(_db_text(error)) from None
    except (sqlite3.DatabaseError, OSError):
        raise ValueError('Die Datenbank lässt sich nicht öffnen.') from None
    try:
        yield store.db
    except sqlite3.OperationalError as error:
        raise ValueError(_db_text(error)) from None
    finally:
        store.close()


def _db_text(error):
    text = str(error).lower()
    if 'locked' in text or 'busy' in text:
        return 'Die Datenbank ist gerade belegt (ein Abruf läuft?). Bitte gleich noch einmal versuchen.'
    return 'Die Datenbank lässt sich nicht öffnen oder beschreiben.'


def create_group(db_path, name):
    with _write(db_path) as db:
        return repo.create_group(db, name)


def check_target(kind, platform, value, interval_min, max_comments, max_videos):
    """Prüft die Eingaben des Dialogs. Gibt den bereinigten Wert zurück, sonst ValueError mit deutscher Meldung."""
    if kind not in KIND_LABELS:
        raise ValueError('Bitte eine Art wählen.')
    if platform not in PLATFORMS_FOR_KIND[kind]:
        raise ValueError('Diese Plattform passt nicht zur gewählten Art.')
    text = (value or '').strip()
    if kind == 'video':
        text = guicore.normalize_link(text)
        found = guicore.detect_platform(text) if text else None
        if text and found is None:
            raise ValueError('Das ist kein Link von TikTok, Instagram, YouTube, Reddit oder X.')
        if found and found != platform:
            raise ValueError(f'Der Link gehört zu {PLATFORM_NAMES[found]}, nicht zu {PLATFORM_NAMES[platform]}.')
    value = repo.normalize_target_value(kind, text, platform)
    try:
        interval_min = int(interval_min)
        max_comments, max_videos = int(max_comments), int(max_videos)
    except (TypeError, ValueError):
        raise ValueError('Intervall, Kommentare und Videos müssen ganze Zahlen sein.') from None
    if not 5 <= interval_min <= 60 * 24 * 30:
        raise ValueError('Das Intervall muss zwischen 5 Minuten und 30 Tagen liegen.')
    if not 0 <= max_comments <= 5000:
        raise ValueError('Kommentare pro Video: 0 bis 5000.')
    if not 1 <= max_videos <= 200:
        raise ValueError('Videos pro Lauf: 1 bis 200.')
    return {'kind': kind, 'platform': platform, 'value': value, 'interval_min': interval_min,
            'max_comments': max_comments, 'max_videos': max_videos}


def add_target(db_path, clean, account_id=None, group_id=None):
    with _write(db_path) as db:
        repo.ensure_default_accounts(db)
        return repo.add_target(db, clean['kind'], clean['platform'], clean['value'], account_id=account_id,
                               group_id=group_id, interval_min=clean['interval_min'],
                               max_comments=clean['max_comments'], max_videos=clean['max_videos'],
                               account_mode=clean.get('account_mode'))


def update_target(db_path, target_id, **fields):
    with _write(db_path) as db:
        if repo.get_target(db, target_id) is None:
            raise ValueError('Dieses Ziel gibt es nicht mehr.')
        repo.update_target(db, target_id, **fields)


def delete_target(db_path, target_id):
    with _write(db_path) as db:
        repo.delete_target(db, target_id)


def request_run_now(db_path, target_id):
    with _write(db_path) as db:
        if repo.get_target(db, target_id) is None:
            raise ValueError('Dieses Ziel gibt es nicht mehr.')
        repo.request_run_now(db, target_id)


# ---------- Ausgabe von `run-target` ----------

_RUN_LINE = re.compile(r'\s*(?:Ziel \d+|.+?): (?:fertig|teilweise fertig|fehlgeschlagen|abgebrochen) - ')


def run_target_message(code, lines):
    """(Farbe, Text) aus Exit-Code und Ausgabe des Befehls `run-target`."""
    index = next((i for i in range(len(lines) - 1, -1, -1) if _RUN_LINE.match(lines[i])), None)
    text = lines[index].strip() if index is not None else ''
    reason = next((l.strip() for l in lines[index + 1:] if l.strip()), '') if index is not None else ''
    if code == 4:
        return 'warn', 'Es läuft gerade schon ein Browserauftrag (z. B. der Hintergrunddienst). Bitte gleich noch einmal versuchen.'
    if code == 0:
        return 'ok', text or 'Das Ziel wurde abgerufen.'
    if code == 130 or code == -1:
        return 'warn', 'Abgebrochen.'
    if reason:
        return 'err', reason[:240]
    return 'err', (text + ' – Details im Protokoll.') if text else 'Der Abruf hat nicht geklappt – Details im Protokoll.'
