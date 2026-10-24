"""Tests für ganze Kanäle: Migration, Links, Schätzung, Warteschlange, Pakete, Pausen, Rotation, Plattformlisten.

Alles mit Wegwerf-Ordnern und simulierten Antworten (kein Netz, keine Konten, keine sichtbaren Browser)."""
import datetime
import os
import pathlib
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from socialscraper import channel_list as cl
from socialscraper import channels, repo, scheduler
from socialscraper.config import Settings
from socialscraper.models import Video, normalize_duration
from socialscraper.net.session_guard import State
from socialscraper.store import Store

os.environ.setdefault('SS_PACE', 'off')
UTC = datetime.timezone.utc
YT = 'https://www.youtube.com/watch?v='


def vid(n):
    return f'v{n:010d}'


def make_lister(count, platform_total=None, exhausted=True, fail_after=None, error=None, prefix='youtube', url_fn=None):
    """Ersatz für discovery.list_channel: liefert `count` Einträge (neueste zuerst) und ruft on_items wie die Adapter."""
    calls = []

    def lister(platform, value, ctx, opts):
        calls.append(opts)
        collector = cl.Collector(opts)
        base = datetime.datetime(2026, 10, 1, tzinfo=UTC)
        for n in range(count):
            if fail_after is not None and n >= fail_after:
                collector.flush(f'x:{n}')
                raise error
            url = url_fn(n) if url_fn else YT + vid(n)
            stamp = cl.to_iso(base - datetime.timedelta(days=n))
            if not collector.add(cl.item(url, None, f'Titel {n}', stamp, 60 + n, platform), f'x:{n}'):
                break
        collector.flush(f'x:{count}')
        return cl.ListResult(found=collector.count, exhausted=exhausted and not collector.done, platform_total=platform_total,
                             reached_known=collector.reached_known, cursor=None)
    lister.calls = calls
    return lister


class FakeAdapter:
    platform = 'youtube'
    needs_browser = False
    needs_auth = False

    def __init__(self):
        self.calls, self.errors = [], {}

    def fetch(self, url, ctx):
        self.calls.append((url, ctx.identity))
        if url in self.errors:
            raise self.errors[url]
        return Video('youtube', url.split('=')[-1], url, author='Kanal', duration_s=61), []


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'data' / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'data' / 'profiles'))
        self.store = Store(self.cfg.db_path)
        self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.adapter = FakeAdapter()
        p = patch.object(scheduler, 'resolve', side_effect=lambda url: self.adapter if 'youtube' in url else None)
        p.start()
        self.addCleanup(p.stop)

    def channel(self, **kw):
        ref = channels.ChannelRef('youtube', 'channel', 'https://www.youtube.com/@test', '@test', 'https://www.youtube.com/@test')
        kw.setdefault('comments', 0)
        return channels.create_channel(self.db, ref, **kw)

    def run_channel(self, target, lister, **kw):
        with patch.object(scheduler.discovery, 'list_channel', side_effect=lister):
            return scheduler.run_target(self.root, target['id'], kw.pop('source', 'daemon'), cfg=self.cfg, **kw)

    def t(self, target):
        return repo.get_target(self.db, target['id'])

    def progress(self, target):
        return repo.channel_progress(self.db, target['id'])


class MigrationTests(unittest.TestCase):
    OLD_TARGETS = '''CREATE TABLE targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK (kind IN ('video','hashtag','search','profile')),
        platform TEXT NOT NULL, value TEXT NOT NULL,
        account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
        account_mode TEXT NOT NULL DEFAULT 'default' CHECK (account_mode IN ('default','fixed','rotate')),
        group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
        interval_min INTEGER NOT NULL DEFAULT 360, max_comments INTEGER NOT NULL DEFAULT 20,
        max_videos INTEGER NOT NULL DEFAULT 20, enabled INTEGER NOT NULL DEFAULT 1,
        last_run_at TEXT, next_run_at TEXT, last_status TEXT, last_error TEXT, created_at TEXT NOT NULL,
        UNIQUE (kind, platform, value));'''

    def old_db(self, path):
        db = sqlite3.connect(path)
        db.executescript('''
            CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, label TEXT NOT NULL,
                identity TEXT NOT NULL UNIQUE, country TEXT NOT NULL DEFAULT 'de', is_default INTEGER NOT NULL DEFAULT 0,
                note TEXT, created_at TEXT NOT NULL);
            CREATE TABLE groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#2563eb', note TEXT, created_at TEXT NOT NULL);
            ''' + self.OLD_TARGETS + '''
            CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER REFERENCES targets(id) ON DELETE SET NULL,
                label TEXT, source TEXT NOT NULL DEFAULT 'manual', started_at TEXT NOT NULL, finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running', videos_seen INTEGER DEFAULT 0, videos_new INTEGER DEFAULT 0,
                comments_new INTEGER DEFAULT 0, error TEXT);
            INSERT INTO groups (name, created_at) VALUES ('G', '2026-01-01T00:00:00Z');
            INSERT INTO targets (kind, platform, value, group_id, interval_min, created_at)
                VALUES ('hashtag','youtube','lofi',1,60,'2026-01-01T00:00:00Z');
            INSERT INTO targets (kind, platform, value, created_at) VALUES ('video','youtube','https://youtu.be/abcdefghijk','2026-01-02T00:00:00Z');
            INSERT INTO targets (kind, platform, value, created_at) VALUES ('video','youtube','https://youtu.be/zzzzzzzzzzz','2026-01-03T00:00:00Z');
            DELETE FROM targets WHERE id=3;
            INSERT INTO runs (target_id, label, started_at, status) VALUES (1,'a','2026-01-01T00:00:00Z','ok');
            INSERT INTO runs (target_id, label, started_at, status) VALUES (2,'b','2026-01-01T01:00:00Z','ok');
        ''')
        db.commit()
        db.close()

    def test_old_database_is_rebuilt_with_all_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'old.db'
            self.old_db(path)
            with Store(path) as store:
                db = store.db
                rows = db.execute('SELECT id, kind, value, group_id, interval_min FROM targets ORDER BY id').fetchall()
                self.assertEqual(rows, [(1, 'hashtag', 'lofi', 1, 60), (2, 'video', 'https://youtu.be/abcdefghijk', None, 360)])
                self.assertEqual(db.execute('SELECT target_id FROM runs ORDER BY id').fetchall(), [(1,), (2,)])
                self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertIn("'channel'", db.execute("SELECT sql FROM sqlite_master WHERE name='targets'").fetchone()[0])
                columns = {r[1] for r in db.execute('PRAGMA table_info(targets)')}
                self.assertTrue({'pace_profile', 'backfill_state', 'quiet_hours', 'scope'} <= columns)
                # UNIQUE (kind, platform, value) bleibt
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO targets (kind, platform, value, created_at) VALUES ('hashtag','youtube','lofi','x')")
                # gelöschte Nummer 3 wird nicht neu vergeben
                tid = repo.add_channel(db, 'youtube', 'https://www.youtube.com/@a')['id']
                self.assertEqual(tid, 4)
                # Löschen eines Ziels löst die Läufe nur vom Ziel (Fremdschlüssel wirken weiter)
                repo.delete_target(db, 1)
                self.assertEqual(db.execute('SELECT target_id FROM runs WHERE id=1').fetchone()[0], None)
            with Store(path) as again:           # zweiter Start: nichts passiert, nichts geht verloren
                self.assertEqual(again.db.execute('SELECT COUNT(*) FROM targets').fetchone()[0], 2)

    def test_failed_rebuild_leaves_old_database_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'old.db'
            self.old_db(path)
            real = sqlite3.connect

            def boom(*a, **k):
                return real(*a, **k)
            with patch('socialscraper.store.targets_ddl', side_effect=RuntimeError('Abbruch mitten im Umbau')):
                with self.assertRaises(RuntimeError):
                    Store(path)
            check = sqlite3.connect(path)
            try:
                self.assertEqual(check.execute('SELECT COUNT(*) FROM targets').fetchone()[0], 2)
                self.assertEqual(check.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 2)
                self.assertNotIn('targets_neu', {r[0] for r in check.execute("SELECT name FROM sqlite_master")})
            finally:
                check.close()
            with Store(path) as store:           # und danach gelingt die Migration
                self.assertIn("'channel'", store.db.execute("SELECT sql FROM sqlite_master WHERE name='targets'").fetchone()[0])

    def test_fresh_database_has_channel_tables(self):
        with Store(':memory:') as store:
            tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn('channel_items', tables)
            repo.add_channel(store.db, 'youtube', 'https://www.youtube.com/@a')
            with self.assertRaises(ValueError):
                repo.add_channel(store.db, 'youtube', 'https://www.youtube.com/@a')


