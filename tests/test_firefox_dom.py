"""Opt-in real Firefox tests: synthetic content and owned loopback servers only."""
import contextlib
import http.server
import os
from pathlib import Path
import random
import select
import socket
import socketserver
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from socialscraper.backends.firefox import FirefoxBackend
from socialscraper.net.fingerprint import build
from socialscraper.net.proxy import Proxy
from socialscraper.net import humanize


class _Loopback:
    """An upstream proxy that only forwards our fixture name to our own origin."""
    def __init__(self):
        self.requests, self.targets = [], []
        owner = self

        class Origin(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                body = b'<!doctype html><body><p id="result">LOCAL_FIXTURE</p></body>'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class ProxyHandler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                data = b''
                while b'\r\n\r\n' not in data:
                    chunk = self.request.recv(4096)
                    if not chunk or len(data) > 8192:
                        return
                    data += chunk
                method, target, _ = data.split(b'\r\n', 1)[0].decode('ascii').split(' ', 2)
                allowed = {f'firefox-r47.test:{owner.port}', f'127.0.0.1:{owner.port}'}
                if method != 'CONNECT' or target not in allowed:
                    self.request.sendall(b'HTTP/1.1 502 Test boundary\r\nContent-Length:0\r\n\r\n')
                    return
                owner.targets.append(target)
                with socket.create_connection(('127.0.0.1', owner.port), timeout=3) as remote:
                    self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                    while True:
                        ready, _, _ = select.select([self.request, remote], [], [], 3)
                        if not ready:
                            return
                        for source in ready:
                            chunk = source.recv(65536)
                            if not chunk:
                                return
                            (remote if source is self.request else self.request).sendall(chunk)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = False

        self.origin = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Origin)
        self.port = self.origin.server_port
        self.upstream = Server(('127.0.0.1', 0), ProxyHandler)
        self.proxy = Proxy('http', '127.0.0.1', self.upstream.server_address[1], anonymous=True)
        self.threads = []
        for server in (self.origin, self.upstream):
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.upstream_closed = False

    def stop_upstream(self):
        if not self.upstream_closed:
            self.upstream.shutdown()
            self.upstream.server_close()
            self.upstream_closed = True

    def close(self):
        self.stop_upstream()
        self.origin.shutdown()
        self.origin.server_close()
        for thread in self.threads:
            thread.join(timeout=2)


@unittest.skipUnless(os.environ.get('SS_RUN_FIREFOX_DOM') == '1', 'isolated Firefox fixture disabled')
class FirefoxDOM(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='firefox-fixture-')))
        self.network = _Loopback()
        self.stack.callback(self.network.close)
        self.backend = FirefoxBackend(self.root / 'profiles')
        self.identity = SimpleNamespace(name='offline_firefox', fp=build('offline_firefox', 'de'))

    @contextlib.contextmanager
    def page(self, platform='mail'):
        with self.backend.session(self.identity, self.network.proxy, headless=True, platform=platform) as handle:
            page = self.backend.page(handle)
            page.set_default_timeout(3000)
            page.set_default_navigation_timeout(5000)
            yield page, handle

    def test_native_firefox_form_and_delayed_content_with_paced_mouse(self):
        with self.page() as (page, handle):
            self.assertEqual(handle.backend, 'firefox')
            user_agent = page.evaluate('navigator.userAgent')
            self.assertIn('Firefox/157.', user_agent)
            self.assertNotIn('Chrome/', user_agent)
            self.assertTrue(page.evaluate('navigator.language').startswith('de'))
            self.assertIsNone(page.evaluate('typeof RTCPeerConnection === "undefined" ? null : true'))
            page.set_content('''<input id="name"><button id="next">Weiter</button><p id="result"></p>
                <script>document.querySelector('#next').onclick = () => setTimeout(() => {
                    document.querySelector('#result').textContent = document.querySelector('#name').value;
                }, 100);</script>''')
            page.locator('#name').fill('OFFLINE_FIXTURE')
            page.locator('#next').click()
            self.assertEqual(humanize.wait_for(page, lambda: page.locator('#result').text_content(), timeout=2),
                             'OFFLINE_FIXTURE')
            self.assertTrue(humanize.move(page, 120, 150, rng=random.Random(2), duration=100))

    def test_destination_name_goes_through_proxy_and_dead_proxy_cannot_bypass_loopback(self):
        from playwright.sync_api import Error
        with self.page() as (page, _):
            response = page.goto(f'http://firefox-r47.test:{self.network.port}/via-proxy')
            self.assertEqual(response.status, 200)
            self.assertEqual(page.locator('#result').text_content(), 'LOCAL_FIXTURE')
            self.assertIn(f'firefox-r47.test:{self.network.port}', self.network.targets)
            self.network.stop_upstream()
            before = list(self.network.requests)
            try:
                response = page.goto(f'http://127.0.0.1:{self.network.port}/must-not-be-direct')
            except Error:
                response = None
            self.assertTrue(response is None or response.status >= 400)
            self.assertFalse(page.is_closed())
            self.assertEqual(self.network.requests, before)

    def test_persistent_cookies_are_scoped_by_platform_and_leave_chrome_profile_untouched(self):
        chrome = self.root / 'profiles' / self.identity.name / 'mail'
        chrome.mkdir(parents=True)
        sentinel = chrome / 'untouched.txt'
        sentinel.write_text('CHROME_FIXTURE', encoding='utf8')
        with self.page() as (_, handle):
            handle._context.add_cookies([{'name': 'fixture', 'value': 'SYNTHETIC',
                'domain': 'firefox-r47.test', 'path': '/', 'expires': time.time() + 3600}])
        with self.page() as (_, handle):
            self.assertTrue(any(cookie['name'] == 'fixture' for cookie in self.backend.read_cookies(handle)))
        with self.page('tiktok') as (_, handle):
            self.assertFalse(any(cookie['name'] == 'fixture' for cookie in self.backend.read_cookies(handle)))
        self.assertEqual(sentinel.read_text(encoding='utf8'), 'CHROME_FIXTURE')

    def test_cancellation_during_real_wait_prevents_another_read(self):
        with self.page() as (page, _):
            page.set_content('<p id="result"></p>')
            stopped = threading.Event()
            reads = []
            original = page.wait_for_timeout

            def wait(milliseconds):
                original(milliseconds)
                stopped.set()

            def read():
                reads.append(True)
                return page.locator('#result').text_content()

            with patch.object(page, 'wait_for_timeout', side_effect=wait):
                self.assertIsNone(humanize.wait_for(page, read, stop=stopped.is_set))
            self.assertEqual(reads, [True])


if __name__ == '__main__':
    unittest.main()
