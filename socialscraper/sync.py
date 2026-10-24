"""Gehosteter Sync: lokale Datenbank <-> Cloud-Datenbank (Supabase / PostgREST) über HTTPS.

Nur Standardbibliothek (urllib), keine nativen Treiber. Push ist idempotent (Upsert mit
`Prefer: resolution=merge-duplicates` und `on_conflict`), schickt nur Zeilen seit dem letzten Stand
(Wasserstand je Tabelle in `data/sync-state.json`) in Paketen und wiederholt bei Netzfehlern/429/5xx.
Pull führt Cloud-Zeilen lokal zusammen (`first_seen_at` bleibt, die neuere `fetched_at` gewinnt).

Schlüssel werden nie protokolliert und nie in Fehlermeldungen übernommen.
Öffentliche Funktionen geben Ergebnis-Wörterbücher zurück (`ok`, `error`, `counts`, …) und werfen nicht.
"""
import hashlib
import http.client
import json
import logging
import os
import pathlib
import socket
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from . import integration
from .store import Store

log = logging.getLogger('sync')

BATCH = 500
MAX_ATTEMPTS = 4
RETRY_BASE = 1.0          # Sekunden; 1, 2, 4 …
RETRY_AFTER_MAX = 30.0
TIMEOUT = 30
STATE_NAME = 'sync-state.json'
_LOCK = threading.Lock()


class SyncError(RuntimeError):
    """Verständliche deutsche Meldung; `retryable` für Netz-/Lastfehler."""

    def __init__(self, message, kind='error', status=None):
        super().__init__(message)
        self.kind = kind
        self.status = status   # HTTP-Status der Cloud, falls es einen gab


def _sql_time(column):
    return f"substr(replace({column},' ','T'),1,19)"


def _norm(value):
    """Zeit -> `YYYY-MM-DDTHH:MM:SSZ` (UTC) für die Cloud."""
    from .api import to_iso
    return to_iso(value)


def _wm(value):
    """Zeit -> Wasserstand-Format `YYYY-MM-DDTHH:MM:SS` (vergleichbar mit _sql_time)."""
    n = _norm(value)
    return n[:19] if isinstance(n, str) else None


# Tabellenbeschreibung. `cols` = Spalten in der Cloud, `json` = lokal als JSON-Text gespeichert,
# `times` = Zeitspalten (für die Cloud normalisiert), `wm` = Spalte für den Wasserstand.
TABLES = {
    'videos': dict(key=('uid',), wm='fetched_at', json=('hashtags', 'mentions', 'raw'), times=('fetched_at', 'first_seen_at'),
                   cols=('uid', 'platform', 'video_id', 'url', 'author', 'author_id', 'caption', 'created_at', 'duration_s',
                         'view_count', 'like_count', 'comment_count', 'share_count', 'save_count', 'hashtags', 'mentions',
                         'music', 'language', 'thumbnail', 'raw', 'first_seen_at', 'fetched_at')),
    'comments': dict(key=('uid',), wm='fetched_at', json=('raw',), times=('fetched_at', 'first_seen_at'),
                     cols=('uid', 'platform', 'video_id', 'comment_id', 'text', 'author', 'author_id', 'created_at', 'like_count',
                           'reply_count', 'parent_id', 'is_author_reply', 'language', 'raw', 'first_seen_at', 'fetched_at')),
    'video_snapshots': dict(key=('video_uid', 'captured_at'), wm='captured_at', json=(), times=('captured_at',),
                            cols=('video_uid', 'captured_at', 'view_count', 'like_count', 'comment_count', 'share_count')),
    'groups': dict(key=('name',), wm=None, json=(), times=('created_at',),
                   cols=('name', 'color', 'note', 'created_at')),
    'group_videos': dict(key=('group_name', 'video_uid'), wm='added_at', json=(), times=('added_at',),
                         cols=('group_name', 'video_uid', 'added_at')),
    # KI-Einschätzung der Stimmung je Kommentar (getrennt von comments)
    'comment_sentiment': dict(key=('comment_uid',), wm='analyzed_at', json=(), times=('analyzed_at',),
                              cols=('comment_uid', 'label', 'score', 'model', 'prompt_version', 'analyzed_at', 'text_hash')),
    # Personen (Ersteller/Kommentarschreiber) in Gruppen; Gruppen sind über ihren Namen verbunden
    'group_people': dict(key=('group_name', 'role', 'platform', 'author_key'), wm='added_at', json=(), times=('added_at',),
                         cols=('group_name', 'role', 'platform', 'author_key', 'display_name', 'added_at')),
}
ORDER = ('groups', 'videos', 'comments', 'video_snapshots', 'group_videos', 'comment_sentiment', 'group_people')
# Neuere Tabellen: fehlen sie in einer älteren Cloud-Datenbank (Schema noch nicht erneut ausgeführt), werden sie
# übersprungen und gemeldet, statt den ganzen Abgleich scheitern zu lassen.
OPTIONAL = ('comment_sentiment', 'group_people')

