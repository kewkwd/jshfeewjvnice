"""Tor-Erkennung: nur lokale Attrappen, kein echtes Netz."""
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from netfakes import FakeControl, FakeHttpConnect, FakeSocks5
from socialscraper.net import proxymode, tor, tunnel
from socialscraper.net.proxy import Proxy


class TorDetectTests(unittest.TestCase):
    def test_detects_socks5_server_and_ignores_closed_port(self):
        server = FakeSocks5(); self.addCleanup(server.close)
        self.assertTrue(tor.is_socks5(server.port))
        self.assertEqual(tor.detect(ports=(1, server.port)), server.port)
        self.assertFalse(tor.is_socks5(1))
        self.assertIsNone(tor.detect(ports=(1,)))

    def test_not_running_gives_clear_german_message_without_crash(self):
        with patch.object(tor, 'detect', return_value=None):
            result = tor.find()
        self.assertFalse(result['ok'])
        self.assertIn('Tor Browser', result['message'])
        self.assertIn('offen lassen', result['message'])
        self.assertIn('nichts automatisch heruntergeladen', result['message'])

    def test_check_reads_istor(self):
        good = (200, '{"IsTor":true,"IP":"203.0.113.5"}', 1.2)
        with patch.object(tunnel, 'https_get', return_value=good) as get:
            result = tor.check(9150)
        self.assertTrue(result['ok'] and result['is_tor'])
        proxy, url = get.call_args.args[:2]
        self.assertEqual((proxy.scheme, proxy.host, proxy.port), ('socks5', '127.0.0.1', 9150))
        self.assertEqual(url, 'https://check.torproject.org/api/ip')
        with patch.object(tunnel, 'https_get', return_value=(200, '{"IsTor":false,"IP":"1.1.1.1"}', 1.0)):
            result = tor.check(9150)
        self.assertFalse(result['ok'])
        self.assertIn('kein Tor', result['message'])

    def test_check_failures_do_not_raise(self):
        with patch.object(tunnel, 'https_get', side_effect=OSError('x')):
            self.assertFalse(tor.check(9150)['ok'])
        with patch.object(tunnel, 'https_get', side_effect=tunnel.TlsBroken('x')):
            self.assertIn('Zertifikat', tor.check(9150)['message'])

    def test_proxy_object_and_remote_dns_url(self):
        proxy = tor.proxy(9150)
        self.assertEqual(proxy.server, 'socks5://127.0.0.1:9150')
        self.assertTrue(proxy.anonymous)
        self.assertEqual(tor.ytdlp_url(9150), 'socks5h://127.0.0.1:9150')
        self.assertEqual(proxymode.ytdlp_proxy_url(proxy), 'socks5h://127.0.0.1:9150')

    def test_pool_raises_instead_of_going_direct(self):
        with patch.object(tor, 'detect', return_value=None), self.assertRaises(proxymode.ProxyUnavailable) as caught:
            proxymode.TorPool().acquire('anon_public')
        self.assertIn('NICHT ohne Tor', str(caught.exception))

    def test_pool_returns_proxy_once_verified(self):
        pool = proxymode.TorPool()
        with patch.object(tor, 'detect', return_value=9050), \
             patch.object(tor, 'check', return_value={'ok': True, 'is_tor': True, 'port': 9050, 'message': 'ok'}):
            first = pool.acquire('anon_public')
        self.assertEqual(first.port, 9050)
        self.assertIs(pool.acquire('anon_public'), first)


