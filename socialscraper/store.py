import dataclasses
import datetime
import json
import logging
import pathlib
import sqlite3
from contextlib import contextmanager

log = logging.getLogger(__name__)

# Kanal-Felder der Tabelle `targets` (Kanäle sind Ziele der Art „channel“). Nur Kanäle benutzen sie; bei den
# anderen Zielen stehen die Standardwerte.
TARGET_CHANNEL_DDL = """
    pace_profile TEXT NOT NULL DEFAULT 'normal' CHECK (pace_profile IN ('careful','normal','fast')),
    scope TEXT NOT NULL DEFAULT 'all' CHECK (scope IN ('all','latest','since')), scope_value TEXT,
    backfill_state TEXT CHECK (backfill_state IS NULL OR backfill_state IN ('queued','listing','fetching','paused','done','watching')),
    quiet_hours TEXT, observe INTEGER NOT NULL DEFAULT 1, include_shorts INTEGER NOT NULL DEFAULT 1,
    only_video INTEGER NOT NULL DEFAULT 0, list_sort TEXT NOT NULL DEFAULT 'new', owner_as_creator INTEGER NOT NULL DEFAULT 0,
    display_name TEXT, total_found INTEGER NOT NULL DEFAULT 0, platform_total INTEGER, list_note TEXT, list_cursor TEXT,
    list_done INTEGER NOT NULL DEFAULT 0, last_listed_at TEXT, pause_until TEXT, pause_reason TEXT, resume_state TEXT,
    force_once INTEGER NOT NULL DEFAULT 0"""
# Spalten mit ihrer Definition, falls eine Datenbank nur ergänzt (nicht neu aufgebaut) werden muss
TARGET_CHANNEL_COLUMNS = {
    'pace_profile': "TEXT NOT NULL DEFAULT 'normal'", 'scope': "TEXT NOT NULL DEFAULT 'all'", 'scope_value': 'TEXT',
    'backfill_state': 'TEXT', 'quiet_hours': 'TEXT', 'observe': 'INTEGER NOT NULL DEFAULT 1',
    'include_shorts': 'INTEGER NOT NULL DEFAULT 1', 'only_video': 'INTEGER NOT NULL DEFAULT 0',
    'list_sort': "TEXT NOT NULL DEFAULT 'new'", 'owner_as_creator': 'INTEGER NOT NULL DEFAULT 0', 'display_name': 'TEXT',
    'total_found': 'INTEGER NOT NULL DEFAULT 0', 'platform_total': 'INTEGER', 'list_note': 'TEXT', 'list_cursor': 'TEXT',
    'list_done': 'INTEGER NOT NULL DEFAULT 0', 'last_listed_at': 'TEXT', 'pause_until': 'TEXT', 'pause_reason': 'TEXT',
    'resume_state': 'TEXT', 'force_once': 'INTEGER NOT NULL DEFAULT 0',
}


def targets_ddl(name='targets', if_not_exists=True):
    return f"""CREATE TABLE {'IF NOT EXISTS ' if if_not_exists else ''}{name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('video','hashtag','search','profile','channel')),
    platform TEXT NOT NULL, value TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    account_mode TEXT NOT NULL DEFAULT 'default' CHECK (account_mode IN ('default','fixed','rotate')),
    group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
    interval_min INTEGER NOT NULL DEFAULT 360, max_comments INTEGER NOT NULL DEFAULT 20,
    max_videos INTEGER NOT NULL DEFAULT 20, enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT, next_run_at TEXT, last_status TEXT, last_error TEXT, created_at TEXT NOT NULL,{TARGET_CHANNEL_DDL},
    UNIQUE (kind, platform, value))"""


