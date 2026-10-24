import datetime
import sqlite3
import tempfile
import unittest

from socialscraper import repo
from socialscraper.models import Comment, Video
from socialscraper.store import Store


class RepoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/s.db'); self.addCleanup(self.store.close)
        self.db = self.store.db

    def test_first_seen_and_new_counts(self):
        v = Video('youtube', 'a', 'https://youtu.be/a')
        c = [Comment('youtube', 'a', '1', 'x'), Comment('youtube', 'a', '2', 'y')]
        self.assertEqual(self.store.save_result(v, c), (True, 2))
        first = self.db.execute("SELECT first_seen_at FROM comments WHERE comment_id='1'").fetchone()[0]
        self.assertEqual(self.store.save_result(v, c + [Comment('youtube', 'a', '3', 'z')]), (False, 1))
        self.assertEqual(self.db.execute("SELECT first_seen_at FROM comments WHERE comment_id='1'").fetchone()[0], first)
        self.assertEqual(len(repo.new_comments_since(self.db, '2000-01-01T00:00:00Z')), 3)

    def test_old_database_is_migrated(self):
        path = self.tmp.name + '/old.db'
        with Store(path) as s:  # eine Datenbank im alten Zustand nachbauen: ohne first_seen_at
            s.save_video(Video('youtube', 'a', 'u'))
            s.db.execute("UPDATE videos SET fetched_at='2026-01-01 10:00:00'")
            for table in ('videos', 'comments'):
                s.db.execute(f'ALTER TABLE {table} DROP COLUMN first_seen_at')
            s.db.commit()
        with Store(path) as s:
            self.assertEqual(s.db.execute('SELECT first_seen_at FROM videos').fetchone()[0], '2026-01-01T10:00:00Z')   # ein Zeitformat

    def test_accounts(self):
        repo.ensure_default_accounts(self.db); repo.ensure_default_accounts(self.db)
        self.assertEqual([a['identity'] for a in repo.list_accounts(self.db)], ['instagram_main', 'reddit_main', 'tiktok_main', 'x_main'])
        a = repo.add_account(self.db, 'tiktok', 'Zweitkonto Ärger')
        self.assertEqual(a['identity'], 'tiktok_zweitkonto_aerger'); self.assertEqual(a['is_default'], 0)
        b = repo.add_account(self.db, 'tiktok', 'Zweitkonto Ärger')
        self.assertEqual(b['identity'], 'tiktok_zweitkonto_aerger_2')
        repo.set_default_account(self.db, a['id']); self.assertEqual(repo.default_account(self.db, 'tiktok')['id'], a['id'])
        repo.delete_account(self.db, a['id']); self.assertEqual(repo.default_account(self.db, 'tiktok')['identity'], 'tiktok_main')
        self.assertEqual(repo.add_account(self.db, 'youtube', 'x')['platform'], 'youtube')
        for bad in (('unsupported', 'x'), ('tiktok', ' ')):
            with self.assertRaises(ValueError): repo.add_account(self.db, *bad)

    def test_groups(self):
        g = repo.create_group(self.db, 'Marli'); h = repo.create_group(self.db, 'Clipper')
        self.assertNotEqual(g['color'], h['color'])
        with self.assertRaises(ValueError): repo.create_group(self.db, 'Marli')
        with self.assertRaises(ValueError): repo.create_group(self.db, 'X', color='rot')
        self.store.save_result(Video('youtube', 'a', 'u', view_count=10, like_count=2), [Comment('youtube', 'a', '1', 't')])
        repo.add_videos_to_group(self.db, g['id'], ['youtube:a', 'youtube:a'])
        stats = {s['name']: s for s in repo.group_stats(self.db)}
        self.assertEqual((stats['Marli']['videos'], stats['Marli']['views'], stats['Marli']['comments']), (1, 10, 1))
        self.assertEqual(stats['Clipper']['videos'], 0)
        repo.delete_group(self.db, g['id']); self.assertEqual(repo.groups_of_video(self.db, 'youtube:a'), [])

    def test_targets_and_schedule(self):
        t = repo.add_target(self.db, 'hashtag', 'tiktok', '#Marli', interval_min=60)
        self.assertEqual(t['value'], 'marli')
        with self.assertRaises(ValueError): repo.add_target(self.db, 'hashtag', 'tiktok', 'marli')
        for bad in (('hashtag', 'instagram', 'x'), ('profile', 'youtube', 'x'), ('video', 'youtube', 'kein link'), ('search', 'youtube', 'a')):
            with self.assertRaises(ValueError): repo.add_target(self.db, *bad)
        with self.assertRaises(ValueError): repo.add_target(self.db, 'search', 'youtube', 'gute suche', interval_min=1)
        self.assertEqual([x['id'] for x in repo.due_targets(self.db)], [t['id']])
        run = repo.start_run(self.db, t['id'], source='daemon')
        repo.finish_run(self.db, run, 'ok', videos_seen=3, videos_new=1, comments_new=5)
        self.assertEqual(repo.get_target(self.db, t['id'])['last_status'], 'ok')
        self.assertEqual(repo.due_targets(self.db), [])
        self.assertEqual(len(repo.due_targets(self.db, repo.now() + datetime.timedelta(minutes=61))), 1)
        run = repo.start_run(self.db, t['id']); repo.finish_run(self.db, run, 'failed', error='x' * 900)
        t3 = repo.get_target(self.db, t['id'])
        self.assertLessEqual(len(t3['last_error']), 500)
        self.assertLessEqual(repo.parse(t3['next_run_at']) - repo.now(), datetime.timedelta(minutes=31))
        repo.update_target(self.db, t['id'], enabled=0)
        self.assertEqual(repo.due_targets(self.db, repo.now() + datetime.timedelta(days=9)), [])
        with self.assertRaises(ValueError): repo.update_target(self.db, t['id'], value='x')
        repo.delete_target(self.db, t['id']); self.assertEqual(repo.list_targets(self.db), [])

    def test_stale_runs_recovered(self):
        run = repo.start_run(self.db)
        self.db.execute('UPDATE runs SET started_at=? WHERE id=?', ('2000-01-01T00:00:00Z', run)); self.db.commit()
        repo.recover_stale_runs(self.db); self.assertEqual(repo.list_runs(self.db)[0]['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