class LinkTests(unittest.TestCase):
    def parse(self, text):
        return channels.parse_channel_link(text)

    def test_youtube_forms(self):
        for text, value in (('https://www.youtube.com/@LinusTechTips', 'https://www.youtube.com/@linustechtips'),
                            ('youtube.com/@LinusTechTips/videos', 'https://www.youtube.com/@linustechtips'),
                            ('https://m.youtube.com/@a.b-c_d/shorts', 'https://www.youtube.com/@a.b-c_d'),
                            ('https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA/featured', 'https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA'),
                            ('https://www.youtube.com/c/MrBeast', 'https://www.youtube.com/c/MrBeast'),
                            ('https://www.youtube.com/user/pewdiepie/videos', 'https://www.youtube.com/user/pewdiepie')):
            ref = self.parse(text)
            self.assertEqual((ref.platform, ref.value), ('youtube', value), text)

    def test_other_platforms(self):
        self.assertEqual(self.parse('https://www.tiktok.com/@Some.User?lang=de')[:4], ('tiktok', 'profile', 'some.user', '@some.user'))
        self.assertEqual(self.parse('https://www.instagram.com/Some.User/reels/')[:3], ('instagram', 'profile', 'some.user'))
        self.assertEqual(self.parse('https://old.reddit.com/user/Spez/submitted')[:3], ('reddit', 'user', 'u/spez'))
        self.assertEqual(self.parse('https://www.reddit.com/u/spez')[2], 'u/spez')
        self.assertEqual(self.parse('https://www.reddit.com/r/Python/new/')[:3], ('reddit', 'subreddit', 'r/python'))
        self.assertEqual(self.parse('https://twitter.com/Jack/with_replies')[:3], ('x', 'profile', 'jack'))
        self.assertEqual(self.parse('https://x.com/jack')[2], 'jack')

    def test_lookalikes_and_wrong_links_are_refused(self):
        bad = ['https://youtube.com.evil.com/@a', 'https://evil.com/?u=https://www.youtube.com/@a', 'https://notyoutube.com/@a',
               'https://www.youtube.com@evil.com/@a', 'https://user:pw@www.youtube.com/@a', 'https://x.com.evil.org/jack',
               'https://tiktok.com.evil.org/@a', 'https://fxtwitter.com/jack', 'ftp://www.youtube.com/@a', '',
               'https://www.youtube.com/watch?v=abcdefghijk', 'https://youtu.be/abcdefghijk',
               'https://www.youtube.com/playlist?list=PL123', 'https://www.tiktok.com/@a/video/123',
               'https://vm.tiktok.com/ZMabc/', 'https://www.instagram.com/p/AbCdEfGh1/', 'https://www.instagram.com/explore/',
               'https://www.reddit.com/r/python/comments/abc123/titel/', 'https://redd.it/abc123', 'https://x.com/jack/status/123',
               'https://x.com/home', 'https://t.co/abcdef', 'https://example.org/@a', 'nur text']
        for text in bad:
            with self.assertRaises(ValueError, msg=text):
                self.parse(text)

    def test_handle_input_needs_a_platform_hint(self):
        self.assertEqual(channels.parse_channel_link('@jack', 'x').value, 'jack')
        self.assertEqual(channels.parse_channel_link('r/python', 'reddit').value, 'r/python')
        with self.assertRaises(ValueError):
            channels.parse_channel_link('@jack')


class EstimateTests(unittest.TestCase):
    def test_example_from_the_brief(self):
        e = channels.estimate(1380, 'youtube', 'careful')
        self.assertEqual((e['per_day'], e['text']), (60, 'rund 23 Tage'))
        self.assertTrue(any('rund 23 Tage' in n for n in e['notes']))
        self.assertIn('1.380 Videos ÷ 60 pro Tag', e['formula'])

    def test_profiles_accounts_quiet_hours_comments(self):
        self.assertEqual(channels.estimate(400, 'youtube', 'normal')['per_day'], 200)
        self.assertEqual(channels.estimate(400, 'youtube', 'fast')['per_day'], 600)
        # zwei Konten verdoppeln das Tageslimit je Konto, höchstens bis zur Grenze der Plattform
        self.assertEqual(channels.estimate(900, 'tiktok', 'careful', accounts=2)['per_day'], 120)
        self.assertEqual(channels.estimate(900, 'tiktok', 'careful', accounts=5)['per_day'], 150)
        self.assertEqual(channels.estimate(900, 'youtube', 'careful', accounts=5)['per_day'], 60)   # YouTube hat keine Konten
        slow = channels.estimate(50, 'youtube', 'careful', quiet_hours='0-12')
        self.assertLessEqual(slow['per_day'], 60)
        self.assertIn('Stunde', channels.estimate(30, 'youtube', 'normal')['text'])
        with_comments = channels.estimate(100, 'youtube', 'normal', comments=50)
        self.assertTrue(any('Kommentare' in n for n in with_comments['notes']))
        self.assertTrue(any('KI-Stimmung' in n and 'Kostenschätzung' in n for n in with_comments['notes']))
        self.assertEqual(channels.estimate(0, 'youtube')['text'], 'Es ist nichts abzurufen.')

    def test_platform_list_limits_are_named(self):
        e = channels.estimate(5000, 'reddit', 'normal')
        self.assertEqual(e['videos'], 1000)
        self.assertTrue(any('höchstens etwa 1.000' in n for n in e['notes']))

    def test_profile_numbers(self):
        self.assertEqual([(k, p['batch'], p['per_account_day']) for k, p in channels.PROFILES.items()],
                         [('careful', 8, 60), ('normal', 20, 200), ('fast', 50, 600)])


