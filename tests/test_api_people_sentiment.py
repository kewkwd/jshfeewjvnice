"""Schnittstelle für PULSE: /v1/sentiment, Stimmung an Kommentaren, Personen, Gruppen mit Zählern, neue Autoren.
Echte HTTP-Anfragen gegen einen Wegwerf-Server und eine Wegwerf-Datenbank."""
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import api, integration, people_repo as pr, repo, sentiment_repo as srepo
from socialscraper.store import Store
from test_people import build

TOKEN = 'test-token-' + 'x' * 30
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.db = cls.root / 'data' / 'scrape.db'
        build(cls.db)
        with Store(cls.db) as s:
            db = s.db
            db.execute("UPDATE comments SET first_seen_at='2026-01-01T00:00:00Z'")
            db.execute("UPDATE videos SET first_seen_at='2026-01-01T00:00:00Z'")
            db.execute("UPDATE comments SET first_seen_at='2026-10-10T08:00:00Z' WHERE author='Zoë'")
            db.commit()
            cls.g1, cls.g2 = repo.create_group(db, 'Beobachtung'), repo.create_group(db, 'Leer & Co')
            repo.add_videos_to_group(db, cls.g1['id'], ['tiktok:a1'])
            pr.add_people(db, cls.g1['id'], 'commenter', [pr.person('tiktok', None, 'Max')])
            pr.add_people(db, cls.g1['id'], 'creator', [pr.person('youtube', None, 'Küchenfee')])
            items = {i['uid']: i for i in srepo.open_comments(db)}
            rows = [(u, items[u]['hash'], l, sc) for u, l, sc in (('tiktok:c1', 'positiv', 0.9), ('tiktok:c2', 'negativ', -0.6),
                                                                  ('tiktok:c3', 'neutral', 0.0), ('tiktok:c4', 'positiv', 0.5),
                                                                  ('youtube:c5', 'negativ', -0.2))]
            srepo.save_results(db, rows, 'claude-haiku-5-5', 1, when='2026-10-11T00:00:00Z')
        cfg = integration.default_config()
        cfg['api'].update(token=TOKEN)
        cls.handle = api.start(cls.root, db_path=cls.db, port=0, cfg=cfg)
        cls.base = cls.handle.url

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        cls._tmp.cleanup()

    def get(self, path, expect=200):
        req = urllib.request.Request(self.base + path, headers={'Authorization': 'Bearer ' + TOKEN})
        try:
            with _OPENER.open(req, timeout=10) as resp:
                self.assertEqual(resp.status, expect)
                return json.loads(resp.read())
        except urllib.error.HTTPError as error:
            body = error.read()
            error.close()
            self.assertEqual(error.code, expect, body)
            return json.loads(body or b'null')


