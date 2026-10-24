"""Logik der Tabs „Konten“, „Gespeicherte Daten“ und „Abrufen“ ohne Tk (damit sie sich ohne Fenster testen lässt).

Schreibzugriffe laufen über `Store` + `repo`, Lesen über die schreibgeschützte Verbindung aus `guicore`.
Fehler werden als `ValueError` mit verständlichem deutschem Text gemeldet; `friendly_error` macht aus allem
anderen (gesperrte Datenbank, Dateifehler) ebenfalls einen verständlichen Satz.
"""
import contextlib
import datetime
import pathlib
import re
import sqlite3

from .. import guicore as core
from .. import people_repo, repo, rotation
from .. import sentiment_repo as srepo
from ..store import Store
from ..util import DEFAULT_LONG_S, DEFAULT_SHORT_S, duration_class, format_duration

COUNTRIES = [('de', 'Deutschland'), ('at', 'Österreich'), ('ch', 'Schweiz'), ('us', 'USA'), ('gb', 'Großbritannien'),
             ('fr', 'Frankreich'), ('es', 'Spanien'), ('it', 'Italien'), ('nl', 'Niederlande'), ('pl', 'Polen'),
             ('tr', 'Türkei')]

INTERVALS = [('Alle 30 Minuten', 30), ('Jede Stunde', 60), ('Alle 3 Stunden', 180), ('Alle 6 Stunden', 360),
             ('Alle 12 Stunden', 720), ('Einmal am Tag', 1440), ('Einmal pro Woche', 10080)]
DEFAULT_INTERVAL = 360

# Spalte in der Videoliste -> SQL-Ausdruck zum Sortieren (nur diese Namen sind erlaubt)
SORT_SQL = {
    'platform': 'v.platform', 'author': 'v.author COLLATE NOCASE', 'caption': 'v.caption COLLATE NOCASE',
    'views': 'CAST(v.view_count AS INTEGER)', 'likes': 'CAST(v.like_count AS INTEGER)',
    'comments': 'CAST(v.comment_count AS INTEGER)', 'date': 'v.created_at', 'fetched': 'v.fetched_at',
    'groups': 'group_names COLLATE NOCASE', 'length': 'v.duration_s',
}
LENGTH_FILTERS = [('Länge: alle', None), ('Länge: kurz', 'short'), ('Länge: mittel', 'medium'), ('Länge: lang', 'long')]
LENGTH_NAMES = {'short': 'kurz', 'medium': 'mittel', 'long': 'lang'}


def length_filter_labels(short=DEFAULT_SHORT_S, long=DEFAULT_LONG_S):
    """Beschriftungen des Längenfilters mit den eingestellten Grenzen: „Länge: kurz (bis 60 s)“ …"""
    return [('Länge: alle', None), (f'Länge: kurz (bis {_span(short)})', 'short'),
            (f'Länge: mittel ({_span(short)} bis {_span(long)})', 'medium'), (f'Länge: lang (über {_span(long)})', 'long')]


def _span(seconds):
    seconds = int(seconds)
    if seconds < 120:
        return f'{seconds} s'
    return f'{seconds // 60} min' if seconds % 60 == 0 else format_duration(seconds)


def _length_sql(length, short, long):
    """(SQL-Bedingung, Argumente) für den Längenfilter; Videos ohne Länge fallen immer heraus."""
    if length is None:
        return '', []
    if length == 'short':
        return ' AND v.duration_s IS NOT NULL AND v.duration_s<=?', [float(short)]
    if length == 'medium':
        return ' AND v.duration_s>? AND v.duration_s<=?', [float(short), float(long)]
    if length == 'long':
        return ' AND v.duration_s>?', [float(long)]
    raise ValueError('Unbekannter Längenfilter')

ALL, UNGROUPED = 'all', 'none'   # Ansichten der Gruppenliste neben echten Gruppen-Nummern
# Ein Video gehört zu einer Gruppe, wenn es dort einsortiert ist ODER sein Ersteller der Gruppe angehört.
_IN_ANY_GROUP = ("(EXISTS (SELECT 1 FROM group_videos gv WHERE gv.video_uid=v.uid) OR EXISTS (SELECT 1 FROM group_people gp "
                 "WHERE gp.role='creator' AND gp.platform=v.platform AND gp.author_key=ss_ak(v.author_id, v.author)))")
_UNGROUPED = 'NOT ' + _IN_ANY_GROUP
_GROUP_NAMES = ("(SELECT GROUP_CONCAT(name, ', ') FROM (SELECT g.name AS name FROM groups g JOIN group_videos gv ON gv.group_id=g.id "
                "WHERE gv.video_uid=v.uid UNION SELECT g.name FROM groups g JOIN group_people gp ON gp.group_id=g.id "
                "WHERE gp.role='creator' AND gp.platform=v.platform AND gp.author_key=ss_ak(v.author_id, v.author)))")


# ---------- Fehler und Verbindungen ----------

