"""Personen in Gruppen: Ersteller von Videos und Kommentarschreiber (Tabelle `group_people`).

Eine Person ist kein eigener Datensatz, sondern ein Name (oder eine Kennung) aus den gespeicherten Videos und
Kommentaren. Ihr Schlüssel ist `(Plattform, author_key)`; `author_key` ist die Autoren-Kennung (`author_id`), falls
vorhanden, sonst der Anzeigename kleingeschrieben und ohne führendes @. Gleiche Namen auf zwei Plattformen sind zwei
verschiedene Personen. Gespeichert werden nur Namen und Kennungen, die ohnehin öffentlich sichtbar in der Datenbank
stehen. Einordnungen geschehen nur durch eine Handlung von Marc – nie automatisch.

Die Funktionen bekommen eine offene sqlite3-Verbindung (zum Lesen genügt eine schreibgeschützte) und committen beim
Schreiben selbst. Zeiten UTC `JJJJ-MM-TTTHH:MM:SSZ`.
"""
import datetime
import unicodedata

from . import sentiment_repo as srepo

ROLES = ('creator', 'commenter')
ROLE_NAMES = {'creator': 'Ersteller', 'commenter': 'Kommentarschreiber'}
SORTS = {
    'creator': {'name': 'ss_sort(p.display_name)', 'videos': 'p.videos', 'views': 'p.views', 'likes': 'p.likes',
                'groups': 'p.group_names', 'platform': 'p.platform', 'seen': 'p.first_seen_at'},
    'commenter': {'name': 'ss_sort(p.display_name)', 'comments': 'p.comments', 'videos': 'p.videos', 'likes': 'p.likes',
                  'first': 'p.first_activity', 'last': 'p.last_activity', 'positive': 'p.positive_share',
                  'negative': 'p.negative_share', 'groups': 'p.group_names', 'platform': 'p.platform', 'seen': 'p.first_seen_at'},
}


def author_key(author_id, author):
    """Schlüssel einer Person: Kennung, sonst Anzeigename (kleingeschrieben, ohne @); '' = nicht verwendbar."""
    ident = str(author_id).strip() if author_id not in (None, '') else ''
    if ident:
        return ident
    return str(author or '').strip().lstrip('@').strip().casefold()


def _casefold(value):
    return value.casefold() if isinstance(value, str) else value


def sort_key(text):
    """Sortierschlüssel: klein, Umlaute und Akzente wie ihre Grundbuchstaben (ä sortiert bei a)."""
    if not isinstance(text, str):
        return ''
    return ''.join(ch for ch in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(ch))


def prepare(db):
    """Meldet `ss_ak(author_id, author)` und `cf(text)` (Groß-/Kleinschreibung egal, auch Umlaute) an der Verbindung an."""
    srepo.prepare(db)
    db.create_function('ss_ak', 2, author_key, deterministic=True)
    db.create_function('cf', 1, _casefold, deterministic=True)
    db.create_function('ss_sort', 1, sort_key, deterministic=True)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _rows(db, sql, args=()):
    cur = db.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def has_table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='group_people'").fetchone() is not None


# ---------- Zuordnung ----------

def person(platform, author_id=None, author=None, key=None, name=None):
    """Eine Personenangabe als Wörterbuch {'platform', 'author_key', 'display_name'} (key überschreibt die Berechnung)."""
    return {'platform': platform, 'author_key': key if key is not None else author_key(author_id, author),
            'display_name': name if name is not None else (author or key or '')}


