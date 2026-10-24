"""Schnittstelle (/v1/channels, Länge in Videos, Gruppen und Statistik), Export (Länge, Kanäle) und Längenauswertung."""
import csv
import json
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.request

from socialscraper import api, chanlogic, channels, guicore, integration, repo
from socialscraper.models import Video
from socialscraper.store import Store
from socialscraper.ui import logic
from socialscraper.util import duration_class, format_duration

TOKEN = 'test-token-' + 'y' * 30
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
YT = 'https://www.youtube.com/watch?v='


def seed(path):
    with Store(str(path)) as store:
        repo.ensure_default_accounts(store.db)
        for vid, dur, views in (('a1', 45, 100), ('b2', 125, 300), ('c3', 3725, 50), ('d4', None, 70), ('e5', 600, 200), ('f6', 0, 5)):
            store.save_result(Video('youtube', vid + 'abcdefghi', f'{YT}{vid}abcdefghi', author='X', caption=vid, duration_s=dur,
                                    view_count=views, like_count=views // 10, comment_count=4), [], None)
        group = repo.create_group(store.db, 'Gruppe')
        repo.add_videos_to_group(store.db, group['id'], ['youtube:a1abcdefghi', 'youtube:b2abcdefghi', 'youtube:c3abcdefghi'])
        ref = channels.ChannelRef('youtube', 'channel', 'https://www.youtube.com/@apikanal', '@apikanal', '')
        t = channels.create_channel(store.db, ref, group_id=group['id'], pace_profile='careful')
        repo.add_channel_items(store.db, t['id'], 'youtube', [{'url': YT + f'q{i:010d}', 'uid': None} for i in range(10)])
        rows = store.db.execute('SELECT id FROM channel_items ORDER BY id').fetchall()
        for (i,), status in zip(rows, ['done'] * 4 + ['failed'] * 2):
            repo.set_channel_item(store.db, i, status, error='Fehler' if status == 'failed' else None, attempt=True)
        store.db.execute("UPDATE accounts SET proxy='http://user:*****@proxy.example:8080' WHERE platform='tiktok'")
        store.db.commit()
        return t['id']


class FormatTests(unittest.TestCase):
    def test_format_and_class(self):
        self.assertEqual([format_duration(x) for x in (0, 5, 59.6, 60, 125, 3600, 3725, 90061, None, '', -1, 'abc', True)],
                         ['0:00', '0:05', '1:00', '1:00', '2:05', '1:00:00', '1:02:05', '25:01:01', '', '', '', '', ''])
        self.assertEqual([duration_class(x) for x in (None, 0, 60, 60.5, 600, 600.5)], [None, 'short', 'short', 'medium', 'medium', 'long'])
        self.assertEqual(duration_class(100, 120, 3600), 'short')


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.db = cls.root / 'data' / 'scrape.db'
        cls.channel_id = seed(cls.db)
        cfg = integration.default_config()
        cfg['api'].update(token=TOKEN)
        cls.handle = api.start(cls.root, db_path=cls.db, port=0, cfg=cfg)

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls._tmp.cleanup()

    def get(self, path):
        request = urllib.request.Request(self.handle.url + path, headers={'Authorization': 'Bearer ' + TOKEN})
        with _OPENER.open(request, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))

    def test_channels_endpoint_shows_progress_and_no_secrets(self):
        body = self.get('/v1/channels')
        self.assertEqual(body['total'], 1)
        item = body['items'][0]
        self.assertEqual((item['platform'], item['channel'], item['pace_profile'], item['group']),
                         ('youtube', 'https://www.youtube.com/@apikanal', 'careful', 'Gruppe'))
        self.assertEqual(item['progress'], {'total': 10, 'done': 4, 'skipped': 0, 'pending': 4, 'running': 0, 'failed': 2})
        self.assertEqual(item['state'], 'queued')
        text = json.dumps(body)
        for secret in ('proxy', 'password', 'token', 'cookie', 'user:'):
            self.assertNotIn(secret, text.lower().replace('"pace_profile"', ''))
        single = self.get(f'/v1/channels/{self.channel_id}')
        self.assertEqual(single['item']['id'], self.channel_id)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get('/v1/channels/9999')
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_paused_channel_reports_reason(self):
        with Store(self.db) as s:
            channels.pause_channel(s.db, self.channel_id, repo.now() + __import__('datetime').timedelta(hours=2), 'CAPTCHA')
        try:
            item = self.get('/v1/channels')['items'][0]
            self.assertEqual((item['state'], item['pause_reason']), ('paused', 'CAPTCHA'))
            self.assertTrue(item['paused_until'].endswith('Z') or '+' in item['paused_until'])
        finally:
            with Store(self.db) as s:
                repo.update_channel(s.db, self.channel_id, backfill_state='queued', pause_until=None, pause_reason=None)

    def test_videos_have_duration_and_class(self):
        items = {i['video_id']: i for i in self.get('/v1/videos?limit=50')['items']}
        self.assertEqual((items['a1abcdefghi']['duration_s'], items['a1abcdefghi']['duration_class']), (45.0, 'short'))
        self.assertEqual(items['b2abcdefghi']['duration_class'], 'medium')
        self.assertEqual(items['c3abcdefghi']['duration_class'], 'long')
        self.assertIsNone(items['d4abcdefghi']['duration_s'])
        self.assertIsNone(items['d4abcdefghi']['duration_class'])
        one = self.get('/v1/videos/youtube%3Aa1abcdefghi')['item']        # Einzelabruf: dieselben Felder wie die Liste
        self.assertEqual((one['duration_s'], one['duration_class']), (45.0, 'short'))

    def test_stats_and_groups_carry_average_min_max(self):
        d = self.get('/v1/stats')['duration']
        self.assertEqual((d['min_s'], d['max_s'], d['measured'], d['unmeasured']), (0.0, 3725.0, 5, 1))
        self.assertEqual(d['avg_s'], round((45 + 125 + 3725 + 600 + 0) / 5, 1))
        g = self.get('/v1/groups')['items'][0]['duration']
        self.assertEqual((g['min_s'], g['max_s'], g['avg_s']), (45.0, 3725.0, round((45 + 125 + 3725) / 3, 1)))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / 'scrape.db'
        seed(self.db)

    def test_csv_has_both_length_columns_and_channel_file(self):
        out = self.root / 'csv'
        files = guicore.export_data(self.db, out, 'csv-excel')
        names = {f.name for f in files}
        self.assertIn('kanaele.csv', names)
        with (out / 'videos.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f, delimiter=';'))
        by = {r['Beschreibung']: r for r in rows}
        self.assertEqual((by['c3']['Länge (m:ss)'], by['c3']['Länge (Sekunden)']), ('1:02:05', '3725.0'))
        self.assertEqual((by['d4']['Länge (m:ss)'], by['d4']['Länge (Sekunden)']), ('', ''))
        self.assertEqual(by['a1']['Länge (m:ss)'], '0:45')
        with (out / 'kanaele.csv').open(encoding='utf-8-sig', newline='') as f:
            channel = list(csv.DictReader(f, delimiter=';'))[0]
        self.assertEqual((channel['Plattform'], channel['Videos gefunden'], channel['Abgerufen'], channel['Fehlgeschlagen'], channel['Tempo']),
                         ('YouTube', '10', '4', '2', 'Vorsichtig'))

    def test_jsonl_has_raw_duration_and_channels(self):
        out = self.root / 'jsonl'
        files = guicore.export_data(self.db, out, 'jsonl')
        self.assertIn('channels.jsonl', {f.name for f in files})
        videos = [json.loads(line) for line in (out / 'videos.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual({v['video_id']: v['duration_s'] for v in videos}['c3abcdefghi'], 3725.0)

    def test_no_channel_file_without_channels(self):
        with Store(self.db) as s:
            for c in repo.list_channels(s.db):
                repo.delete_target(s.db, c['id'])
        files = guicore.export_data(self.db, self.root / 'o', 'csv')
        self.assertNotIn('kanaele.csv', {f.name for f in files})


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = pathlib.Path(self.tmp.name) / 'scrape.db'
        seed(self.db)

    def test_comparison_by_class_ignores_empty_values(self):
        rows = {r['key']: r for r in logic.length_comparison(self.db)}
        self.assertEqual([rows[k]['videos'] for k in ('short', 'medium', 'long', 'none')], [2, 2, 1, 1])
        self.assertEqual(rows['short']['avg_views'], (100 + 5) / 2)          # 0 s zählt als kurz, das Video ohne Länge nicht
        self.assertEqual(rows['none']['avg_views'], 70)
        self.assertIsNone(rows['none']['avg_length'])
        self.assertEqual(rows['long']['avg_length'], 3725)

    def test_comparison_respects_group_and_limits(self):
        group = repo_group(self.db)
        rows = {r['key']: r['videos'] for r in logic.length_comparison(self.db, group=group)}
        self.assertEqual(rows, {'short': 1, 'medium': 1, 'long': 1, 'none': 0})
        rows = {r['key']: r['videos'] for r in logic.length_comparison(self.db, short=100, long=1000)}
        self.assertEqual((rows['short'], rows['medium'], rows['long']), (2, 2, 1))

    def test_empty_database(self):
        self.assertEqual(logic.length_comparison(self.db.with_name('gibtsnicht.db')), [])
        empty = self.db.with_name('leer.db')
        Store(str(empty)).close()
        self.assertEqual([r['videos'] for r in logic.length_comparison(empty)], [0, 0, 0, 0])

    def test_filter_and_sort_in_query(self):
        short = logic.query_videos(self.db, length='short')
        self.assertEqual(sorted(r['caption'] for r in short), ['a1', 'f6'])
        self.assertTrue(all(r['duration_s'] is not None for r in logic.query_videos(self.db, length='long')))
        by_length = logic.query_videos(self.db, sort='length', descending=False)
        self.assertEqual([r['caption'] for r in by_length][-1], 'd4')            # leere Werte am Ende
        self.assertEqual([r['caption'] for r in by_length][:2], ['f6', 'a1'])
        by_length = logic.query_videos(self.db, sort='length', descending=True)
        self.assertEqual([r['caption'] for r in by_length][-1], 'd4')
        with self.assertRaises(ValueError):
            logic.query_videos(self.db, length='riesig')

    def test_settings_validation_of_limits(self):
        from socialscraper.config import Settings, validate
        validate(Settings(length_short_s=30, length_long_s=900))
        for short, long in ((600, 60), (60, 60), (0, 10), (10, 100000)):
            with self.assertRaises(ValueError):
                validate(Settings(length_short_s=short, length_long_s=long))


def repo_group(db):
    with Store(str(db)) as s:
        return s.db.execute('SELECT id FROM groups').fetchone()[0]


if __name__ == '__main__':
    unittest.main()
