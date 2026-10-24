"""Loopback HTTP proxy forwarding exclusively through an authenticated SOCKS5 proxy.

Chrome cannot authenticate SOCKS5. CONNECT destinations (including DNS names) are
passed to the configured upstream; there is deliberately no direct fallback.
"""
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit

from .tunnel import open_tunnel


class BrowserProxy:
    def __init__(self, proxy, allow_no_auth=False):
        if proxy.scheme not in ('socks5','http') or (proxy.user is None and not allow_no_auth):
            raise ValueError('Die Browserbrücke benötigt einen HTTP-/SOCKS5-Proxy mit Anmeldung.')
        self.proxy = proxy
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.sockets = set()
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                remote = None
                established = False
                try:
                    self.request.settimeout(10)
                    data = b''
                    while b'\r\n\r\n' not in data:
                        chunk = self.request.recv(4096)
                        if not chunk:
                            return
                        data += chunk
                        if len(data) > 65536:
                            raise ValueError('Zu große Proxyanfrage')
                    header, body = data.split(b'\r\n\r\n', 1)
                    lines = header.split(b'\r\n')
                    method, target, version = lines[0].decode('ascii').split(' ', 2)
                    connect = method == 'CONNECT'
                    parts = urlsplit('//' + target if connect else target)
                    if not parts.hostname or (not connect and parts.scheme != 'http') or parts.username:
                        raise ValueError('Ungültiges Proxyziel')
                    port = parts.port or (443 if connect else 80)
                    remote = open_tunnel(owner.proxy, parts.hostname, port, timeout=10)
                    with owner.lock:
                        if owner.stopped.is_set():
                            return
                        owner.sockets.update((self.request, remote))
                    if connect:
                        self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                        established = True
                        if body:
                            remote.sendall(body)
                    else:
                        path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
                        clean = [x for x in lines[1:] if not x.lower().startswith(
                            (b'proxy-authorization:', b'proxy-connection:', b'connection:'))]
                        remote.sendall((f'{method} {path} {version}\r\n').encode('ascii') +
                                       b'\r\n'.join(clean) + b'\r\nConnection: close\r\n\r\n' + body)
                        established = True
                    self.request.settimeout(None)
                    remote.settimeout(None)
                    while not owner.stopped.is_set():
                        ready, _, _ = select.select([self.request, remote], [], [], .5)
                        for source in ready:
                            chunk = source.recv(65536)
                            if not chunk:
                                return
                            (remote if source is self.request else self.request).sendall(chunk)
                except (OSError, ValueError):
                    try:
                        if not established:
                            self.request.sendall(b'HTTP/1.1 502 Proxy connection failed\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
                    except OSError:
                        pass
                finally:
                    with owner.lock:
                        owner.sockets.discard(self.request)
                        if remote:
                            owner.sockets.discard(remote)
                    if remote:
                        remote.close()

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = False
        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .1}, daemon=True)
        self.thread.start()

    @property
    def playwright(self):
        return {'server': f'http://127.0.0.1:{self.server.server_address[1]}'}

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        with self.lock:
            for sock in tuple(self.sockets):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            self.sockets.clear()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def options(proxy):
    """Return browser options plus an owned bridge that the caller must close."""
    if proxy is not None and proxy.scheme == 'socks5' and proxy.user is not None:
        bridge = BrowserProxy(proxy)
        return bridge.playwright, bridge
    if proxy is not None and proxy.scheme == 'socks4' and proxy.user is not None:
        raise ValueError('SOCKS4-Anmeldung wird vom Browser nicht unterstützt; HTTP oder SOCKS5 verwenden.')
    return (proxy.playwright if proxy else None), None
