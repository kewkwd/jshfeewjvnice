"""Kleine Attrappen für Netztests: lokaler SOCKS5-Server, lokaler HTTP-CONNECT-Proxy, Steuerport."""
import socket
import threading


class _Server:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.seen = []          # was die Gegenstelle gesendet hat
        self._stop = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            except Exception:
                break
            try:
                conn.settimeout(2)
                self.handle(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def handle(self, conn):
        raise NotImplementedError

    def close(self):
        self._stop = True
        self.sock.close()
        self.thread.join(2)


class FakeSocks5(_Server):
    """SOCKS5 ohne Anmeldung; merkt sich Zieladresstyp und Name. Nach dem Verbinden schließt er."""

    def handle(self, conn):
        greeting = conn.recv(3)
        self.seen.append(('gruss', greeting))
        conn.sendall(b'\x05\x00')
        head = conn.recv(4)
        if len(head) < 4:
            return
        atyp = head[3]
        if atyp == 3:
            n = conn.recv(1)[0]
            name = conn.recv(n).decode()
        else:
            name = None
            conn.recv(4 if atyp == 1 else 16)
        conn.recv(2)
        self.seen.append(('ziel', atyp, name))
        conn.sendall(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')


class FakeHttpConnect(_Server):
    def __init__(self, status=b'200 Connection established'):
        self.status = status
        super().__init__()

    def handle(self, conn):
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = conn.recv(1024)
            if not chunk:
                return
            data += chunk
        self.seen.append(data.split(b'\r\n')[0].decode())
        conn.sendall(b'HTTP/1.1 ' + self.status + b'\r\n\r\n')


class FakeControl(_Server):
    """Tor-Steuerport: akzeptiert AUTHENTICATE "" (oder nicht) und NEWNYM."""

    def __init__(self, accept_empty_password=True):
        self.accept = accept_empty_password
        super().__init__()

    def handle(self, conn):
        line = conn.recv(256)
        self.seen.append(line)
        if not self.accept:
            conn.sendall(b'515 Authentication failed\r\n')
            return
        conn.sendall(b'250 OK\r\n')
        self.seen.append(conn.recv(256))
        conn.sendall(b'250 OK\r\n')
