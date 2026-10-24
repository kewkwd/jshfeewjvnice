"""Owned loopback DNS for an AVD, using one explicit proxy for HTTPS DoH.

There is no system resolver or direct fallback. The caller owns the HTTP proxy
bridge and must keep it alive until the emulator and this relay have stopped.
"""
import socket
import struct
import threading
import time
from urllib.parse import urlsplit

import requests

from ..net.errors import FinalError

ENDPOINT = 'https://cloudflare-dns.com/dns-query'
MAX_PACKET = 4096
MAX_WORKERS = 4
HTTP_TIMEOUT = (2, 2)
RESPONSE_SECONDS = 5
CLOSE_SECONDS = 3


def _name(packet, offset, *, compressed=True):
    """Validate a bounded DNS name and return its end and normalized labels."""
    labels, end, seen, length = [], None, set(), 1
    while True:
        if offset >= len(packet) or offset in seen:
            raise ValueError('invalid DNS name')
        seen.add(offset)
        size = packet[offset]
        if size & 0xc0 == 0xc0:
            if not compressed or offset + 1 >= len(packet):
                raise ValueError('invalid DNS pointer')
            pointer = ((size & 0x3f) << 8) | packet[offset + 1]
            if pointer < 12 or pointer >= offset:
                raise ValueError('invalid DNS pointer')
            if end is None:
                end = offset + 2
            offset = pointer
            continue
        if size > 63 or offset + size + 1 > len(packet):
            raise ValueError('invalid DNS label')
        offset += 1
        if not size:
            return end if end is not None else offset, tuple(labels)
        length += size + 1
        if length > 255:
            raise ValueError('oversized DNS name')
        labels.append(packet[offset:offset + size].lower())
        offset += size


def _packet(data, *, response=False):
    if not 12 <= len(data) <= MAX_PACKET:
        raise ValueError('invalid DNS size')
    identifier, flags, questions, answers, authority, extra = struct.unpack('!6H', data[:12])
    if bool(flags & 0x8000) != response or flags & 0x7800 or questions != 1:
        raise ValueError('invalid DNS header')
    if not response and (flags & ~0x0130 or answers or authority or extra > 1):
        raise ValueError('unsupported DNS query')
    offset, labels = _name(data, 12, compressed=response)
    if offset + 4 > len(data):
        raise ValueError('truncated DNS question')
    kind, klass = struct.unpack('!2H', data[offset:offset + 4])
    if kind == 0 or klass != 1:
        raise ValueError('unsupported DNS question')
    question_end = offset = offset + 4
    for _ in range(answers + authority + extra):
        offset, record_name = _name(data, offset, compressed=response)
        if offset + 10 > len(data):
            raise ValueError('truncated DNS record')
        record_kind, _, _, size = struct.unpack('!2HIH', data[offset:offset + 10])
        if not response and (record_name or record_kind != 41):
            raise ValueError('unsupported query additional record')
        offset += 10 + size
        if offset > len(data):
            raise ValueError('truncated DNS record')
    if offset != len(data):
        raise ValueError('trailing DNS data')
    return identifier, flags, (labels, kind, klass), question_end


def _servfail(query, parsed):
    identifier, flags, _, end = parsed
    # Echo only the validated question; do not copy EDNS or untrusted records.
    return struct.pack('!6H', identifier, 0x8082 | (flags & 0x0110), 1, 0, 0, 0) + query[12:end]


