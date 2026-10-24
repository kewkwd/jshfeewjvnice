"""Fester Proxy je Konto: nur dieser Proxy, kein Direktweg, Passwort nie sichtbar. Lokale Attrappen, Wegwerf-Ordner."""
import base64
import json
import logging
import os
import pathlib
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from socialscraper import cli, guicore, repo, rotation, scheduler
from socialscraper.config import Settings
from socialscraper.models import Video
from socialscraper.net import accountproxy, notify
from socialscraper.net.accountproxy import AccountProxyError
from socialscraper.net.identity import IdentityManager
from socialscraper.net.proxy import Proxy, ProxyPool
from socialscraper.net.session_guard import State
from socialscraper.store import Store
from tests.netfakes import FakeHttpConnect, _Server
from tests.test_rotation import make_login

os.environ.setdefault('SS_PACE', 'off')
PW = 'Geh3imesPasswort77'
URL = f'http://proxyuser:{PW}@127.0.0.1:%d'


def closed_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class WebProxy(_Server):
    """Lokaler HTTP-Proxy: verlangt Anmeldung (407), merkt sich jede Anfrage, antwortet mit einer festen Seite."""

    def __init__(self, auth=None):
        self.auth = auth
        self.requests = []
        super().__init__()

    def handle(self, conn):
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        lines = data.split(b'\r\n\r\n')[0].decode('latin-1').split('\r\n')
        headers = {k.lower(): v.strip() for k, v in (line.split(':', 1) for line in lines[1:] if ':' in line)}
        self.requests.append((lines[0], headers))
        if self.auth and headers.get('proxy-authorization') != 'Basic ' + base64.b64encode(self.auth.encode()).decode():
            conn.sendall(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="x"\r\n'
                         b'Content-Length: 0\r\nConnection: close\r\n\r\n')
            return
        body = b'<html><body>via-festen-proxy</body></html>'
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\nConnection: close\r\n\r\n' % len(body) + body)


class ProxyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db_path = self.root / 'data' / 'scrape.db'
        self.store = Store(str(self.db_path)); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.a = repo.default_account(self.db, 'tiktok')
        self.b = repo.add_account(self.db, 'tiktok', 'Zweit')
        self.c = repo.add_account(self.db, 'tiktok', 'Dritt')
        for acc in (self.a, self.b, self.c):
            make_login(self.root, 'tiktok', acc['identity'])
        self.servers = []

    def web_proxy(self, **kw):
        server = WebProxy(**kw)
        self.servers.append(server)
        self.addCleanup(server.close)
        return server

    def secret_scan(self):
        """Alle Dateien des Wegwerf-Ordners (außer der Proxy-Datei selbst): kommt das Passwort irgendwo vor?"""
        hits = []
        for path in self.root.rglob('*'):
            if path.is_file() and path.name != accountproxy.FILE_NAME and 'cookies' not in path.parts:
                if PW.encode() in path.read_bytes():
                    hits.append(path.name)
        return hits


