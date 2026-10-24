"""Kostenlose Proxys: Listen lesen, testen, zwischenspeichern, rotieren – ohne echtes Netz."""
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch

from socialscraper.net import freeproxy, tunnel

LIST_A = "1.1.1.1:80\n# Kommentar\n\n2.2.2.2:8080\n192.168.0.5:3128\n127.0.0.1:9\nsocks4://3.3.3.3:1080\n1.1.1.1:80\nfoo\n"


class ParseTests(unittest.TestCase):
    def test_parse_formats_and_filters(self):
        self.assertEqual(freeproxy.parse_list(LIST_A, 'http'), ['http://1.1.1.1:80', 'http://2.2.2.2:8080'])
        self.assertEqual(freeproxy.parse_list('socks5://4.4.4.4:1080\nhttp://5.5.5.5:3128\n6.6.6.6:1\n', 'socks5'),
                         ['socks5://4.4.4.4:1080', 'http://5.5.5.5:3128', 'socks5://6.6.6.6:1'])
        self.assertEqual(freeproxy.parse_list('user:pw@8.8.8.8:80\n', 'http'), [])
        self.assertEqual(freeproxy.parse_list(None, 'http'), [])


class FreeProxyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        freeproxy.clear_memory()
        self.addCleanup(freeproxy.clear_memory)
        self.lists = {'u1': '\n'.join(f'10.0.{i}.1:80' for i in range(1, 7)).replace('10.0', '8.8'),
                      'u2': '9.9.9.1:1080\n9.9.9.2:1080'}
        self.sources = [('a', 'u1', 'http'), ('b', 'u2', 'socks5')]
        self.speeds = {}

    def fetch(self, url):
        if url not in self.lists:
            raise OSError('weg')
        return self.lists[url]

    def fake_probe(self, url):
        info = self.speeds.get(url, {'ok': False, 'error': 'timeout'})
        return info

    def refresh(self, **kw):
        return freeproxy.refresh(self.root, fetch=self.fetch, tester=self.fake_probe, sources=self.sources,
                                 workers=4, **kw)


class RefreshTests(FreeProxyCase):
    def test_keeps_fastest_n_and_writes_cache(self):
        self.speeds = {'http://8.8.1.1:80': {'ok': True, 'ms': 900, 'ip': '8.8.1.1'},
                       'http://8.8.2.1:80': {'ok': True, 'ms': 100, 'ip': '8.8.2.1'},
                       'socks5://9.9.9.1:1080': {'ok': True, 'ms': 300, 'ip': '9.9.9.1'},
                       'http://8.8.3.1:80': {'ok': False, 'error': 'proxy'}}
        stats = self.refresh(limit=2)
        self.assertEqual((stats['kandidaten'], stats['getestet'], stats['funktionierend']), (8, 8, 3))
        self.assertEqual([item['ms'] for item in stats['schnellste']], [100, 300])
        data = json.loads(freeproxy.cache_path(self.root).read_text(encoding='utf-8'))
        self.assertEqual([p['url'] for p in data['proxies']], ['http://8.8.2.1:80', 'socks5://9.9.9.1:1080'])
        self.assertAlmostEqual(data['fetched_at'], time.time(), delta=10)
        self.assertEqual(list(freeproxy.cache_path(self.root).parent.glob('.freeproxies-*')), [])  # atomar, kein Rest

    def test_failing_source_does_not_stop_refresh(self):
        self.sources.append(('c', 'kaputt', 'http'))
        self.speeds = {'http://8.8.1.1:80': {'ok': True, 'ms': 50, 'ip': 'x'}}
        stats = self.refresh()
        self.assertEqual(stats['quellen_ok'], 2)
        self.assertEqual(len(stats['quellen_fehler']), 1)
        self.assertEqual(stats['funktionierend'], 1)

    def test_tls_breaking_proxy_is_discarded_banned_and_list_marked(self):
        self.speeds = {'http://8.8.1.1:80': {'ok': False, 'error': 'tls'},
                       'http://8.8.2.1:80': {'ok': True, 'ms': 10, 'ip': 'x'}}
        stats = self.refresh()
        self.assertEqual(stats['zertifikatsfehler'], 1)
        self.assertEqual(stats['funktionierend'], 1)
        self.assertEqual(len(stats['verdaechtige_quellen']), 1)
        self.assertIn('a', stats['verdaechtige_quellen'][0])
        data = json.loads(freeproxy.cache_path(self.root).read_text(encoding='utf-8'))
        self.assertIn('http://8.8.1.1:80', data['banned'])
        self.assertEqual(data['sources']['a']['tls_funde'], 1)
        # Beim nächsten Mal wird der Zertifikats-Proxy nicht mehr getestet.
        stats = self.refresh()
        self.assertEqual(stats['kandidaten'], 7)

    def test_source_with_many_tls_findings_is_skipped_next_time(self):
        self.speeds = {f'http://8.8.{i}.1:80': {'ok': False, 'error': 'tls'} for i in range(1, 5)}
        self.refresh()
        stats = self.refresh()
        self.assertIn('a', stats['quellen_uebersprungen'])

    def test_probe_classifies_errors(self):
        with patch.object(tunnel, 'https_get', side_effect=tunnel.TlsBroken('x')):
            self.assertEqual(freeproxy.probe('http://1.2.3.4:80')['error'], 'tls')
        with patch.object(tunnel, 'https_get', side_effect=TimeoutError()):
            self.assertEqual(freeproxy.probe('http://1.2.3.4:80')['error'], 'timeout')
        with patch.object(tunnel, 'https_get', side_effect=tunnel.TunnelError('x')):
            self.assertEqual(freeproxy.probe('http://1.2.3.4:80')['error'], 'proxy')
        with patch.object(tunnel, 'https_get', return_value=(200, 'kein ip', 0.1)):
            self.assertEqual(freeproxy.probe('http://1.2.3.4:80')['error'], 'antwort')
        with patch.object(tunnel, 'https_get', return_value=(200, '5.6.7.8', 0.25)):
            result = freeproxy.probe('http://1.2.3.4:80', real_ip='9.9.9.9')
        self.assertEqual((result['ok'], result['ms'], result['ip']), (True, 250, '5.6.7.8'))
        with patch.object(tunnel, 'https_get', return_value=(200, '9.9.9.9', 0.1)):
            self.assertEqual(freeproxy.probe('http://1.2.3.4:80', real_ip='9.9.9.9')['error'], 'gleiche-ip')


