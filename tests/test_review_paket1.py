"""Regressionen zu den bestätigten Befunden des Cloud-Reviews (Paket 1). Alles offline, alles in Wegwerf-Ordnern."""
import asyncio
import contextlib
import importlib
import io
import logging
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('SS_PACE', 'off')  # keine Zufallspausen zwischen Abrufen in Tests

from socialscraper import cli, people_repo, repo, scheduler
from socialscraper import sentiment_repo as srepo
from socialscraper.adapters import youtube
from socialscraper.config import Settings
from socialscraper.models import Comment, Video
from socialscraper.net import cookies, errors
from socialscraper.net.cookies import CookieJar, parse_any
from socialscraper.net.proxy import Proxy
from socialscraper.net.retry import PermanentFetchError, with_retry
from socialscraper.net.session_guard import State
from socialscraper.store import Store

PROJECT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 1: credentials.example.json ist nicht ignoriert
class GitignoreTests(unittest.TestCase):
    def test_example_credentials_are_not_ignored(self):
        lines = [x.strip() for x in (PROJECT / '.gitignore').read_text(encoding='utf-8').splitlines()]
        self.assertIn('credentials*.json', lines)
        self.assertIn('!credentials.example.json', lines)
        self.assertGreater(lines.index('!credentials.example.json'), lines.index('credentials*.json'))
        self.assertTrue((PROJECT / 'credentials.example.json').is_file())

    def test_git_does_not_ignore_example_but_ignores_real_credentials(self):
        try:
            ignored = lambda name: subprocess.run(['git', 'check-ignore', '-q', name], cwd=PROJECT, capture_output=True).returncode == 0
            probe = subprocess.run(['git', 'rev-parse', '--git-dir'], cwd=PROJECT, capture_output=True)
        except OSError:
            self.skipTest('git nicht verfügbar')
        if probe.returncode != 0:
            self.skipTest('kein Git-Arbeitsverzeichnis')
        self.assertFalse(ignored('credentials.example.json'))
        self.assertTrue(ignored('credentials.json'))
        self.assertTrue(ignored('data/credentials.json'))


# ---------------------------------------------------------------- 3: Rate-Limit ist nie „dauerhaft“
RATE_LIMIT = ("ERROR: [youtube] aaaaaaaaaaa: Video unavailable. This content isn't available, try again later. "
              "The current session has been rate-limited by YouTube for up to an hour. It is recommended to use "
              "`-t sleep` to add a delay between video requests to avoid exceeding the rate limit.")
BOT_CHECK = ("ERROR: [youtube] aaaaaaaaaaa: Sign in to confirm you\u2019re not a bot. "
             "Use --cookies-from-browser or --cookies for the authentication.")
REALLY_UNAVAILABLE = 'ERROR: [youtube] aaaaaaaaaaa: Video unavailable. This video is no longer available'
YT = 'https://www.youtube.com/watch?v=aaaaaaaaaaa'


