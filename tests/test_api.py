"""Tests der lokalen Schnittstelle mit echten HTTP-Anfragen gegen einen Wegwerf-Server und eine Wegwerf-Datenbank."""
import json
import pathlib
import socket
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

from socialscraper import api, integration, repo
from socialscraper.models import Comment, Video
from socialscraper.store import Store

TOKEN = 'test-token-' + 'x' * 30
ORIGIN = 'http://localhost:5173'
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def make_db(path):
    with Store(str(path)) as store:
        videos = [Video('tiktok', f'{i}', f'https://www.tiktok.com/@a/video/{i}', author='anna' if i % 2 else 'ben',
                        caption=f'Video {i} mit 100% Spaß_ und #tag{i}', view_count=i * 1000, like_count=i * 10,
                        comment_count=2, share_count=1, hashtags=[f'tag{i}'], created_at='2026-10-01T10:00:00+00:00',
                        raw={'geheim': 'nur-intern'}) for i in range(1, 6)]
        videos.append(Video('youtube', 'yt1', 'https://youtu.be/yt1', author='anna', caption='YouTube Clip', view_count=5))
        for v in videos:
            comments = [Comment(v.platform, v.video_id, f'{v.video_id}-c{j}', f'Kommentar {j} zu {v.video_id}', author='fan',
                                like_count=j, is_author_reply=j == 2, parent_id=None if j == 1 else f'{v.video_id}-c1')
                        for j in (1, 2)]
            store.save_result(v, comments, f'2026-10-0{1 + videos.index(v) % 3}T12:00:00.123456+00:00')
        store.snapshot(videos[0], '2026-10-05T12:00:00+00:00')
        db = store.db
        db.execute("UPDATE comments SET first_seen_at='2026-10-03T00:00:00Z'")
        db.execute("UPDATE videos SET first_seen_at='2026-10-03T00:00:00Z'")
        db.execute("UPDATE comments SET first_seen_at='2026-10-02 08:00:00' WHERE video_id='1'")   # altes Format (Migration)
        db.execute("UPDATE comments SET first_seen_at='2026-10-10T08:00:00Z' WHERE video_id='2'")
        db.commit()
        repo.ensure_default_accounts(db)
        group = repo.create_group(db, 'Wichtig & Co')
        repo.add_videos_to_group(db, group['id'], ['tiktok:1', 'tiktok:2'])
        return group


class ApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.db = cls.root / 'data' / 'scrape.db'
        cls.group = make_db(cls.db)
        cfg = integration.default_config()
        cfg['api'].update(token=TOKEN, cors_origins=[ORIGIN])
        cls.handle = api.start(cls.root, db_path=cls.db, port=0, cfg=cfg)
        cls.base = cls.handle.url

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls._tmp.cleanup()

    def get(self, path, token=TOKEN, headers=None, method='GET', data=None):
        req = urllib.request.Request(self.base + path, method=method, data=data, headers=dict(headers or {}))
        if token:
            req.add_header('Authorization', 'Bearer ' + token)
        try:
            with _OPENER.open(req, timeout=10) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None), resp.headers
        except urllib.error.HTTPError as error:
            raw = error.read()
            error.close()
            return error.code, (json.loads(raw) if raw else None), error.headers


