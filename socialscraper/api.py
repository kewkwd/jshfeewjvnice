"""Lokale, nur lesende Schnittstelle für PULSE (http.server der Standardbibliothek).

Start:  handle = api.start(root)   Stopp: handle.stop()   Port: handle.port

Sicherheit: standardmäßig nur 127.0.0.1, Anmeldung per `Authorization: Bearer <Token>` (konstantzeitiger
Vergleich), CORS nur für eingetragene Herkunftsadressen, schreibgeschützte Datenbankverbindung je Anfrage,
SQL nur mit Parametern. Das Format ist in docs/PULSE-Anbindung.md beschrieben.
"""
import datetime
import hmac
import http.server
import json
import logging
import math
import pathlib
import socket
import sqlite3
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from . import integration, people_repo, repo
from . import sentiment_repo as srepo
from .util import duration_class

log = logging.getLogger('api')

SCHEMA_VERSION = 1
MAX_LIMIT = 500
DEFAULT_LIMIT = 100
MAX_QUERY_LENGTH = 2048
MAX_Q_LENGTH = 200
PLATFORMS = ('tiktok', 'instagram', 'youtube', 'reddit', 'x')
START_READ_TRIES = 5   # Dienststart: Konfiguration so oft lesen, bevor die Schnittstelle ausbleibt

VIDEO_COLUMNS = ('uid', 'platform', 'video_id', 'url', 'author', 'author_id', 'caption', 'created_at', 'duration_s',
                 'view_count', 'like_count', 'comment_count', 'share_count', 'save_count', 'hashtags', 'mentions',
                 'music', 'language', 'thumbnail', 'first_seen_at', 'fetched_at')
COMMENT_COLUMNS = ('uid', 'platform', 'video_id', 'comment_id', 'text', 'author', 'author_id', 'created_at',
                   'like_count', 'reply_count', 'parent_id', 'is_author_reply', 'language', 'first_seen_at', 'fetched_at')
VIDEO_SORTS = ('fetched_at', 'first_seen_at', 'created_at', 'view_count', 'like_count', 'comment_count', 'share_count')
COMMENT_SORTS = ('created_at', 'first_seen_at', 'like_count', 'reply_count')
_TIME_FIELDS = ('created_at', 'first_seen_at', 'fetched_at', 'captured_at', 'added_at')
LENGTH_LIMITS = {'short': 60, 'long': 600}   # Grenzen für `duration_class` (Sekunden); `start` liest sie aus den Einstellungen