def friendly_error(error):
    """Ein verständlicher Satz für jede Art von Fehler (nie ein Stacktrace)."""
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, sqlite3.OperationalError):
        text = str(error).lower()
        if 'locked' in text or 'busy' in text:
            return ('Die Datenbank ist gerade gesperrt (zum Beispiel weil der Hintergrunddienst schreibt). '
                    'Bitte in ein paar Sekunden noch einmal versuchen.')
        if 'unable to open' in text or 'readonly' in text:
            return 'Die Datenbankdatei kann nicht geöffnet oder beschrieben werden. Ist der Ordner „data“ schreibbar?'
    if isinstance(error, sqlite3.DatabaseError):
        return f'Die Datenbank meldet einen Fehler: {error}'
    if isinstance(error, PermissionError):
        return 'Die Datei ist gesperrt oder gehört einem anderen Programm. Bitte schließen und noch einmal versuchen.'
    if isinstance(error, OSError):
        return f'Dateifehler: {error.strerror or error}'
    return f'Unerwarteter Fehler: {error}'


@contextlib.contextmanager
def writer(path=None):
    """Schreibende Verbindung (legt Datenbank und Tabellen bei Bedarf an) und schließt sie sicher wieder."""
    store = Store(path or core.db_path())
    try:
        yield store.db
    finally:
        store.close()


_MIGRATED = set()


def _ensure_schema(path):
    """Eine ältere Datenbank bekommt beim ersten Lesen die neuen Tabellen (Stimmung, Personengruppen): einmal kurz über Store öffnen."""
    try:
        key = str(pathlib.Path(path).resolve())
        if key in _MIGRATED or not pathlib.Path(path).is_file():
            return
        Store(path).close()
        _MIGRATED.add(key)
    except Exception:   # gesperrt o. Ä.: die Lesefunktionen kommen auch ohne die neuen Tabellen zurecht
        pass


@contextlib.contextmanager
def reader(path=None):
    """Schreibgeschützte Verbindung mit Zeilen als Wörterbuch. Wirft sqlite3.OperationalError, wenn es die Datei nicht gibt."""
    path = path or core.db_path()
    _ensure_schema(path)
    with core._ro(path) as db:
        db.row_factory = sqlite3.Row
        people_repo.prepare(db)   # SQL-Funktionen für Personen und Stimmung
        yield db


def _dicts(rows):
    return [dict(r) for r in rows]


def fmt_number(value):
    if value is None or value == '':
        return ''
    try:
        return f'{int(value):,}'.replace(',', '.')
    except (TypeError, ValueError):
        return str(value)


def short_date(text):
    return (text or '')[:10]


# ---------- Konten ----------

def load_accounts(path=None):
    """Alle Konten (Standardkonten werden beim ersten Aufruf angelegt)."""
    with writer(path) as db:
        repo.ensure_default_accounts(db)
        return repo.list_accounts(db)


def _check_label(db, platform, label, own_id=None):
    label = (label or '').strip()
    if not label:
        raise ValueError('Bitte einen Namen für das Konto eingeben')
    if len(label) > 60:
        raise ValueError('Der Kontoname ist zu lang (höchstens 60 Zeichen)')
    for acc in repo.list_accounts(db, platform):
        if acc['id'] != own_id and acc['label'].strip().lower() == label.lower():
            raise ValueError(f'Ein Konto „{acc["label"]}“ gibt es bei {core.PLATFORMS[platform]["name"]} schon')
    return label


def check_country(country):
    country = (country or '').strip().lower()
    if not re.fullmatch(r'[a-z]{2}', country):
        raise ValueError('Das Land besteht aus zwei Buchstaben, zum Beispiel de, at oder us')
    return country


def add_account(path, platform, label, country='de'):
    if platform not in repo.LOGIN_PLATFORMS:
        raise ValueError('Konten gibt es nur für TikTok, Instagram, Reddit und X')
    country = check_country(country)
    with writer(path) as db:
        label = _check_label(db, platform, label)
        return repo.add_account(db, platform, label, country)


def rename_account(path, account_id, label):
    with writer(path) as db:
        acc = repo.get_account(db, account_id)
        if acc is None:
            raise ValueError('Dieses Konto gibt es nicht mehr. Die Liste wird neu geladen.')
        repo.rename_account(db, account_id, _check_label(db, acc['platform'], label, own_id=account_id))


def set_default(path, account_id):
    with writer(path) as db:
        repo.set_default_account(db, account_id)


def account_target_count(path, account_id):
    """Wie viele automatische Ziele dieses Konto benutzen."""
    with writer(path) as db:
        return db.execute('SELECT COUNT(*) FROM targets WHERE account_id=?', (account_id,)).fetchone()[0]


def remove_account(path, account_id):
    """Entfernt den Eintrag. Das letzte Konto einer Plattform bleibt immer erhalten."""
    with writer(path) as db:
        acc = repo.get_account(db, account_id)
        if acc is None:
            return
        if len(repo.list_accounts(db, acc['platform'])) <= 1:
            raise ValueError(f'Bei {core.PLATFORMS[acc["platform"]]["name"]} muss mindestens ein Konto bleiben. '
                             'Lege zuerst ein anderes an oder benenne dieses um.')
        repo.delete_account(db, account_id)


