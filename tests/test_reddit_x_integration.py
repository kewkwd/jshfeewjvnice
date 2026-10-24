"""Reddit und X im ganzen Programm: Planer, Befehle, Anmeldung, Sitzungsprüfung, Daten, Schnittstelle, Export und Fenster.

Alles mit Wegwerf-Ordnern und simulierten Seiten; kein echtes Netz, keine echten Konten, keine Zugangsdaten.
"""
import csv
import gc
import json
import os
import pathlib
import re
import sys
import tempfile
import unittest
import urllib.request
from contextlib import contextmanager
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import api, autologic as al, cli, discovery, guicore as core, integration, login, repo, scheduler
from socialscraper.adapters import reddit as reddit_mod, x as x_mod
from socialscraper.adapters.base import ScrapeContext
from socialscraper.adapters.reddit import RedditAdapter
from socialscraper.adapters.x import XAdapter
from socialscraper.config import Settings
from socialscraper.models import Comment, Video
from socialscraper.net import proxymode, session_guard
from socialscraper.net.session_guard import SessionExpired, State
from socialscraper.store import Store
from socialscraper.ui import fetch_feedback, logic, plain_log
import test_reddit_adapter as reddit_tests
import test_x_adapter as x_tests

os.environ.setdefault('SS_PACE', 'off')
FIXTURES = pathlib.Path(__file__).parent / 'fixtures'


# ---------------------------------------------------------------- Planer

class PlannerBase(unittest.TestCase):
    platform = 'reddit'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='', profiles_dir=str(self.root / 'p'))
        self.store = Store(self.cfg.db_path); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        needs_auth = self.platform == 'x'
        self.adapter = Mock(platform=self.platform, needs_browser=True, needs_auth=needs_auth)
        self.adapter.fetch.side_effect = lambda url, ctx: (Video(self.platform, re.search(r'(?:comments|status)/([^/?]+)', url).group(1), url, comment_count=0), [])
        self.state = (False, None if self.platform == 'reddit' else State.MISSING)
        for target, kwargs in ((scheduler.cli, dict(make_runtime=Mock(return_value=(Mock(), Mock(), Mock())))),
                               (scheduler.cli, dict(shutdown_runtime=Mock())),
                               (scheduler, dict(resolve=lambda url: self.adapter))):
            p = patch.multiple(target, **kwargs); p.start(); self.addCleanup(p.stop)
        p = patch.object(scheduler.cli, 'auth_state', side_effect=lambda *a: self.state); self.auth = p.start(); self.addCleanup(p.stop)

    def run_target(self, tid, **kw):
        return scheduler.run_target(self.root, tid, 'daemon', cfg=kw.pop('cfg', self.cfg))


class RedditPlannerTests(PlannerBase):
    platform = 'reddit'
    URL = 'https://www.reddit.com/r/python/comments/abc123/titel/'

    def test_video_target_runs_without_any_account_login(self):
        t = repo.add_target(self.db, 'video', 'reddit', self.URL, max_comments=30)
        r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['videos_seen']), ('ok', 1))
        ctx = self.adapter.fetch.call_args.args[1]
        self.assertEqual((ctx.identity, ctx.country, ctx.authenticated, ctx.cfg.max_comments), ('reddit_main', 'de', False, 30))

    def test_works_even_when_the_account_entry_is_missing(self):
        self.db.execute('DELETE FROM accounts'); self.db.commit()
        t = repo.add_target(self.db, 'video', 'reddit', self.URL)
        with patch.object(repo, 'ensure_default_accounts'):
            r = self.run_target(t['id'])
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(self.adapter.fetch.call_args.args[1].identity, 'reddit_main')

    def test_chosen_account_is_used(self):
        acc = repo.add_account(self.db, 'reddit', 'Zweitkonto', country='at')
        t = repo.add_target(self.db, 'video', 'reddit', self.URL, account_id=acc['id'])
        self.run_target(t['id'])
        ctx = self.adapter.fetch.call_args.args[1]
        self.assertEqual((ctx.identity, ctx.country), ('reddit_zweitkonto', 'at'))

    def test_subreddit_target_discovers_and_assigns_group(self):
        g = repo.create_group(self.db, 'Python')
        t = repo.add_target(self.db, 'search', 'reddit', 'r/python', group_id=g['id'], max_videos=4, max_comments=0)
        urls = ['https://www.reddit.com/r/python/comments/p1/a/', 'https://www.reddit.com/r/python/comments/p2/b/']
        with patch.object(scheduler.discovery, 'discover', return_value=urls) as d:
            r = self.run_target(t['id'])
        self.assertEqual(d.call_args.args[:3], ('search', 'reddit', 'r/python'))
        self.assertEqual((r['status'], r['videos_new']), ('ok', 2))
        self.assertEqual({row[0] for row in self.db.execute('SELECT video_uid FROM group_videos')}, {'reddit:p1', 'reddit:p2'})

    def test_block_message_is_reported_clearly(self):
        self.adapter.fetch.side_effect = reddit_mod.PermanentFetchError(reddit_mod.MSG_BLOCKED)
        t = repo.add_target(self.db, 'video', 'reddit', self.URL)
        r = self.run_target(t['id'])
        self.assertEqual(r['status'], 'failed')
        self.assertIn('Sicherheitsprüfung gesperrt', r['error'])
        self.assertIn('keine eindeutige Ursache', r['error'])
        self.assertNotIn('Traceback', r['error'])

    def test_reddit_is_allowed_with_tor_and_free_proxies(self):
        t = repo.add_target(self.db, 'video', 'reddit', self.URL, max_comments=10)
        for mode in ('tor', 'frei'):
            cfg = Settings(db_path=self.cfg.db_path, notify_file='', profiles_dir=self.cfg.profiles_dir, proxy_mode=mode)
            r = self.run_target(t['id'], cfg=cfg)
            self.assertEqual(r['status'], 'ok', mode)
            ctx = self.adapter.fetch.call_args.args[1]
            self.assertEqual(ctx.identity, 'anon_public')                              # keine Konten mit Tor/freien Proxys