class SentimentTests(Base):
    def test_overall_distribution_series_and_labels(self):
        d = self.get('/v1/sentiment')
        self.assertEqual((d['schema_version'], d['kind'], d['model'], d['interval']), (1, 'ki_einschaetzung', 'claude-haiku-5-5', 'day'))
        self.assertEqual((d['positive'], d['neutral'], d['negative'], d['analyzed'], d['total_comments']), (2, 1, 2, 5, 6))
        self.assertAlmostEqual(d['coverage'], 5 / 6, places=3)
        self.assertAlmostEqual(d['share']['positive'], 0.4)
        self.assertAlmostEqual(d['score_avg'], (0.9 - 0.6 + 0 + 0.5 - 0.2) / 5, places=3)
        self.assertIn('KI-Einschätzung', d['note'])
        self.assertEqual(d['models'], {'claude-haiku-5-5': 5})
        self.assertEqual(sum(row['analyzed'] for row in d['series']), 5)
        self.assertTrue({'date', 'positive', 'neutral', 'negative', 'analyzed', 'share', 'score_avg'} <= set(d['series'][0]))

    def test_filters(self):
        self.assertEqual(self.get('/v1/sentiment?platform=youtube')['analyzed'], 1)
        self.assertEqual(self.get('/v1/sentiment?group=Beobachtung')['analyzed'], 3)
        self.assertEqual(self.get(f'/v1/sentiment?group={self.g1["id"]}')['analyzed'], 3)
        self.assertEqual(self.get('/v1/sentiment?group=gibt-es-nicht')['total_comments'], 0)
        self.assertEqual(self.get('/v1/sentiment?video=' + urllib.parse.quote('tiktok:a2'))['analyzed'], 1)
        d = self.get('/v1/sentiment?since=2026-10-10&until=2026-10-10')
        self.assertEqual(d['total_comments'], 1)                                 # Zoë, erstmals gesehen am 10.10. (kein Plattformdatum)
        self.assertEqual(self.get('/v1/sentiment?interval=week')['interval'], 'week')
        empty = self.get('/v1/sentiment?group=' + urllib.parse.quote('Leer & Co'))
        self.assertEqual((empty['analyzed'], empty['share']['positive'], empty['score_avg'], empty['model'], empty['series']),
                         (0, None, None, None, []))

    def test_bad_parameters_and_unknown_video(self):
        self.get('/v1/sentiment?interval=jahr', 400)
        self.get('/v1/sentiment?platform=myspace', 400)
        self.get('/v1/sentiment?since=gestern', 400)
        self.get('/v1/sentiment?until=morgen', 400)
        self.get('/v1/sentiment?foo=1', 400)
        self.get('/v1/sentiment?video=' + urllib.parse.quote('tiktok:nix'), 404)

    def test_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _OPENER.open(self.base + '/v1/sentiment', timeout=10)
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 401)

    def test_comments_carry_sentiment_only_when_valid(self):
        items = {i['comment_id']: i for i in self.get('/v1/videos/' + urllib.parse.quote('tiktok:a1') + '/comments')['items']}
        self.assertEqual(items['c1']['sentiment']['label'], 'positiv')
        self.assertEqual((items['c1']['sentiment']['score'], items['c1']['sentiment']['kind'], items['c1']['sentiment']['model']),
                         (0.9, 'ki_einschaetzung', 'claude-haiku-5-5'))
        self.assertTrue(items['c1']['sentiment']['analyzed_at'].endswith('Z'))
        self.assertIsNone(self.get('/v1/videos/' + urllib.parse.quote('tiktok:a1') + '/comments?parent=none')['items'][0].get('x'))
        feed = self.get('/v1/comments/new?since=2000-01-01')
        self.assertTrue(all('sentiment' in i for i in feed['items']))
        with Store(self.db) as s:                       # Text geändert: die Einstufung gilt nicht mehr
            s.db.execute("UPDATE comments SET text='ganz anders' WHERE comment_id='c1'")
            s.db.commit()
        items = {i['comment_id']: i for i in self.get('/v1/videos/' + urllib.parse.quote('tiktok:a1') + '/comments')['items']}
        self.assertIsNone(items['c1']['sentiment'])
        with Store(self.db) as s:
            s.db.execute("UPDATE comments SET text='Super' WHERE comment_id='c1'")
            s.db.commit()