# ---------- Rotation und fester Proxy ----------

def load_rotation(path=None):
    """Rotationsstatus für die Kontenliste: {'rows': {Konto-Nummer: Zeile}, 'summary': {Plattform: Text},
    'hints': {Plattform: [Sätze]}}. Siehe rotation.rotation_status."""
    with writer(path) as db:
        repo.ensure_default_accounts(db)
        rows = rotation.rotation_status(db)
        out = {'rows': {r['id']: r for r in rows}, 'summary': {}, 'hints': {}}
        for platform in repo.LOGIN_PLATFORMS:
            mine = [r for r in rows if r['platform'] == platform]
            if mine:
                out['summary'][platform] = rotation.summary(mine)
                out['hints'][platform] = rotation.ip_hints(mine)
        return out


def set_rotation(path, account_id, enabled):
    with writer(path) as db:
        repo.set_account_rotation(db, account_id, enabled)


def parse_daily_limit(text):
    """Leer = unbegrenzt (None), sonst eine ganze Zahl von 1 bis 100000."""
    text = str(text or '').strip()
    if not text:
        return None
    if not re.fullmatch(r'[0-9]{1,6}', text) or not 1 <= int(text) <= 100000:
        raise ValueError('Bitte eine ganze Zahl von 1 bis 100000 eingeben – oder das Feld leer lassen für „unbegrenzt“.')
    return int(text)


def set_daily_limit(path, account_id, text):
    limit = parse_daily_limit(text)
    with writer(path) as db:
        repo.set_account_daily_limit(db, account_id, limit)
    return limit


def clear_cooldown(path, account_id):
    with writer(path) as db:
        rotation.clear_cooldown(db, account_id)


def set_proxy(path, account_id, raw):
    """Fester Proxy für ein Konto. Gibt die Anzeige ohne Passwort zurück."""
    with writer(path) as db:
        return rotation.set_account_proxy(db, account_id, raw)


def clear_proxy(path, account_id):
    with writer(path) as db:
        rotation.clear_account_proxy(db, account_id)


def check_proxy_text(raw):
    """Prüft nur das Format. Gibt (Anzeige ohne Passwort, Fehlertext) zurück."""
    from ..net import accountproxy
    try:
        return accountproxy.mask(accountproxy.normalize(raw)), None
    except ValueError as error:
        return None, str(error)


def rotation_problem(path, platform):
    """Satz, warum die Rotation für diese Plattform gerade nicht starten kann (z. B. alle Konten in Abkühlung, mit
    dem Zeitpunkt, an dem wieder eins frei wird) – oder None, wenn mindestens ein Konto bereit ist."""
    with writer(path) as db:
        repo.ensure_default_accounts(db)
        if rotation.pick_account(db, platform, mode='rotate') is not None:
            return None
        return str(rotation.no_account_message(db, platform)[0])


def test_saved_proxy(path, identity):
    """Testet den gespeicherten festen Proxy eines Kontos (sein Passwort verlässt diese Funktion nicht)."""
    from ..net import accountproxy
    raw = accountproxy.load(path).get(identity)
    if not raw:
        return False, 'Für dieses Konto ist kein Proxy gespeichert.', None
    return rotation.test_proxy(raw)


def test_proxy(raw):
    """(ok, Meldung, Ausgangs-IP). Netzzugriff: nur in einem Hintergrund-Thread aufrufen."""
    return rotation.test_proxy(raw)


# ---------- Gruppen ----------

