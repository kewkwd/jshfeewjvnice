"""Offline regressions for account-bound mailbox routing (rounds 27-29).

External sockets and DNS are blocked. Peers are test doubles or loopback-only
servers; no GUI, provider account or disposable mailbox is opened by this suite.
"""
import contextlib
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import accountvault, mailbox, signup, streuung
from socialscraper.config import Settings
from socialscraper.net import tunnel
from socialscraper.net.proxy import Proxy
from socialscraper.store import Store


class _ReachedBrowser(RuntimeError):
    pass


class MailboxProxyTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'SS_REQUIRE_PROXY': '0'}))
        for target in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(
                socket, target, side_effect=AssertionError('Unexpected direct networking')))
        self.stack.enter_context(patch.object(
            socket.socket, 'connect', side_effect=AssertionError('Unexpected real socket')))
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.db = self.root / 'data/test.sqlite'
        self.cfg = Settings(db_path=str(self.db), profiles_dir=str(self.root / 'data/profiles'))
        from profile_fixture import prepare
        prepare(self.cfg, 'proxy-mail-test', 'invalid-proxy-test')
        with Store(self.db):
            pass
        self.proxy = Proxy('socks5', 'proxy.example.test', 1080, 'test-user', 'test-password')

    @contextlib.contextmanager
    def imap_peer(self, *, tunnel_error=None, tls_error=None):
        """Keep real IMAP socket selection/TLS setup, replace only protocol I/O."""
        raw, secure = MagicMock(name='proxy-socket'), MagicMock(name='tls-socket')

        def init(connection, host='', port=143, timeout=None):
            connection.host, connection.port = host, port
            connection.sock = connection._create_socket(timeout)

        with contextlib.ExitStack() as stack:
            route = stack.enter_context(patch.object(
                tunnel, 'open_tunnel', return_value=raw, side_effect=tunnel_error))
            wrap = stack.enter_context(patch.object(
                ssl.SSLContext, 'wrap_socket', autospec=True,
                return_value=secure, side_effect=tls_error))
            stack.enter_context(patch.object(mailbox.imaplib.IMAP4, '__init__', init))
            stack.enter_context(patch.object(mailbox.imaplib.IMAP4, 'login', return_value=('OK', [])))
            stack.enter_context(patch.object(mailbox.imaplib.IMAP4, 'select', return_value=('OK', [])))
            stack.enter_context(patch.object(mailbox.imaplib.IMAP4, 'search', return_value=('OK', [b''])))
            logout = stack.enter_context(patch.object(mailbox.imaplib.IMAP4, 'logout'))
            yield SimpleNamespace(raw=raw, secure=secure, route=route, wrap=wrap, logout=logout)

    def test_signup_pool_mailbox_uses_the_selected_account_proxy(self):
        accountvault.add_postfach(self.db, {'adresse': 'pool@web.de', 'passwort': 'test-only'})
        manager = MagicMock()
        manager.proxy_for.return_value = self.proxy
        module = SimpleNamespace(REGISTER_URL='https://registration.example.test')

        def inspect_context(_module, ctx, _person, selected_proxy, _window):
            # The inbox preflight must have used the selected tunnel already.
            peer.route.assert_called_once_with(self.proxy, 'imap.web.de', 993, timeout=20)
            self.assertIs(selected_proxy, self.proxy)
            self.assertIsInstance(ctx.box, mailbox.ImapBox)
            self.assertEqual(ctx.box.address(), 'pool@web.de')
            self.assertEqual(ctx.box.fetch(), [])
            raise _ReachedBrowser()

        with self.imap_peer() as peer, \
                patch.object(signup, 'MODULE', {'reddit': module}), \
                patch.object(signup, 'precheck_local_proxy') as precheck, \
                patch.object(streuung, 'wartezeit_bis_registrierung', return_value=0), \
                patch.object(signup, '_browser_lauf', side_effect=inspect_context):
            with self.assertRaises(_ReachedBrowser):
                signup.create(self.cfg, 'reddit', email='pool@web.de', name='proxy-mail-test',
                              backend=MagicMock(), im=manager, warmup='aus')
        precheck.assert_called_once_with(self.proxy)
        self.assertEqual(peer.route.call_count, 2)
        self.assertEqual(peer.route.call_args_list[0], peer.route.call_args_list[1])

    def test_failed_proxy_precheck_prevents_disposable_mailbox_creation(self):
        accountvault.set_postfach(self.db, {'art': 'wegwerf'})
        manager = MagicMock()
        manager.proxy_for.return_value = self.proxy
        module = SimpleNamespace(REGISTER_URL='https://registration.example.test')
        temporary = MagicMock()
        temporary.address.return_value = 'created@example.test'
        with patch.object(signup, 'MODULE', {'reddit': module}), \
                patch.object(signup, 'precheck_local_proxy', side_effect=ValueError('Invalid proxy')), \
                patch.object(mailbox.TempMailBox, 'create', return_value=temporary) as create, \
                patch.object(signup, '_browser_lauf') as browser:
            with self.assertRaisesRegex(ValueError, 'Invalid proxy'):
                signup.create(self.cfg, 'reddit', name='invalid-proxy-test', backend=MagicMock(),
                              im=manager, warmup='aus')
        create.assert_not_called()
        browser.assert_not_called()

    def test_imap_explicit_proxy_keeps_remote_hostname_and_verified_tls(self):
        box = mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only', proxy=self.proxy)
        with self.imap_peer() as peer:
            self.assertEqual(box.fetch(), [])
        peer.route.assert_called_once_with(self.proxy, 'imap.example.test', 993, timeout=20)
        peer.wrap.assert_called_once()
        context, raw = peer.wrap.call_args.args
        self.assertIs(raw, peer.raw)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(peer.wrap.call_args.kwargs['server_hostname'], 'imap.example.test')
        peer.logout.assert_called_once()

    def test_imap_tls_failure_closes_tunnel_without_direct_retry(self):
        box = mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only', proxy=self.proxy)
        with self.imap_peer(tls_error=ssl.SSLCertVerificationError('Untrusted certificate')) as peer:
            with self.assertRaises(mailbox.MailboxError):
                box.fetch()
        peer.route.assert_called_once()
        peer.raw.close.assert_called_once()
        peer.logout.assert_not_called()

    def test_imap_tunnel_failure_does_not_fall_back_to_direct_connection(self):
        box = mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only', proxy=self.proxy)
        with self.imap_peer(tunnel_error=tunnel.TunnelError('Proxy unavailable')) as peer:
            with self.assertRaises(mailbox.MailboxError):
                box.fetch()
        peer.route.assert_called_once()
        peer.wrap.assert_not_called()
        peer.logout.assert_not_called()

    def test_new_disposable_mailbox_keeps_one_proxy_for_create_login_and_read(self):
        replies = [
            {'hydra:member': [{'domain': 'example.test'}]}, {}, {'token': 'test-token'},
            {'hydra:member': [{'id': 'one', 'createdAt': '2026-10-15T00:00:00Z'}]},
            {'subject': 'Test', 'text': 'One message', 'from': {'address': 'sender@example.test'}},
        ]
        with patch.object(mailbox, '_request', side_effect=replies) as request:
            box = mailbox.build({'art': 'wegwerf', 'basis': 'https://mail.example.test'}, proxy=self.proxy)
            messages = box.fetch()
        self.assertEqual([call.args[0] for call in request.call_args_list], [
            'https://mail.example.test/domains', 'https://mail.example.test/accounts',
            'https://mail.example.test/token', 'https://mail.example.test/messages?page=1',
            'https://mail.example.test/messages/one'])
        self.assertTrue(all(call.kwargs.get('proxy') is self.proxy for call in request.call_args_list))
        self.assertEqual(messages[0]['text'], 'One message')

    def test_existing_disposable_mailbox_receives_proxy_without_creating_an_account(self):
        cfg = {'art': 'wegwerf', 'adresse': 'existing@example.test', 'passwort': 'test-only',
               'basis': 'https://mail.example.test'}
        with patch.object(mailbox, '_request', side_effect=[
                {'token': 'test-token'}, {'hydra:member': []}]) as request:
            box = mailbox.build(cfg, proxy=self.proxy)
            self.assertEqual(box.fetch(), [])
        self.assertEqual([call.args[0] for call in request.call_args_list], [
            'https://mail.example.test/token', 'https://mail.example.test/messages?page=1'])
        self.assertTrue(all(call.kwargs.get('proxy') is self.proxy for call in request.call_args_list))

    def test_collection_formats_support_creation_and_message_details(self):
        wrappers = {'legacy_hydra': lambda rows: {'hydra:member': rows},
                    'plain_list': lambda rows: rows,
                    'member': lambda rows: {'member': rows}}
        for name, wrap in wrappers.items():
            with self.subTest(format=name):
                replies = [wrap([{'domain': 'example.test'}]), {}, {'token': 'test-token'},
                           wrap([{'id': 'one', 'createdAt': '2026-10-15T00:00:00Z'}]),
                           {'subject': 'Confirmation', 'text': 'Code 123456',
                            'from': {'address': 'sender@example.test'}}]
                with patch.object(mailbox, '_request', side_effect=replies) as request:
                    box = mailbox.TempMailBox.create(basis='https://mail.example.test', proxy=self.proxy)
                    messages = box.fetch()
                self.assertTrue(box.address().endswith('@example.test'))
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0]['subject'], 'Confirmation')
                self.assertEqual(messages[0]['text'], 'Code 123456')
                self.assertEqual(messages[0]['from'], 'sender@example.test')
                self.assertIsInstance(messages[0]['stamp'], float)
                self.assertEqual([call.args[0] for call in request.call_args_list], [
                    'https://mail.example.test/domains', 'https://mail.example.test/accounts',
                    'https://mail.example.test/token', 'https://mail.example.test/messages?page=1',
                    'https://mail.example.test/messages/one'])
                self.assertTrue(all(call.kwargs.get('proxy') is self.proxy for call in request.call_args_list))

    def test_invalid_collections_raise_mailbox_error_before_account_or_detail_request(self):
        invalid = [None, 'invalid', 42, {}, {'items': []}, {'hydra:member': 'invalid'},
                   {'member': [1]}, [{'domain': 'example.test', 'id': 'one'}, 'invalid']]
        for collection in invalid:
            for operation in ('create', 'fetch'):
                with self.subTest(collection=collection, operation=operation):
                    replies = [collection] if operation == 'create' else [{'token': 'test-token'}, collection]
                    with patch.object(mailbox, '_request', side_effect=replies) as request:
                        with self.assertRaises(mailbox.MailboxError):
                            if operation == 'create':
                                mailbox.TempMailBox.create(basis='https://mail.example.test', proxy=self.proxy)
                            else:
                                mailbox.TempMailBox('test@example.test', 'test-only',
                                                    basis='https://mail.example.test', proxy=self.proxy).fetch()
                    self.assertEqual(request.call_count, 1 if operation == 'create' else 2)

    def test_valid_empty_collections_return_empty_inbox_without_detail_requests(self):
        for collection in ([], {'hydra:member': []}, {'member': []}):
            with self.subTest(collection=collection):
                with patch.object(mailbox, '_request', side_effect=[{'token': 'test-token'}, collection]) as request:
                    box = mailbox.TempMailBox('test@example.test', 'test-only',
                                              basis='https://mail.example.test', proxy=self.proxy)
                    self.assertEqual(box.fetch(), [])
                self.assertEqual(request.call_count, 2)
                with patch.object(mailbox, '_request', return_value=collection) as request:
                    with self.assertRaises(mailbox.MailboxError):
                        mailbox.TempMailBox.create(basis='https://mail.example.test', proxy=self.proxy)
                request.assert_called_once()
                self.assertEqual(request.call_args.args[0], 'https://mail.example.test/domains')
                self.assertIs(request.call_args.kwargs['proxy'], self.proxy)

    def test_round29_zero_fetch_limit_returns_empty_before_connect_or_login(self):
        connection = MagicMock()
        connection.select.return_value = ('OK', [])
        connection.search.return_value = ('OK', [b'1 2'])
        connection.fetch.return_value = ('OK', [(b'RFC822', b'Subject: Test\r\n\r\nTest message')])
        boxes = [mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only'),
                 mailbox.TempMailBox('test@example.test', 'test-only')]
        for box in boxes:
            with self.subTest(box=type(box).__name__), \
                    patch.object(mailbox.imaplib, 'IMAP4_SSL', return_value=connection) as connect, \
                    patch.object(mailbox, '_request', side_effect=[{'token': 'test-token'}, []]) as request:
                self.assertEqual(box.fetch(limit=0), [])
                connect.assert_not_called()
                request.assert_not_called()

    def test_round29_invalid_fetch_limits_are_rejected_before_io(self):
        connection = MagicMock()
        connection.select.return_value = ('OK', [])
        connection.search.return_value = ('OK', [b'1'])
        connection.fetch.return_value = ('OK', [(b'RFC822', b'Subject: Test\r\n\r\nTest message')])
        for limit in (-1, 1.5, '1', None, True, False, float('nan'), float('inf')):
            for kind in ('imap', 'temporary'):
                box = (mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only') if kind == 'imap'
                       else mailbox.TempMailBox('test@example.test', 'test-only'))
                with self.subTest(box=kind, limit=limit), \
                        patch.object(mailbox.imaplib, 'IMAP4_SSL', return_value=connection) as connect, \
                        patch.object(mailbox, '_request', side_effect=[{'token': 'test-token'}, []]) as request:
                    with self.assertRaises(mailbox.MailboxError):
                        box.fetch(limit=limit)
                    connect.assert_not_called()
                    request.assert_not_called()

    def test_round29_wait_time_limits_are_finite_and_zero_timeout_allows_one_attempt(self):
        invalid = [(-1, 1), (float('nan'), 1), (float('inf'), 1),
                   (1, -1), (1, 0), (1, float('nan')), (1, float('inf'))]
        for method in ('wait_for_code', 'wait_for_link'):
            box = mailbox.Mailbox()
            for timeout, interval in invalid:
                with self.subTest(method=method, timeout=timeout, interval=interval), \
                        patch.object(box, 'fetch', return_value=[]) as fetch, \
                        patch('socialscraper.operation.wait', side_effect=AssertionError('Unexpected wait')):
                    # If validation is broken, abort immediately after its first fetch.
                    stop = iter([False, True]).__next__
                    with self.assertRaises(mailbox.MailboxError):
                        getattr(box, method)(platform='google', timeout=timeout, interval=interval, stop=stop)
                    fetch.assert_not_called()
            link = 'https://accounts.google.com/verify?token=test'
            message = {'subject': 'Google confirmation', 'text': 'Code 123456 ' + link,
                       'from': 'noreply@google.com'}
            with self.subTest(method=method, timeout=0), \
                    patch.object(box, 'fetch', return_value=[message]) as fetch, \
                    patch('socialscraper.operation.wait', side_effect=AssertionError('Unexpected wait')):
                value, found = getattr(box, method)(platform='google', timeout=0, interval=1)
                self.assertEqual(value, '123456' if method == 'wait_for_code' else link)
                self.assertEqual(found, message)
                fetch.assert_called_once()

    def test_http_request_explicit_proxy_ignores_no_proxy_environment(self):
        raw, secure = MagicMock(name='raw-http'), MagicMock(name='tls-http')
        response = MagicMock(status=200)
        response.read.return_value = b'{"ok": true}'
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'}), \
                patch.object(tunnel, 'open_tunnel', return_value=raw) as route, \
                patch.object(ssl.SSLContext, 'wrap_socket', autospec=True, return_value=secure) as wrap, \
                patch('http.client.HTTPSConnection', return_value=connection), \
                patch.object(mailbox.urllib.request, 'urlopen', side_effect=AssertionError('Proxy bypass')) as direct:
            result = mailbox._request('https://mail.example.test/messages?page=1',
                                      kopf={'Authorization': 'Bearer test-token'}, proxy=self.proxy)
        self.assertEqual(result, {'ok': True})
        route.assert_called_once_with(self.proxy, 'mail.example.test', 443, timeout=20)
        self.assertIs(connection.sock, secure)
        self.assertEqual(wrap.call_args.kwargs['server_hostname'], 'mail.example.test')
        self.assertTrue(wrap.call_args.args[0].check_hostname)
        self.assertEqual(wrap.call_args.args[0].verify_mode, ssl.CERT_REQUIRED)
        request = connection.request.call_args
        self.assertEqual(request.args[:2], ('GET', '/messages?page=1'))
        headers = {key.lower(): value for key, value in request.kwargs['headers'].items()}
        self.assertEqual(headers['authorization'], 'Bearer test-token')
        direct.assert_not_called()
        connection.close.assert_called_once()

    def test_http_tunnel_failure_never_uses_urlopen_as_fallback(self):
        connection = MagicMock()
        with patch.object(tunnel, 'open_tunnel', side_effect=tunnel.TunnelError('Proxy unavailable')) as route, \
                patch('http.client.HTTPSConnection', return_value=connection), \
                patch.object(mailbox.urllib.request, 'urlopen', side_effect=AssertionError('Proxy bypass')) as direct:
            with self.assertRaises(mailbox.MailboxError):
                mailbox._request('https://mail.example.test/token', data={'test': True}, proxy=self.proxy)
        route.assert_called_once()
        direct.assert_not_called()
        connection.request.assert_not_called()

    def test_required_proxy_without_active_route_fails_closed_for_both_transports(self):
        from socialscraper.net import testpolicy
        box = mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only')
        with patch.dict(os.environ, {'SS_REQUIRE_PROXY': '1'}), \
                patch.object(testpolicy, 'open_route', side_effect=OSError('No active route')) as route, \
                patch('http.client.HTTPSConnection'), \
                patch.object(mailbox.urllib.request, 'urlopen', side_effect=AssertionError('Proxy bypass')) as direct, \
                self.imap_peer():
            for operation in (box.fetch, lambda: mailbox._request('https://mail.example.test/token')):
                with self.subTest(operation=operation), self.assertRaises(mailbox.MailboxError):
                    operation()
        self.assertEqual(route.call_count, 2)
        direct.assert_not_called()


