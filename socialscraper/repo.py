"""Verwaltungsdaten in der Datenbank: Konten, Gruppen, Ziele (Automatik) und Läufe.

Alle Funktionen bekommen eine offene sqlite3-Verbindung (z. B. `Store(...).db`) und committen selbst.
Zeiten sind UTC im Format 2026-10-11T04:15:33Z.
"""
import datetime
import json
import logging
import re
import sqlite3
from contextlib import contextmanager

log = logging.getLogger(__name__)

PLATFORMS = ('tiktok', 'instagram', 'youtube', 'reddit', 'x')
REVIEW_PLATFORMS = ('google', 'trustpilot')
ACCOUNT_PLATFORMS = PLATFORMS + REVIEW_PLATFORMS
KINDS = ('video', 'hashtag', 'search', 'profile')
TARGET_KINDS = KINDS + ('channel',)
PACE_PROFILES = ('careful', 'normal', 'fast')
SCOPES = ('all', 'latest', 'since')
BACKFILL_STATES = ('queued', 'listing', 'fetching', 'paused', 'done', 'watching')
ITEM_STATUSES = ('pending', 'running', 'done', 'failed', 'skipped')
MAX_ITEM_ATTEMPTS = 3
LOGIN_PLATFORMS = ('tiktok', 'instagram', 'reddit', 'x')   # Reddit: Anmeldung freiwillig
ACCOUNT_MODES = ('default', 'fixed', 'rotate')   # Ziel: Standardkonto der Plattform | bestimmtes Konto | Rotation
DEFAULT_COLORS = ('#2563eb', '#16a34a', '#d97706', '#dc2626', '#7c3aed', '#0891b2', '#db2777', '#65a30d')
_FMT = '%Y-%m-%dT%H:%M:%SZ'
_COLOR = re.compile(r'#[0-9a-fA-F]{6}')

@contextmanager
def atomic(db):
    """Allow legacy helpers to commit together, rolling back all writes on failure."""
    class Connection:
        def commit(self):
            pass
        def __getattr__(self, name):
            return getattr(db, name)
    db.execute('BEGIN IMMEDIATE')
    try:
        yield Connection()
        db.commit()
    except BaseException:
        db.rollback()
        raise


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(moment=None):
    return (moment or now()).strftime(_FMT)


def parse(text):
    return datetime.datetime.strptime(text, _FMT).replace(tzinfo=datetime.timezone.utc)


def _rows(db, sql, args=()):
    cur = db.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(db, sql, args=()):
    rows = _rows(db, sql, args)
    return rows[0] if rows else None


# ---------- Konten ----------

def slug(text):
    t = (text or '').lower().replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    return re.sub(r'[^a-z0-9]+', '_', t).strip('_')[:40] or 'konto'


def ensure_default_accounts(db):
    """Bestehende Einzelkonten (tiktok_main, instagram_main) werden zu „Hauptkonto“; nichts geht verloren."""
    db.execute('CREATE TABLE IF NOT EXISTS account_bootstrap (id INTEGER PRIMARY KEY CHECK(id=1))')
    first = db.execute('INSERT OR IGNORE INTO account_bootstrap (id) VALUES (1)').rowcount
    if not first:
        db.commit()
        return
    for platform in LOGIN_PLATFORMS:
        if db.execute('SELECT 1 FROM accounts WHERE platform=?', (platform,)).fetchone() is None:
            db.execute('INSERT INTO accounts (platform, label, identity, is_default, created_at) VALUES (?,?,?,1,?)',
                       (platform, 'Hauptkonto', f'{platform}_main', iso()))
    db.commit()


def list_accounts(db, platform=None):
    sql, args = 'SELECT * FROM accounts', ()
    if platform:
        sql, args = sql + ' WHERE platform=?', (platform,)
    return _rows(db, sql + ' ORDER BY platform, is_default DESC, label COLLATE NOCASE', args)


def get_account(db, account_id):
    return _one(db, 'SELECT * FROM accounts WHERE id=?', (account_id,))


def default_account(db, platform):
    return _one(db, 'SELECT * FROM accounts WHERE platform=? ORDER BY is_default DESC, id LIMIT 1', (platform,))