def _finite(value):
    """Nicht endliche Zahlen (inf, nan) als null: JSON kennt sie nicht."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


class ApiError(RuntimeError):
    """Verständliche deutsche Meldung (z. B. Port belegt)."""


class HttpError(Exception):
    def __init__(self, status, code, message, headers=None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


# ---------- Zeiten ----------

def to_iso(value):
    """Beliebige gespeicherte Zeitangabe -> `YYYY-MM-DDTHH:MM:SSZ` (UTC). Unlesbares bleibt unverändert."""
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    try:
        moment = datetime.datetime.fromisoformat(text.replace('Z', '+00:00').replace('z', '+00:00'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        return moment.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, OverflowError):   # unlesbar oder nicht nach UTC umrechenbar (Jahr 1 / 9999 mit Zeitzone)
        return value


def sql_time(column):
    """SQL-Ausdruck, der gemischte Zeitformate vergleichbar macht (`YYYY-MM-DDTHH:MM:SS`)."""
    return f"substr(replace({column},' ','T'),1,19)"


def parse_since(text):
    if len(text) > 64:
        raise ValueError
    try:
        if len(text) == 10:
            moment = datetime.datetime.fromisoformat(text).replace(tzinfo=datetime.timezone.utc)
        else:
            moment = datetime.datetime.fromisoformat(text.replace('Z', '+00:00').replace('z', '+00:00'))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=datetime.timezone.utc)
        return moment.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    except (ValueError, OverflowError):   # auch 0001-01-01T00:00:00+05:00 (Umrechnung nach UTC läuft über)
        raise ValueError from None


# ---------- Parameter ----------

class Params:
    def __init__(self, query, allowed):
        if len(query) > MAX_QUERY_LENGTH:
            raise HttpError(400, 'bad_request', 'Die Anfrage ist zu lang.')
        try:
            parsed = parse_qs(query, keep_blank_values=True, strict_parsing=bool(query), errors='strict', max_num_fields=20)
        except ValueError:
            raise HttpError(400, 'bad_request', 'Die Parameter der Anfrage sind ungültig.') from None
        self.values = {}
        for key, values in parsed.items():
            if key not in allowed:
                raise HttpError(400, 'bad_request', f'Unbekannter Parameter „{key[:40]}“. Erlaubt: {", ".join(sorted(allowed)) or "keine"}.')
            if len(values) != 1:
                raise HttpError(400, 'bad_request', f'Der Parameter „{key}“ darf nur einmal vorkommen.')
            self.values[key] = values[0]

    def text(self, key, maxlen=MAX_Q_LENGTH):
        value = self.values.get(key)
        if value is None or value == '':
            return None
        if len(value) > maxlen or '\x00' in value:
            raise HttpError(400, 'bad_request', f'Der Parameter „{key}“ ist zu lang oder ungültig.')
        return value

    def choice(self, key, options):
        value = self.text(key, 40)
        if value is not None and value not in options:
            raise HttpError(400, 'bad_request', f'Ungültiger Wert für „{key}“. Erlaubt: {", ".join(options)}.')
        return value

    def integer(self, key, default, low, high):
        value = self.values.get(key)
        if value is None or value == '':
            return default
        try:
            number = int(value)
        except ValueError:
            raise HttpError(400, 'bad_request', f'„{key}“ muss eine ganze Zahl sein.') from None
        if not low <= number <= high:
            raise HttpError(400, 'bad_request', f'„{key}“ muss zwischen {low} und {high} liegen.')
        return number

    def since(self, required=False):
        value = self.text('since', 64)
        if value is None:
            if required:
                raise HttpError(400, 'bad_request', 'Der Parameter „since“ fehlt (Beispiel: since=2026-10-11T00:00:00Z).')
            return None
        try:
            return parse_since(value)
        except ValueError:
            raise HttpError(400, 'bad_request', '„since“ ist keine gültige Zeit (Beispiel: 2026-10-11T00:00:00Z).') from None

    def moment(self, key):
        """Eine Zeitangabe wie `since`, aber unter beliebigem Namen (z. B. `until`)."""
        value = self.text(key, 64)
        if value is None:
            return None
        try:
            return parse_since(value)
        except ValueError:
            raise HttpError(400, 'bad_request', f'„{key}“ ist keine gültige Zeit (Beispiel: 2026-10-11).') from None

    def paging(self):
        return self.integer('limit', DEFAULT_LIMIT, 1, MAX_LIMIT), self.integer('offset', 0, 0, 10_000_000)


def like_pattern(text):
    escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


# ---------- Datenzugriff ----------

def _rows(db, sql, args=()):
    cur = db.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _json_list(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return [value] if value else []
        return parsed if isinstance(parsed, list) else ([] if parsed in (None, '') else [parsed])
    return []


def _shape(row, *, raw=False):
    out = {}
    for key, value in row.items():
        if key in _TIME_FIELDS:
            value = to_iso(value)
        elif key in ('hashtags', 'mentions'):
            value = _json_list(value)
        elif key == 'is_author_reply':
            value = bool(value)
        elif key == 'raw':
            if not raw:
                continue
            try:
                value = json.loads(value) if isinstance(value, str) else value
            except ValueError:
                pass
        out[key] = value
    if 'duration_s' in out:
        out['duration_class'] = duration_class(out['duration_s'], LENGTH_LIMITS['short'], LENGTH_LIMITS['long'])
    return out


def _group_number(text):
    """Gruppenangabe als Nummer, wenn sie nur aus ASCII-Ziffern besteht und in SQLite passt; sonst None (ein Name).
    str.isdigit() allein ist auch für „²“ wahr (int() scheitert), und 20 Stellen lassen sich nicht binden."""
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 18 else None


def _video_filters(params):
    where, args = [], []
    platform = params.choice('platform', PLATFORMS)
    if platform:
        where.append('v.platform=?'); args.append(platform)
    group = params.text('group', 60)
    if group:
        if _group_number(group) is not None:
            where.append('v.uid IN (SELECT video_uid FROM group_videos WHERE group_id=?)'); args.append(_group_number(group))
        else:
            where.append('v.uid IN (SELECT gv.video_uid FROM group_videos gv JOIN groups g ON g.id=gv.group_id WHERE g.name=?)')
            args.append(group)
    q = params.text('q')
    if q:
        pat = like_pattern(q)
        where.append("(v.caption LIKE ? ESCAPE '\\' OR v.author LIKE ? ESCAPE '\\' OR v.hashtags LIKE ? ESCAPE '\\')")
        args += [pat, pat, pat]
    since = params.since()
    if since:
        where.append(sql_time('v.fetched_at') + '>=?'); args.append(since)
    return where, args


def _attach_groups(db, videos):
    if not videos:
        return
    by_uid = {v['uid']: v for v in videos}
    for v in videos:
        v['groups'] = []
    uids = list(by_uid)
    for start in range(0, len(uids), 400):
        chunk = uids[start:start + 400]
        marks = ','.join('?' for _ in chunk)
        for row in _rows(db, f'SELECT gv.video_uid, g.id, g.name, g.color FROM group_videos gv JOIN groups g ON g.id=gv.group_id '
                             f'WHERE gv.video_uid IN ({marks}) ORDER BY g.name COLLATE NOCASE', chunk):
            by_uid[row.pop('video_uid')]['groups'].append(row)


def _envelope(**body):
    return {'schema_version': SCHEMA_VERSION, **body}


def ep_stats(db, params, parts):
    q = Params(params, ('since',))
    since = q.since()
    def count(sql, args=()):
        return db.execute(sql, args).fetchone()[0]
    def by_platform(table):
        return dict(db.execute(f'SELECT platform, COUNT(*) FROM {table} GROUP BY platform').fetchall())
    last = db.execute(f"SELECT MAX({sql_time('fetched_at')}) FROM videos").fetchone()[0]
    return _envelope(
        videos={'total': count('SELECT COUNT(*) FROM videos'), 'by_platform': by_platform('videos')},
        duration=_duration_stats(db),
        comments={'total': count('SELECT COUNT(*) FROM comments'), 'by_platform': by_platform('comments')},
        snapshots=count('SELECT COUNT(*) FROM video_snapshots'),
        groups=count('SELECT COUNT(*) FROM groups'),
        accounts=count('SELECT COUNT(*) FROM accounts'),
        last_fetched_at=to_iso(last), **_new_authors(db, since))


def _duration_stats(db, where='', args=()):
    """Ø, kürzeste und längste Videolänge (Sekunden, nur gemessene Werte) und wie viele Videos gar keine Länge haben."""
    row = db.execute(f'SELECT AVG(v.duration_s), MIN(v.duration_s), MAX(v.duration_s), COUNT(v.duration_s), COUNT(*) '
                     f'FROM videos v{where}', args).fetchone()
    avg = None if row[0] is None else round(row[0], 1)
    return _finite({'avg_s': avg, 'min_s': row[1], 'max_s': row[2], 'measured': row[3], 'unmeasured': row[4] - row[3]})


def _new_authors(db, since):
    """Mit `since`: Zahl der Ersteller und Kommentarschreiber, die zum ersten Mal seit dann in der Datenbank gesehen wurden."""
    if not since:
        return {}
    return {'new_authors': {'since': to_iso(since + 'Z'), 'creators': people_repo.count_new(db, 'creator', since),
                            'commenters': people_repo.count_new(db, 'commenter', since)}}


def ep_videos(db, query, parts):
    params = Params(query, ('platform', 'group', 'q', 'since', 'limit', 'offset', 'sort', 'order'))
    where, args = _video_filters(params)
    limit, offset = params.paging()
    sort = params.choice('sort', VIDEO_SORTS) or 'fetched_at'
    order = params.choice('order', ('asc', 'desc')) or 'desc'
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = db.execute(f'SELECT COUNT(*) FROM videos v{clause}', args).fetchone()[0]
    sort_expr = sql_time(f'v.{sort}') if sort.endswith('_at') else f'v.{sort}'
    cols = ', '.join(f'v.{c}' for c in VIDEO_COLUMNS)
    rows = _rows(db, f'SELECT {cols} FROM videos v{clause} ORDER BY {sort_expr} {order.upper()}, v.uid LIMIT ? OFFSET ?',
                 [*args, limit, offset])
    items = [_shape(r) for r in rows]
    _attach_groups(db, items)
    return _envelope(items=items, total=total, limit=limit, offset=offset)


def _find_video(db, uid):
    row = _rows(db, 'SELECT * FROM videos WHERE uid=?', (uid,))
    if not row:
        raise HttpError(404, 'not_found', 'Dieses Video gibt es nicht.')
    return row[0]


def ep_video(db, query, parts):
    params = Params(query, ('include',))
    include = params.choice('include', ('raw',))
    row = _find_video(db, parts[2])
    item = _shape(row, raw=include == 'raw')
    item = {k: v for k, v in item.items() if k in VIDEO_COLUMNS or k in ('raw', 'duration_class')}
    _attach_groups(db, [item])
    return _envelope(item=item)


def _group_id(db, text):
    """Gruppe nach Nummer oder Name; -1, wenn es sie nicht gibt (ergibt dann ein leeres Ergebnis)."""
    number = _group_number(text)
    if number is not None and db.execute('SELECT 1 FROM groups WHERE id=?', (number,)).fetchone():
        return number
    row = db.execute('SELECT id FROM groups WHERE name=?', (text,)).fetchone()   # auch ein Name aus Ziffern
    return row[0] if row else -1


def _commenter_filter(db, params, where, args, alias):
    """Filter `commenter_group`: nur Kommentare von Kommentarschreibern dieser Gruppe."""
    group = params.text('commenter_group', 60)
    if group:
        people_repo.prepare(db)
        if people_repo.has_table(db):
            where.append(people_repo.commenter_member_sql(alias)); args.append(_group_id(db, group))
        else:
            where.append('0')


def _sentiment_item(label):
    return {'label': label['label'], 'score': label['score'], 'model': label['model'], 'prompt_version': label['prompt_version'],
            'analyzed_at': to_iso(label['analyzed_at']), 'kind': srepo.KIND}


def _attach_sentiment(db, items):
    """Jeder Kommentar bekommt `sentiment`: die gültige KI-Einschätzung oder null."""
    labels = srepo.labels_for(db, [{'uid': i['uid'], 'text': i.get('text')} for i in items])
    for item in items:
        item['sentiment'] = _sentiment_item(labels[item['uid']]) if item['uid'] in labels else None


def ep_video_comments(db, query, parts):
    params = Params(query, ('q', 'since', 'limit', 'offset', 'sort', 'order', 'parent', 'commenter_group'))
    video = _find_video(db, parts[2])
    where, args = ['platform=?', 'video_id=?'], [video['platform'], video['video_id']]
    _commenter_filter(db, params, where, args, 'comments')
    q = params.text('q')
    if q:
        where.append("text LIKE ? ESCAPE '\\'"); args.append(like_pattern(q))
    since = params.since()
    if since:
        where.append(sql_time('first_seen_at') + '>=?'); args.append(since)
    parent = params.text('parent', 200)
    if parent == 'none':
        where.append('(parent_id IS NULL OR parent_id=\'\')')
    elif parent:
        where.append('parent_id=?'); args.append(parent)
    limit, offset = params.paging()
    sort = params.choice('sort', COMMENT_SORTS) or 'created_at'
    order = params.choice('order', ('asc', 'desc')) or 'desc'
    sort_expr = sql_time(sort) if sort.endswith('_at') else sort
    clause = ' WHERE ' + ' AND '.join(where)
    total = db.execute(f'SELECT COUNT(*) FROM comments{clause}', args).fetchone()[0]
    rows = _rows(db, f'SELECT {", ".join(COMMENT_COLUMNS)} FROM comments{clause} ORDER BY {sort_expr} {order.upper()}, uid '
                     f'LIMIT ? OFFSET ?', [*args, limit, offset])
    items = [_shape(r) for r in rows]
    _attach_sentiment(db, items)
    return _envelope(video_uid=video['uid'], items=items, total=total, limit=limit, offset=offset)


def ep_video_history(db, query, parts):
    params = Params(query, ('since', 'limit', 'offset'))
    video = _find_video(db, parts[2])
    where, args = ['video_uid=?'], [video['uid']]
    since = params.since()
    if since:
        where.append(sql_time('captured_at') + '>=?'); args.append(since)
    limit, offset = params.paging()
    clause = ' WHERE ' + ' AND '.join(where)
    total = db.execute(f'SELECT COUNT(*) FROM video_snapshots{clause}', args).fetchone()[0]
    rows = _rows(db, f'SELECT captured_at, view_count, like_count, comment_count, share_count FROM video_snapshots{clause} '
                     f'ORDER BY {sql_time("captured_at")}, captured_at LIMIT ? OFFSET ?', [*args, limit, offset])
    return _envelope(video_uid=video['uid'], items=[_shape(r) for r in rows], total=total, limit=limit, offset=offset)


def ep_comments_new(db, query, parts):
    params = Params(query, ('since', 'platform', 'limit', 'offset', 'commenter_group'))
    since = params.since(required=True)
    where, args = [sql_time('c.first_seen_at') + '>=?'], [since]
    _commenter_filter(db, params, where, args, 'c')
    platform = params.choice('platform', PLATFORMS)
    if platform:
        where.append('c.platform=?'); args.append(platform)
    limit, offset = params.paging()
    clause = ' WHERE ' + ' AND '.join(where)
    total = db.execute(f'SELECT COUNT(*) FROM comments c{clause}', args).fetchone()[0]
    cols = ', '.join(f'c.{c}' for c in COMMENT_COLUMNS)
    rows = _rows(db, f'SELECT {cols}, v.uid AS video_uid, v.url AS video_url, v.author AS video_author FROM comments c '
                     f"LEFT JOIN videos v ON v.uid = c.platform || ':' || c.video_id{clause} "
                     f'ORDER BY {sql_time("c.first_seen_at")} DESC, c.uid LIMIT ? OFFSET ?', [*args, limit, offset])
    items = []
    for r in rows:
        item = _shape(r)
        if item.get('video_uid') is None:
            item['video_uid'] = f"{item['platform']}:{item['video_id']}"
        items.append(item)
    _attach_sentiment(db, items)
    first = people_repo.first_seen_map(db, 'commenter', [(i['platform'], people_repo.author_key(i.get('author_id'), i.get('author'))) for i in items])
    for item in items:   # Autor vor `since` noch nie gesehen?
        seen = first.get((item['platform'], people_repo.author_key(item.get('author_id'), item.get('author'))))
        item['author_new'] = bool(seen) and seen.replace(' ', 'T')[:19] >= since
    return _envelope(since=to_iso(since + 'Z'), items=items, total=total, limit=limit, offset=offset)


def ep_groups(db, query, parts):
    Params(query, ())
    people = people_repo.counts_by_group(db)
    stats = {s['id']: s for s in repo.group_stats(db)}
    items = [{**g, 'created_at': to_iso(g['created_at']), 'creator_count': people.get(g['id'], {}).get('creators', 0),
              'commenter_count': people.get(g['id'], {}).get('commenters', 0),
              'duration': _finite({'avg_s': None if stats.get(g['id'], {}).get('avg_duration') is None
                                   else round(stats[g['id']]['avg_duration'], 1),
                                   'min_s': stats.get(g['id'], {}).get('min_duration'),
                                   'max_s': stats.get(g['id'], {}).get('max_duration')})}
             for g in repo.list_groups(db)]
    return _envelope(items=items, total=len(items))


def ep_channels(db, query, parts):
    """Ganze Kanäle mit Fortschritt der Warteschlange. Nie Proxy, Konto-Zugangsdaten oder Schlüssel."""
    Params(query, ())
    try:
        rows = repo.list_channels(db)
    except sqlite3.OperationalError as error:
        if 'no such' not in str(error).lower():
            raise
        rows = []
    if len(parts) == 3:
        rows = [r for r in rows if str(r['id']) == parts[2]]
        if not rows:
            raise HttpError(404, 'not_found', 'Diesen Kanal gibt es nicht.')
    items = []
    for r in rows:
        p = r['progress']
        items.append({
            'id': r['id'], 'platform': r['platform'], 'channel': r['value'], 'name': r.get('display_name'),
            'state': r.get('backfill_state'), 'enabled': bool(r['enabled']), 'pace_profile': r.get('pace_profile'),
            'scope': r.get('scope'), 'scope_value': r.get('scope_value'), 'group': r.get('group_name'),
            'progress': {'total': p['total'], 'done': p['done'], 'skipped': p['skipped'], 'pending': p['pending'],
                         'running': p['running'], 'failed': p['failed']},
            'platform_total': r.get('platform_total'), 'list_complete': bool(r.get('list_done')), 'note': r.get('list_note'),
            'paused_until': to_iso(r['pause_until']) if r.get('backfill_state') == 'paused' and r.get('pause_until') else None,
            'pause_reason': r.get('pause_reason') if r.get('backfill_state') == 'paused' else None,
            'next_run_at': to_iso(r['next_run_at']) if r.get('next_run_at') and r['enabled'] else None,
            'last_run_at': to_iso(r['last_run_at']) if r.get('last_run_at') else None,
            'observe_new': bool(r.get('observe')), 'quiet_hours': r.get('quiet_hours'),
            'created_at': to_iso(r['created_at'])})
    if len(parts) == 3:
        return _envelope(item=items[0])
    return _envelope(items=items, total=len(items))


PEOPLE_SORTS = ('name', 'videos', 'views', 'likes', 'comments', 'first', 'last', 'positive', 'negative', 'platform', 'seen')


def _person_item(db, row, since=None):
    role = row.get('_role') or row['role']
    groups = people_repo.groups_of_person(db, role, row['platform'], row['author_key'])
    seen = to_iso(row.get('first_seen_at'))
    item = {'role': role, 'platform': row['platform'], 'author_key': row['author_key'], 'display_name': row['display_name'],
            'first_seen_at': seen, 'is_new': (bool(seen) and seen[:19] >= since[:19]) if since else None,
            'videos': row['videos'], 'likes': row['likes'], 'groups': [{'id': g['id'], 'name': g['name'], 'color': g['color']} for g in groups]}
    if role == 'creator':
        item['views'] = row['views']
    else:
        item.update(comments=row['comments'], first_activity=row['first_activity'], last_activity=row['last_activity'],
                    sentiment={'kind': srepo.KIND, 'analyzed': row['analyzed'] or 0, 'positive': row['positive'] or 0,
                               'negative': row['negative'] or 0,
                               'neutral': (row['analyzed'] or 0) - (row['positive'] or 0) - (row['negative'] or 0)})
    return item


def ep_people(db, query, parts):
    params = Params(query, ('role', 'platform', 'group', 'q', 'limit', 'offset', 'sort', 'order', 'first_seen_since', 'known'))
    role = params.choice('role', people_repo.ROLES)
    first_seen_since = params.moment('first_seen_since')
    known = params.choice('known', ('true', 'false'))
    if known and not first_seen_since:
        raise HttpError(400, 'bad_request', 'Der Parameter „known“ braucht „first_seen_since“ (ab wann gilt jemand als neu?).')
    known = None if known is None else known == 'true'
    platform = params.choice('platform', PLATFORMS)
    group = params.text('group', 60)
    q = params.text('q') or ''
    sort = params.choice('sort', PEOPLE_SORTS)
    order = params.choice('order', ('asc', 'desc'))
    limit, offset = params.paging()
    people_repo.prepare(db)
    gid = None
    if group:
        gid = 'none' if group == 'none' else _group_id(db, group)
    desc = None if order is None else order == 'desc'
    roles = [role] if role else list(people_repo.ROLES)
    if len(roles) == 1:
        rows, total = people_repo.list_people(db, role, platform=platform, group=gid, search=q, sort=sort, desc=desc,
                                              limit=limit, offset=offset, first_seen_since=first_seen_since if known is not None else None, known=known)
        rows = [dict(r, _role=role) for r in rows]
    else:   # beide Rollen: zusammenführen, gemeinsam sortieren, dann die Seite schneiden
        merged, total = [], 0
        for r in roles:
            part, count = people_repo.list_people(db, r, platform=platform, group=gid, search=q, sort=sort, desc=desc,
                                                  limit=offset + limit, first_seen_since=first_seen_since if known is not None else None, known=known)
            merged += [dict(x, _role=r) for x in part]
            total += count
        if sort in ('name', 'platform'):
            key = {'name': lambda x: (x['display_name'] or '').casefold(), 'platform': lambda x: x['platform']}[sort]
            merged.sort(key=key, reverse=bool(desc))
        else:   # Kennzahl oder Zeitpunkt: Feldnamen wie in people_repo, Zeiten in einer Schreibweise, ohne Wert ans Ende
            field = {'first': 'first_activity', 'last': 'last_activity', 'seen': 'first_seen_at',
                     'positive': 'positive_share', 'negative': 'negative_share'}.get(sort, sort or 'videos')

            def value(x):
                v = x.get(field)
                return v.replace(' ', 'T')[:19] if isinstance(v, str) else v
            with_value = sorted((x for x in merged if value(x) is not None), key=value, reverse=desc is None or desc)
            merged = with_value + [x for x in merged if value(x) is None]
        rows = merged[offset:offset + limit]
    return _envelope(items=[_person_item(db, r, first_seen_since) for r in rows], total=total, limit=limit, offset=offset,
                     first_seen_since=to_iso(first_seen_since + 'Z') if first_seen_since else None)


def ep_group_people(db, query, parts):
    params = Params(query, ('role', 'limit', 'offset'))
    role = params.choice('role', people_repo.ROLES)
    limit, offset = params.paging()
    number = _group_number(parts[2])
    group = db.execute('SELECT id, name FROM groups WHERE id=?', (number,)).fetchone() if number is not None else None
    if group is None:
        group = db.execute('SELECT id, name FROM groups WHERE name=?', (parts[2],)).fetchone()
    if group is None:
        raise HttpError(404, 'not_found', 'Diese Gruppe gibt es nicht.')
    members = people_repo.group_members(db, group[0], role) if people_repo.has_table(db) else []
    items = [{'role': m['role'], 'platform': m['platform'], 'author_key': m['author_key'], 'display_name': m['display_name'],
              'added_at': to_iso(m['added_at'])} for m in members[offset:offset + limit]]
    return _envelope(group={'id': group[0], 'name': group[1]}, items=items, total=len(members), limit=limit, offset=offset)


def ep_sentiment(db, query, parts):
    params = Params(query, ('platform', 'group', 'video', 'since', 'until', 'interval'))
    interval = params.choice('interval', ('day', 'week')) or 'day'
    scope = {}
    platform = params.choice('platform', PLATFORMS)
    if platform:
        scope['platform'] = platform
    group = params.text('group', 60)
    if group:
        scope['group'] = _group_id(db, group)
    video = params.text('video', 200)
    if video:
        scope['videos'] = [_find_video(db, video)['uid']]
    since, until = params.since(), params.moment('until')
    if since:
        scope['since'] = since[:10]
    if until:
        scope['until'] = until[:10]
    dist = srepo.distribution(db, scope)
    series = srepo.series(db, scope, interval)
    return _envelope(kind=srepo.KIND, note=srepo.NOTE, model=dist['model'], models=dist['models'],
                     prompt_versions=dist['prompt_versions'], positive=dist['positive'], neutral=dist['neutral'],
                     negative=dist['negative'], analyzed=dist['analyzed'], total_comments=dist['total_comments'],
                     coverage=dist['coverage'], share=dist['share'], score_avg=dist['score_avg'], interval=interval,
                     scope={'platform': platform, 'group': group, 'video': video, 'since': scope.get('since'),
                            'until': scope.get('until')}, series=series)


def ep_accounts(db, query, parts):
    Params(query, ())
    from . import rotation
    status = {row['id']: row for row in rotation.rotation_status(db)}
    items = []
    for a in repo.list_accounts(db):
        s = status[a['id']]
        # Rotationsstatus ja, den Proxy NIE (weder Adresse noch Zugangsdaten) – nur, dass einer festgelegt ist.
        items.append({'id': a['id'], 'platform': a['platform'], 'label': a['label'], 'identity': a['identity'],
                      'country': a['country'], 'is_default': bool(a['is_default']), 'note': a['note'],
                      'created_at': to_iso(a['created_at']),
                      'rotation_enabled': s['in_rotation'], 'status': s['state'],
                      'cooldown_until': to_iso(s['until']) if s['state'] == 'abkuehlung' else None,
                      'cooldown_reason': s['cooldown_reason'], 'uses_today': s['used_today'],
                      'daily_limit': s['daily_limit'], 'use_count': s['use_count'],
                      'last_used_at': to_iso(s['last_used_at']) if s['last_used_at'] else None,
                      'has_proxy': s['has_proxy']})
    return _envelope(items=items, total=len(items))


# Zuordnung: Pfadteile (nach /v1) -> Funktion. `uid` steht an Position 2 (URL-dekodiert).
def route(parts):
    n = len(parts)
    if n == 2 and parts[1] == 'stats': return ep_stats
    if n == 2 and parts[1] == 'videos': return ep_videos
    if n == 3 and parts[1] == 'videos': return ep_video
    if n == 4 and parts[1] == 'videos' and parts[3] == 'comments': return ep_video_comments
    if n == 4 and parts[1] == 'videos' and parts[3] == 'history': return ep_video_history
    if n == 3 and parts[1] == 'comments' and parts[2] == 'new': return ep_comments_new
    if n == 2 and parts[1] == 'groups': return ep_groups
    if n == 4 and parts[1] == 'groups' and parts[3] == 'people': return ep_group_people
    if n == 2 and parts[1] == 'people': return ep_people
    if n == 2 and parts[1] == 'sentiment': return ep_sentiment
    if n == 2 and parts[1] == 'accounts': return ep_accounts
    if n in (2, 3) and parts[1] == 'channels': return ep_channels
    return None


# ---------- HTTP ----------

class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False   # Unter Windows würde sonst ein zweiter Server denselben Port mitbenutzen.
    request_queue_size = 32
    max_connections = 64   # gleichzeitig offene Verbindungen (je ein Thread); weitere werden sofort geschlossen

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        log.debug('Verbindungsfehler von %s', client_address[0] if client_address else '?')


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'SocialscraperAPI'
    sys_version = ''
    protocol_version = 'HTTP/1.1'
    timeout = 5   # Leerlauf pro Verbindung (auch zwischen zwei Anfragen): hängende Clients halten keinen Thread fest

    # --- Hilfen ---
    @property
    def ctx(self):
        return self.server.ctx

    def log_message(self, fmt, *args):
        pass   # Kein Zugriffsprotokoll: es würde Adressen/Parameter enthalten. Fehler gehen über log.

    def _cors_headers(self):
        origin = self.headers.get('Origin')
        if origin and integration.normalize_origin(origin) in self.ctx['origins']:
            return {'Access-Control-Allow-Origin': origin, 'Vary': 'Origin'}
        return {'Vary': 'Origin'}

    def _send(self, status, body=None, headers=None):
        if body is None:
            payload = b''
        else:
            try:
                payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            except ValueError:   # inf/nan in einer Zahlenspalte: als null ausgeben statt 500
                payload = json.dumps(_finite(body), ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        if body is not None:
            self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in {**self._cors_headers(), **(headers or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if payload and self.command != 'HEAD':
            self.wfile.write(payload)

    def _error(self, status, code, message, headers=None):
        self._send(status, {'schema_version': SCHEMA_VERSION, 'error': {'code': code, 'message': message}}, headers)

    def _host_ok(self):
        if not integration.is_loopback(self.ctx['host']):
            return True
        host = (self.headers.get('Host') or '').strip().lower()
        name = host.rsplit(':', 1)[0] if not host.startswith('[') else host.split(']')[0] + ']'
        return integration.is_loopback(name)

    def _authorized(self):
        header = self.headers.get('Authorization') or ''
        scheme, _, given = header.partition(' ')
        if scheme.lower() != 'bearer' or not given.strip():
            return False
        return hmac.compare_digest(given.strip().encode('utf-8'), self.ctx['token'].encode('utf-8'))

    # --- Methoden ---
    def do_OPTIONS(self):
        origin = self.headers.get('Origin')
        if origin and integration.normalize_origin(origin) in self.ctx['origins'] and self.headers.get('Access-Control-Request-Method'):
            self._send(204, None, {'Access-Control-Allow-Methods': 'GET, OPTIONS',
                                   'Access-Control-Allow-Headers': 'Authorization, Content-Type',
                                   'Access-Control-Max-Age': '600'})
        elif origin:
            self._error(403, 'forbidden_origin', 'Diese Herkunftsadresse ist nicht freigegeben.')
        else:
            self._send(204, None, {'Allow': 'GET, OPTIONS'})

    def do_GET(self):
        try:
            self._get()
        except HttpError as error:
            self._error(error.status, error.code, error.message, error.headers)
        except (BrokenPipeError, ConnectionError, TimeoutError):
            self.close_connection = True
        except sqlite3.OperationalError as error:
            text = str(error).lower()
            if 'locked' in text or 'busy' in text:
                self._error(503, 'db_busy', 'Die Datenbank ist gerade beschäftigt. Bitte gleich noch einmal versuchen.', {'Retry-After': '2'})
            elif 'readonly' in text or 'unable to open' in text or 'disk i/o' in text:
                # z. B. offenes Journal nach einem Absturz des Schreibers: ein Lesezugriff kann es nicht aufräumen
                self._error(503, 'db_unreadable', 'Die Datenbank ist gerade nicht lesbar (ein Schreibvorgang wurde unterbrochen). '
                                                  'Starte Socialscraper einmal oder versuche es gleich noch einmal.', {'Retry-After': '5'})
            elif 'no such' in text:
                self._error(503, 'db_old', 'Die Datenbank hat noch nicht das neue Format. Starte Socialscraper einmal, dann klappt es.')
            else:
                log.error('Datenbankfehler: %s', error)
                self._error(500, 'internal', 'Interner Fehler beim Lesen der Datenbank.')
        except Exception:   # niemals Stacktrace nach außen
            log.exception('Unerwarteter Fehler in der Schnittstelle')
            try:
                self._error(500, 'internal', 'Interner Fehler.')
            except OSError:
                pass

    def _method_not_allowed(self):
        self.close_connection = True   # ein evtl. mitgeschickter Inhalt wird nicht gelesen
        self._error(405, 'method_not_allowed', 'Diese Schnittstelle erlaubt nur lesende Anfragen (GET).',
                    {'Allow': 'GET, OPTIONS', 'Connection': 'close'})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _method_not_allowed

    def _get(self):
        if not self._host_ok():
            raise HttpError(403, 'bad_host', 'Ungültige Host-Angabe.')
        raw = self.path
        if len(raw) > MAX_QUERY_LENGTH + 512 or not raw.startswith('/'):
            raise HttpError(400, 'bad_request', 'Ungültige Anfrage.')
        split = urlsplit(raw)
        segments = split.path.split('/')[1:]   # roh teilen, erst danach dekodieren ("%2F" bleibt Teil der uid)
        if segments and segments[-1] == '' and len(segments) > 1:
            segments.pop()
        try:
            parts = [unquote(s, errors='strict') for s in segments]
        except UnicodeDecodeError:
            raise HttpError(400, 'bad_request', 'Ungültige Zeichen im Pfad.') from None
        if not parts or parts[0] != 'v1' or any('\x00' in p for p in parts):
            raise HttpError(404, 'not_found', 'Diese Adresse gibt es nicht. Die Schnittstelle liegt unter /v1/.')
        if parts == ['v1', 'health']:
            Params(split.query, ())
            self._send(200, _envelope(ok=True, service='socialscraper', authenticated=self._authorized(),
                                      server_time=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                      database=self.ctx['db_path'].exists()))
            return
        handler = route(parts)
        if not self._authorized():
            raise HttpError(401, 'unauthorized', 'Anmeldung fehlt oder ist falsch. Sende den Token im Header „Authorization: Bearer …“.',
                            {'WWW-Authenticate': 'Bearer realm="socialscraper"'})
        if handler is None:
            raise HttpError(404, 'not_found', 'Diese Adresse gibt es nicht.')
        db_file = self.ctx['db_path']
        if not db_file.exists():
            raise HttpError(503, 'no_database', 'Es gibt noch keine Datenbank. Rufe zuerst ein Video ab.')
        uri = f'{db_file.resolve().as_uri()}?mode=ro'
        db = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            body = handler(db, split.query, parts)
        finally:
            db.close()
        self._send(200, body)


# ---------- Start / Stopp ----------

class ApiHandle:
    def __init__(self, server, thread, host, port):
        self._server, self._thread = server, thread
        self.host, self.port = host, port

    @property
    def url(self):
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'http://{host}:{self.port}'

    @property
    def running(self):
        return self._thread.is_alive()

    def stop(self, timeout=5):
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()
        self._thread.join(timeout)


def start(root=None, *, db_path=None, host=None, port=None, cfg=None, token=None):
    """Startet die Schnittstelle im Hintergrund-Thread und gibt ein Handle zurück.

    Werte aus `integration.json` (api.host/port/cors_origins/token); `host`, `port`, `token` überschreiben sie.
    `port=0` wählt einen freien Port (Tests). Fehlt der Token, wird nicht gestartet.
    """
    cfg = cfg or integration.load(root)
    api_cfg = cfg['api']
    try:   # Grenzen für „kurz/mittel/lang“ aus den Einstellungen (Standard 60 und 600 Sekunden)
        from . import scheduler
        settings = scheduler.load_config(root or '.')
        LENGTH_LIMITS.update(short=settings.length_short_s, long=settings.length_long_s)
    except Exception:
        pass
    token = token if token is not None else api_cfg['token']
    if not isinstance(token, str) or len(token) < integration.MIN_TOKEN_LENGTH:
        raise ApiError('Die Schnittstelle braucht einen Token (mindestens 24 Zeichen). Erzeuge ihn in den Einstellungen.')
    host = (host or api_cfg['host']).strip().strip('[]')
    port = api_cfg['port'] if port is None else port
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ApiError('Der Port ist ungültig.')
    ctx = {'token': token, 'host': host,
           'db_path': pathlib.Path(db_path) if db_path else integration.db_path(root),
           'origins': {integration.normalize_origin(o) for o in api_cfg['cors_origins']}}

    class Server(_Server):
        address_family = socket.AF_INET6 if ':' in host else socket.AF_INET

    try:
        server = Server((host, port), _Handler)
    except OSError as error:
        if getattr(error, 'winerror', None) in (10048, 10013) or error.errno in (98, 48, 10048, 10013):
            raise ApiError(f'Der Port {port} ist schon belegt (läuft die Schnittstelle schon, oder nutzt ein anderes Programm den Port?). '
                           'Wähle in den Einstellungen einen anderen Port.') from None
        raise ApiError(f'Die Schnittstelle konnte nicht starten ({error.strerror or "Fehler"}). Adresse {host}, Port {port}.') from None
    server.ctx = ctx
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.2}, name='socialscraper-api', daemon=True)
    thread.start()
    handle = ApiHandle(server, thread, host, server.server_address[1])
    log.info('Schnittstelle läuft auf %s', handle.url)
    return handle


def start_if_enabled(root=None, *, db_path=None):
    """Für Hintergrunddienst/Fenster: startet nur, wenn aktiviert. Gibt Handle oder None zurück; wirft nie.

    Ein Problem steht im Protokoll; `last_error` meldet es zusätzlich als zweiter Rückgabewert.
    """
    try:
        for attempt in range(START_READ_TRIES):
            cfg, problem = integration.load_with_status(root)
            if not problem or 'nicht lesen' not in problem or attempt == START_READ_TRIES - 1:
                break
            log.warning('%s Neuer Versuch …', problem)
            time.sleep(0.5)
        if problem:
            log.warning('Schnittstelle nicht gestartet: %s', problem)
            return None, problem
        if not cfg['api']['enabled']:
            return None, None
        return start(root, db_path=db_path, cfg=cfg), None
    except ApiError as error:
        log.warning('%s', error)
        return None, str(error)
    except Exception as error:
        log.warning('Schnittstelle nicht gestartet: %s', type(error).__name__)
        return None, 'Die Schnittstelle konnte nicht gestartet werden.'