class QuietAndTimeTests(unittest.TestCase):
    def test_quiet_hours(self):
        local = datetime.datetime.now().astimezone().replace(hour=23, minute=30, second=0, microsecond=0)
        at = local.astimezone(UTC)
        self.assertTrue(channels.in_quiet('23-7', at))
        self.assertTrue(channels.in_quiet('22-24'.replace('24', '0'), at))
        self.assertFalse(channels.in_quiet('1-6', at))
        self.assertFalse(channels.in_quiet('', at))
        end = channels.quiet_end('23-7', at).astimezone()
        self.assertEqual((end.hour, end.minute), (7, 0))
        self.assertGreater(end, local)
        for bad in ('abc', '25-3', '5-5', '1-', '-3'):
            with self.assertRaises(ValueError, msg=bad):
                repo.check_quiet_hours(bad)

    def test_jitter_and_gap(self):
        rng = random.Random(1)
        values = {round(channels.jitter_minutes(360, rng)) for _ in range(30)}
        self.assertGreater(len(values), 10)
        self.assertTrue(all(306 <= v <= 414 for v in values))
        self.assertGreaterEqual(channels.jitter_minutes(5, rng), 5)
        for profile, (low, high) in (('careful', (45, 90)), ('normal', (20, 45)), ('fast', (8, 15))):
            self.assertTrue(all(low <= channels.gap_minutes(profile, rng) <= high for _ in range(20)))

    def test_pace_follows_profile(self):
        from socialscraper.net import humanize
        rng = random.Random(3)
        careful = [humanize.pace_delay(rng, channels.pace_bounds('careful')) for _ in range(200)]
        fast = [humanize.pace_delay(rng, channels.pace_bounds('fast')) for _ in range(200)]
        self.assertGreaterEqual(min(careful), 8.0)
        self.assertGreaterEqual(min(fast), 1.0)
        self.assertGreater(sum(careful) / 200, 2 * sum(fast) / 200)

    def test_honest_notes_never_claim_all(self):
        n = channels.honest_note
        self.assertIn('230 von 1.380 bekannten Videos gefunden; die Plattform zeigt nicht mehr', n('tiktok', 230, 1380, True, 'all'))
        self.assertIn('höchstens etwa 1.000', n('reddit', 990, None, True, 'all'))
        self.assertNotIn('alle', n('x', 100, None, True, 'all').replace('ob das wirklich alle sind', ''))
        self.assertIn('nach deiner Auswahl', n('youtube', 5, None, True, 'latest'))
        self.assertIn('Ende der Liste', n('youtube', 5, None, True, 'all'))


class DurationTests(unittest.TestCase):
    def test_normalize_duration(self):
        self.assertEqual(normalize_duration(75, 'youtube'), 75.0)
        self.assertEqual(normalize_duration('1:15', 'x'), 75.0)
        self.assertEqual(normalize_duration('PT1M15S'), 75.0)
        self.assertEqual(normalize_duration(15000, 'tiktok'), 15.0)       # Millisekunden-Fehler
        self.assertEqual(normalize_duration(75000, 'reddit'), 75.0)
        self.assertEqual(normalize_duration(10 ** 9, 'tiktok'), None)
        for bad in (None, '', -3, float('nan'), float('inf'), True, 'abc', [], {}):
            self.assertIsNone(normalize_duration(bad), repr(bad))
        self.assertEqual(normalize_duration(0), 0.0)

    def test_known_duration_is_not_overwritten_with_none(self):
        with Store(':memory:') as store:
            store.save_result(Video('youtube', 'abcdefghijk', 'u', duration_s=212), [], None)
            store.save_result(Video('youtube', 'abcdefghijk', 'u', duration_s=None, view_count=5), [], None)
            self.assertEqual(store.db.execute('SELECT duration_s, view_count FROM videos').fetchone(), (212.0, 5))
            store.save_result(Video('youtube', 'abcdefghijk', 'u', duration_s=213), [], None)
            self.assertEqual(store.db.execute('SELECT duration_s FROM videos').fetchone()[0], 213.0)


