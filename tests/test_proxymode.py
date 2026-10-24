"""Proxy-Modus: Einstellung, Pools und die harte Regel „frei/Tor nie mit einem Konto“ (alles mit Attrappen)."""
import contextlib
import hashlib
import io
import os
import pathlib
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from socialscraper import cli, config, discovery, repo, scheduler
from socialscraper.adapters.base import ScrapeContext
from socialscraper.backends.base import BrowserBackend
from socialscraper.config import Settings, load, validate
from socialscraper.models import Video
from socialscraper.net import cookies, freeproxy, proxymode, tor
from socialscraper.net.cookies import CookieJar
from socialscraper.net.identity import IdentityManager
from socialscraper.net.proxy import Proxy, ProxyPool
from socialscraper.store import Store

TT = 'https://www.tiktok.com/@user/video/1'
IG = 'https://www.instagram.com/reel/abc/'
YT = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'


def digest(path):
    return hashlib.md5(pathlib.Path(path).read_bytes()).hexdigest()


class Scoped(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        patcher = patch.object(cookies, 'BASE', self.root / 'cookies')
        patcher.start(); self.addCleanup(patcher.stop)
        freeproxy.clear_memory(); self.addCleanup(freeproxy.clear_memory)
        # Cookie-Datei eines echten Kontos: muss in jedem anonymen Lauf unberührt bleiben.
        CookieJar.save('tiktok_main', 'tiktok', [{'name': 'sessionid', 'value': 'konto-geheimnis', 'domain': '.tiktok.com'}])
        self.cookie_file = CookieJar.path('tiktok_main', 'tiktok')
        self.before = digest(self.cookie_file)
        self.cfg = Settings(db_path=str(self.root / 'data' / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'data' / 'profiles'), proxy_mode='frei')

    def assertCookiesUntouched(self):
        self.assertEqual(digest(self.cookie_file), self.before)


class ConfigTests(unittest.TestCase):
    def test_default_is_aus_and_old_settings_still_load(self):
        self.assertEqual(Settings().proxy_mode, 'aus')
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 's.toml'
            path.write_text('proxies = ["http://host:80"]\n', encoding='utf-8')
            cfg = load(str(path))
        self.assertEqual((cfg.proxy_mode, cfg.proxies), ('aus', ['http://host:80']))

    def test_toml_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 's.toml'
            path.write_text('proxy_mode = "tor"\n', encoding='utf-8')
            self.assertEqual(load(str(path)).proxy_mode, 'tor')
            with patch.dict(os.environ, {'SS_PROXY_MODE': ' FREI '}):
                self.assertEqual(load(str(path)).proxy_mode, 'frei')

    def test_validation(self):
        for mode in config.PROXY_MODES:
            validate(Settings(proxy_mode=mode))
        with self.assertRaises(ValueError):
            validate(Settings(proxy_mode='gratis'))
        with self.assertRaises(ValueError):
            validate(Settings(proxy_mode=1))

    def test_mode_helpers_ignore_garbage(self):
        self.assertEqual(proxymode.mode(Mock()), 'aus')
        self.assertFalse(proxymode.is_anonymous(Mock()))
        self.assertTrue(proxymode.is_anonymous(Settings(proxy_mode='tor')))
        self.assertTrue(proxymode.uses_proxy(Settings(proxies=['http://h:1'])))
        self.assertFalse(proxymode.uses_proxy(Settings()))


class PoolTests(Scoped):
    def test_pool_choice_per_mode(self):
        self.assertIsInstance(proxymode.make_pool(Settings(proxies=['http://h:1'])), ProxyPool)
        self.assertFalse(proxymode.make_pool(Settings()).anonymous)
        self.assertIsInstance(proxymode.make_pool(Settings(proxy_mode='liste', proxies=['http://h:1'])), ProxyPool)
        self.assertIsInstance(proxymode.make_pool(self.cfg), proxymode.FreePool)
        self.assertIsInstance(proxymode.make_pool(Settings(proxy_mode='tor')), proxymode.TorPool)

    def test_liste_without_proxies_refuses_instead_of_going_direct(self):
        with self.assertRaises(proxymode.ProxyUnavailable):
            proxymode.make_pool(Settings(proxy_mode='liste'))

    def test_aus_with_list_keeps_working_for_accounts(self):
        pool = proxymode.make_pool(Settings(proxies=['http://u:p@host:80']))
        manager = IdentityManager(pool, self.root / 'profiles')
        manager.get('tiktok_main', 'de')   # Konto-Identität erlaubt
        self.assertEqual(manager.proxy_for(manager.get('tiktok_main', 'de')).host, 'host')

    def test_free_pool_never_goes_direct_and_rotates(self):
        pool = proxymode.make_pool(self.cfg)
        with patch.object(freeproxy, 'acquire', return_value=None), self.assertRaises(proxymode.ProxyUnavailable) as caught:
            pool.acquire('anon_public')
        self.assertIn('NICHT ohne Proxy', str(caught.exception))
        first, second = Proxy('http', '1.1.1.1', 80), Proxy('http', '2.2.2.2', 80)
        with patch.object(freeproxy, 'acquire', side_effect=[first, second]), \
             patch.object(freeproxy, 'report_failure') as report:
            self.assertIs(pool.acquire('anon_public'), first)
            self.assertIs(pool.acquire('anon_public'), first)   # bleibt beim selben Proxy
            pool.report_failure(first)
            self.assertIs(pool.acquire('anon_public'), second)  # nach Fehler der nächste
        report.assert_called_once()

    def test_identity_manager_refuses_every_account_identity_but_not_the_anonymous_one(self):
        manager = IdentityManager(proxymode.FreePool(self.root), self.root / 'profiles')
        for name in ('tiktok_main', 'youtube_main', 'instagram_main', 'default'):
            with self.subTest(name=name), self.assertRaises(proxymode.AccountsForbidden):
                manager.get(name, 'de')
        ident = manager.get(proxymode.ANON_IDENTITY, 'de')
        self.assertEqual(ident.name, 'anon_public')
        with self.assertRaises(proxymode.AccountsForbidden):
            IdentityManager(proxymode.TorPool(), self.root / 'profiles').get('tiktok_main', 'de')

    def test_message_is_the_agreed_german_text(self):
        with self.assertRaises(proxymode.AccountsForbidden) as caught:
            proxymode.refuse_accounts()
        self.assertEqual(str(caught.exception), 'Mit kostenlosen Proxys/Tor werden keine Konten benutzt – aus Sicherheitsgründen.')


class CookieIsolationTests(Scoped):
    def handle(self, name, proxy):
        return types.SimpleNamespace(identity=types.SimpleNamespace(name=name), proxy=proxy)

    class Backend(BrowserBackend):
        name = 'fake'
        def open(self, *a, **k): ...
        def close(self, handle): ...

    def test_no_cookies_loaded_or_injected_with_anonymous_proxy_even_for_account_identity(self):
        backend = self.Backend()
        anon = Proxy('socks5', '127.0.0.1', 9150); anon.anonymous = True
        backend.inject_cookies = Mock()
        with patch('socialscraper.backends.base.CookieJar.load') as load:
            self.assertEqual(backend._apply_cookies(self.handle('tiktok_main', anon), 'tiktok'), 0)
            self.assertEqual(backend._apply_cookies(self.handle('anon_public', None), 'tiktok'), 0)
        load.assert_not_called()
        backend.inject_cookies.assert_not_called()
        self.assertCookiesUntouched()

    def test_normal_proxy_still_gets_cookies(self):
        backend = self.Backend()
        backend.inject_cookies = Mock()
        self.assertEqual(backend._apply_cookies(self.handle('tiktok_main', Proxy('http', 'h', 1)), 'tiktok'), 1)
        backend.inject_cookies.assert_called_once()


class CliTests(Scoped):
    def setUp(self):
        super().setUp()
        self.adapter = Mock(platform='tiktok', needs_browser=True, needs_auth=True)
        self.adapter.fetch.return_value = (Video('tiktok', '1', TT, comment_count=0), [])
        self.log = io.StringIO()

    def run_cli(self, args, platform='tiktok'):
        import logging
        self.adapter.platform = platform
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger('cli')
        logger.addHandler(handler); self.addCleanup(logger.removeHandler, handler)
        with patch.object(cli, 'load', return_value=self.cfg), patch.object(cli, 'resolve', return_value=self.adapter),              patch.object(cli, '_ctx_and_backend', return_value=(Mock(), Mock(), Mock())),              patch.object(cli, 'ensure') as ensure, patch('socialscraper.login.login') as login,              patch('socialscraper.login.check') as check, contextlib.redirect_stdout(io.StringIO()):
            self.ensure, self.login, self.check = ensure, login, check
            code = cli.main(args)
        self.errors = stream.getvalue()
        logger.removeHandler(handler)
        return code

    def test_login_check_and_import_are_refused(self):
        importfile = self.root / 'c.json'
        importfile.write_text('[{"name":"sessionid","value":"x","domain":".tiktok.com"}]', encoding='utf-8')
        for argv in (['login'], ['login', '--platform', 'instagram'], ['check'],
                     ['import-cookies', '--file', str(importfile), '--identity', 'tiktok_main']):
            for mode in ('frei', 'tor'):
                self.cfg.proxy_mode = mode
                with self.subTest(argv=argv, mode=mode):
                    self.assertEqual(self.run_cli(argv), 1)
                    self.assertIn('keine Konten benutzt', self.errors)
                    self.login.assert_not_called(); self.check.assert_not_called(); self.ensure.assert_not_called()
        self.assertCookiesUntouched()

    def test_instagram_and_tiktok_comments_refused_before_any_browser_work(self):
        self.assertEqual(self.run_cli(['scrape', IG], 'instagram'), 1)
        self.assertEqual(self.run_cli(['scrape', TT, '--max-comments', '5']), 1)
        self.assertEqual(self.run_cli(['profile', 'user', '--max-comments', '5']), 1)
        self.adapter.fetch.assert_not_called()
        self.ensure.assert_not_called()
        self.assertCookiesUntouched()

    def test_tiktok_without_comments_runs_with_anonymous_identity_and_no_login_check(self):
        self.assertEqual(self.run_cli(['scrape', TT, '--max-comments', '0', '--identity', 'tiktok_main']), 0)
        ctx = self.adapter.fetch.call_args.args[1]
        self.assertEqual((ctx.identity, ctx.country, ctx.mobile, ctx.authenticated), ('anon_public', 'de', False, False))
        self.ensure.assert_not_called(); self.login.assert_not_called()
        self.assertCookiesUntouched()

    def test_normal_mode_unchanged(self):
        self.cfg.proxy_mode = 'aus'
        self.assertEqual(self.run_cli(['scrape', TT, '--identity', 'tiktok_main']), 0)
        self.assertEqual(self.adapter.fetch.call_args.args[1].identity, 'tiktok_main')
        self.ensure.assert_called()


class GuardTests(Scoped):
    def test_login_function_and_session_check_refuse_with_anonymous_pool(self):
        from socialscraper import login as login_module
        from socialscraper.net.session_guard import ensure
        im = IdentityManager(proxymode.FreePool(self.root), self.root / 'profiles')
        backend = Mock()
        for call in (lambda: login_module.login(backend, im, 'tiktok_main', 'tiktok'),
                     lambda: login_module.check(backend, im, 'tiktok_main', 'tiktok'),
                     lambda: ensure(backend, im, 'tiktok_main', 'tiktok')):
            with self.assertRaises(proxymode.AccountsForbidden):
                call()
        backend.session.assert_not_called(); backend.open.assert_not_called()
        self.assertCookiesUntouched()

    def test_adapters_refuse_directly(self):
        from socialscraper.adapters.instagram import InstagramAdapter
        from socialscraper.adapters.tiktok import TikTokAdapter
        ctx = ScrapeContext(cfg=self.cfg, store=None, im=Mock(), sessions=Mock(), identity='anon_public')
        with self.assertRaises(proxymode.AccountsForbidden):
            InstagramAdapter().fetch(IG, ctx)
        with self.assertRaises(proxymode.AccountsForbidden):
            TikTokAdapter().fetch(TT, ctx)   # Standard: 500/20 Kommentare -> Konto nötig
        ctx.sessions.acquire.assert_not_called()

    def test_discovery_refuses_login_dependent_kinds(self):
        ctx = ScrapeContext(cfg=self.cfg, store=None, im=Mock(), sessions=Mock(), identity='anon_public')
        for kind in ('hashtag', 'search'):
            with self.assertRaises(proxymode.AccountsForbidden):
                discovery.discover(kind, 'tiktok', 'x', ctx, 5)
        ctx.sessions.acquire.assert_not_called()


class SchedulerTests(Scoped):
    def setUp(self):
        super().setUp()
        self.store = Store(self.cfg.db_path); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.adapter = Mock(platform='youtube', needs_browser=False, needs_auth=False)
        self.adapter.fetch.side_effect = lambda url, ctx: (Video('youtube', 'v1', url, comment_count=0), [])
        patcher = patch.object(scheduler, 'resolve', side_effect=lambda u: self.adapter if 'youtube' in u else None)
        patcher.start(); self.addCleanup(patcher.stop)

    def run_target(self, kind, platform, value, **kw):
        target = repo.add_target(self.db, kind, platform, value, **kw)
        return scheduler.run_target(self.root, target['id'], 'manual', cfg=self.cfg)

    def test_accounts_are_refused_with_german_message(self):
        cases = (('video', 'instagram', IG, {}), ('hashtag', 'tiktok', 'lofi', {'max_comments': 0}),
                 ('search', 'tiktok', 'lofi', {'max_comments': 0}), ('video', 'tiktok', TT, {'max_comments': 10}))
        for mode in ('frei', 'tor'):
            self.cfg.proxy_mode = mode
            for number, (kind, platform, value, extra) in enumerate(cases):
                with self.subTest(mode=mode, kind=kind, platform=platform):
                    value = value + ('x' if mode == 'tor' else '') if kind != 'video' else value.replace('1', str(number + (4 if mode == 'tor' else 1)), 1) if platform == 'tiktok' else value.rstrip('/') + mode + '/'
                    result = self.run_target(kind, platform, value, **extra)
                    self.assertEqual(result['status'], 'failed')
                    self.assertIn('keine Konten benutzt', result['error'])
        self.assertCookiesUntouched()

    def test_youtube_runs_with_anonymous_identity_and_pool(self):
        result = self.run_target('video', 'youtube', YT)
        self.assertEqual(result['status'], 'ok')
        ctx = self.adapter.fetch.call_args.args[1]
        self.assertEqual((ctx.identity, ctx.country), ('anon_public', 'de'))
        self.assertTrue(ctx.im.pool.anonymous)
        self.assertCookiesUntouched()

    def test_normal_mode_still_uses_youtube_main(self):
        self.cfg.proxy_mode = 'aus'
        self.run_target('video', 'youtube', YT)
        self.assertEqual(self.adapter.fetch.call_args.args[1].identity, 'youtube_main')


class YouTubeProxyTests(Scoped):
    def make_ctx(self, mode, pool_proxy):
        self.cfg.proxy_mode = mode
        self.cfg.max_comments = 0
        self.cfg.impersonate = ''
        im = Mock(); im.pool = Mock(); im.pool.anonymous = True
        im.proxy_for.side_effect = pool_proxy
        return ScrapeContext(cfg=self.cfg, store=None, im=im, identity='anon_public', country='de')

    def fetch(self, ctx, outcomes):
        from socialscraper.adapters import youtube
        calls = []

        class DownloadError(Exception):
            pass

        class FakeYDL:
            def __init__(self, options): self.options = options
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False, process=True):   # yt-dlp: erst roh lesen, dann verarbeiten
                calls.append(dict(self.options))
                outcome = outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            def process_ie_result(self, result, download=False): return result

        module = types.ModuleType('yt_dlp')
        module.YoutubeDL = FakeYDL
        module.utils = types.SimpleNamespace(DownloadError=DownloadError)
        self.DownloadError = DownloadError
        with patch.dict('sys.modules', {'yt_dlp': module}), \
             patch.object(youtube, 'youtube_runtime_options', return_value={}):
            outcomes[:] = [DownloadError(m) if isinstance(m, str) else m for m in outcomes]
            return youtube.YouTubeAdapter().fetch(YT, ctx), calls

    def test_tor_uses_remote_dns_and_free_mode_rotates_after_failure(self):
        tor_proxy = tor.proxy(9150)
        patcher = patch('socialscraper.net.errors.precheck_local_proxy'); patcher.start(); self.addCleanup(patcher.stop)
        ctx = self.make_ctx('tor', lambda ident: tor_proxy)
        (video, comments), calls = self.fetch(ctx, [{'id': 'jNQXAC9IVRw'}])
        self.assertEqual(calls[0]['proxy'], 'socks5h://127.0.0.1:9150')

        a, b = Proxy('http', '1.1.1.1', 80), Proxy('socks5', '2.2.2.2', 1080)
        a.anonymous = b.anonymous = True
        queue = [a, b]
        ctx = self.make_ctx('frei', lambda ident: queue[0])
        ctx.im.pool.report_failure.side_effect = lambda proxy: queue.pop(0)
        (video, comments), calls = self.fetch(ctx, ['Unable to connect to proxy', {'id': 'jNQXAC9IVRw'}])
        self.assertEqual([c['proxy'] for c in calls], ['http://1.1.1.1:80', 'socks5h://2.2.2.2:1080'])
        ctx.im.pool.report_failure.assert_called_once_with(a)
        self.assertLessEqual(calls[0]['socket_timeout'], 10)

    def test_permanent_error_does_not_ban_proxy(self):
        from socialscraper.net.retry import PermanentFetchError
        a = Proxy('http', '1.1.1.1', 80); a.anonymous = True
        ctx = self.make_ctx('frei', lambda ident: a)
        with self.assertRaises(PermanentFetchError):
            self.fetch(ctx, ['Private video'])
        ctx.im.pool.report_failure.assert_not_called()

    def test_no_proxy_available_raises_and_never_calls_youtube_directly(self):
        def none(ident):
            raise proxymode.ProxyUnavailable('keiner')
        ctx = self.make_ctx('frei', none)
        with self.assertRaises(proxymode.ProxyUnavailable):
            self.fetch(ctx, [{'id': 'x'}])


