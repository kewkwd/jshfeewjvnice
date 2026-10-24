"""Tests des Cloud-Sync gegen einen selbstgebauten lokalen Fake-PostgREST-Server (keine echte Cloud!)."""
import json
import logging
import pathlib
import sqlite3
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from socialscraper import integration, repo, sync
from socialscraper.models import Comment, Video
from socialscraper.store import Store

KEY = 'FAKE-SERVICE-KEY-do-not-leak-0123456789'


class FakeCloud:
    """Minimaler PostgREST-Ersatz: Upsert (merge-duplicates), Filter gte, order, limit/offset, Fehlerfälle."""

    def __init__(self):
        self.tables = {}          # name -> {key tuple: row}
        self.keys = {}            # name -> Schlüsselspalten
        self.requests = []        # (method, table, rowcount)
        self.faults = []          # Liste von int (HTTP-Status), ('retry', status, seconds) oder 'drop'
        self.missing = set()
        self.key = KEY
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a): pass

            def _reply(self, status, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else b''
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Type', 'application/json')
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length) if length else b''
                split = urllib.parse.urlsplit(self.path)
                table = split.path.rsplit('/', 1)[-1]
                query = urllib.parse.parse_qs(split.query)
                if self.headers.get('apikey') != outer.key or self.headers.get('Authorization') != 'Bearer ' + outer.key:
                    return self._reply(401, {'message': 'Invalid API key'})
                if outer.faults:
                    fault = outer.faults.pop(0)
                    if fault == 'drop':
                        self.close_connection = True
                        self.connection.close()
                        return
                    if isinstance(fault, tuple):
                        return self._reply(fault[1], {'message': 'slow down'}, {'Retry-After': str(fault[2])})
                    return self._reply(fault, {'message': 'Fehler vom Fake'})
                if table in outer.missing:
                    return self._reply(404, {'code': 'PGRST205', 'message': f'Could not find the table {table}'})
                if self.command == 'POST':
                    rows = json.loads(raw)
                    keys = query['on_conflict'][0].split(',')
                    outer.keys[table] = keys
                    store = outer.tables.setdefault(table, {})
                    for row in rows:
                        store[tuple(row[k] for k in keys)] = row
                    outer.requests.append(('POST', table, len(rows)))
                    assert 'merge-duplicates' in self.headers.get('Prefer', '')
                    return self._reply(201)
                rows = list(outer.tables.get(table, {}).values())
                if 'or' in query:
                    rows = [r for r in rows if outer.match_or(r, query['or'][0])]
                for col, values in query.items():
                    v = values[0]
                    if v.startswith('gte.'):
                        rows = [r for r in rows if str(r.get(col)) >= v[4:]]
                    elif v.startswith('eq.'):
                        rows = [r for r in rows if str(r.get(col)) == v[3:]]
                    elif v.startswith('in.('):
                        wanted = [x.strip('"') for x in v[4:-1].split(',')]
                        rows = [r for r in rows if str(r.get(col)) in wanted]
                order = [o.split('.')[0] for o in query.get('order', [''])[0].split(',') if o]
                rows.sort(key=lambda r: tuple(str(r.get(c)) for c in order))
                if order and query.get('order', [''])[0].split(',')[0].endswith('.desc'):
                    rows.reverse()
                off, lim = int(query.get('offset', ['0'])[0]), int(query.get('limit', ['1000'])[0])
                outer.requests.append(('GET', table, len(rows[off:off + lim])))
                extra = {'Content-Range': f'{off}-{off + len(rows[off:off + lim]) - 1}/{len(rows)}'}                     if 'count=exact' in self.headers.get('Prefer', '') else None
                return self._reply(200, rows[off:off + lim], extra)

            do_GET = do_POST = _handle

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True)
        self.thread.start()

    @staticmethod
    def match_or(row, text):
        """Versteht `(a.gt."x",and(a.eq."x",b.gt."y"))` (Keyset-Filter des Clients)."""
        def split(t):
            parts, depth, cur, quoted, esc = [], 0, '', False, False
            for ch in t:
                if esc:
                    cur += ch; esc = False; continue
                if ch == '\\' and quoted:
                    cur += ch; esc = True; continue
                if ch == '"':
                    quoted = not quoted
                if not quoted and ch == '(':
                    depth += 1
                if not quoted and ch == ')':
                    depth -= 1
                if ch == ',' and depth == 0 and not quoted:
                    parts.append(cur); cur = ''
                else:
                    cur += ch
            parts.append(cur)
            return parts

        def cond(c):
            if c.startswith('and(') and c.endswith(')'):
                return all(cond(x) for x in split(c[4:-1]))
            col, op, value = c.split('.', 2)
            value = value[1:-1].replace('\\"', '"').replace('\\', '\\') if value.startswith('"') else value
            have = str(row.get(col))
            return have > value if op == 'gt' else have == value

        assert text.startswith('(') and text.endswith(')')
        return any(cond(c) for c in split(text[1:-1]))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def posts(self, table=None):
        return [r for r in self.requests if r[0] == 'POST' and (table is None or r[1] == table)]


