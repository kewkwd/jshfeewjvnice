"""Ganze Kanäle abrufen (Backfill + Beobachtung), ohne Fenster und ohne Netz testbar.

Ein Kanal ist ein Ziel der Art `channel` (Tabelle `targets`) mit einer Warteschlange `channel_items`. Der Ablauf:

    1. Listen sammeln (billig): Adapter liefern Link, Datum, Titel, Länge -> `channel_items` (pending).
    2. Videos abrufen (teuer): in kleinen Paketen, neueste zuerst, mit Pausen, Tages- und Ruhezeit-Grenzen.
    3. Danach „Beobachte neue Videos“: in jedem Intervall nur die neuesten Listen prüfen.

Die Tempo-Profile begrenzen Pakete, Abrufhäufigkeit und Tagesmengen. CAPTCHA, Sperren und 429 führen zu Pausen.
Diese Ablaufsteuerung ist kein Nachweis für geringere Erkennung und verhindert keine Sperre.
"""
import collections
import datetime
import logging
import math
import random
import re
from urllib.parse import unquote, urlsplit

from . import repo
from .platforms import PLATFORM_NAMES
from .util import matches_host

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- Tempo-Profile (einzige Stelle für die Zahlen)

PROFILES = {
    'careful': {
        'name': 'Vorsichtig', 'batch': 8, 'per_account_day': 60, 'platform_day': 150,
        'pause': (8.0, 25.0), 'break_chance': 0.25, 'break_range': (20.0, 60.0), 'gap_min': (45, 90),
        'text': 'höchstens 8 Videos pro Lauf, 60 pro Tag und Konto, Pausen von 8 bis 25 Sekunden mit gelegentlichen '
                'Lesepausen, etwa alle 45 bis 90 Minuten ein Lauf',
    },
    'normal': {
        'name': 'Normal', 'batch': 20, 'per_account_day': 200, 'platform_day': 500,
        'pause': (2.0, 9.0), 'break_chance': 0.08, 'break_range': (12.0, 30.0), 'gap_min': (20, 45),
        'text': 'höchstens 20 Videos pro Lauf, 200 pro Tag und Konto, Pausen von 2 bis 9 Sekunden, etwa alle 20 bis '
                '45 Minuten ein Lauf',
    },
    'fast': {
        'name': 'Schnell', 'batch': 50, 'per_account_day': 600, 'platform_day': 1500,
        'pause': (1.0, 4.0), 'break_chance': 0.04, 'break_range': (8.0, 20.0), 'gap_min': (8, 15),
        'text': 'höchstens 50 Videos pro Lauf, 600 pro Tag und Konto, Pausen von 1 bis 4 Sekunden, etwa alle 8 bis '
                '15 Minuten ein Lauf – mehr Abrufe in kürzerer Zeit',
    },
}
DEFAULT_PROFILE = 'normal'
PROFILE_NAMES = {key: p['name'] for key, p in PROFILES.items()}
SKIP_KNOWN_DAYS = 7              # schon gespeicherte Videos, die jünger abgerufen wurden, werden nicht noch einmal geholt
DEFAULT_SUBREDDIT_LIMIT = 500    # „alle“ bei einem Subreddit heißt nie unbegrenzt
WATCH_LIST_LIMIT = 60            # beim Beobachten werden nur die neuesten so vielen Einträge der Liste geprüft
WATCH_STOP_AFTER_KNOWN = 5       # …und beim fünften bekannten Eintrag in Folge aufgehört
LIST_LIMITS = {'reddit': 1000, 'x': 3200}   # was die Plattform höchstens zeigt (ungefähr)
BASE_SECONDS_PER_VIDEO = 10.0    # Annahme für die Dauer eines Abrufs ohne Kommentare (nur für die Schätzung)
DEFAULT_PAUSE_HOURS = {'captcha': 6, 'block': 6, 'login': 6, 'ratelimit': 1, 'proxy': 0.5}



def pace_bounds(profile):
    """Pausen-Angaben des Profils für `humanize.pace`."""
    p = PROFILES.get(profile) or PROFILES[DEFAULT_PROFILE]
    return {'low': p['pause'][0], 'high': p['pause'][1], 'break_chance': p['break_chance'], 'break_range': p['break_range']}


# ---------------------------------------------------------------- Kanal-Links erkennen

ChannelRef = collections.namedtuple('ChannelRef', 'platform kind value display url')

_YT_PATHS = (
    (re.compile(r'/(@[\w.\-%]{3,60})(?:/(?:videos|shorts|streams|featured|playlists|about|community|live))?/?', re.U), 'handle'),
    (re.compile(r'/channel/(UC[0-9A-Za-z_-]{22})(?:/(?:videos|shorts|streams|featured|playlists|about))?/?'), 'id'),
    (re.compile(r'/c/([\w.\-%]{1,100})(?:/(?:videos|shorts|streams|featured|playlists|about))?/?', re.U), 'custom'),
    (re.compile(r'/user/([\w.\-%]{1,100})(?:/(?:videos|shorts|streams|featured|playlists|about))?/?', re.U), 'user'),
)
_TT_USER = re.compile(r'/@([A-Za-z0-9_.]{2,24})/?')
_IG_RESERVED = {'p', 'reel', 'reels', 'tv', 'explore', 'accounts', 'stories', 'direct', 'about', 'developer', 'legal',
                'privacy', 'web', 'api', 'challenge', 'directory', 'press', 'oauth', 'static', 'emails', 'login', 'nametag'}
_IG_USER = re.compile(r'/([A-Za-z0-9._]{1,30})(?:/(?:reels|tagged|videos)?)?/?')
_X_RESERVED = {'home', 'explore', 'search', 'i', 'settings', 'messages', 'notifications', 'hashtag', 'intent', 'share', 'login',
               'compose', 'tos', 'privacy', 'signup', 'logout', 'account', 'download', 'jobs', 'about', 'help'}