_SELECT = {
    'videos': 'SELECT {cols}{extra} FROM videos',
    'comments': 'SELECT {cols}{extra} FROM comments',
    'video_snapshots': 'SELECT {cols}{extra} FROM video_snapshots',
    'groups': 'SELECT {cols}{extra} FROM groups',
    'group_videos': 'SELECT g.name AS group_name, gv.video_uid AS video_uid, gv.added_at AS added_at{extra} FROM group_videos gv '
                    'JOIN groups g ON g.id=gv.group_id',
    'comment_sentiment': 'SELECT {cols}{extra} FROM comment_sentiment',
    'group_people': 'SELECT g.name AS group_name, gp.role AS role, gp.platform AS platform, gp.author_key AS author_key, '
                    'gp.display_name AS display_name, gp.added_at AS added_at{extra} FROM group_people gp JOIN groups g ON g.id=gp.group_id',
}
_WM_EXPR = {'videos': 'fetched_at', 'comments': 'fetched_at', 'video_snapshots': 'captured_at', 'group_videos': 'gv.added_at',
            'comment_sentiment': 'analyzed_at', 'group_people': 'gp.added_at'}
# Schlüsselspalten hinter dem Wasserstand: (Name im Ergebnis, SQL-Ausdruck). Zusammen eindeutig -> Keyset-Paging.
_KEYSET = {'videos': (('uid', 'uid'),), 'comments': (('uid', 'uid'),),
           'video_snapshots': (('video_uid', 'video_uid'), ('captured_at', 'captured_at')),
           'groups': (('name', 'name'),), 'group_videos': (('group_name', 'g.name'), ('video_uid', 'gv.video_uid')),
           'comment_sentiment': (('comment_uid', 'comment_uid'),),
           'group_people': (('group_name', 'g.name'), ('role', 'gp.role'), ('platform', 'gp.platform'), ('author_key', 'gp.author_key'))}


# ---------- Zustandsdatei (Wasserstände) ----------

def _state_path(root):
    return integration.resolve_root(root) / 'data' / STATE_NAME


def _fingerprint(url):
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]


def load_state(root, url):
    try:
        data = json.loads(_state_path(root).read_text(encoding='utf-8'))
        if isinstance(data, dict) and data.get('cloud') == _fingerprint(url):
            return {'cloud': data['cloud'], 'push': dict(data.get('push') or {}), 'pull': dict(data.get('pull') or {})}
    except (OSError, ValueError, TypeError):
        pass
    return {'cloud': _fingerprint(url), 'push': {}, 'pull': {}}


def save_state(root, state):
    p = _state_path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f'{STATE_NAME}.{os.getpid()}.{threading.get_ident()}.tmp')
        tmp.write_text(json.dumps(state, indent=1), encoding='utf-8')
        integration._replace(tmp, p)
    except OSError as error:
        log.warning('Sync-Stand konnte nicht gespeichert werden (%s)', error.strerror)


def reset_state(root=None):
    try:
        _state_path(root).unlink()
    except FileNotFoundError:
        pass


# ---------- HTTP ----------