class XPlannerTests(PlannerBase):
    platform = 'x'
    URL = 'https://x.com/beispiel/status/123'

    def test_missing_login_fails_even_without_comments(self):
        t = repo.add_target(self.db, 'video', 'x', self.URL, max_comments=0)
        r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['error']), ('failed', 'Anmeldung fehlt für Konto „Hauptkonto“. Bitte unter „Konten“ anmelden.'))
        self.adapter.fetch.assert_not_called()

    def test_discovery_needs_login_and_names_the_chosen_account(self):
        acc = repo.add_account(self.db, 'x', 'Zweit X', country='at')
        for kind, value in (('search', 'lofi'), ('hashtag', 'tanzen'), ('profile', 'beispiel')):
            t = repo.add_target(self.db, kind, 'x', value, account_id=acc['id'])
            self.state = (False, State.EXPIRED)
            with patch.object(scheduler.discovery, 'discover') as d:
                r = self.run_target(t['id'])
            self.assertEqual(r['status'], 'failed', kind)
            self.assertIn('Anmeldung abgelaufen für Konto „Zweit X“', r['error'])
            d.assert_not_called()
        self.assertEqual(self.auth.call_args.args[2:4], ('x_zweit_x', 'at'))

    def test_logged_in_account_fetches_with_authenticated_context(self):
        t = repo.add_target(self.db, 'video', 'x', self.URL, max_comments=20)
        self.state = (True, State.OK)
        r = self.run_target(t['id'])
        self.assertEqual(r['status'], 'ok')
        ctx = self.adapter.fetch.call_args.args[1]
        self.assertEqual((ctx.identity, ctx.country, ctx.authenticated), ('x_main', 'de', True))

    def test_logged_in_discovery_passes_through(self):
        t = repo.add_target(self.db, 'hashtag', 'x', 'tanzen', max_videos=2)
        self.state = (True, State.OK)
        with patch.object(scheduler.discovery, 'discover', return_value=['https://x.com/a/status/5']) as d:
            r = self.run_target(t['id'])
        self.assertEqual((r['status'], r['videos_new']), ('ok', 1))
        self.assertEqual(d.call_args.args[:3], ('hashtag', 'x', 'tanzen'))
        self.assertTrue(d.call_args.args[3].authenticated)

    def test_login_lost_during_the_run_stops_with_message(self):
        t = repo.add_target(self.db, 'video', 'x', self.URL)
        self.state = (True, State.OK)
        self.adapter.fetch.side_effect = SessionExpired('weg')
        r = self.run_target(t['id'])
        self.assertEqual(r['status'], 'failed'); self.assertIn('Anmeldung abgelaufen', r['error'])

    def test_free_proxies_and_tor_refuse_x(self):
        t = repo.add_target(self.db, 'video', 'x', self.URL)
        for mode in ('tor', 'frei'):
            cfg = Settings(db_path=self.cfg.db_path, notify_file='', profiles_dir=self.cfg.profiles_dir, proxy_mode=mode)
            r = self.run_target(t['id'], cfg=cfg)
            self.assertEqual(r['status'], 'failed'); self.assertIn('X braucht immer ein Konto', r['error'])
        self.adapter.fetch.assert_not_called(); self.auth.assert_not_called()


# ---------------------------------------------------------------- Befehle und Anmeldung

class CommandTests(unittest.TestCase):
    def test_parser_knows_the_platforms(self):
        ap = cli.parser()
        for platform in ('reddit', 'x'):
            for command in ('login', 'check', 'logout'):
                args = ap.parse_args([command, '--platform', platform, '--identity', f'{platform}_main'])
                self.assertEqual(args.platform, platform)
        args = ap.parse_args(['discover', 'search', 'r/python', '--platform', 'reddit'])
        self.assertEqual((args.platform, args.value), ('reddit', 'r/python'))
        ap.parse_args(['discover', 'hashtag', 'tanzen', '--platform', 'x'])
        with self.assertRaises(SystemExit):
            with patch('sys.stderr'):
                ap.parse_args(['login', '--platform', 'myspace'])

    def test_discover_command_refuses_nothing_for_reddit_but_checks_value(self):
        from socialscraper.commands import discover as command
        args = cli.parser().parse_args(['discover', 'profile', 'ab', '--platform', 'reddit'])
        with self.assertRaisesRegex(ValueError, 'Reddit-Benutzername'):
            command.run(args, Settings())

    def test_discover_command_refuses_x_in_anonymous_modes(self):
        from socialscraper.commands import discover as command
        args = cli.parser().parse_args(['discover', 'search', 'lofi', '--platform', 'x'])
        with self.assertRaises(proxymode.AccountsForbidden):
            command.run(args, Settings(proxy_mode='tor'))

    def test_scrape_commands_parse(self):
        ap = cli.parser()
        for platform, link in (('reddit', 'https://www.reddit.com/r/a/comments/abcdef/'), ('x', 'https://x.com/a/status/12')):
            args = ap.parse_args(core.scrape_command(platform, [link], 5, require_auth=platform == 'x'))
            self.assertEqual(args.urls, [link])
        self.assertTrue(ap.parse_args(core.scrape_command('x', ['https://x.com/a/status/12'], 5, require_auth=True)).require_auth)

    def test_auth_state_of_reddit_never_blocks_and_x_needs_session(self):
        cfg = Settings()
        self.assertEqual(cli.auth_state(RedditAdapter(), cfg, 'reddit_main', 'de', False, Mock(), Mock(pool=None)), (False, None))
        with patch.object(cli, 'ensure', return_value=State.MISSING) as ensure:
            self.assertEqual(cli.auth_state(XAdapter(), cfg, 'x_main', 'de', False, Mock(), Mock(pool=None)), (False, State.MISSING))
            self.assertEqual(ensure.call_args.args[3], 'x')
            cfg.require_auth = True
            with self.assertRaises(SessionExpired):
                cli.auth_state(XAdapter(), cfg, 'x_main', 'de', False, Mock(), Mock(pool=None))

    def test_unknown_link_message_names_all_platforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = []
            with patch.dict(os.environ, {'SS_DB_PATH': str(pathlib.Path(tmp) / 'x.db')}), patch('builtins.print', side_effect=lambda *a, **k: out.append(' '.join(map(str, a)))):
                with patch.dict(os.environ, {'SS_PROXY_MODE': 'aus'}):
                    cli.main(['scrape', 'https://example.org/video'])
            text = '\n'.join(out)
            self.assertIn('Reddit oder X', text)