def add_account(db, platform, label, country='de', note=None):
    if platform not in ACCOUNT_PLATFORMS:
        raise ValueError('Diese Kontoplattform wird nicht unterstützt')
    label = (label or '').strip()
    if not label:
        raise ValueError('Bitte einen Namen für das Konto eingeben')
    if len(label) > 60:
        raise ValueError('Der Kontoname ist zu lang (höchstens 60 Zeichen)')
    base, n = f'{platform}_{slug(label)}', 1
    while True:
        identity = base if n == 1 else f'{base}_{n}'
        if db.execute('SELECT 1 FROM accounts WHERE identity=?', (identity,)).fetchone() is None:
            break
        n += 1
    first = db.execute('SELECT 1 FROM accounts WHERE platform=?', (platform,)).fetchone() is None
    cur = db.execute('INSERT INTO accounts (platform, label, identity, country, is_default, note, created_at) VALUES (?,?,?,?,?,?,?)',
                     (platform, label, identity, country.lower(), int(first), note, iso()))
    db.commit()
    return get_account(db, cur.lastrowid)


def set_default_account(db, account_id):
    acc = get_account(db, account_id)
    if acc is None:
        raise ValueError('Konto nicht gefunden')
    db.execute('UPDATE accounts SET is_default=(id=?) WHERE platform=?', (account_id, acc['platform']))
    db.commit()


def rename_account(db, account_id, label):
    label = (label or '').strip()
    if not label:
        raise ValueError('Bitte einen Namen eingeben')
    db.execute('UPDATE accounts SET label=? WHERE id=?', (label, account_id))
    db.commit()


def set_account_rotation(db, account_id, enabled):
    """„In Rotation“ ein- oder ausschalten (sofort gespeichert)."""
    if get_account(db, account_id) is None:
        raise ValueError('Konto nicht gefunden')
    db.execute('UPDATE accounts SET rotation_enabled=? WHERE id=?', (int(bool(enabled)), account_id))
    db.commit()


def set_account_daily_limit(db, account_id, limit):
    """Abrufe (Videos) pro Tag; None = unbegrenzt."""
    if get_account(db, account_id) is None:
        raise ValueError('Konto nicht gefunden')
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000):
        raise ValueError('Das Tageslimit muss eine ganze Zahl von 1 bis 100000 sein (oder leer für unbegrenzt)')
    db.execute('UPDATE accounts SET daily_limit=? WHERE id=?', (limit, account_id))
    db.commit()


def set_account_proxy_ref(db, account_id, masked):
    """Vermerkt in der Datenbank NUR die maskierte Anzeige des festen Proxys (das Passwort liegt in
    data/account_proxies.json, siehe net/accountproxy.py). None entfernt den Vermerk."""
    if get_account(db, account_id) is None:
        raise ValueError('Konto nicht gefunden')
    db.execute('UPDATE accounts SET proxy=? WHERE id=?', (masked or None, account_id))
    db.commit()


def delete_account(db, account_id):
    """Entfernt Konto und Geheimnisse; Cookies/Profil entfernt der Aufrufer über „Abmelden“."""
    acc = get_account(db, account_id)
    if acc is None:
        return
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='aktionen'").fetchone():
        db.execute("UPDATE aktionen SET status='uebersprungen', fehler='Das zugeordnete Konto wurde entfernt.' "
                   "WHERE account_id=? AND status IN ('offen','fehler','laeuft')", (account_id,))
    db.execute("UPDATE targets SET account_mode='default' WHERE account_id=? AND account_mode='fixed'", (account_id,))
    db.execute('DELETE FROM accounts WHERE id=?', (account_id,))
    if acc['is_default']:
        nxt = db.execute('SELECT id FROM accounts WHERE platform=? ORDER BY id LIMIT 1', (acc['platform'],)).fetchone()
        if nxt:
            db.execute('UPDATE accounts SET is_default=1 WHERE id=?', (nxt[0],))
    db.commit()
    if acc.get('proxy'):   # der feste Proxy gehört zum Konto: Zugangsdaten erst entfernen, wenn das Löschen gespeichert ist
        try:
            from .net import accountproxy
            path = accountproxy.db_file(db)
            if path is not None:
                accountproxy.remove_entry(path, acc['identity'])
        except Exception as error:
            log.warning('Der Proxy-Eintrag von Konto „%s“ konnte nicht entfernt werden (%s); bitte unter „Konten“ prüfen.',
                        acc['label'], type(error).__name__)
    from .net.accountproxy import db_file
    path = db_file(db)
    if path is not None:
        from . import accountvault
        try:
            accountvault.remove(path, acc['identity'])
        except Exception:
            raise ValueError('Das Konto wurde entfernt, aber seine Zugangsdaten oder Postfachzuordnung '
                             'konnten nicht bereinigt werden. Bitte account_secrets.json prüfen.') from None


# ---------- Gruppen ----------

def list_groups(db):
    return _rows(db, '''SELECT g.*, (SELECT COUNT(*) FROM group_videos gv WHERE gv.group_id=g.id) AS video_count
                        FROM groups g ORDER BY g.name COLLATE NOCASE''')


