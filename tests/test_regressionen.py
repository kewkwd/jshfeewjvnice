"""Regressionstests zu den elf bestätigten Prüferbefunden (Wegwerf-Ordner, keine echten Daten, keine Cloud).

Nummern wie im Prüfbericht:
 1 Dienst übersteht gesperrte Datenbank   2 Startfehler stehen im daemon.log   3 keine Weiterleitung mit Schlüssel
 4 Keyset-Paging in Push/Pull             5 Migration atomar + Nachfüllen        6 Unicode-Zeilenumbrüche, Kopfteil
 7 strenge Link-Prüfung                   8 API: since/inf/Journal               9 integration.json gleichzeitig
10 fehlerhafte Cloud-Zeile                11 Auftragsstart mit NUL im Link
"""
import http.server
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from socialscraper import api, daemon, formlogic, guicore, integration, repo, scheduler, sync
from socialscraper.models import Comment, Video
from socialscraper.store import Store

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # test_sync wie bei `unittest discover -s tests`
ROOT = pathlib.Path(__file__).resolve().parent.parent
YT = 'https://www.youtube.com/watch?v='
TOKEN = 'regress-token-' + 'x' * 30


def child_env(db_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith('SS_')}
    env.update(PYTHONPATH=str(ROOT), PYTHONUTF8='1', SS_DB_PATH=str(db_path))
    return env


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        (self.root / 'data').mkdir()
        self.db_file = self.root / 'data' / 'scrape.db'


# ---------- 1 + Hinweis: runs kürzen ----------