class FormatTests(unittest.TestCase):
    def test_normalize_accepts_good_and_refuses_bad(self):
        self.assertEqual(accountproxy.normalize('1.2.3.4:8080'), 'http://1.2.3.4:8080')
        self.assertEqual(accountproxy.normalize(' socks5://1.2.3.4:1080 '), 'socks5://1.2.3.4:1080')
        self.assertEqual(accountproxy.normalize('socks5://u:p@1.2.3.4:1080'), 'socks5://u:p@1.2.3.4:1080')
        self.assertEqual(accountproxy.normalize('https://u:p@proxy.example.de:443'), 'https://u:p@proxy.example.de:443')
        for bad in ('', '   ', 'ftp://1.2.3.4:21', 'http://1.2.3.4', 'http://:80', 'http://1.2.3.4:99999', 'http://a b:80',
                    'http://1.2.3.4:abc', 'socks4://u:p@1.2.3.4:1080', None):
            with self.assertRaises(ValueError, msg=bad):
                accountproxy.normalize(bad)

    def test_mask_hides_password_everywhere(self):
        self.assertEqual(accountproxy.mask(f'http://user:{PW}@1.2.3.4:8080'), 'http://user:*****@1.2.3.4:8080')
        self.assertEqual(accountproxy.mask('socks5://1.2.3.4:1080'), 'socks5://1.2.3.4:1080')
        self.assertEqual(accountproxy.mask(Proxy.parse(f'http://u:{PW}@h.example:81')), 'http://u:*****@h.example:81')
        self.assertEqual(accountproxy.mask(''), '')
        self.assertNotIn(PW, accountproxy.mask(Proxy('http', 'h', 1, 'u', PW)))

    def test_scrub_removes_known_and_url_passwords(self):
        accountproxy._remember(Proxy.parse(f'http://u:{PW}@1.2.3.4:80'))
        text = f'Chrome: net::ERR_TUNNEL via http://u:{PW}@1.2.3.4:80 und {PW} allein, quoted %s' % PW
        clean = accountproxy.scrub(text)
        self.assertNotIn(PW, clean)
        self.assertIn('*****', clean)
        self.assertNotIn('anderespw', accountproxy.scrub('http://u:anderespw@h:1/'))
        self.assertEqual(accountproxy.scrub(''), '')

    def test_log_filter_scrubs_records(self):
        accountproxy._remember(Proxy.parse(f'http://u:{PW}@1.2.3.4:80'))
        logger = logging.getLogger('test_scrub_filter')
        logger.propagate = False
        logger.setLevel(logging.INFO)
        stream = []

        class Capture(logging.Handler):
            def emit(self, record):
                stream.append(self.format(record))
        handler = Capture()
        handler.addFilter(accountproxy._ScrubFilter())
        logger.addHandler(handler); self.addCleanup(logger.removeHandler, handler)
        logger.info('Proxy %s kaputt, %s', f'http://u:{PW}@1.2.3.4:80', PW)
        self.assertEqual(len(stream), 1)
        self.assertNotIn(PW, stream[0])


