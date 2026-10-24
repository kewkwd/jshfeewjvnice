"""Tests der Personen in Gruppen (Ersteller, Kommentarschreiber) und der „neuen Autoren“: Speicher, Migration, Filter,
Export, Laufzähler. Alles in Wegwerf-Ordnern."""
import csv
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import guicore as core, people_repo as pr, repo, scheduler, sentiment_repo as srepo
from socialscraper.config import Settings
from socialscraper.models import Comment, Video
from socialscraper.store import Store
from socialscraper.ui import logic

os.environ.setdefault('SS_PACE', 'off')


def build(path):
    """Zwei Plattformen, gleiche Namen, verschiedene Schreibweisen, Kennungen."""
    with Store(path) as s:
        v1 = Video('tiktok', 'a1', 'https://t/a1', author='Küchenfee', author_id='uid-9', view_count=100, like_count=10)
        v2 = Video('tiktok', 'a2', 'https://t/a2', author='@Küchenfee', author_id='uid-9', view_count=50, like_count=5)
        v3 = Video('youtube', 'y1', 'https://y/y1', author='Küchenfee', view_count=7, like_count=1)
        v4 = Video('instagram', 'i1', 'https://i/i1', author='ÄRGER', view_count=3)
        s.save_result(v1, [Comment('tiktok', 'a1', 'c1', 'Super', author='Max', like_count=2, created_at='2026-10-01T10:00:00+00:00'),
                           Comment('tiktok', 'a1', 'c2', 'Mist', author='@max', like_count=3, created_at='2026-10-03T10:00:00+00:00'),
                           Comment('tiktok', 'a1', 'c3', 'ok', author='Zoë', author_id='z-1')], None)
        s.save_result(v2, [Comment('tiktok', 'a2', 'c4', 'toll', author='MAX', like_count=1)], None)
        s.save_result(v3, [Comment('youtube', 'y1', 'c5', 'Max auf YouTube', author='Max')], None)
        s.save_result(v4, [Comment('instagram', 'i1', 'c6', 'hm', author='ärger')], None)
        return [v1, v2, v3, v4]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / 'data' / 'scrape.db'
        env = patch.dict(os.environ, {'SS_DB_PATH': str(self.db)})
        env.start()
        self.addCleanup(env.stop)
        patch.object(core, 'ROOT', self.root).start()
        self.addCleanup(patch.stopall)
        self.videos = build(self.db)
        self.store = Store(self.db)
        self.addCleanup(self.store.close)
        self.conn = self.store.db


class KeyTests(unittest.TestCase):
    def test_author_key_prefers_id_then_lowercase_name_without_at(self):
        self.assertEqual(pr.author_key('uid-9', 'Egal'), 'uid-9')
        self.assertEqual(pr.author_key(None, '@Max'), 'max')
        self.assertEqual(pr.author_key('', ' MAX '), 'max')
        self.assertEqual(pr.author_key(None, 'Ärger'), 'ärger')     # Umlaute werden mit kleingeschrieben
        self.assertEqual(pr.author_key(None, None), '')
        self.assertEqual(pr.author_key(None, '@'), '')