_X_USER = re.compile(r'/([A-Za-z0-9_]{1,15})(?:/(?:with_replies|media|videos|highlights|articles))?/?')
_REDDIT_USER = re.compile(r'/(?:u|user)/([A-Za-z0-9_-]{3,20})(?:/(?:submitted|posts|overview|comments|about)?)?/?', re.I)
_REDDIT_SUB = re.compile(r'/r/([A-Za-z0-9_]{2,21})(?:/(?:new|top|hot|rising|controversial|about))?/?', re.I)


def _clean(text):
    from .guicore import normalize_link
    return normalize_link(text)


def parse_channel_link(text, platform_hint=None):
    """Erkennt Plattform und Kanal in einem eingefügten Link (Host wird echt geprüft, Lookalikes werden abgelehnt).

    Gibt ChannelRef(platform, kind, value, display, url) zurück; `value` ist die Form, in der der Kanal gespeichert
    wird. Wirft ValueError mit einem deutschen Satz, wenn der Link kein Kanal-/Profil-Link ist."""
    from .guicore import detect_platform
    raw = _clean(text)
    if not raw:
        raise ValueError('Bitte den Link zu einem Kanal oder Profil einfügen.')
    if platform_hint and not re.match(r'^[a-z]+://', raw) and not raw.startswith('www.'):
        raw = _from_handle(raw, platform_hint)
    platform = detect_platform(raw)
    if platform is None:
        raise ValueError('Das ist kein Link von YouTube, TikTok, Instagram, Reddit oder X. Bitte den Link aus der '
                         'Adresszeile des Browsers einfügen.')
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise ValueError('Der Link ist ungültig.') from None
    path = parts.path or '/'
    host = (parts.hostname or '').lower()
    if platform == 'youtube':
        return _youtube(raw, host, path, parts)
    if platform == 'tiktok':
        if host in ('vm.tiktok.com', 'vt.tiktok.com') or host.startswith(('vm.', 'vt.')):
            raise ValueError('Das ist ein TikTok-Kurzlink. Bitte den Link zum Profil verwenden (tiktok.com/@name).')
        if re.fullmatch(r'/@[^/]+/(?:video|photo)/\d+/?', path):
            raise ValueError('Das ist der Link zu einem einzelnen Video. Für ein Video nimm den Tab „Abrufen“; '
                             'für den ganzen Kanal den Profil-Link (tiktok.com/@name).')
        m = _TT_USER.fullmatch(path)
        if not m:
            raise ValueError('Das ist kein TikTok-Profil-Link. Erwartet: tiktok.com/@name')
        name = m.group(1).lower()
        return ChannelRef('tiktok', 'profile', name, '@' + name, f'https://www.tiktok.com/@{name}')
    if platform == 'instagram':
        first = [p for p in path.split('/') if p]
        if first and first[0].lower() in _IG_RESERVED:
            raise ValueError('Das ist kein Instagram-Profil-Link (ein einzelner Beitrag? Dann bitte den Tab „Abrufen“ nehmen). '
                             'Erwartet: instagram.com/name')
        m = _IG_USER.fullmatch(path)
        if not m or m.group(1).lower() in _IG_RESERVED:
            raise ValueError('Das ist kein Instagram-Profil-Link. Erwartet: instagram.com/name')
        name = m.group(1).lower()
        return ChannelRef('instagram', 'profile', name, '@' + name, f'https://www.instagram.com/{name}/')
    if platform == 'reddit':
        if host == 'redd.it' or host.endswith('.redd.it'):
            raise ValueError('Das ist ein Reddit-Kurzlink zu einem Beitrag. Bitte den Link zum Benutzer (reddit.com/user/name) '
                             'oder Subreddit (reddit.com/r/name) verwenden.')
        if '/comments/' in path or '/gallery/' in path:
            raise ValueError('Das ist der Link zu einem einzelnen Beitrag. Für einen Beitrag nimm den Tab „Abrufen“; '
                             'für alle Beiträge den Link zum Benutzer (reddit.com/user/name) oder Subreddit (reddit.com/r/name).')
        m = _REDDIT_USER.fullmatch(path)
        if m:
            name = m.group(1).lower()
            return ChannelRef('reddit', 'user', f'u/{name}', f'u/{name}', f'https://www.reddit.com/user/{name}/')
        m = _REDDIT_SUB.fullmatch(path)
        if m:
            name = m.group(1).lower()
            return ChannelRef('reddit', 'subreddit', f'r/{name}', f'r/{name}', f'https://www.reddit.com/r/{name}/')
        raise ValueError('Das ist kein Reddit-Benutzer- oder Subreddit-Link. Erwartet: reddit.com/user/name oder reddit.com/r/name')
    # X
    if host == 't.co':
        raise ValueError('Das ist ein t.co-Kurzlink; er wird nicht verfolgt. Bitte den Link zum Profil verwenden (x.com/name).')
    if re.search(r'/status(?:es)?/\d', path):
        raise ValueError('Das ist der Link zu einem einzelnen Beitrag. Für einen Beitrag nimm den Tab „Abrufen“; '
                         'für das ganze Profil x.com/name.')
    m = _X_USER.fullmatch(path)
    if not m or m.group(1).lower() in _X_RESERVED:
        raise ValueError('Das ist kein X-Profil-Link. Erwartet: x.com/name')
    name = m.group(1).lower()
    return ChannelRef('x', 'profile', name, '@' + name, f'https://x.com/{name}')


def _from_handle(text, platform):
    """Eingabe ohne Link (nur für ausdrücklich gewählte Plattform): @name, u/name, r/name."""
    text = text.strip()
    if platform == 'reddit' and re.match(r'/?[ur]/', text, re.I):
        return 'https://www.reddit.com/' + text.lstrip('/')
    name = text.lstrip('@')
    base = {'youtube': 'https://www.youtube.com/@', 'tiktok': 'https://www.tiktok.com/@', 'instagram': 'https://www.instagram.com/',
            'x': 'https://x.com/', 'reddit': 'https://www.reddit.com/user/'}.get(platform)
    if base is None:
        raise ValueError('Unbekannte Plattform')
    return base + name