def add_people(db, group_id, role, people):
    """Ordnet Personen einer Gruppe zu (mehrfach, idempotent). Gibt (neu hinzugefügt, schon drin, nicht verwendbar) zurück."""
    if role not in ROLES:
        raise ValueError('Unbekannte Rolle')
    if db.execute('SELECT 1 FROM groups WHERE id=?', (group_id,)).fetchone() is None:
        raise ValueError('Diese Gruppe gibt es nicht mehr.')
    wanted, unusable = {}, 0
    for p in people:
        if not p.get('platform') or not p.get('author_key'):
            unusable += 1
            continue
        wanted.setdefault((p['platform'], p['author_key']), p.get('display_name') or p['author_key'])
    added = 0
    ts = now_iso()
    with db:
        for (platform, key), name in wanted.items():
            cur = db.execute('INSERT OR IGNORE INTO group_people (group_id, role, platform, author_key, display_name, added_at) '
                             'VALUES (?,?,?,?,?,?)', (group_id, role, platform, key, name, ts))
            added += cur.rowcount
    return added, len(wanted) - added, unusable


def remove_people(db, group_id, role, people):
    """Nimmt Personen aus der Gruppe (nur die Zuordnung). Gibt die Anzahl entfernter Zuordnungen zurück."""
    removed = 0
    with db:
        for p in people:
            cur = db.execute('DELETE FROM group_people WHERE group_id=? AND role=? AND platform=? AND author_key=?',
                             (group_id, role, p.get('platform'), p.get('author_key')))
            removed += cur.rowcount
    return removed


def groups_of_person(db, role, platform, key):
    if not has_table(db):
        return []
    return _rows(db, 'SELECT g.* FROM groups g JOIN group_people gp ON gp.group_id=g.id '
                     'WHERE gp.role=? AND gp.platform=? AND gp.author_key=? ORDER BY g.name COLLATE NOCASE', (role, platform, key))


def group_members(db, group_id, role=None):
    sql, args = 'SELECT * FROM group_people WHERE group_id=?', [group_id]
    if role:
        sql, args = sql + ' AND role=?', [*args, role]
    return _rows(db, sql + ' ORDER BY role, platform, display_name COLLATE NOCASE', args)


# ---------- Liste mit Kennzahlen ----------

def _sql_time(column):
    return f"substr(replace({column},' ','T'),1,19)"


def first_seen_map(db, role, keys):
    """{(Plattform, author_key): erste Sichtung} für die genannten Personen (Kommentare bzw. Videos; ältester first_seen_at)."""
    keys = {k for k in keys if k[1]}
    if not keys:
        return {}
    prepare(db)
    table, a = ('videos', 'v') if role == 'creator' else ('comments', 'c')
    out = {}
    for platform, key, seen in db.execute(
            f"SELECT {a}.platform, ss_ak({a}.author_id, {a}.author), MIN({_sql_time(a + '.first_seen_at')}) FROM {table} {a} GROUP BY 1, 2"):
        if (platform, key) in keys:
            out[(platform, key)] = seen
    return out


def count_new(db, role, since_iso):
    """Zahl der Personen einer Rolle, die zum ersten Mal seit `since_iso` gesehen wurden (nie vorher)."""
    prepare(db)
    table, a = ('videos', 'v') if role == 'creator' else ('comments', 'c')
    cut = str(since_iso).replace(' ', 'T')[:19]
    return db.execute(f"SELECT COUNT(*) FROM (SELECT {a}.platform, ss_ak({a}.author_id, {a}.author) AS k, MIN({_sql_time(a + '.first_seen_at')}) AS m "
                      f"FROM {table} {a} WHERE ss_ak({a}.author_id, {a}.author) <> '' GROUP BY 1, 2) WHERE m >= ?", (cut,)).fetchone()[0]