class QueueRunTests(Base):
    def test_backfill_in_batches_then_watching(self):
        t = self.channel(pace_profile='normal', interval_min=360)
        lister = make_lister(25, platform_total=25)
        r1 = self.run_channel(t, lister)
        self.assertEqual((r1['status'], r1['videos_seen'], r1['videos_new']), ('ok', 20, 20))
        p = self.progress(t)
        self.assertEqual((p['done'], p['pending'], p['total']), (20, 5, 25))
        now = self.t(t)
        self.assertEqual((now['backfill_state'], now['list_done'], now['total_found']), ('fetching', 1, 25))
        self.assertIn('25 Videos', now['list_note'])
        gap = (repo.parse(now['next_run_at']) - repo.now()).total_seconds() / 60
        self.assertTrue(18 <= gap <= 46, gap)                    # Takt des Profils „Normal“, nicht das Intervall
        r2 = self.run_channel(t, lister)
        self.assertEqual((r2['videos_seen'], self.progress(t)['pending']), (5, 0))
        self.assertEqual(self.t(t)['backfill_state'], 'watching')
        interval = (repo.parse(self.t(t)['next_run_at']) - repo.now()).total_seconds() / 60
        self.assertTrue(300 <= interval <= 420, interval)        # 6 Stunden ± 15 %
        self.assertEqual(len(lister.calls), 1)                   # die Liste wurde nur einmal geholt
        self.assertEqual(len(self.adapter.calls), 25)
        self.assertEqual(self.adapter.calls[0][0], YT + vid(0))   # neueste zuerst

    def test_careful_profile_uses_small_packages(self):
        t = self.channel(pace_profile='careful')
        lister = make_lister(20)
        self.assertEqual(self.run_channel(t, lister)['videos_seen'], 8)
        self.assertEqual(self.run_channel(t, lister)['videos_seen'], 8)
        self.assertEqual(self.run_channel(t, lister)['videos_seen'], 4)
        self.assertEqual(self.t(t)['backfill_state'], 'watching')

    def test_new_videos_are_found_while_watching(self):
        t = self.channel(pace_profile='fast')
        self.run_channel(t, make_lister(3))
        self.assertEqual(self.t(t)['backfill_state'], 'watching')
        # zwei neue Videos vorn: die Liste wird nur kurz geprüft und hört bei bekannten Einträgen auf
        urls = [YT + 'neu00000001', YT + 'neu00000002'] + [YT + vid(n) for n in range(3)]
        lister = make_lister(len(urls), url_fn=lambda n: urls[n])
        r = self.run_channel(t, lister)
        self.assertEqual((r['videos_seen'], r['videos_new']), (2, 2))
        opts = lister.calls[0]
        self.assertEqual((opts.limit, opts.stop_after_known), (channels.WATCH_LIST_LIMIT, channels.WATCH_STOP_AFTER_KNOWN))
        self.assertEqual(self.progress(t)['total'], 5)

    def test_scope_latest_and_since(self):
        t = self.channel(scope='latest', scope_value=7)
        lister = make_lister(30)
        self.run_channel(t, lister)
        self.assertEqual(self.progress(t)['total'], 7)
        self.assertEqual((self.progress(t)['done'], self.t(t)['backfill_state']), (7, 'watching'))
        ref = channels.ChannelRef('youtube', 'channel', 'https://www.youtube.com/@zwei', '@zwei', '')
        t2 = channels.create_channel(self.db, ref, scope='since', scope_value='2026-09-25', pace_profile='fast')
        self.run_channel(t2, make_lister(30))
        # Einträge am 1.10. bis 25.9. = 7 Tage ab 2026-09-25 (mit zwei Tagen Toleranz für ungefähre Daten)
        self.assertTrue(7 <= self.progress(t2)['total'] <= 10, self.progress(t2))

    def test_failed_videos_are_retried_three_times_then_failed_and_can_be_requeued(self):
        t = self.channel()
        bad = YT + vid(1)
        self.adapter.errors[bad] = RuntimeError('Video kaputt')
        lister = make_lister(3)
        r = self.run_channel(t, lister)
        self.assertEqual((r['status'], r['videos_seen']), ('partial', 2))
        item = self.db.execute('SELECT status, attempts, last_error FROM channel_items WHERE video_url=?', (bad,)).fetchone()
        self.assertEqual(item[:2], ('pending', 1))
        self.run_channel(t, lister)
        self.run_channel(t, lister)
        item = self.db.execute('SELECT status, attempts, last_error FROM channel_items WHERE video_url=?', (bad,)).fetchone()
        self.assertEqual(item[:2], ('failed', 3))
        self.assertIn('Video kaputt', item[2])
        self.assertEqual(self.progress(t)['failed'], 1)
        self.assertEqual(self.t(t)['backfill_state'], 'watching')
        del self.adapter.errors[bad]
        self.assertEqual(repo.requeue_failed(self.db, t['id']), 1)
        self.assertEqual(self.t(t)['backfill_state'], 'fetching')
        r = self.run_channel(t, lister)
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(self.progress(t)['failed'], 0)

    def test_crash_resume_puts_running_items_back(self):
        t = self.channel()
        self.run_channel(t, make_lister(5, exhausted=True), cancel=lambda: False)
        self.db.execute("UPDATE channel_items SET status='running' WHERE id IN (SELECT id FROM channel_items LIMIT 2)")
        self.db.execute("UPDATE channel_items SET status='pending'")
        self.db.execute("UPDATE channel_items SET status='running' WHERE id<=2")
        self.db.commit()
        self.assertEqual(repo.recover_channel_items(self.db), 2)
        self.assertEqual(self.progress(t)['running'], 0)
        self.db.execute("UPDATE channel_items SET status='running' WHERE id<=2")
        self.db.commit()
        channels.ChannelRun(self.db, self.t(t))      # der nächste Lauf räumt von selbst auf
        self.assertEqual(self.progress(t)['running'], 0)

    def test_daemon_start_recovers_running_items(self):
        from socialscraper import daemon
        t = self.channel()
        repo.add_channel_items(self.db, t['id'], 'youtube', [{'url': YT + vid(1), 'uid': None}])
        self.db.execute("UPDATE channel_items SET status='running'")
        self.db.commit()
        daemon.recover_at_start(self.db)
        self.assertEqual(self.progress(t)['pending'], 1)

    def test_listing_error_keeps_what_was_found_and_resumes(self):
        t = self.channel()
        lister = make_lister(10, fail_after=4, error=RuntimeError('Netz weg'))
        r = self.run_channel(t, lister)
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(self.progress(t)['total'], 4)            # der Zwischenstand ist gespeichert
        self.assertEqual(self.t(t)['backfill_state'], 'listing')
        self.assertEqual(self.t(t)['list_cursor'], 'x:4')
        self.assertEqual(self.t(t)['list_done'], 0)
        r = self.run_channel(t, make_lister(10))
        self.assertEqual(self.progress(t)['total'], 10)
        self.assertEqual(self.t(t)['list_done'], 1)

    def test_known_videos_are_skipped_and_grouped(self):
        g = repo.create_group(self.db, 'Kanal')
        self.store.save_result(Video('youtube', vid(0), YT + vid(0)), [], None)
        t = self.channel(group_id=g['id'])
        self.run_channel(t, make_lister(3))
        self.assertEqual(self.progress(t)['skipped'], 1)
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertEqual({r[0] for r in self.db.execute('SELECT video_uid FROM group_videos')},
                         {'youtube:' + vid(n) for n in range(3)})

    def test_group_gets_videos_and_owner_only_when_asked(self):
        g = repo.create_group(self.db, 'G')
        t = self.channel(group_id=g['id'])
        self.run_channel(t, make_lister(2))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM group_people').fetchone()[0], 0)
        ref = channels.ChannelRef('youtube', 'channel', 'https://www.youtube.com/@zwei', '@zwei', '')
        t2 = channels.create_channel(self.db, ref, group_id=g['id'], owner_as_creator=True)
        self.run_channel(t2, make_lister(2, url_fn=lambda n: YT + f'zz{n:09d}'))
        rows = self.db.execute('SELECT role, platform FROM group_people').fetchall()
        self.assertEqual(rows, [('creator', 'youtube')])
        with self.assertRaises(ValueError):
            channels.create_channel(self.db, channels.ChannelRef('youtube', 'channel', 'https://www.youtube.com/@drei', '@drei', ''),
                                    owner_as_creator=True)

    def test_remove_channel_keeps_saved_videos(self):
        t = self.channel()
        self.run_channel(t, make_lister(3))
        repo.delete_target(self.db, t['id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM channel_items').fetchone()[0], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM videos').fetchone()[0], 3)

    def test_channel_does_not_starve_other_targets(self):
        """Ein Kanal-Lauf ist begrenzt (Paket) und plant den nächsten Lauf mit Abstand: ein anderes Ziel kommt dazwischen dran."""
        t = self.channel(pace_profile='careful')
        other = repo.add_target(self.db, 'video', 'youtube', YT + 'andere00001')
        self.run_channel(t, make_lister(40))
        due = [x['id'] for x in repo.due_targets(self.db)]
        self.assertIn(other['id'], due)
        self.assertNotIn(t['id'], due)
        self.assertGreater(repo.parse(self.t(t)['next_run_at']), repo.now() + datetime.timedelta(minutes=40))


class LimitTests(Base):
    def test_daily_limit_per_platform_caps_the_package(self):
        t = self.channel(pace_profile='careful')
        lister = make_lister(100)
        self.run_channel(t, lister)
        # heute sind schon 55 Abrufe der Plattform gelaufen: nur noch 5 dürfen (Tageslimit 60)
        stamp = repo.iso()
        self.db.execute("UPDATE channel_items SET tried_at=? WHERE id IN (SELECT id FROM channel_items WHERE status='pending' LIMIT 47)", (stamp,))
        self.db.commit()
        a = channels.allowance(self.db, self.t(t))
        self.assertEqual(a['batch'], 5)
        self.db.execute("UPDATE channel_items SET tried_at=? WHERE id IN (SELECT id FROM channel_items WHERE tried_at IS NULL LIMIT 10)", (stamp,))
        self.db.commit()
        a = channels.allowance(self.db, self.t(t))
        self.assertEqual(a['batch'], 0)
        self.assertGreater(a['until'], repo.now())

    def test_gate_waits_for_midnight_when_limit_is_reached(self):
        t = self.channel(pace_profile='careful')
        self.run_channel(t, make_lister(100))
        self.db.execute("UPDATE channel_items SET tried_at=? WHERE status='pending'", (repo.iso(),))
        self.db.execute("UPDATE channel_items SET tried_at=? WHERE status='done'", (repo.iso(),))
        self.db.commit()
        calls = len(self.adapter.calls)
        r = self.run_channel(t, make_lister(100))
        self.assertIsNone(r['run_id'])                       # kein Lauf, kein Eintrag im Protokoll
        self.assertIn('Tagesgrenze', r['waiting'])
        self.assertEqual(len(self.adapter.calls), calls)
        self.assertGreater(repo.parse(self.t(t)['next_run_at']), repo.now() + datetime.timedelta(minutes=1))

    def test_quiet_hours_stop_the_run_and_reschedule(self):
        here = datetime.datetime.now().astimezone().hour
        quiet = f'{(here - 1) % 24}-{(here + 2) % 24}'
        t = self.channel(quiet_hours=quiet)
        r = self.run_channel(t, make_lister(5))
        self.assertIsNone(r['run_id'])
        self.assertIn('Ruhezeit', r['waiting'])
        self.assertEqual(len(self.adapter.calls), 0)
        self.assertEqual(self.progress(t)['total'], 0)
        end = repo.parse(self.t(t)['next_run_at']).astimezone()
        self.assertGreaterEqual((end - datetime.datetime.now().astimezone()).total_seconds(), 0)
        # „Jetzt weiter“ überspringt die Ruhezeit einmal
        channels.resume_channel(self.db, t['id'], now=repo.now())
        r = self.run_channel(t, make_lister(5))
        self.assertIsNotNone(r['run_id'])
        self.assertEqual(r['videos_seen'], 5)
        self.assertEqual(self.t(t)['force_once'], 0)

    def test_account_limit_of_profile_is_enforced_for_logged_in_platforms(self):
        acc = repo.default_account(self.db, 'tiktok')
        self.db.execute('UPDATE accounts SET used_today=?, used_day=? WHERE id=?',
                        (60, channels.rotation_local_day() if hasattr(channels, 'rotation_local_day') else
                         __import__('socialscraper.rotation', fromlist=['x']).local_day(repo.now()), acc['id']))
        self.db.commit()
        ref = channels.ChannelRef('tiktok', 'profile', 'someone', '@someone', '')
        t = channels.create_channel(self.db, ref, pace_profile='careful')
        self.assertEqual(channels.allowance(self.db, t)['batch'], 0)
        t2 = channels.create_channel(self.db, channels.ChannelRef('tiktok', 'profile', 'other', '@other', ''), pace_profile='normal')
        self.assertEqual(channels.allowance(self.db, t2)['batch'], 20)


class PauseTests(Base):
    def test_bot_check_pauses_the_channel_without_counting_attempts(self):
        t = self.channel()
        self.adapter.errors[YT + vid(1)] = RuntimeError("Sign in to confirm you're not a bot")
        r = self.run_channel(t, make_lister(5))
        now = self.t(t)
        self.assertEqual(now['backfill_state'], 'paused')
        self.assertGreater(repo.parse(now['pause_until']), repo.now() + datetime.timedelta(hours=1))
        self.assertIn('Sperre', now['pause_reason'])
        self.assertEqual(now['next_run_at'], now['pause_until'])
        p = self.progress(t)
        self.assertEqual(p['failed'], 0)
        self.assertEqual(self.db.execute("SELECT attempts FROM channel_items WHERE video_url=?", (YT + vid(1),)).fetchone()[0], 0)   # Fehler des Kontos zählen nicht gegen das Video
        # solange die Pause läuft, passiert nichts (kein Weiterhämmern)
        calls = len(self.adapter.calls)
        r2 = self.run_channel(t, make_lister(5))
        self.assertIn('pausiert bis', r2['waiting'])
        self.assertEqual(len(self.adapter.calls), calls)
        # nach „Jetzt weiter“ geht es dort weiter, wo es war
        del self.adapter.errors[YT + vid(1)]
        channels.resume_channel(self.db, t['id'], now=repo.now())
        self.assertEqual(self.t(t)['backfill_state'], 'fetching')
        r3 = self.run_channel(t, make_lister(5))
        self.assertEqual(self.progress(t)['done'], 5)

    def test_listing_captcha_pauses_and_resumes_listing(self):
        t = self.channel()
        lister = make_lister(10, fail_after=3, error=RuntimeError('Die Plattform zeigt eine Sicherheitsabfrage (CAPTCHA).'))
        self.run_channel(t, lister)
        now = self.t(t)
        self.assertEqual((now['backfill_state'], now['resume_state'], now['list_done']), ('paused', 'listing', 0))
        self.assertEqual(self.progress(t)['total'], 3)
        channels.resume_channel(self.db, t['id'], now=repo.now())
        self.assertEqual(self.t(t)['backfill_state'], 'listing')

    def test_state_texts(self):
        t = self.channel()
        t['progress'] = {'total': 1380, 'finished': 412, 'failed': 3, 'done': 412, 'pending': 965, 'running': 0, 'skipped': 0}
        channels.pause_channel(self.db, t['id'], repo.now() + datetime.timedelta(hours=2), 'CAPTCHA')
        t.update(self.t(t), progress=t['progress'])
        line = channels.progress_line(t)
        self.assertIn('412 von 1.380', line)
        self.assertIn('3 fehlgeschlagen', line)
        self.assertIn('pausiert bis', line)
        self.assertIn('wegen CAPTCHA', line)
        self.assertEqual(len(channels.bar(412, 1380)), 20)


class RotationRunTests(unittest.TestCase):
    """TikTok-Kanal mit zwei Konten: ein Konto wird gesperrt, das andere macht weiter; sind beide weg, pausiert der Kanal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='', profiles_dir=str(self.root / 'p'))
        self.store = Store(self.cfg.db_path)
        self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.second = repo.add_account(self.db, 'tiktok', 'Zweit')
        self.main = repo.default_account(self.db, 'tiktok')
        self.used, self.blocked, self.forbidden = [], set(), set()
        outer = self

        class Adapter:
            platform, needs_browser, needs_auth = 'tiktok', True, True

            def fetch(self, url, ctx):
                outer.used.append(ctx.identity)
                if ctx.identity in outer.blocked:
                    raise RuntimeError('TikTok zeigt eine Sicherheitsabfrage (CAPTCHA).')
                if url in outer.forbidden:
                    raise RuntimeError('HTTP Error 403: Forbidden')
                return Video('tiktok', url.split('/')[-1], url, author='a', duration_s=15), []
        self.adapter = Adapter()
        for target, kwargs in ((scheduler.cli, dict(make_runtime=Mock(return_value=(Mock(), Mock(), Mock())))),
                               (scheduler.cli, dict(shutdown_runtime=Mock())),
                               (scheduler, dict(resolve=lambda url: self.adapter))):
            p = patch.multiple(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(scheduler.cli, 'auth_state', side_effect=lambda *a: (True, State.OK))
        p.start()
        self.addCleanup(p.stop)
        # beide Konten gelten als angemeldet
        p = patch('socialscraper.guicore.account_status', return_value=('saved', 'ok'))
        p.start()
        self.addCleanup(p.stop)

    def url(self, n):
        return f'https://www.tiktok.com/@someone/video/{7000000000000000000 + n}'

    def run_it(self, target, count=6):
        lister = make_lister(count, url_fn=self.url)
        with patch.object(scheduler.discovery, 'list_channel', side_effect=lister):
            return scheduler.run_target(self.root, target['id'], 'daemon', cfg=self.cfg)

    def channel(self, **kw):
        ref = channels.ChannelRef('tiktok', 'profile', 'someone', '@someone', '')
        return channels.create_channel(self.db, ref, account_mode='rotate', pace_profile='normal', **kw)

    def test_rotation_changes_account_when_one_is_blocked(self):
        self.blocked.add(self.main['identity'])
        t = self.channel()
        r = self.run_it(t)
        self.assertEqual(r['videos_seen'], 6)
        self.assertEqual(set(self.used[-6:]), {self.second['identity']})
        acc = repo.get_account(self.db, self.main['id'])
        self.assertIsNotNone(acc['cooldown_until'])                # mark_failure
        self.assertEqual(repo.get_target(self.db, t['id'])['backfill_state'], 'watching')

    def test_single_403_video_fails_alone_and_does_not_cool_the_account(self):
        self.forbidden.add(self.url(2))
        t = self.channel()
        r = self.run_it(t, count=6)
        self.assertEqual(repo.channel_progress(self.db, t['id'])['done'], 5)
        self.assertIsNone(repo.get_account(self.db, self.main['id'])['cooldown_until'])
        self.assertIsNone(repo.get_account(self.db, self.second['id'])['cooldown_until'])
        self.assertNotEqual(repo.get_target(self.db, t['id'])['backfill_state'], 'paused')
        self.assertEqual(r['videos_seen'], 5)

    def test_three_403_videos_in_a_row_count_as_account_block(self):
        for n in (1, 2, 3):
            self.forbidden.add(self.url(n))
        t = self.channel()
        self.run_it(t, count=6)
        cooled = [a for a in (self.main, self.second) if repo.get_account(self.db, a['id'])['cooldown_until']]
        self.assertEqual(len(cooled), 1)                             # genau das Konto, das die drei 403 bekam
        self.assertEqual(repo.get_account(self.db, cooled[0]['id'])['cooldown_reason'], 'Sperre')

    def test_all_accounts_blocked_pauses_channel_until_first_account_returns(self):
        self.blocked |= {self.main['identity'], self.second['identity']}
        t = self.channel()
        r = self.run_it(t)
        now = repo.get_target(self.db, t['id'])
        self.assertEqual(now['backfill_state'], 'paused')
        self.assertIn('CAPTCHA', now['pause_reason'])
        until = repo.parse(now['pause_until'])
        self.assertGreater(until, repo.now() + datetime.timedelta(hours=5))
        self.assertEqual(repo.channel_progress(self.db, t['id'])['failed'], 0)
        hammer = len(self.used)
        again = self.run_it(t)                                       # kein Weiterhämmern
        self.assertEqual(len(self.used), hammer)
        self.assertIn('waiting', again)

    def test_profile_account_limit_switches_to_next_account(self):
        self.db.execute('UPDATE accounts SET used_today=?, used_day=? WHERE id=?',
                        (196, __import__('socialscraper.rotation', fromlist=['x']).local_day(repo.now()), self.main['id']))
        self.db.execute("UPDATE accounts SET last_used_at='2000-01-01T00:00:00Z' WHERE id=?", (self.main['id'],))
        self.db.commit()
        t = self.channel()
        self.run_it(t, count=10)
        # Konto 1 hat noch 4 Abrufe nach dem Profil „Normal“ (200 am Tag), danach nimmt Konto 2 den Rest
        first = [i for i in self.used if i == self.main['identity']]
        self.assertEqual(len(first), 4)
        self.assertEqual(len([i for i in self.used if i == self.second['identity']]), 6)


class AdapterListTests(unittest.TestCase):
    def opts(self, **kw):
        batches = []
        o = cl.ListOptions(on_items=lambda items, cursor: batches.append((list(items), cursor)), **kw)
        o.batches = batches
        o.sleep = lambda s: None
        return o

    def fake_ytdlp(self, pages):
        """pages: Reiter -> (info-Wörterbuch); gibt ein Ersatz-yt_dlp-Modul zurück."""
        outer = self

        class DownloadError(Exception):
            pass

        class FakeYDL:
            def __init__(self, options):
                outer.options = options
                outer.requests = getattr(outer, 'requests', [])

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                outer.requests.append((url, outer.options.get('playliststart')))
                tab = url.rsplit('/', 1)[-1]
                page = pages[tab]
                if isinstance(page, Exception):
                    raise page
                info = dict(page)
                start = (outer.options.get('playliststart') or 1) - 1
                info['entries'] = iter(page['entries'][start:])
                return info
        module = Mock()
        module.YoutubeDL = FakeYDL
        module.utils.DownloadError = DownloadError
        return module, DownloadError

    def entries(self, n, offset=0, **extra):
        return [dict({'id': f'a{n_:010d}', 'title': f'T{n_}', 'duration': 30 + n_, 'timestamp': 1_790_000_000 - n_ * 86400,
                      'ie_key': 'Youtube'}, **extra) for n_ in range(offset, offset + n)]

    def run_yt(self, pages, opts, value='https://www.youtube.com/@x'):
        from socialscraper.adapters.youtube import YouTubeAdapter
        module, _ = self.fake_ytdlp(pages)
        ctx = Mock(cfg=Settings(impersonate=''), im=None)
        with patch.dict('sys.modules', {'yt_dlp': module}), \
                patch('socialscraper.adapters.youtube.youtube_runtime_options', return_value={}):
            return YouTubeAdapter().channel_items(value, ctx, opts)

    def test_youtube_flat_listing_with_duration_and_total(self):
        pages = {'videos': {'channel': 'Test Kanal', 'playlist_count': 45, 'entries': self.entries(45)},
                 'shorts': {'entries': self.entries(5, offset=100)}}
        opts = self.opts()
        result = self.run_yt(pages, opts)
        self.assertEqual((result.found, result.exhausted, result.platform_total, result.name), (50, True, 45, 'Test Kanal'))
        got = [i for b, _c in opts.batches for i in b]
        self.assertEqual(len(got), 50)
        self.assertEqual(got[0]['url'], 'https://www.youtube.com/watch?v=a0000000000')
        self.assertEqual((got[0]['duration_s'], got[0]['title']), (30.0, 'T0'))
        self.assertTrue(got[0]['published_at'].endswith('Z'))
        self.assertGreater(got[0]['published_at'], got[1]['published_at'])
        self.assertEqual(opts.batches[0][1], 'videos:30')      # Speicherpunkt nach 30 Einträgen

    def test_youtube_without_shorts_and_options(self):
        pages = {'videos': {'entries': self.entries(3)}}
        result = self.run_yt(pages, self.opts(include_shorts=False))
        self.assertEqual(result.found, 3)
        self.assertEqual(self.options['extract_flat'], 'in_playlist')
        self.assertTrue(self.options['lazy_playlist'])
        self.assertFalse(self.options['getcomments'])
        self.assertEqual([r[0] for r in self.requests], ['https://www.youtube.com/@x/videos'])

    def test_youtube_skips_live_and_upcoming_and_bad_entries(self):
        entries = self.entries(4)
        entries[1]['live_status'] = 'is_upcoming'
        entries[2]['live_status'] = 'is_live'
        entries.append({'id': 'kurz', 'ie_key': 'Youtube'})
        entries.append(None)
        result = self.run_yt({'videos': {'entries': entries}}, self.opts(include_shorts=False))
        self.assertEqual(result.found, 2)

    def test_youtube_limit_cursor_and_resume(self):
        pages = {'videos': {'entries': self.entries(100)}, 'shorts': {'entries': []}}
        opts = self.opts(limit=40, include_shorts=False)
        first = self.run_yt(pages, opts)
        self.assertEqual((first.found, first.exhausted), (40, False))
        self.assertEqual(first.cursor, 'videos:40')
        opts2 = self.opts(limit=60, cursor=first.cursor, include_shorts=False)
        second = self.run_yt(pages, opts2)
        self.assertEqual(self.requests[-1][1], 41)                    # weitergemacht bei Eintrag 41
        self.assertEqual(second.found, 60)
        self.assertEqual(opts2.batches[0][0][0]['url'], 'https://www.youtube.com/watch?v=a0000000040')

    def test_youtube_since_stops_at_old_entries(self):
        pages = {'videos': {'entries': self.entries(100)}}
        since = cl.parse_utc(cl.to_iso(1_790_000_000 - 10 * 86400))
        result = self.run_yt(pages, self.opts(since=since, include_shorts=False))
        self.assertTrue(result.exhausted)
        self.assertTrue(10 <= result.found <= 14, result.found)

    def test_youtube_watching_stops_after_known_entries(self):
        entries = self.entries(100)
        known = frozenset(f'https://www.youtube.com/watch?v={e["id"]}' for e in entries[3:])
        result = self.run_yt({'videos': {'entries': entries}}, self.opts(known=known, stop_after_known=5, limit=60, include_shorts=False))
        self.assertEqual((result.found, result.reached_known), (3, True))

    def test_youtube_error_keeps_saved_items_and_cursor(self):
        class Boom(Exception):
            pass
        entries = self.entries(70)

        def broken():
            for n, e in enumerate(entries):
                if n == 45:
                    raise self.fake_ytdlp({})[1]('HTTP Error 429: Too Many Requests')
                yield e
        pages = {'videos': {'entries': []}}
        module, error = self.fake_ytdlp(pages)

        class YDL:
            def __init__(self, options): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                def gen():
                    for n, e in enumerate(entries):
                        if n == 45:
                            raise error('HTTP Error 429: Too Many Requests')
                        yield e
                return {'entries': gen()}
        module.YoutubeDL = YDL
        from socialscraper.adapters.youtube import YouTubeAdapter
        opts = self.opts(include_shorts=False)
        with patch.dict('sys.modules', {'yt_dlp': module}), patch('socialscraper.adapters.youtube.youtube_runtime_options', return_value={}):
            with self.assertRaises(Exception) as caught:
                YouTubeAdapter().channel_items('https://www.youtube.com/@x', Mock(cfg=Settings(impersonate=''), im=None), opts)
        self.assertIn('Zu viele Anfragen', str(caught.exception))
        saved = [i for b, _c in opts.batches for i in b]
        self.assertEqual(len(saved), 45)                           # alles bis zum Fehler ist übergeben

    def test_reddit_listing_entries_and_video_duration(self):
        from socialscraper.adapters import reddit
        payload = {'data': {'after': 't3_x', 'children': [
            {'kind': 't3', 'data': {'id': 'abc123', 'title': 'Video', 'permalink': '/r/a/comments/abc123/video/', 'created_utc': 1790000000,
                                    'is_video': True, 'secure_media': {'reddit_video': {'duration': 42}}}},
            {'kind': 't3', 'data': {'id': 'def456', 'title': 'Text', 'permalink': '/r/a/comments/def456/text/', 'created_utc': 1789900000,
                                    'is_self': True}},
            {'kind': 't3', 'data': {'id': 'ghi789', 'title': 'Alt', 'permalink': '/r/a/comments/ghi789/x/', 'created_utc': 1789800000,
                                    'media': {'reddit_video': {'duration': 7}}}},
            {'kind': 't1', 'data': {}}]}}
        entries, after = reddit.listing_entries(payload)
        self.assertEqual(after, 't3_x')
        self.assertEqual([(e['uid'], e['duration_s'], e['is_video']) for e in entries],
                         [('reddit:abc123', 42.0, True), ('reddit:def456', None, False), ('reddit:ghi789', 7.0, True)])
        video = reddit.video_from_post(payload['data']['children'][0]['data'])
        self.assertEqual(video.duration_s, 42.0)
        self.assertIsNone(reddit.video_from_post(payload['data']['children'][1]['data']).duration_s)

    def test_reddit_channel_items_pages_with_cursor_and_ends_honestly(self):
        from socialscraper.adapters.reddit import RedditAdapter

        def page(after, start):
            return {'data': {'after': after, 'children': [
                {'kind': 't3', 'data': {'id': f'p{start + n:05d}', 'title': 't', 'permalink': f'/r/a/comments/p{start + n:05d}/t/',
                                        'created_utc': 1790000000 - (start + n)}} for n in range(100)]}}
        responses = [page('t3_a', 0), page('t3_b', 100), page(None, 200)]
        adapter = RedditAdapter()
        paths = []

        def fetch_json(page_, path):
            paths.append(path)
            return responses.pop(0)
        sessions = Mock()
        sessions.acquire.return_value.__enter__ = lambda s: Mock(url='', evaluate=Mock(return_value=''), goto=Mock(), wait_for_timeout=Mock())
        sessions.acquire.return_value.__exit__ = lambda *a: False
        ctx = Mock(sessions=sessions, identity='reddit_main', country='de', mobile=False)
        opts = self.opts()
        with patch.object(RedditAdapter, 'fetch_json', side_effect=fetch_json), patch.object(RedditAdapter, '_pause'):
            result = adapter.channel_items('r/test', ctx, opts)
        self.assertEqual((result.found, result.exhausted), (300, True))
        self.assertIn('1.000', result.note)
        self.assertIn('/r/test/new.json', paths[0])
        self.assertIn('after=t3_a', paths[1])

    def test_tiktok_profile_scrolls_reads_dates_from_ids_and_stops_on_captcha(self):
        from socialscraper.adapters.tiktok import TikTokAdapter
        base = 1_790_000_000
        ids = [(base - n * 3600) << 32 for n in range(5)]
        links = [f'https://www.tiktok.com/@someone/video/{i}?lang=de' for i in ids]
        rounds = [links[:2], links[:4], links, links, links, links, links, links]

        class Page:
            url = ''

            def __init__(self):
                self.n = 0

            def evaluate(self, js, *a):
                if 'videoCount' in js:
                    return {'videoCount': 123, 'name': 'Some One'}
                if 'a[href*="/video/"]' in js:
                    value = rounds[min(self.n, len(rounds) - 1)]
                    self.n += 1
                    return value
                return ''

            def goto(self, *a, **k): pass
            def wait_for_timeout(self, *a): pass
        page = Page()
        sessions = Mock()
        sessions.acquire.return_value.__enter__ = lambda s: page
        sessions.acquire.return_value.__exit__ = lambda *a: False
        ctx = Mock(sessions=sessions, identity='t', country='de', mobile=False, authenticated=False, require_auth=False)
        ctx.cfg = Settings()
        opts = self.opts()
        with patch('socialscraper.net.humanize.warmup'), patch('socialscraper.net.humanize.scroll'):
            result = TikTokAdapter().channel_items('someone', ctx, opts)
        self.assertEqual((result.found, result.exhausted, result.platform_total, result.name), (5, True, 123, 'Some One'))
        items = [i for b, _c in opts.batches for i in b]
        self.assertEqual(items[0]['url'], f'https://www.tiktok.com/@someone/video/{ids[0]}')
        self.assertEqual(items[0]['published_at'], cl.to_iso(base))
        # Sicherheitsabfrage: bricht ab, schon Gefundenes bleibt übergeben
        page2 = Page()
        sessions.acquire.return_value.__enter__ = lambda s: page2
        rounds[:] = [links[:2]] * 10
        opts2 = self.opts()
        with patch('socialscraper.net.humanize.warmup'), patch('socialscraper.net.humanize.scroll'), \
                patch.object(TikTokAdapter, '_blocked_message', return_value='TikTok zeigt eine Sicherheitsabfrage (CAPTCHA).'):
            with self.assertRaises(Exception) as caught:
                TikTokAdapter().channel_items('someone', ctx, opts2)
        self.assertIn('CAPTCHA', str(caught.exception))
        self.assertEqual(len([i for b, _c in opts2.batches for i in b]), 2)

    def test_x_infos_carry_date_duration_and_media(self):
        import json
        from socialscraper.adapters import x
        data = json.loads((pathlib.Path(__file__).parent / 'fixtures' / 'x_tweetdetail.json').read_text(encoding='utf-8'))
        infos = x.infos_from_payloads([data])
        self.assertTrue(infos)
        self.assertTrue(all(i['id'] and i['handle'] for i in infos))
        self.assertTrue(all('duration' in i and 'media_types' in i for i in infos))
        self.assertIsNotNone(cl.x_time('1800000000000000000'))
        self.assertIsNone(cl.x_time('12'))

    def test_instagram_lists_only_videos_with_duration_and_reports_scan(self):
        from socialscraper.adapters.instagram import InstagramAdapter
        import instaloader
        base = datetime.datetime(2026, 10, 1, tzinfo=UTC)

        class Post:
            def __init__(self, n, video):
                self.shortcode, self.is_video, self.typename = f'Sc{n:08d}', video, 'GraphVideo' if video else 'GraphImage'
                self.date_utc = base - datetime.timedelta(days=n)
                self.video_duration = 12.5 + n if video else None
                self.caption = f'Beitrag {n}'

        class Profile:
            mediacount, full_name, is_private, followed_by_viewer = 10, 'Voller Name', False, True

            def get_posts(self):
                return iter([Post(n, n % 2 == 0) for n in range(10)])
        ctx = Mock(cfg=Settings(), im=None)
        with patch.object(InstagramAdapter, '_login'), patch.object(instaloader.Profile, 'from_username', return_value=Profile()):
            result = InstagramAdapter().channel_items('someone', ctx, self.opts())
        self.assertEqual((result.found, result.exhausted, result.platform_total, result.name), (5, True, 10, 'Voller Name'))
        self.assertIn('10 Beiträge geprüft, davon 5 Videos', result.note)

    def test_instagram_rate_limit_is_reported_as_429(self):
        from socialscraper.adapters.instagram import InstagramAdapter
        from socialscraper import rotation
        import instaloader

        class Profile:
            mediacount, full_name, is_private, followed_by_viewer = 10, 'x', False, True

            def get_posts(self):
                raise instaloader.exceptions.TooManyRequestsException('429')
        with patch.object(InstagramAdapter, '_login'), patch.object(instaloader.Profile, 'from_username', return_value=Profile()):
            with self.assertRaises(RuntimeError) as caught:
                InstagramAdapter().channel_items('someone', Mock(cfg=Settings(), im=None), self.opts())
        self.assertEqual(rotation.classify(caught.exception), 'ratelimit')


if __name__ == '__main__':
    unittest.main()