class LoginRuleTests(unittest.TestCase):
    def test_login_is_manual_only_for_reddit_and_x(self):
        for platform in ('reddit', 'x'):
            with self.assertRaisesRegex(login.LoginError, 'selbst an'):
                login.login(Mock(), Mock(pool=None), f'{platform}_main', platform, mode='password', cfg=Settings())
            for mode in ('auto', 'qr', 'manual'):
                with patch.object(login, '_qr_flow', return_value=['c']) as qr, patch.object(login, '_password_flow') as pw, \
                        patch.object(login, 'get_creds') as creds:
                    login.login(Mock(), Mock(pool=None), f'{platform}_main', platform, mode=mode, cfg=Settings())
                qr.assert_called_once(); pw.assert_not_called(); creds.assert_not_called()      # auch mit hinterlegten Zugangsdaten: von Hand

    def test_tiktok_and_instagram_keep_password_mode(self):
        with patch.object(login, '_password_flow', return_value=['c']) as pw, patch.object(login, 'get_creds'):
            login.login(Mock(), Mock(pool=None), 'tiktok_main', 'tiktok', mode='auto', cfg=Settings())
        pw.assert_called_once()

    def test_login_refused_with_free_proxies_and_tor(self):
        for platform in ('reddit', 'x'):
            with self.assertRaises(proxymode.AccountsForbidden):
                login.login(Mock(), Mock(pool=None), f'{platform}_main', platform, mode='qr', cfg=Settings(proxy_mode='tor'))

    def test_urls_selectors_and_dismiss_texts(self):
        self.assertEqual(login.LOGIN_URL['x'], 'https://x.com/i/flow/login')
        self.assertTrue(login.LOGIN_URL['reddit'].startswith('https://www.reddit.com/login'))
        self.assertEqual(login.EMAIL_URL['x'], login.LOGIN_URL['x'])
        for platform in ('reddit', 'x'):
            self.assertTrue(login.SEL[platform]['user'] and login.SEL[platform]['pass'] and login.SEL[platform]['submit'])
        for platform, texts in login.DISMISS.items():
            for text in texts:
                for accepting in ('accept', 'allow all', 'alle akzeptieren', 'zustimmen', 'einverstanden', 'agree'):
                    self.assertNotIn(accepting, text, (platform, text))        # es wird nur abgelehnt, nie zugestimmt
            self.assertIn('nur erforderliche cookies erlauben', texts)
        self.assertIn('nicht notwendige ablehnen', login.DISMISS['reddit']); self.assertIn('nicht notwendige ablehnen', login.DISMISS['x'])

    def test_qr_flow_opens_the_platform_login_page_and_saves_cookies(self):
        class Page:
            def __init__(self): self.urls = []
            def goto(self, url, **kw): self.urls.append(url)
            def wait_for_timeout(self, ms): pass

        class Backend:
            def __init__(self, cookies): self.cookies, self.page_obj = cookies, Page()
            @contextmanager
            def session(self, *a, **kw): yield 'handle'
            def page(self, handle): return self.page_obj
            def read_cookies(self, handle): return self.cookies

        im = Mock(pool=None); im.get.return_value = Mock(name='x'); im.proxy_for.return_value = None
        cookies = [{'name': 'auth_token', 'value': 'v', 'domain': '.x.com', 'path': '/', 'secure': True},
                   {'name': 'ct0', 'value': 'w', 'domain': '.x.com', 'path': '/', 'secure': True}]
        backend = Backend(cookies)
        with tempfile.TemporaryDirectory() as tmp, patch('socialscraper.net.cookies.BASE', pathlib.Path(tmp)), \
                patch.object(login, 'read_state', return_value=State.OK), patch.object(login, 'notify'):
            login.login(backend, im, 'x_main', 'x', cfg=Settings())
            self.assertTrue(pathlib.Path(tmp, 'x_main.x.json').is_file())
        self.assertEqual(backend.page_obj.urls, ['https://x.com/i/flow/login'])


