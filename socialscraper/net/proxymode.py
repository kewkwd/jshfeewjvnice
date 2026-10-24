"""Proxy-Modus (Einstellung `proxy_mode`) und die harte Regel „kostenlose Proxys/Tor nie mit einem Konto“.

Modi:
    aus    kein Proxy (Standard). Eine nicht leere `proxies`-Liste bleibt wie bisher wirksam.
    liste  die eigene `proxies`-Liste (z. B. gekaufter Proxy) – darf mit Konten benutzt werden.
    frei   kostenlose öffentliche Proxys (net/freeproxy.py) – NUR ohne Konto.
    tor    Tor (net/tor.py, Tor Browser oder Tor-Dienst muss laufen) – NUR ohne Konto.

Im Modus frei/tor wird technisch erzwungen (nicht nur dokumentiert):
  * Abrufe laufen nur mit der anonymen Identität `anon_public` (kein Cookie, eigenes Browserprofil);
    `IdentityManager.get` lehnt jede andere Identität ab, `backends/base._apply_cookies` lädt keine Cookies.
  * login, check, import-cookies, Instagram, X und Abrufe, die ein Konto brauchen (TikTok-Kommentare,
    -Hashtags, -Suche), werden mit `AccountsForbidden` abgelehnt (cli, scheduler, discovery, Adapter).
    Reddit geht ohne Konto und ist deshalb erlaubt (Reddit sperrt kostenlose Proxys und Tor aber oft; der Adapter
    meldet das klar).
  * Gibt es keinen funktionierenden Proxy, wird NICHT stillschweigend ohne Proxy abgerufen
    (`ProxyUnavailable`).

API für die Oberfläche (ohne Tk):
    describe(cfg, root=None)     -> (modus, text, ok)       schnell, nur lokale Prüfung
    ensure_ready(cfg, root=None) -> (ok, meldung)           kann dauern (Netz): im Hintergrund-Thread aufrufen
"""
import logging
import threading

from ..config import PROXY_MODES
from . import freeproxy, tor
from .proxy import ProxyPool

log = logging.getLogger(__name__)

ANON_IDENTITY = 'anon_public'
ANON_COUNTRY = 'de'
NO_ACCOUNTS = 'Mit kostenlosen Proxys/Tor werden keine Konten benutzt – aus Sicherheitsgründen.'
LABELS = {'aus': 'Kein Proxy', 'liste': 'Eigene Proxy-Liste', 'frei': 'Kostenlose Proxys', 'tor': 'Tor'}


class AccountsForbidden(ValueError):
    """Ein Konto sollte zusammen mit kostenlosem Proxy oder Tor benutzt werden – verboten."""


class ProxyUnavailable(RuntimeError):
    """Der gewählte Proxy-Modus hat gerade keinen funktionierenden Proxy; es wird nicht ohne Proxy abgerufen."""
    retryable = False


def mode(cfg):
    value = getattr(cfg, 'proxy_mode', 'aus')
    return value if isinstance(value, str) and value in PROXY_MODES else 'aus'


def is_anonymous(cfg):
    return mode(cfg) in ('frei', 'tor')


def is_anonymous_im(im):
    """Benutzt dieser IdentityManager einen anonymen Proxy-Pool (frei/Tor)?"""
    return getattr(getattr(im, 'pool', None), 'anonymous', False) is True


def uses_proxy(cfg):
    return mode(cfg) != 'aus' or bool(getattr(cfg, 'proxies', None))


def root_for(cfg):
    from .. import runlock
    return runlock.root_for_db(cfg.db_path)


def refuse_accounts(detail=None):
    raise AccountsForbidden(NO_ACCOUNTS + (' ' + detail if detail else ''))


def require_no_anon(cfg, what=None):
    """Für login/check/import-cookies: im Modus frei/tor ablehnen."""
    if is_anonymous(cfg):
        refuse_accounts(f'({what} ist hier nicht möglich; Proxy-Modus in den Einstellungen auf „aus“ oder „liste“ stellen.)'
                        if what else None)