class TunnelTests(unittest.TestCase):
    def test_socks5_sends_hostname_to_proxy_not_local_dns(self):
        server = FakeSocks5(); self.addCleanup(server.close)
        sock = tunnel.open_tunnel(Proxy('socks5', '127.0.0.1', server.port), 'check.torproject.org', 443, timeout=3)
        sock.close()
        self.assertIn(('ziel', 3, 'check.torproject.org'), server.seen)

    def test_http_connect_and_refusal(self):
        server = FakeHttpConnect(); self.addCleanup(server.close)
        tunnel.open_tunnel(Proxy('http', '127.0.0.1', server.port), 'example.org', 443, timeout=3).close()
        self.assertEqual(server.seen, ['CONNECT example.org:443 HTTP/1.1'])
        bad = FakeHttpConnect(b'403 Forbidden'); self.addCleanup(bad.close)
        with self.assertRaises(tunnel.TunnelError):
            tunnel.open_tunnel(Proxy('http', '127.0.0.1', bad.port), 'example.org', 443, timeout=3)

    def test_certificate_failure_is_reported_as_broken_tls(self):
        import ssl
        server = FakeSocks5(); self.addCleanup(server.close)
        with patch('ssl.SSLContext.wrap_socket', side_effect=ssl.SSLCertVerificationError('bad')):
            with self.assertRaises(tunnel.TlsBroken):
                tunnel.https_get(Proxy('socks5', '127.0.0.1', server.port), 'https://example.org/', timeout=3)

    def test_plain_http_url_is_refused(self):
        with self.assertRaises(ValueError):
            tunnel.https_get(None, 'http://example.org/')


class NewCircuitTests(unittest.TestCase):
    def test_newnym_only_without_password(self):
        ok = FakeControl(); self.addCleanup(ok.close)
        self.assertTrue(tor.new_circuit(ports=(ok.port,)))
        self.assertIn(b'NEWNYM', ok.seen[1])
        locked = FakeControl(accept_empty_password=False); self.addCleanup(locked.close)
        self.assertFalse(tor.new_circuit(ports=(locked.port,)))
        self.assertFalse(tor.new_circuit(ports=(1,)))


ROOT = pathlib.Path(__file__).resolve().parent.parent
NETWORK = bool(os.environ.get('SS_RUN_LOCAL_BROWSER_TESTS') or os.environ.get('SS_RUN_NETWORK_TESTS'))


class TorStartTests(unittest.TestCase):
    def test_missing_bundle_names_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(tor, 'detect', return_value=None):
            result = tor.ensure_running(tmp)
        self.assertFalse(result['ok'])
        self.assertIn('tools', result['message'])
        self.assertIn(pathlib.Path(tmp).name, result['message'])

    def test_existing_tor_is_used_without_starting(self):
        with patch.object(tor, 'detect', return_value=9150),              patch.object(tor, 'check', return_value={'ok': True, 'message': 'ok'}),              patch('subprocess.Popen') as popen:
            result = tor.ensure_running('x')
        self.assertEqual((result['ok'], result['port'], result['started']), (True, 9150, False))
        popen.assert_not_called()

    def test_torrc_is_created_only_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tor.write_torrc(tmp)
            text = path.read_text(encoding='utf-8')
            for line in ('SocksPort 127.0.0.1:9050', 'DataDirectory data/tor', 'ClientOnly 1',
                         'GeoIPFile tools/tor/data/geoip', 'GeoIPv6File tools/tor/data/geoip6'):
                self.assertIn(line, text)
            path.write_text('eigene', encoding='utf-8')
            self.assertEqual(tor.write_torrc(tmp).read_text(encoding='utf-8'), 'eigene')

    def test_stop_without_pid_file_kills_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, message = tor.stop(tmp)
        self.assertFalse(ok)
        self.assertIn('kein von Socialscraper gestartetes Tor', message)

    @unittest.skipUnless(os.environ.get('SS_RUN_TOR_TESTS') == '1' and tor.tor_exe(ROOT).exists(),
                         'echter Torstart (dauert bis zu mehreren Minuten, hängt vom Tor-Netz ab): nur mit SS_RUN_TOR_TESTS=1')
    def test_real_start_and_stop_on_own_port(self):
        port = 9061
        self.assertFalse(tor.is_socks5(port))
        with tempfile.TemporaryDirectory() as tmp:
            data = pathlib.Path(tmp) / 'tordata'
            result = tor.ensure_running(ROOT, port=port, data_dir=str(data), torrc=pathlib.Path(tmp) / 'torrc',
                                        ports=(port,), timeout=240)
            try:
                self.assertTrue(result['ok'], result['message'])
                self.assertTrue(result['started'])
                self.assertTrue(tor.is_socks5(port))
                self.assertTrue((data / 'tor.pid').exists())
            finally:
                ok, _ = tor.stop(ROOT, data_dir=str(data))
            self.assertTrue(ok)
            self.assertFalse(tor.is_socks5(port))
            self.assertFalse((data / 'tor.pid').exists())


if __name__ == '__main__':
    unittest.main()