class SessionProbeTests(unittest.TestCase):
    def test_probe_scripts_and_home_urls_exist(self):
        for platform in ('reddit', 'x'):
            self.assertIn(platform, session_guard.PROBE_JS)
        self.assertEqual(session_guard.HOME_URL['reddit'], 'https://www.reddit.com/')
        self.assertEqual(session_guard.HOME_URL['x'], 'https://x.com/home')

    def test_read_state_uses_the_platform_script(self):
        class Page:
            def __init__(self, answers): self.answers = list(answers); self.scripts = []
            def evaluate(self, script):
                self.scripts.append(script); return self.answers.pop(0)
            def wait_for_timeout(self, ms): pass
        page = Page(['error', 'ok'])
        self.assertEqual(session_guard.read_state(page, 'x', attempts=3, interval_ms=1), 'ok')
        self.assertEqual(set(page.scripts), {session_guard.PROBE_JS['x']})
        self.assertEqual(session_guard.read_state(Page(['expired']), 'reddit'), 'expired')
        self.assertEqual(session_guard.read_state(Page(['error'] * 3), 'reddit', attempts=3, interval_ms=1), 'error')

    def test_ensure_without_cookies_does_not_launch_a_browser(self):
        backend = Mock(); im = Mock(pool=None)
        with tempfile.TemporaryDirectory() as tmp, patch('socialscraper.net.cookies.BASE', pathlib.Path(tmp)), patch.object(session_guard, 'notify'):
            for platform in ('reddit', 'x'):
                self.assertEqual(session_guard.ensure(backend, im, f'{platform}_main', platform), State.MISSING)
        backend.session.assert_not_called()

    def test_ensure_with_cookies_probes_the_home_page(self):
        class Page:
            def __init__(self): self.urls = []
            def goto(self, url, **kw): self.urls.append(url)
            def wait_for_timeout(self, ms): pass

        class Backend:
            def __init__(self): self.page_obj = Page()
            @contextmanager
            def session(self, *a, **kw): yield 'handle'
            def page(self, handle): return self.page_obj
        im = Mock(pool=None); im.get.return_value = Mock(); im.proxy_for.return_value = None
        cookies = {'reddit': [{'name': 'reddit_session', 'value': 'v', 'domain': '.reddit.com'}],
                   'x': [{'name': 'auth_token', 'value': 'v', 'domain': '.x.com'}, {'name': 'ct0', 'value': 'w', 'domain': '.x.com'}]}
        with tempfile.TemporaryDirectory() as tmp, patch('socialscraper.net.cookies.BASE', pathlib.Path(tmp)), \
                patch.object(session_guard, 'notify'), patch.object(session_guard, 'read_state', return_value=State.OK):
            for platform, jar in cookies.items():
                backend = Backend()
                session_guard.CookieJar.save(f'{platform}_main', platform, jar)
                self.assertEqual(session_guard.ensure(backend, im, f'{platform}_main', platform, force=True), State.OK)
                self.assertEqual(backend.page_obj.urls, [session_guard.HOME_URL[platform]])

    def test_x_with_only_one_cookie_counts_as_missing(self):
        im = Mock(pool=None)
        with tempfile.TemporaryDirectory() as tmp, patch('socialscraper.net.cookies.BASE', pathlib.Path(tmp)), patch.object(session_guard, 'notify'):
            session_guard.CookieJar.save('x_main', 'x', [{'name': 'auth_token', 'value': 'v', 'domain': '.x.com'}])
            self.assertEqual(session_guard.ensure(Mock(), im, 'x_main', 'x'), State.MISSING)


# ---------------------------------------------------------------- Daten, Schnittstelle, Export

