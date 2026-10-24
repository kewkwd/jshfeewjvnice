"""Cloud-Sync der Stimmungs-Einschätzungen und der Personen in Gruppen (Fake-PostgREST-Server, keine echte Cloud)."""
import pathlib
import sqlite3
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from test_sync import KEY, SyncTestBase, seed

from socialscraper import people_repo as pr, repo, sentiment_repo as srepo, sync
from socialscraper.store import Store


def enrich(db_path):
    with Store(str(db_path)) as s:
        db = s.db
        g = db.execute("SELECT id FROM groups WHERE name='Sammlung'").fetchone()[0]
        pr.add_people(db, g, 'commenter', [pr.person('tiktok', None, 'Fan')])
        pr.add_people(db, g, 'creator', [pr.person('tiktok', None, '@Anna')])
        items = srepo.open_comments(db)
        srepo.save_results(db, [(i['uid'], i['hash'], 'positiv' if n % 2 else 'negativ', 0.5 if n % 2 else -0.5)
                                for n, i in enumerate(items[:6])], 'claude-haiku-5-5', 1, when='2026-10-05T10:00:00Z')


class SentimentAndPeopleSync(SyncTestBase):
    def test_push_sends_sentiment_and_people_idempotently_with_group_names(self):
        seed(self.db)
        enrich(self.db)
        result = self.push()
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['counts']['comment_sentiment'], result['counts']['group_people']), (6, 2))
        row = self.cloud.tables['comment_sentiment'][('tiktok:1-1',)]
        self.assertEqual((row['label'], row['score'], row['model'], row['prompt_version']), ('negativ', -0.5, 'claude-haiku-5-5', 1))
        self.assertEqual(row['analyzed_at'], '2026-10-05T10:00:00Z')
        self.assertEqual(len(row['text_hash']), 64)
        people = self.cloud.tables['group_people']
        self.assertEqual(set(people), {('Sammlung', 'commenter', 'tiktok', 'fan'), ('Sammlung', 'creator', 'tiktok', 'anna')})
        self.assertNotIn('group_id', next(iter(people.values())))
        self.assertEqual(self.cloud.keys['comment_sentiment'], ['comment_uid'])
        self.assertEqual(self.cloud.keys['group_people'], ['group_name', 'role', 'platform', 'author_key'])
        before = len(self.cloud.posts())
        again = self.push()
        self.assertEqual((again['counts']['comment_sentiment'], again['counts']['group_people']), (6, 2))      # Wasserstand >= : gleiche Sekunde nochmals
        self.assertEqual(len(self.cloud.tables['comment_sentiment']), 6)                                         # Upsert: keine Doppelten
        self.assertGreater(len(self.cloud.posts()), before)

    def test_new_sentiment_after_first_push_is_sent_with_keyset_paging(self):
        seed(self.db, n=4)
        enrich(self.db)
        self.push()
        with Store(str(self.db)) as s:
            items = srepo.open_comments(s.db)
            srepo.save_results(s.db, [(i['uid'], i['hash'], 'neutral', 0.0) for i in items], 'm', 1, when='2026-10-06T10:00:00Z')
        self.cloud.requests.clear()
        result = self.push(batch=1)
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.cloud.tables['comment_sentiment']), 8)
        self.assertEqual([n for _, _, n in self.cloud.posts('comment_sentiment')], [1] * len(self.cloud.posts('comment_sentiment')))

    def test_pull_merges_sentiment_and_people_and_newer_wins(self):
        seed(self.db)
        enrich(self.db)
        self.push()
        other = self.root / 'other' / 'scrape.db'
        with Store(str(other)) as s:       # zweiter Rechner: kennt nichts
            pass
        result = self.pull_into(other)
        self.assertTrue(result['ok'], result)
        with Store(str(other)) as s:
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM comment_sentiment').fetchone()[0], 6)
            self.assertEqual(s.db.execute("SELECT g.name, p.role, p.author_key FROM group_people p JOIN groups g ON g.id=p.group_id ORDER BY 2").fetchall(),
                             [('Sammlung', 'commenter', 'fan'), ('Sammlung', 'creator', 'anna')])
            # lokal neuere Einstufung bleibt, ältere wird nicht darübergelegt
            s.db.execute("UPDATE comment_sentiment SET label='negativ', analyzed_at='2030-01-01T00:00:00Z' WHERE comment_uid='tiktok:1-1'")
            s.db.commit()
        self.pull_into(other, full=True)
        with Store(str(other)) as s:
            self.assertEqual(s.db.execute("SELECT label FROM comment_sentiment WHERE comment_uid='tiktok:1-1'").fetchone()[0], 'negativ')

    def pull_into(self, path, **kw):
        return sync.pull(self.root, db_path=path, **kw)

    def test_bad_rows_from_the_cloud_do_not_break_the_pull(self):
        self.cloud.tables['comment_sentiment'] = {
            ('x:1',): {'comment_uid': 'x:1', 'label': 'super', 'score': 0.5, 'analyzed_at': '2026-10-05T10:00:00Z'},    # unbekanntes Label
            ('x:2',): {'comment_uid': 'x:2', 'label': 'positiv', 'score': 7, 'analyzed_at': '2026-10-05T10:00:00Z'},     # Wert außerhalb
            ('x:3',): {'comment_uid': 'x:3', 'label': 'positiv', 'score': 0.5, 'analyzed_at': '2026-10-05T10:00:00Z'}}
        self.cloud.tables['group_people'] = {('Nix', 'creator', 'tiktok', 'a'): {'group_name': 'Nix', 'role': 'creator', 'platform': 'tiktok',
                                                                              'author_key': 'a', 'display_name': 'A', 'added_at': '2026-10-05T10:00:00Z'},
                                             ('G', 'boss', 'tiktok', 'a'): {'group_name': 'G', 'role': 'boss', 'platform': 'tiktok',
                                                                           'author_key': 'a', 'display_name': 'A', 'added_at': '2026-10-05T10:00:00Z'}}
        seed(self.db)
        result = self.pull()
        self.assertTrue(result['ok'], result)
        with Store(str(self.db)) as s:
            self.assertEqual([r[0] for r in s.db.execute('SELECT comment_uid FROM comment_sentiment')], ['x:3'])
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM group_people').fetchone()[0], 0)       # Gruppe unbekannt bzw. Rolle falsch

    def test_older_cloud_without_new_tables_is_skipped_not_fatal(self):
        seed(self.db)
        enrich(self.db)
        self.cloud.missing |= {'comment_sentiment', 'group_people'}
        result = self.push()
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['counts']['videos'], result['counts']['comment_sentiment']), (4, 0))
        self.assertIn('deploy/supabase_schema.sql', result['warning'])
        self.assertIn('comment_sentiment', result['warning'])
        pulled = self.pull_into(self.root / 'p2' / 'scrape.db')
        self.assertTrue(pulled['ok'], pulled)
        self.assertIn('supabase_schema.sql', pulled['warning'])
        checked = sync.test(self.root)
        self.assertTrue(checked['ok'], checked)                                                         # Verbindung gut, nur Hinweis
        self.assertEqual(checked['tables']['group_people'], 'fehlt')
        self.assertIn('fehlen noch Tabellen', checked['message'])
        self.cloud.missing.add('videos')
        self.assertFalse(sync.test(self.root)['ok'])                                                    # fehlt eine Pflichttabelle: Fehler

    def test_old_local_database_without_new_tables_pushes_the_rest(self):
        old = self.root / 'alt.db'
        con = sqlite3.connect(old)
        con.executescript('''CREATE TABLE videos (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, url TEXT, author TEXT, author_id TEXT, caption TEXT,
            created_at TEXT, duration_s REAL, view_count INTEGER, like_count INTEGER, comment_count INTEGER, share_count INTEGER, save_count INTEGER,
            hashtags TEXT, mentions TEXT, music TEXT, language TEXT, thumbnail TEXT, raw TEXT, first_seen_at TEXT, fetched_at TEXT);
            CREATE TABLE comments (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, comment_id TEXT, text TEXT, author TEXT, author_id TEXT,
            created_at TEXT, like_count INTEGER, reply_count INTEGER, parent_id TEXT, is_author_reply INTEGER, language TEXT, raw TEXT, first_seen_at TEXT, fetched_at TEXT);
            CREATE TABLE video_snapshots (video_uid TEXT, captured_at TEXT, view_count INTEGER, like_count INTEGER, comment_count INTEGER, share_count INTEGER);
            CREATE TABLE groups (id INTEGER PRIMARY KEY, name TEXT, color TEXT, note TEXT, created_at TEXT);
            CREATE TABLE group_videos (group_id INTEGER, video_uid TEXT, added_at TEXT);
            INSERT INTO videos (uid, platform, video_id, fetched_at) VALUES ('tiktok:1', 'tiktok', '1', '2026-10-01 10:00:00');''')
        con.commit()
        con.close()
        result = sync.push(self.root, db_path=old)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['counts']['videos'], 1)
        status = sync.status(self.root, db_path=old)
        self.assertIsNone(status['config_problem'])

    def test_status_counts_pending_new_tables(self):
        seed(self.db)
        enrich(self.db)
        pending = sync.status(self.root, db_path=self.db)['pending']
        self.assertEqual((pending['comment_sentiment'], pending['group_people']), (6, 2))

    def test_key_never_in_results_for_new_tables(self):
        seed(self.db)
        enrich(self.db)
        self.cloud.faults.append(401)
        result = self.push()
        self.assertFalse(result['ok'])
        self.assertNotIn(KEY, repr(result))


if __name__ == '__main__':
    unittest.main()