def create_group(db, name, color=None, note=None):
    name = (name or '').strip()
    if not name:
        raise ValueError('Bitte einen Gruppennamen eingeben')
    if len(name) > 60:
        raise ValueError('Der Gruppenname ist zu lang (höchstens 60 Zeichen)')
    if color is None:
        color = DEFAULT_COLORS[db.execute('SELECT COUNT(*) FROM groups').fetchone()[0] % len(DEFAULT_COLORS)]
    if not _COLOR.fullmatch(color):
        raise ValueError('Farbe muss wie #2563eb aussehen')
    try:
        cur = db.execute('INSERT INTO groups (name, color, note, created_at) VALUES (?,?,?,?)', (name, color, note, iso()))
    except sqlite3.IntegrityError:
        raise ValueError(f'Eine Gruppe „{name}“ gibt es schon') from None
    db.commit()
    return _one(db, 'SELECT * FROM groups WHERE id=?', (cur.lastrowid,))


def update_group(db, group_id, name=None, color=None, note=None):
    cur = _one(db, 'SELECT * FROM groups WHERE id=?', (group_id,))
    if cur is None:
        raise ValueError('Gruppe nicht gefunden')
    name = cur['name'] if name is None else name.strip()
    color = cur['color'] if color is None else color
    if not name:
        raise ValueError('Bitte einen Gruppennamen eingeben')
    if not _COLOR.fullmatch(color):
        raise ValueError('Farbe muss wie #2563eb aussehen')
    try:
        db.execute('UPDATE groups SET name=?, color=?, note=? WHERE id=?',
                   (name, color, cur['note'] if note is None else note, group_id))
    except sqlite3.IntegrityError:
        raise ValueError(f'Eine Gruppe „{name}“ gibt es schon') from None
    db.commit()


def delete_group(db, group_id):
    db.execute('DELETE FROM group_videos WHERE group_id=?', (group_id,))
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='group_people'").fetchone():
        db.execute('DELETE FROM group_people WHERE group_id=?', (group_id,))
    db.execute('DELETE FROM groups WHERE id=?', (group_id,))
    db.commit()


def add_videos_to_group(db, group_id, video_uids):
    ts = iso()
    db.executemany('INSERT OR IGNORE INTO group_videos (group_id, video_uid, added_at) VALUES (?,?,?)',
                   [(group_id, uid, ts) for uid in video_uids])
    db.commit()


def remove_videos_from_group(db, group_id, video_uids):
    db.executemany('DELETE FROM group_videos WHERE group_id=? AND video_uid=?', [(group_id, u) for u in video_uids])
    db.commit()


def groups_of_video(db, video_uid):
    return _rows(db, 'SELECT g.* FROM groups g JOIN group_videos gv ON gv.group_id=g.id WHERE gv.video_uid=? ORDER BY g.name', (video_uid,))


def group_stats(db):
    """Je Gruppe: Videos, gespeicherte Kommentare, Aufrufe/Likes (Summe der letzten bekannten Werte) sowie die Zahl der
    zugeordneten Ersteller (`creators`) und Kommentarschreiber (`commenters`)."""
    rows = _group_stats(db)
    people = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='group_people'").fetchone():
        for gid, role, n in db.execute('SELECT group_id, role, COUNT(*) FROM group_people GROUP BY group_id, role'):
            people.setdefault(gid, {})[role] = n
    for row in rows:
        row['creators'] = people.get(row['id'], {}).get('creator', 0)
        row['commenters'] = people.get(row['id'], {}).get('commenter', 0)
    return rows


def _group_stats(db):
    return _rows(db, '''
        SELECT g.id, g.name, g.color, COUNT(v.uid) AS videos,
               COALESCE(SUM(v.view_count),0) AS views, COALESCE(SUM(v.like_count),0) AS likes,
               AVG(v.duration_s) AS avg_duration, MIN(v.duration_s) AS min_duration, MAX(v.duration_s) AS max_duration,
               (SELECT COUNT(*) FROM comments c JOIN group_videos gv2 ON gv2.video_uid = c.platform || ':' || c.video_id
                 WHERE gv2.group_id=g.id) AS comments
        FROM groups g LEFT JOIN group_videos gv ON gv.group_id=g.id LEFT JOIN videos v ON v.uid=gv.video_uid
        GROUP BY g.id ORDER BY g.name COLLATE NOCASE''')


# ---------- Ziele (Automatik) ----------