class StorageTests(ProxyBase):
    def test_password_only_in_proxy_file_database_has_masked_display(self):
        shown = rotation.set_account_proxy(self.db, self.b['id'], URL % 8080)
        self.assertEqual(shown, 'http://proxyuser:*****@127.0.0.1:8080')
        self.assertEqual(repo.get_account(self.db, self.b['id'])['proxy'], shown)
        stored = json.loads((self.root / 'data' / accountproxy.FILE_NAME).read_text(encoding='utf-8'))
        self.assertEqual(stored['proxies'][self.b['identity']], URL % 8080)
        self.assertEqual(self.secret_scan(), [])
        self.assertEqual(accountproxy.load(self.db_path)[self.b['identity']], URL % 8080)

    def test_invalid_proxy_is_refused_and_nothing_is_stored(self):
        with self.assertRaises(ValueError):
            rotation.set_account_proxy(self.db, self.b['id'], 'kein proxy')
        self.assertIsNone(repo.get_account(self.db, self.b['id'])['proxy'])
        self.assertEqual(accountproxy.load(self.db_path), {})

    def test_remove_and_delete_account_remove_the_secret(self):
        rotation.set_account_proxy(self.db, self.b['id'], URL % 8080)
        rotation.set_account_proxy(self.db, self.c['id'], URL % 8081)
        rotation.clear_account_proxy(self.db, self.b['id'])
        self.assertIsNone(repo.get_account(self.db, self.b['id'])['proxy'])
        self.assertEqual(set(accountproxy.load(self.db_path)), {self.c['identity']})
        repo.delete_account(self.db, self.c['id'])
        self.assertEqual(accountproxy.load(self.db_path), {})

    def test_broken_file_is_treated_as_empty_but_flag_keeps_it_closed(self):
        rotation.set_account_proxy(self.db, self.b['id'], URL % 8080)
        (self.root / 'data' / accountproxy.FILE_NAME).write_text('{kaputt', encoding='utf-8')
        self.assertEqual(accountproxy.load(self.db_path), {})
        with self.assertRaises(AccountProxyError):
            accountproxy.Resolver(self.db_path).proxy_for(self.b['identity'])

    def test_export_api_backup_sync_never_contain_the_proxy(self):
        from socialscraper import api, formlogic, sync
        rotation.set_account_proxy(self.db, self.b['id'], URL % 8080)
        rotation.set_account_proxy(self.db, self.a['id'], 'http://u:%s@10.0.0.9:3128' % PW)
        # 1. API: nur has_proxy, nie die Adresse
        _status, body = None, api.ep_accounts(self.db, {}, ['v1', 'accounts'])
        text = json.dumps(body, ensure_ascii=False)
        for secret in (PW, '127.0.0.1', '10.0.0.9', 'proxyuser', 'http://'):
            self.assertNotIn(secret, text)
        self.assertEqual(sorted(i['has_proxy'] for i in body['items'] if i['platform'] == 'tiktok'), [False, True, True])
        self.assertNotIn('proxy', {k for i in body['items'] for k in i})
        # 2. Export (JSON und CSV)
        for fmt in ('jsonl', 'csv', 'csv-excel'):
            out = self.root / f'export_{fmt}'
            guicore.export_data(self.db_path, out, fmt)
        # 3. Sicherung der Datenbank
        formlogic.backup_database(self.db_path, self.root / 'sicherung.db')
        # 4. Cloud-Abgleich kennt keine Konten
        self.assertNotIn('accounts', sync.TABLES)
        self.assertEqual(self.secret_scan(), [])
        for path in self.root.rglob('*'):
            if path.is_file() and path.name != accountproxy.FILE_NAME:
                self.assertNotIn(b'proxyuser:' + PW.encode(), path.read_bytes())

    def test_gui_account_listing_shows_only_masked_proxy(self):
        rotation.set_account_proxy(self.db, self.b['id'], URL % 8080)
        rows = repo.list_accounts(self.db, 'tiktok')
        self.assertNotIn(PW, json.dumps(rows, default=str))