def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen werden abgelehnt: sonst gingen apikey/Authorization an einen fremden Host mit."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SyncError('Die Cloud-Datenbank leitet auf eine andere Adresse weiter. Dem wird aus Sicherheitsgründen '
                        'nicht gefolgt (der Schlüssel würde mitgeschickt). Trage die endgültige Adresse direkt ein '
                        '(https://…supabase.co).', 'rejected')


def _opener(context):
    handlers = [_NoRedirect()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


class Client:
    def __init__(self, cfg, *, sleep=None, attempts=None):
        sync = cfg['sync']
        self.cfg = cfg
        self.base = sync['url'].rstrip('/')
        if self.base.endswith('/rest/v1'):
            self.base = self.base[:-len('/rest/v1')]
        self.key = sync['key']
        self.sleep = sleep or time.sleep
        self.attempts = attempts or MAX_ATTEMPTS
        self._ctx = _ssl_context() if self.base.startswith('https') else None
        self._opener = _opener(self._ctx)

    def _message(self, status, body):
        if status in (401, 403):
            return ('Die Cloud-Datenbank lehnt den Schlüssel ab. Prüfe, ob du den richtigen Schlüssel eingetragen hast '
                    'und ob er Schreibrecht hat (zum Senden) bzw. Leserecht (zum Holen).')
        if status == 404:
            return ('Die Tabellen oder die Adresse wurden nicht gefunden. Prüfe die Adresse und führe '
                    'deploy/supabase_schema.sql in der Cloud-Datenbank aus.')
        if status == 429:
            return 'Die Cloud-Datenbank bremst gerade (zu viele Anfragen). Es wird später automatisch weitergemacht.'
        if status >= 500:
            return f'Die Cloud-Datenbank meldet einen Serverfehler ({status}). Später erneut versuchen.'
        detail = ''
        try:
            info = json.loads(body.decode('utf-8', 'replace'))
            if isinstance(info, dict):
                detail = ' '.join(str(info[k]) for k in ('message', 'hint') if info.get(k))
        except ValueError:
            pass
        detail = integration.scrub(detail, self.cfg)[:300]
        return f'Die Cloud-Datenbank hat die Daten abgelehnt ({status}){": " + detail if detail else ""}.'

    def request(self, method, path, *, body=None, prefer=None):
        url = self.base + '/rest/v1/' + path
        headers = {'apikey': self.key, 'Authorization': f'Bearer {self.key}', 'Accept': 'application/json',
                   'User-Agent': 'socialscraper-sync/1'}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False, default=str).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if prefer:
            headers['Prefer'] = prefer
        last = None
        for attempt in range(1, self.attempts + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            wait = RETRY_BASE * 2 ** (attempt - 1)
            try:
                with self._opener.open(req, timeout=TIMEOUT) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw.strip() else None
            except urllib.error.HTTPError as error:
                try:
                    payload = error.read()
                except OSError:
                    payload = b''
                finally:
                    error.close()
                status = error.code
                last = SyncError(self._message(status, payload), 'rate' if status == 429 else 'server' if status >= 500 else 'rejected', status)
                if status == 429 or status >= 500:
                    after = error.headers.get('Retry-After') if error.headers else None
                    try:
                        wait = min(float(after), RETRY_AFTER_MAX) if after else wait
                    except ValueError:
                        pass
                else:
                    raise last from None
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ssl.SSLError, http.client.HTTPException) as error:
                last = SyncError('Keine Verbindung zur Cloud-Datenbank (Netz weg, Adresse falsch oder Zeitüberschreitung). '
                                 'Es geht nichts verloren; der nächste Versuch macht dort weiter.', 'network')
            except ValueError:
                raise SyncError('Die Cloud-Datenbank hat eine unlesbare Antwort geschickt. Stimmt die Adresse?', 'rejected') from None
            if attempt < self.attempts:
                self.sleep(wait)
        raise last


# ---------- Hilfen ----------

def _check_config(root):
    cfg, problem = integration.load_with_status(root)
    if problem:
        raise SyncError(problem)
    sync = cfg['sync']
    if sync['kind'] != 'supabase':
        raise SyncError('Die Cloud-Datenbank ist nicht eingerichtet (Art: keine).', 'unconfigured')
    problems = integration.validate(cfg)
    if problems:
        raise SyncError(' '.join(p for p in problems))
    return cfg