class YouTubeTransientTests(unittest.TestCase):
    def test_classification(self):
        self.assertFalse(youtube.is_permanent_error(RATE_LIMIT))
        self.assertFalse(youtube.is_permanent_error(BOT_CHECK))
        self.assertFalse(youtube.is_permanent_error('Video unavailable. Too many requests'))
        self.assertTrue(youtube.is_permanent_error(REALLY_UNAVAILABLE))
        self.assertTrue(youtube.is_permanent_error('ERROR: [youtube] x: Private video. Sign in if you have been granted access'))
        self.assertTrue(youtube.is_permanent_error('ERROR: Sign in to confirm your age. This video may be inappropriate'))

    def test_rate_limit_is_not_explained_as_private_video(self):
        self.assertNotIn('privat', errors.explain(Exception(RATE_LIMIT)))
        self.assertFalse(errors.is_hopeless(Exception(RATE_LIMIT)))

    def run_fetch(self, outcomes, mode='aus', proxies=()):
        calls = []

        class DownloadError(Exception):
            pass

        class FakeYDL:
            def __init__(self, options): self.options = options
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False, process=True):
                calls.append(dict(self.options))
                outcome = outcomes.pop(0)
                if isinstance(outcome, str):
                    raise DownloadError(outcome)
                return outcome
            def process_ie_result(self, result, download=False):
                return result

        module = types.ModuleType('yt_dlp')
        module.YoutubeDL = FakeYDL
        module.utils = types.SimpleNamespace(DownloadError=DownloadError)
        cfg = Settings(max_comments=0, impersonate='', proxy_mode=mode)
        queue = list(proxies)
        im = Mock(); im.pool = Mock(); im.pool.anonymous = True
        im.proxy_for.side_effect = lambda ident: queue[0] if queue else None
        im.pool.report_failure.side_effect = lambda proxy: queue.pop(0)
        ctx = SimpleNamespace(cfg=cfg, store=None, im=im, identity='anon_public', country='de', mobile=False)
        try:
            with patch.dict(sys.modules, {'yt_dlp': module}), \
                 patch.object(youtube, 'youtube_runtime_options', return_value={}), \
                 patch('socialscraper.net.errors.precheck_local_proxy'), \
                 patch('socialscraper.net.retry.time.sleep') as sleep:
                result = youtube.YouTubeAdapter().fetch(YT, ctx)
        except Exception as error:
            return error, calls, sleep, im
        return result, calls, sleep, im

    def test_rate_limit_is_retried_like_any_transient_error(self):
        for message in (RATE_LIMIT, BOT_CHECK):
            with self.subTest(message=message[:40]):
                error, calls, sleep, _ = self.run_fetch([message, message, message])
                self.assertNotIsInstance(error, PermanentFetchError)
                self.assertEqual(len(calls), 3)          # with_retry greift
                self.assertEqual(sleep.call_count, 2)

    def test_really_unavailable_video_stays_permanent(self):
        error, calls, sleep, _ = self.run_fetch([REALLY_UNAVAILABLE])
        self.assertIsInstance(error, PermanentFetchError)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_rate_limit_rotates_proxy_in_anonymous_mode(self):
        a, b = Proxy('http', '1.1.1.1', 80), Proxy('socks5', '2.2.2.2', 1080)
        a.anonymous = b.anonymous = True
        (video, comments), calls, sleep, im = self.run_fetch([RATE_LIMIT, {'id': 'aaaaaaaaaaa'}], mode='frei', proxies=[a, b])
        self.assertEqual(video.video_id, 'aaaaaaaaaaa')
        im.pool.report_failure.assert_called_once_with(a)   # Proxy gesperrt, nicht „dauerhaft“ abgebrochen
        self.assertEqual([c['proxy'] for c in calls], ['http://1.1.1.1:80', 'socks5h://2.2.2.2:1080'])