class IdentityProxyTests(ProxyBase):
    def manager(self, pool=None):
        pool = pool or Mock(acquire=Mock(side_effect=AssertionError('der Pool darf nicht benutzt werden')))
        im = IdentityManager(pool, profiles_dir=str(self.root / 'data' / 'profiles'))
        im.account_proxies = accountproxy.Resolver(self.db_path)
        return im, pool

    def test_fixed_proxy_is_used_and_pool_is_not_asked(self):
        server = self.web_proxy()
        rotation.set_account_proxy(self.db, self.b['id'], f'http://proxyuser:{PW}@127.0.0.1:{server.port}')
        im, pool = self.manager()
        proxy = im.proxy_for(im.get(self.b['identity'], 'de', False))
        self.assertEqual((proxy.host, proxy.port, proxy.user, proxy.password), ('127.0.0.1', server.port, 'proxyuser', PW))
        pool.acquire.assert_not_called()
        self.assertEqual(proxy.playwright['username'], 'proxyuser')      # so bekommt es der Browser

    def test_fixed_proxy_has_priority_over_list_and_off_mode_still_uses_it(self):
        server = self.web_proxy()
        rotation.set_account_proxy(self.db, self.b['id'], f'http://127.0.0.1:{server.port}')
        for mode, listed in (('liste', ['http://10.9.9.9:80']), ('aus', [])):
            cfg = Settings(db_path=str(self.db_path), profiles_dir=str(self.root / 'data' / 'profiles'),
                           proxy_mode=mode, proxies=listed, notify_file='')
            im, _backend, _sessions = cli.make_runtime(cfg, False)
            proxy = im.proxy_for(im.get(self.b['identity'], 'de', False))
            self.assertEqual((proxy.host, proxy.port), ('127.0.0.1', server.port), mode)
            other = im.proxy_for(im.get(self.a['identity'], 'de', False))       # Konto ohne festen Proxy: wie bisher
            self.assertEqual((other.host, other.port) if other else None, ('10.9.9.9', 80) if listed else None)

    def test_free_and_tor_modes_do_not_use_account_proxies_and_stay_locked(self):
        from socialscraper.net import proxymode
        for mode in ('frei', 'tor'):
            cfg = Settings(db_path=str(self.db_path), profiles_dir=str(self.root / 'data' / 'profiles'), proxy_mode=mode)
            with patch.object(proxymode, 'make_pool', return_value=Mock(anonymous=True)):
                im, _b, _s = cli.make_runtime(cfg, False)
            self.assertIsNone(im.account_proxies)
            with self.assertRaises(proxymode.AccountsForbidden):
                im.get(self.b['identity'], 'de', False)

    def test_dead_proxy_raises_clear_error_and_never_falls_back(self):
        rotation.set_account_proxy(self.db, self.b['id'], f'http://proxyuser:{PW}@127.0.0.1:{closed_port()}')
        im, pool = self.manager()
        with self.assertRaises(AccountProxyError) as caught:
            im.proxy_for(im.get(self.b['identity'], 'de', False))
        text = str(caught.exception)
        self.assertIn('nicht erreichbar', text)
        self.assertIn('NICHT ohne Proxy', text)
        self.assertNotIn(PW, text)
        self.assertFalse(caught.exception.retryable)
        pool.acquire.assert_not_called()

    def test_flag_without_secret_fails_closed(self):
        repo.set_account_proxy_ref(self.db, self.b['id'], 'http://proxyuser:*****@127.0.0.1:1')
        im, pool = self.manager()
        with self.assertRaisesRegex(AccountProxyError, 'Zugangsdaten fehlen'):
            im.proxy_for(im.get(self.b['identity'], 'de', False))
        pool.acquire.assert_not_called()

    def test_account_without_proxy_uses_normal_path(self):
        im, pool = self.manager(pool=Mock(acquire=Mock(return_value=None)))
        self.assertIsNone(im.proxy_for(im.get(self.a['identity'], 'de', False)))
        pool.acquire.assert_called_once()

    def test_reachability_check_is_short_cached(self):
        server = self.web_proxy()
        rotation.set_account_proxy(self.db, self.b['id'], f'http://127.0.0.1:{server.port}')
        im, _pool = self.manager()
        identity = im.get(self.b['identity'], 'de', False)
        im.proxy_for(identity)
        server.close()
        im.proxy_for(identity)             # innerhalb weniger Sekunden nicht erneut geprüft
        accountproxy._REACH.clear()
        with self.assertRaises(AccountProxyError):
            im.proxy_for(identity)

    def test_pool_session_acquire_uses_fixed_proxy(self):
        """Der Sitzungspool (Browserstart) bekommt den festen Proxy – und bei totem Proxy gar keinen Browser."""
        from socialscraper.net.pool import SessionPool
        server = self.web_proxy()
        rotation.set_account_proxy(self.db, self.b['id'], f'http://proxyuser:{PW}@127.0.0.1:{server.port}')
        im, _pool = self.manager()
        backend = Mock()
        backend.open.return_value = 'handle'
        sessions = SessionPool(backend, im, 2, 100, headless=True)
        with sessions.acquire(self.b['identity'], 'tiktok', 'de'):
            pass
        proxy = backend.open.call_args.args[1]
        self.assertEqual((proxy.host, proxy.port, proxy.password), ('127.0.0.1', server.port, PW))
        server.close(); accountproxy._REACH.clear(); sessions.shutdown()
        backend.open.reset_mock()
        with self.assertRaises(AccountProxyError):
            with sessions.acquire(self.b['identity'], 'tiktok', 'de'):
                pass
        backend.open.assert_not_called()