def normalize_target_value(kind, value, platform=None):
    """Bereinigt den Wert eines Ziels. `platform` macht die Prüfung genauer (Reddit: `u/name`, `r/name`; X: Kontoname)."""
    value = (value or '').strip()
    if platform == 'reddit' and kind == 'profile':
        from .platformlinks import reddit_user
        user = reddit_user(value)
        if not user:
            raise ValueError('Ungültiger Reddit-Benutzername (3 bis 20 Zeichen: Buchstaben, Zahlen, _ und -; z. B. u/name)')
        return user
    if platform == 'reddit' and kind == 'search' and re.match(r'/?r/', value, re.I):
        from .platformlinks import reddit_subreddit
        sub = reddit_subreddit(value)
        if not sub:
            raise ValueError('Ungültiges Subreddit (z. B. r/python: 2 bis 21 Zeichen aus Buchstaben, Zahlen und _)')
        return f'r/{sub}'
    if platform == 'x' and kind == 'profile':
        from .platformlinks import x_handle
        handle = x_handle(value)
        if not handle:
            raise ValueError('Ungültiger X-Kontoname (1 bis 15 Zeichen: Buchstaben, Zahlen und _)')
        return handle
    if kind == 'hashtag':
        value = value.lstrip('#').strip().lower()
        if not re.fullmatch(r'\w{1,100}', value):
            raise ValueError('Ein Hashtag besteht nur aus Buchstaben, Zahlen und _ (ohne Leerzeichen)')
    elif kind == 'profile':
        value = value.lstrip('@').strip()
        if not re.fullmatch(r'[A-Za-z0-9._]{1,64}', value):
            raise ValueError('Ungültiger Benutzername')
    elif kind == 'search':
        if not 2 <= len(value) <= 200:
            raise ValueError('Der Suchbegriff muss 2 bis 200 Zeichen lang sein')
    elif kind == 'video':
        if not value.startswith(('http://', 'https://')):
            raise ValueError('Bitte einen vollständigen Video-Link eingeben')
    else:
        raise ValueError('Unbekannte Zielart')
    return value


def _check_interval(minutes):
    if not 5 <= int(minutes) <= 60 * 24 * 30:
        raise ValueError('Das Intervall muss zwischen 5 Minuten und 30 Tagen liegen')


def _mode_for(account_mode, account_id):
    """Kontomodus eines Ziels: ausdrücklich gewählt oder aus `account_id` abgeleitet (gesetzt = fixed)."""
    if account_mode is None:
        return 'fixed' if account_id else 'default'
    if account_mode not in ACCOUNT_MODES:
        raise ValueError('Unbekannter Kontomodus')
    if account_mode == 'fixed' and not account_id:
        raise ValueError('Bitte ein Konto wählen')
    return account_mode