def group_overview(path=None):
    """Für die Gruppenliste: {'total', 'ungrouped', 'groups': [{id,name,color,video_count}], 'stats': [...]}.
    Ohne Datenbankdatei: alles leer."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return {'total': 0, 'ungrouped': 0, 'groups': [], 'stats': []}
    with reader(path) as db:
        total = db.execute('SELECT COUNT(*) FROM videos').fetchone()[0]
        ungrouped = db.execute(f'SELECT COUNT(*) FROM videos v WHERE {_UNGROUPED}').fetchone()[0]
        groups = repo.list_groups(db)
        stats = repo.group_stats(db)
        mood = srepo.group_distributions(db)
        member_mood = people_repo.group_sentiment(db)
    for s in stats:   # Stimmung der Kommentare unter den Videos der Gruppe und der Kommentare ihrer Mitglieder (KI-Einschätzung)
        for key, source in (('mood', mood), ('member_mood', member_mood)):
            m = source.get(s['id'], {})
            s[key] = {'positiv': m.get('positiv', 0), 'neutral': m.get('neutral', 0), 'negativ': m.get('negativ', 0)}
    return {'total': total, 'ungrouped': ungrouped, 'groups': groups, 'stats': stats}


def _check_group_name(db, name, own_id=None):
    name = (name or '').strip()
    if not name:
        raise ValueError('Bitte einen Gruppennamen eingeben')
    if len(name) > 60:
        raise ValueError('Der Gruppenname ist zu lang (höchstens 60 Zeichen)')
    for g in repo.list_groups(db):
        if g['id'] != own_id and g['name'].strip().lower() == name.lower():
            raise ValueError(f'Eine Gruppe „{g["name"]}“ gibt es schon. Bitte einen anderen Namen wählen.')
    return name


def create_group(path, name, color=None):
    with writer(path) as db:
        return repo.create_group(db, _check_group_name(db, name), color)


def update_group(path, group_id, name, color):
    with writer(path) as db:
        repo.update_group(db, group_id, name=_check_group_name(db, name, own_id=group_id), color=color)


def delete_group(path, group_id):
    """Löscht nur die Gruppe und ihre Zuordnungen, nie Videos."""
    with writer(path) as db:
        repo.delete_group(db, group_id)


def add_to_group(path, group_id, uids):
    uids = list(dict.fromkeys(uids))
    if not uids:
        raise ValueError('Bitte zuerst Videos auswählen')
    with writer(path) as db:
        if db.execute('SELECT 1 FROM groups WHERE id=?', (group_id,)).fetchone() is None:
            raise ValueError('Diese Gruppe gibt es nicht mehr.')
        before = db.execute('SELECT COUNT(*) FROM group_videos WHERE group_id=?', (group_id,)).fetchone()[0]
        repo.add_videos_to_group(db, group_id, uids)
        return db.execute('SELECT COUNT(*) FROM group_videos WHERE group_id=?', (group_id,)).fetchone()[0] - before


def remove_from_group(path, group_id, uids):
    uids = list(dict.fromkeys(uids))
    if not uids:
        raise ValueError('Bitte zuerst Videos auswählen')
    with writer(path) as db:
        before = db.execute('SELECT COUNT(*) FROM group_videos WHERE group_id=?', (group_id,)).fetchone()[0]
        repo.remove_videos_from_group(db, group_id, uids)
        after = db.execute('SELECT COUNT(*) FROM group_videos WHERE group_id=?', (group_id,)).fetchone()[0]
    return before - after   # wirklich entfernte Zeilen – Videos, die nur über ihren Ersteller zur Gruppe gehören, zählen nicht


def groups_of(path, uids):
    """Gruppen, in denen mindestens eines der Videos steckt."""
    if not uids or not pathlib.Path(path or core.db_path()).is_file():
        return []
    with reader(path) as db:
        seen = {}
        for uid in uids:
            for g in repo.groups_of_video(db, uid):
                seen[g['id']] = dict(g)
    return sorted(seen.values(), key=lambda g: g['name'].lower())


# ---------- Videos und Kommentare ----------

def _scope_sql(search, platform, group):
    """Gemeinsame Filter für Videoliste und Längenvergleich (Plattform, Gruppe, Suche)."""
    sql, args = '', []
    if platform:
        sql += ' AND v.platform=?'; args.append(platform)
    if group == UNGROUPED:
        sql += f' AND {_UNGROUPED}'
    elif group not in (ALL, None):
        sql += (' AND (EXISTS (SELECT 1 FROM group_videos gv WHERE gv.video_uid=v.uid AND gv.group_id=?) OR '
                + people_repo.creator_member_sql('v') + ')')
        args += [int(group), int(group)]
    if (search or '').strip():
        sql += " AND (cf(v.author) LIKE ? ESCAPE '\\' OR cf(v.caption) LIKE ? ESCAPE '\\' OR cf(v.url) LIKE ? ESCAPE '\\')"
        pat = core.search_pattern(search)
        args += [pat, pat, pat]
    return sql, args


def length_comparison(path=None, search='', platform='', group=ALL, short=DEFAULT_SHORT_S, long=DEFAULT_LONG_S):
    """„Länge im Vergleich“: je Längenklasse die Zahl der Videos und der Durchschnitt von Aufrufen, Likes und Kommentaren
    (nur gemessene Werte – ein leerer Wert zählt weder als 0 noch im Nenner). Videos ohne Länge stehen in einer eigenen Zeile.
    Gibt [{'key', 'name', 'videos', 'avg_views', 'avg_likes', 'avg_comments', 'avg_length'}] zurück."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return []
    scope, args = _scope_sql(search, platform, group)
    cls = ('CASE WHEN v.duration_s IS NULL THEN \'none\' WHEN v.duration_s<=? THEN \'short\' '
           'WHEN v.duration_s<=? THEN \'medium\' ELSE \'long\' END')
    sql = (f'SELECT {cls} AS klass, COUNT(*), AVG(v.view_count), AVG(v.like_count), AVG(v.comment_count), AVG(v.duration_s) '
           f'FROM videos v WHERE 1=1{scope} GROUP BY klass')
    with reader(path) as db:
        found = {r[0]: r for r in db.execute(sql, [float(short), float(long), *args]).fetchall()}
    out = []
    for key, name in (('short', 'kurz'), ('medium', 'mittel'), ('long', 'lang'), ('none', 'ohne Länge')):
        r = found.get(key)
        out.append({'key': key, 'name': name, 'videos': r[1] if r else 0, 'avg_views': r[2] if r else None,
                    'avg_likes': r[3] if r else None, 'avg_comments': r[4] if r else None, 'avg_length': r[5] if r else None})
    return out


