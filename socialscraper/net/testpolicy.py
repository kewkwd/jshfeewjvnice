"""Opt-in process boundary for tests: proxy-only internet or loopback-only offline.

The boundary also catches Python clients which forgot to honour proxy settings.
Target DNS names are not resolved locally. Chrome is configured separately by
its backend; a missing browser proxy is rejected before launch.
"""
import atexit
import ipaddress
import os
import socket

_installed = False
_allowed = set()
_hosts = set()
_bridge = None
_proxy = None
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo
_sendto = socket.socket.sendto


def _local(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check(address):
    if not isinstance(address, tuple):
        return
    host, port = str(address[0]), int(address[1])
    if _local(host) or (host, port) in _allowed:
        return
    raise OSError('Direkte externe Verbindung durch die Test-Netzwerksperre verhindert.')


def enable(cfg=None, offline=False):
    global _installed, _bridge, _proxy
    if _installed:
        return
    if not offline:
        if cfg is None:
            raise ValueError('Für externe Tests fehlt die Proxykonfiguration.')
        from . import proxymode
        if proxymode.mode(cfg) == 'frei':
            raise ValueError('Externe Tests benötigen einen vorhandenen Proxy oder Tor; direkte Beschaffung kostenloser Proxys ist gesperrt.')
        pool = proxymode.make_pool(cfg)
        proxy = pool.acquire('audit_network', country=None)
        if proxy is None:
            raise ValueError('Kein Proxy/Tor für den Test eingerichtet; kein externer Abruf gestartet.')
        _proxy = proxy
        proxies = [proxy]
        from .accountproxy import load
        from .proxy import Proxy
        for raw in load(cfg.db_path).values():
            proxies.append(Proxy.parse(raw))
        for item in proxies:
            _hosts.add(item.host)
            _allowed.add((item.host, item.port))
            for info in _getaddrinfo(item.host, item.port, type=socket.SOCK_STREAM):
                _allowed.add((info[4][0], item.port))
        if proxy.scheme == 'socks5':
            # The same HTTP bridge also makes urllib/requests use remote DNS.
            from .browserproxy import BrowserProxy
            _bridge = BrowserProxy(proxy, allow_no_auth=True)
            route = _bridge.playwright['server']
            atexit.register(_bridge.close)
        else:
            route = proxy.url
        os.environ.update(HTTP_PROXY=route, HTTPS_PROXY=route, ALL_PROXY=route,
                          http_proxy=route, https_proxy=route, all_proxy=route,
                          NO_PROXY='localhost,127.0.0.1,::1', no_proxy='localhost,127.0.0.1,::1')
    else:
        os.environ.update(HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',
                          ALL_PROXY='http://127.0.0.1:9',http_proxy='http://127.0.0.1:9',
                          https_proxy='http://127.0.0.1:9',all_proxy='http://127.0.0.1:9',
                          NO_PROXY='localhost,127.0.0.1,::1',no_proxy='localhost,127.0.0.1,::1')

    def connect(sock, address):
        _check(address)
        return _connect(sock, address)
    def connect_ex(sock, address):
        _check(address)
        return _connect_ex(sock, address)
    def resolve(host, *args, **kwargs):
        name = str(host) if host is not None else 'localhost'
        if not _local(name) and name not in _hosts and not any(name == h for h, _p in _allowed):
            raise OSError('Lokale DNS-Auflösung eines externen Testziels verhindert; Proxy-DNS verwenden.')
        return _getaddrinfo(host, *args, **kwargs)
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    def sendto(sock, data, *args):
        _check(args[-1])
        return _sendto(sock, data, *args)
    socket.socket.sendto = sendto
    socket.getaddrinfo = resolve
    _installed = True

def open_route(host, port, timeout=20):
    """Raw protocols such as IMAP also use remote DNS through the test proxy."""
    if not _installed or _proxy is None:
        raise OSError('Kein freigegebener Proxy für diesen Testabruf verfügbar.')
    from .tunnel import open_tunnel
    return open_tunnel(_proxy, host, port, timeout=timeout)