class ProxyTestFunctionTests(ProxyBase):
    def test_success_returns_exit_ip(self):
        getter = Mock(return_value=(200, '203.0.113.7\n', 0.25))
        ok, message, ip = rotation.test_proxy(f'http://u:{PW}@127.0.0.1:8080', getter=getter)
        self.assertEqual((ok, ip), (True, '203.0.113.7'))
        self.assertIn('Ausgangs-IP: 203.0.113.7', message)
        self.assertNotIn(PW, message)
        self.assertEqual(getter.call_args.args[1], rotation.IP_URL)
        self.assertTrue(rotation.IP_URL.startswith('https://'))

    def test_failures_have_clear_german_messages_without_password(self):
        from socialscraper.net import tunnel
        proxy = f'http://u:{PW}@127.0.0.1:8080'
        cases = [(tunnel.TlsBroken('x'), 'greift in die verschlüsselte Verbindung ein'),
                 (tunnel.TunnelError('Proxy lehnt CONNECT ab'), 'lehnt die Verbindung ab'),
                 (ConnectionRefusedError(), 'nicht erreichbar'), (TimeoutError(), 'nicht erreichbar')]
        for error, expected in cases:
            ok, message, ip = rotation.test_proxy(proxy, getter=Mock(side_effect=error))
            self.assertFalse(ok); self.assertIsNone(ip)
            self.assertIn(expected, message)
            self.assertNotIn(PW, message)
        ok, message, _ = rotation.test_proxy(proxy, getter=Mock(return_value=(200, 'kein ip', 0.1)))
        self.assertFalse(ok); self.assertIn('nicht wie erwartet', message)
        ok, message, _ = rotation.test_proxy('quatsch quatsch')
        self.assertFalse(ok)
        ok, message, _ = rotation.test_proxy('socks4://1.2.3.4:1080')
        self.assertFalse(ok); self.assertIn('nicht testen', message)

    def test_real_connection_attempt_against_local_fake_proxies(self):
        """Echter Verbindungsversuch durch tunnel.https_get (ohne Mock): Proxy verweigert / Port zu / Proxy lehnt ab."""
        refuse = FakeHttpConnect(status=b'407 Proxy Authentication Required')
        self.servers.append(refuse); self.addCleanup(refuse.close)
        ok, message, _ = rotation.test_proxy(f'http://u:{PW}@127.0.0.1:{refuse.port}', timeout=3)
        self.assertFalse(ok); self.assertIn('lehnt die Verbindung ab', message); self.assertNotIn(PW, message)
        ok, message, _ = rotation.test_proxy(f'http://u:{PW}@127.0.0.1:{closed_port()}', timeout=3)
        self.assertFalse(ok); self.assertIn('nicht erreichbar', message); self.assertNotIn(PW, message)

    def test_test_proxy_does_not_touch_accounts(self):
        before = [dict(r) for r in repo.list_accounts(self.db)]
        rotation.test_proxy('http://127.0.0.1:%d' % closed_port(), timeout=2)
        self.assertEqual(before, [dict(r) for r in repo.list_accounts(self.db)])