def _cloud_value(table, column, value):
    spec = TABLES[table]
    if column in spec['json']:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value
    if column in spec['times']:
        return _norm(value)
    if column == 'is_author_reply':
        return bool(value)
    if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
        return None
    return value


def _ro(db_path):
    p = pathlib.Path(db_path)
    if not p.exists():
        raise SyncError('Es gibt noch keine lokale Datenbank. Rufe zuerst ein Video ab.')
    return sqlite3.connect(f'{p.resolve().as_uri()}?mode=ro', uri=True, timeout=10)


def _local_batches(db, table, wm, batch):
    """Liefert Listen von (Cloud-Zeile, Wasserstand-Wert) in stabiler Reihenfolge.

    Keyset-Paging (statt LIMIT/OFFSET): jede Seite beginnt hinter dem letzten Schlüssel der vorigen, so
    überspringt nichts Zeilen, wenn zwischendurch geschrieben wird."""
    spec = TABLES[table]
    cols = ', '.join(spec['cols']) if table not in ('group_videos', 'group_people') else ''
    # COALESCE: eine Zeile ohne Zeit darf den Zeilenvergleich nicht zu NULL machen
    wm_sql = f"COALESCE({_sql_time(_WM_EXPR[table])},'')" if spec['wm'] else None
    keys = _KEYSET[table]
    base = _SELECT[table].format(cols=cols, extra=f', {wm_sql} AS __wm' if wm_sql else '')
    exprs = ([wm_sql] if wm_sql else []) + [e for _, e in keys]
    order = ', '.join(exprs)
    last = None
    while True:
        where, args = [], []
        if wm_sql and wm:
            where.append(f'{wm_sql}>=?')
            args.append(wm)
        if last is not None:
            where.append(f'({order}) > ({", ".join("?" for _ in last)})')
            args.extend(last)
        sql = base + (' WHERE ' + ' AND '.join(where) if where else '') + f' ORDER BY {order} LIMIT ?'
        cur = db.execute(sql, [*args, batch])
        names = [c[0] for c in cur.description]
        rows = cur.fetchall()
        if not rows:
            return
        out = []
        for r in rows:
            record = dict(zip(names, r))
            cloud = {c: _cloud_value(table, c, record.get(c)) for c in spec['cols']}
            out.append((cloud, _wm(record.get(spec['wm'])) if spec['wm'] else None))
        yield out
        if len(rows) < batch:
            return
        final = dict(zip(names, rows[-1]))
        last = ([final['__wm']] if wm_sql else []) + [final[n] for n, _ in keys]