def _youtube(raw, host, path, parts):
    if host == 'youtu.be' or host.endswith('.youtu.be') or path in ('/watch', '/playlist', '/results') or path.startswith(
            ('/shorts/', '/live/', '/embed/', '/clip/')):
        raise ValueError('Das ist der Link zu einem einzelnen Video oder einer Wiedergabeliste, nicht zu einem Kanal. '
                         'Für ein Video nimm den Tab „Abrufen“. Kanal-Links sehen so aus: youtube.com/@name oder '
                         'youtube.com/channel/UC…')
    for pattern, kind in _YT_PATHS:
        m = pattern.fullmatch(path)
        if not m:
            continue
        name = m.group(1)
        if kind == 'handle':
            handle = unquote(name).lower()
            return ChannelRef('youtube', 'channel', f'https://www.youtube.com/{handle}', handle,
                              f'https://www.youtube.com/{handle}')
        prefix = {'id': 'channel', 'custom': 'c', 'user': 'user'}[kind]
        url = f'https://www.youtube.com/{prefix}/{name}'
        return ChannelRef('youtube', 'channel', url, name if kind != 'id' else name, url)
    raise ValueError('Das ist kein YouTube-Kanal-Link. Erwartet: youtube.com/@name, youtube.com/channel/UC…, '
                     'youtube.com/c/name oder youtube.com/user/name')


def value_display(platform, value):
    """Anzeigename eines Kanalwerts: @handle, u/name, r/name."""
    if platform == 'youtube':
        tail = str(value).rstrip('/').split('youtube.com/', 1)[-1]
        return tail if tail.startswith('@') else tail.split('/', 1)[-1]
    if platform in ('tiktok', 'instagram', 'x'):
        return '@' + str(value)
    return str(value)


def channel_url(platform, value):
    return {'youtube': lambda v: str(v), 'tiktok': lambda v: f'https://www.tiktok.com/@{v}',
            'instagram': lambda v: f'https://www.instagram.com/{v}/', 'x': lambda v: f'https://x.com/{v}',
            'reddit': lambda v: f'https://www.reddit.com/{"user" if str(v).startswith("u/") else "r"}/{str(v)[2:]}/'}[platform](value)


# ---------------------------------------------------------------- Ruhezeiten, Jitter, Tageszeiten

def _local(moment):
    return moment.astimezone()


def _at_local(day, hour=0):
    """Der Moment (UTC), an dem am Ortstag `day` die volle Stunde `hour` beginnt.

    Gerechnet über eine naive Ortszeit, die `astimezone()` mit dem Versatz auflöst, der an genau diesem Tag gilt
    (Sommer-/Winterzeit). `moment.astimezone()` liefert dagegen eine Zone mit festem Versatz – nach `replace(hour=…)`
    oder `+ 1 Tag` stimmt der am Tag der Zeitumstellung nicht mehr (Ruhezeit-Ende und Tagesgrenze eine Stunde daneben)."""
    return datetime.datetime.combine(day, datetime.time(hour)).astimezone(datetime.timezone.utc)


def in_quiet(quiet, now=None):
    """Liegt `now` in der Ruhezeit (Text wie „23-7“, Ortszeit)? Ohne Ruhezeit immer False."""
    hours = repo.check_quiet_hours(quiet)
    if hours is None:
        return False
    start, end = hours
    h = _local(now or repo.now()).hour
    return (start <= h < end) if start < end else (h >= start or h < end)


def quiet_end(quiet, now=None):
    """Der Moment (UTC), an dem die Ruhezeit endet (volle Stunde, Ortszeit)."""
    start, end = repo.check_quiet_hours(quiet)
    now = now or repo.now()
    local = _local(now)
    candidate = _at_local(local.date(), end)
    if candidate <= local:
        candidate = _at_local(local.date() + datetime.timedelta(days=1), end)
    return candidate


def quiet_hours_count(quiet):
    hours = repo.check_quiet_hours(quiet)
    if hours is None:
        return 0
    start, end = hours
    return (end - start) % 24


def quiet_text(quiet):
    hours = repo.check_quiet_hours(quiet)
    return 'keine' if hours is None else f'{hours[0]}:00 bis {hours[1]}:00 Uhr'


def jitter_minutes(minutes, rng=random, spread=0.15):
    """Intervall mit Zufall (±15 %), damit Läufe nicht jede Stunde auf die Minute kommen; mindestens 5 Minuten."""
    return max(5.0, minutes * (1 + rng.uniform(-spread, spread)))


def gap_minutes(profile, rng=random):
    low, high = (PROFILES.get(profile) or PROFILES[DEFAULT_PROFILE])['gap_min']
    return rng.uniform(low, high)


def local_day_start(now=None):
    """Beginn des heutigen Tages (Ortszeit) als UTC-Zeitstempel-Text."""
    return repo.iso(_at_local(_local(now or repo.now()).date()))


def next_midnight(now=None):
    return _at_local(_local(now or repo.now()).date() + datetime.timedelta(days=1))


def fmt_when(moment, now=None):
    from . import rotation
    return rotation.fmt_when(moment, now)


# ---------------------------------------------------------------- Zeitschätzung