class UiApiTests(Scoped):
    def test_describe(self):
        self.assertEqual(proxymode.describe(Settings())[0:3:2], ('aus', True))
        self.assertFalse(proxymode.describe(Settings(proxy_mode='liste'))[2])
        self.assertTrue(proxymode.describe(Settings(proxy_mode='liste', proxies=['http://h:1']))[2])
        mode, text, ok = proxymode.describe(self.cfg, self.root)
        self.assertEqual((mode, ok), ('frei', False))
        self.assertIn('Nur ohne Konto', text)
        with patch.object(tor, 'detect', return_value=None),              patch.object(tor, 'tor_exe', return_value=pathlib.Path('gibt-es-nicht.exe')):
            mode, text, ok = proxymode.describe(Settings(proxy_mode='tor'))
        self.assertFalse(ok)
        self.assertIn('Tor Browser', text)
        self.assertIn('tools', text)

    def test_ensure_ready_free_refreshes_and_reports(self):
        stats = {'funktionierend': 0, 'getestet': 10, 'zertifikatsfehler': 2, 'schnellste': []}
        with patch.object(freeproxy, 'refresh', return_value=stats):
            ok, message = proxymode.ensure_ready(self.cfg, self.root)
        self.assertFalse(ok)
        self.assertIn('Zertifikatsfehler', message)
        stats = {'funktionierend': 4, 'getestet': 10, 'zertifikatsfehler': 0, 'schnellste': [{'proxy': 'x', 'ms': 321}]}
        with patch.object(freeproxy, 'refresh', return_value=stats):
            ok, message = proxymode.ensure_ready(self.cfg, self.root)
        self.assertTrue(ok)
        self.assertIn('321 ms', message)

    def test_ensure_ready_other_modes(self):
        self.assertTrue(proxymode.ensure_ready(Settings(), self.root)[0])
        self.assertFalse(proxymode.ensure_ready(Settings(proxy_mode='liste'), self.root)[0])
        self.assertTrue(proxymode.ensure_ready(Settings(proxy_mode='liste', proxies=['http://h:1']), self.root)[0])
        with patch.object(tor, 'detect', return_value=None):
            ok, message = proxymode.ensure_ready(Settings(proxy_mode='tor'), self.root)
        self.assertFalse(ok)
        self.assertIn('Tor Browser', message)


