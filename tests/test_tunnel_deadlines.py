"""Bound proxy setup across DNS, address retries and fragmented handshakes."""
import socket
import socketserver
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from socialscraper.net import tunnel
from socialscraper.net.proxy import Proxy


class TunnelDeadlines(unittest.TestCase):
    def test_invalid_budgets_never_start_dns_or_connections(self):
        with patch.object(tunnel.socket,'getaddrinfo') as resolve, \
             patch.object(tunnel.socket,'socket') as create:
            for timeout in (0,-1,float('nan'),float('inf'),True,None,'invalid'):
                with self.subTest(timeout=timeout):
                    with self.assertRaises((ValueError,TypeError)):
                        tunnel.open_tunnel(Proxy('socks5','gateway.invalid',9),'target.invalid',443,timeout=timeout)
            resolve.assert_not_called();create.assert_not_called()

    def test_address_retries_share_one_budget(self):
        clock=[0.0];attempts=[]
        class TimedSocket:
            def __init__(self,*args):self.timeout=None
            def settimeout(self,value):self.timeout=value
            def connect(self,address):
                attempts.append(address);clock[0]+=self.timeout
                raise TimeoutError('test timeout')
            def close(self):pass
        addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',p)) for p in range(10000,10015)]
        with patch.object(tunnel.socket,'getaddrinfo',return_value=addresses), \
             patch.object(tunnel.socket,'socket',TimedSocket), \
             patch.object(tunnel.time,'monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(TimeoutError):
                tunnel.open_tunnel(Proxy('socks5','gateway.invalid',10000),'target.invalid',443,timeout=5)
        self.assertLessEqual(clock[0],5)
        self.assertEqual(len(attempts),1)

    def test_slow_gateway_dns_is_bounded_and_late_result_cannot_connect(self):
        release=threading.Event();finished=threading.Event()
        def resolve(*args,**kwargs):
            release.wait(.3);finished.set()
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',9))]
        started=time.monotonic()
        with patch.object(tunnel.socket,'getaddrinfo',side_effect=resolve), \
             patch.object(tunnel.socket,'socket',side_effect=AssertionError('Late connection after DNS timeout')) as create:
            try:
                with self.assertRaises(TimeoutError):
                    tunnel.open_tunnel(Proxy('socks5','gateway.invalid',9),'target.invalid',443,timeout=.05)
                self.assertLess(time.monotonic()-started,.2)
            finally:
                release.set();self.assertTrue(finished.wait(1))
            create.assert_not_called()

    def test_fragmented_socks_replies_cannot_restart_the_handshake_budget(self):
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(1)
                try:
                    self.request.recv(100)
                    for byte in b'\x05\x00':
                        self.request.sendall(bytes([byte]));time.sleep(.07)
                    self.request.recv(100)
                    for byte in b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00':
                        self.request.sendall(bytes([byte]));time.sleep(.07)
                except OSError:pass
        class Server(socketserver.ThreadingTCPServer):daemon_threads=True
        server=Server(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        thread.start()
        try:
            started=time.monotonic()
            with self.assertRaises(TimeoutError):
                sock=tunnel.open_tunnel(Proxy('socks5','127.0.0.1',server.server_address[1]),
                    'target.invalid',443,timeout=.2)
                sock.close()
            self.assertLess(time.monotonic()-started,.6)
        finally:
            server.shutdown();server.server_close();thread.join(1)

    def test_slow_http_headers_and_body_share_the_entire_request_budget(self):
        for stage in ('headers','body'):
            with self.subTest(stage=stage):
                class Handler(socketserver.BaseRequestHandler):
                    def handle(self):
                        try:
                            self.request.settimeout(1);self.request.recv(4096)
                            header=b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n'
                            if stage=='body':self.request.sendall(header)
                            data=header+b'x'*20 if stage=='headers' else b'x'*20
                            for byte in data:
                                self.request.sendall(bytes([byte]));time.sleep(.03)
                        except OSError:pass
                class Server(socketserver.ThreadingTCPServer):daemon_threads=True
                server=Server(('127.0.0.1',0),Handler)
                thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
                thread.start()
                try:
                    # TLS is stubbed only to exercise real blocking HTTP reads
                    # on loopback. Certificate validation has its own tests.
                    context=SimpleNamespace(wrap_socket=lambda sock,server_hostname:sock)
                    started=time.monotonic()
                    with patch.object(tunnel.ssl,'create_default_context',return_value=context):
                        with self.assertRaises(TimeoutError):
                            tunnel.https_get(None,f'https://127.0.0.1:{server.server_address[1]}/',timeout=.15)
                    self.assertLess(time.monotonic()-started,.6)
                finally:
                    server.shutdown();server.server_close();thread.join(1)

    def test_address_family_allocation_failure_still_allows_ipv4(self):
        addresses=[(socket.AF_INET6,socket.SOCK_STREAM,6,'',('::1',9,0,0)),
                   (socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',9))]
        class ConnectedSocket:
            def settimeout(self,value):self.timeout=value
            def connect(self,address):self.address=address
            def close(self):pass
        good=ConnectedSocket()
        def create(family,*args):
            if family==socket.AF_INET6:raise OSError('IPv6 disabled')
            return good
        with patch.object(tunnel.socket,'getaddrinfo',return_value=addresses), \
             patch.object(tunnel.socket,'socket',side_effect=create), \
             patch.object(tunnel,'_socks5'):
            self.assertIs(tunnel.open_tunnel(Proxy('socks5','gateway.invalid',9),'target.invalid',443,timeout=1),good)
        self.assertEqual(good.address,('127.0.0.1',9))


if __name__=='__main__':unittest.main()