def estimate(count, platform, profile=DEFAULT_PROFILE, *, accounts=1, comments=0, quiet_hours=None, already=0):
    """Ehrliche Schätzung, wie lange das Abrufen von `count` Videos dauert.

    Formel (keine erfundene Genauigkeit):
        pro Tag = min(Tageslimit je Konto × Zahl der Konten, Tageslimit der Plattform, Läufe pro Tag × Paketgröße)
        Läufe pro Tag = aktive Stunden × 60 ÷ mittlerer Abstand der Läufe
        Tage = (Videos − schon gespeicherte) ÷ pro Tag
    Ohne Konten (YouTube, Reddit ohne Konto) zählt eine Verbindung. Gemessen ist nichts davon: es sind die Grenzen, die
    das Programm sich selbst setzt. Plattformen können früher bremsen oder später."""
    p = PROFILES[profile]
    notes = []
    limit = LIST_LIMITS.get(platform)
    n = max(0, int(count) - max(0, int(already)))
    if limit and n > limit:
        notes.append(f'{PLATFORM_NAMES.get(platform, platform)} zeigt höchstens etwa {limit:,} Einträge'.replace(',', '.')
                     + ' – mehr lassen sich nicht holen.')
        n = limit
    usable = max(1, int(accounts)) if platform in repo.LOGIN_PLATFORMS else 1
    per_day = min(p['per_account_day'] * usable, p['platform_day'])
    active_h = max(1.0, 24 - quiet_hours_count(quiet_hours))
    runs_per_day = active_h * 60.0 / ((p['gap_min'][0] + p['gap_min'][1]) / 2.0)
    run_cap = max(1, int(runs_per_day * p['batch']))
    effective = max(1, min(per_day, run_cap))
    days = n / effective if n else 0.0
    if n == 0:
        text = 'Es ist nichts abzurufen.'
    elif days <= 1.0:
        hours = max(1, math.ceil(days * active_h))
        text = f'etwa {hours} {"Stunde" if hours == 1 else "Stunden"}'
    else:
        d = math.ceil(days)
        text = f'rund {d} Tage'
    formula = (f'{n:,} Videos ÷ {effective:,} pro Tag'.replace(',', '.') +
               f' (Tageslimit {p["per_account_day"]} je Konto'
               + (f' × {usable} Konten, höchstens {p["platform_day"]} je Plattform' if usable > 1 else '')
               + f'; {runs_per_day:.0f} Läufe à {p["batch"]} Videos möglich)')
    if days > 14 and n:
        notes.append(f'Bei {n:,} Videos und {effective} pro Tag dauert das {text}. Schnelleres Tempo erhöht das Risiko einer '
                     'Sperre; mehr Konten (Rotation) verteilen die Last.'.replace(',', '.'))
    if comments and comments > 0:
        notes.append('Kommentare machen jedes Video langsamer; die Tagesgrenze zählt Videos und bleibt gleich.')
    if comments and comments > 0:
        notes.append('Die KI-Stimmung kostet zusätzlich Geld – die Kostenschätzung findest du unter Einstellungen → KI / Stimmung.')
    return {'videos': n, 'per_day': effective, 'days': days, 'text': text, 'formula': formula, 'notes': notes,
            'profile': profile, 'batch': p['batch']}


# ---------------------------------------------------------------- Anlegen / Prüfen

def normalize_options(ref, scope='all', scope_value=None):
    """Bei einem Subreddit heißt „alle“ nie unbegrenzt: Standard „neueste 500“ (Plattform-Listen enden bei etwa 1.000)."""
    notes = []
    if ref.platform == 'reddit' and ref.kind == 'subreddit' and scope == 'all':
        scope, scope_value = 'latest', str(DEFAULT_SUBREDDIT_LIMIT)
        notes.append(f'Bei einem Subreddit werden standardmäßig die neuesten {DEFAULT_SUBREDDIT_LIMIT} Beiträge geholt '
                     '(Reddit zeigt ohnehin höchstens etwa 1.000).')
    return scope, scope_value, notes


def create_channel(db, ref, *, scope='all', scope_value=None, comments=0, group_id=None, account_mode='default',
                   account_id=None, pace_profile=DEFAULT_PROFILE, interval_min=360, observe=True, quiet_hours=None,
                   include_shorts=True, only_video=False, list_sort='new', owner_as_creator=False, display_name=None,
                   platform_total=None):
    scope, scope_value, notes = normalize_options(ref, scope, scope_value)
    if ref.kind == 'subreddit':
        owner_as_creator = False        # in einem Subreddit schreiben viele: dort gibt es keinen „Inhaber“
    if group_id is None and owner_as_creator:
        raise ValueError('„Kanal-Inhaber auch als Ersteller“ braucht eine Gruppe.')
    repo.ensure_default_accounts(db)
    target = repo.add_channel(db, ref.platform, ref.value, display_name=display_name or ref.display, scope=scope,
                              scope_value=scope_value, max_comments=comments, group_id=group_id, account_id=account_id,
                              account_mode=account_mode, pace_profile=pace_profile, interval_min=interval_min, observe=observe,
                              quiet_hours=quiet_hours, include_shorts=include_shorts, only_video=only_video,
                              list_sort=list_sort, owner_as_creator=owner_as_creator, platform_total=platform_total)
    target['notes'] = notes
    return target


def accounts_for(db, platform, mode, account_id):
    """Konten, die dieser Kanal benutzen kann (für Tageslimit und Schätzung)."""
    if platform not in repo.LOGIN_PLATFORMS:
        return []
    accounts = repo.list_accounts(db, platform)
    if mode == 'rotate':
        return [a for a in accounts if a.get('rotation_enabled', 1)]
    if mode == 'fixed':
        return [a for a in accounts if a['id'] == account_id]
    default = [a for a in accounts if a['is_default']] or accounts[:1]
    return default


# ---------------------------------------------------------------- Warteschlange füllen

def uid_for_url(platform, url):
    """`platform:id` eines Video-Links (wie in der Tabelle videos) oder None."""
    from .guicore import canonical_video
    key, _ = canonical_video(url)
    return key if key.startswith(platform + ':') else None