class DaemonDbLockTests(Tmp):
    def setUp(self):
        super().setUp()
        from socialscraper.config import Settings
        self.cfg = Settings(db_path=str(self.db_file), notify_file='', profiles_dir=str(self.root / 'data' / 'profiles'))
        with Store(self.cfg.db_path) as s:
            repo.ensure_default_accounts(s.db)
            self.target = repo.add_target(s.db, 'video', 'youtube', YT + 'aaaaaaaaaaa')

    def start(self, **kw):
        stop = threading.Event()
        result = {}

        def work():
            try:
                result['ran'] = daemon.run(self.root, stop_event=stop, cfg=self.cfg, install_signals=False, **kw)
            except BaseException as error:   # noqa
                result['error'] = error
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(10)))
        return stop, thread, result

    def wait_for(self, condition, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if condition():
                return True
            time.sleep(0.05)
        return False

    def test_database_errors_in_the_loop_are_logged_and_survived(self):
        calls = {'n': 0}
        real = repo.due_targets

        def flaky(db, moment=None):
            calls['n'] += 1
            if calls['n'] <= 2:
                raise sqlite3.OperationalError('database is locked')
            return real(db, moment)

        ran = []
        with mock.patch.object(daemon, 'DB_BACKOFF_MIN', 0.1), mock.patch.object(repo, 'due_targets', flaky), \
                mock.patch.object(scheduler, 'run_target', side_effect=lambda root, tid, source, **kw: ran.append(tid) or
                                  {'status': 'ok', 'target_id': tid}), \
                self.assertLogs('socialscraper.daemon', 'ERROR') as logs:
            stop, thread, result = self.start(poll=1)
            self.assertTrue(self.wait_for(lambda: ran), 'der Dienst muss nach den Fehlern weiterarbeiten')
            self.assertTrue(thread.is_alive())
            self.assertNotIn('error', result)
            stop.set()
            thread.join(10)
        self.assertTrue(any('database is locked' in line for line in logs.output), logs.output)

    def test_real_exclusive_lock_longer_than_the_timeout_is_survived(self):
        """Wie vom Prüfer: zweiter Prozess hält BEGIN EXCLUSIVE länger als die 5 s Wartezeit von sqlite."""
        holder = sqlite3.connect(self.db_file, isolation_level=None)
        holder.execute('BEGIN EXCLUSIVE')
        ran = []
        with mock.patch.object(daemon, 'DB_BACKOFF_MIN', 0.5), \
                mock.patch.object(scheduler, 'run_target', side_effect=lambda root, tid, source, **kw: ran.append(tid) or
                                  {'status': 'ok', 'target_id': tid}), \
                self.assertLogs('socialscraper.daemon', 'ERROR') as logs:
            stop, thread, result = self.start(poll=1)
            # Sperre halten, bis der Dienst sie als Fehler gemeldet hat (sqlite wartet 5 s) – nicht nach fester Zeit,
            # damit ein langsamer Rechner den Test nicht kippt.
            self.assertTrue(self.wait_for(lambda: any('Datenbank gerade nicht nutzbar' in m for m in logs.output), 40))
            time.sleep(1)
            self.assertTrue(thread.is_alive(), 'der Dienst darf bei gesperrter Datenbank nicht sterben')
            self.assertEqual(ran, [])
            holder.execute('ROLLBACK')
            holder.close()
            self.assertTrue(self.wait_for(lambda: ran), 'nach der Sperre muss der Dienst weiterarbeiten')
            self.assertTrue(daemon.status(self.root)['last_beat'])   # Herzschlag läuft weiter
            self.assertNotIn('error', result)
            stop.set()
            thread.join(10)
        self.assertTrue(any('Datenbank gerade nicht nutzbar' in line for line in logs.output), logs.output)

    def test_backoff_grows_and_is_capped(self):
        real_sleep = time.sleep
        with mock.patch.object(daemon, 'DB_BACKOFF_MIN', 2.0), mock.patch.object(daemon, 'DB_BACKOFF_MAX', 8.0), \
                mock.patch.object(repo, 'due_targets', side_effect=sqlite3.OperationalError('database is locked')), \
                mock.patch.object(daemon.time, 'sleep', side_effect=lambda s: real_sleep(0.001)), \
                self.assertLogs('socialscraper.daemon', 'ERROR') as logs:
            stop, thread, result = self.start(poll=1)
            self.assertTrue(self.wait_for(lambda: len([m for m in logs.output if 'Neuer Versuch' in m]) >= 5))
            stop.set()
            thread.join(10)
        waits = [int(m.rsplit(' in ', 1)[1].split(' s')[0]) for m in logs.output if 'Neuer Versuch' in m][:5]
        self.assertEqual(waits[:4], [2, 4, 8, 8])

    def test_runs_are_trimmed_at_start(self):
        with Store(self.cfg.db_path) as s:
            s.db.executemany("INSERT INTO runs (target_id, label, source, started_at, finished_at, status) VALUES (?,?,?,?,?,?)",
                             [(None, f'r{i}', 'daemon', '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z', 'ok') for i in range(5200)])
            s.db.commit()
        with mock.patch.object(scheduler, 'run_target', return_value={'status': 'ok', 'target_id': 1}):   # kein Netz
            daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        with Store(self.cfg.db_path) as s:
            labels = [r[0] for r in s.db.execute("SELECT label FROM runs WHERE label LIKE 'r%' ORDER BY id")]
        self.assertEqual(len(labels), 5000)
        self.assertEqual(labels[-1], 'r5199')
        self.assertEqual(labels[0], 'r200')

    def test_trim_runs_keeps_running_rows(self):
        with Store(self.cfg.db_path) as s:
            s.db.execute("INSERT INTO runs (label, source, started_at, status) VALUES ('alt-laufend','daemon','2026-01-01T00:00:00Z','running')")
            s.db.executemany("INSERT INTO runs (label, source, started_at, status) VALUES (?,?,?,?)",
                             [(f'x{i}', 'daemon', '2026-01-01T00:00:00Z', 'ok') for i in range(10)])
            s.db.commit()
            repo.trim_runs(s.db, 3)
            labels = [r[0] for r in s.db.execute('SELECT label FROM runs ORDER BY id')]
        self.assertEqual(labels, ['alt-laufend', 'x7', 'x8', 'x9'])


# ---------- 2 ----------

class DaemonStartupErrorsTests(Tmp):
    def run_daemon(self, settings, db_path=None):
        if settings is not None:
            (self.root / 'settings.toml').write_text(settings, encoding='utf-8')
        done = subprocess.run([sys.executable, '-m', 'socialscraper', 'daemon', '--root', str(self.root), '--once'],
                              cwd=str(self.root), env=child_env(db_path or self.db_file), capture_output=True, timeout=60,
                              stdin=subprocess.DEVNULL)
        log = self.root / 'data' / 'daemon.log'
        self.assertTrue(log.exists(), 'daemon.log muss existieren')
        return done, log.read_text(encoding='utf-8')

    def test_broken_settings_toml_is_logged(self):
        done, text = self.run_daemon('max_comments = = 5\n')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('nicht angelaufen', text)
        self.assertIn('TOMLDecodeError', text)

    def test_unknown_key_is_logged_with_its_name(self):
        done, text = self.run_daemon('foo = 1\n')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('Unbekannte Einstellung: foo', text)

    def test_db_path_that_is_a_folder_is_logged(self):
        folder = self.root / 'data' / 'ordner'
        folder.mkdir()
        done, text = self.run_daemon('', db_path=folder)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('unable to open database file', text)

    def test_error_is_logged_only_once(self):
        done, text = self.run_daemon('foo = 1\n')
        self.assertEqual(text.count('Unbekannte Einstellung: foo'), 1, text)


# ---------- 3 ----------

class RedirectTests(Tmp):
    def test_redirects_are_refused_and_key_is_not_forwarded(self):
        seen = []

        class Other(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                seen.append(dict(self.headers))
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'[]')
            do_POST = do_GET
        other = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Other)
        threading.Thread(target=other.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()
        self.addCleanup(lambda: (other.shutdown(), other.server_close()))
        target = f'http://127.0.0.1:{other.server_address[1]}/stolen'

        class Redirecting(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                self.send_response(307); self.send_header('Location', target); self.send_header('Content-Length', '0')
                self.end_headers()
            do_POST = do_GET
        first = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Redirecting)
        threading.Thread(target=first.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()
        self.addCleanup(lambda: (first.shutdown(), first.server_close()))

        key = 'SECRET-KEY-regression-0123456789abcdef'
        cfg = integration.default_config()
        cfg['sync'].update(kind='supabase', url=f'http://127.0.0.1:{first.server_address[1]}', key=key)
        integration.save(cfg, self.root)
        with Store(str(self.db_file)) as s:
            s.save_result(Video('tiktok', '1', 'https://www.tiktok.com/@a/video/1', author='a'), [])
        for message in (sync.test(self.root)['message'], sync.push(self.root, db_path=self.db_file)['error']):
            self.assertIn('leitet auf eine andere Adresse weiter', message)
            self.assertNotIn(key, message)
        self.assertEqual(seen, [], 'der fremde Host darf nichts (erst recht keinen Schlüssel) bekommen haben')
        self.assertNotIn(key, json.dumps(integration.load(self.root)['sync'].get('last_error')))


# ---------- 4 + 10 ----------

class _Cloud:
    """Kleiner Zugriff auf den Fake-PostgREST aus test_sync."""

    def __new__(cls):
        from test_sync import FakeCloud
        return FakeCloud()


class KeysetPagingTests(Tmp):
    def seed_videos(self, n=1000):
        with Store(str(self.db_file)) as s:
            rows = [(f'tiktok:{i:04d}', 'tiktok', f'{i:04d}', f'2026-10-01 10:{i // 60:02d}:{i % 60:02d}', f'2026-10-01 10:{i // 60:02d}:{i % 60:02d}')
                    for i in range(n)]
            s.db.executemany('INSERT INTO videos (uid, platform, video_id, fetched_at, first_seen_at) VALUES (?,?,?,?,?)', rows)
            s.db.commit()

    def test_push_batches_lose_nothing_when_rows_change_between_batches(self):
        self.seed_videos(1000)
        ro = sync._ro(self.db_file)
        writer = sqlite3.connect(self.db_file)
        seen, first = [], True
        try:
            for batch in sync._local_batches(ro, 'videos', None, 100):
                seen.extend(c['uid'] for c, _ in batch)
                if first:   # zwischendurch: zehn frühe Videos werden neu abgerufen (ihr Zeitstempel springt nach hinten)
                    first = False
                    writer.execute("UPDATE videos SET fetched_at='2026-12-01 00:00:00' WHERE uid < 'tiktok:0010'")
                    writer.execute("INSERT INTO videos (uid, platform, video_id, fetched_at) VALUES ('tiktok:neu','tiktok','neu','2026-09-01 00:00:00')")
                    writer.commit()
        finally:
            ro.close()
            writer.close()
        self.assertEqual({f'tiktok:{i:04d}' for i in range(1000)} - set(seen), set(), 'es dürfen keine Zeilen fehlen')

    def test_push_pages_are_in_order_without_duplicates_when_nothing_changes(self):
        self.seed_videos(250)
        ro = sync._ro(self.db_file)
        try:
            pages = list(sync._local_batches(ro, 'videos', None, 100))
        finally:
            ro.close()
        uids = [c['uid'] for page in pages for c, _ in page]
        self.assertEqual([len(p) for p in pages], [100, 100, 50])
        self.assertEqual(uids, sorted(uids))
        self.assertEqual(len(set(uids)), 250)

    def test_push_keyset_with_composite_keys(self):
        with Store(str(self.db_file)) as s:
            s.db.executemany('INSERT INTO video_snapshots (video_uid, captured_at, view_count) VALUES (?,?,?)',
                             [(f'tiktok:{i % 7}', f'2026-10-01T10:00:{i // 7:02d}Z', i) for i in range(70)] +
                             [('tiktok:1', '2026-10-01T10:00:00.5Z', 1), ('tiktok:1', '2026-10-01 10:00:00', 2)])
            s.db.commit()
        ro = sync._ro(self.db_file)
        try:
            got = [(c['video_uid'], c['captured_at']) for page in sync._local_batches(ro, 'video_snapshots', None, 4) for c, _ in page]
        finally:
            ro.close()
        self.assertEqual(len(got), 72, 'keine Zeile fehlt, keine kommt doppelt')

    def test_pull_loses_nothing_when_cloud_rows_change_between_pages(self):
        cloud = _Cloud()
        self.addCleanup(cloud.close)
        cfg = integration.default_config()
        from test_sync import KEY
        cfg['sync'].update(kind='supabase', url=cloud.url, key=KEY)
        integration.save(cfg, self.root)
        cloud.tables['videos'] = {(f'tiktok:{i:03d}',): {'uid': f'tiktok:{i:03d}', 'platform': 'tiktok', 'video_id': f'{i:03d}',
                                                           'fetched_at': f'2026-10-01T10:{i // 60:02d}:{i % 60:02d}Z'}
                                  for i in range(60)}
        real = sync.Client(integration.load(self.root))
        state = {'n': 0}

        class Wrapper:
            def request(self_inner, method, path, **kw):
                result = real.request(method, path, **kw)
                state['n'] += 1 if path.startswith('videos') else 0
                if state['n'] == 1 and path.startswith('videos'):   # nach der ersten Seite werden frühe Zeilen neu geschrieben
                    for i in range(8):
                        cloud.tables['videos'][(f'tiktok:{i:03d}',)]['fetched_at'] = '2026-12-01T00:00:00Z'
                return result
        result = sync.pull(self.root, db_path=self.db_file, client=Wrapper(), batch=10)
        self.assertTrue(result['ok'], result)
        with Store(str(self.db_file)) as s:
            have = {r[0] for r in s.db.execute('SELECT uid FROM videos')}
        self.assertEqual({f'tiktok:{i:03d}' for i in range(60)} - have, set())


class BadCloudRowTests(Tmp):
    def test_one_unusable_row_does_not_fail_the_pull(self):
        cloud = _Cloud()
        self.addCleanup(cloud.close)
        from test_sync import KEY
        cfg = integration.default_config()
        cfg['sync'].update(kind='supabase', url=cloud.url, key=KEY)
        integration.save(cfg, self.root)
        rows = {}
        for i in range(5):
            uid = f'tiktok:{i}'
            rows[(uid,)] = {'uid': uid, 'platform': 'tiktok', 'video_id': str(i), 'fetched_at': f'2026-10-0{i + 1}T10:00:00Z',
                            'view_count': 10 ** 30 if i == 2 else i}
        cloud.tables['videos'] = rows
        with self.assertLogs('sync', 'WARNING'):
            result = sync.pull(self.root, db_path=self.db_file)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['counts']['videos'], 4)
        self.assertEqual(result['invalid'], 1)
        self.assertIn('1 fehlerhafte Zeilen', result['warning'])
        with Store(str(self.db_file)) as s:
            have = {r[0] for r in s.db.execute('SELECT uid FROM videos')}
        self.assertEqual(have, {'tiktok:0', 'tiktok:1', 'tiktok:3', 'tiktok:4'})

    def test_database_errors_still_abort_the_merge(self):
        with Store(str(self.root / 'data' / 'x.db')) as s:
            with mock.patch.object(sync, '_merge_one', side_effect=sqlite3.OperationalError('database is locked')):
                with self.assertRaises(sqlite3.OperationalError):
                    sync._merge_rows(s.db, 'videos', [{'uid': 'a'}])


# ---------- 5 ----------

class MigrationTests(Tmp):
    OLD = '''CREATE TABLE videos (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, url TEXT, author TEXT, author_id TEXT,
        caption TEXT, created_at TEXT, duration_s REAL, view_count INTEGER, like_count INTEGER, comment_count INTEGER,
        share_count INTEGER, save_count INTEGER, hashtags TEXT, mentions TEXT, music TEXT, language TEXT, thumbnail TEXT,
        raw TEXT, fetched_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE comments (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, comment_id TEXT, text TEXT, author TEXT,
        author_id TEXT, created_at TEXT, like_count INTEGER, reply_count INTEGER, parent_id TEXT, is_author_reply INTEGER,
        language TEXT, raw TEXT, fetched_at TEXT DEFAULT CURRENT_TIMESTAMP);'''

    def make_old(self):
        c = sqlite3.connect(self.db_file)
        c.executescript(self.OLD)
        c.executemany("INSERT INTO videos (uid, platform, video_id, fetched_at) VALUES (?,?,?,?)",
                      [(f'tiktok:{i}', 'tiktok', str(i), f'2026-01-0{i + 1} 10:00:00') for i in range(3)])
        c.execute("INSERT INTO comments (uid, platform, video_id, comment_id, fetched_at) VALUES ('tiktok:0:c','tiktok','0','c','2026-01-01 10:00:00')")
        c.commit()
        c.close()

    def columns(self, table='videos'):
        c = sqlite3.connect(self.db_file)
        try:
            return [r[1] for r in c.execute(f'PRAGMA table_info({table})')]
        finally:
            c.close()

    def test_failing_update_rolls_back_the_new_column(self):
        self.make_old()
        c = sqlite3.connect(self.db_file)
        c.execute("CREATE TRIGGER stop_it BEFORE UPDATE ON videos BEGIN SELECT RAISE(ABORT, 'nein'); END")
        c.commit()
        c.close()
        with self.assertRaises(sqlite3.Error):
            Store(str(self.db_file))
        self.assertNotIn('first_seen_at', self.columns(), 'ALTER und UPDATE gehören in eine Transaktion')
        c = sqlite3.connect(self.db_file)
        c.execute('DROP TRIGGER stop_it')
        c.commit()
        c.close()
        with Store(str(self.db_file)) as s:
            nulls = s.db.execute('SELECT COUNT(*) FROM videos WHERE first_seen_at IS NULL').fetchone()[0]
            self.assertEqual(nulls, 0)
            self.assertEqual(s.db.execute("SELECT first_seen_at FROM videos WHERE uid='tiktok:1'").fetchone()[0], '2026-01-02T10:00:00Z')

    def test_rows_left_without_first_seen_at_are_filled_at_start(self):
        self.make_old()
        c = sqlite3.connect(self.db_file)
        c.execute('ALTER TABLE videos ADD COLUMN first_seen_at TEXT')   # wie nach dem früheren, halb gescheiterten Lauf
        c.execute('ALTER TABLE comments ADD COLUMN first_seen_at TEXT')
        c.execute("UPDATE videos SET first_seen_at='2020-05-05T00:00:00Z' WHERE uid='tiktok:2'")
        c.commit()
        c.close()
        with Store(str(self.db_file)) as s:
            got = dict(s.db.execute('SELECT uid, first_seen_at FROM videos'))
            self.assertEqual(got['tiktok:2'], '2020-05-05T00:00:00Z', 'vorhandene Werte bleiben')
            self.assertEqual(got['tiktok:0'], '2026-01-01T10:00:00Z')      # die Migration schreibt das vereinbarte Zeitformat
            self.assertEqual(s.db.execute('SELECT first_seen_at FROM comments').fetchone()[0], '2026-01-01T10:00:00Z')


# ---------- 6 ----------

class UnicodeSettingsTests(Tmp):
    def write(self, text):
        (self.root / 'settings.toml').write_text(text, encoding='utf-8', newline='')

    def test_unicode_line_separators_do_not_lock_the_writer_out(self):
        for ch in ('\u2028', '\u2029', '\x85'):
            self.write('max_comments = 5\n')
            guicore.write_settings({'notify_webhook': f'http://127.0.0.1/a{ch}b'}, self.root)   # wird maskiert geschrieben
            guicore.write_settings({'max_comments': 9}, self.root)                              # zweiter Schreibvorgang geht
            self.assertEqual(tomllib.loads((self.root / 'settings.toml').read_text(encoding='utf-8'))['notify_webhook'],
                             f'http://127.0.0.1/a{ch}b')

    def test_already_present_separators_in_comments_and_strings_survive(self):
        for ch in ('\u2028', '\u2029', '\x85'):
            self.write(f'# Kommentar mit {ch} Zeichen\nnotify_webhook = "a{ch}b"\nmax_comments = 5\n')
            guicore.write_settings({'max_comments': 9}, self.root)
            data = tomllib.loads((self.root / 'settings.toml').read_text(encoding='utf-8'))
            self.assertEqual((data['max_comments'], data['notify_webhook']), (9, f'a{ch}b'))
            self.assertIn(f'# Kommentar mit {ch} Zeichen', (self.root / 'settings.toml').read_text(encoding='utf-8'))

    def test_crlf_stays_crlf(self):
        self.write('# x\r\nmax_comments = 5\r\nheadless = true\r\n')
        guicore.write_settings({'max_comments': 7}, self.root)
        raw = (self.root / 'settings.toml').read_bytes()
        self.assertEqual(raw, b'# x\r\nmax_comments = 7\r\nheadless = true\r\n')

    def test_form_rejects_the_characters(self):
        for ch in ('\u2028', '\u2029', '\x85', '\x7f', '\x00'):
            value, error = formlogic.validate_notify_file(f'data/a{ch}b.log')
            self.assertIsNone(value, repr(ch))
            self.assertTrue(error)
            self.assertIsNone(formlogic.validate_webhook(f'https://x.de/a{ch}b')[0])

    def test_bracket_line_inside_multiline_string_is_not_a_table(self):
        self.write('ms_token = """\n[evil]\nmax_comments = 1\n"""\nmax_comments = 5\n[sphere_presets]\ntiktok = ""\n')
        guicore.write_settings({'max_comments': 9, 'headless': False}, self.root)
        data = tomllib.loads((self.root / 'settings.toml').read_text(encoding='utf-8'))
        self.assertEqual((data['max_comments'], data['headless']), (9, False))
        self.assertEqual(data['ms_token'], '[evil]\nmax_comments = 1\n')   # der Text bleibt unberührt
        self.assertEqual(list(data['sphere_presets']), ['tiktok'])
        self.assertNotIn('evil', data)

    def test_bracket_line_inside_multiline_array_is_not_a_table(self):
        self.write('proxies = [\n  "http://a.de:1",\n  ["x"],\n]\nmax_comments = 5\n[sphere_presets]\ntiktok = ""\n')
        text = guicore.render_settings((self.root / 'settings.toml').read_text(encoding='utf-8'),
                                       {'max_comments': 9, 'headless': False})
        data = tomllib.loads(text)
        self.assertEqual((data['max_comments'], data['headless']), (9, False))
        self.assertLess(text.index('headless'), text.index('[sphere_presets]'))

    def test_new_key_goes_before_the_first_real_table_even_with_bracket_lines_in_strings(self):
        raw = 'note = """\n[nicht]\n"""\n\n[tabelle]\nx = 1\n'
        text = guicore.render_settings(raw, {'max_comments': 3})
        self.assertLess(text.index('max_comments'), text.index('[tabelle]'))
        self.assertEqual(tomllib.loads(text)['max_comments'], 3)


# ---------- 7 ----------

class LinkTests(unittest.TestCase):
    def test_backslash_in_host_is_rejected(self):
        for link in ('https://evil.com\\.tiktok.com/x', 'https://evil.com\\@tiktok.com/x', 'https://tiktok.com\\@evil.com/x'):
            self.assertIsNone(guicore.detect_platform(link), link)
        self.assertEqual(guicore.detect_platform('https://www.tiktok.com/@a/video/1'), 'tiktok')
        self.assertEqual(guicore.detect_platform('https://youtu.be/abc'), 'youtube')
        self.assertEqual(guicore.detect_platform('https://www.instagram.com/reel/x/'), 'instagram')
        self.assertIsNone(guicore.detect_platform('https://evil.com/?u=tiktok.com'))
        self.assertIsNone(guicore.detect_platform('https://tiktok.com:99999/x'))

    def test_control_characters_are_rejected(self):
        for link in ('https://www.tiktok.com/@a/video/1\x00', 'https://www.tiktok.com/\x01x', 'https://www.tiktok.com/a\x7fb'):
            self.assertIsNone(guicore.detect_platform(link), repr(link))
        groups, bad = guicore.parse_links('https://www.tiktok.com/@a/video/1\nhttps://www.tiktok.com/@a/video/2\x00\nhttps://www.tiktok.com/x\ty\x1b[0m')
        self.assertEqual(groups, {'tiktok': ['https://www.tiktok.com/@a/video/1']})
        self.assertEqual(len(bad), 2)


# ---------- 8 ----------

class ApiRegressionTests(Tmp):
    def setUp(self):
        super().setUp()
        with Store(str(self.db_file)) as s:
            s.save_result(Video('tiktok', '1', 'https://www.tiktok.com/@a/video/1', author='a'), [])
        cfg = integration.default_config()
        cfg['api'].update(token=TOKEN)
        self.handle = api.start(self.root, db_path=self.db_file, port=0, cfg=cfg)
        self.addCleanup(self.handle.stop)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path):
        req = urllib.request.Request(self.handle.url + path, headers={'Authorization': 'Bearer ' + TOKEN})
        try:
            with self.opener.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read()), resp.headers
        except urllib.error.HTTPError as error:
            body = error.read()
            error.close()
            return error.code, json.loads(body), error.headers

    def test_since_at_the_edge_of_the_calendar_is_400_not_500(self):
        for since in ('0001-01-01T00:00:00+05:00', '9999-12-31T23:59:59-05:00'):
            status, body, _ = self.get(f'/v1/videos?since={urllib.parse.quote(since)}')
            self.assertEqual(status, 400, since)
        with self.assertRaises(ValueError):
            api.parse_since('0001-01-01T00:00:00+05:00')
        self.assertEqual(api.parse_since('2026-10-01T10:00:00+02:00'), '2026-10-01T08:00:00')

    def test_non_finite_numbers_are_null(self):
        with Store(str(self.db_file)) as s:
            s.db.execute("UPDATE videos SET duration_s=?", (float('inf'),))
            s.db.commit()
        status, body, _ = self.get('/v1/videos')
        self.assertEqual(status, 200)
        self.assertIsNone(body['items'][0]['duration_s'])
        status, body, _ = self.get('/v1/videos/' + urllib.parse.quote('tiktok:1', safe=''))
        self.assertEqual(status, 200)

    def test_unlesbare_zeit_wird_unveraendert_ausgegeben(self):
        self.assertEqual(api.to_iso('0001-01-01T00:00:00+05:00'), '0001-01-01T00:00:00+05:00')

    def test_hot_journal_is_503_with_retry_after_and_window_does_not_raise(self):
        with Store(str(self.db_file)) as s:
            s.db.executemany('INSERT INTO videos (uid, platform, video_id, url, caption, fetched_at) VALUES (?,?,?,?,?,?)',
                             [(f'tiktok:x{i}', 'tiktok', f'x{i}', 'u', 'x' * 500, '2026-01-01T00:00:00Z') for i in range(3000)])
            s.db.commit()
        code = (f"import sqlite3, os\nc=sqlite3.connect({str(self.db_file)!r}, isolation_level=None)\n"
                "c.execute('PRAGMA cache_size=10')\nc.execute('BEGIN')\nc.execute(\"update videos set caption=caption||'y'\")\nos._exit(1)\n")
        subprocess.run([sys.executable, '-c', code], timeout=60)
        journal = self.db_file.with_name(self.db_file.name + '-journal')
        if not journal.exists():
            self.skipTest('Auf diesem Rechner blieb kein offenes Journal übrig.')
        status, body, headers = self.get('/v1/stats')
        self.assertEqual(status, 503, body)
        self.assertEqual(body['error']['code'], 'db_unreadable')
        self.assertTrue(headers.get('Retry-After'))
        self.assertEqual(guicore.counts(self.db_file), (None, None))    # wirft nicht
        Store(str(self.db_file)).close()                                 # ein Schreiber räumt das Journal auf
        self.assertEqual(self.get('/v1/stats')[0], 200)
        self.assertEqual(guicore.counts(self.db_file)[0], 3001)

    def test_idle_connection_is_closed_after_a_few_seconds(self):
        so = socket.create_connection(('127.0.0.1', self.handle.port), timeout=12)
        started = time.monotonic()
        try:
            self.assertEqual(so.recv(10), b'')       # Server schließt von selbst
        finally:
            so.close()
        self.assertLess(time.monotonic() - started, 9)

    def test_connection_cap(self):
        with mock.patch.object(api._Server, 'max_connections', 2):
            cfg = integration.default_config()
            cfg['api'].update(token=TOKEN)
            small = api.start(self.root, db_path=self.db_file, port=0, cfg=cfg)
        self.addCleanup(small.stop)
        held = [socket.create_connection(('127.0.0.1', small.port), timeout=5) for _ in range(2)]
        self.addCleanup(lambda: [h.close() for h in held])
        time.sleep(0.3)
        extra = socket.create_connection(('127.0.0.1', small.port), timeout=5)
        self.addCleanup(extra.close)
        self.assertEqual(extra.recv(10), b'')        # sofort geschlossen: Obergrenze erreicht
        held[0].close()
        time.sleep(0.5)
        so = socket.create_connection(('127.0.0.1', small.port), timeout=5)
        self.addCleanup(so.close)
        so.sendall(b'GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
        self.assertIn(b'200', so.recv(100))


# ---------- 9 ----------

class IntegrationFileTests(Tmp):
    def configure(self):
        cfg = integration.default_config()
        cfg['sync'].update(kind='supabase', url='https://abc.supabase.co', key='K' * 40)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            free_port = probe.getsockname()[1]
        cfg['api'].update(token='T' * 30, enabled=True, port=free_port)
        integration.save(cfg, self.root)

    def test_short_read_failures_are_retried(self):
        self.configure()
        real = pathlib.Path.read_text
        calls = {'n': 0}

        def flaky(self_path, *a, **k):
            if self_path.name == integration.FILE_NAME:
                calls['n'] += 1
                if calls['n'] <= 4:
                    raise PermissionError(13, 'Zugriff verweigert')
            return real(self_path, *a, **k)
        with mock.patch.object(pathlib.Path, 'read_text', flaky):
            cfg, problem = integration.load_with_status(self.root)
        self.assertIsNone(problem)
        self.assertEqual(cfg['sync']['key'], 'K' * 40)
        self.assertEqual(calls['n'], 5)

    def test_permanent_failure_is_reported_and_never_overwrites_with_defaults(self):
        self.configure()
        real = pathlib.Path.read_text

        def broken(self_path, *a, **k):
            if self_path.name == integration.FILE_NAME:
                raise PermissionError(13, 'Zugriff verweigert')
            return real(self_path, *a, **k)
        with mock.patch.object(pathlib.Path, 'read_text', broken):
            _cfg, problem = integration.load_with_status(self.root)
            self.assertIn('lässt sich nicht lesen', problem)
            with self.assertRaises(integration.IntegrationError):
                integration.load_for_update(self.root)
            with self.assertRaises(integration.IntegrationError):
                integration.ensure_token(self.root)
            self.assertFalse(integration.update_sync_status(self.root, last_error='x'))
        cfg = integration.load(self.root)    # nichts wurde überschrieben
        self.assertEqual((cfg['sync']['key'], cfg['api']['token']), ('K' * 40, 'T' * 30))

    def test_api_start_retries_a_failing_read_and_logs(self):
        self.configure()
        results = [(integration.default_config(), 'Die Datei integration.json lässt sich nicht lesen (Fehler).')] * 2
        real = integration.load_with_status
        count = {'n': 0}

        def flaky(root=None):
            count['n'] += 1
            return results[count['n'] - 1] if count['n'] <= 2 else real(root)
        with mock.patch.object(integration, 'load_with_status', flaky), mock.patch.object(api.time, 'sleep'), \
                self.assertLogs('api', 'WARNING') as logs:
            handle, problem = api.start_if_enabled(self.root, db_path=self.db_file)
        try:
            self.assertIsNone(problem)
            self.assertIsNotNone(handle, 'die Schnittstelle darf nicht stillschweigend ausbleiben')
        finally:
            if handle:
                handle.stop()
        self.assertTrue(any('Neuer Versuch' in m for m in logs.output))

    def test_api_start_gives_up_with_a_logged_reason(self):
        self.configure()
        bad = (integration.default_config(), 'Die Datei integration.json lässt sich nicht lesen (Fehler).')
        with mock.patch.object(integration, 'load_with_status', return_value=bad), mock.patch.object(api.time, 'sleep'), \
                self.assertLogs('api', 'WARNING') as logs:
            handle, problem = api.start_if_enabled(self.root, db_path=self.db_file)
        self.assertIsNone(handle)
        self.assertIn('nicht lesen', problem)
        self.assertTrue(any('Schnittstelle nicht gestartet' in m for m in logs.output))

    def test_concurrent_reading_and_replacing(self):
        self.configure()
        stop = threading.Event()
        problems = []

        def writer():
            i = 0
            while not stop.is_set():
                integration.update_sync_status(self.root, last_push=f'2026-01-01T00:00:{i % 60:02d}Z')
                i += 1

        def reader():
            for _ in range(1500):
                _cfg, problem = integration.load_with_status(self.root)
                if problem:
                    problems.append(problem)
        w = threading.Thread(target=writer)
        readers = [threading.Thread(target=reader) for _ in range(3)]
        w.start()
        [r.start() for r in readers]
        [r.join(60) for r in readers]
        stop.set()
        w.join(10)
        self.assertEqual(problems, [])
        self.assertEqual(integration.load(self.root)['sync']['key'], 'K' * 40)


# ---------- 11 ----------

class StartJobTests(unittest.TestCase):
    def fake_app(self, args):
        from socialscraper.ui import app as appmod
        calls = []
        fake = types.SimpleNamespace(
            steps=[('Abruf', args)], cancelled=False, results=[], events=None, job='ALT', current_label='',
            log=lambda text, tag='': calls.append((text, tag)), set_status=mock.Mock(), on_finish=None, finished=False)

        def finish():
            fake.finished = True
        fake._finish = finish
        return appmod, fake, calls

    def test_nul_in_a_link_resets_state_cleanly(self):
        appmod, fake, calls = self.fake_app(['scrape', 'https://www.tiktok.com/@a/video/1\x00'])
        appmod.App._next_step(fake)
        self.assertIsNone(fake.job, 'self.job darf nicht gesetzt bleiben (Knöpfe blieben sonst gesperrt)')
        self.assertTrue(fake.finished)
        self.assertEqual(fake.results[0][1], 1)
        self.assertTrue(any('konnte nicht gestartet werden' in c[0] for c in calls))

    def test_other_start_errors_are_contained_too(self):
        appmod, fake, calls = self.fake_app(['scrape', 'x'])
        with mock.patch.object(guicore.Job, 'start', side_effect=RuntimeError('kaputt')):
            appmod.App._next_step(fake)
        self.assertIsNone(fake.job)
        self.assertTrue(fake.finished)


if __name__ == '__main__':
    unittest.main()
