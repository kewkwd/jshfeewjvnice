"""Kleiner HTTPS-Client durch einen Proxy – nur Standardbibliothek.

Wird von `freeproxy` (Proxys testen) und `tor` (Tor erkennen) benutzt. Unterstützt HTTP-Proxys mit CONNECT
und SOCKS5 (ohne Anmeldung oder mit Benutzername/Passwort). Der Zielname wird immer dem Proxy übergeben
(Namensauflösung beim Proxy, wie `socks5h`), das Zertifikat des Ziels wird immer geprüft.
"""
import contextlib
import http.client
import ipaddress
import math
import socket
import ssl
import threading
import time
import urllib.parse


class TunnelError(OSError):
    """Verbindung über den Proxy nicht möglich."""


class TlsBroken(TunnelError):
    """Das Zertifikat des Ziels stimmt nicht: der Proxy greift in die verschlüsselte Verbindung ein."""


def _remaining(deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:
        raise TimeoutError('Zeitlimit für die Verbindung über den Proxy erreicht')
    return remaining


def _deadline(timeout):
    seconds=float(timeout)
    if isinstance(timeout,bool) or not math.isfinite(seconds) or seconds<=0:
        raise ValueError('Das Verbindungszeitlimit muss positiv und endlich sein.')
    return time.monotonic()+seconds


def _addresses(host,port,deadline):
    # Only the gateway is resolved here. Target hostnames stay in SOCKS/CONNECT.
    try:ipaddress.ip_address(host)
    except ValueError:pass
    else:
        return socket.getaddrinfo(host,port,type=socket.SOCK_STREAM,flags=socket.AI_NUMERICHOST)
    done=threading.Event();result=[]
    def resolve():
        try:result.append((True,socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
        except BaseException as error:result.append((False,error))
        finally:done.set()
    threading.Thread(target=resolve,daemon=True,name='proxy-dns').start()
    if not done.wait(_remaining(deadline)):
        raise TimeoutError('Zeitlimit bei der Namensauflösung des Proxy-Gateways erreicht')
    _remaining(deadline)
    ok,value=result[0]
    if not ok:raise value
    return value


def _connect(host,port,deadline):
    addresses=_addresses(host,port,deadline)
    error=None
    for family,kind,protocol,_name,address in addresses:
        remaining=_remaining(deadline)
        sock=None
        try:
            sock=socket.socket(family,kind,protocol)
            sock.settimeout(remaining)
            sock.connect(address)
            _remaining(deadline)
            return sock
        except OSError as failure:
            error=failure
            if sock is not None:sock.close()
        except BaseException:
            if sock is not None:sock.close()
            raise
    _remaining(deadline)
    if error is not None:raise error
    raise OSError('Keine Adresse für das Proxy-Gateway gefunden')


def _sendall(sock,data,deadline):
    if deadline is not None:sock.settimeout(_remaining(deadline))
    sock.sendall(data)


def _recv_exact(sock, count, deadline=None):
    data = b''
    while len(data) < count:
        if deadline is not None:sock.settimeout(_remaining(deadline))
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise TunnelError('Proxy hat die Verbindung beendet')
        data += chunk
    return data


def _socks5(sock, host, port, user=None, password=None, *, deadline=None):
    methods = b'\x05\x02\x00\x02' if user is not None else b'\x05\x01\x00'
    _sendall(sock,methods,deadline)
    answer = _recv_exact(sock, 2,deadline)
    if answer[0] != 5:
        raise TunnelError('Kein SOCKS5-Proxy')
    if answer[1] == 2:
        if user is None:
            raise TunnelError('SOCKS5-Proxy verlangt Anmeldung')
        u, p = user.encode('utf-8')[:255], (password or '').encode('utf-8')[:255]
        _sendall(sock,b'\x01' + bytes([len(u)]) + u + bytes([len(p)]) + p,deadline)
        if _recv_exact(sock, 2,deadline)[1] != 0:
            raise TunnelError('SOCKS5-Anmeldung abgelehnt')
    elif answer[1] != 0:
        raise TunnelError('SOCKS5-Proxy akzeptiert keine passende Anmeldeart')
    try:
        ip = ipaddress.ip_address(host)
        target = (b'\x01' if ip.version == 4 else b'\x04') + ip.packed
    except ValueError:
        name = host.encode('idna')
        target = b'\x03' + bytes([len(name)]) + name  # Name geht an den Proxy (kein lokales DNS)
    _sendall(sock,b'\x05\x01\x00' + target + port.to_bytes(2, 'big'),deadline)
    head = _recv_exact(sock, 4,deadline)
    if head[1] != 0:
        raise TunnelError(f'SOCKS5-Proxy konnte das Ziel nicht erreichen (Code {head[1]})')
    skip = {1: 4, 4: 16}.get(head[3])
    if skip is None:
        skip = _recv_exact(sock, 1,deadline)[0]
    _recv_exact(sock, skip + 2,deadline)


def _http_connect(sock, host, port, user=None, password=None, *, deadline=None):
    lines = [f'CONNECT {host}:{port} HTTP/1.1', f'Host: {host}:{port}']
    if user is not None:
        import base64
        token = base64.b64encode(f'{user}:{password or ""}'.encode('utf-8')).decode('ascii')
        lines.append('Proxy-Authorization: Basic ' + token)
    _sendall(sock,('\r\n'.join(lines) + '\r\n\r\n').encode('ascii'),deadline)
    data = b''
    while b'\r\n\r\n' not in data:
        if deadline is not None:sock.settimeout(_remaining(deadline))
        chunk = sock.recv(4096)
        if not chunk:
            raise TunnelError('Proxy hat die Verbindung beendet')
        data += chunk
        if len(data) > 65536:
            raise TunnelError('Proxy-Antwort zu lang')
    status = data.split(b'\r\n', 1)[0].split()
    if len(status) < 2 or status[1] != b'200':
        raise TunnelError('Proxy lehnt CONNECT ab')


def open_tunnel(proxy, host, port, timeout=8.0):
    """Proxy-Aufbau mit gemeinsamem Zeitbudget für DNS, TCP und Handshake."""
    deadline=_deadline(timeout)
    if proxy.scheme not in ('socks5','socks5h','http'):
        raise TunnelError('Proxy-Art wird nicht unterstützt')
    sock = _connect(proxy.host,proxy.port,deadline)
    try:
        sock.settimeout(float(timeout))
        if proxy.scheme in ('socks5', 'socks5h'):
            _socks5(sock, host, port, proxy.user, proxy.password,deadline=deadline)
        elif proxy.scheme in ('http',):
            _http_connect(sock, host, port, proxy.user, proxy.password,deadline=deadline)
        _remaining(deadline)
        sock.settimeout(float(timeout))
        return sock
    except BaseException:
        sock.close()
        raise


@contextlib.contextmanager
def _read_deadline(sock,deadline):
    """Wake buffered HTTP reads even if the peer keeps trickling single bytes."""
    finished=threading.Event();expired=threading.Event();lock=threading.Lock()
    def abort():
        with lock:
            if finished.is_set():return
            expired.set()
            with contextlib.suppress(OSError):sock.shutdown(socket.SHUT_RDWR)
    timer=threading.Timer(_remaining(deadline),abort)
    timer.daemon=True;timer.start()
    try:
        yield
        _remaining(deadline)
    except (KeyboardInterrupt,SystemExit):
        raise
    except Exception as error:
        if expired.is_set() or time.monotonic()>=deadline:
            if isinstance(error,TimeoutError):raise
            raise TimeoutError('Zeitlimit für den HTTPS-Abruf über den Proxy erreicht') from error
        raise
    finally:
        with lock:
            finished.set();timer.cancel()


def https_get(proxy, url, timeout=8.0, max_bytes=65536):
    """GET auf eine https-Adresse (durch den Proxy oder, bei `proxy=None`, direkt).

    Gibt (Status, Text, Dauer in Sekunden) zurück. Zertifikatsfehler -> TlsBroken, sonst TunnelError/OSError.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname:
        raise ValueError('Nur https-Adressen erlaubt')
    port = parts.port or 443
    started = time.monotonic()
    deadline = _deadline(timeout)
    if proxy is None:
        sock = _connect(parts.hostname,port,deadline)
    else:
        sock = open_tunnel(proxy, parts.hostname, port, _remaining(deadline))
    response=None
    try:
        context = ssl.create_default_context()
        sock.settimeout(_remaining(deadline))
        try:
            tls = context.wrap_socket(sock, server_hostname=parts.hostname)
        except ssl.SSLCertVerificationError as error:
            raise TlsBroken('Zertifikat des Ziels ungültig (Proxy greift in die Verbindung ein)') from error
        except ssl.SSLError as error:
            raise TunnelError('TLS-Aufbau fehlgeschlagen') from error
        sock = tls
        sock.settimeout(_remaining(deadline))
        connection = http.client.HTTPConnection(parts.hostname, port, timeout=_remaining(deadline))
        connection.sock = sock
        path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        with _read_deadline(sock,deadline):
            connection.request('GET', path, headers={'Host': parts.netloc, 'Connection': 'close',
                                                     'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'})
            response = connection.getresponse()
            body = response.read(max_bytes)
        return response.status, body.decode('utf-8', 'replace'), time.monotonic() - started
    except http.client.HTTPException as error:
        raise TunnelError('Ungültige Antwort') from error
    finally:
        if response is not None:
            with contextlib.suppress(OSError):response.close()
        try:
            sock.close()
        except OSError:
            pass