class FixedProxyRunTests(ProxyBase):
    """Planer + fester Proxy: toter Proxy -> klare Meldung, Abkühlung „Proxy nicht erreichbar“, kein Direktweg."""

    def setUp(self):
        super().setUp()
        self.cfg = Settings(db_path=str(self.db_path), notify_file='', profiles_dir=str(self.root / 'data' / 'profiles'),
                            rotation_every=5)
        self.pool = Mock(acquire=Mock(side_effect=AssertionError('kein Ausweichen auf Pool/Direktweg')))
        self.fetches = []
        self.urls = [f'https://www.tiktok.com/@a/video/{n}' for n in range(1, 4)]
        self.adapter = Mock(platform='tiktok', needs_browser=True, needs_auth=True)

        def fetch(url, ctx):
            ctx.im.proxy_for(ctx.im.get(ctx.identity, ctx.country, ctx.mobile))     # wie SessionPool beim Browserstart
            self.fetches.append((url.rsplit('/', 1)[-1], ctx.identity))
            return Video('tiktok', url.rsplit('/', 1)[-1], url, comment_count=0), []
        self.adapter.fetch.side_effect = fetch

        def runtime(cfg, need_browser):
            im = IdentityManager(self.pool, profiles_dir=cfg.profiles_dir)
            im.account_proxies = accountproxy.Resolver(cfg.db_path)
            return im, Mock(), Mock()
        for target, kwargs in ((scheduler.cli, dict(make_runtime=runtime, shutdown_runtime=Mock())),
                               (scheduler, dict(resolve=lambda url: self.adapter))):
            p = patch.multiple(target, **kwargs); p.start(); self.addCleanup(p.stop)
        p = patch.object(scheduler.cli, 'auth_state', return_value=(True, State.OK)); p.start(); self.addCleanup(p.stop)
        p = patch.object(scheduler.discovery, 'discover', side_effect=lambda *a: list(self.urls)); p.start(); self.addCleanup(p.stop)

    def run_target(self, **kw):
        t = repo.add_target(self.db, 'search', 'tiktok', f'k{len(repo.list_targets(self.db))}', max_comments=0, **kw)
        return scheduler.run_target(self.root, t['id'], 'manual', cfg=self.cfg)

    def test_default_account_with_dead_proxy_aborts_run_and_cools_account(self):
        rotation.set_account_proxy(self.db, self.a['id'], f'http://proxyuser:{PW}@127.0.0.1:{closed_port()}')
        with self.assertLogs(level='INFO') as logs:
            r = self.run_target()
            logging.getLogger('test').info('Ende')
        self.assertEqual(r['status'], 'failed')
        self.assertIn('Der feste Proxy dieses Kontos', r['error'])
        self.assertIn('NICHT ohne Proxy', r['error'])
        self.assertEqual(self.fetches, [])
        self.assertEqual(self.adapter.fetch.call_count, 1)                     # abgebrochen, nicht für jedes Video versucht
        self.assertEqual(repo.get_account(self.db, self.a['id'])['cooldown_reason'], 'Proxy nicht erreichbar')
        self.assertNotIn(PW, r['error'] + ' '.join(logs.output))
        self.pool.acquire.assert_not_called()
        self.assertEqual(self.secret_scan(), [])

    def test_rotation_skips_account_with_dead_proxy_and_continues_with_next(self):
        rotation.set_account_proxy(self.db, self.a['id'], f'http://127.0.0.1:{closed_port()}')
        good = self.web_proxy()
        rotation.set_account_proxy(self.db, self.b['id'], f'http://127.0.0.1:{good.port}')
        r = self.run_target(account_mode='rotate')
        self.assertEqual((r['status'], r['videos_seen']), ('ok', 3))
        self.assertEqual({identity for _v, identity in self.fetches}, {self.b['identity']})
        self.assertEqual(repo.get_account(self.db, self.a['id'])['cooldown_reason'], 'Proxy nicht erreichbar')
        until = repo.parse(repo.get_account(self.db, self.a['id'])['cooldown_until'])
        self.assertAlmostEqual((until - repo.now()).total_seconds(), 30 * 60, delta=5)
        self.assertEqual(r['accounts_used'], ['Hauptkonto', 'Zweit'])

    def test_fixed_account_uses_only_its_own_proxy(self):
        server = self.web_proxy()
        rotation.set_account_proxy(self.db, self.c['id'], f'http://127.0.0.1:{server.port}')
        r = self.run_target(account_id=self.c['id'])
        self.assertEqual(r['status'], 'ok')
        self.assertEqual({identity for _v, identity in self.fetches}, {self.c['identity']})
        self.pool.acquire.assert_not_called()