def query_videos(path=None, search='', platform='', group=ALL, sort='fetched', descending=True, limit=2000,
                 length=None, short=DEFAULT_SHORT_S, long=DEFAULT_LONG_S):
    """Videos für die Liste. group: ALL | UNGROUPED | Gruppen-Nummer. length: None | 'short' | 'medium' | 'long'.
    Gibt Wörterbücher zurück."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return []
    if sort not in SORT_SQL:
        raise ValueError('Unbekannte Sortierung')
    sql = ('SELECT v.uid, v.platform, v.video_id, v.url, v.author, v.caption, v.view_count, v.like_count, '
           'v.comment_count, v.created_at, v.fetched_at, v.first_seen_at, v.duration_s, '
           '(SELECT COUNT(*) FROM comments c WHERE c.platform=v.platform AND c.video_id=v.video_id) AS saved_comments, '
           f'{_GROUP_NAMES} AS group_names, '
           "EXISTS(SELECT 1 FROM targets t WHERE t.kind='video' AND t.platform=v.platform AND t.value=v.url) AS tracked "
           'FROM videos v WHERE 1=1')
    scope, args = _scope_sql(search, platform, group)
    sql += scope
    extra, extra_args = _length_sql(length, short, long)
    sql += extra
    args += extra_args
    col = SORT_SQL[sort]
    # Leere Werte immer ans Ende, egal in welche Richtung sortiert wird.
    null_test = col.split(' COLLATE')[0]
    sql += f' ORDER BY ({null_test} IS NULL), {col} {"DESC" if descending else "ASC"}, v.uid LIMIT ?'
    args.append(int(limit))
    with reader(path) as db:
        return _dicts(db.execute(sql, args).fetchall())


def count_videos(path=None):
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return 0, 0
    return core.counts(path)


def _parse_stamp(text):
    """Zeitstempel aus der Datenbank (…Z, …+00:00 oder 'JJJJ-MM-TT HH:MM:SS') als UTC-Zeit, sonst None."""
    if not text:
        return None
    t = str(text).strip().replace('Z', '+00:00')
    try:
        moment = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    return moment.replace(tzinfo=moment.tzinfo or datetime.timezone.utc).astimezone(datetime.timezone.utc)


def local_stamp(text):
    """UTC-Zeitstempel aus der Datenbank als Ortszeit zum Anzeigen (JJJJ-MM-TT HH:MM); unlesbares bleibt wie es ist."""
    moment = _parse_stamp(text)
    if moment is None:
        return (text or '')[:16].replace('T', ' ')
    return moment.astimezone().strftime('%Y-%m-%d %H:%M')


SINCE_PRESETS = [('Alle Kommentare', None), ('Neu in der letzten Stunde', datetime.timedelta(hours=1)),
                 ('Neu in den letzten 24 Stunden', datetime.timedelta(hours=24)),
                 ('Neu in den letzten 7 Tagen', datetime.timedelta(days=7)),
                 ('Neu in den letzten 30 Tagen', datetime.timedelta(days=30))]


def parse_since(text, now=None):
    """Auswahl oder eingetipptes Datum -> UTC-Zeit als Text (zum Vergleich mit first_seen_at) oder None für „alle“.

    Erlaubt: die Voreinstellungen aus SINCE_PRESETS, `TT.MM.JJJJ`, `TT.MM.JJJJ HH:MM`, `JJJJ-MM-TT`, `JJJJ-MM-TT HH:MM`
    (Ortszeit)."""
    text = (text or '').strip()
    now = now or repo.now()
    for label, delta in SINCE_PRESETS:
        if text.lower() == label.lower():
            return None if delta is None else repo.iso(now - delta)
    if not text:
        return None
    for fmt in ('%d.%m.%Y %H:%M', '%d.%m.%Y', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            local = datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
        return repo.iso(local.astimezone(datetime.timezone.utc))
    raise ValueError('Datum nicht verstanden. Bitte so eingeben: 31.12.2026 oder 31.12.2026 18:30')


MOOD_FILTERS = [('Alle Kommentare', None), ('nur negative', 'negativ'), ('nur positive', 'positiv'), ('nur neutrale', 'neutral'),
                ('noch nicht eingestuft', 'offen')]
MOOD_SYMBOL = {'positiv': '▲ positiv', 'neutral': '● neutral', 'negativ': '▼ negativ'}


def mood_text(label):
    """Symbol und Wort der KI-Einschätzung für die Kommentarliste; leer, wenn (noch) nicht eingestuft."""
    return MOOD_SYMBOL.get(label, '')


def _comment_sql(db, mood, with_video=False):
    has = srepo.has_table(db)
    cols = ('c.uid AS uid, c.platform, c.video_id, c.author, c.author_id, c.text, c.like_count, c.created_at, c.first_seen_at, '
            'c.parent_id, ' + ('s.label AS mood, s.score AS mood_score' if has else 'NULL AS mood, NULL AS mood_score'))
    if with_video:
        cols += ', v.author AS video_author, v.caption AS video_caption'
    src = 'comments c' + (' LEFT JOIN comment_sentiment s ON s.comment_uid=c.uid AND s.text_hash=ss_th(c.text)' if has else '')
    if with_video:
        src += " LEFT JOIN videos v ON v.uid = c.platform || ':' || c.video_id"
    where = []
    if mood in ('positiv', 'neutral', 'negativ'):
        where.append("s.label='%s'" % mood if has else '0')
    elif mood == 'offen':
        where.append('s.comment_uid IS NULL' if has else '1')
    return f'SELECT {cols} FROM {src}', where


def query_comments(path, platform, video_id, since_iso=None, limit=5000, mood=None, group=None):
    """Kommentare eines Videos (meiste Likes zuerst); since_iso: nur Kommentare, die zum ersten Mal danach gesehen wurden.

    mood: None | 'positiv' | 'neutral' | 'negativ' | 'offen' (Filter nach KI-Einschätzung); group: nur Kommentare von
    Kommentarschreibern dieser Gruppe. Jede Zeile hat zusätzlich uid, mood (Stimmung oder None) und mood_score."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return []
    with reader(path) as db:
        sql, where = _comment_sql(db, mood)
        where += ['c.platform=?', 'c.video_id=?']
        args = [platform, video_id]
        if since_iso:
            where.append("substr(REPLACE(c.first_seen_at, ' ', 'T'),1,19)>=substr(?,1,19)")   # ältere Zeilen haben 'JJJJ-MM-TT HH:MM:SS'
            args.append(since_iso)
        if group is not None:
            where.append(people_repo.commenter_member_sql('c'))
            args.append(int(group))
        sql += ' WHERE ' + ' AND '.join(where) + ' ORDER BY COALESCE(c.like_count,0) DESC, c.rowid LIMIT ?'
        args.append(int(limit))
        return _dicts(db.execute(sql, args).fetchall())