def _has_local(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _record(root, **fields):
    try:
        integration.update_sync_status(root, **fields)
    except Exception:
        pass


def _result(direction, counts, error=None, partial=False):
    return {'ok': error is None, 'direction': direction, 'counts': counts, 'total': sum(counts.values()),
            'error': error, 'partial': bool(error and partial)}


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ---------- Push ----------

def _missing_warning(tables):
    return ('In der Cloud-Datenbank fehlen noch Tabellen (' + ', '.join(tables) + '). Diese Daten wurden nicht abgeglichen – '
            'führe deploy/supabase_schema.sql in der Cloud-Datenbank erneut aus.')


def push(root=None, *, db_path=None, full=False, client=None, batch=BATCH, record=True):
    """Sendet neue/geänderte Zeilen in die Cloud. `full=True` ignoriert die gespeicherten Wasserstände."""
    counts = dict.fromkeys(ORDER, 0)
    missing = []
    if not _LOCK.acquire(blocking=False):
        return _result('push', counts, 'Es läuft gerade schon ein Abgleich.')
    try:
        cfg = _check_config(root)
        client = client or Client(cfg)
        state = load_state(root, cfg['sync']['url'])
        if full:
            state['push'] = {}
        db = _ro(db_path or integration.db_path(root))
        try:
            for table in ORDER:
                spec = TABLES[table]
                if table in OPTIONAL and not _has_local(db, table):
                    continue   # ältere lokale Datenbank ohne diese Tabelle
                wm = state['push'].get(table)
                try:
                    for rows in _local_batches(db, table, wm, batch):
                        key = ','.join(spec['key'])
                        client.request('POST', f'{table}?on_conflict={quote(key)}', body=[r for r, _ in rows],
                                       prefer='resolution=merge-duplicates,return=minimal')
                        counts[table] += len(rows)
                        if spec['wm']:
                            state['push'][table] = max([w for _, w in rows if w] + [state['push'].get(table) or ''])
                        save_state(root, state)
                except SyncError as error:
                    if table in OPTIONAL and error.status == 404:
                        missing.append(table)
                        continue
                    raise
        finally:
            db.close()
        if record:
            _record(root, last_push=_now(), last_error=None)
        result = _result('push', counts)
        if missing:
            result['warning'] = _missing_warning(missing)
        return result
    except SyncError as error:
        message = integration.scrub(str(error), cfg if 'cfg' in locals() else {})
        if record and error.kind != 'unconfigured':
            _record(root, last_error=message)
        return _result('push', counts, message, partial=sum(counts.values()) > 0)
    except sqlite3.Error as error:
        message = 'Die lokale Datenbank konnte nicht gelesen werden (gesperrt oder beschädigt). Später erneut versuchen.'
        log.warning('Sync push: %s', type(error).__name__)
        if record:
            _record(root, last_error=message)
        return _result('push', counts, message, partial=sum(counts.values()) > 0)
    except Exception as error:   # niemals Rohdaten oder Schlüssel in die Meldung
        log.warning('Sync push unerwartet gescheitert: %s', type(error).__name__)
        message = 'Der Abgleich ist unerwartet gescheitert. Details stehen im Protokoll.'
        if record:
            _record(root, last_error=message)
        return _result('push', counts, message, partial=sum(counts.values()) > 0)
    finally:
        _LOCK.release()


def push_if_configured(root=None, *, db_path=None):
    """Für den Hintergrunddienst nach jedem Lauf: sendet, wenn Sync eingerichtet und automatisch.

    Wirft nie. Gibt das Ergebnis oder None zurück (nicht eingerichtet / nicht automatisch)."""
    try:
        cfg, problem = integration.load_with_status(root)
        if problem or cfg['sync']['kind'] != 'supabase' or not cfg['sync']['auto']:
            return None
        if not cfg['sync']['url'] or not cfg['sync']['key']:
            return None
        return push(root, db_path=db_path)
    except Exception as error:
        log.warning('Sync push_if_configured: %s', type(error).__name__)
        return None


# ---------- Pull ----------

def _local_value(column, value, spec):
    if column in spec['json']:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if column == 'is_author_reply':
        return int(bool(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if column in spec['times']:
        return _norm(value) if value else None
    return value


_BAD_ROW = (OverflowError, ValueError, TypeError, sqlite3.InterfaceError, sqlite3.DataError, sqlite3.IntegrityError)


def _merge_one(db, table, spec, row):
    """Schreibt eine Cloud-Zeile lokal. Gibt 1 (geschrieben) oder 0 (übersprungen) zurück."""
    if not isinstance(row, dict):
        return 0
    rec = {c: _local_value(c, row.get(c), spec) for c in spec['cols'] if c in row}
    if table == 'videos':
        if not all(isinstance(rec.get(k), str) and rec.get(k) for k in ('uid', 'platform', 'video_id')):
            return 0
    elif table == 'comments':
        if not all(isinstance(rec.get(k), str) and rec.get(k) for k in ('uid', 'platform', 'video_id', 'comment_id')):
            return 0
    elif table == 'video_snapshots':
        if not rec.get('video_uid') or not rec.get('captured_at'):
            return 0
    elif not all(rec.get(k) for k in spec['key']):
        return 0
    if table == 'comment_sentiment':
        if rec.get('label') not in ('positiv', 'neutral', 'negativ'):
            return 0
        rec['analyzed_at'] = rec.get('analyzed_at') or _now()
        names = list(rec)
        updates = ', '.join(f'{c}=excluded.{c}' for c in names if c != 'comment_uid')
        cur = db.execute(
            f'INSERT INTO comment_sentiment ({", ".join(names)}) VALUES ({", ".join("?" for _ in names)}) '
            f'ON CONFLICT(comment_uid) DO UPDATE SET {updates} '
            f'WHERE {_sql_time("excluded.analyzed_at")} > {_sql_time("comment_sentiment.analyzed_at")}', [rec[c] for c in names])
        return 1 if cur.rowcount else 0
    if table == 'group_people':
        if rec.get('role') not in ('creator', 'commenter'):
            return 0
        gid = db.execute('SELECT id FROM groups WHERE name=?', (rec['group_name'],)).fetchone()
        if not gid:
            return 0
        cur = db.execute('INSERT OR IGNORE INTO group_people (group_id, role, platform, author_key, display_name, added_at) '
                         'VALUES (?,?,?,?,?,?)', (gid[0], rec['role'], rec['platform'], rec['author_key'],
                                                  rec.get('display_name'), rec.get('added_at') or _now()))
        return cur.rowcount
    if table in ('videos', 'comments'):
        rec['fetched_at'] = rec.get('fetched_at') or _now()
        rec['first_seen_at'] = rec.get('first_seen_at') or rec['fetched_at']
        names = list(rec)
        updates = ', '.join(f'{c}=COALESCE(excluded.{c}, {table}.{c})' if (table, c) == ('videos', 'duration_s') else f'{c}=excluded.{c}'
                            for c in names if c not in ('uid', 'first_seen_at'))   # Länge wie in Store._upsert nie mit leer überschreiben
        cur = db.execute(
            f'INSERT INTO {table} ({", ".join(names)}) VALUES ({", ".join("?" for _ in names)}) '
            f'ON CONFLICT(uid) DO UPDATE SET {updates} '
            f'WHERE {_sql_time("excluded.fetched_at")} > {_sql_time(table + ".fetched_at")}',
            [rec[c] for c in names])
        return 1 if cur.rowcount else 0
    if table == 'video_snapshots':
        dup = db.execute(f'SELECT 1 FROM video_snapshots WHERE video_uid=? AND {_sql_time("captured_at")}=?',
                         (rec['video_uid'], rec['captured_at'][:19])).fetchone()
        if dup:
            return 0
        db.execute('INSERT OR IGNORE INTO video_snapshots (video_uid, captured_at, view_count, like_count, comment_count, share_count) '
                   'VALUES (?,?,?,?,?,?)', [rec.get(c) for c in spec['cols']])
        return 1
    if table == 'groups':
        cur = db.execute('INSERT OR IGNORE INTO groups (name, color, note, created_at) VALUES (?,?,?,?)',
                         (rec['name'][:60], rec.get('color') if isinstance(rec.get('color'), str) and len(rec['color']) == 7 else '#2563eb',
                          rec.get('note'), rec.get('created_at') or _now()))
        return cur.rowcount
    gid = db.execute('SELECT id FROM groups WHERE name=?', (rec['group_name'],)).fetchone()   # group_videos
    if not gid:
        return 0
    cur = db.execute('INSERT OR IGNORE INTO group_videos (group_id, video_uid, added_at) VALUES (?,?,?)',
                     (gid[0], rec['video_uid'], rec.get('added_at') or _now()))
    return cur.rowcount


def _merge_rows(db, table, rows):
    """Schreibt Cloud-Zeilen lokal. Gibt (geschrieben, übersprungen, unbrauchbar) zurück.

    Eine fehlerhafte Zeile (z. B. eine Zahl, die in keine Spalte passt) wird einzeln übersprungen und gezählt;
    sie lässt den Rest nicht scheitern. Datenbankfehler (gesperrt …) brechen dagegen weiterhin ab."""
    spec = TABLES[table]
    written = skipped = invalid = 0
    with db:
        for row in rows:
            try:
                done = _merge_one(db, table, spec, row)
            except _BAD_ROW as error:
                log.warning('Sync pull: Zeile in %s übersprungen (%s)', table, type(error).__name__)
                invalid += 1
                skipped += 1
                continue
            written += done
            skipped += 0 if done else 1
    return written, skipped, invalid


def _pg_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _keyset_filter(columns, last_row):
    """PostgREST-`or`-Filter: alle Zeilen hinter `last_row` in der Reihenfolge `columns` (Keyset-Paging)."""
    parts = []
    for i, col in enumerate(columns):
        eq = [f'{c}.eq.{_pg_quote(last_row.get(c))}' for c in columns[:i]]
        gt = f'{col}.gt.{_pg_quote(last_row.get(col))}'
        parts.append(gt if not eq else 'and(' + ','.join(eq + [gt]) + ')')
    return '(' + ','.join(parts) + ')'


def pull(root=None, *, db_path=None, full=False, client=None, batch=BATCH, record=True):
    """Holt Zeilen aus der Cloud und führt sie lokal zusammen."""
    counts = dict.fromkeys(ORDER, 0)
    skipped_total = 0
    missing = []
    if not _LOCK.acquire(blocking=False):
        return _result('pull', counts, 'Es läuft gerade schon ein Abgleich.')
    store = None
    invalid_total = 0
    try:
        cfg = _check_config(root)
        client = client or Client(cfg)
        state = load_state(root, cfg['sync']['url'])
        if full:
            state['pull'] = {}
        store = Store(str(db_path or integration.db_path(root)))
        for table in ORDER:
            spec = TABLES[table]
            wm = state['pull'].get(table)
            # Sortierung und Keyset-Spalten: Wasserstand (falls vorhanden), dann der Schlüssel – ohne Doppelte
            columns = ([spec['wm']] if spec['wm'] else []) + [k for k in spec['key'] if k != spec['wm']]
            order = ','.join(f'{c}.asc' for c in columns)
            last_row = None
            while True:
                query = f'{table}?select=*&order={quote(order, safe=",.")}&limit={batch}'
                if spec['wm'] and wm:
                    query += f'&{spec["wm"]}=gte.{quote(wm + "Z", safe="")}'
                if last_row is not None:
                    query += '&or=' + quote(_keyset_filter(columns, last_row), safe='(),.')
                try:
                    rows = client.request('GET', query)
                except SyncError as error:
                    if table in OPTIONAL and error.status == 404:
                        missing.append(table)
                        break
                    raise
                if not isinstance(rows, list):
                    raise SyncError('Die Cloud-Datenbank hat eine unerwartete Antwort geschickt.', 'rejected')
                if not rows:
                    break
                written, skipped, invalid = _merge_rows(store.db, table, rows)
                counts[table] += written
                skipped_total += skipped
                invalid_total += invalid
                if spec['wm']:
                    last = max([w for w in (_wm(r.get(spec['wm'])) for r in rows if isinstance(r, dict)) if w] + [state['pull'].get(table) or ''])
                    state['pull'][table] = last
                    save_state(root, state)
                if len(rows) < batch:
                    break
                last_row = rows[-1]
                if not isinstance(last_row, dict) or any(last_row.get(c) is None for c in columns):
                    raise SyncError('Die Cloud-Datenbank hat Zeilen ohne Schlüssel geschickt.', 'rejected')
        if record:
            _record(root, last_pull=_now(), last_error=None)
        result = _result('pull', counts)
        result['skipped'] = skipped_total
        result['invalid'] = invalid_total
        if invalid_total:
            result['warning'] = (f'{invalid_total} fehlerhafte Zeilen aus der Cloud wurden übersprungen '
                                 f'(z. B. unmögliche Zahlen). Der Rest wurde übernommen.')
        if missing:
            result['warning'] = (result.get('warning', '') + ' ' + _missing_warning(missing)).strip()
        return result
    except SyncError as error:
        message = integration.scrub(str(error), cfg if 'cfg' in locals() else {})
        if record and error.kind != 'unconfigured':
            _record(root, last_error=message)
        result = _result('pull', counts, message, partial=sum(counts.values()) > 0)
        result['skipped'] = skipped_total
        return result
    except sqlite3.Error as error:
        log.warning('Sync pull: %s', type(error).__name__)
        message = 'Die lokale Datenbank konnte nicht beschrieben werden (gesperrt?). Später erneut versuchen.'
        if record:
            _record(root, last_error=message)
        return _result('pull', counts, message, partial=sum(counts.values()) > 0)
    except Exception as error:
        log.warning('Sync pull unerwartet gescheitert: %s', type(error).__name__)
        message = 'Der Abgleich ist unerwartet gescheitert. Details stehen im Protokoll.'
        if record:
            _record(root, last_error=message)
        return _result('pull', counts, message, partial=sum(counts.values()) > 0)
    finally:
        if store is not None:
            store.close()
        _LOCK.release()


# ---------- Test und Status ----------

def test(root=None, *, cfg=None, client=None):
    """Prüft Verbindung, Schlüssel und Tabellen. Gibt {'ok', 'message', 'tables': {name: 'ok'|'fehlt'}} zurück.

    `cfg` erlaubt, noch nicht gespeicherte Eingaben aus der Oberfläche zu prüfen."""
    tables = {}
    try:
        if cfg is None:
            cfg = _check_config(root)
        else:
            if cfg['sync']['kind'] != 'supabase':
                raise SyncError('Die Cloud-Datenbank ist nicht eingerichtet (Art: keine).', 'unconfigured')
            problems = integration.validate(cfg)
            if problems:
                raise SyncError(' '.join(problems))
        client = client or Client(cfg, attempts=2)
        missing, optional_missing = [], []
        for table in ORDER:
            try:
                client.request('GET', f'{table}?select={quote(TABLES[table]["key"][0])}&limit=1')
                tables[table] = 'ok'
            except SyncError as error:
                if error.kind == 'rejected' and 'nicht gefunden' in str(error):
                    tables[table] = 'fehlt'
                    (optional_missing if table in OPTIONAL else missing).append(table)
                else:
                    raise
        if not missing and optional_missing:
            return {'ok': True, 'tables': tables, 'message': 'Verbindung in Ordnung. ' + _missing_warning(optional_missing)}
        missing += optional_missing
        if missing:
            return {'ok': False, 'tables': tables,
                    'message': 'Diese Tabellen fehlen in der Cloud-Datenbank (oder die Adresse stimmt nicht): ' + ', '.join(missing) +
                               '. Führe deploy/supabase_schema.sql in der Cloud-Datenbank aus.'}
        return {'ok': True, 'tables': tables, 'message': 'Verbindung und Tabellen sind in Ordnung.'}
    except SyncError as error:
        return {'ok': False, 'tables': tables, 'message': integration.scrub(str(error), cfg or {})}
    except Exception as error:
        log.warning('Sync test: %s', type(error).__name__)
        return {'ok': False, 'tables': tables, 'message': 'Der Test ist unerwartet gescheitert. Details stehen im Protokoll.'}


def status(root=None, *, db_path=None):
    """Für die Oberfläche: Einrichtung, letzter Stand, noch nicht gesendete Zeilen. Ohne Geheimnisse."""
    cfg, problem = integration.load_with_status(root)
    sync = cfg['sync']
    out = {'configured': sync['kind'] == 'supabase' and bool(sync['url']) and bool(sync['key']), 'kind': sync['kind'],
           'host': urlsplit(sync['url']).hostname if sync['url'] else None, 'auto': sync['auto'],
           'last_push': sync['last_push'], 'last_pull': sync['last_pull'], 'last_error': sync['last_error'],
           'config_problem': problem, 'pending': None}
    if out['configured']:
        try:
            state = load_state(root, sync['url'])
            db = _ro(db_path or integration.db_path(root))
            try:
                pending = {}
                for table in ('videos', 'comments', 'video_snapshots', 'group_videos', 'comment_sentiment', 'group_people'):
                    if table in OPTIONAL and not _has_local(db, table):
                        continue
                    wm = state['push'].get(table)
                    sql = f'SELECT COUNT(*) FROM {table}'
                    args = []
                    if wm:
                        sql += f' WHERE {_sql_time("added_at" if table == "group_videos" else TABLES[table]["wm"])}>=?'
                        args.append(wm)
                    pending[table] = db.execute(sql, args).fetchone()[0]
                out['pending'] = pending
            finally:
                db.close()
        except (SyncError, sqlite3.Error):
            pass
    return out