class PeopleApiTests(Base):
    def test_groups_have_role_counts(self):
        groups = {g['name']: g for g in self.get('/v1/groups')['items']}
        self.assertEqual((groups['Beobachtung']['video_count'], groups['Beobachtung']['creator_count'], groups['Beobachtung']['commenter_count']), (1, 1, 1))
        self.assertEqual((groups['Leer & Co']['creator_count'], groups['Leer & Co']['commenter_count']), (0, 0))

    def test_people_lists_filters_and_fields(self):
        d = self.get('/v1/people?role=commenter')
        self.assertEqual(d['total'], 4)
        by = {(p['platform'], p['author_key']): p for p in d['items']}
        mx = by[('tiktok', 'max')]
        self.assertEqual((mx['comments'], mx['videos'], mx['role']), (3, 2, 'commenter'))
        self.assertEqual([g['name'] for g in mx['groups']], ['Beobachtung'])
        self.assertEqual(mx['sentiment']['kind'], 'ki_einschaetzung')
        self.assertEqual((mx['sentiment']['analyzed'], mx['sentiment']['positive'], mx['sentiment']['negative'], mx['sentiment']['neutral']), (3, 2, 1, 0))
        self.assertIsNone(mx['is_new'])                       # ohne first_seen_since gibt es kein „neu“
        self.assertTrue(mx['first_seen_at'].endswith('Z'))
        c = self.get('/v1/people?role=creator')
        self.assertEqual(c['total'], 3)
        self.assertIn('views', c['items'][0])
        self.assertNotIn('comments', c['items'][0])
        both = self.get('/v1/people')
        self.assertEqual(both['total'], 7)
        self.assertEqual({p['role'] for p in both['items']}, {'creator', 'commenter'})
        newest = self.get('/v1/people?sort=seen&order=desc')['items']        # beide Rollen: nach erster Sichtung sortiert
        self.assertEqual(newest[0]['display_name'], 'Zoë')
        self.assertEqual(self.get('/v1/people?sort=seen&order=asc')['items'][-1]['display_name'], 'Zoë')
        self.assertEqual(len(self.get('/v1/people?limit=3&offset=5')['items']), 2)
        self.assertEqual([p['display_name'] for p in self.get('/v1/people?role=commenter&q=' + urllib.parse.quote('ZOË'))['items']], ['Zoë'])
        self.assertEqual(self.get('/v1/people?role=commenter&platform=youtube')['total'], 1)
        self.assertEqual(self.get('/v1/people?role=commenter&group=Beobachtung')['total'], 1)
        self.assertEqual(self.get('/v1/people?role=commenter&group=none')['total'], 3)
        self.assertEqual(self.get('/v1/people?role=commenter&sort=name&order=asc')['items'][0]['display_name'], 'ärger')
        self.get('/v1/people?role=boss', 400)
        self.get('/v1/people?sort=farbe', 400)

    def test_new_authors_first_seen_since_and_known(self):
        d = self.get('/v1/people?role=commenter&first_seen_since=2026-10-10T00:00:00Z&known=false')
        self.assertEqual([p['author_key'] for p in d['items']], ['z-1'])
        self.assertTrue(d['items'][0]['is_new'])
        self.assertEqual(d['first_seen_since'], '2026-10-10T00:00:00Z')
        k = self.get('/v1/people?role=commenter&first_seen_since=2026-10-10T00:00:00Z&known=true')
        self.assertEqual(k['total'], 3)
        self.assertFalse(any(p['is_new'] for p in k['items']))
        flags = self.get('/v1/people?role=commenter&first_seen_since=2026-10-10T00:00:00Z')
        self.assertEqual({p['author_key']: p['is_new'] for p in flags['items'] if p['platform'] == 'tiktok'}, {'max': False, 'z-1': True})
        self.get('/v1/people?known=true', 400)                 # ohne Zeitpunkt unklar, was „bekannt“ heißt
        self.get('/v1/people?known=vielleicht&first_seen_since=2026-10-10', 400)
        self.get('/v1/people?first_seen_since=irgendwann', 400)

    def test_stats_new_authors_and_feed_flag(self):
        self.assertNotIn('new_authors', self.get('/v1/stats'))
        s = self.get('/v1/stats?since=2026-10-10T00:00:00Z')['new_authors']
        self.assertEqual((s['commenters'], s['since']), (1, '2026-10-10T00:00:00Z'))
        self.assertEqual(s['creators'], 0)
        feed = {i['comment_id']: i for i in self.get('/v1/comments/new?since=2026-10-10T00:00:00Z')['items']}
        self.assertEqual(set(feed), {'c3'})
        self.assertTrue(feed['c3']['author_new'])

    def test_group_people_and_commenter_group_filter(self):
        d = self.get(f'/v1/groups/{self.g1["id"]}/people')
        self.assertEqual((d['group']['name'], d['total']), ('Beobachtung', 2))
        self.assertEqual({(m['role'], m['author_key']) for m in d['items']}, {('commenter', 'max'), ('creator', 'küchenfee')})
        self.assertEqual(self.get(f'/v1/groups/{self.g1["id"]}/people?role=creator')['total'], 1)
        self.assertEqual(self.get('/v1/groups/' + urllib.parse.quote('Beobachtung') + '/people')['total'], 2)
        self.get('/v1/groups/999/people', 404)
        items = self.get('/v1/videos/' + urllib.parse.quote('tiktok:a1') + '/comments?commenter_group=Beobachtung')['items']
        self.assertEqual({i['text'] for i in items}, {'Super', 'Mist'})
        self.assertEqual(self.get('/v1/videos/' + urllib.parse.quote('tiktok:a1') + '/comments?commenter_group=gibt-es-nicht')['total'], 0)
        feed = self.get('/v1/comments/new?since=2000-01-01&commenter_group=' + str(self.g1['id']))
        self.assertEqual({i['text'] for i in feed['items']}, {'Super', 'Mist', 'toll'})


class OldDatabaseTests(unittest.TestCase):
    def test_old_file_without_new_tables_still_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            db = root / 'alt.db'
            con = sqlite3.connect(db)
            con.executescript('''CREATE TABLE videos (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, url TEXT, author TEXT, author_id TEXT, view_count INTEGER,
                like_count INTEGER, first_seen_at TEXT, fetched_at TEXT);
                CREATE TABLE comments (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, comment_id TEXT, text TEXT, author TEXT, author_id TEXT,
                created_at TEXT, like_count INTEGER, first_seen_at TEXT, fetched_at TEXT);
                INSERT INTO comments VALUES ('tiktok:1','tiktok','v','1','Hallo','Max',NULL,NULL,1,'2026-10-01T00:00:00Z',NULL);''')
            con.commit()
            con.close()
            cfg = integration.default_config()
            cfg['api'].update(token=TOKEN)
            handle = api.start(root, db_path=db, port=0, cfg=cfg)
            try:
                req = lambda p: json.loads(_OPENER.open(urllib.request.Request(handle.url + p, headers={'Authorization': 'Bearer ' + TOKEN}), timeout=10).read())
                d = req('/v1/sentiment')
                self.assertEqual((d['analyzed'], d['total_comments'], d['series']), (0, 1, []))
                p = req('/v1/people?role=commenter')
                self.assertEqual((p['total'], p['items'][0]['groups']), (1, []))
            finally:
                handle.stop()


if __name__ == '__main__':
    unittest.main()