def query_group_comments(path, group_id, since_iso=None, limit=2000, mood=None):
    """Kommentare aller Kommentarschreiber einer Gruppe (über alle Videos), mit Angaben zum Video."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return []
    with reader(path) as db:
        sql, where = _comment_sql(db, mood, with_video=True)
        where.append(people_repo.commenter_member_sql('c'))
        args = [int(group_id)]
        if since_iso:
            where.append("substr(REPLACE(c.first_seen_at, ' ', 'T'),1,19)>=substr(?,1,19)")
            args.append(since_iso)
        sql += ' WHERE ' + ' AND '.join(where) + ' ORDER BY COALESCE(c.like_count,0) DESC, c.rowid LIMIT ?'
        args.append(int(limit))
        return _dicts(db.execute(sql, args).fetchall())


def query_person_comments(path, people, since_iso=None, limit=2000, mood=None):
    """Kommentare gewählter Kommentarschreiber über alle Videos. people: [(Plattform, author_key)] (höchstens 200)."""
    path = path or core.db_path()
    people = list(people)[:200]
    if not people or not pathlib.Path(path).is_file():
        return []
    with reader(path) as db:
        sql, where = _comment_sql(db, mood, with_video=True)
        where.append('(' + ' OR '.join('(c.platform=? AND ss_ak(c.author_id, c.author)=?)' for _ in people) + ')')
        args = [x for p in people for x in p]
        if since_iso:
            where.append("substr(REPLACE(c.first_seen_at, ' ', 'T'),1,19)>=substr(?,1,19)")
            args.append(since_iso)
        sql += ' WHERE ' + ' AND '.join(where) + ' ORDER BY COALESCE(c.like_count,0) DESC, c.rowid LIMIT ?'
        args.append(int(limit))
        return _dicts(db.execute(sql, args).fetchall())


# ---------- Stimmung (KI-Einschätzung) ----------

def mood_scope(uids=None, group=None):
    """Umfang für die Stimmung: gewählte Videos, sonst eine Gruppe (Videos der Gruppe), sonst alles."""
    if uids:
        return {'videos': list(uids)}
    if group is not None:
        return {'group': int(group)}
    return {}


def mood_distribution(path, scope=None):
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return {'positive': 0, 'neutral': 0, 'negative': 0, 'analyzed': 0, 'total_comments': 0, 'open': 0}
    with reader(path) as db:
        return srepo.distribution(db, scope)


def mood_series(path, scope=None, interval='day'):
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return []
    with reader(path) as db:
        return srepo.series(db, scope, interval)


# ---------- Personen in Gruppen ----------

# Wann gilt ein Autor als „neu“? Wenn er zum ersten Mal seit diesem Zeitpunkt in der Datenbank gesehen wurde.
NEW_WINDOWS = [('seit heute', 'today'), ('in den letzten 24 Stunden', datetime.timedelta(hours=24)),
               ('in den letzten 7 Tagen', datetime.timedelta(days=7)), ('in den letzten 30 Tagen', datetime.timedelta(days=30)),
               ('seit dem letzten Lauf', 'lastrun')]
SHOW_FILTERS = [('Alle', None), ('Nur neue Autoren', 'new'), ('Nur bekannte (nicht neu)', 'known'), ('Ohne Gruppe', 'none')]


def last_run_start(path=None):
    """Beginn des letzten abgeschlossenen Laufs (ok oder teilweise) oder None."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return None
    with reader(path) as db:
        row = db.execute("SELECT MAX(started_at) FROM runs WHERE status IN ('ok','partial')").fetchone()
    return row[0] if row and row[0] else None