class PeopleRepoTests(Base):
    def test_creators_group_by_key_and_keep_platforms_apart(self):
        rows, total = pr.list_people(self.conn, 'creator')
        got = {(r['platform'], r['author_key']): r for r in rows}
        self.assertEqual(total, 3)
        self.assertEqual(got[('tiktok', 'uid-9')]['videos'], 2)                 # @Küchenfee und Küchenfee mit gleicher Kennung
        self.assertEqual((got[('tiktok', 'uid-9')]['views'], got[('tiktok', 'uid-9')]['likes']), (150, 15))
        self.assertEqual(got[('youtube', 'küchenfee')]['videos'], 1)             # gleicher Name, andere Plattform: eigene Person
        self.assertIn(('instagram', 'ärger'), got)

    def test_commenters_merge_case_and_at_sign_but_not_platforms(self):
        rows, total = pr.list_people(self.conn, 'commenter')
        got = {(r['platform'], r['author_key']): r for r in rows}
        self.assertEqual(total, 4)
        tt = got[('tiktok', 'max')]
        self.assertEqual((tt['comments'], tt['videos'], tt['likes']), (3, 2, 6))      # Max, @max, MAX
        self.assertEqual((tt['first_activity'], tt['last_activity']), ('2026-10-01', tt['last_activity']))
        self.assertEqual(got[('youtube', 'max')]['comments'], 1)
        self.assertEqual(got[('tiktok', 'z-1')]['display_name'], 'Zoë')
        self.assertEqual(tt['analyzed'], 0)

    def test_filters_search_umlauts_platform_and_sort(self):
        names = lambda **kw: [r['display_name'] for r in pr.list_people(self.conn, 'commenter', **kw)[0]]
        self.assertEqual(names(search='ZOË'), ['Zoë'])
        self.assertEqual(names(search='ÄRGER'), ['ärger'])
        self.assertEqual(sorted(names(platform='youtube')), ['Max'])
        self.assertEqual(names(search='%'), [])
        self.assertEqual(pr.list_people(self.conn, 'commenter', sort='comments', desc=False)[0][-1]['comments'], 3)
        self.assertEqual(pr.list_people(self.conn, 'commenter', limit=2)[1], 4)                     # Gesamtzahl trotz Seitenlänge
        self.assertEqual(len(pr.list_people(self.conn, 'commenter', limit=2, offset=3)[0]), 1)
        with self.assertRaises(ValueError):
            pr.list_people(self.conn, 'chef')

    def test_add_remove_idempotent_multi_group_and_group_filters(self):
        g1, g2 = repo.create_group(self.conn, 'Eins'), repo.create_group(self.conn, 'Zwei')
        tt_max = pr.person('tiktok', None, 'Max')
        yt_max = pr.person('youtube', None, 'max')
        self.assertEqual(pr.add_people(self.conn, g1['id'], 'commenter', [tt_max, tt_max, pr.person('tiktok', None, '')]), (1, 0, 1))
        self.assertEqual(pr.add_people(self.conn, g1['id'], 'commenter', [pr.person('tiktok', None, '@MAX'), yt_max]), (1, 1, 0))   # gleiche Person, anderer Name
        self.assertEqual(pr.add_people(self.conn, g2['id'], 'commenter', [tt_max]), (1, 0, 0))                # mehrere Gruppen
        rows = {(r['platform'], r['author_key']): r for r in pr.list_people(self.conn, 'commenter')[0]}
        self.assertEqual(sorted(rows[('tiktok', 'max')]['group_names'].split(', ')), ['Eins', 'Zwei'])
        self.assertEqual(rows[('youtube', 'max')]['group_names'], 'Eins')
        self.assertIsNone(rows[('tiktok', 'z-1')]['group_names'])
        self.assertEqual({r['platform'] for r in pr.list_people(self.conn, 'commenter', group=g1['id'])[0]}, {'tiktok', 'youtube'})
        self.assertEqual(len(pr.list_people(self.conn, 'commenter', group=g2['id'])[0]), 1)
        self.assertEqual(len(pr.list_people(self.conn, 'commenter', group='none')[0]), 2)
        self.assertEqual(len(pr.list_people(self.conn, 'creator', group=g1['id'])[0]), 0)                     # Rollen getrennt
        self.assertEqual([g['name'] for g in pr.groups_of_person(self.conn, 'commenter', 'tiktok', 'max')], ['Eins', 'Zwei'])
        stats = {s['name']: s for s in repo.group_stats(self.conn)}
        self.assertEqual((stats['Eins']['commenters'], stats['Eins']['creators']), (2, 0))
        self.assertEqual(pr.remove_people(self.conn, g1['id'], 'commenter', [tt_max]), 1)
        self.assertEqual(pr.remove_people(self.conn, g1['id'], 'commenter', [tt_max]), 0)
        with self.assertRaises(ValueError):
            pr.add_people(self.conn, 999, 'commenter', [tt_max])
        with self.assertRaises(ValueError):
            pr.add_people(self.conn, g1['id'], 'boss', [tt_max])
        repo.delete_group(self.conn, g2['id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM group_people WHERE group_id=?', (g2['id'],)).fetchone()[0], 0)

    def test_creator_membership_pulls_in_their_videos_and_commenter_membership_filters_comments(self):
        g = repo.create_group(self.conn, 'Küche')
        pr.add_people(self.conn, g['id'], 'creator', [pr.person('tiktok', 'uid-9', 'Küchenfee')])
        repo.add_videos_to_group(self.conn, g['id'], ['instagram:i1'])
        vids = logic.query_videos(self.db, group=g['id'])
        self.assertEqual({v['uid'] for v in vids}, {'tiktok:a1', 'tiktok:a2', 'instagram:i1'})        # Videos der Gruppe ODER ihrer Ersteller
        self.assertIn('Küche', [v for v in vids if v['uid'] == 'tiktok:a1'][0]['group_names'])
        self.assertEqual({v['uid'] for v in logic.query_videos(self.db, group=logic.UNGROUPED)}, {'youtube:y1'})
        pr.add_people(self.conn, g['id'], 'commenter', [pr.person('tiktok', None, 'Max')])
        a1 = logic.query_comments(self.db, 'tiktok', 'a1')
        self.assertEqual(len(a1), 3)
        self.assertEqual({c['text'] for c in logic.query_comments(self.db, 'tiktok', 'a1', group=g['id'])}, {'Super', 'Mist'})
        self.assertEqual(len(logic.query_group_comments(self.db, g['id'])), 3)                       # alle Kommentare der Mitglieder, über Videos
        self.assertEqual(logic.group_overview(self.db)['ungrouped'], 1)

    def test_person_refs_dedup_and_logic_wrappers(self):
        refs = logic.person_refs(self.db, 'creator', uids=['tiktok:a1', 'tiktok:a2', 'nope'])
        self.assertEqual([(r['platform'], r['author_key']) for r in refs], [('tiktok', 'uid-9')])
        with self.conn:
            uids = [r[0] for r in self.conn.execute("SELECT uid FROM comments WHERE author LIKE '%ax'")]
        refs = logic.person_refs(self.db, 'commenter', comment_uids=uids)
        self.assertEqual(sorted((r['platform'], r['author_key']) for r in refs), [('tiktok', 'max'), ('youtube', 'max')])
        g = repo.create_group(self.conn, 'X')
        self.assertEqual(logic.add_people_to_group(self.db, g['id'], 'commenter', refs), (2, 0, 0))
        self.assertEqual(logic.remove_people_from_group(self.db, g['id'], 'commenter', refs), 2)
        with self.assertRaises(ValueError):
            logic.add_people_to_group(self.db, g['id'], 'commenter', [])

    def test_commenter_sentiment_share_and_group_mood(self):
        items = {i['uid']: i for i in srepo.open_comments(self.conn)}
        srepo.save_results(self.conn, [(items['tiktok:c1']['uid'], items['tiktok:c1']['hash'], 'positiv', 0.9),
                                       (items['tiktok:c2']['uid'], items['tiktok:c2']['hash'], 'negativ', -0.5),
                                       (items['tiktok:c4']['uid'], items['tiktok:c4']['hash'], 'positiv', 0.7)], 'm', 1)
        row = [r for r in pr.list_people(self.conn, 'commenter')[0] if r['author_key'] == 'max' and r['platform'] == 'tiktok'][0]
        self.assertEqual((row['positive'], row['negative'], row['analyzed']), (2, 1, 3))
        g = repo.create_group(self.conn, 'Beobachtet')
        pr.add_people(self.conn, g['id'], 'commenter', [pr.person('tiktok', None, 'Max')])
        self.assertEqual(pr.group_sentiment(self.conn)[g['id']], {'positiv': 2, 'neutral': 0, 'negativ': 1})
        ov = logic.group_overview(self.db)['stats'][0]
        self.assertEqual(ov['member_mood'], {'positiv': 2, 'neutral': 0, 'negativ': 1})

    def test_old_database_gets_people_table_and_runs_column(self):
        old = self.root / 'alt.db'
        con = sqlite3.connect(old)
        con.executescript('''CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER, label TEXT, source TEXT NOT NULL DEFAULT 'manual',
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL DEFAULT 'running', videos_seen INTEGER DEFAULT 0, videos_new INTEGER DEFAULT 0,
            comments_new INTEGER DEFAULT 0, error TEXT);
            CREATE TABLE groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, color TEXT NOT NULL DEFAULT '#2563eb', note TEXT, created_at TEXT NOT NULL);
            INSERT INTO groups (name, created_at) VALUES ('Alt', '2026-01-01T00:00:00Z');
            INSERT INTO runs (started_at, status) VALUES ('2026-01-01T00:00:00Z', 'ok');''')
        con.commit()
        con.close()
        with Store(old) as s:
            self.assertIn('authors_new', [r[1] for r in s.db.execute('PRAGMA table_info(runs)')])
            self.assertEqual(s.db.execute('SELECT authors_new FROM runs').fetchone()[0], 0)
            self.assertIn('group_people', {r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertEqual(repo.group_stats(s.db)[0]['commenters'], 0)
        with Store(old):                                  # zweites Öffnen: nichts geht kaputt
            pass
        # nur lesen, ohne die Tabelle (ältere Datei, die noch nie über Store geöffnet wurde)
        bare = sqlite3.connect(':memory:')
        self.addCleanup(bare.close)
        bare.executescript('''CREATE TABLE groups (id INTEGER PRIMARY KEY, name TEXT, color TEXT, note TEXT, created_at TEXT);
            CREATE TABLE comments (uid TEXT, platform TEXT, video_id TEXT, text TEXT, author TEXT, author_id TEXT, like_count INT, created_at TEXT, first_seen_at TEXT);
            INSERT INTO comments VALUES ('a','tiktok','v','x','Max',NULL,1,NULL,'2026-10-01T00:00:00Z');''')
        rows, total = pr.list_people(bare, 'commenter')
        self.assertEqual((total, rows[0]['group_names']), (1, None))
        self.assertEqual(pr.list_people(bare, 'commenter', group=3), ([], 0))


class NewAuthorTests(Base):
    def test_first_seen_new_since_and_known_filters(self):
        with self.conn:
            self.conn.execute("UPDATE comments SET first_seen_at='2026-01-01T00:00:00Z'")
            self.conn.execute("UPDATE comments SET first_seen_at='2026-10-10T08:00:00Z' WHERE author='Zoë'")
            self.conn.execute("UPDATE comments SET first_seen_at='2026-10-10T09:00:00Z' WHERE comment_id='c4'")   # MAX, aber Max war schon da
        since = '2026-10-10T00:00:00Z'
        new, total = pr.list_people(self.conn, 'commenter', first_seen_since=since, known=False)
        self.assertEqual([(r['platform'], r['author_key']) for r in new], [('tiktok', 'z-1')])                    # nur wirklich neu
        known = pr.list_people(self.conn, 'commenter', first_seen_since=since, known=True)[0]
        self.assertEqual(sorted(r['author_key'] for r in known), ['max', 'max', 'ärger'])
        self.assertEqual(pr.count_new(self.conn, 'commenter', since), 1)
        self.assertEqual(pr.count_new(self.conn, 'commenter', '2000-01-01T00:00:00Z'), 4)
        self.assertEqual(pr.count_new(self.conn, 'commenter', '2030-01-01T00:00:00Z'), 0)
        row = [r for r in pr.list_people(self.conn, 'commenter')[0] if r['author_key'] == 'z-1'][0]
        self.assertEqual(row['first_seen_at'], '2026-10-10T08:00:00Z')
        self.assertEqual(pr.count_new(self.conn, 'creator', '2000-01-01T00:00:00Z'), 3)

    def test_new_comments_feed_flags_new_authors(self):
        with self.conn:
            self.conn.execute("UPDATE comments SET first_seen_at='2026-01-01T00:00:00Z'")
            self.conn.execute("UPDATE comments SET first_seen_at='2026-10-10T08:00:00Z' WHERE comment_id IN ('c3', 'c4')")
        rows = {r['comment_id']: r for r in repo.new_comments_since(self.conn, '2026-10-10T00:00:00Z')}
        self.assertEqual(set(rows), {'c3', 'c4'})
        self.assertTrue(rows['c3']['author_new'])           # Zoë tauchte erst jetzt auf
        self.assertFalse(rows['c4']['author_new'])          # Max war schon vorher da (nur andere Schreibweise)

    def test_comment_list_marks_new_authors_with_the_window(self):
        with self.conn:
            self.conn.execute("UPDATE comments SET first_seen_at='2026-01-01T00:00:00Z'")
            self.conn.execute("UPDATE comments SET first_seen_at=? WHERE author='Zoë'", (repo.iso(),))
        rows = logic.query_comments(self.db, 'tiktok', 'a1')
        logic.mark_new_authors(self.db, rows, logic.new_since(self.db, 'in den letzten 24 Stunden'))
        self.assertEqual({r['author']: r['author_new'] for r in rows}, {'Max': False, '@max': False, 'Zoë': True})
        logic.mark_new_authors(self.db, rows, None)
        self.assertFalse(any(r['author_new'] for r in rows))

    def test_new_since_choices(self):
        t = lambda s: logic.new_since(self.db, s)
        self.assertLess(t('in den letzten 7 Tagen'), t('in den letzten 24 Stunden'))
        self.assertLessEqual(t('seit heute'), repo.iso())
        self.assertEqual(t('31.12.2026')[:4], '2026')
        with self.assertRaises(ValueError):
            t('gestern Abend')
        with self.assertRaises(ValueError):
            t('')
        with self.assertRaises(ValueError):
            t('seit dem letzten Lauf')                                # noch kein Lauf
        with self.conn:
            run = repo.start_run(self.conn, None, 'x', 'manual')
            repo.finish_run(self.conn, run, 'ok')
        self.assertEqual(t('seit dem letzten Lauf'), logic.last_run_start(self.db))
        self.assertEqual(logic.new_people_count(self.db, 'commenter', t('in den letzten 24 Stunden')), 4)


class FakeAdapter:
    platform = 'youtube'
    needs_browser = False
    needs_auth = False

    def __init__(self):
        self.round = 0

    def fetch(self, url, ctx):
        self.round += 1
        names = ['Ann', 'bob'] if self.round == 1 else ['ANN', '@Bob', 'Cleo']
        vid = url.split('=')[-1]
        return Video('youtube', vid, url), [Comment('youtube', vid, f'k{self.round}-{i}', f'text {i}', author=n) for i, n in enumerate(names)]


class RunCountTests(unittest.TestCase):
    def test_author_seen_for_the_first_time_in_the_second_run_is_counted_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cfg = Settings(db_path=str(root / 'data' / 'scrape.db'), notify_file='', profiles_dir=str(root / 'data' / 'profiles'))
            adapter = FakeAdapter()
            with Store(cfg.db_path) as store, patch.object(scheduler, 'resolve', side_effect=lambda url: adapter):
                repo.ensure_default_accounts(store.db)
                t = repo.add_target(store.db, 'video', 'youtube', 'https://www.youtube.com/watch?v=aaaaaaaaaaa', max_comments=5)
                first = scheduler.run_target(root, t['id'], 'manual', cfg=cfg)
                time.sleep(1.1)                                 # Zeiten sind sekundengenau
                second = scheduler.run_target(root, t['id'], 'manual', cfg=cfg)
                runs = {r['id']: r for r in repo.list_runs(store.db)}
        self.assertEqual((first['status'], first['authors_new']), ('ok', 2))                       # Ann, bob
        self.assertEqual((second['status'], second['authors_new']), ('ok', 1))                     # ANN und @Bob kannte die Datenbank, nur Cleo ist neu
        self.assertEqual(runs[second['run_id']]['authors_new'], 1)


class ExportTests(Base):
    def rows(self, folder, name, delimiter=';'):
        with (folder / f'{name}.csv').open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f, delimiter=delimiter))

    def test_people_csv_group_filter_and_jsonl(self):
        g = repo.create_group(self.conn, 'Beobachtung')
        other = repo.create_group(self.conn, 'Andere')
        pr.add_people(self.conn, g['id'], 'commenter', [pr.person('tiktok', None, 'Max')])
        pr.add_people(self.conn, g['id'], 'creator', [pr.person('youtube', None, 'Küchenfee')])
        pr.add_people(self.conn, other['id'], 'commenter', [pr.person('youtube', None, 'Max')])
        items = {i['uid']: i for i in srepo.open_comments(self.conn)}
        srepo.save_results(self.conn, [(u, items[u]['hash'], 'positiv', 0.5) for u in ('tiktok:c1', 'tiktok:c4')], 'm', 1)
        out = self.root / 'alles'
        files = core.export_data(self.db, out, 'csv-excel')
        self.assertIn('personen.csv', [f.name for f in files])
        people = self.rows(out, 'personen')
        self.assertEqual(len(people), 3 + 4)                              # 3 Ersteller, 4 Kommentarschreiber
        self.assertIn('Zuerst gesehen', people[0])
        mx = [p for p in people if p['Rolle'] == 'Kommentarschreiber' and p['Kennung'] == 'max' and p['Plattform'] == 'TikTok'][0]
        self.assertEqual((mx['Kommentare'], mx['Gruppen']), ('3', 'Beobachtung'))
        self.assertEqual(len(self.rows(out, 'gruppen_personen')), 3)
        comments = self.rows(out, 'kommentare')
        mood = {c['Text']: (c['Stimmung (KI-Einschätzung)'], c['Stimmungswert (KI-Einschätzung)']) for c in comments}
        self.assertEqual(mood['Super'], ('positiv', '0.5'))
        self.assertEqual(mood['Mist'], ('', ''))                          # nicht eingestuft: leer
        # nur diese Gruppe: Mitglieder, ihre Videos (Ersteller) und die Kommentare der Mitglieder
        grp = self.root / 'gruppe'
        core.export_data(self.db, grp, 'csv-excel', group_id=g['id'])
        self.assertEqual({(p['Rolle'], p['Name']) for p in self.rows(grp, 'personen')}, {('Kommentarschreiber', 'Max'), ('Ersteller', 'Küchenfee')})
        self.assertEqual({v['Plattform'] for v in self.rows(grp, 'videos')}, {'YouTube'})
        self.assertTrue({'Super', 'Mist', 'toll'} <= {c['Text'] for c in self.rows(grp, 'kommentare')})
        self.assertEqual(len(self.rows(grp, 'gruppen_personen')), 2)
        jsonl = self.root / 'j'
        core.export_data(self.db, jsonl, 'jsonl', group_id=g['id'])
        gp = [json.loads(l) for l in (jsonl / 'group_people.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual({(r['role'], r['author_key']) for r in gp}, {('commenter', 'max'), ('creator', 'küchenfee')})
        cs = [json.loads(l) for l in (jsonl / 'comment_sentiment.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(r['label'] == 'positiv' for r in cs) and len(cs) == 2)

    def test_export_of_an_old_database_without_new_tables(self):
        old = self.root / 'alt.db'
        con = sqlite3.connect(old)
        con.executescript('''CREATE TABLE videos (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, url TEXT, author TEXT, author_id TEXT, caption TEXT,
            created_at TEXT, view_count INTEGER, like_count INTEGER, comment_count INTEGER, first_seen_at TEXT, fetched_at TEXT);
            CREATE TABLE comments (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, comment_id TEXT, text TEXT, author TEXT, author_id TEXT,
            created_at TEXT, like_count INTEGER, parent_id TEXT, first_seen_at TEXT, fetched_at TEXT);
            INSERT INTO comments VALUES ('tiktok:1','tiktok','v','1','Hallo','Max',NULL,NULL,1,NULL,'2026-10-01T00:00:00Z','2026-10-01 00:00:00');''')
        con.commit()
        con.close()
        out = self.root / 'altexport'
        core.export_data(old, out, 'csv-excel')
        self.assertEqual(self.rows(out, 'kommentare')[0]['Text'], 'Hallo')
        self.assertEqual(self.rows(out, 'gruppen_personen'), [])


if __name__ == '__main__':
    unittest.main()