SCHEMA = '''
CREATE TABLE IF NOT EXISTS videos (
    uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, url TEXT,
    author TEXT, author_id TEXT, caption TEXT, created_at TEXT,
    duration_s REAL, view_count INTEGER, like_count INTEGER,
    comment_count INTEGER, share_count INTEGER, save_count INTEGER,
    hashtags TEXT, mentions TEXT, music TEXT, language TEXT,
    thumbnail TEXT, raw TEXT, fetched_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS comments (
    uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, comment_id TEXT,
    text TEXT, author TEXT, author_id TEXT, created_at TEXT,
    like_count INTEGER, reply_count INTEGER, parent_id TEXT,
    is_author_reply INTEGER, language TEXT, raw TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS video_snapshots (
    video_uid TEXT, captured_at TEXT, view_count INTEGER,
    like_count INTEGER, comment_count INTEGER, share_count INTEGER,
    PRIMARY KEY (video_uid, captured_at));
CREATE INDEX IF NOT EXISTS idx_c_video ON comments(platform, video_id);
CREATE INDEX IF NOT EXISTS idx_c_parent ON comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_v_author ON videos(platform, author);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, label TEXT NOT NULL,
    identity TEXT NOT NULL UNIQUE, country TEXT NOT NULL DEFAULT 'de',
    is_default INTEGER NOT NULL DEFAULT 0, note TEXT, created_at TEXT NOT NULL,
    rotation_enabled INTEGER NOT NULL DEFAULT 1, proxy TEXT, cooldown_until TEXT, cooldown_reason TEXT,
    last_used_at TEXT, use_count INTEGER DEFAULT 0, fail_streak INTEGER DEFAULT 0, daily_limit INTEGER,
    used_today INTEGER DEFAULT 0, used_day TEXT);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#2563eb', note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS group_videos (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    video_uid TEXT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (group_id, video_uid));
CREATE INDEX IF NOT EXISTS idx_gv_video ON group_videos(video_uid);
%TARGETS%;
CREATE TABLE IF NOT EXISTS channel_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    platform TEXT NOT NULL, video_url TEXT NOT NULL, video_uid TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','failed','skipped')),
    attempts INTEGER NOT NULL DEFAULT 0, discovered_at TEXT NOT NULL, done_at TEXT, tried_at TEXT, last_error TEXT,
    published_at TEXT, title TEXT, duration_s REAL,
    UNIQUE (target_id, video_url));
CREATE INDEX IF NOT EXISTS idx_ci_target ON channel_items(target_id, status);
CREATE INDEX IF NOT EXISTS idx_ci_tried ON channel_items(platform, tried_at);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER REFERENCES targets(id) ON DELETE SET NULL,
    label TEXT, source TEXT NOT NULL DEFAULT 'manual', started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running', videos_seen INTEGER DEFAULT 0, videos_new INTEGER DEFAULT 0,
    comments_new INTEGER DEFAULT 0, error TEXT, authors_new INTEGER DEFAULT 0, accounts_used TEXT);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
CREATE TABLE IF NOT EXISTS comment_sentiment (
    comment_uid TEXT PRIMARY KEY,
    label TEXT NOT NULL CHECK (label IN ('positiv','neutral','negativ')),
    score REAL CHECK (score IS NULL OR (score >= -1.0 AND score <= 1.0)),
    model TEXT, prompt_version INTEGER, analyzed_at TEXT, text_hash TEXT);
CREATE INDEX IF NOT EXISTS idx_cs_label ON comment_sentiment(label);
CREATE INDEX IF NOT EXISTS idx_cs_analyzed ON comment_sentiment(analyzed_at);
CREATE TABLE IF NOT EXISTS group_people (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('creator','commenter')),
    platform TEXT NOT NULL, author_key TEXT NOT NULL, display_name TEXT, added_at TEXT,
    PRIMARY KEY (group_id, role, platform, author_key));
CREATE INDEX IF NOT EXISTS idx_gp_person ON group_people(platform, author_key);
'''
ACCOUNT_COLUMNS = {
    'rotation_enabled': 'INTEGER NOT NULL DEFAULT 1', 'proxy': 'TEXT', 'cooldown_until': 'TEXT',
    'cooldown_reason': 'TEXT', 'last_used_at': 'TEXT', 'use_count': 'INTEGER DEFAULT 0',
    'fail_streak': 'INTEGER DEFAULT 0', 'daily_limit': 'INTEGER', 'used_today': 'INTEGER DEFAULT 0', 'used_day': 'TEXT',
}
TARGET_MODE_DDL = "TEXT NOT NULL DEFAULT 'default' CHECK (account_mode IN ('default','fixed','rotate'))"
SCHEMA = SCHEMA.replace('%TARGETS%', targets_ddl())


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

TABLES = frozenset(('videos', 'comments', 'video_snapshots'))
JSON_COLUMNS = ('hashtags', 'mentions', 'raw')