class ProxiesCommandTests(Scoped):
    def run_cmd(self, argv):
        out = io.StringIO()
        with patch.object(cli, 'load', return_value=self.cfg), contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_status_and_tor_without_network(self):
        with patch.object(tor, 'detect', return_value=None):
            code, out = self.run_cmd(['proxies', 'status'])
            self.assertEqual(code, 0)
            self.assertIn('Proxy-Modus: frei', out)
            self.assertIn('Tor: läuft nicht', out)
            code, out = self.run_cmd(['proxies', 'tor'])
        self.assertEqual(code, 2)
        self.assertIn('Tor Browser', out)

    def test_refresh_prints_counts(self):
        stats = {'quellen_ok': 8, 'quellen_fehler': [], 'kandidaten': 300, 'getestet': 300, 'funktionierend': 2,
                 'zertifikatsfehler': 1, 'verdaechtige_quellen': ['x (1 Zertifikatsfehler)'],
                 'schnellste': [{'proxy': 'http://1.1.1.1:80', 'ms': 123}]}
        with patch.object(freeproxy, 'refresh', return_value=stats):
            code, out = self.run_cmd(['proxies', 'refresh'])
        self.assertEqual(code, 0)
        for text in ('Funktionierend: 2', 'Zertifikatsfehler (verworfen): 1', '123 ms'):
            self.assertIn(text, out)
        stats = dict(stats, funktionierend=0, schnellste=[])
        with patch.object(freeproxy, 'refresh', return_value=stats):
            code, out = self.run_cmd(['proxies', 'refresh'])
        self.assertEqual(code, 2)
        self.assertIn('nicht abgerufen', out)


if __name__ == '__main__':
    unittest.main()