def new_since(path, text, now=None):
    """Auswahl oder eingetipptes Datum -> UTC-Zeit (`JJJJ-MM-TTTHH:MM:SSZ`), ab der ein Autor „neu“ heißt. Wirft ValueError mit Hilfetext."""
    text = (text or '').strip()
    now = now or repo.now()
    for label, kind in NEW_WINDOWS:
        if text.lower() == label.lower():
            if kind == 'today':   # naive Ortszeit -> astimezone nimmt den Versatz, der an diesem Tag gilt (Zeitumstellung)
                midnight = datetime.datetime.combine(now.astimezone().date(), datetime.time(0)).astimezone(datetime.timezone.utc)
                return repo.iso(midnight)
            if kind == 'lastrun':
                started = last_run_start(path)
                if not started:
                    raise ValueError('Es gibt noch keinen abgeschlossenen Lauf. Wähle einen anderen Zeitraum.')
                return started
            return repo.iso(now - kind)
    moment = parse_since(text, now)
    if moment is None:
        raise ValueError('Zeitraum nicht verstanden. Wähle einen aus der Liste oder tippe ein Datum, z. B. 31.12.2026 oder 31.12.2026 18:30.')
    return moment


def people_list(path, role, platform='', group=None, search='', sort=None, desc=None, limit=2000, first_seen_since=None, known=None):
    """Personen einer Rolle ('creator' | 'commenter') mit Kennzahlen: (Zeilen, Gesamtzahl). group: Nummer, 'none' oder None.
    first_seen_since + known: False = nur neue (zum ersten Mal seit dann gesehen), True = nur bekannte."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return [], 0
    with reader(path) as db:
        return people_repo.list_people(db, role, platform=platform or None, group=group, search=search, sort=sort,
                                       desc=desc, limit=limit, first_seen_since=first_seen_since, known=known)


def new_people_count(path, role, since_iso):
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return 0
    with reader(path) as db:
        return people_repo.count_new(db, role, since_iso)


def mark_new_authors(path, comments, since_iso):
    """Setzt bei jedem Kommentar `author_new`: sein Autor war vor `since_iso` noch nie in der Datenbank."""
    path = path or core.db_path()
    keys = [(c['platform'], people_repo.author_key(c.get('author_id'), c.get('author'))) for c in comments]
    first = {}
    if keys and since_iso and pathlib.Path(path).is_file():
        with reader(path) as db:
            first = people_repo.first_seen_map(db, 'commenter', keys)
    cut = (since_iso or '').replace(' ', 'T')[:19]
    for c, key in zip(comments, keys):
        seen = first.get(key)
        c['author_new'] = bool(since_iso and seen and seen.replace(' ', 'T')[:19] >= cut)
    return comments


def person_refs(path, role, uids=None, comment_uids=None):
    """Personen zu gewählten Videos (Ersteller) bzw. Kommentaren (Kommentarschreiber), ohne Doppelte und ohne Unbrauchbare."""
    path = path or core.db_path()
    refs, seen = [], set()
    table, ids = ('videos', list(uids or [])) if role == 'creator' else ('comments', list(comment_uids or []))
    if not ids or not pathlib.Path(path).is_file():
        return refs
    with reader(path) as db:
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            for r in db.execute(f'SELECT platform, author_id, author FROM {table} WHERE uid IN ({",".join("?" for _ in chunk)})', chunk):
                p = people_repo.person(r['platform'], r['author_id'], r['author'])
                key = (p['platform'], p['author_key'])
                if p['author_key'] and key not in seen:
                    seen.add(key)
                    refs.append(p)
    return refs


def add_people_to_group(path, group_id, role, people):
    """Gibt (neu, schon drin, nicht verwendbar) zurück."""
    if not people:
        raise ValueError('Bitte zuerst Personen auswählen')
    with writer(path) as db:
        return people_repo.add_people(db, group_id, role, people)


def remove_people_from_group(path, group_id, role, people):
    if not people:
        raise ValueError('Bitte zuerst Personen auswählen')
    with writer(path) as db:
        return people_repo.remove_people(db, group_id, role, people)


def history(path, uid):
    """Verlaufspunkte eines Videos (älteste zuerst)."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return []
    with reader(path) as db:
        return repo.video_history(db, uid)