def assert_fetch_allowed(cfg, platform, max_comments=0, kind='video'):
    """Im Modus frei/tor nur Abrufe ohne Konto: YouTube und Reddit immer, TikTok-Video/Profil nur ohne Kommentare."""
    if not is_anonymous(cfg):
        return
    if platform == 'instagram':
        refuse_accounts('Instagram braucht immer ein Konto.')
    if platform == 'x':
        refuse_accounts('X braucht immer ein Konto.')
    if platform == 'tiktok':
        if kind in ('hashtag', 'search'):
            refuse_accounts('TikTok-Hashtags und -Suche brauchen ein Konto.')
        if int(max_comments or 0) > 0:
            refuse_accounts('TikTok-Kommentare brauchen ein Konto – ohne Konto nur mit 0 Kommentaren (--max-comments 0).')


def anon_run(cfg):
    """(Identität, Land) für Abrufe im Modus frei/tor."""
    return ANON_IDENTITY, ANON_COUNTRY


def ytdlp_proxy_url(proxy):
    """Proxy-Adresse für yt-dlp; SOCKS immer mit Namensauflösung beim Proxy (socks5h)."""
    url = proxy.url
    return url.replace('socks5://', 'socks5h://', 1) if getattr(proxy, 'scheme', None) == 'socks5' else url


def note_failure(im, proxy):
    """Meldet einen fehlgeschlagenen Abruf an den Pool (kostenlose Proxys werden dann gesperrt)."""
    reporter = getattr(getattr(im, 'pool', None), 'report_failure', None)
    if proxy is not None and callable(reporter):
        try:
            reporter(proxy)
        except Exception:
            log.debug('Proxy-Fehler konnte nicht gemeldet werden', exc_info=True)


def note_success(im, proxy):
    """Meldet einen gelungenen Abruf (Gesundheitsstatistik der kostenlosen Proxys)."""
    reporter = getattr(getattr(im, 'pool', None), 'report_success', None)
    if proxy is not None and callable(reporter):
        try:
            reporter(proxy)
        except Exception:
            log.debug('Proxy-Erfolg konnte nicht gemeldet werden', exc_info=True)


# ---------------------------------------------------------------- Pools

class FreePool:
    """Pool-Schnittstelle (wie ProxyPool) über kostenlose Proxys, mit Rotation bei Fehlern."""
    anonymous = True

    def __init__(self, root, limit=10):
        self.root, self.limit = root, limit
        self._current = None
        self._lock = threading.Lock()

    def acquire(self, identity, country=None, sticky=True):
        with self._lock:
            if self._current is not None:
                return self._current
            proxy = freeproxy.acquire(self.root, limit=self.limit)
            if proxy is None:
                raise ProxyUnavailable('Keine funktionierenden kostenlosen Proxys gefunden. Der Abruf wurde NICHT ohne '
                                       'Proxy ausgeführt (deine IP bleibt verborgen). Später noch einmal versuchen '
                                       '(Befehl „proxies refresh“) oder den Proxy-Modus ändern.')
            self._current = proxy
            log.info('Kostenloser Proxy gewählt: %s:%s (%s)', proxy.host, proxy.port, proxy.scheme)
            return proxy

    def rotate(self, identity=None):
        with self._lock:
            self._current = None

    def report_failure(self, proxy):
        freeproxy.report_failure(proxy, self.root)
        with self._lock:
            if self._current is not None and (self._current.host, self._current.port) == (proxy.host, proxy.port):
                self._current = None


    def report_success(self, proxy):
        freeproxy.report_success(proxy, self.root)


class TorPool:
    """Pool-Schnittstelle über den lokalen Tor-Anschluss."""
    anonymous = True

    def __init__(self, root=None):
        self.root = root
        self._proxy = None
        self._lock = threading.Lock()

    def acquire(self, identity, country=None, sticky=True):
        with self._lock:
            if self._proxy is None:
                if self.root is not None:
                    found = tor.ensure_running(self.root)   # nutzt ein laufendes Tor oder startet das mitgelieferte
                else:
                    found = tor.find(verify=True)
                if not found['ok']:
                    raise ProxyUnavailable(found['message'] + ' Der Abruf wurde NICHT ohne Tor ausgeführt.')
                self._proxy = tor.proxy(found['port'])
                log.info('Tor-Anschluss %s wird benutzt', found['port'])
            return self._proxy

    def rotate(self, identity=None):
        with self._lock:
            self._proxy = None
        tor.new_circuit()

    def report_failure(self, proxy):
        with self._lock:
            self._proxy = None