class DeadProxyMessageTests(ProxyBase):
    def test_clean_error_masks_known_proxy_password_in_foreign_error_texts(self):
        rotation.set_account_proxy(self.db, self.b['id'], URL % 8080)
        accountproxy.load(self.db_path)       # merkt sich das Passwort wie ein Lauf
        message = scheduler.clean_error(RuntimeError(f'net::ERR_PROXY_CONNECTION_FAILED proxy proxyuser {PW} at 127.0.0.1:8080'))
        self.assertNotIn(PW, message)

    def test_notify_payload_has_no_proxy_password(self):
        accountproxy._remember(Proxy.parse(URL % 8080))
        with self.assertLogs('socialscraper.net.notify', 'WARNING') as logs:
            notify.notify('x', accountproxy.scrub(f'Proxy http://proxyuser:{PW}@127.0.0.1:8080 kaputt'))
        self.assertNotIn(PW, ' '.join(logs.output))


@unittest.skipUnless(os.environ.get('SS_RUN_LOCAL_BROWSER_TESTS') == '1', 'SS_RUN_LOCAL_BROWSER_TESTS=1 nicht gesetzt')
class RealBrowserThroughFixedProxy(ProxyBase):
    """Echter, unsichtbarer Chrome (headless) mit dem festen Proxy des Kontos gegen einen lokalen Fake-Proxy."""

    def open_page(self, proxy):
        from socialscraper.backends.local import LocalBackend
        backend = LocalBackend('patchright', str(self.root / 'data' / 'profiles'))
        im = IdentityManager(ProxyPool([]), profiles_dir=str(self.root / 'data' / 'profiles'))
        im.account_proxies = accountproxy.Resolver(self.db_path)
        ident = im.get(self.b['identity'], 'de', False)
        handle = backend.open(ident, im.proxy_for(ident), headless=True, platform='tiktok')
        self.addCleanup(backend.close, handle)
        return backend.page(handle)

    def test_chrome_uses_only_the_fixed_proxy_including_password_login(self):
        server = self.web_proxy(auth=f'proxyuser:{PW}')
        rotation.set_account_proxy(self.db, self.b['id'], f'http://proxyuser:{PW}@127.0.0.1:{server.port}')
        page = self.open_page(None)
        page.goto('http://seite.beispiel.invalid/test', wait_until='domcontentloaded')
        self.assertIn('via-festen-proxy', page.content())
        lines = [line for line, _h in server.requests]
        self.assertTrue(any(line.startswith('GET http://seite.beispiel.invalid/test') for line in lines), lines)
        authed = [h for line, h in server.requests if 'proxy-authorization' in h]
        self.assertTrue(authed)                                    # Anmeldung am Proxy hat der Browser selbst gemacht

    def test_chrome_never_goes_direct_when_fixed_proxy_is_gone(self):
        server = self.web_proxy()
        rotation.set_account_proxy(self.db, self.b['id'], f'http://127.0.0.1:{server.port}')
        im = IdentityManager(ProxyPool([]), profiles_dir=str(self.root / 'data' / 'profiles'))
        im.account_proxies = accountproxy.Resolver(self.db_path)
        proxy = im.proxy_for(im.get(self.b['identity'], 'de', False))    # Prüfung bestanden, danach geht der Proxy aus
        server.close()
        from socialscraper.backends.local import LocalBackend
        backend = LocalBackend('patchright', str(self.root / 'data' / 'profiles'))
        ident = im.get(self.b['identity'], 'de', False)
        handle = backend.open(ident, proxy, headless=True, platform='tiktok')
        self.addCleanup(backend.close, handle)
        with self.assertRaises(Exception) as caught:
            backend.page(handle).goto('http://seite.beispiel.invalid/test', wait_until='domcontentloaded', timeout=15000)
        self.assertIn('PROXY', str(caught.exception).upper())


if __name__ == '__main__':
    unittest.main()