# ---------------------------------------------------------------- 4: Fremde kaputte Cookies brechen nichts ab
class ForeignCookieTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    GOOD = {'name': 'sessionid', 'value': 'abc', 'domain': '.tiktok.com'}

    def test_nameless_foreign_cookie_is_ignored(self):
        broken_foreign = [{'value': 'x', 'domain': '.google.com'}, {'name': '', 'domain': 'example.org'},
                          {'name': 'a;b', 'value': 'x', 'domain': '.instagram.com'}, 'kein Objekt?']
        out = CookieJar.for_platform([*broken_foreign[:3], self.GOOD], 'tiktok')
        self.assertEqual([c['name'] for c in out], ['sessionid'])
        self.assertTrue(CookieJar.authenticated([{'value': 'x', 'domain': '.google.com'}, self.GOOD], 'tiktok'))

    def test_invalid_own_cookie_is_skipped_and_counted_without_values(self):
        raw = [{'name': '', 'value': 'SECRET-VALUE', 'domain': '.tiktok.com'}, self.GOOD,
               {'name': 'x', 'value': 'y', 'domain': '.tiktok.com', 'expires': 'SECRET-DATE'}]
        with self.assertLogs(cookies.log, logging.WARNING) as logs:
            out = CookieJar.for_platform(raw, 'tiktok')
        self.assertEqual([c['name'] for c in out], ['sessionid'])
        text = ' '.join(logs.output)
        self.assertIn('2', text)
        self.assertNotIn('SECRET', text)

    def test_save_with_browser_list_containing_foreign_junk(self):
        CookieJar.save('alice', 'tiktok', [{'value': 'x', 'domain': '.google.com'}, self.GOOD], base_dir=self.tmp.name)
        self.assertEqual([c['name'] for c in CookieJar.load('alice', 'tiktok', base_dir=self.tmp.name)], ['sessionid'])

    def test_login_wait_for_auth_survives_foreign_junk(self):
        from socialscraper import login as login_module
        backend = Mock(); backend.read_cookies.return_value = [{'value': 'x', 'domain': '.google.com'}, self.GOOD]
        page = Mock(); page.evaluate.return_value = State.OK
        result = login_module._wait_for_auth(page, backend, None, 'tiktok', 5)
        self.assertEqual([c['name'] for c in result], ['sessionid'])

    def test_import_path_stays_strict(self):
        for text in ('[{"value":"x","domain":".google.com"}]', '[{"name":"","value":"x"}]'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_any(text)
        with self.assertRaises(ValueError):
            CookieJar.for_platform([{'value': 'x', 'domain': '.google.com'}], 'tiktok', strict=True)
        with self.assertRaises(ValueError):
            CookieJar.for_platform('keine Liste', 'tiktok')


# ---------------------------------------------------------------- 5: max_retries / retry_backoff wirken
class RetrySettingsTests(unittest.TestCase):
    def make(self, times=3, backoff=2.0):
        calls = []

        class Thing:
            @with_retry(times=times, backoff=backoff)
            def go(self, url, ctx):
                calls.append(url)
                raise RuntimeError('vorübergehend')
        return Thing(), calls

    def run_go(self, thing, ctx):
        with patch('socialscraper.net.retry.time.sleep') as sleep:
            with self.assertRaises(RuntimeError):
                thing.go('u', ctx)
        return sleep

    def test_max_retries_one_means_one_attempt_and_no_waiting(self):
        thing, calls = self.make()
        sleep = self.run_go(thing, SimpleNamespace(cfg=Settings(max_retries=1)))
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_defaults_keep_three_attempts_and_old_waits(self):
        thing, calls = self.make()
        sleep = self.run_go(thing, SimpleNamespace(cfg=Settings()))
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.0, 4.0])
        thing, calls = self.make(backoff=3.0)   # Instagram
        sleep = self.run_go(thing, SimpleNamespace(cfg=Settings()))
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [3.0, 9.0])

    def test_configured_values_are_used(self):
        thing, calls = self.make()
        sleep = self.run_go(thing, SimpleNamespace(cfg=Settings(max_retries=4, retry_backoff=4.0)))
        self.assertEqual(len(calls), 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [4.0, 8.0, 16.0])

    def test_ctx_as_keyword_and_without_settings(self):
        thing, calls = self.make()
        with patch('socialscraper.net.retry.time.sleep'):
            with self.assertRaises(RuntimeError):
                thing.go('u', ctx=SimpleNamespace(cfg=Settings(max_retries=2)))
        self.assertEqual(len(calls), 2)
        for ctx in (SimpleNamespace(), Mock(), SimpleNamespace(cfg=SimpleNamespace(max_retries=True, retry_backoff='x'))):
            thing, calls = self.make()
            self.run_go(thing, ctx)
            self.assertEqual(len(calls), 3)

    def test_adapter_fetch_honors_max_retries(self):
        class DownloadError(Exception):
            pass

        handle = Mock(); handle.__enter__ = Mock(return_value=handle); handle.__exit__ = Mock(return_value=False)
        handle.extract_info.side_effect = DownloadError('Unable to download webpage: timed out')
        module = types.SimpleNamespace(YoutubeDL=Mock(return_value=handle), utils=types.SimpleNamespace(DownloadError=DownloadError))
        ctx = SimpleNamespace(cfg=Settings(max_comments=0, impersonate='', max_retries=1), im=None, identity='default',
                              country='us', mobile=False)
        with patch.dict(sys.modules, {'yt_dlp': module}), \
             patch.object(youtube, 'youtube_runtime_options', return_value={}), \
             patch('socialscraper.net.retry.time.sleep') as sleep:
            with self.assertRaises(Exception):
                youtube.YouTubeAdapter().fetch(YT, ctx)
        self.assertEqual(handle.extract_info.call_count, 1)
        sleep.assert_not_called()