def add_target(db, kind, platform, value, *, account_id=None, group_id=None, interval_min=360,
               max_comments=20, max_videos=20, enabled=True, account_mode=None):
    if kind not in KINDS:
        raise ValueError('Unbekannte Zielart')
    if platform not in PLATFORMS:
        raise ValueError('Unbekannte Plattform')
    if kind == 'profile' and platform not in ('tiktok', 'reddit', 'x'):
        raise ValueError('Profile lassen sich nur für TikTok, Reddit und X verfolgen')
    if kind == 'hashtag' and platform == 'reddit':
        raise ValueError('Hashtags werden für Reddit nicht unterstützt')
    if kind == 'hashtag' and platform == 'instagram':
        raise ValueError('Hashtags werden für Instagram nicht unterstützt')
    if kind == 'search' and platform == 'instagram':
        raise ValueError('Die Suche wird für Instagram nicht unterstützt')
    _check_interval(interval_min)
    if not 0 <= int(max_comments) <= 5000 or not 1 <= int(max_videos) <= 200:
        raise ValueError('Kommentare 0–5000, Videos 1–200')
    value = normalize_target_value(kind, value, platform)
    account_mode = _mode_for(account_mode, account_id)
    if account_mode != 'fixed':
        account_id = None
    try:
        cur = db.execute('''INSERT INTO targets (kind, platform, value, account_id, account_mode, group_id, interval_min,
                            max_comments, max_videos, enabled, next_run_at, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (kind, platform, value, account_id, account_mode, group_id, int(interval_min), int(max_comments),
                          int(max_videos), int(bool(enabled)), iso(), iso()))
    except sqlite3.IntegrityError as error:
        raise ValueError(_integrity_message(error, 'Dieses Ziel gibt es schon')) from None
    db.commit()
    return get_target(db, cur.lastrowid)


def _integrity_message(error, duplicate):
    """Eine IntegrityError ist meist ein Doppel (UNIQUE), kann aber auch ein inzwischen gelöschtes Konto oder eine
    gelöschte Gruppe sein (FOREIGN KEY) – dann die passende Meldung statt „gibt es schon“."""
    if 'FOREIGN KEY' in str(error).upper():
        return 'Das gewählte Konto oder die Gruppe gibt es nicht mehr. Bitte die Liste neu laden.'
    return duplicate


def get_target(db, target_id):
    return _one(db, 'SELECT * FROM targets WHERE id=?', (target_id,))


def list_targets(db):
    return _rows(db, '''SELECT t.*, a.label AS account_label, g.name AS group_name FROM targets t
                        LEFT JOIN accounts a ON a.id=t.account_id LEFT JOIN groups g ON g.id=t.group_id
                        ORDER BY t.kind, t.platform, t.value COLLATE NOCASE''')


_TARGET_EDITABLE = ('account_id', 'account_mode', 'group_id', 'interval_min', 'max_comments', 'max_videos', 'enabled')


def update_target(db, target_id, **fields):
    for key in fields:
        if key not in _TARGET_EDITABLE:
            raise ValueError(f'Feld nicht änderbar: {key}')
    if 'interval_min' in fields:
        _check_interval(fields['interval_min'])
    if 'account_mode' in fields or 'account_id' in fields:   # Modus und Konto bleiben zusammenpassend
        if 'account_id' not in fields and fields.get('account_mode') == 'fixed':
            current = get_target(db, target_id)
            fields['account_id'] = current['account_id'] if current else None
        fields['account_mode'] = _mode_for(fields.get('account_mode'), fields.get('account_id'))
        if fields['account_mode'] != 'fixed':
            fields['account_id'] = None
    if fields:
        sets = ', '.join(f'{k}=?' for k in fields)
        try:
            db.execute(f'UPDATE targets SET {sets} WHERE id=?', [*fields.values(), target_id])
        except sqlite3.IntegrityError as error:
            raise ValueError(_integrity_message(error, 'Diese Änderung ist in der Datenbank nicht erlaubt.')) from None
        if fields.get('enabled'):
            db.execute('UPDATE targets SET next_run_at=? WHERE id=? AND (next_run_at IS NULL OR next_run_at>?)',
                       (iso(), target_id, iso()))
        db.commit()


# ---------- Kanäle (ganze Kanäle abrufen) ----------

_CHANNEL_EDITABLE = ('account_id', 'account_mode', 'group_id', 'interval_min', 'max_comments', 'enabled', 'pace_profile',
                     'quiet_hours', 'observe', 'backfill_state', 'pause_until', 'pause_reason', 'resume_state', 'list_cursor',
                     'list_done', 'total_found', 'platform_total', 'list_note', 'last_listed_at', 'display_name',
                     'next_run_at', 'force_once', 'include_shorts', 'only_video', 'owner_as_creator', 'scope', 'scope_value',
                     'last_status', 'last_error')


def check_quiet_hours(text):
    """„23-7“ (von Uhr bis Uhr, Ortszeit) -> (23, 7); leer -> None. Ungültiges wirft ValueError."""
    text = (text or '').strip()
    if not text:
        return None
    match = re.fullmatch(r'(\d{1,2})\s*-\s*(\d{1,2})', text)
    if not match or not (0 <= int(match.group(1)) <= 23 and 0 <= int(match.group(2)) <= 23) \
            or match.group(1) == match.group(2) or int(match.group(1)) == int(match.group(2)):
        raise ValueError('Ruhezeit: bitte von-bis in Uhr angeben, zum Beispiel 23-7 (Stunden von 0 bis 23, Anfang und Ende verschieden)')
    return int(match.group(1)), int(match.group(2))


def add_channel(db, platform, value, *, display_name=None, scope='all', scope_value=None, max_comments=0, group_id=None,
                account_id=None, account_mode=None, pace_profile='normal', interval_min=360, observe=True, quiet_hours=None,
                include_shorts=True, only_video=False, list_sort='new', owner_as_creator=False, platform_total=None,
                enabled=True):
    """Legt einen Kanal an: ein Ziel der Art „channel“. `value` ist der bereinigte Kanalwert aus channels.parse_channel_link."""
    if platform not in PLATFORMS:
        raise ValueError('Unbekannte Plattform')
    if scope not in SCOPES:
        raise ValueError('Unbekannter Umfang')
    if pace_profile not in PACE_PROFILES:
        raise ValueError('Unbekanntes Tempo-Profil')
    if list_sort not in ('new', 'top'):
        raise ValueError('Unbekannte Sortierung')
    if scope == 'latest':
        try:
            scope_value = str(int(scope_value))
        except (TypeError, ValueError):
            raise ValueError('Bitte die Zahl der neuesten Videos angeben') from None
        if not 1 <= int(scope_value) <= 100000:
            raise ValueError('Die Zahl der neuesten Videos muss zwischen 1 und 100000 liegen')
    elif scope == 'since':
        try:
            datetime.date.fromisoformat(str(scope_value or '').strip())
        except ValueError:
            raise ValueError('Bitte ein Datum wie 2026-01-31 angeben') from None
        scope_value = str(scope_value).strip()
    else:
        scope_value = None
    _check_interval(interval_min)
    if not 0 <= int(max_comments) <= 5000:
        raise ValueError('Kommentare 0–5000')
    check_quiet_hours(quiet_hours)
    value = (value or '').strip()
    if not value:
        raise ValueError('Kanal fehlt')
    account_mode = _mode_for(account_mode, account_id)
    if account_mode != 'fixed':
        account_id = None
    try:
        cur = db.execute('''INSERT INTO targets (kind, platform, value, account_id, account_mode, group_id, interval_min,
                            max_comments, max_videos, enabled, next_run_at, created_at, pace_profile, scope, scope_value,
                            backfill_state, quiet_hours, observe, include_shorts, only_video, list_sort, owner_as_creator,
                            display_name, platform_total)
                            VALUES ('channel',?,?,?,?,?,?,?,20,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?)''',
                         (platform, value, account_id, account_mode, group_id, int(interval_min), int(max_comments),
                          int(bool(enabled)), iso(), iso(), pace_profile, scope, scope_value,
                          (quiet_hours or '').strip() or None, int(bool(observe)), int(bool(include_shorts)),
                          int(bool(only_video)), list_sort, int(bool(owner_as_creator)), display_name, platform_total))
    except sqlite3.IntegrityError as error:
        raise ValueError(_integrity_message(error, 'Diesen Kanal gibt es schon')) from None
    db.commit()
    return get_target(db, cur.lastrowid)


def update_channel(db, target_id, **fields):
    for key in fields:
        if key not in _CHANNEL_EDITABLE:
            raise ValueError(f'Feld nicht änderbar: {key}')
    if 'pace_profile' in fields and fields['pace_profile'] not in PACE_PROFILES:
        raise ValueError('Unbekanntes Tempo-Profil')
    if 'backfill_state' in fields and fields['backfill_state'] not in BACKFILL_STATES:
        raise ValueError('Unbekannter Zustand')
    if 'quiet_hours' in fields:
        check_quiet_hours(fields['quiet_hours'])
        fields['quiet_hours'] = (fields['quiet_hours'] or '').strip() or None
    if 'interval_min' in fields:
        _check_interval(fields['interval_min'])
    if 'account_mode' in fields or 'account_id' in fields:
        if 'account_id' not in fields and fields.get('account_mode') == 'fixed':
            current = get_target(db, target_id)
            fields['account_id'] = current['account_id'] if current else None
        fields['account_mode'] = _mode_for(fields.get('account_mode'), fields.get('account_id'))
        if fields['account_mode'] != 'fixed':
            fields['account_id'] = None
    if fields:
        sets = ', '.join(f'{k}=?' for k in fields)
        try:
            db.execute(f"UPDATE targets SET {sets} WHERE id=? AND kind='channel'", [*fields.values(), target_id])
        except sqlite3.IntegrityError as error:
            raise ValueError(_integrity_message(error, 'Diese Änderung ist in der Datenbank nicht erlaubt.')) from None
        db.commit()


def list_channels(db):
    """Alle Kanäle mit Fortschritt (`progress`: pending, running, done, failed, skipped, total)."""
    rows = _rows(db, '''SELECT t.*, a.label AS account_label, g.name AS group_name FROM targets t
                        LEFT JOIN accounts a ON a.id=t.account_id LEFT JOIN groups g ON g.id=t.group_id
                        WHERE t.kind='channel' ORDER BY t.platform, t.value COLLATE NOCASE''')
    counts = {}
    for target_id, status, n in db.execute('SELECT target_id, status, COUNT(*) FROM channel_items GROUP BY target_id, status'):
        counts.setdefault(target_id, {})[status] = n
    for row in rows:
        row['progress'] = progress_from(counts.get(row['id'], {}))
    return rows


def progress_from(counts):
    out = {s: int(counts.get(s, 0)) for s in ITEM_STATUSES}
    out['total'] = sum(out.values())
    out['finished'] = out['done'] + out['skipped']
    return out


def channel_progress(db, target_id):
    return progress_from(dict(db.execute('SELECT status, COUNT(*) FROM channel_items WHERE target_id=? GROUP BY status',
                                         (target_id,)).fetchall()))


def add_channel_items(db, target_id, platform, items):
    """Einträge in die Warteschlange (doppelte Links werden übersprungen). Gibt die Zahl neuer Einträge zurück.
    `items`: Wörterbücher mit url, uid, published_at, title, duration_s und optional status."""
    before = db.execute('SELECT COUNT(*) FROM channel_items WHERE target_id=?', (target_id,)).fetchone()[0]
    stamp = iso()
    db.executemany('''INSERT OR IGNORE INTO channel_items (target_id, platform, video_url, video_uid, status, discovered_at,
                      published_at, title, duration_s) VALUES (?,?,?,?,?,?,?,?,?)''',
                   [(target_id, platform, i['url'], i.get('uid'), i.get('status') or 'pending', stamp, i.get('published_at'),
                     (i.get('title') or None) and str(i['title'])[:300], i.get('duration_s')) for i in items])
    after = db.execute('SELECT COUNT(*) FROM channel_items WHERE target_id=?', (target_id,)).fetchone()[0]
    db.execute('UPDATE targets SET total_found=? WHERE id=?', (after, target_id))
    db.commit()
    return after - before


def list_channel_items(db, target_id, status=None, limit=500):
    sql, args = 'SELECT * FROM channel_items WHERE target_id=?', [target_id]
    if status:
        sql += ' AND status=?'
        args.append(status)
    sql += ' ORDER BY COALESCE(published_at, \'\') DESC, id LIMIT ?'
    return _rows(db, sql + '', [*args, int(limit)])


def pending_channel_items(db, target_id, limit):
    """Die nächsten Videos (neueste zuerst, dann in der Reihenfolge der Entdeckung), die noch abgerufen werden müssen."""
    return _rows(db, '''SELECT * FROM channel_items WHERE target_id=? AND status='pending'
                        ORDER BY (published_at IS NULL), published_at DESC, id LIMIT ?''', (target_id, int(limit)))


def set_channel_item(db, item_id, status, *, error=None, attempt=False):
    if status not in ITEM_STATUSES:
        raise ValueError('Unbekannter Status')
    stamp = iso()
    sets = ['status=?', 'last_error=?']
    args = [status, (error or None) and str(error)[:300]]
    if attempt:
        sets += ['attempts=attempts+1', 'tried_at=?']
        args.append(stamp)
    if status in ('done', 'failed', 'skipped'):
        sets.append('done_at=?')
        args.append(stamp)
    db.execute(f'UPDATE channel_items SET {", ".join(sets)} WHERE id=?', [*args, item_id])
    db.commit()


def recover_channel_items(db):
    """Nach Absturz/Neustart: Einträge, die auf „running“ stehen geblieben sind, kommen zurück in die Warteschlange."""
    cur = db.execute("UPDATE channel_items SET status='pending' WHERE status='running'")
    db.commit()
    return cur.rowcount


def requeue_failed(db, target_id):
    """„Fehlgeschlagene erneut“: setzt die Versuche zurück. Gibt die Zahl zurück."""
    cur = db.execute("UPDATE channel_items SET status='pending', attempts=0, done_at=NULL WHERE target_id=? AND status='failed'",
                     (target_id,))
    if cur.rowcount:
        db.execute("UPDATE targets SET backfill_state=CASE WHEN backfill_state IN ('done','watching') THEN 'fetching' "
                   "ELSE backfill_state END, enabled=1 WHERE id=? AND kind='channel'", (target_id,))
    db.commit()
    return cur.rowcount


def tried_since(db, platform, since_iso):
    """Zahl der Kanal-Abrufversuche dieser Plattform seit `since_iso` (für die Tagesgrenze)."""
    return db.execute('SELECT COUNT(*) FROM channel_items WHERE platform=? AND tried_at>=?', (platform, since_iso)).fetchone()[0]


def delete_target(db, target_id):
    db.execute('DELETE FROM channel_items WHERE target_id=?', (target_id,))
    db.execute('DELETE FROM targets WHERE id=?', (target_id,))
    db.commit()


def due_targets(db, moment=None):
    """Aktive Ziele, deren Zeit gekommen ist, die am längsten Wartenden zuerst."""
    return _rows(db, 'SELECT * FROM targets WHERE enabled=1 AND (next_run_at IS NULL OR next_run_at<=?) '
                     'ORDER BY COALESCE(next_run_at, created_at)', (iso(moment),))


def request_run_now(db, target_id):
    db.execute('UPDATE targets SET next_run_at=? WHERE id=?', (iso(), target_id))
    db.commit()


# ---------- Läufe ----------

def start_run(db, target_id=None, label=None, source='manual'):
    cur = db.execute('INSERT INTO runs (target_id, label, source, started_at, status) VALUES (?,?,?,?,?)',
                     (target_id, label, source, iso(), 'running'))
    db.commit()
    return cur.lastrowid


def finish_run(db, run_id, status, *, videos_seen=0, videos_new=0, comments_new=0, error=None, authors_new=0,
               accounts_used=None):
    if status not in ('ok', 'partial', 'failed', 'cancelled'):
        raise ValueError('Unbekannter Laufstatus')
    error = error[:500] if error else None
    run = _one(db, 'SELECT * FROM runs WHERE id=?', (run_id,))
    if accounts_used is not None and not isinstance(accounts_used, str):
        accounts_used = json.dumps([str(x) for x in accounts_used], ensure_ascii=False)
    db.execute('UPDATE runs SET finished_at=?, status=?, videos_seen=?, videos_new=?, comments_new=?, error=?, authors_new=?, '
               'accounts_used=? WHERE id=?',
               (iso(), status, videos_seen, videos_new, comments_new, error, authors_new, accounts_used or None, run_id))
    if run and run['target_id']:
        t = get_target(db, run['target_id'])
        if t:
            # Nach Fehlern früher erneut versuchen (höchstens nach 30 Minuten), sonst nach dem Intervall.
            minutes = t['interval_min'] if status in ('ok', 'partial') else min(t['interval_min'], 30)
            db.execute('UPDATE targets SET last_run_at=?, next_run_at=?, last_status=?, last_error=? WHERE id=?',
                       (iso(), iso(now() + datetime.timedelta(minutes=minutes)), status, error, t['id']))
    db.commit()


def accounts_used_list(text):
    """Die in `runs.accounts_used` vermerkten Kontonamen als Liste (leer, wenn nichts vermerkt ist)."""
    if not text:
        return []
    try:
        data = json.loads(text)
        return [str(x) for x in data] if isinstance(data, list) else [str(data)]
    except ValueError:
        return [str(text)]


def recover_stale_runs(db, max_age_min=180):
    """Läufe, die „running“ stehen geblieben sind (Absturz, PC aus), als fehlgeschlagen markieren."""
    limit = iso(now() - datetime.timedelta(minutes=max_age_min))
    db.execute("UPDATE runs SET status='failed', finished_at=?, error='Unterbrochen (Programm beendet)' "
               "WHERE status='running' AND started_at<=?", (iso(), limit))
    db.commit()


def trim_runs(db, keep=5000):
    """Kürzt `runs` auf die letzten `keep` Zeilen (laufende Läufe bleiben). Gibt die Zahl gelöschter Zeilen zurück."""
    cur = db.execute("DELETE FROM runs WHERE status<>'running' AND id NOT IN "
                     "(SELECT id FROM runs ORDER BY id DESC LIMIT ?)", (int(keep),))
    db.commit()
    return cur.rowcount


def list_runs(db, limit=100):
    return _rows(db, '''SELECT r.*, t.kind AS target_kind, t.value AS target_value, t.platform AS target_platform
                        FROM runs r LEFT JOIN targets t ON t.id=r.target_id ORDER BY r.started_at DESC, r.id DESC LIMIT ?''',
                 (int(limit),))


# ---------- Verlauf (für Anzeige, API und Sync) ----------

def video_history(db, uid):
    return _rows(db, 'SELECT captured_at, view_count, like_count, comment_count, share_count FROM video_snapshots '
                     'WHERE video_uid=? ORDER BY captured_at', (uid,))


def new_comments_since(db, since_iso, limit=500):
    """Neue Kommentare seit `since_iso`; jede Zeile hat `author_new`: der Autor wurde vor diesem Zeitpunkt noch nie gesehen."""
    rows = _rows(db, '''SELECT c.*, v.url AS video_url, v.author AS video_author FROM comments c
                        LEFT JOIN videos v ON v.uid = c.platform || ':' || c.video_id
                        WHERE substr(replace(c.first_seen_at,' ','T'),1,19)>=?
                        ORDER BY substr(replace(c.first_seen_at,' ','T'),1,19) DESC, c.first_seen_at DESC LIMIT ?''',
                (str(since_iso or '').replace(' ', 'T')[:19], int(limit)))
    from . import people_repo
    first = people_repo.first_seen_map(db, 'commenter', [(r['platform'], people_repo.author_key(r.get('author_id'), r.get('author'))) for r in rows])
    cut = (since_iso or '').replace(' ', 'T')[:19]
    for r in rows:
        seen = first.get((r['platform'], people_repo.author_key(r.get('author_id'), r.get('author'))))
        r['author_new'] = bool(seen) and seen.replace(' ', 'T')[:19] >= cut
    return rows