def fill_queue(db, target, items, *, now=None):
    """Neue Listeneinträge in die Warteschlange. Schon gespeicherte Videos (zuletzt vor weniger als SKIP_KNOWN_DAYS Tagen
    abgerufen) werden als „übersprungen“ vermerkt und gehören trotzdem in die Gruppe. Gibt (neu, übersprungen) zurück."""
    now = now or repo.now()
    since = repo.iso(now - datetime.timedelta(days=SKIP_KNOWN_DAYS))
    rows, skipped_uids = [], []
    for item in items:
        uid = item.get('uid') or uid_for_url(target['platform'], item['url'])
        row = dict(item, uid=uid)
        if uid:
            known = db.execute('SELECT fetched_at FROM videos WHERE uid=?', (uid,)).fetchone()
            if known and _stamp_utc(known[0]) >= since:
                row['status'] = 'skipped'
                skipped_uids.append(uid)
        rows.append(row)
    new = repo.add_channel_items(db, target['id'], target['platform'], rows)
    if skipped_uids and target.get('group_id'):
        repo.add_videos_to_group(db, target['group_id'], skipped_uids)
    skipped = sum(1 for r in rows if r.get('status') == 'skipped')
    return new, skipped


def _stamp_utc(text):
    """Zeitstempel (…Z oder 'JJJJ-MM-TT HH:MM:SS') als Text im Format …Z zum Vergleichen."""
    text = (text or '').replace(' ', 'T')[:19]
    return text + 'Z' if text else ''


# ---------------------------------------------------------------- Tagesgrenze und Paketgröße

def allowance(db, target, now=None, root=None):
    """Wie viele Videos dürfen jetzt abgerufen werden? Gibt {'batch', 'reason', 'until'} zurück; batch 0 = nicht jetzt."""
    from . import rotation
    now = now or repo.now()
    p = PROFILES.get(target.get('pace_profile') or DEFAULT_PROFILE) or PROFILES[DEFAULT_PROFILE]
    platform = target['platform']
    day_start = local_day_start(now)
    tried = repo.tried_since(db, platform, day_start)
    platform_left = p['platform_day'] - tried
    mode = rotation_mode(target)
    accounts = accounts_for(db, platform, mode, target.get('account_id'))
    if accounts:
        left = 0
        for account in accounts:
            used = rotation.used_today(account, now)
            room = p['per_account_day'] - used
            if account.get('daily_limit'):
                room = min(room, int(account['daily_limit']) - used)
            left += max(0, room)
        account_left = left
        if mode != 'rotate':
            account_left = left
    else:
        account_left = p['per_account_day'] - tried
    batch = min(p['batch'], platform_left, account_left)
    if batch <= 0:
        which = 'Plattform' if platform_left <= 0 else 'Konto'
        return {'batch': 0, 'reason': f'Tagesgrenze erreicht ({which}); morgen geht es weiter', 'until': next_midnight(now)}
    return {'batch': int(batch), 'reason': None, 'until': None}


def rotation_mode(target):
    mode = target.get('account_mode')
    if mode in repo.ACCOUNT_MODES:
        return mode
    return 'fixed' if target.get('account_id') else 'default'


def account_room(account, profile, now=None):
    """Wie viele Abrufe dieses Konto heute nach dem Tempo-Profil noch darf (0 = ausgeschöpft)."""
    from . import rotation
    p = PROFILES.get(profile) or PROFILES[DEFAULT_PROFILE]
    used = rotation.used_today(account, now or repo.now())
    room = p['per_account_day'] - used
    if account.get('daily_limit'):
        room = min(room, int(account['daily_limit']) - used)
    return max(0, room)


# ---------------------------------------------------------------- Planung (wann der nächste Lauf ist)

def gate(db, target, now=None, rng=random):
    """Vor einem Lauf: darf der Kanal jetzt arbeiten? None = ja. Sonst (Text, Zeitpunkt UTC) – der Lauf entfällt und
    `next_run_at` ist schon gesetzt. „Jetzt weiter“ (`force_once`) überspringt Pause und Ruhezeit, nie die Tagesgrenze."""
    now = now or repo.now()
    forced = bool(target.get('force_once'))
    pause = _parse(target.get('pause_until'))
    if not forced and target.get('backfill_state') == 'paused' and pause and pause > now:
        return _wait(db, target, pause, f'pausiert bis {fmt_when(pause, now)}')
    if not forced and in_quiet(target.get('quiet_hours'), now):
        until = quiet_end(target['quiet_hours'], now) + datetime.timedelta(minutes=rng.uniform(2, 40))
        return _wait(db, target, until, f'Ruhezeit bis {fmt_when(until, now)}')
    if target.get('backfill_state') in ('fetching', 'watching', 'listing', 'queued', 'paused'):
        progress = repo.channel_progress(db, target['id'])
        if progress['pending'] and allowance(db, target, now)['batch'] <= 0 and target.get('list_done'):
            until = next_midnight(now) + datetime.timedelta(minutes=rng.uniform(5, 90))
            return _wait(db, target, until, f'Tagesgrenze erreicht; weiter {fmt_when(until, now)}')
    return None


def _wait(db, target, until, text):
    repo.update_channel(db, target['id'], next_run_at=repo.iso(until))
    return text, until


def _parse(text):
    try:
        return repo.parse(text) if text else None
    except ValueError:
        return None