def make_pool(cfg):
    """Proxy-Pool passend zum Modus (ersetzt `ProxyPool(cfg.proxies, ...)` in cli.make_runtime)."""
    selected = mode(cfg)
    if selected == 'frei':
        return FreePool(root_for(cfg))
    if selected == 'tor':
        return TorPool(root_for(cfg))
    if selected == 'liste' and not cfg.proxies:
        raise ProxyUnavailable('Proxy-Modus „liste“ gewählt, aber keine Proxys eingetragen. '
                               'Der Abruf wurde NICHT ohne Proxy ausgeführt.')
    return ProxyPool(cfg.proxies, cfg.proxy_sticky_ttl)


# ---------------------------------------------------------------- Für die Oberfläche

def describe(cfg, root=None):
    """Kurzbeschreibung: (modus, text, ok). Prüft nur lokal (Datei, lokaler Port), kein Netz."""
    selected = mode(cfg)
    if selected == 'aus':
        if getattr(cfg, 'proxies', None):
            return selected, f'Kein Proxy-Modus gewählt; die eingetragene Proxy-Liste ({len(cfg.proxies)}) wird benutzt.', True
        return selected, 'Kein Proxy: Abrufe laufen mit deiner eigenen IP-Adresse.', True
    if selected == 'liste':
        if not cfg.proxies:
            return selected, 'Eigene Proxy-Liste ist leer – bitte einen Proxy eintragen.', False
        return selected, f'Eigene Proxy-Liste mit {len(cfg.proxies)} Eintrag/Einträgen. Darf mit Konten benutzt werden.', True
    if selected == 'frei':
        info = freeproxy.status(root if root is not None else root_for(cfg))
        warn = ' Nur ohne Konto (Instagram, X und TikTok-Kommentare sind gesperrt).'
        if info['gueltig']:
            return selected, f'{info["vorhanden"]} kostenlose Proxys bereit (Liste {info["alter_s"] // 60} Min. alt).' + warn, True
        return selected, 'Noch keine geprüften kostenlosen Proxys – „Proxys aktualisieren“ drücken.' + warn, False
    port = tor.detect(timeout=0.5)
    if port is None:
        root = root if root is not None else root_for(cfg)
        if tor.tor_exe(root).exists():
            return selected, ('Tor läuft noch nicht; das mitgelieferte Tor wird beim ersten Abruf automatisch gestartet '
                              '(oder „Tor starten“ / Befehl „proxies tor-start“). Nur ohne Konto.'), True
        return selected, tor.missing_message(root) + ' ' + tor.NOT_RUNNING, False
    return selected, f'Tor-Anschluss gefunden ({port}). Nur ohne Konto (Instagram, X und TikTok-Kommentare sind gesperrt).', True


def ensure_ready(cfg, root=None):
    """Macht den Modus einsatzbereit und prüft ihn (kann Sekunden bis Minuten dauern). Gibt (ok, meldung) zurück."""
    selected = mode(cfg)
    root = root if root is not None else root_for(cfg)
    if selected == 'aus':
        return True, 'Kein Proxy nötig.'
    if selected == 'liste':
        if not cfg.proxies:
            return False, 'Keine Proxys eingetragen.'
        try:
            ProxyPool(cfg.proxies)
        except ValueError as error:
            return False, f'Proxy-Eintrag ungültig: {error}'
        return True, f'{len(cfg.proxies)} Proxy-Eintrag/Einträge in der Liste.'
    if selected == 'tor':
        found = tor.ensure_running(root)
        return found['ok'], found['message']
    info = freeproxy.status(root)
    if info['gueltig']:
        return True, f'{info["vorhanden"]} kostenlose Proxys bereit.'
    stats = freeproxy.refresh(root)
    if stats['funktionierend'] == 0:
        extra = f' ({stats["zertifikatsfehler"]} mit Zertifikatsfehler verworfen)' if stats['zertifikatsfehler'] else ''
        return False, ('Keine funktionierenden kostenlosen Proxys gefunden' + extra +
                       '. Später noch einmal versuchen; ohne Proxy wird nicht abgerufen.')
    fastest = stats['schnellste'][0]['ms']
    return True, (f'{stats["funktionierend"]} von {stats["getestet"]} getesteten Proxys funktionieren, '
                  f'der schnellste braucht {fastest} ms.')