class ProxyDns:
    def __init__(self, route, port=53):
        try:
            parts = urlsplit(route)
            valid = (isinstance(route, str) and parts.scheme == 'http' and
                     parts.hostname == '127.0.0.1' and parts.port is not None and
                     1 <= parts.port <= 65535 and not parts.username and not parts.password and
                     parts.path in ('', '/') and not parts.query and not parts.fragment)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise FinalError('AVD-DNS benötigt die eigene HTTP-Proxybrücke auf 127.0.0.1.')
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError('Ungültiger lokaler DNS-Port.')
        self.route = route
        self.port = port
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_WORKERS)
        self._socket = self._thread = None
        self._workers = set()
        self._sessions = set()

    def start(self):
        with self._lock:
            if self._stop.is_set():
                raise FinalError('Der AVD-DNS-Dienst wurde bereits beendet.')
            if self._socket is not None:
                return self
            owned = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    owned.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                owned.bind(('127.0.0.1', self.port))
                owned.settimeout(.2)
                self.port = owned.getsockname()[1]
                self._socket = owned
                self._thread = threading.Thread(target=self._receive, name='avd-dns', daemon=True)
                self._thread.start()
            except (OSError, RuntimeError):
                owned.close()
                self._socket = self._thread = None
                raise FinalError('AVD-DNS konnte den eigenen lokalen Port nicht öffnen; '
                                 'ein vorhandener Dienst bleibt unverändert.') from None
        return self

    def _send(self, packet, peer):
        with self._lock:
            if self._stop.is_set() or self._socket is None:
                return
            try:
                self._socket.sendto(packet, peer)
            except OSError:
                pass

    def _receive(self):
        while not self._stop.is_set():
            try:
                query, peer = self._socket.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            if peer[0] != '127.0.0.1':
                continue
            try:
                parsed = _packet(query)
            except ValueError:
                continue
            if not self._slots.acquire(blocking=False):
                self._send(_servfail(query, parsed), peer)
                continue
            with self._lock:
                if self._stop.is_set():
                    self._slots.release()
                    break
                worker = threading.Thread(target=self._answer, args=(query, parsed, peer),
                                          name='avd-dns-query', daemon=True)
                self._workers.add(worker)
                try:
                    worker.start()
                except RuntimeError:
                    self._workers.discard(worker)
                    self._slots.release()

    def _answer(self, query, parsed, peer):
        session = None
        packet = _servfail(query, parsed)
        try:
            session = requests.Session()
            session.trust_env = False
            session.proxies = {'http': self.route, 'https': self.route}
            with self._lock:
                if self._stop.is_set():
                    return
                self._sessions.add(session)
            deadline = time.monotonic() + RESPONSE_SECONDS
            with session.post(ENDPOINT, data=query, proxies=dict(session.proxies),
                              headers={'Accept': 'application/dns-message',
                                       'Content-Type': 'application/dns-message',
                                       'Accept-Encoding': 'identity'},
                              timeout=HTTP_TIMEOUT, verify=True, allow_redirects=False,
                              stream=True) as response:
                if (response.status_code != 200 or
                        response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                        != 'application/dns-message'):
                    raise ValueError('invalid DoH response')
                length = response.headers.get('Content-Length')
                if length is not None and not 12 <= int(length) <= MAX_PACKET:
                    raise ValueError('invalid DoH length')
                data = bytearray()
                # Small reads make the total response deadline effective even
                # for a peer that trickles bytes without triggering read timeout.
                for chunk in response.iter_content(chunk_size=1):
                    if self._stop.is_set() or time.monotonic() >= deadline:
                        raise ValueError('DoH response stopped')
                    data.extend(chunk)
                    if len(data) > MAX_PACKET:
                        raise ValueError('oversized DoH response')
                if length is not None and len(data) != int(length):
                    raise ValueError('truncated DoH response')
                checked = _packet(bytes(data), response=True)
                if checked[0] != parsed[0] or checked[2] != parsed[2]:
                    raise ValueError('unrelated DoH response')
                packet = bytes(data)
        except (requests.RequestException, OSError, ValueError):
            pass  # No query, proxy, IP address, or transport error is logged.
        finally:
            if session is not None:
                session.close()
            self._send(packet, peer)
            with self._lock:
                self._sessions.discard(session)
                self._workers.discard(threading.current_thread())
            self._slots.release()

    def close(self):
        with self._lock:
            self._stop.set()
            owned, receiver = self._socket, self._thread
            workers, sessions = tuple(self._workers), tuple(self._sessions)
        if owned is not None:
            owned.close()
        for session in sessions:
            session.close()
        deadline = time.monotonic() + CLOSE_SECONDS
        for thread in (receiver, *workers):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=max(0, deadline - time.monotonic()))