def reschedule(db, target_id, status, *, rng=random, now=None, wait_until=None):
    """Nach einem Lauf: nächster Zeitpunkt mit Zufall; Zustand weiterschalten (fertig / beobachten)."""
    now = now or repo.now()
    t = repo.get_target(db, target_id)
    if t is None:
        return None
    progress = repo.channel_progress(db, target_id)
    state = t.get('backfill_state') or 'queued'
    fields = {'force_once': 0}
    if state == 'paused':
        # Eine spätere Neuplanung darf eine gespeicherte Schutzpause nicht verkürzen.
        waits = [until for until in (_parse(t.get('pause_until')), wait_until)
                 if until is not None and until > now]
        nxt = max(waits, default=now + datetime.timedelta(hours=1))
    else:
        complete = bool(t.get('list_done')) and progress['pending'] == 0 and progress['running'] == 0
        if complete and state in ('queued', 'listing', 'fetching', 'watching'):
            if t.get('observe'):
                fields['backfill_state'] = 'watching'
                nxt = now + datetime.timedelta(minutes=jitter_minutes(t['interval_min'], rng))
            else:
                fields['backfill_state'] = 'done'
                fields['enabled'] = 0
                nxt = now + datetime.timedelta(days=3650)
        elif progress['pending'] or not t.get('list_done'):
            nxt = wait_until or (now + datetime.timedelta(minutes=gap_minutes(t.get('pace_profile') or DEFAULT_PROFILE, rng)))
            if status == 'failed' and not wait_until:
                nxt = now + datetime.timedelta(minutes=max(30, gap_minutes(t.get('pace_profile') or DEFAULT_PROFILE, rng)))
        else:
            nxt = now + datetime.timedelta(minutes=jitter_minutes(t['interval_min'], rng))
    if wait_until and fields.get('backfill_state') not in ('done',):
        nxt = max(nxt, wait_until)
    if in_quiet(t.get('quiet_hours'), nxt) and 'enabled' not in fields:
        nxt = quiet_end(t['quiet_hours'], nxt) + datetime.timedelta(minutes=rng.uniform(2, 40))
    fields['next_run_at'] = repo.iso(nxt)
    repo.update_channel(db, target_id, **fields)
    return nxt


def pause_channel(db, target_id, until, reason, now=None):
    """Pause setzen oder verlängern; gibt den wirksamen Termin zurück. Verkürzen nur über `resume_channel`."""
    now = now or repo.now()
    t = repo.get_target(db, target_id)
    resume = t.get('resume_state') if t.get('backfill_state') == 'paused' else t.get('backfill_state')
    old = _parse(t.get('pause_until')) if t.get('backfill_state') == 'paused' else None
    if old is not None and old > now and old >= until:
        until, reason = old, t.get('pause_reason') or reason
    repo.update_channel(db, target_id, backfill_state='paused', pause_until=repo.iso(until), pause_reason=(reason or '')[:300],
                        resume_state=resume or 'fetching', next_run_at=repo.iso(until))
    return until


def resume_channel(db, target_id, *, now=None, force=True):
    """Pause beenden („Fortsetzen“ / „Jetzt weiter“): zurück in den Zustand vor der Pause, Lauf sofort fällig."""
    t = repo.get_target(db, target_id)
    if t is None:
        return
    state = t.get('backfill_state')
    if state == 'paused':
        state = t.get('resume_state') or ('fetching' if t.get('list_done') else 'listing')
    if state == 'done':
        state = 'fetching'
    repo.update_channel(db, target_id, backfill_state=state or 'queued', pause_until=None, pause_reason=None, resume_state=None,
                        enabled=1, next_run_at=repo.iso(now), force_once=int(bool(force)))


def pause_for_fault(db, target, kind, until=None, now=None, rng=random):
    """Pausenzeitpunkt nach CAPTCHA/Sperre/429/abgelaufener Anmeldung: der längere von `until` (Konto-Abkühlung) und der Standardzeit."""
    now = now or repo.now()
    hours = DEFAULT_PAUSE_HOURS.get(kind, 1)
    base = now + datetime.timedelta(hours=hours * rng.uniform(1.0, 1.25))
    return max(base, until) if until else base


# ---------------------------------------------------------------- Anzeige

def state_text(target, now=None):
    """Zustand in einfachen Worten: „Holt die Videoliste“, „Ruft Videos ab“, „Beobachte neue Videos“ …"""
    now = now or repo.now()
    state = target.get('backfill_state') or 'queued'
    if not target.get('enabled') and state != 'done':
        return 'angehalten (von dir)'
    if state == 'paused':
        until = _parse(target.get('pause_until'))
        when = f' bis {fmt_when(until, now)}' if until else ''
        reason = f' wegen {target["pause_reason"]}' if target.get('pause_reason') else ''
        return f'pausiert{when}{reason}'
    return {'queued': 'wartet auf den ersten Lauf', 'listing': 'holt die Videoliste', 'fetching': 'ruft Videos ab',
            'done': 'fertig', 'watching': 'beobachtet neue Videos'}.get(state, state)


def progress_parts(target, now=None):
    """Der Stand in drei Teilen (für Tabellen mit eigenen Spalten): (Zahlen, Zustand, nächster Schritt).

    Zahlen: „412 von 1.380 · 3 fehlgeschlagen“; Zustand: „ruft Videos ab“ usw.; nächster Schritt: „jetzt“, „14:30“ … oder ''."""
    now = now or repo.now()
    pr = target.get('progress') or {}
    total = pr.get('total', 0)
    count = f'{_n(pr.get("finished", 0))} von {_n(total)}' if total else 'noch keine Videos gefunden'
    if pr.get('failed'):
        count += f' · {pr["failed"]} fehlgeschlagen'
    state = target.get('backfill_state')
    nxt = _parse(target.get('next_run_at'))
    when = ''
    if target.get('enabled') and state not in ('done',) and nxt:
        when = 'jetzt' if nxt <= now else fmt_when(nxt, now)
    return count, state_text(target, now), when


def progress_line(target, now=None):
    """„412 von 1.380 · 3 fehlgeschlagen · nächster Schritt 14:30 · pausiert bis … wegen CAPTCHA“"""
    count, state, when = progress_parts(target, now)
    return ' · '.join([count] + ([f'nächster Schritt {when}'] if when else []) + [state])


def _n(value):
    return f'{int(value):,}'.replace(',', '.')


def bar(finished, total, width=20):
    if not total:
        return '□' * width
    filled = int(round(width * min(1.0, finished / total)))
    return '■' * filled + '□' * (width - filled)