# ---------------------------------------------------------------- 6: Sitzungsverlust rastet ein (CLI + Planer)
class LostSessionTests(unittest.TestCase):
    URLS = [f'https://www.tiktok.com/@u/video/{i}' for i in range(1, 6)]

    @staticmethod
    def make_adapter(entries, alarms):
        """Wie TikTokAdapter._check_session: ab dem 2. Link ist die Sitzung weg; Alarm nur, wenn ctx sie noch für gültig hält."""
        adapter = Mock(platform='tiktok', needs_browser=True, needs_auth=True)

        def fetch(url, ctx):
            entries.append(ctx.authenticated)
            if len(entries) >= 2 and ctx.authenticated:
                ctx.authenticated = False
                alarms.append(url)          # entspricht invalidate() + notify() je Link
            return Video('tiktok', url.rsplit('/', 1)[-1], url, comment_count=0), []
        adapter.fetch.side_effect = fetch
        return adapter

    def test_cli_alarms_once_for_five_urls(self):
        entries, alarms = [], []
        adapter = self.make_adapter(entries, alarms)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=str(pathlib.Path(tmp) / 'scrape.db'), notify_file='')
            with patch.object(cli, 'load', return_value=cfg), patch.object(cli, 'resolve', return_value=adapter), \
                 patch.object(cli, '_ctx_and_backend', return_value=(Mock(), Mock(), Mock())), \
                 patch.object(cli, 'ensure', return_value=State.OK), contextlib.redirect_stdout(io.StringIO()):
                code = cli.main(['scrape', *self.URLS])
        self.assertEqual(code, 0)
        self.assertEqual(len(entries), 5)
        self.assertEqual(len(alarms), 1)
        self.assertEqual(entries, [True, True, False, False, False])

    def test_cli_profile_discovery_loss_is_kept(self):
        entries, alarms = [], []
        adapter = self.make_adapter(entries, alarms)
        entries.append('vorher')   # zählt den Aufruf als „nicht der erste“: Alarm schon beim ersten Link

        def profile_urls(self_, username, ctx, limit=20):
            ctx.authenticated = False        # Verlust schon bei der Profilsuche
            return self_urls
        self_urls = self.URLS[:2]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=str(pathlib.Path(tmp) / 'scrape.db'), notify_file='')
            with patch.object(cli, 'load', return_value=cfg), patch.object(cli, 'resolve', return_value=adapter), \
                 patch.object(cli, '_ctx_and_backend', return_value=(Mock(), Mock(), Mock())), \
                 patch.object(cli, 'ensure', return_value=State.OK), \
                 patch('socialscraper.adapters.tiktok.TikTokAdapter.profile_urls', profile_urls), \
                 contextlib.redirect_stdout(io.StringIO()):
                cli.main(['profile', 'u', '--max-comments', '0'])
        self.assertEqual(entries[1:], [False, False])
        self.assertEqual(alarms, [])


class LostSessionSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='', profiles_dir=str(self.root / 'p'))
        self.store = Store(self.cfg.db_path); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.entries, self.alarms = [], []
        self.adapter = LostSessionTests.make_adapter(self.entries, self.alarms)
        for target, kwargs in (
                (scheduler.cli, dict(make_runtime=Mock(return_value=(Mock(), Mock(), Mock())))),
                (scheduler.cli, dict(shutdown_runtime=Mock())),
                (scheduler, dict(resolve=lambda url: self.adapter))):
            p = patch.multiple(target, **kwargs); p.start(); self.addCleanup(p.stop)
        p = patch.object(scheduler.cli, 'auth_state', side_effect=lambda *a: (True, State.OK)); p.start(); self.addCleanup(p.stop)

    def test_scheduler_alarms_once_for_five_urls(self):
        t = repo.add_target(self.db, 'hashtag', 'tiktok', 'cats', max_comments=0)
        with patch.object(scheduler.discovery, 'discover', return_value=LostSessionTests.URLS):
            r = scheduler.run_target(self.root, t['id'], 'daemon', cfg=self.cfg)
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(self.entries, [True, True, False, False, False])
        self.assertEqual(len(self.alarms), 1)

    def test_scheduler_loss_during_discovery_is_kept(self):
        t = repo.add_target(self.db, 'hashtag', 'tiktok', 'cats', max_comments=0)

        def discover(kind, platform, value, ctx, limit):
            ctx.authenticated = False
            return LostSessionTests.URLS[:2]
        self.entries.append('vorher')
        with patch.object(scheduler.discovery, 'discover', discover):
            scheduler.run_target(self.root, t['id'], 'daemon', cfg=self.cfg)
        self.assertEqual(self.entries[1:], [False, False])
        self.assertEqual(self.alarms, [])


# ---------------------------------------------------------------- 7: gemischte Zeitformate
class MixedTimeFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(str(pathlib.Path(self.tmp.name) / 'scrape.db')); self.addCleanup(self.store.close)
        self.db = self.store.db
        self.store.save_result(Video('tiktok', 'v1', 'https://www.tiktok.com/@a/video/v1'), [
            Comment('tiktok', 'v1', 'c_old', 'alt', author='ann'),
            Comment('tiktok', 'v1', 'c_mig', 'migriert, gleicher Tag', author='bob'),
            Comment('tiktok', 'v1', 'c_new', 'neu, gleicher Tag', author='cid'),
            Comment('tiktok', 'v1', 'c_early', 'früh', author='dan'),
        ], None)
        times = {'c_old': '2026-10-10 23:59:59', 'c_mig': '2026-10-11 08:00:00',
                 'c_new': '2026-10-11T09:30:00Z', 'c_early': '2026-10-11T07:00:00Z'}
        for cid, stamp in times.items():
            self.db.execute('UPDATE comments SET first_seen_at=? WHERE comment_id=?', (stamp, cid))
        self.db.commit()

    def ids(self, since):
        return [r['comment_id'] for r in repo.new_comments_since(self.db, since)]

    def test_same_day_comments_of_both_formats_are_found(self):
        self.assertEqual(self.ids('2026-10-11T08:00:00Z'), ['c_new', 'c_mig'])   # neueste zuerst; c_mig genau auf der Grenze
        self.assertEqual(self.ids('2026-10-11T00:00:00Z'), ['c_new', 'c_mig', 'c_early'])

    def test_space_separated_cut_also_works(self):
        self.assertEqual(self.ids('2026-10-11 08:00:00'), ['c_new', 'c_mig'])   # 07:00 darf nicht durchrutschen

    def test_first_seen_of_author_is_the_true_earliest(self):
        # Gleicher Autor in beiden Formaten: rohes MIN() würde den späteren migrierten Wert nehmen (Leerzeichen < 'T').
        self.db.execute("UPDATE comments SET author='zoe' WHERE comment_id IN ('c_early','c_mig')")
        self.db.commit()
        first = people_repo.first_seen_map(self.db, 'commenter', [('tiktok', 'zoe')])
        self.assertEqual(first[('tiktok', 'zoe')][:19], '2026-10-11T07:00:00')
        rows = {r['comment_id']: r for r in repo.new_comments_since(self.db, '2026-10-11T08:00:00Z')}
        self.assertFalse(rows['c_mig']['author_new'])     # zoe war um 07:00 schon da
        self.assertTrue(rows['c_new']['author_new'])

    def test_api_stats_last_fetched_uses_normalized_max(self):
        from socialscraper import api
        self.db.execute("UPDATE videos SET fetched_at='2026-10-11 23:00:00'")
        self.store.save_result(Video('tiktok', 'v2', 'https://www.tiktok.com/@a/video/v2'), [], None)
        self.db.execute("UPDATE videos SET fetched_at='2026-10-11T01:00:00Z' WHERE video_id='v2'")
        self.db.commit()
        last = self.db.execute(f"SELECT MAX({api.sql_time('fetched_at')}) FROM videos").fetchone()[0]
        self.assertEqual(last, '2026-10-11T23:00:00')
        envelope = api.ep_stats(self.db, {}, [])
        self.assertEqual(envelope['last_fetched_at'], '2026-10-11T23:00:00Z')