def seed(db_path, n=4, prefix=''):
    with Store(str(db_path)) as store:
        for i in range(1, n + 1):
            v = Video('tiktok', f'{prefix}{i}', f'https://t.example/{prefix}{i}', author='anna', caption=f'Video {i} äöü',
                      view_count=i * 100, hashtags=['a', 'b'], mentions=[], raw={'k': i}, duration_s=float(i))
            comments = [Comment('tiktok', v.video_id, f'{prefix}{i}-{j}', f'Text {j}', author='fan', is_author_reply=bool(j % 2),
                                like_count=j) for j in (1, 2)]
            store.save_result(v, comments, f'2026-10-0{i}T10:00:00.5+00:00')
        db = store.db
        for i in range(1, n + 1):
            db.execute('UPDATE videos SET fetched_at=? WHERE uid=?', (f'2026-10-0{i} 10:00:00', f'tiktok:{prefix}{i}'))
            db.execute('UPDATE comments SET fetched_at=? WHERE video_id=?', (f'2026-10-0{i} 10:00:00', f'{prefix}{i}'))
        db.commit()
        g = repo.create_group(db, 'Sammlung')
        repo.add_videos_to_group(db, g['id'], [f'tiktok:{prefix}1', f'tiktok:{prefix}2'])
        repo.ensure_default_accounts(db)


class SyncTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.db = self.root / 'data' / 'scrape.db'
        self.cloud = FakeCloud()
        self.addCleanup(self.cloud.close)
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(sync, 'RETRY_BASE', 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.configure()

    def configure(self, **sync_fields):
        cfg = integration.default_config()
        cfg['sync'].update(kind='supabase', url=self.cloud.url, key=KEY, **sync_fields)
        integration.save(cfg, self.root)

    def push(self, **kw):
        return sync.push(self.root, db_path=self.db, **kw)

    def pull(self, **kw):
        return sync.pull(self.root, db_path=self.db, **kw)


class PushTests(SyncTestBase):
    def test_first_push_sends_everything_in_cloud_shape(self):
        seed(self.db)
        result = self.push()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['counts'], {'groups': 1, 'videos': 4, 'comments': 8, 'video_snapshots': 4, 'group_videos': 2,
                                           'comment_sentiment': 0, 'group_people': 0})
        video = self.cloud.tables['videos'][('tiktok:1',)]
        self.assertEqual(video['hashtags'], ['a', 'b'])
        self.assertEqual(video['raw'], {'k': 1})
        self.assertEqual(video['fetched_at'], '2026-10-01T10:00:00Z')
        self.assertRegex(video['first_seen_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertIs(self.cloud.tables['comments'][('tiktok:1-1',)]['is_author_reply'], True)
        self.assertEqual(set(self.cloud.tables), {'groups', 'videos', 'comments', 'video_snapshots', 'group_videos'})
        self.assertEqual(self.cloud.keys['group_videos'], ['group_name', 'video_uid'])
        self.assertEqual(list(self.cloud.tables['group_videos'])[0][0], 'Sammlung')
        self.assertNotIn('accounts', self.cloud.tables)
        snapshot = next(iter(self.cloud.tables['video_snapshots'].values()))
        self.assertTrue(snapshot['captured_at'].endswith('Z'))

    def test_second_push_sends_only_new_and_is_idempotent(self):
        seed(self.db)
        self.push()
        before = {t: dict(rows) for t, rows in self.cloud.tables.items()}
        self.cloud.requests.clear()
        result = self.push()
        self.assertTrue(result['ok'])
        self.assertEqual(result['counts']['videos'], 1, 'nur die Zeile am Wasserstand wird wiederholt')
        self.assertEqual({t: dict(rows) for t, rows in self.cloud.tables.items()}, before)
        with Store(str(self.db)) as store:
            store.save_result(Video('tiktok', 'neu', 'https://t.example/neu'), [Comment('tiktok', 'neu', 'neu-1', 'Hallo')],
                              '2026-10-09T00:00:00+00:00')
        result = self.push()
        self.assertEqual(result['counts']['videos'], 2)
        self.assertIn(('tiktok:neu',), self.cloud.tables['videos'])
        self.assertIn(('tiktok:neu-1',), self.cloud.tables['comments'])

    def test_full_push_ignores_watermarks(self):
        seed(self.db)
        self.push()
        self.assertEqual(self.push(full=True)['counts']['videos'], 4)

    def test_batches(self):
        seed(self.db, n=4)
        self.push(batch=3)
        self.assertEqual([n for _, t, n in self.cloud.posts('comments')], [3, 3, 2])
        self.assertEqual(len(self.cloud.tables['comments']), 8)

    def test_status_updates_and_state_file_has_no_secret(self):
        seed(self.db)
        self.push()
        cfg = integration.load(self.root)
        self.assertRegex(cfg['sync']['last_push'], r'^\d{4}-')
        self.assertIsNone(cfg['sync']['last_error'])
        state = (self.root / 'data' / 'sync-state.json').read_text(encoding='utf-8')
        self.assertNotIn(KEY, state)
        self.assertNotIn(self.cloud.url, state)

    def test_changing_cloud_url_resets_watermarks(self):
        seed(self.db)
        self.push()
        state = sync.load_state(self.root, self.cloud.url)
        self.assertTrue(state['push'])
        self.assertEqual(sync.load_state(self.root, 'https://anders.example')['push'], {})

    def test_unauthorized_gives_clear_message_without_key(self):
        seed(self.db)
        self.cloud.key = 'ein-anderer-schluessel'
        with mock.patch('logging.Logger.warning') as warn:
            result = self.push()
        self.assertFalse(result['ok'])
        self.assertIn('Schlüssel', result['error'])
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn(KEY, str(warn.call_args_list))
        self.assertEqual(self.cloud.requests, [])
        self.assertIn('Schlüssel', integration.load(self.root)['sync']['last_error'])
        self.assertNotIn(KEY, integration.path(self.root).read_text(encoding='utf-8').replace(f'"key": "{KEY}"', ''))

    def test_rate_limit_is_retried_with_wait(self):
        seed(self.db)
        waits = []
        cfg = integration.load(self.root)
        client = sync.Client(cfg, sleep=waits.append)
        self.cloud.faults = [('retry', 429, 2), ('retry', 429, 100)]
        result = self.push(client=client)
        self.assertTrue(result['ok'], result)
        self.assertEqual(waits, [2.0, sync.RETRY_AFTER_MAX])
        self.assertEqual(len(self.cloud.tables['videos']), 4)

    def test_rate_limit_forever_gives_up_with_message(self):
        seed(self.db)
        self.cloud.faults = [('retry', 429, 0)] * 20
        result = self.push()
        self.assertFalse(result['ok'])
        self.assertIn('bremst', result['error'])

    def test_server_error_retried_then_reported(self):
        seed(self.db)
        self.cloud.faults = [503, 500]
        self.assertTrue(self.push()['ok'])
        self.cloud.faults = [500] * 10
        result = self.push(full=True)
        self.assertFalse(result['ok'])
        self.assertIn('Serverfehler', result['error'])

    def test_network_drop_then_recovery(self):
        seed(self.db)
        self.cloud.faults = ['drop', 'drop']
        self.assertTrue(self.push()['ok'])
        self.cloud.faults = ['drop'] * 10
        result = self.push(full=True)
        self.assertFalse(result['ok'])
        self.assertIn('Keine Verbindung', result['error'])

    def test_unreachable_server(self):
        seed(self.db)
        dead = FakeCloud()
        url = dead.url
        dead.close()
        cfg = integration.load(self.root)
        cfg['sync']['url'] = url
        integration.save(cfg, self.root)
        result = self.push()
        self.assertFalse(result['ok'])
        self.assertIn('Keine Verbindung', result['error'])

    def test_partial_success_resumes_where_it_stopped(self):
        seed(self.db, n=4)
        # groups (1 Anfrage) ok, videos Paket 1 ok, Paket 2 scheitert dauerhaft
        calls = {'n': 0}
        real = sync.Client.request

        def flaky(client, method, path, **kw):
            calls['n'] += 1
            if calls['n'] == 3:
                raise sync.SyncError('Keine Verbindung zur Cloud-Datenbank (Test).', 'network')
            return real(client, method, path, **kw)
        with mock.patch.object(sync.Client, 'request', flaky):
            result = self.push(batch=2)
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['counts']['groups'], 1)
        self.assertEqual(result['counts']['videos'], 2)
        self.assertEqual(len(self.cloud.tables['videos']), 2)
        state = sync.load_state(self.root, self.cloud.url)
        self.assertEqual(state['push']['videos'], '2026-10-02T10:00:00')
        resumed = self.push(batch=2)
        self.assertTrue(resumed['ok'])
        self.assertEqual(len(self.cloud.tables['videos']), 4)
        self.assertEqual(len(self.cloud.tables['comments']), 8)

    def test_missing_tables_and_test_command(self):
        seed(self.db)
        self.cloud.missing = {'videos'}
        result = self.push()
        self.assertFalse(result['ok'])
        self.assertIn('supabase_schema.sql', result['error'])
        checked = sync.test(self.root)
        self.assertFalse(checked['ok'])
        self.assertEqual(checked['tables']['videos'], 'fehlt')
        self.assertEqual(checked['tables']['comments'], 'ok')
        self.cloud.missing = set()
        checked = sync.test(self.root)
        self.assertTrue(checked['ok'], checked)
        self.assertNotIn(KEY, json.dumps(checked))

    def test_test_with_wrong_key_and_unsaved_config(self):
        self.cloud.key = 'anderer'
        result = sync.test(self.root)
        self.assertFalse(result['ok'])
        self.assertIn('Schlüssel', result['message'])
        self.cloud.key = KEY
        cfg = integration.default_config()
        cfg['sync'].update(kind='supabase', url=self.cloud.url, key=KEY)
        self.assertTrue(sync.test(cfg=cfg)['ok'])
        cfg['sync']['url'] = 'ftp://nein'
        self.assertFalse(sync.test(cfg=cfg)['ok'])

    def test_not_configured_and_bad_config_do_not_raise(self):
        cfg = integration.default_config()
        integration.save(cfg, self.root)
        result = self.push()
        self.assertFalse(result['ok'])
        self.assertIn('nicht eingerichtet', result['error'])
        integration.path(self.root).write_text('kaputt', encoding='utf-8')
        self.assertFalse(self.push()['ok'])
        self.assertFalse(sync.test(self.root)['ok'])

    def test_no_local_database(self):
        result = self.push()
        self.assertFalse(result['ok'])
        self.assertIn('lokale Datenbank', result['error'])

    def test_push_if_configured(self):
        seed(self.db)
        cfg = integration.load(self.root)
        cfg['sync']['auto'] = False
        integration.save(cfg, self.root)
        self.assertIsNone(sync.push_if_configured(self.root, db_path=self.db))
        self.assertEqual(self.cloud.requests, [])
        cfg['sync']['auto'] = True
        integration.save(cfg, self.root)
        self.assertTrue(sync.push_if_configured(self.root, db_path=self.db)['ok'])
        with mock.patch.object(sync, 'push', side_effect=RuntimeError('boom')):
            self.assertIsNone(sync.push_if_configured(self.root, db_path=self.db))
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.assertIsNone(sync.push_if_configured(other.name))

    def test_unexpected_error_is_contained(self):
        seed(self.db)
        with mock.patch.object(sync.Client, 'request', side_effect=ZeroDivisionError(KEY)):
            result = self.push()
        self.assertFalse(result['ok'])
        self.assertNotIn(KEY, json.dumps(result))

    def test_concurrent_run_is_refused(self):
        seed(self.db)
        sync._LOCK.acquire()
        try:
            self.assertIn('schon', self.push()['error'])
        finally:
            sync._LOCK.release()

    def test_status(self):
        seed(self.db)
        before = sync.status(self.root, db_path=self.db)
        self.assertTrue(before['configured'])
        self.assertEqual(before['host'], '127.0.0.1')
        self.assertNotIn(KEY, json.dumps(before))
        self.push()
        after = sync.status(self.root, db_path=self.db)
        self.assertEqual(after['pending']['videos'], 1)
        self.assertIsNotNone(after['last_push'])


class PullTests(SyncTestBase):
    def test_roundtrip_into_fresh_database(self):
        seed(self.db)
        self.push()
        other = self.root / 'zweit' / 'scrape.db'
        result = sync.pull(self.root, db_path=other, full=True)
        self.assertTrue(result['ok'], result)
        with Store(str(other)) as store:
            self.assertEqual(store.stats(), {'videos': {'tiktok': 4}, 'comments': {'tiktok': 8}})
            row = store.db.execute("SELECT hashtags, raw, duration_s FROM videos WHERE uid='tiktok:1'").fetchone()
            self.assertEqual(json.loads(row[0]), ['a', 'b'])
            self.assertEqual(json.loads(row[1]), {'k': 1})
            self.assertEqual(row[2], 1.0)
            self.assertEqual(store.db.execute("SELECT is_author_reply FROM comments WHERE uid='tiktok:1-1'").fetchone()[0], 1)
            self.assertEqual(repo.group_stats(store.db)[0]['videos'], 2)
            self.assertEqual(len(repo.video_history(store.db, 'tiktok:1')), 1)
        # zweiter Abgleich: nichts Doppeltes
        again = sync.pull(self.root, db_path=other, full=True)
        self.assertEqual(again['counts']['video_snapshots'], 0)
        with Store(str(other)) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM video_snapshots').fetchone()[0], 4)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM group_videos').fetchone()[0], 2)

    def test_newer_fetched_at_wins_and_first_seen_at_stays(self):
        with Store(str(self.db)) as store:
            store.save_video(Video('tiktok', 'x', 'https://t.example/x', view_count=1, caption='lokal'))
            store.db.execute("UPDATE videos SET first_seen_at='2020-01-01T00:00:00Z', fetched_at='2026-10-05 10:00:00'")
            store.db.commit()
        base = {'uid': 'tiktok:x', 'platform': 'tiktok', 'video_id': 'x', 'url': 'u', 'view_count': 99, 'caption': 'cloud',
                'first_seen_at': '2026-10-01T00:00:00Z', 'hashtags': ['z']}
        self.cloud.tables['videos'] = {('tiktok:x',): {**base, 'fetched_at': '2026-10-04T10:00:00Z'}}
        result = self.pull()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['counts']['videos'], 0, 'ältere Cloud-Zeile gewinnt nicht')
        with Store(str(self.db)) as store:
            self.assertEqual(store.db.execute('SELECT caption, view_count FROM videos').fetchone(), ('lokal', 1))
        self.cloud.tables['videos'] = {('tiktok:x',): {**base, 'fetched_at': '2026-10-06T10:00:00Z'}}
        result = self.pull(full=True)
        self.assertEqual(result['counts']['videos'], 1)
        with Store(str(self.db)) as store:
            row = store.db.execute('SELECT caption, view_count, first_seen_at, fetched_at, hashtags FROM videos').fetchone()
        self.assertEqual(row, ('cloud', 99, '2020-01-01T00:00:00Z', '2026-10-06T10:00:00Z', '["z"]'))

    def test_pull_does_not_erase_a_known_duration(self):
        with Store(str(self.db)) as store:
            store.save_video(Video('tiktok', 'x', 'https://t.example/x', duration_s=45))
            store.db.execute("UPDATE videos SET fetched_at='2026-10-05 10:00:00'"); store.db.commit()
        self.cloud.tables['videos'] = {('tiktok:x',): {'uid': 'tiktok:x', 'platform': 'tiktok', 'video_id': 'x', 'url': 'u',
                                                       'duration_s': None, 'view_count': 7, 'fetched_at': '2026-10-06T10:00:00Z'}}
        self.assertEqual(self.pull(full=True)['counts']['videos'], 1)
        with Store(str(self.db)) as store:
            self.assertEqual(store.db.execute('SELECT duration_s, view_count FROM videos').fetchone(), (45.0, 7))

    def test_pull_keeps_mixed_local_formats_comparable_and_skips_bad_rows(self):
        self.cloud.tables['videos'] = {
            ('a',): {'uid': 'tiktok:ok', 'platform': 'tiktok', 'video_id': 'ok', 'fetched_at': '2026-10-06T10:00:00+00:00'},
            ('b',): {'uid': '', 'platform': 'tiktok', 'video_id': 'b', 'fetched_at': '2026-10-06T10:00:00Z'},
            ('c',): {'platform': 'tiktok', 'fetched_at': '2026-10-06T10:00:00Z'},
        }
        self.cloud.tables['group_videos'] = {('Unbekannt', 'tiktok:ok'): {'group_name': 'Unbekannt', 'video_uid': 'tiktok:ok',
                                                                           'added_at': '2026-10-06T10:00:00Z'}}
        result = self.pull()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['counts']['videos'], 1)
        self.assertGreaterEqual(result['skipped'], 3)

    def test_pull_pages_and_errors(self):
        rows = {(f'tiktok:{i}',): {'uid': f'tiktok:{i}', 'platform': 'tiktok', 'video_id': str(i),
                                   'fetched_at': f'2026-10-0{i}T10:00:00Z'} for i in range(1, 6)}
        self.cloud.tables['videos'] = rows
        self.assertEqual(self.pull(batch=2)['counts']['videos'], 5)
        self.cloud.key = 'falsch'
        result = self.pull(full=True)
        self.assertFalse(result['ok'])
        self.assertIn('Schlüssel', result['error'])
        self.cloud.key = KEY
        self.cloud.faults = ['drop'] * 10
        self.assertIn('Keine Verbindung', self.pull(full=True)['error'])

    def test_pull_ignores_non_list_answer(self):
        with mock.patch.object(sync.Client, 'request', return_value={'x': 1}):
            result = self.pull()
        self.assertFalse(result['ok'])