@contextmanager
def _atomic_schema(db, name):
    """Run DDL atomically without committing a caller's open transaction."""
    nested = db.in_transaction
    if nested:
        db.execute(f'SAVEPOINT {name}')
    else:
        db.execute('BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        if nested:
            db.execute(f'ROLLBACK TO {name}')
            db.execute(f'RELEASE {name}')
        else:
            db.rollback()
        raise
    else:
        if nested:
            db.execute(f'RELEASE {name}')
        else:
            db.commit()


class Store:
    def __init__(self, path='data/scrape.db'):
        if str(path) != ':memory:': pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.executescript(SCHEMA)
            self._migrate()
        except BaseException:
            self.db.close()
            raise

    def _migrate(self):
        """Ältere Datenbanken bekommen first_seen_at (Zeitpunkt, an dem ein Eintrag zum ersten Mal gesehen wurde).

        Spalte anlegen und füllen geschieht in einer Transaktion; Zeilen ohne Wert (z. B. nach einem früheren
        Abbruch) werden bei jedem Start nachgefüllt."""
        from .warmup import SPALTEN as warmup_columns
        from .streuung import SPALTEN as scheduling_columns
        from .aktionen import ensure_schema
        self._add_columns('accounts', {**warmup_columns, **scheduling_columns})
        ensure_schema(self.db)
        from . import personas as _personas
        from .fyp import ensure_schema as _fyp_schema
        with _atomic_schema(self.db, 'astra_schema'):
            _personas.ensure_schema(self.db)
            _fyp_schema(self.db)
        self._add_columns('accounts', ACCOUNT_COLUMNS)    # Rotation, fester Proxy (Referenz), Abkühlung, Tageslimit
        self._add_columns('accounts', ACCOUNT_COLUMNS)    # Rotation, fester Proxy (Referenz), Abkühlung, Tageslimit
        self._add_columns('runs', {'accounts_used': 'TEXT'})
        if self._add_columns('targets', {'account_mode': TARGET_MODE_DDL}):
            # Alte Ziele: gewähltes Konto -> „fixed“, sonst Standardkonto der Plattform.
            self.db.execute("UPDATE targets SET account_mode='fixed' WHERE account_id IS NOT NULL")
            self.db.commit()
        self._migrate_targets()   # Zielart „channel“ und die Kanal-Felder (SQLite braucht dafür einen Tabellenneubau)
        if 'authors_new' not in [r[1] for r in self.db.execute('PRAGMA table_info(runs)')]:   # neu: Zahl neuer Kommentarautoren je Lauf
            try:
                self.db.execute('ALTER TABLE runs ADD COLUMN authors_new INTEGER DEFAULT 0')
                self.db.commit()
            except sqlite3.OperationalError:   # ein zweiter Prozess war schneller
                if 'authors_new' not in [r[1] for r in self.db.execute('PRAGMA table_info(runs)')]:
                    raise
        for table in ('videos', 'comments'):
            columns = [r[1] for r in self.db.execute(f'PRAGMA table_info({table})')]
            missing = 'first_seen_at' not in columns
            if not missing and self.db.execute(f'SELECT 1 FROM {table} WHERE first_seen_at IS NULL LIMIT 1').fetchone() is None:
                continue
            self.db.execute('BEGIN IMMEDIATE')
            try:
                columns = [r[1] for r in self.db.execute(f'PRAGMA table_info({table})')]  # nach dem Sperren neu prüfen
                if 'first_seen_at' not in columns:
                    self.db.execute(f'ALTER TABLE {table} ADD COLUMN first_seen_at TEXT')
                self.db.execute(f"UPDATE {table} SET first_seen_at=COALESCE(strftime('%Y-%m-%dT%H:%M:%SZ', fetched_at), "
                                f"fetched_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')) WHERE first_seen_at IS NULL")   # ein Zeitformat
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise

    def _migrate_targets(self):
        """Ältere Datenbanken haben in `targets` die Prüfung `kind IN (video, hashtag, search, profile)` und keine
        Kanal-Felder. SQLite kann eine Prüfung nicht ändern: die Tabelle wird neu aufgebaut (alle Zeilen, Nummern,
        Verknüpfungen und die Eindeutigkeit (kind, platform, value) bleiben erhalten). Läuft in einer Transaktion mit
        abgeschalteten Fremdschlüsseln (sonst würde das Löschen der alten Tabelle die Läufe vom Ziel lösen)."""
        db = self.db
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='targets'").fetchone()
        if row is None or "'channel'" in (row[0] or ''):
            self._add_columns('targets', TARGET_CHANNEL_COLUMNS)   # Sicherheitsnetz, normalerweise nichts zu tun
            return
        db.commit()
        db.execute('PRAGMA foreign_keys=OFF')
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='targets'").fetchone()
                if row is not None and "'channel'" not in (row[0] or ''):   # nach dem Sperren neu prüfen
                    old = [r[1] for r in db.execute('PRAGMA table_info(targets)')]
                    db.execute('DROP TABLE IF EXISTS targets_neu')
                    db.execute(targets_ddl('targets_neu', if_not_exists=False))
                    new = {r[1] for r in db.execute('PRAGMA table_info(targets_neu)')}
                    columns = ', '.join(c for c in old if c in new)
                    db.execute(f'INSERT INTO targets_neu ({columns}) SELECT {columns} FROM targets')
                    seq = db.execute("SELECT seq FROM sqlite_sequence WHERE name='targets'").fetchone()
                    db.execute('DROP TABLE targets')
                    db.execute('ALTER TABLE targets_neu RENAME TO targets')
                    if seq is not None:   # die Zählung der Nummern nicht zurücksetzen (gelöschte Nummern nicht neu vergeben)
                        if db.execute("SELECT 1 FROM sqlite_sequence WHERE name='targets'").fetchone():
                            db.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='targets'", (seq[0],))
                        else:
                            db.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('targets', ?)", (seq[0],))
                    broken = db.execute('PRAGMA foreign_key_check').fetchall()
                    if broken:
                        log.warning('Nach dem Umbau der Zieltabelle: %d verwaiste Verknüpfungen', len(broken))
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise
        finally:
            db.execute('PRAGMA foreign_keys=ON')

    def _add_columns(self, table, columns):
        """Fehlende Spalten ergänzen (abwärtskompatibel). Gibt True zurück, wenn `account_mode`-artige Spalten neu sind,
        d. h. wenn mindestens eine Spalte hinzugefügt wurde."""
        have = {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')}
        added = False
        for name, ddl in columns.items():
            if name in have:
                continue
            try:
                self.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
                added = True
            except sqlite3.OperationalError:   # ein zweiter Prozess war schneller
                if name not in {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')}:
                    raise
        self.db.commit()
        return added

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def _row(self, dc):
        d = dataclasses.asdict(dc)
        for key in ('platform', 'video_id', 'comment_id'):
            if key in d and (not isinstance(d[key], str) or not d[key].strip()):
                raise ValueError(f'{key} fehlt im Datensatz')
        d['uid'] = dc.uid
        for key in ('hashtags', 'mentions', 'raw'):
            if key in d: d[key] = json.dumps(d[key], ensure_ascii=False, default=str)
        if 'is_author_reply' in d: d['is_author_reply'] = int(bool(d['is_author_reply']))
        return d

    def _upsert(self, table, dc):
        if table not in ('videos', 'comments'): raise ValueError('Ungültige Datentabelle')
        d = self._row(dc)
        d['first_seen_at'] = _now()
        columns = ', '.join(d)
        placeholders = ', '.join('?' for _ in d)
        # Die Länge eines Videos wird bei Folge-Abrufen nicht mit „leer“ überschrieben, wenn sie schon bekannt ist.
        updates = ', '.join((f'{key}=COALESCE(excluded.{key}, {table}.{key})' if (table, key) == ('videos', 'duration_s')
                             else f'{key}=excluded.{key}') for key in d if key not in ('uid', 'first_seen_at'))
        self.db.execute(
            f'INSERT INTO {table} ({columns}) VALUES ({placeholders}) '
            f'ON CONFLICT(uid) DO UPDATE SET {updates}, fetched_at=CURRENT_TIMESTAMP', list(d.values()))

    def save_video(self, video):
        with self.db: self._upsert('videos', video)

    def save_comment(self, comment):
        with self.db: self._upsert('comments', comment)

    def save_many(self, videos, comments):
        with self.db:
            for video in videos: self._upsert('videos', video)
            for comment in comments: self._upsert('comments', comment)

    def save_result(self, video, comments, captured_at=None):
        """Speichert Video, Kommentare und optional einen Verlaufs-Schnappschuss in einer Transaktion.

        Gibt (video_ist_neu, anzahl_neuer_kommentare) zurück.
        """
        with self.db:
            video_is_new = self.db.execute('SELECT 1 FROM videos WHERE uid=?', (video.uid,)).fetchone() is None
            known = {r[0] for r in self.db.execute('SELECT uid FROM comments WHERE platform=? AND video_id=?',
                                                   (video.platform, video.video_id))}
            self._upsert('videos', video)
            for comment in comments: self._upsert('comments', comment)
            if captured_at is not None: self._snapshot(video, captured_at)
        return video_is_new, len({c.uid for c in comments} - known)

    def _snapshot(self, video, captured_at):
        self.db.execute('INSERT OR REPLACE INTO video_snapshots VALUES (?,?,?,?,?,?)',
            (video.uid, captured_at, video.view_count, video.like_count, video.comment_count, video.share_count))

    def snapshot(self, video, captured_at):
        with self.db: self._snapshot(video, captured_at)

    def stats(self):
        return {table: dict(self.db.execute(f'SELECT platform, COUNT(*) FROM {table} GROUP BY platform'))
                for table in ('videos', 'comments')}

    def dump_jsonl(self, table, path):
        if table not in TABLES: raise ValueError('Ungültige Exporttabelle')
        destination = pathlib.Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        columns = [r[1] for r in self.db.execute(f'PRAGMA table_info({table})')]
        with destination.open('w', encoding='utf-8') as output:
            for row in self.db.execute(f'SELECT * FROM {table}'):
                record = dict(zip(columns, row))
                # Lists and raw data are stored as JSON text; export them as real JSON values.
                for key in JSON_COLUMNS:
                    if isinstance(record.get(key), str):
                        try: record[key] = json.loads(record[key])
                        except ValueError: pass
                output.write(json.dumps(record, ensure_ascii=False) + '\n')

