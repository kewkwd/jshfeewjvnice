import random
import time
import urllib.parse
from dataclasses import dataclass

@dataclass
class Proxy:
    scheme: str
    host: str
    port: int
    user: str | None = None
    password: str | None = None
    country: str | None = None
    # True bei kostenlosen Proxys und Tor: nie zusammen mit einem Konto verwenden (siehe proxymode).
    anonymous: bool = False

    @property
    def server(self):
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'{self.scheme}://{host}:{self.port}'

    @property
    def url(self):
        if self.user is None: return self.server
        auth = urllib.parse.quote(self.user, safe='') + ':' + urllib.parse.quote(self.password or '', safe='')
        return self.server.replace('://', '://' + auth + '@', 1)

    @property
    def playwright(self):
        result = {'server': self.server}
        if self.user is not None: result.update(username=self.user, password=self.password or '')
        return result

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, str) or not raw.strip(): raise ValueError('Proxy-URL fehlt')
        url = urllib.parse.urlsplit(raw.strip() if '://' in raw else 'http://' + raw.strip())
        if url.scheme not in ('http', 'https', 'socks5', 'socks5h', 'socks4') or not url.hostname:
            raise ValueError('Proxy-URL hat keinen gültigen Host oder ein unbekanntes Protokoll')
        port = url.port if url.port is not None else {'http': 80, 'https': 443, 'socks5': 1080, 'socks5h':1080, 'socks4': 1080}[url.scheme]
        if port < 1 or port > 65535: raise ValueError('Proxy-Port ungültig')
        query = urllib.parse.parse_qs(url.query)
        country = (query.get('country') or [None])[0]
        return cls('socks5' if url.scheme == 'socks5h' else url.scheme, url.hostname, port,
                   urllib.parse.unquote(url.username) if url.username is not None else None,
                   urllib.parse.unquote(url.password) if url.password is not None else None,
                   country.lower() if country else None)

class ProxyPool:
    anonymous = False  # Proxys der eigenen Liste (z. B. gekauft) dürfen mit Konten benutzt werden

    def __init__(self, raw_list, sticky_ttl=86400):
        self.proxies = [Proxy.parse(raw) for raw in raw_list]
        self.sticky_ttl = sticky_ttl
        self._bind = {}

    def _sessionize(self, proxy, identity, country):
        # Anbieter-Login bleibt unverändert; Sessionvorlagen sind ausdrücklich konfigurierbar.
        user = proxy.user
        if user is not None:
            user = user.replace('{identity}', identity).replace('{country}', (country or proxy.country or '').lower())
        return Proxy(proxy.scheme, proxy.host, proxy.port, user, proxy.password, country or proxy.country)

    def acquire(self, identity, country=None, sticky=True):
        country = country.lower() if country else None
        now = time.monotonic()
        key = (identity, country)
        if sticky and key in self._bind:
            proxy, expires = self._bind[key]
            if expires > now: return proxy
        candidates = [proxy for proxy in self.proxies if country is None or proxy.country in (None, country)]
        if not candidates:
            if self.proxies: raise ValueError('Kein konfigurierter Proxy passt zum Land der Identity')
            import os
            if os.environ.get('SS_REQUIRE_PROXY') == '1':
                raise ValueError('Kein Proxy gewählt; direkte Testverbindung gesperrt.')
            return None
        proxy = self._sessionize(random.choice(candidates), identity, country)
        if sticky: self._bind[key] = (proxy, now + self.sticky_ttl)
        return proxy

    def rotate(self, identity):
        for key in list(self._bind):
            if key[0] == identity: self._bind.pop(key)


