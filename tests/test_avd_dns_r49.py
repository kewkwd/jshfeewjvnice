"""AVD DNS boundary tests: fake HTTPS and owned loopback UDP only."""
import contextlib
import os
import socket
import struct
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from socialscraper.android import avd_dns
from socialscraper.net.errors import FinalError


QUESTION = b'\x07example\x04test\x00' + struct.pack('!2H', 1, 1)
QUERY = struct.pack('!6H', 0x1234, 0x0100, 1, 0, 0, 0) + QUESTION
ANSWER = struct.pack('!6H', 0x1234, 0x8180, 1, 1, 0, 0) + QUESTION + (
    b'\xc0\x0c' + struct.pack('!2HIH', 1, 1, 60, 4) + b'\xc0\x00\x02\x01')


class Response:
    def __init__(self, body=ANSWER, *, status=200, headers=None, chunks=None):
        self.body = body
        self.status_code = status
        self.headers = {'Content-Type': 'application/dns-message', 'Content-Length': str(len(body))}
        if headers is not None:
            self.headers.update(headers)
        self.closed = False
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.closed = True

    def iter_content(self, chunk_size):
        if self.chunks is not None:
            yield from self.chunks()
        else:
            for index in range(0, len(self.body), chunk_size):
                yield self.body[index:index + chunk_size]


class ProxyDnsTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('getaddrinfo', 'create_connection'):
            self.stack.enter_context(patch.object(socket, target,
                side_effect=AssertionError('No real DNS or TCP permitted')))
        self.stack.enter_context(patch.object(socket.socket, 'connect',
            side_effect=AssertionError('No TCP connection permitted')))
        self.stack.enter_context(patch.object(subprocess, 'Popen',
            side_effect=AssertionError('No process permitted')))
        original_sendto = socket.socket.sendto

        def only_local(sock, data, *args):
            if args[-1][0] != '127.0.0.1':
                raise AssertionError('Only the test loopback peer is permitted')
            return original_sendto(sock, data, *args)
        self.stack.enter_context(patch.object(socket.socket, 'sendto', only_local))
        self.route = 'http://127.0.0.1:23456'
        self.sessions, self.calls = [], []
        self.reply = lambda query: Response()
        owner = self

        class Session:
            def __init__(self):
                self.trust_env, self.proxies, self.closed = True, {}, False
                owner.sessions.append(self)

            def post(self, url, **kwargs):
                owner.calls.append((self, url, kwargs))
                return owner.reply(kwargs['data'])

            def close(self):
                self.closed = True

        self.stack.enter_context(patch.object(avd_dns.requests, 'Session', Session))

    def relay(self):
        relay = avd_dns.ProxyDns(self.route, port=0).start()
        self.addCleanup(relay.close)
        return relay

    def client(self):
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(('127.0.0.1', 0))
        peer.settimeout(1.5)
        self.addCleanup(peer.close)
        return peer

    def exchange(self, relay, query=QUERY, peer=None):
        peer = peer or self.client()
        peer.sendto(query, ('127.0.0.1', relay.port))
        return peer.recvfrom(8192)[0]

    def assert_servfail(self, value, query=QUERY):
        identifier, flags, questions, answers, authority, additional = struct.unpack('!6H', value[:12])
        self.assertEqual(identifier, struct.unpack('!H', query[:2])[0])
        self.assertEqual(flags & 15, 2)
        self.assertTrue(flags & 0x8000)
        self.assertEqual((questions, answers, authority, additional), (1, 0, 0, 0))
        self.assertEqual(value[12:], QUESTION)

    def test_udp_answer_uses_only_fixed_https_endpoint_and_explicit_proxy(self):
        response = Response()
        self.reply = lambda _: response
        with patch.dict(os.environ, {'NO_PROXY': '*', 'HTTPS_PROXY': 'http://unusable.invalid:80'}):
            relay = self.relay()
            self.assertEqual(self.exchange(relay), ANSWER)
        relay.close()
        self.assertEqual(len(self.calls), 1)
        session, url, options = self.calls[0]
        self.assertFalse(session.trust_env)
        self.assertEqual(url, 'https://cloudflare-dns.com/dns-query')
        self.assertEqual(options['proxies'], {'http': self.route, 'https': self.route})
        self.assertEqual(options['data'], QUERY)
        self.assertTrue(options['verify'])
        self.assertFalse(options['allow_redirects'])
        self.assertEqual(options['headers']['Content-Type'], 'application/dns-message')
        self.assertEqual(options['timeout'], (2, 2))
        self.assertTrue(response.closed)
        self.assertTrue(session.closed)

    def test_non_loopback_or_ambiguous_proxy_routes_fail_before_socket_creation(self):
        routes = (None, '', 'http://localhost:1', 'http://192.0.2.1:80', 'https://127.0.0.1:9',
                  'http://127.0.0.1', 'http://127.0.0.1:0', 'http://user:pass@127.0.0.1:9',
                  'http://127.0.0.1:9/path', 'http://127.0.0.1:9?x=1')
        with patch.object(avd_dns.socket, 'socket', side_effect=AssertionError('Unexpected bind')):
            for route in routes:
                with self.subTest(route=route):
                    with self.assertRaises(FinalError):
                        avd_dns.ProxyDns(route).start()
            for port in (True, -1, 65536, 1.5):
                with self.subTest(port=port):
                    with self.assertRaises(ValueError):
                        avd_dns.ProxyDns(self.route, port).start()

    def test_invalid_dns_is_discarded_without_https_and_receiver_survives(self):
        relay, peer = self.relay(), self.client()
        invalid = (b'', b'bad', ANSWER, QUERY + b'extra', QUERY[:12] + b'\xc0\x0c\x00\x01\x00\x01',
                   struct.pack('!6H', 1, 0x0100, 2, 0, 0, 0) + QUESTION, b'x' * 4097)
        for query in invalid:
            peer.sendto(query, ('127.0.0.1', relay.port))
        self.assertEqual(self.exchange(relay, peer=peer), ANSWER)
        self.assertEqual(len(self.calls), 1)

    def test_http_failures_redirects_and_invalid_answers_return_servfail_without_retry(self):
        relay = self.relay()
        cases = {
            'redirect': lambda _: Response(status=302, headers={'Location': 'https://elsewhere.invalid'}),
            'http failure': lambda _: Response(status=503),
            'content type': lambda _: Response(headers={'Content-Type': 'text/html'}),
            'wrong transaction': lambda _: Response(b'\x00\x01' + ANSWER[2:]),
            'wrong question': lambda _: Response(ANSWER.replace(b'example', b'changed')),
            'oversized declared': lambda _: Response(headers={'Content-Length': '4097'}),
            'oversized body': lambda _: Response(b'x' * 4097, headers={'Content-Length': '4096'}),
            'truncated DNS': lambda _: Response(ANSWER[:-1]),
            'truncated HTTP': lambda _: Response(headers={'Content-Length': str(len(ANSWER) + 1)}),
        }
        for name, reply in cases.items():
            with self.subTest(name=name):
                before = len(self.calls)
                self.reply = reply
                self.assert_servfail(self.exchange(relay))
                self.assertEqual(len(self.calls), before + 1)
        self.reply = lambda _: (_ for _ in ()).throw(avd_dns.requests.ConnectionError('private test error'))
        self.assert_servfail(self.exchange(relay))

    def test_edns_query_and_compressed_answer_work_without_copying_edns_on_failure(self):
        query = QUERY[:10] + b'\x00\x01' + QUERY[12:] + b'\x00' + struct.pack('!2HIH', 41, 1232, 0, 0)
        relay = self.relay()
        self.assertEqual(self.exchange(relay, query), ANSWER)
        self.assertEqual(self.calls[-1][2]['data'], query)
        self.reply = lambda _: Response(status=500)
        self.assert_servfail(self.exchange(relay, query), query)

    def test_at_most_four_active_requests_and_overload_does_not_create_worker(self):
        relay, peer = self.relay(), self.client()
        release, four_started = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        counter_lock = threading.Lock()
        count = 0

        def reply(query):
            nonlocal count
            with counter_lock:
                count += 1
                if count == 4:
                    four_started.set()
            if not release.wait(2):
                raise AssertionError('Test HTTP gate was not released')
            return Response(query[:2] + ANSWER[2:])

        self.reply = reply
        for identifier in range(4):
            peer.sendto(struct.pack('!H', identifier) + QUERY[2:], ('127.0.0.1', relay.port))
        self.assertTrue(four_started.wait(1), 'Four fake requests should start')
        overloaded = b'\x77\x77' + QUERY[2:]
        self.assert_servfail(self.exchange(relay, overloaded, peer), overloaded)
        self.assertEqual(count, 4)
        release.set()
        returned = {peer.recvfrom(8192)[0][:2] for _ in range(4)}
        self.assertEqual(returned, {struct.pack('!H', identifier) for identifier in range(4)})
        relay.close()
        self.assertTrue(all(session.closed for session in self.sessions))

    def test_response_deadline_prevents_slow_stream_success(self):
        clock = SimpleNamespace(now=0)

        def chunks():
            yield ANSWER[:1]
            clock.now = 6
            yield ANSWER[1:]

        self.reply = lambda _: Response(chunks=chunks)
        relay = self.relay()
        with patch.object(avd_dns, 'time', SimpleNamespace(monotonic=lambda: clock.now)):
            self.assert_servfail(self.exchange(relay))

    def test_bind_conflict_preserves_existing_socket_and_close_is_owned_and_bounded(self):
        foreign = self.client()
        foreign_port = foreign.getsockname()[1]
        with self.assertRaises(FinalError):
            avd_dns.ProxyDns(self.route, foreign_port).start()
        self.assertEqual(foreign.getsockname()[1], foreign_port)
        relay = self.relay()
        self.assertIs(relay.start(), relay)
        released_port = relay.port
        started = time.monotonic()
        relay.close()
        relay.close()
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(relay._thread.is_alive())
        self.assertEqual(foreign.getsockname()[1], foreign_port)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as fresh:
            fresh.bind(('127.0.0.1', released_port))
        with self.assertRaises(FinalError):
            relay.start()

    def test_close_during_http_wait_stops_answers_and_bounds_join(self):
        relay, peer = self.relay(), self.client()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def reply(_):
            entered.set()
            if not release.wait(2):
                raise AssertionError('Test HTTP gate was not released')
            return Response()

        self.reply = reply
        peer.sendto(QUERY, ('127.0.0.1', relay.port))
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        with patch.object(avd_dns, 'CLOSE_SECONDS', .1):
            relay.close()
        self.assertLess(time.monotonic() - started, .5)
        self.assertTrue(all(session.closed for session in self.sessions))
        release.set()
        relay.close()
        self.assertEqual(len(relay._workers), 0)
        peer.settimeout(.05)
        with self.assertRaises(socket.timeout):
            peer.recvfrom(8192)


if __name__ == '__main__':
    unittest.main()