class DataBase(unittest.TestCase):
    """Ergebnisse der echten Adapter (mit simulierten Seiten) in einer Wegwerf-Datenbank."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.db = cls.root / 'data' / 'scrape.db'
        with patch('socialscraper.net.retry.time.sleep'), patch('socialscraper.adapters.x.humanize.scroll'):
            ctx = ScrapeContext(cfg=Settings(max_comments=50), store=None, im=None, sessions=reddit_tests.FakePool(reddit_tests.FakePage()))
            cls.reddit = RedditAdapter().fetch(reddit_tests.POST_URL, ctx)
            page = x_tests.FakePage([[x_tests.detail()], [x_tests.detail('x_tweetdetail_more.json')]])
            ctx = ScrapeContext(cfg=Settings(max_comments=50), store=None, im=None, sessions=x_tests.FakePool(page),
                                identity='x_main', country='de', authenticated=True)
            cls.x = XAdapter().fetch(x_tests.URL, ctx)
        with Store(str(cls.db)) as store:
            for result in (cls.reddit, cls.x):
                store.save_result(result.video, result.comments, '2026-10-08T10:00:00.000000+00:00')
            store.save_result(Video('tiktok', 't1', 'https://www.tiktok.com/@a/video/t1', author='anna', caption='TikTok', view_count=5),
                              [Comment('tiktok', 't1', 'tc1', 'Hallo', author='bob')], '2026-10-08T10:00:00.000000+00:00')
            repo.ensure_default_accounts(store.db)
            group = repo.create_group(store.db, 'Gemischt')
            repo.add_videos_to_group(store.db, group['id'], ['reddit:abc123', 'x:1000'])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


class StoredDataTests(DataBase):
    def test_both_platforms_land_in_the_unchanged_schema(self):
        with Store(str(self.db)) as store:
            rows = {r[0]: r for r in store.db.execute('SELECT uid, platform, author, view_count, like_count, comment_count, share_count FROM videos')}
            self.assertEqual(rows['reddit:abc123'][1:], ('reddit', 'beispiel_op', None, 1520, 9, None))
            self.assertEqual(rows['x:1000'][1:], ('x', 'beispiel_konto', 12345, 250, 6, 34))
            counts = dict(store.db.execute('SELECT platform, COUNT(*) FROM comments GROUP BY platform').fetchall())
            self.assertEqual(counts, {'reddit': 7, 'x': 6, 'tiktok': 1})
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM comments WHERE uid IN ('reddit:c1','x:1001')").fetchone()[0], 2)

    def test_saving_again_adds_no_duplicates(self):
        with Store(str(self.db)) as store:
            new_video, new_comments = store.save_result(self.reddit.video, self.reddit.comments, '2026-10-09T10:00:00.000000+00:00')
            self.assertEqual((new_video, new_comments), (False, 0))

    def test_stats_by_platform(self):
        with Store(str(self.db)) as store:
            stats = store.stats()
        self.assertEqual(stats['videos'], {'reddit': 1, 'x': 1, 'tiktok': 1})

    def test_data_tab_logic_filters_by_platform(self):
        rows = logic.query_videos(self.db, '', 'reddit'); self.assertEqual([r['uid'] for r in rows], ['reddit:abc123'])
        rows = logic.query_videos(self.db, '', 'x'); self.assertEqual([r['uid'] for r in rows], ['x:1000'])
        self.assertEqual(len(logic.query_videos(self.db, '', '')), 3)
        self.assertEqual([r[0] for r in core.list_videos(self.db, '', 'x')], ['x:1000'])
        self.assertEqual(len(core.list_comments(self.db, 'reddit', 'abc123')), 7)
        self.assertEqual({r['platform'] for r in logic.query_comments(self.db, 'x', '1000')}, {'x'})

    def test_people_are_listed_per_platform(self):
        rows, total = logic.people_list(self.db, 'commenter', platform='x')
        names = {p['display_name'] for p in rows}
        self.assertIn('antwort_eins', names); self.assertNotIn('user_a', names)
        self.assertEqual(total, len(rows))
        names = {p['display_name'] for p in logic.people_list(self.db, 'commenter', platform='reddit')[0]}
        self.assertIn('user_a', names); self.assertNotIn('[deleted]', names)

    def test_export_has_readable_platform_names(self):
        with tempfile.TemporaryDirectory() as out:
            files = core.export_data(self.db, out, 'csv')
            by_name = {pathlib.Path(f).name: pathlib.Path(f) for f in files}
            with open(by_name['videos.csv'], encoding='utf-8-sig', newline='') as fh:
                platforms = {row['Plattform'] for row in csv.DictReader(fh)}
            self.assertEqual(platforms, {'Reddit', 'X', 'TikTok'})
            with open(by_name['kommentare.csv'], encoding='utf-8-sig', newline='') as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual({r['Plattform'] for r in rows}, {'Reddit', 'X', 'TikTok'})
            files = core.export_data(self.db, out, 'jsonl', group_id=None)
            videos = [json.loads(line) for line in pathlib.Path(out, 'videos.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual({v['platform'] for v in videos}, {'reddit', 'x', 'tiktok'})

    def test_export_by_group_keeps_only_group_videos(self):
        with tempfile.TemporaryDirectory() as out, Store(str(self.db)) as store:
            gid = repo.list_groups(store.db)[0]['id']
            core.export_data(self.db, out, 'jsonl', group_id=gid)
            videos = [json.loads(line) for line in pathlib.Path(out, 'videos.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual({v['platform'] for v in videos}, {'reddit', 'x'})

    def test_live_log_line_names_the_platform(self):
        line = '[SS_FETCH] ' + json.dumps({'version': 1, 'stage': 'result', 'platform': 'reddit', 'url': 'u', 'uid': 'reddit:abc123',
                                          'video_saved': True, 'comments_saved': 7, 'comments_status': 'received', 'success': True, 'message': ''})
        self.assertIn('Reddit abc123: Video gespeichert', core.display_line(line))
        self.assertEqual(plain_log.PlainLog().format('12:00:00 INFO    cli | x -> 5 Kommentare')[0] is not None, True)
        formatted = plain_log.PlainLog().format('12:00:00 INFO    socialscraper.adapters.reddit | reddit abc123: 7 Kommentare')[0]
        self.assertEqual(formatted, 'Reddit: 7 Kommentare wurden geladen.')

    def test_feedback_texts(self):
        event = {'version': 1, 'stage': 'result', 'platform': 'x', 'url': 'https://x.com/a/status/1', 'uid': 'x:1', 'video_saved': True,
                 'comments_saved': 0, 'comments_status': 'unavailable', 'success': True, 'message': x_mod.MSG_NO_REPLIES}
        text = fetch_feedback.describe(event)
        self.assertTrue(text.startswith('X · 1: ')); self.assertIn('Antworten brauchen eine Anmeldung', text)
        self.assertEqual(fetch_feedback.describe({**event, 'platform': 'reddit', 'video_saved': False, 'message': reddit_mod.MSG_BLOCKED}).split(':')[0], 'Reddit · 1')


class ApiTests(DataBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cfg = integration.default_config()
        cfg['api'].update(token='test-token-' + 'x' * 30, cors_origins=[])
        cls.handle = api.start(cls.root, db_path=cls.db, port=0, cfg=cfg)

    @classmethod
    def tearDownClass(cls):
        cls.handle.stop()
        super().tearDownClass()

    def get(self, path):
        req = urllib.request.Request(self.handle.url + path, headers={'Authorization': 'Bearer test-token-' + 'x' * 30})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as error:
            body = error.read(); error.close()
            return error.code, json.loads(body)

    def test_platform_filter_accepts_the_new_values(self):
        status, body = self.get('/v1/videos?platform=reddit')
        self.assertEqual((status, [v['uid'] for v in body['items']]), (200, ['reddit:abc123']))
        status, body = self.get('/v1/videos?platform=x')
        self.assertEqual((status, [v['uid'] for v in body['items']]), (200, ['x:1000']))
        status, body = self.get('/v1/videos?platform=myspace')
        self.assertEqual(status, 400)

    def test_comments_and_people_and_stats(self):
        status, body = self.get('/v1/comments/new?platform=x&since=2020-01-01')
        self.assertEqual(status, 200); self.assertEqual({c['platform'] for c in body['items']}, {'x'}); self.assertEqual(len(body['items']), 6)
        status, body = self.get('/v1/videos/reddit%3Aabc123/comments')
        self.assertEqual(status, 200); self.assertEqual(len(body['items']), 7)
        parents = {c['comment_id']: c['parent_id'] for c in body['items']}
        self.assertEqual((parents['c1a'], parents['c5'], parents['c4']), ('c1', 'c3', None))
        status, body = self.get('/v1/people?role=commenter&platform=reddit')
        self.assertEqual(status, 200); self.assertTrue(body['items']); self.assertEqual({p['platform'] for p in body['items']}, {'reddit'})
        status, body = self.get('/v1/stats')
        self.assertEqual((status, body['videos']['by_platform']), (200, {'reddit': 1, 'tiktok': 1, 'x': 1}))
        status, body = self.get('/v1/accounts')
        self.assertIn({'reddit', 'x'}, [set(a['platform'] for a in body['items']) & {'reddit', 'x'}])
        self.assertNotIn('auth_token', json.dumps(body))


# ---------------------------------------------------------------- Fenster

@unittest.skipUnless(os.name == 'nt', 'Tk-Fenster nur unter Windows')
class WindowBase(unittest.TestCase):
    def setUp(self):
        import tkinter
        from ui_fake import FakeApp
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / 'data' / 'scrape.db'
        for p in (patch.dict(os.environ, {'SS_DB_PATH': str(self.db), 'SS_PROXY_MODE': 'aus'}), patch.object(core, 'ROOT', self.root),
                  patch.object(core, 'write_settings')):
            p.start(); self.addCleanup(p.stop)
        self.addCleanup(gc.collect)          # Fenster werden im Hauptfaden freigegeben, nicht später im Server-Faden
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self.app.destroy)

    def cookie(self, identity, platform, names):
        d = self.root / 'data' / 'cookies'; d.mkdir(parents=True, exist_ok=True)
        (d / f'{identity}.{platform}.json').write_text(json.dumps([{'name': n, 'value': 'x'} for n in names]), encoding='utf-8')

    def opt(self, cmd, name):
        return cmd[cmd.index(name) + 1]


class AccountsTabTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_accounts import AccountsTab
        self.tab = self.app.add('Konten', AccountsTab); self.tab.refresh(); self.app.update()

    def pick(self, platform):
        iid = next(i for i, a in self.tab.accounts.items() if a['platform'] == platform)
        self.tab.tree.selection_set(iid); self.app.update(); return self.tab.accounts[iid]

    def test_list_shows_all_four_platforms_in_fixed_order(self):
        rows = [self.tab.tree.item(i, 'values') for i in self.tab.tree.get_children()]
        self.assertEqual([r[0] for r in rows], ['TikTok', 'Instagram', 'Reddit', 'X'])
        self.assertIn('freiwillig', rows[2][4]); self.assertNotIn('freiwillig', rows[3][4])

    def test_add_dialog_offers_reddit_and_x(self):
        seen = {}

        def fake(parent, title, fields, intro='', ok_text='OK', validator=None):
            seen['choices'] = next(f['choices'] for f in fields if f['key'] == 'platform')
            return None
        with patch('socialscraper.ui.tab_accounts.ask_form', side_effect=fake):
            self.tab.add()
        self.assertEqual(seen['choices'], [('tiktok', 'TikTok'), ('instagram', 'Instagram'), ('youtube', 'YouTube'), ('reddit', 'Reddit'), ('x', 'X'), ('google', 'Google'), ('trustpilot', 'Trustpilot')])

    def test_password_ways_are_disabled_for_reddit_and_x(self):
        for platform in ('reddit', 'x'):
            self.pick(platform)
            self.assertEqual(str(self.tab.btn['login_pw']['state']), 'disabled'); self.assertEqual(str(self.tab.btn['creds']['state']), 'disabled')
            self.assertEqual(str(self.tab.btn['login']['state']), 'normal'); self.assertEqual(str(self.tab.btn['import']['state']), 'normal')
        self.pick('tiktok')
        self.assertEqual(str(self.tab.btn['login_pw']['state']), 'normal')

    def test_detail_texts(self):
        self.pick('x')
        self.assertIn('Regeln von X', self.tab.next_step['text']); self.assertIn('Zweitkonto', self.tab.next_step['text'])
        self.assertEqual(self.tab.btn['primary']['text'], 'Im Browser anmelden')
        self.pick('reddit')
        self.assertIn('Reddit geht auch ohne Anmeldung', self.tab.next_step['text'])
        self.pick('tiktok'); self.assertNotIn('Regeln von X', self.tab.next_step['text'])

    def test_login_command_for_x_is_manual(self):
        acc = self.pick('x')
        self.tab.login('qr')
        (_label, cmd), = self.app.steps[-1][0]
        self.assertEqual((cmd[0], self.opt(cmd, '--platform'), self.opt(cmd, '--mode'), self.opt(cmd, '--identity')), ('login', 'x', 'qr', 'x_main'))

    def test_logged_in_x_shows_saved_status(self):
        self.cookie('x_main', 'x', ['auth_token', 'ct0'])
        self.tab.refresh()
        row = [self.tab.tree.item(i, 'values') for i in self.tab.tree.get_children()][3]
        self.assertIn('Anmeldung gespeichert', row[4])


class FetchTabTests(WindowBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_data import DataTab
        from socialscraper.ui.tab_fetch import FetchTab
        self.data = self.app.add('Gespeicherte Daten', DataTab)
        self.tab = self.app.add('Abrufen', FetchTab); self.tab.refresh(); self.app.update()

    def set_links(self, text):
        self.tab.links.delete('1.0', 'end'); self.tab.links.insert('1.0', text)

    def test_account_boxes_for_all_four(self):
        self.assertEqual(list(self.tab.acc_boxes), ['tiktok', 'instagram', 'reddit', 'x'])
        self.assertEqual(self.tab.acc_vars['reddit'].get(), 'Standardkonto')
        self.assertEqual(self.tab.acc_vars['x'].get(), 'Standardkonto')

    def test_reddit_links_run_without_login_requirement(self):
        self.set_links('https://old.reddit.com/r/python/comments/abc123/x/\nhttps://redd.it/abc123')
        with patch.object(logic, 'describe_busy', return_value=''):
            self.tab.start_fetch()
        (label, cmd), = self.app.steps[-1][0]
        self.assertEqual(cmd[0], 'scrape'); self.assertEqual(cmd[1], 'https://www.reddit.com/r/python/comments/abc123/'); self.assertNotIn('--require-auth', cmd)
        self.assertEqual([c for c in cmd if c.startswith('https://')], ['https://www.reddit.com/r/python/comments/abc123/'])
        self.assertEqual(self.opt(cmd, '--identity'), 'reddit_main'); self.assertIn('Reddit', label)
        self.assertIn('doppelter Link', self.app.logs[-1][0])

    def test_x_without_login_warns_but_still_fetches_post_data(self):
        self.set_links('https://x.com/a/status/12?s=20')
        with patch.object(logic, 'describe_busy', return_value=''):
            self.tab.start_fetch()
        self.assertTrue(any('Antworten brauchen eine Anmeldung' in line for line, _tag in self.app.logs))
        (label, cmd), = self.app.steps[-1][0]
        self.assertEqual((cmd[1], self.opt(cmd, '--identity')), ('https://x.com/a/status/12', 'x_main'))
        self.assertNotIn('--require-auth', cmd)

    def test_x_with_only_post_data_requested_does_not_warn(self):
        self.tab.max_comments.set(0); self.set_links('https://x.com/a/status/12')
        with patch.object(logic, 'describe_busy', return_value=''):
            self.tab.start_fetch()
        self.assertFalse(any('Antworten brauchen' in line for line, _tag in self.app.logs))

    def test_checkbox_makes_x_and_tiktok_require_login(self):
        self.cookie('x_main', 'x', ['auth_token', 'ct0'])
        self.tab.need_auth.set(True)
        self.set_links('https://x.com/a/status/12\nhttps://www.tiktok.com/@u/video/1\nhttps://www.reddit.com/r/a/comments/abcdef/')
        with patch.object(logic, 'describe_busy', return_value=''):
            self.tab.start_fetch()
        steps = {label.split(':')[0]: cmd for label, cmd in self.app.steps[-1][0]}
        self.assertIn('--require-auth', steps['X']); self.assertIn('--require-auth', steps['TikTok']); self.assertNotIn('--require-auth', steps['Reddit'])

    def test_chosen_x_account_flows_into_command(self):
        logic.add_account(self.db, 'x', 'Zweit', 'at'); self.tab.refresh()
        self.tab.acc_vars['x'].set('Zweit'); self.set_links('https://x.com/a/status/12')
        with patch.object(logic, 'describe_busy', return_value=''):
            self.tab.start_fetch()
        (_l, cmd), = self.app.steps[-1][0]
        self.assertEqual((self.opt(cmd, '--identity'), self.opt(cmd, '--country')), ('x_zweit', 'at'))

    def test_foreign_links_are_listed_as_errors(self):
        self.set_links('https://fxtwitter.com/a/status/12\nhttps://x.com/a/status/12')
        with patch('tkinter.messagebox.showwarning') as warn:
            self.tab.start_fetch()
        self.assertIn('Reddit oder X', warn.call_args[0][1]); self.assertIn('fxtwitter.com', warn.call_args[0][1]); self.assertEqual(self.app.steps, [])

    def test_proxy_mode_refuses_x_with_a_clear_text(self):
        self.set_links('https://x.com/a/status/12')
        with patch('socialscraper.proxylogic.current_mode', return_value='tor'), patch('tkinter.messagebox.showwarning') as warn, \
                patch.object(self.tab.mode_banner, 'update_mode', return_value='tor'):
            self.tab.start_fetch()
        self.assertIn('X braucht immer ein Konto', warn.call_args[0][1]); self.assertEqual(self.app.steps, [])

    def test_reddit_is_allowed_with_tor(self):
        self.set_links('https://www.reddit.com/r/a/comments/abcdef/')
        with patch.object(logic, 'describe_busy', return_value=''), patch.object(self.tab.mode_banner, 'update_mode', return_value='tor'):
            self.tab.start_fetch()
        self.assertEqual(len(self.app.steps), 1)

    def test_background_service_lock_applies_to_reddit_and_x(self):
        for link in ('https://www.reddit.com/r/a/comments/abcdef/', 'https://x.com/a/status/12'):
            self.set_links(link)
            with patch.object(logic, 'describe_busy', return_value='Der Hintergrunddienst arbeitet gerade.'), patch('tkinter.messagebox.showinfo'):
                self.tab.start_fetch()
        self.assertEqual(self.app.steps, [])


class AutoTabTests(WindowBase):
    def setUp(self):
        super().setUp()
        with Store(str(self.db)) as s:
            repo.ensure_default_accounts(s.db)
        from socialscraper.ui.tab_auto import AutoTab
        self.tab = self.app.add('Automatik', AutoTab); self.tab.refresh(); self.app.update()

    def dialog(self):
        return self.tab.add_target(modal=False)

    def test_platform_lists_per_kind(self):
        dlg = self.dialog()
        expect = {'Video-Link': ('TikTok', 'Instagram', 'YouTube', 'Reddit', 'X'), 'Hashtag': ('TikTok', 'YouTube', 'X'),
                  'Suchbegriff': ('TikTok', 'YouTube', 'Reddit', 'X'), 'Profil': ('TikTok', 'Reddit', 'X')}
        for kind, platforms in expect.items():
            dlg.kind_var.set(kind); dlg._kind_changed()
            self.assertEqual(tuple(dlg.platform_cb['values']), platforms, kind)
        dlg.destroy()

    def test_labels_and_examples_explain_reddit_syntax(self):
        dlg = self.dialog()
        dlg.kind_var.set('Suchbegriff'); dlg._kind_changed(); dlg.platform_var.set('Reddit'); dlg._platform_changed()
        self.assertEqual(dlg.value_title['text'], 'Suchbegriff oder Subreddit')
        self.assertIn('r/python', dlg.value_hint['text']); self.assertIn('Subreddit: r/name', dlg.value_hint['text'])
        dlg.kind_var.set('Profil'); dlg._kind_changed(); dlg.platform_var.set('Reddit'); dlg._platform_changed()
        self.assertIn('u/', dlg.value_title['text']); self.assertIn('u/spez', dlg.value_hint['text'])
        dlg.platform_var.set('X'); dlg._platform_changed()
        self.assertEqual(dlg.value_title['text'], 'Kontoname (ohne @)'); self.assertIn('Anmeldung', dlg.value_hint['text'])
        dlg.kind_var.set('Video-Link'); dlg._kind_changed()
        dlg.platform_var.set('Reddit'); dlg._platform_changed(); self.assertIn('reddit.com/r/', dlg.value_hint['text'])
        dlg.platform_var.set('X'); dlg._platform_changed(); self.assertIn('x.com/name/status/', dlg.value_hint['text'])
        dlg.destroy()

    def test_hints_in_the_dialog(self):
        dlg = self.dialog()
        dlg.kind_var.set('Suchbegriff'); dlg._kind_changed(); dlg.platform_var.set('X'); dlg._platform_changed()
        self.assertIn('X funktioniert nur mit Anmeldung', dlg.notice['text'])
        self.cookie('x_main', 'x', ['auth_token', 'ct0']); dlg._platform_changed()
        self.assertIn('Regeln von X', dlg.notice['text']); self.assertIn('zweitkonto', dlg.notice['text'].lower())
        dlg.platform_var.set('Reddit'); dlg._platform_changed(); self.assertIn('Reddit wird im Browser gelesen', dlg.notice['text'])
        dlg.destroy()

    def test_saving_reddit_and_x_targets(self):
        dlg = self.dialog()
        dlg.kind_var.set('Suchbegriff'); dlg._kind_changed(); dlg.platform_var.set('Reddit'); dlg._platform_changed()
        dlg.value_var.set('/r/Python/'); self.assertTrue(dlg.save())
        dlg = self.dialog()
        dlg.kind_var.set('Profil'); dlg._kind_changed(); dlg.platform_var.set('Reddit'); dlg._platform_changed()
        dlg.value_var.set('u/spez'); self.assertTrue(dlg.save())
        dlg = self.dialog()
        dlg.kind_var.set('Hashtag'); dlg._kind_changed(); dlg.platform_var.set('X'); dlg._platform_changed()
        dlg.value_var.set('#Tanzen'); self.assertTrue(dlg.save())
        dlg = self.dialog()
        dlg.kind_var.set('Profil'); dlg._kind_changed(); dlg.platform_var.set('X'); dlg._platform_changed()
        dlg.value_var.set('@Beispiel_Konto'); self.assertTrue(dlg.save())
        dlg = self.dialog()
        dlg.kind_var.set('Video-Link'); dlg._kind_changed(); dlg.platform_var.set('X'); dlg._platform_changed()
        dlg.value_var.set('https://twitter.com/a/status/55?s=20'); self.assertTrue(dlg.save())
        with Store(str(self.db)) as s:
            got = {(t['kind'], t['platform'], t['value']) for t in repo.list_targets(s.db)}
        self.assertEqual(got, {('search', 'reddit', 'r/Python'), ('profile', 'reddit', 'spez'), ('hashtag', 'x', 'tanzen'),
                               ('profile', 'x', 'Beispiel_Konto'), ('video', 'x', 'https://twitter.com/a/status/55?s=20')})

    def test_invalid_values_show_german_messages(self):
        dlg = self.dialog()
        dlg.kind_var.set('Suchbegriff'); dlg._kind_changed(); dlg.platform_var.set('Reddit'); dlg._platform_changed()
        dlg.value_var.set('r/x'); self.assertFalse(dlg.save()); self.assertIn('Subreddit', dlg.error['text'])
        dlg.kind_var.set('Profil'); dlg._kind_changed(); dlg.platform_var.set('X'); dlg._platform_changed()
        dlg.value_var.set('name.mit.punkt'); self.assertFalse(dlg.save()); self.assertIn('X-Kontoname', dlg.error['text'])
        dlg.kind_var.set('Video-Link'); dlg._kind_changed()
        dlg.value_var.set('https://evil.com/?u=reddit.com'); self.assertFalse(dlg.save())
        self.assertIn('Reddit oder X', dlg.error['text'])
        dlg.destroy()
        with self.assertRaisesRegex(ValueError, 'gehört zu X, nicht zu Reddit'):
            al.check_target('video', 'reddit', 'https://x.com/a/status/55', 60, 5, 5)

    def test_pasting_a_link_switches_the_platform(self):
        dlg = self.dialog()
        dlg.kind_var.set('Video-Link'); dlg._kind_changed()
        dlg.value_var.set('https://www.reddit.com/r/python/comments/abc123/x/'); self.app.update()
        self.assertEqual(dlg.platform_var.get(), 'Reddit')
        dlg.value_var.set('https://twitter.com/a/status/1'); self.app.update()
        self.assertEqual(dlg.platform_var.get(), 'X')
        dlg.destroy()

    def test_texts_mention_posts(self):
        self.assertIn('Reddit und X', al.EXPLAIN)
        self.assertIn('r/name', al.KIND_HELP['search'])
        self.assertEqual(al.PLATFORM_NAMES['reddit'], 'Reddit'); self.assertEqual(al.PLATFORM_NAMES['x'], 'X')
        self.assertEqual(al.value_text('profile', 'reddit', 'spez'), 'u/spez')
        self.assertEqual(al.value_text('profile', 'x', 'name'), '@name'); self.assertEqual(al.value_text('hashtag', 'x', 'tanzen'), '#tanzen')
        self.assertEqual(al.value_text('search', 'reddit', 'r/python'), 'r/python')
        self.assertEqual(al.target_title({'kind': 'profile', 'platform': 'reddit', 'value': 'spez'}), 'Profil u/spez (Reddit)')


class DataTabTests(WindowBase):
    def test_filter_lists_reddit_and_x(self):
        from socialscraper.ui.tab_data import DataTab
        with Store(str(self.db)) as s:
            s.save_result(Video('reddit', 'abc123', 'https://www.reddit.com/comments/abc123/', author='op', caption='Titel', like_count=5), [], repo.iso())
            s.save_result(Video('x', '1', 'https://x.com/a/status/1', author='a', caption='Text', like_count=1), [], repo.iso())
        tab = self.app.add('Gespeicherte Daten', DataTab); tab.refresh(); self.app.update()
        self.assertEqual(list(tab.filter_platform_cb['values']) if hasattr(tab, 'filter_platform_cb') else
                         ['Alle'] + [v['name'] for v in core.PLATFORMS.values()], ['Alle', 'TikTok', 'Instagram', 'YouTube', 'Reddit', 'X'])
        names = {tab.tree.item(i, 'values')[0] for i in tab.tree.get_children()}
        self.assertEqual(names, {'Reddit', 'X'})
        tab.filter_platform.set('Reddit'); tab.refresh(); self.app.update()
        self.assertEqual({tab.tree.item(i, 'values')[0] for i in tab.tree.get_children()}, {'Reddit'})
        tab.filter_platform.set('X'); tab.refresh(); self.app.update()
        self.assertEqual({tab.tree.item(i, 'values')[0] for i in tab.tree.get_children()}, {'X'})


if __name__ == '__main__':
    unittest.main()