def honest_note(platform, found, platform_total, exhausted, scope):
    """Satz zur Vollständigkeit der Liste – nie „alle“, wenn die Plattform die Liste begrenzt."""
    if scope != 'all':
        return f'{_n(found)} Videos nach deiner Auswahl gefunden.'
    if platform_total and found < platform_total:
        return (f'{_n(found)} von {_n(platform_total)} bekannten Videos gefunden; die Plattform zeigt nicht mehr.'
                if exhausted else f'{_n(found)} von {_n(platform_total)} bekannten Videos gefunden; die Liste ist noch nicht zu Ende.')
    limit = LIST_LIMITS.get(platform)
    if limit and found >= limit * 0.9:
        return f'{_n(found)} Beiträge gefunden. {PLATFORM_NAMES.get(platform, platform)} zeigt höchstens etwa {_n(limit)} – ältere sind nicht erreichbar.'
    if platform in ('tiktok', 'instagram', 'x', 'reddit') and exhausted:
        return (f'{_n(found)} Videos gefunden. Mehr zeigt die Plattform nicht; ob das wirklich alle sind, '
                f'lässt sich nicht beweisen.')
    if exhausted:
        return f'{_n(found)} Videos gefunden (das Ende der Liste ist erreicht).'
    return f'{_n(found)} Videos bisher gefunden; die Liste ist noch nicht zu Ende.'


# ---------------------------------------------------------------- Ein Lauf für einen Kanal (vom Planer benutzt)