class NodeClientTests(SyncTestBase):
    def test_pulse_client_against_local_api_and_fake_cloud(self):
        import os
        import shutil
        import subprocess
        from socialscraper import api
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js nicht installiert')
        seed(self.db)
        self.assertTrue(self.push()['ok'])
        token = 'node-test-token-' + 'y' * 20
        handle = api.start(self.root, db_path=self.db, port=0, token=token)
        self.addCleanup(handle.stop)
        script = pathlib.Path(__file__).resolve().parent.parent / 'docs' / 'pulse-client.test.mjs'
        env = {**os.environ, 'PULSE_LOCAL_URL': handle.url, 'PULSE_TOKEN': token,
               'PULSE_CLOUD_URL': self.cloud.url, 'PULSE_CLOUD_KEY': KEY}
        done = subprocess.run([node, str(script)], env=env, capture_output=True, text=True, timeout=60, encoding='utf-8')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('bestanden', done.stdout)


class CliTests(SyncTestBase):
    def test_cli_sync_commands(self):
        import contextlib
        import io
        from socialscraper import cli
        seed(self.db)
        os_cwd = pathlib.Path.cwd()
        import os
        os.chdir(self.root)
        self.addCleanup(os.chdir, os_cwd)
        with mock.patch.dict(os.environ, {'SS_DB_PATH': str(self.db)}):
            for action, expected in (('test', 0), ('push', 0), ('pull', 0)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.main(['sync', action])
                self.assertEqual(code, expected, out.getvalue())
                self.assertNotIn(KEY, out.getvalue())
            self.assertIn('Videos', out.getvalue())
            self.cloud.key = 'falsch'
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(['sync', 'push']), 1)
            self.assertIn('Fehler', out.getvalue())


if __name__ == '__main__':
    unittest.main()