class AcquireTests(FreeProxyCase):
    def setUp(self):
        super().setUp()
        self.speeds = {'http://8.8.1.1:80': {'ok': True, 'ms': 100, 'ip': 'a'},
                       'http://8.8.2.1:80': {'ok': True, 'ms': 200, 'ip': 'b'},
                       'socks5://9.9.9.1:1080': {'ok': True, 'ms': 300, 'ip': 'c'}}
        self.refresh()

    def test_acquire_uses_fresh_cache_without_network(self):
        with patch.object(freeproxy, 'refresh', side_effect=AssertionError('kein Netz erwartet')):
            proxy = freeproxy.acquire(self.root)
        self.assertEqual(proxy.server, 'http://8.8.1.1:80')
        self.assertTrue(proxy.anonymous)

    def test_failure_bans_and_rotates_to_next(self):
        first = freeproxy.acquire(self.root, auto_refresh=False)
        freeproxy.report_failure(first, self.root)
        second = freeproxy.acquire(self.root, auto_refresh=False)
        self.assertEqual(second.server, 'http://8.8.2.1:80')
        freeproxy.report_failure(second, self.root)
        third = freeproxy.acquire(self.root, auto_refresh=False)
        self.assertEqual((third.scheme, third.port), ('socks5', 1080))
        freeproxy.report_failure(third, self.root)
        self.assertIsNone(freeproxy.acquire(self.root, auto_refresh=False))
        data = json.loads(freeproxy.cache_path(self.root).read_text(encoding='utf-8'))
        self.assertEqual(data['proxies'], [])
        self.assertEqual(len(data['banned']), 3)

    def test_expired_cache_triggers_refresh(self):
        path = freeproxy.cache_path(self.root)
        data = json.loads(path.read_text(encoding='utf-8'))
        data['fetched_at'] = time.time() - freeproxy.VALID_SECONDS - 5
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assertFalse(freeproxy.status(self.root)['gueltig'])
        calls = []
        with patch.object(freeproxy, 'refresh', side_effect=lambda root, **kw: calls.append(root)):
            freeproxy.acquire(self.root)
        self.assertEqual(calls, [self.root])

    def test_nothing_found_returns_none_and_status_reports_it(self):
        self.speeds = {}
        self.refresh()
        self.assertIsNone(freeproxy.acquire(self.root, auto_refresh=False))
        info = freeproxy.status(self.root)
        self.assertEqual((info['vorhanden'], info['gueltig']), (0, False))

    def test_status_counts_health(self):
        proxy = freeproxy.acquire(self.root, auto_refresh=False)
        freeproxy.report_success(proxy, self.root)
        info = freeproxy.status(self.root)
        self.assertEqual(info['proxys'][0]['ok'], 1)
        self.assertEqual(info['vorhanden'], 3)

    def test_corrupt_cache_is_treated_as_empty(self):
        freeproxy.cache_path(self.root).write_text('{kaputt', encoding='utf-8')
        self.assertIsNone(freeproxy.acquire(self.root, auto_refresh=False))
        self.assertEqual(freeproxy.status(self.root)['vorhanden'], 0)

    def test_no_secrets_or_own_ip_in_cache(self):
        text = freeproxy.cache_path(self.root).read_text(encoding='utf-8')
        self.assertNotIn('password', text.lower())


if __name__ == '__main__':
    unittest.main()