class ApiTests(ApiTestBase):
    def test_health_is_public_and_reports_token_validity(self):
        status, body, _ = self.get('/v1/health', token=None)
        self.assertEqual((status, body['ok'], body['authenticated'], body['schema_version']), (200, True, False, 1))
        self.assertTrue(self.get('/v1/health')[1]['authenticated'])
        self.assertFalse(self.get('/v1/health', token='falsch')[1]['authenticated'])

    def test_auth_required_everywhere_else(self):
        for path in ('/v1/stats', '/v1/videos', '/v1/groups', '/v1/accounts', '/v1/videos/tiktok%3A1', '/v1/comments/new?since=2026-01-01',
                     '/v1/unbekannt'):
            status, body, headers = self.get(path, token=None)
            self.assertEqual(status, 401, path)
            self.assertEqual(body['error']['code'], 'unauthorized')
            self.assertIn('Bearer', headers['WWW-Authenticate'])
        self.assertEqual(self.get('/v1/stats', token='x' * 40)[0], 401)
        self.assertEqual(self.get('/v1/stats', token=None, headers={'Authorization': 'Basic abc'})[0], 401)
        # Token in der Adresse zählt nicht
        self.assertEqual(self.get('/v1/stats?token=' + TOKEN)[0], 400)
        self.assertEqual(self.get('/v1/stats?token=' + TOKEN, token=None)[0], 401)

    def test_stats(self):
        status, body, _ = self.get('/v1/stats')
        self.assertEqual(status, 200)
        self.assertEqual(body['videos'], {'total': 6, 'by_platform': {'tiktok': 5, 'youtube': 1}})
        self.assertEqual(body['comments']['total'], 12)
        self.assertEqual(body['groups'], 1)
        self.assertEqual(body['accounts'], 4)          # Hauptkonto je Plattform: TikTok, Instagram, Reddit, X
        self.assertRegex(body['last_fetched_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')

    def test_video_list_shape_filters_paging(self):
        _, body, _ = self.get('/v1/videos?limit=2&offset=1&sort=view_count&order=asc')
        self.assertEqual((body['total'], body['limit'], body['offset'], len(body['items'])), (6, 2, 1, 2))
        self.assertEqual([i['uid'] for i in body['items']], ['tiktok:1', 'tiktok:2'])
        item = body['items'][0]
        for key in ('uid', 'platform', 'video_id', 'url', 'author', 'caption', 'view_count', 'hashtags', 'first_seen_at', 'fetched_at', 'groups'):
            self.assertIn(key, item)
        self.assertNotIn('raw', item)
        self.assertEqual(item['hashtags'], ['tag1'])
        self.assertEqual(item['created_at'], '2026-10-01T10:00:00Z')
        self.assertEqual(item['groups'][0]['name'], 'Wichtig & Co')
        self.assertEqual(self.get('/v1/videos?platform=youtube')[1]['total'], 1)
        self.assertEqual(self.get('/v1/videos?group=%d' % self.group['id'])[1]['total'], 2)
        self.assertEqual(self.get('/v1/videos?group=' + urllib.parse.quote('Wichtig & Co'))[1]['total'], 2)
        self.assertEqual(self.get('/v1/videos?group=gibtesnicht')[1]['total'], 0)
        self.assertEqual(self.get('/v1/videos?q=anna')[1]['total'], 4)
        self.assertEqual(self.get('/v1/videos?since=2999-01-01')[1]['total'], 0)
        self.assertEqual(self.get('/v1/videos?since=2000-01-01T00:00:00%2B02:00')[1]['total'], 6)

    def test_search_treats_wildcards_literally(self):
        self.assertEqual(self.get('/v1/videos?q=' + urllib.parse.quote('100%'))[1]['total'], 5)
        self.assertEqual(self.get('/v1/videos?q=' + urllib.parse.quote('%'))[1]['total'], 5)
        self.assertEqual(self.get('/v1/videos?q=' + urllib.parse.quote('Spaß_'))[1]['total'], 5)
        self.assertEqual(self.get('/v1/videos?q=' + urllib.parse.quote("'; DROP TABLE videos; --"))[1]['total'], 0)
        self.assertEqual(self.get('/v1/stats')[1]['videos']['total'], 6)

    def test_video_detail_uid_is_url_decoded_and_raw_optional(self):
        status, body, _ = self.get('/v1/videos/tiktok%3A3')
        self.assertEqual((status, body['item']['uid']), (200, 'tiktok:3'))
        self.assertNotIn('raw', body['item'])
        self.assertEqual(self.get('/v1/videos/tiktok:3?include=raw')[1]['item']['raw'], {'geheim': 'nur-intern'})
        self.assertEqual(self.get('/v1/videos/tiktok%3A999')[0], 404)
        self.assertEqual(self.get('/v1/videos/%FF')[0], 400)
        self.assertEqual(self.get('/v1/videos/a%2Fb')[0], 404)

    def test_comments_and_history(self):
        _, body, _ = self.get('/v1/videos/tiktok%3A1/comments')
        self.assertEqual((body['video_uid'], body['total']), ('tiktok:1', 2))
        first = body['items'][0]
        self.assertIsInstance(first['is_author_reply'], bool)
        self.assertNotIn('raw', first)
        self.assertEqual(self.get('/v1/videos/tiktok%3A1/comments?parent=none')[1]['total'], 1)
        self.assertEqual(self.get('/v1/videos/tiktok%3A1/comments?q=Kommentar%202')[1]['total'], 1)
        self.assertEqual(self.get('/v1/videos/nein/comments')[0], 404)
        _, hist, _ = self.get('/v1/videos/tiktok%3A1/history')
        self.assertEqual(hist['total'], 2)
        captured = [h['captured_at'] for h in hist['items']]
        self.assertEqual(captured, sorted(captured))
        self.assertTrue(all(c.endswith('Z') for c in captured))
        self.assertEqual(set(hist['items'][0]), {'captured_at', 'view_count', 'like_count', 'comment_count', 'share_count'})
        self.assertEqual(self.get('/v1/videos/tiktok%3A1/history?since=2026-10-05')[1]['total'], 1)

    def test_new_comments_since_handles_mixed_stored_formats(self):
        status, body, _ = self.get('/v1/comments/new?since=2026-10-09T00:00:00Z')
        self.assertEqual(status, 200)
        self.assertEqual({c['video_uid'] for c in body['items']}, {'tiktok:2'})
        self.assertEqual(body['items'][0]['video_url'], 'https://www.tiktok.com/@a/video/2')
        self.assertEqual(body['since'], '2026-10-09T00:00:00Z')
        self.assertEqual(self.get('/v1/comments/new?since=2026-10-02T07:59:59Z')[1]['total'], 12)
        self.assertEqual(self.get('/v1/comments/new?since=2026-10-02T08:00:01Z')[1]['total'], 10)
        self.assertEqual(self.get('/v1/comments/new?since=2026-10-03&platform=youtube')[1]['total'], 2)
        self.assertEqual(self.get('/v1/comments/new')[0], 400)
        self.assertEqual(self.get('/v1/comments/new?since=gestern')[0], 400)

    def test_groups_and_accounts_without_secrets(self):
        _, groups, _ = self.get('/v1/groups')
        self.assertEqual(groups['items'][0]['name'], 'Wichtig & Co')
        self.assertEqual(groups['items'][0]['video_count'], 2)
        _, accounts, _ = self.get('/v1/accounts')
        self.assertEqual({a['platform'] for a in accounts['items']}, {'tiktok', 'instagram', 'reddit', 'x'})
        text = json.dumps(accounts)
        for word in ('cookie', 'password', 'token', 'secret'):
            self.assertNotIn(word, text.lower())

    def test_parameter_validation(self):
        for bad in ('limit=0', 'limit=501', 'limit=abc', 'offset=-1', 'platform=myspace', 'sort=uid;drop', 'order=sideways',
                    'unbekannt=1', 'limit=1&limit=2', 'since=nonsense', 'q=' + 'a' * 300, 'q=a%00b'):
            status, body, _ = self.get('/v1/videos?' + bad)
            self.assertEqual(status, 400, bad)
            self.assertEqual(body['error']['code'], 'bad_request')
        self.assertEqual(self.get('/v1/videos?limit=500')[0], 200)
        self.assertEqual(self.get('/v1/videos?' + 'a=1&' * 3000)[0], 400)

    def test_errors_are_json_without_traces(self):
        for path, status in (('/', 404), ('/v2/videos', 404), ('/v1/', 404), ('/v1/nope', 404), ('/v1/videos/a/b/c/d', 404)):
            code, body, headers = self.get(path)
            self.assertEqual(code, status, path)
            self.assertIn('application/json', headers['Content-Type'])
            self.assertIn('message', body['error'])
            self.assertNotIn('Traceback', json.dumps(body))

    def test_only_read_methods(self):
        for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            status, body, headers = self.get('/v1/videos', method=method, data=b'{"x":1}' if method != 'DELETE' else None)
            self.assertEqual(status, 405, method)
            self.assertIn('GET', headers['Allow'])
            self.assertEqual(body['error']['code'], 'method_not_allowed')
        self.assertEqual(self.get('/v1/stats')[1]['videos']['total'], 6)

    def test_cors_only_for_listed_origins(self):
        status, _, headers = self.get('/v1/stats', headers={'Origin': ORIGIN})
        self.assertEqual(headers['Access-Control-Allow-Origin'], ORIGIN)
        self.assertEqual(headers['Vary'], 'Origin')
        _, _, headers = self.get('/v1/stats', headers={'Origin': 'http://boese.example'})
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))
        _, _, headers = self.get('/v1/stats')
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))

    def test_preflight(self):
        pre = {'Origin': ORIGIN, 'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'authorization'}
        status, _, headers = self.get('/v1/videos', token=None, method='OPTIONS', headers=pre)
        self.assertEqual(status, 204)
        self.assertEqual(headers['Access-Control-Allow-Origin'], ORIGIN)
        self.assertIn('Authorization', headers['Access-Control-Allow-Headers'])
        self.assertIn('GET', headers['Access-Control-Allow-Methods'])
        self.assertNotIn('POST', headers['Access-Control-Allow-Methods'])
        status, body, headers = self.get('/v1/videos', token=None, method='OPTIONS', headers={**pre, 'Origin': 'http://boese.example'})
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get('Access-Control-Allow-Origin'))

    def test_foreign_host_header_rejected(self):
        status, body, _ = self.get('/v1/stats', headers={'Host': 'boese.example'})
        self.assertEqual((status, body['error']['code']), (403, 'bad_host'))
        self.assertEqual(self.get('/v1/stats', headers={'Host': 'localhost:1234'})[0], 200)

    def test_read_only_connection_cannot_write(self):
        import sqlite3
        db = sqlite3.connect(f'{self.db.resolve().as_uri()}?mode=ro', uri=True)
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.OperationalError):
            db.execute("DELETE FROM videos")


class ApiLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_start_requires_token(self):
        with self.assertRaises(api.ApiError) as ctx:
            api.start(self.root, port=0)
        self.assertIn('Token', str(ctx.exception))

    def test_missing_database_gives_503_not_crash(self):
        handle = api.start(self.root, db_path=self.root / 'nichts.db', port=0, token=TOKEN)
        try:
            req = urllib.request.Request(handle.url + '/v1/stats', headers={'Authorization': 'Bearer ' + TOKEN})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                _OPENER.open(req, timeout=5)
            self.assertEqual(ctx.exception.code, 503)
            ctx.exception.close()
            with _OPENER.open(handle.url + '/v1/health', timeout=5) as resp:
                self.assertFalse(json.loads(resp.read())['database'])
        finally:
            handle.stop()

    def test_port_in_use_gives_clear_message_and_stop_frees_port(self):
        db = self.root / 'a.db'
        make_db(db)
        first = api.start(self.root, db_path=db, port=0, token=TOKEN)
        try:
            with self.assertRaises(api.ApiError) as ctx:
                api.start(self.root, db_path=db, port=first.port, token=TOKEN)
            self.assertIn('belegt', str(ctx.exception))
            self.assertNotIn(TOKEN, str(ctx.exception))
        finally:
            first.stop()
        self.assertFalse(first.running)
        again = api.start(self.root, db_path=db, port=first.port, token=TOKEN)
        again.stop()
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', first.port), timeout=1).close()

    def test_binds_loopback_by_default(self):
        db = self.root / 'a.db'
        make_db(db)
        handle = api.start(self.root, db_path=db, port=0, token=TOKEN)
        try:
            self.assertEqual(handle.host, '127.0.0.1')
        finally:
            handle.stop()

    def test_start_if_enabled(self):
        self.assertEqual(api.start_if_enabled(self.root), (None, None))
        cfg = integration.default_config()
        cfg['api'].update(enabled=True, port=1, token=TOKEN)
        integration.save(cfg, self.root)
        cfg['api']['port'] = 0
        # Port 0 ist in der Datei nicht erlaubt; der Start mit fester Konfiguration läuft über start(cfg=...)
        handle, problem = api.start_if_enabled(self.root, db_path=self.root / 'x.db')
        if handle:
            handle.stop()
        else:
            self.assertIsNotNone(problem)
        integration.path(self.root).write_text('kaputt', encoding='utf-8')
        handle, problem = api.start_if_enabled(self.root)
        self.assertIsNone(handle)
        self.assertIn('beschädigt', problem)

    def test_time_helpers(self):
        self.assertEqual(api.to_iso('2026-10-11 04:15:33'), '2026-10-11T04:15:33Z')
        self.assertEqual(api.to_iso('2026-10-11T06:15:33.5+02:00'), '2026-10-11T04:15:33Z')
        self.assertEqual(api.to_iso('unlesbar'), 'unlesbar')
        self.assertIsNone(api.to_iso(None))


if __name__ == '__main__':
    unittest.main()