class ChannelRun:
    """Der Zustand eines Laufs für einen Kanal: Liste holen, nächstes Paket wählen, Ergebnisse je Video festhalten,
    am Ende Pause oder nächsten Zeitpunkt bestimmen. Der Planer (scheduler.py) ruft die Methoden an festen Stellen auf."""

    def __init__(self, db, target, cfg=None, *, rng=random, clock=None):
        self.db, self.target, self.cfg, self.rng = db, target, cfg, rng
        self.clock = clock or repo.now
        self.profile = target.get('pace_profile') or DEFAULT_PROFILE
        self.pace = pace_bounds(self.profile)
        self.rows = {}
        self.rot = None
        self.wait_until = None
        self.wait_reason = None
        self.listed_new = 0
        self.stop_fault = None
        self.owner_done = False
        # Reste eines abgebrochenen Laufs zurück in die Warteschlange; eine abgelaufene Pause endet
        self.db.execute("UPDATE channel_items SET status='pending' WHERE target_id=? AND status='running'", (target['id'],))
        self.db.commit()
        if target.get('backfill_state') == 'paused':
            resume = target.get('resume_state') or ('fetching' if target.get('list_done') else 'listing')
            repo.update_channel(db, target['id'], backfill_state=resume, pause_until=None, pause_reason=None, resume_state=None)
            self.target = repo.get_target(db, target['id'])

    # -- Liste --

    def _fresh(self):
        self.target = repo.get_target(self.db, self.target['id']) or self.target
        return self.target

    def _on_items(self, items, cursor):
        new, _skipped = fill_queue(self.db, self.target, items, now=self.clock())
        self.listed_new += new
        if cursor is not None:
            repo.update_channel(self.db, self.target['id'], list_cursor=str(cursor))

    def list_options(self, cancelled, watching):
        from .channel_list import ListOptions, parse_utc
        t = self.target
        opts = ListOptions(include_shorts=bool(t.get('include_shorts', 1)), only_video=bool(t.get('only_video')),
                           sort=t.get('list_sort') or 'new', on_items=self._on_items, cancelled=cancelled, rng=self.rng)
        if watching:
            opts.limit, opts.stop_after_known = WATCH_LIST_LIMIT, WATCH_STOP_AFTER_KNOWN
            opts.known = frozenset(r[0] for r in self.db.execute('SELECT video_url FROM channel_items WHERE target_id=?', (t['id'],)))
            return opts
        opts.cursor = t.get('list_cursor')
        scope = t.get('scope') or 'all'
        have = repo.channel_progress(self.db, t['id'])['total']
        if scope == 'latest':
            opts.limit = max(0, int(t.get('scope_value') or 0) - have)
        elif scope == 'since':
            opts.since = parse_utc(str(t.get('scope_value')) + 'T00:00:00Z')
        return opts

    def need_listing(self):
        t = self._fresh()
        if not t.get('list_done'):
            return True
        return (t.get('backfill_state') == 'watching') and bool(t.get('observe'))

    def list_stage(self, ctx, cancelled=None):
        """Holt die Videoliste (Backfill oder Beobachten) und füllt die Warteschlange. Fehler laufen weiter nach oben (der
        Planer wechselt dann das Konto oder pausiert); was schon gefunden wurde, ist gespeichert."""
        from . import discovery
        t = self._fresh()
        watching = bool(t.get('list_done'))
        opts = self.list_options(cancelled, watching)
        if not watching:
            repo.update_channel(self.db, t['id'], backfill_state='listing')
        result = None if opts.limit == 0 else discovery.list_channel(t['platform'], t['value'], ctx, opts)
        t = self._fresh()
        stamp = repo.iso(self.clock())
        if watching:
            repo.update_channel(self.db, t['id'], last_listed_at=stamp)
            return result
        scope = t.get('scope') or 'all'
        complete = result is None or result.exhausted or result.reached_known or (
            scope == 'latest' and opts.limit is not None and result.found >= opts.limit)
        found = repo.channel_progress(self.db, t['id'])['total']
        fields = {'last_listed_at': stamp}
        if result is not None:
            if result.platform_total:
                fields['platform_total'] = int(result.platform_total)
            if result.name and (not t.get('display_name') or t['display_name'] == value_display(t['platform'], t['value'])):
                fields['display_name'] = result.name      # der echte Name des Kanals statt nur der Kennung
            note = honest_note(t['platform'], found, fields.get('platform_total') or t.get('platform_total'), result.exhausted, scope)
            fields['list_note'] = note + (' ' + result.note if result.note and result.exhausted else '')
            fields['list_cursor'] = None if complete else result.cursor
        else:
            fields['list_note'] = honest_note(t['platform'], found, t.get('platform_total'), True, scope)
        if complete:
            fields.update(list_done=1, backfill_state='fetching')
        repo.update_channel(self.db, t['id'], **fields)
        return result

    # -- Paket --

    def pending_urls(self):
        t = self._fresh()
        allow = allowance(self.db, t, self.clock())
        if allow['batch'] <= 0:
            self.wait_until = allow['until'] + datetime.timedelta(minutes=self.rng.uniform(5, 90))
            self.wait_reason = allow['reason']
            return []
        rows = repo.pending_channel_items(self.db, t['id'], allow['batch'])
        for row in rows:
            self.rows[row['video_url']] = row
            repo.set_channel_item(self.db, row['id'], 'running')
        if rows and t.get('backfill_state') in ('queued', 'listing'):
            repo.update_channel(self.db, t['id'], backfill_state='fetching')
        return [r['video_url'] for r in rows]

    def stage(self, ctx, cancelled=None):
        """Alles vor dem Abruf: Liste (falls nötig) und das nächste Paket. Gibt die Links des Pakets zurück."""
        if self.need_listing():
            self.list_stage(ctx, cancelled)
        if cancelled is not None and cancelled():
            return []
        return self.pending_urls()

    def item_result(self, url, outcome, error=None):
        """outcome: 'done' | 'failed' | None (nicht bearbeitet: bleibt in der Warteschlange)."""
        from . import rotation
        row = self.rows.get(url)
        if row is None:
            return
        if outcome == 'done':
            repo.set_channel_item(self.db, row['id'], 'done', attempt=True)
        elif outcome == 'failed':
            if error and rotation.classify(error) in rotation.COOLDOWN:      # Fehler des Kontos/der Verbindung, nicht des Videos
                repo.set_channel_item(self.db, row['id'], 'pending', error=error)
                return
            attempts = int(row.get('attempts') or 0) + 1
            row['attempts'] = attempts
            repo.set_channel_item(self.db, row['id'], 'failed' if attempts >= repo.MAX_ITEM_ATTEMPTS else 'pending',
                                  error=error or 'Abruf fehlgeschlagen', attempt=True)
        else:
            repo.set_channel_item(self.db, row['id'], 'pending')

    # -- Konten --

    def account_full(self, account):
        row = repo.get_account(self.db, account['id']) or account
        return account_room(row, self.profile, self.clock()) <= 0

    def exclude_account(self, rot):
        """Konto hat seine Tagesgrenze erreicht: in dieser Rotation nicht mehr benutzen."""
        ids = rot.allowed if rot.allowed is not None else {a['id'] for a in repo.list_accounts(self.db, rot.platform)}
        rot.allowed = set(ids) - {rot.current['id']}

    # -- Ergebnis --

    def on_saved(self, video):
        """Auf ausdrücklichen Wunsch (Häkchen im Dialog): der Inhaber des Kanals auch als Ersteller in die Gruppe."""
        t = self.target
        if self.owner_done or not t.get('owner_as_creator') or not t.get('group_id'):
            return
        if t['platform'] == 'reddit' and str(t['value']).startswith('r/'):
            return
        name = (video.author or '').lstrip('@').casefold()
        owner = value_display(t['platform'], t['value']).lstrip('@').casefold()
        if t['platform'] == 'youtube' or name == owner or name == str(t['value']).removeprefix('u/').casefold():
            from . import people_repo
            people_repo.add_people(self.db, t['group_id'], 'creator',
                                   [people_repo.person(video.platform, video.author_id, video.author)])
            self.owner_done = True

    def finish(self, status, error, rot=None):
        """Nach dem Lauf: Reste zurück in die Warteschlange, Pause bei Konto-/Sperrproblemen, nächster Zeitpunkt."""
        from . import rotation
        t = self._fresh()
        self.db.execute("UPDATE channel_items SET status='pending' WHERE target_id=? AND status='running'", (t['id'],))
        self.db.commit()
        now = self.clock()
        fault = self.stop_fault
        if rot is not None and rot.failures:
            fault = fault or rot.failures[-1][1]
        if not fault and status in ('failed', 'partial') and error:
            fault = rotation.classify(error)
        if fault in rotation.COOLDOWN and status in ('failed', 'partial'):
            until = self._availability(t, rot, now)
            if until is not None:                       # kein Konto frei: der Kanal wartet, kein Weiterhämmern
                if until <= now:
                    until = pause_for_fault(self.db, t, fault, None, now, self.rng)
                until = pause_channel(self.db, t['id'], until, rotation.COOLDOWN[fault][0], now=now)
                log.info('Kanal %s pausiert bis %s (%s)', t['value'], repo.iso(until), rotation.COOLDOWN[fault][0])
                repo.update_channel(self.db, t['id'], last_status=status, force_once=0)
                return until
        return reschedule(self.db, t['id'], status, rng=self.rng, now=now, wait_until=self.wait_until)

    def _availability(self, t, rot, now):
        """Wann ist wieder ein Konto frei? None = es ist eins bereit (dann muss der Kanal nicht pausieren)."""
        from . import rotation
        platform = t['platform']
        mode = rotation_mode(t)
        if platform not in repo.LOGIN_PLATFORMS:     # YouTube: keine Konten, nur die Verbindung selbst
            return now
        if mode == 'rotate':
            if rotation.pick_account(self.db, platform, mode='rotate', now=now) is not None:
                return None
            _msg, until = rotation.no_account_message(self.db, platform, now)
            return until or now
        account = repo.get_account(self.db, t.get('account_id')) if mode == 'fixed' else repo.default_account(self.db, platform)
        until = rotation._parse((account or {}).get('cooldown_until'))
        return until if until and until > now else now
