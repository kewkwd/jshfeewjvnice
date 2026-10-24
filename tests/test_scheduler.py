import os
import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from socialscraper import repo, scheduler
from socialscraper.config import Settings
from socialscraper.models import Comment, Video
from socialscraper.net.session_guard import SessionExpired, State
from socialscraper.store import Store
os.environ.setdefault('SS_PACE', 'off')  # keine Zufallspausen zwischen Abrufen in Tests

NETWORK = bool(os.environ.get('SS_RUN_LOCAL_BROWSER_TESTS') or os.environ.get('SS_RUN_NETWORK_TESTS'))
YT = 'https://www.youtube.com/watch?v='


class FakeAdapter:
    """Ersatz für YouTube: kennt pro Link ein Video und Kommentare oder einen Fehler."""
    platform = 'youtube'
    needs_browser = False
    needs_auth = False

    def __init__(self, comments=None, errors=None):
        self.comments = comments or {}
        self.errors = errors or {}
        self.calls = []

    def fetch(self, url, ctx):
        self.calls.append((url, ctx.identity, ctx.country, ctx.cfg.max_comments, ctx.authenticated))
        if url in self.errors:
            raise self.errors[url]
        vid = url.split('=')[-1]
        video = Video('youtube', vid, url, view_count=10 + len(self.calls), comment_count=len(self.comments.get(url, [])))
        return video, [Comment('youtube', vid, c, 'text ' + c) for c in self.comments.get(url, [])]


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'data' / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'data' / 'profiles'))
        self.store = Store(self.cfg.db_path); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.adapter = FakeAdapter()
        patcher = patch.object(scheduler, 'resolve', side_effect=lambda url: self.adapter if 'youtube' in url else None)
        patcher.start(); self.addCleanup(patcher.stop)

    def run_target(self, target_id, **kw):
        return scheduler.run_target(self.root, target_id, kw.pop('source', 'daemon'), cfg=self.cfg, **kw)

    def run_row(self, run_id):
        return repo._one(self.db, 'SELECT * FROM runs WHERE id=?', (run_id,))

    def test_video_target_fetches_again_and_counts_only_new_comments(self):
        url = YT + 'aaaaaaaaaaa'
        t = repo.add_target(self.db, 'video', 'youtube', url, max_comments=7)
        self.adapter.comments[url] = ['1', '2']
        r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['videos_seen'], r['videos_new'], r['comments_new'], r['error']), ('ok', 1, 1, 2, None))
        self.adapter.comments[url] = ['1', '2', '3']
        r2 = self.run_target(t['id'], source='manual')
        self.assertEqual((r2['status'], r2['videos_new'], r2['comments_new']), ('ok', 0, 1))
        # max_comments des Ziels, Identität youtube_main
        self.assertEqual(self.adapter.calls[0][1:4], ('youtube_main', 'de', 7))
        # Läufe und Ziel aktualisiert
        rows = repo.list_runs(self.db)
        self.assertEqual([x['source'] for x in rows], ['manual', 'daemon'])
        self.assertTrue(all(x['finished_at'] and x['status'] == 'ok' for x in rows))
        target = repo.get_target(self.db, t['id'])
        self.assertEqual((target['last_status'], target['last_error']), ('ok', None))
        self.assertGreater(target['next_run_at'], repo.iso())

    def test_history_point_on_every_run(self):
        url = YT + 'bbbbbbbbbbb'
        t = repo.add_target(self.db, 'video', 'youtube', url)
        self.run_target(t['id'])
        time.sleep(1.1)  # Verlaufspunkte haben Sekundenauflösung
        self.run_target(t['id'])
        self.assertEqual(len(repo.video_history(self.db, 'youtube:bbbbbbbbbbb')), 2)

    def test_search_target_discovers_assigns_group_and_fetches_all(self):
        g = repo.create_group(self.db, 'Lofi')
        t = repo.add_target(self.db, 'search', 'youtube', 'lofi', group_id=g['id'], max_videos=3, max_comments=5)
        urls = [YT + 'aaaaaaaaaaa', YT + 'bbbbbbbbbbb']
        with patch.object(scheduler.discovery, 'discover', return_value=urls) as d:
            r = self.run_target(t['id'])
        self.assertEqual(d.call_args.args[:3], ('search', 'youtube', 'lofi'))
        self.assertEqual(d.call_args.args[4], 3)  # max_videos
        self.assertEqual((r['status'], r['videos_seen'], r['videos_new']), ('ok', 2, 2))
        self.assertEqual({row[0] for row in self.db.execute('SELECT video_uid FROM group_videos')},
                         {'youtube:aaaaaaaaaaa', 'youtube:bbbbbbbbbbb'})
        # zweiter Lauf: nichts Neues
        with patch.object(scheduler.discovery, 'discover', return_value=urls + [YT + 'ccccccccccc']):
            r2 = self.run_target(t['id'])
        self.assertEqual((r2['videos_seen'], r2['videos_new']), (3, 1))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM group_videos').fetchone()[0], 3)

    def test_empty_discovery_is_ok(self):
        t = repo.add_target(self.db, 'hashtag', 'youtube', 'nichts')
        with patch.object(scheduler.discovery, 'discover', return_value=[]):
            r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['videos_seen']), ('ok', 0))

    def test_single_failing_video_makes_run_partial_and_error_has_no_secrets(self):
        bad = YT + 'bbbbbbbbbbb'
        self.adapter.errors[bad] = RuntimeError('Abruf fehlgeschlagen token=abc123SECRET und proxy http://user:pw@host:80/x')
        t = repo.add_target(self.db, 'search', 'youtube', 'lofi')
        with patch.object(scheduler.discovery, 'discover', return_value=[YT + 'aaaaaaaaaaa', bad, YT + 'ccccccccccc']):
            r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['videos_seen']), ('partial', 2))
        self.assertIn('1 von 3', r['error'])
        for secret in ('abc123SECRET', 'user:pw'):
            self.assertNotIn(secret, r['error'])
        self.assertEqual(repo.get_target(self.db, t['id'])['last_status'], 'partial')

    def test_all_failing_is_failed_and_retried_within_30_minutes(self):
        url = YT + 'aaaaaaaaaaa'
        self.adapter.errors[url] = ValueError('kaputt')
        t = repo.add_target(self.db, 'video', 'youtube', url, interval_min=600)
        r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['error']), ('failed', 'kaputt'))
        nxt = repo.parse(repo.get_target(self.db, t['id'])['next_run_at'])
        self.assertLessEqual((nxt - repo.now()).total_seconds(), 31 * 60)

    def test_discovery_error_fails_run_with_message(self):
        t = repo.add_target(self.db, 'search', 'youtube', 'lofi')
        with patch.object(scheduler.discovery, 'discover', side_effect=RuntimeError('Netz weg')):
            r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['error']), ('failed', 'Netz weg'))
        self.assertEqual(self.run_row(r['run_id'])['status'], 'failed')

    def test_session_lost_midway_stops_with_login_message(self):
        a, b, c = YT + 'aaaaaaaaaaa', YT + 'bbbbbbbbbbb', YT + 'ccccccccccc'
        self.adapter.errors[b] = SessionExpired('weg')
        t = repo.add_target(self.db, 'search', 'youtube', 'lofi')
        with patch.object(scheduler.discovery, 'discover', return_value=[a, b, c]):
            r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['videos_seen']), ('partial', 1))
        self.assertIn('Anmeldung abgelaufen', r['error'])
        self.assertEqual(len(self.adapter.calls), 2)  # c wurde nicht mehr versucht

    def test_cancel_between_videos(self):
        stop = threading.Event()
        orig = self.adapter.fetch
        self.adapter.fetch = lambda url, ctx: (stop.set(), orig(url, ctx))[1]
        t = repo.add_target(self.db, 'search', 'youtube', 'lofi')
        with patch.object(scheduler.discovery, 'discover', return_value=[YT + 'aaaaaaaaaaa', YT + 'bbbbbbbbbbb']):
            r = self.run_target(t['id'], cancel=stop)
        self.assertEqual((r['status'], r['videos_seen']), ('cancelled', 1))
        self.assertEqual(self.run_row(r['run_id'])['status'], 'cancelled')

    def test_hard_abort_marks_run_cancelled_and_reraises(self):
        url = YT + 'aaaaaaaaaaa'
        self.adapter.errors[url] = KeyboardInterrupt()
        t = repo.add_target(self.db, 'video', 'youtube', url)
        with self.assertRaises(KeyboardInterrupt):
            self.run_target(t['id'])
        row = repo.list_runs(self.db)[0]
        self.assertEqual((row['status'], row['error']), ('cancelled', 'Abgebrochen'))
        self.assertIsNotNone(row['finished_at'])

    def test_instagram_video_target_lets_the_adapter_check_its_own_login(self):
        """Instagram läuft ohne Browser: cli.auth_state liefert (False, None) und darf die Plattform nicht sperren –
        der Adapter prüft die gespeicherte Anmeldung selbst beim Abruf."""
        url = 'https://www.instagram.com/p/ABCDEFG/'
        class InstagramFake:
            platform, needs_browser, needs_auth = 'instagram', False, True
            calls = []
            def fetch(self, u, ctx):
                self.calls.append(u)
                return Video('instagram', 'ABCDEFG', u, comment_count=1), [Comment('instagram', 'ABCDEFG', '1', 'text')]
        ig = InstagramFake()
        with patch.object(scheduler, 'resolve', side_effect=lambda u: ig if 'instagram' in u else None):
            t = repo.add_target(self.db, 'video', 'instagram', url, max_comments=5)
            r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['error'], r['comments_new'], ig.calls), ('ok', None, 1, [url]))

    def test_unknown_target(self):
        with self.assertRaisesRegex(ValueError, 'nicht gefunden'):
            self.run_target(999)

    def test_instagram_discovery_is_refused_by_repo_and_by_discovery(self):
        with self.assertRaises(ValueError):
            repo.add_target(self.db, 'hashtag', 'instagram', 'x')
        self.db.execute("INSERT INTO targets (kind, platform, value, interval_min, created_at) VALUES ('search','instagram','abc',60,?)",
                        (repo.iso(),))
        self.db.commit()
        tid = self.db.execute('SELECT id FROM targets').fetchone()[0]
        r = self.run_target(tid)
        self.assertEqual(r['status'], 'failed'); self.assertIn('Instagram', r['error'])

    def test_clean_error(self):
        e = scheduler.clean_error
        self.assertEqual(e(ValueError('a\nb')), 'a')
        self.assertNotIn('xyz', e('Authorization: Bearer xyz'))
        self.assertNotIn('geheim', e('sessionid=geheim'))
        self.assertNotIn('pw', e('http://u:pw@h/'))
        self.assertEqual(len(e('x' * 1000)), 300)
        self.assertEqual(e(KeyError()), 'KeyError')

    def test_load_config_resolves_paths_against_root(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SS_DB_PATH', None)
            cfg = scheduler.load_config(self.root)
        self.assertEqual(pathlib.Path(cfg.db_path), self.root / 'data' / 'scrape.db')
        self.assertEqual(pathlib.Path(cfg.profiles_dir), self.root / 'data' / 'profiles')


class TikTokTargetTests(unittest.TestCase):
    """TikTok-Ziele mit simuliertem Browser: Konto, Anmeldung, öffentlicher Abruf."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='', profiles_dir=str(self.root / 'p'))
        self.store = Store(self.cfg.db_path); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.adapter = Mock(platform='tiktok', needs_browser=True, needs_auth=True)
        self.adapter.fetch.side_effect = lambda url, ctx: (Video('tiktok', url.split('/')[-1], url, comment_count=0), [])
        self.state = (False, State.MISSING)
        for target, kwargs in (
                (scheduler.cli, dict(make_runtime=Mock(return_value=(Mock(), Mock(), Mock())))),
                (scheduler.cli, dict(shutdown_runtime=Mock())),
                (scheduler, dict(resolve=lambda url: self.adapter))):
            p = patch.multiple(target, **kwargs); p.start(); self.addCleanup(p.stop)
        p = patch.object(scheduler.cli, 'auth_state', side_effect=lambda *a: self.state); self.auth = p.start(); self.addCleanup(p.stop)

    def run_target(self, tid):
        return scheduler.run_target(self.root, tid, 'daemon', cfg=self.cfg)

    def test_missing_login_fails_with_clear_message_when_comments_wanted(self):
        t = repo.add_target(self.db, 'video', 'tiktok', 'https://www.tiktok.com/@a/video/1', max_comments=20)
        r = self.run_target(t['id'])
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['error'], 'Anmeldung fehlt für Konto „Hauptkonto“. Bitte unter „Konten“ anmelden.')
        self.adapter.fetch.assert_not_called()

    def test_expired_login_message_names_chosen_account(self):
        acc = repo.add_account(self.db, 'tiktok', 'Zweitkonto', country='at')
        t = repo.add_target(self.db, 'hashtag', 'tiktok', 'cats', account_id=acc['id'])
        self.state = (False, State.EXPIRED)
        r = self.run_target(t['id'])
        self.assertEqual(r['status'], 'failed')
        self.assertIn('Anmeldung abgelaufen für Konto „Zweitkonto“', r['error'])
        # Identität und Land stammen vom Konto
        self.assertEqual(self.auth.call_args.args[2:4], ('tiktok_zweitkonto', 'at'))

    def test_public_video_without_comments_runs_without_login(self):
        t = repo.add_target(self.db, 'video', 'tiktok', 'https://www.tiktok.com/@a/video/1', max_comments=0)
        r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['videos_seen']), ('ok', 1))
        self.assertFalse(self.adapter.fetch.call_args.args[1].authenticated)

    def test_logged_in_account_is_used_and_marks_context(self):
        t = repo.add_target(self.db, 'video', 'tiktok', 'https://www.tiktok.com/@a/video/1', max_comments=5)
        self.state = (True, State.OK)
        r = self.run_target(t['id'])
        self.assertEqual(r['status'], 'ok')
        ctx = self.adapter.fetch.call_args.args[1]
        self.assertEqual((ctx.identity, ctx.country, ctx.authenticated), ('tiktok_main', 'de', True))
        scheduler.cli.shutdown_runtime.assert_called()

    def test_profile_target_discovers_without_login_requirement(self):
        g = repo.create_group(self.db, 'G')
        t = repo.add_target(self.db, 'profile', 'tiktok', 'a', group_id=g['id'], max_comments=0)
        with patch.object(scheduler.discovery, 'discover', return_value=['https://www.tiktok.com/@a/video/1']):
            r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['videos_new']), ('ok', 1))
        self.assertEqual(self.db.execute('SELECT video_uid FROM group_videos').fetchall(), [('tiktok:1',)])

    def test_missing_account_gives_message(self):
        self.db.execute('DELETE FROM accounts'); self.db.commit()
        t = repo.add_target(self.db, 'video', 'tiktok', 'https://www.tiktok.com/@a/video/1')
        with patch.object(repo, 'ensure_default_accounts'):
            r = self.run_target(t['id'])
        self.assertEqual(r['status'], 'failed'); self.assertIn('kein Konto', r['error'])


@unittest.skipUnless(NETWORK, 'echtes Netz: SS_RUN_LOCAL_BROWSER_TESTS=1 oder SS_RUN_NETWORK_TESTS=1')
class RealYouTubeRunTests(unittest.TestCase):
    def test_real_video_target_and_real_search_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cfg = Settings(db_path=str(root / 'data' / 'scrape.db'), notify_file='', profiles_dir=str(root / 'data' / 'profiles'))
            with Store(cfg.db_path) as store:
                video_t = repo.add_target(store.db, 'video', 'youtube', 'https://www.youtube.com/watch?v=jNQXAC9IVRw', max_comments=2)
                group = repo.create_group(store.db, 'Echt')
                search_t = repo.add_target(store.db, 'search', 'youtube', 'lofi hip hop', group_id=group['id'],
                                           max_videos=2, max_comments=0)
                r1 = scheduler.run_target(root, video_t['id'], 'manual', cfg=cfg)
                r2 = scheduler.run_target(root, search_t['id'], 'manual', cfg=cfg)
                self.assertEqual((r1['status'], r1['videos_seen'], r1['videos_new']), ('ok', 1, 1), r1)
                self.assertGreater(r1['comments_new'], 0)
                self.assertEqual(r2['status'], 'ok', r2)
                self.assertTrue(1 <= r2['videos_seen'] <= 2)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM group_videos').fetchone()[0], r2['videos_seen'])
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM video_snapshots').fetchone()[0], 1 + r2['videos_seen'])


if __name__ == '__main__':
    unittest.main()