def _inner(role, with_sentiment, platform):
    where, args = ['ss_ak({a}.author_id, {a}.author) <> \'\''], []
    if platform:
        where.append('{a}.platform=?')
        args.append(platform)
    if role == 'creator':
        sql = ('SELECT v.platform AS platform, ss_ak(v.author_id, v.author) AS author_key, MAX(v.author) AS display_name, '
               'COUNT(*) AS videos, COALESCE(SUM(v.view_count),0) AS views, COALESCE(SUM(v.like_count),0) AS likes, '
               'NULL AS comments, MIN(substr(v.first_seen_at,1,10)) AS first_activity, MAX(substr(v.fetched_at,1,10)) AS last_activity, '
               "MIN(substr(replace(v.first_seen_at,' ','T'),1,19)) || 'Z' AS first_seen_at, "   # beide Schreibweisen vergleichbar
               'NULL AS positive, NULL AS negative, NULL AS analyzed, NULL AS positive_share, NULL AS negative_share '
               'FROM videos v WHERE ' + ' AND '.join(where).format(a='v') + ' GROUP BY v.platform, 2')
        return sql, args
    day = srepo.DAY_EXPR
    if with_sentiment:
        join = 'comments c LEFT JOIN comment_sentiment s ON s.comment_uid=c.uid AND s.text_hash=ss_th(c.text)'
        extra = ('COALESCE(SUM(s.label=\'positiv\'),0) AS positive, COALESCE(SUM(s.label=\'negativ\'),0) AS negative, '
                 'COALESCE(SUM(s.comment_uid IS NOT NULL),0) AS analyzed, '
                 '1.0*SUM(s.label=\'positiv\')/NULLIF(SUM(s.comment_uid IS NOT NULL),0) AS positive_share, '
                 '1.0*SUM(s.label=\'negativ\')/NULLIF(SUM(s.comment_uid IS NOT NULL),0) AS negative_share')
    else:
        join = 'comments c'
        extra = 'NULL AS positive, NULL AS negative, NULL AS analyzed, NULL AS positive_share, NULL AS negative_share'
    sql = ('SELECT c.platform AS platform, ss_ak(c.author_id, c.author) AS author_key, MAX(c.author) AS display_name, '
           'COUNT(DISTINCT c.video_id) AS videos, NULL AS views, COALESCE(SUM(c.like_count),0) AS likes, '
           f'COUNT(*) AS comments, MIN({day}) AS first_activity, MAX({day}) AS last_activity, MIN(substr(replace(c.first_seen_at,\' \',\'T\'),1,19)) || \'Z\' AS first_seen_at, {extra} '
           f'FROM {join} WHERE ' + ' AND '.join(where).format(a='c') + ' GROUP BY c.platform, 2')
    return sql, args