# ---------------------------------------------------------------- 8: IPv6-Proxy in Klammern
class IPv6ProxyTests(unittest.TestCase):
    def test_nodriver_proxy_argument_brackets_ipv6(self):
        from socialscraper.net.fingerprint import build
        uc = types.ModuleType('nodriver')
        with patch.dict(sys.modules, {'nodriver': uc}):
            engine = importlib.import_module('socialscraper.backends.nodriver_engine')
            command = lambda name: (lambda **kwargs: (name, kwargs))
            uc.cdp = SimpleNamespace(
                emulation=SimpleNamespace(set_timezone_override=command('t'), set_locale_override=command('l'),
                                          set_geolocation_override=command('g')),
                browser=SimpleNamespace(grant_permissions=command('p'), PermissionType=SimpleNamespace(GEOLOCATION='geolocation')))
            tab = SimpleNamespace(send=AsyncMock())
            uc.start = AsyncMock(return_value=SimpleNamespace(get=AsyncMock(return_value=tab)))

            class Runner:
                def run(self, coroutine, timeout=180):
                    return asyncio.run(coroutine)
            backend = object.__new__(engine.NodriverBackend)
            backend.runner = Runner()
            backend.profiles = pathlib.Path(tempfile.gettempdir()) / 'ss-review-r1-profiles'
            backend._apply_cookies = Mock()
            identity = SimpleNamespace(name='ipv6_regression', fp=build('ipv6_regression', 'de'))
            with patch.object(engine, 'uc', uc):
                for raw, expected in (('http://[2001:db8::1]:8080', '--proxy-server=http://[2001:db8::1]:8080'),
                                      ('socks5://203.0.113.5:1080', '--proxy-server=socks5://203.0.113.5:1080')):
                    backend.open(identity, Proxy.parse(raw), headless=True, platform='tiktok')
                    self.assertIn(expected, uc.start.call_args.kwargs['browser_args'])

    def test_freeproxy_keys_use_bracketed_ipv6(self):
        proxy = Proxy.parse('http://[2001:db8::1]:8080')
        self.assertEqual(proxy.server, 'http://[2001:db8::1]:8080')
        self.assertEqual(Proxy.parse(proxy.server).host, '2001:db8::1')


# ---------------------------------------------------------------- 9: reset() braucht ss_ak
class SentimentResetTests(unittest.TestCase):
    def test_reset_with_people_scope_on_fresh_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / 'scrape.db')
            store = Store(path)
            store.save_result(Video('tiktok', 'v1', 'https://www.tiktok.com/@a/video/v1'),
                              [Comment('tiktok', 'v1', 'c1', 'hallo', author='Ann')], None)
            store.close()
            db = sqlite3.connect(path)            # frische Verbindung: ss_ak ist hier noch nicht angemeldet
            try:
                db.row_factory = sqlite3.Row
                self.assertEqual(srepo.reset(db, {'people': [('tiktok', 'ann')]}), 0)
                self.assertEqual(srepo.reset(db, {'commenter_group': 1}), 0)
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