def chart_layout(values, width, height, pad=(46, 14, 14, 26)):
    """Rechnet Messwerte in Bildpunkte um (ohne Tk). values: Liste von (Zeitstempeltext, Zahl oder None).

    Gibt Liste von (x, y, text, zahl) zurück; Punkte ohne Zahl entfallen. Ein einzelner Punkt steht in der Mitte.
    Zeitlich gleichmäßig, wenn alle Zeitstempel lesbar und verschieden sind, sonst nach Reihenfolge."""
    pts = [(t, v) for t, v in values if isinstance(v, (int, float))]
    left, top, right, bottom = pad
    w, h = max(width - left - right, 1), max(height - top - bottom, 1)
    if not pts:
        return []
    stamps = [_parse_stamp(t) for t, _v in pts]
    if all(stamps) and len({s for s in stamps}) > 1:
        t0, span = stamps[0], (stamps[-1] - stamps[0]).total_seconds() or 1
        xs = [left + w * ((s - t0).total_seconds() / span) for s in stamps]
    elif len(pts) == 1:
        xs = [left + w / 2]
    else:
        xs = [left + w * i / (len(pts) - 1) for i in range(len(pts))]
    lo, hi = min(v for _t, v in pts), max(v for _t, v in pts)
    if lo == hi:   # gleichbleibende Zahl: Linie in der Mitte
        ys = [top + h / 2] * len(pts)
    else:
        ys = [top + h * (1 - (v - lo) / (hi - lo)) for _t, v in pts]
    return [(x, y, t, v) for x, y, (t, v) in zip(xs, ys, pts)]


# ---------- Verfolgen, Einsortieren nach dem Abruf ----------

def track_videos(path, uids, interval_min=DEFAULT_INTERVAL, account_by_platform=None):
    """Legt je Video ein Ziel (kind=video) an. Gibt {'added','existing','skipped':[Text]} zurück."""
    uids = list(dict.fromkeys(uids))
    if not uids:
        raise ValueError('Bitte zuerst Videos auswählen')
    result = {'added': 0, 'existing': 0, 'skipped': []}
    with writer(path) as db:
        for uid in uids:
            row = db.execute('SELECT platform, url, author FROM videos WHERE uid=?', (uid,)).fetchone()
            if row is None or not (row[1] or '').strip():
                result['skipped'].append(f'{uid}: kein Link gespeichert')
                continue
            platform, url = row[0], row[1].strip()
            account = (account_by_platform or {}).get(platform)
            mode = None
            if account == 'rotate':         # „Automatisch wechseln (Rotation)“ gewählt
                account, mode = None, 'rotate'
            elif account is None and platform in repo.LOGIN_PLATFORMS:
                default = repo.default_account(db, platform)
                account = default['id'] if default else None
            try:
                repo.add_target(db, 'video', platform, url, account_id=account, interval_min=interval_min, account_mode=mode)
                result['added'] += 1
            except ValueError as error:
                if 'gibt es schon' in str(error):
                    result['existing'] += 1
                else:
                    result['skipped'].append(f'{row[2] or uid}: {error}')
    return result


def find_fetched_uids(path, since, platforms, links_by_platform=None):
    """Videos, die seit `since` (UTC-Zeit) abgerufen wurden, auf den genannten Plattformen.

    Mit Links: nur die Videos, deren Kennung in einem der Links vorkommt. Gibt es einen Link, der sich keinem Video
    zuordnen lässt (z. B. ein Kurzlink), zählen alle Videos der Plattform aus dem Zeitfenster."""
    path = path or core.db_path()
    if not pathlib.Path(path).is_file():
        return []
    # fetched_at steht lokal als 'JJJJ-MM-TT HH:MM:SS', aus der Cloud als 'JJJJ-MM-TTTHH:MM:SSZ' (beides UTC). Ein roher
    # Textvergleich hielte jede Cloud-Zeile desselben Tages für „gerade abgerufen“ ('T' > ' '): darum beides auf
    # 'JJJJ-MM-TTTHH:MM:SS' bringen, wie repo.new_comments_since.
    start = (since - datetime.timedelta(seconds=2)).strftime('%Y-%m-%dT%H:%M:%S')
    with reader(path) as db:
        rows = db.execute("SELECT uid, platform, video_id FROM videos WHERE substr(replace(fetched_at,' ','T'),1,19)>=?",
                          (start,)).fetchall()
    chosen = []
    for plat in platforms:
        cand = [r for r in rows if r['platform'] == plat]
        links = (links_by_platform or {}).get(plat)
        if links:
            ids = [r['video_id'] for r in cand]
            if any(not any(i in link for i in ids) for link in links):
                pick = cand
            else:
                pick = [r for r in cand if any(r['video_id'] in link for link in links)]
        else:
            pick = cand
        chosen += [r['uid'] for r in pick]
    return list(dict.fromkeys(chosen))


def describe_busy(root=None):
    """Satz, wenn gerade ein anderer Browserauftrag läuft (Sperre data/run.lock), sonst None."""
    try:
        from .. import runlock
    except ImportError:
        return None
    root = pathlib.Path(core.ROOT if root is None else root)
    try:
        lock_root = runlock.root_for_db(core.db_path(root))   # dieselbe Sperrdatei wie Befehle und Dienst
        if not runlock.lock_path(lock_root).exists():
            return None
        info = runlock.holder(lock_root)
    except Exception:   # die Prüfung ist nur eine Hilfe, sie darf den Abruf nie verhindern
        return None
    if not info:
        return None
    if info.get('owner') == 'daemon':
        return ('Der Hintergrunddienst arbeitet gerade. Bitte warte, bis er fertig ist, und starte den Abruf dann noch einmal '
                '(oder beende den Dienst im Tab „Automatik“).')
    return runlock.describe(info)
