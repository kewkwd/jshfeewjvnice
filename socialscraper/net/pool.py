import time
import logging
from contextlib import contextmanager

log = logging.getLogger(__name__)

class SessionPool:
    def __init__(self, backend, im, size=4, ttl=1800, headless=False):
        if size < 1 or ttl <= 0:
            raise ValueError("Pool-Größe und Session-TTL müssen positiv sein")
        self.backend, self.im = backend, im
        self.size, self.ttl = size, ttl
        self.headless = headless
        self._live: dict[tuple, tuple] = {}
        self._busy = set()

    def _discard(self, key):
        entry = self._live.pop(key, None)
        if entry is not None:
            try:
                self.backend.close(entry[0])
            except Exception:
                log.warning("Browser-Session konnte nicht vollständig schließen (vermutlich schon beendet)")
                log.debug("Einzelheiten", exc_info=True)

    def _evict(self):
        now = time.monotonic()
        for k, (h, exp) in list(self._live.items()):
            if exp <= now and k not in self._busy:
                self._discard(k)

    def _make_room(self):
        while len(self._live) >= self.size:
            k = next((k for k in self._live if k not in self._busy), None)
            if k is None:
                raise RuntimeError("Alle Browser-Sessions sind bereits in Benutzung")
            self._discard(k)

    @contextmanager
    def acquire(self, identity_name, platform, country="us", mobile=False):
        key = (identity_name, platform, country.lower(), bool(mobile))
        if key in self._busy:
            raise RuntimeError("Browser-Session ist bereits in Benutzung")
        self._evict()
        if key in self._live:
            handle, _ = self._live.pop(key)
        else:
            self._make_room()
            self.backend.prepare(platform)
            ident = self.im.get(identity_name, country, mobile)
            proxy = self.im.proxy_for(ident)
            from .errors import precheck_local_proxy
            precheck_local_proxy(proxy)   # lokaler Proxy aus? sofort klare Meldung statt Browser-Wartezeit
            handle = self.backend.open(ident, proxy, headless=self.headless, platform=platform)
        self._live[key] = (handle, time.monotonic() + self.ttl)
        self._busy.add(key)
        try:
            yield self.backend.page(handle)
        except BaseException as error:
            marker = getattr(self.backend, 'failed_profile', None)
            if callable(marker):marker(handle)
            if isinstance(error, Exception):
                self._report_proxy_failure(handle)
            self._discard(key)
            raise
        finally:
            self._busy.discard(key)
            if key in self._live:
                self._live[key] = (handle, time.monotonic() + self.ttl)

    def _report_proxy_failure(self, handle):
        # Kostenlose Proxys werden bei einem Fehler gesperrt; der nächste Versuch bekommt den nächsten Proxy.
        reporter = getattr(getattr(self.im, 'pool', None), 'report_failure', None)
        proxy = getattr(handle, 'proxy', None)
        if callable(reporter) and proxy is not None and getattr(proxy, 'anonymous', False) is True:
            try:
                reporter(proxy)
            except Exception:
                log.debug("Proxy-Fehler konnte nicht gemeldet werden", exc_info=True)

    def drop(self, identity_name, platform=None):
        for key in list(self._live):
            if key[0] == identity_name and (platform is None or key[1] == platform):
                if key in self._busy:
                    raise RuntimeError("Eine benutzte Browser-Session kann nicht entfernt werden")
                self._discard(key)

    def shutdown(self):
        for key in list(self._live):
            self._discard(key)
        self._busy.clear()