def list_people(db, role, *, platform=None, group=None, search='', sort=None, desc=None, limit=2000, offset=0,
                first_seen_since=None, known=None):
    """Personen einer Rolle mit Kennzahlen: (Liste von Wörterbüchern, Gesamtzahl).

    group: Gruppen-Nummer, 'none' (in keiner Gruppe) oder None. search: Name oder Kennung enthält (Groß-/Kleinschreibung
    und Umlaute egal). Ersteller: videos, views, likes. Kommentarschreiber: comments, videos (unter so vielen Videos),
    likes, first_activity, last_activity und – bei vorhandenen Einstufungen – positive/negative/analyzed (KI-Einschätzung)."""
    if role not in ROLES:
        raise ValueError('Unbekannte Rolle')
    prepare(db)
    sql, args = _inner(role, srepo.has_table(db), platform)
    sorts = SORTS[role]
    sort = sort if sort in sorts else ('videos' if role == 'creator' else 'comments')
    if desc is None:
        desc = sort not in ('name', 'groups', 'platform')
    where, wargs = [], []
    table = has_table(db)
    if not table and group not in (None, 'none'):
        return [], 0
    if group == 'none' and not table:
        group = None
    if group == 'none':
        where.append('NOT EXISTS (SELECT 1 FROM group_people gp WHERE gp.role=? AND gp.platform=p.platform AND gp.author_key=p.author_key)')
        wargs.append(role)
    elif group is not None:
        where.append('EXISTS (SELECT 1 FROM group_people gp WHERE gp.group_id=? AND gp.role=? AND gp.platform=p.platform AND gp.author_key=p.author_key)')
        wargs += [int(group), role]
    if first_seen_since:   # known=False: nur neue (zum ersten Mal seit dann gesehen), known=True: nur bekannte (schon vorher gesehen)
        cut = str(first_seen_since).replace(' ', 'T')[:19]
        if known is True:
            where.append(f"{_sql_time('p.first_seen_at')} < ?")
            wargs.append(cut)
        else:
            where.append(f"{_sql_time('p.first_seen_at')} >= ?")
            wargs.append(cut)
    if (search or '').strip():
        pat = '%' + (search or '').strip().casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where.append("(cf(p.display_name) LIKE ? ESCAPE '\\' OR cf(p.author_key) LIKE ? ESCAPE '\\')")
        wargs += [pat, pat]
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    groups = ("(SELECT GROUP_CONCAT(g.name, ', ') FROM group_people gp JOIN groups g ON g.id=gp.group_id "
              "WHERE gp.role=? AND gp.platform=q.platform AND gp.author_key=q.author_key)") if table else '(SELECT NULL WHERE ?)'
    total = db.execute(f'WITH p AS ({sql}) SELECT COUNT(*) FROM p{clause}', [*args, *wargs]).fetchone()[0]
    order = sorts[sort]
    # Leere Werte immer ans Ende; weitere Schlüssel machen die Reihenfolge stabil.
    full = (f'WITH q AS ({sql}), p AS (SELECT q.*, {groups} AS group_names FROM q) SELECT p.* FROM p{clause} '
            f'ORDER BY ({order} IS NULL), {order} {"DESC" if desc else "ASC"}, ss_sort(p.display_name), p.platform, p.author_key LIMIT ? OFFSET ?')
    rows = _rows(db, full, [*args, role, *wargs, int(limit), int(offset)])
    return rows, total


def counts_by_group(db):
    """{Gruppen-Nummer: {'creators', 'commenters'}} (Zahl der zugeordneten Personen je Rolle)."""
    out = {}
    if not has_table(db):
        return out
    for gid, role, n in db.execute('SELECT group_id, role, COUNT(*) FROM group_people GROUP BY group_id, role'):
        out.setdefault(gid, {'creators': 0, 'commenters': 0})['creators' if role == 'creator' else 'commenters'] = n
    return out


def group_sentiment(db):
    """{Gruppen-Nummer: {'positiv', 'neutral', 'negativ'}}: KI-Einschätzung der Kommentare, die Kommentarschreiber der Gruppe geschrieben haben."""
    prepare(db)
    out = {}
    if not has_table(db) or not srepo.has_table(db):
        return out
    for gid, label, n in db.execute(
            "SELECT gp.group_id, s.label, COUNT(*) FROM group_people gp "
            "JOIN comments c ON c.platform=gp.platform AND ss_ak(c.author_id, c.author)=gp.author_key "
            "JOIN comment_sentiment s ON s.comment_uid=c.uid AND s.text_hash=ss_th(c.text) "
            "WHERE gp.role='commenter' GROUP BY gp.group_id, s.label"):
        out.setdefault(gid, {'positiv': 0, 'neutral': 0, 'negativ': 0})[label] = n
    return out


def commenter_member_sql(alias='c'):
    """SQL-Bedingung: Kommentar `alias` stammt von einem Kommentarschreiber der Gruppe (Parameter: Gruppen-Nummer)."""
    return (f"EXISTS (SELECT 1 FROM group_people gp WHERE gp.group_id=? AND gp.role='commenter' "
            f"AND gp.platform={alias}.platform AND gp.author_key=ss_ak({alias}.author_id, {alias}.author))")


def creator_member_sql(alias='v'):
    return (f"EXISTS (SELECT 1 FROM group_people gp WHERE gp.group_id=? AND gp.role='creator' "
            f"AND gp.platform={alias}.platform AND gp.author_key=ss_ak({alias}.author_id, {alias}.author))")