class _LoopbackProxy:
    """One local peer speaks CONNECT and then the target HTTP response."""
    def __init__(self, response):
        self.response = response
        self.requests, self.targets, self.sockets, self.errors = [], [], [], []
        self.closed = threading.Event()
        self.stop = threading.Event()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(4)
        self.listener.settimeout(.05)
        self.proxy = Proxy('http', '127.0.0.1', self.listener.getsockname()[1])
        self.thread = threading.Thread(target=self._serve, daemon=True, name='mailbox-loopback-test')

    @staticmethod
    def _headers(peer):
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = peer.recv(4096)
            if not chunk:
                return None, b''
            data += chunk
            if len(data) > 32768:
                raise AssertionError('Test request exceeded the header limit')
        return data.split(b'\r\n\r\n', 1)

    def _serve(self):
        while not self.stop.is_set():
            try:
                peer, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.sockets.append(peer)
            try:
                with peer:
                    peer.settimeout(2)
                    connect, _ = self._headers(peer)
                    if connect is None:
                        continue
                    self.targets.append(connect.split(b'\r\n', 1)[0])
                    peer.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    header, body = self._headers(peer)
                    if header is None:
                        self.closed.set()
                        continue
                    lines = header.decode('iso-8859-1').split('\r\n')
                    fields = dict(line.split(':', 1) for line in lines[1:])
                    fields = {name.lower(): value.strip() for name, value in fields.items()}
                    size = int(fields.get('content-length', '0'))
                    if size > 32768:
                        raise AssertionError('Test request exceeded the body limit')
                    while len(body) < size:
                        chunk = peer.recv(min(4096, size - len(body)))
                        if not chunk:
                            raise AssertionError('Request body was truncated')
                        body += chunk
                    self.requests.append({'line': lines[0], 'headers': fields, 'body': body})
                    peer.sendall(self.response)
                    # Leave the peer open: EOF must be caused by the client cleanup.
                    try:
                        if peer.recv(1) == b'':
                            self.closed.set()
                    except ConnectionResetError:
                        self.closed.set()
            except Exception as error:
                if not self.stop.is_set():
                    self.errors.append(error)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.listener.close()
        for peer in self.sockets:
            with contextlib.suppress(OSError):
                peer.shutdown(socket.SHUT_RDWR)
            peer.close()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise AssertionError('Loopback proxy thread did not stop')


class MailboxLoopbackTransportTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        connect, resolve = socket.socket.connect, socket.getaddrinfo
        self.destinations, self.client_sockets = [], []

        def local_connect(peer, address):
            if address[0] != '127.0.0.1':
                raise AssertionError('Non-loopback connection blocked')
            self.destinations.append(address)
            return connect(peer, address)

        def local_resolve(host, *args, **kwargs):
            if host != '127.0.0.1':
                raise AssertionError('External DNS blocked')
            return resolve(host, *args, **kwargs)

        original_tunnel = tunnel.open_tunnel

        def observed_tunnel(*args, **kwargs):
            peer = original_tunnel(*args, **kwargs)
            self.client_sockets.append(peer)
            return peer

        stack.enter_context(patch.object(socket.socket, 'connect', local_connect))
        stack.enter_context(patch.object(socket, 'getaddrinfo', local_resolve))
        stack.enter_context(patch.object(tunnel, 'open_tunnel', side_effect=observed_tunnel))
        stack.enter_context(patch.object(mailbox.urllib.request, 'urlopen',
                                        side_effect=AssertionError('Unexpected direct HTTP client')))

    def assert_closed(self, peer):
        self.assertTrue(peer.closed.wait(2.5), 'The server did not observe the client closing its socket')
        self.assertFalse(peer.errors, peer.errors)
        self.assertTrue(self.client_sockets)
        self.assertTrue(all(sock.fileno() == -1 for sock in self.client_sockets))

    def test_real_loopback_tunnel_carries_post_json_and_reads_json_response(self):
        body = b'{"created": true}'
        response = b'HTTP/1.1 201 Created\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
        with _LoopbackProxy(response) as peer:
            result = mailbox._request('http://127.0.0.1:8088/accounts?source=test',
                                      data={'address': 'test@example.test', 'password': 'test-only'},
                                      proxy=peer.proxy)
            self.assert_closed(peer)
        self.assertEqual(result, {'created': True})
        self.assertEqual(peer.targets, [b'CONNECT 127.0.0.1:8088 HTTP/1.1'])
        self.assertEqual(self.destinations, [('127.0.0.1', peer.proxy.port)])
        self.assertEqual(len(peer.requests), 1)
        request = peer.requests[0]
        self.assertEqual(request['line'], 'POST /accounts?source=test HTTP/1.1')
        self.assertEqual(request['headers']['host'], '127.0.0.1:8088')
        self.assertEqual(request['headers']['content-type'], 'application/json')
        self.assertEqual(json.loads(request['body']), {'address': 'test@example.test', 'password': 'test-only'})
        self.assertTrue(all(sock.fileno() == -1 for sock in peer.sockets))

    def test_redirect_does_not_reconnect_or_forward_credentials(self):
        response = b'HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:8089/steal\r\nContent-Length: 0\r\n\r\n'
        with _LoopbackProxy(response) as peer:
            with self.assertRaisesRegex(mailbox.MailboxError, '302'):
                mailbox._request('http://127.0.0.1:8088/token', data={'password': 'test-only'},
                                 kopf={'Authorization': 'Bearer test-token'}, proxy=peer.proxy)
            self.assert_closed(peer)
        self.assertEqual(self.destinations, [('127.0.0.1', peer.proxy.port)])
        self.assertEqual(peer.targets, [b'CONNECT 127.0.0.1:8088 HTTP/1.1'])
        self.assertEqual(len(peer.requests), 1)
        self.assertEqual(peer.requests[0]['headers']['authorization'], 'Bearer test-token')
        self.assertEqual(peer.requests[0]['line'], 'POST /token HTTP/1.1')

    def test_http_and_tls_errors_release_real_tunnel_sockets(self):
        for scenario in ('status', 'malformed', 'tls'):
            with self.subTest(scenario=scenario):
                response = (b'invalid HTTP status\r\n\r\n' if scenario == 'malformed' else
                            b'HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n')
                tls = (patch.object(ssl.SSLContext, 'wrap_socket', side_effect=ssl.SSLCertVerificationError('Untrusted certificate'))
                       if scenario == 'tls' else contextlib.nullcontext())
                with _LoopbackProxy(response) as peer, tls:
                    scheme = 'https' if scenario == 'tls' else 'http'
                    with self.assertRaises(mailbox.MailboxError):
                        mailbox._request(f'{scheme}://127.0.0.1:8088/token', proxy=peer.proxy)
                    self.assert_closed(peer)
                self.assertTrue(all(sock.fileno() == -1 for sock in peer.sockets))


if __name__ == '__main__':
    unittest.main()
