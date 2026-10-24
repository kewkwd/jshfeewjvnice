"""Kostenlose öffentliche Proxys: beschaffen, testen, zwischenspeichern, rotieren.

    stats = refresh(root)            # Listen laden, parallel testen, die schnellsten behalten
    proxy = acquire(root)            # nächster funktionierender Proxy (oder None)
    report_failure(proxy, root)      # Proxy sperren; acquire() liefert dann den nächsten

WICHTIG: Kostenlose Proxys sind fremde Rechner. Sie dürfen NIE zusammen mit einem Konto benutzt werden
(`proxymode` erzwingt das). Zum Prüfen wird eine https-Adresse über den Proxy aufgerufen, das Zertifikat
wird geprüft; ein Proxy mit Zertifikatsfehler greift in die Verbindung ein und wird sofort verworfen.

Zwischenspeicher: `<root>/data/freeproxies.json` (Zeitstempel, 20 Minuten gültig, atomar geschrieben).
Es werden keine Zugangsdaten und keine eigene IP gespeichert.
"""
import concurrent.futures
import ipaddress
import json
import logging
import os
import pathlib
import random
import tempfile
import threading
import time
import urllib.request

from .proxy import Proxy
from . import tunnel

log = logging.getLogger(__name__)

# (Name, Adresse, Protokoll für Zeilen ohne "://")  – öffentliche Listen auf GitHub bzw. Proxyscrape.
SOURCES = [
    ('monosans-http', 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt', 'http'),
    ('monosans-socks5', 'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt', 'socks5'),
    ('proxyscrape-http', 'https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies'
                         '&protocol=http&proxy_format=ipport&format=text&timeout=5000', 'http'),
    ('proxyscrape-socks5', 'https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies'
                           '&protocol=socks5&proxy_format=ipport&format=text&timeout=5000', 'socks5'),
    ('thespeedx-http', 'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt', 'http'),
    ('thespeedx-socks5', 'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt', 'socks5'),
    ('proxifly-http', 'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt', 'http'),
    ('proxifly-socks5', 'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt', 'socks5'),
]
TEST_URL = 'https://api.ipify.org/'      # harmlos: gibt nur die Ausgangs-IP zurück
VALID_SECONDS = 20 * 60                  # so lange gilt die zwischengespeicherte Liste
BAN_SECONDS = 24 * 3600                  # gesperrte Proxys / Listen mit Zertifikatsfunden
MAX_LIST_BYTES = 3_000_000
PER_SOURCE_SAMPLE = 80                   # große Listen nur stichprobenartig testen
TLS_SOURCE_LIMIT = 3                     # ab so vielen Zertifikatsfunden wird eine Liste 24 h übersprungen

_lock = threading.RLock()
_memory_ban = {}                         # url -> Zeit; gilt auch ohne Datei im selben Prozess


class FreeProxyError(RuntimeError):
    retryable = False


def cache_path(root=None):
    return pathlib.Path(root if root is not None else '.') / 'data' / 'freeproxies.json'


# ---------------------------------------------------------------- Zwischenspeicher

def _read(root):
    try:
        data = json.loads(cache_path(root).read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('proxies'), list):
            return data
    except (OSError, ValueError):
        pass
    return {'fetched_at': 0, 'proxies': [], 'banned': {}, 'sources': {}, 'stats': {}}


def _write(root, data):
    path = cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.freeproxies-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = pathlib.Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=1)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _fresh_bans(bans, now):
    return {url: ts for url, ts in (bans or {}).items() if isinstance(ts, (int, float)) and now - ts < BAN_SECONDS}


# ---------------------------------------------------------------- Listen laden

def parse_list(text, default_scheme):
    """Zeilen `ip:port` oder `scheme://ip:port` -> Liste von Proxy-Adressen (nur http und socks5)."""
    out, seen = [], set()
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '://' not in line:
            line = f'{default_scheme}://{line}'
        scheme = line.split('://', 1)[0].lower()
        if scheme not in ('http', 'socks5'):
            continue
        try:
            proxy = Proxy.parse(line)
        except ValueError:
            continue
        try:
            address = ipaddress.ip_address(proxy.host)
        except ValueError:
            continue  # nur IP-Adressen; Namen und Zugangsdaten in Listen sind verdächtig
        if proxy.user is not None or not address.is_global:
            continue  # keine privaten/lokalen Adressen (Zugriff aufs eigene Netz)
        if line in seen:
            continue
        seen.add(line)
        out.append(proxy.server)
    return out


def fetch_list(url, timeout=15):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:   # prüft das Zertifikat der Quelle
        return response.read(MAX_LIST_BYTES).decode('utf-8', 'replace')


# ---------------------------------------------------------------- Test eines Proxys

def probe(url, real_ip=None, timeout=8.0, test_url=TEST_URL):
    """Prüft einen Proxy. Ergebnis: dict(ok, ms, ip, error) mit error in 'tls', 'timeout', 'proxy', 'antwort', 'gleiche-ip'."""
    proxy = Proxy.parse(url)
    try:
        status, body, seconds = tunnel.https_get(proxy, test_url, timeout=timeout, max_bytes=256)
    except tunnel.TlsBroken:
        return {'ok': False, 'error': 'tls'}
    except (TimeoutError, OSError) as error:
        kind = 'timeout' if isinstance(error, TimeoutError) or 'timed out' in str(error).lower() else 'proxy'
        return {'ok': False, 'error': kind}
    except Exception:
        return {'ok': False, 'error': 'proxy'}
    ip = body.strip()
    if status != 200 or not _looks_like_ip(ip):
        return {'ok': False, 'error': 'antwort'}
    if real_ip and ip == real_ip:
        return {'ok': False, 'error': 'gleiche-ip'}   # versteckt die eigene Adresse nicht
    return {'ok': True, 'ms': int(seconds * 1000), 'ip': ip}


def _looks_like_ip(text):
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def real_ip(timeout=8.0):
    """Die eigene Ausgangs-IP (nur im Speicher, wird nie gespeichert oder protokolliert)."""
    try:
        status, body, _ = tunnel.https_get(None, TEST_URL, timeout=timeout, max_bytes=256)
        return body.strip() if status == 200 and _looks_like_ip(body.strip()) else None
    except Exception:
        return None


# ---------------------------------------------------------------- Aktualisieren

def refresh(root=None, limit=10, max_candidates=300, workers=40, timeout=8.0,
            sources=None, fetch=None, tester=None, own_ip=None, progress=None):
    """Listen laden, Kandidaten parallel testen, die schnellsten `limit` speichern. Gibt eine Statistik zurück.

    `fetch(url) -> Text` und `tester(url) -> dict` sind austauschbar (Tests). Statistik-Schlüssel:
    quellen_ok, quellen_fehler, kandidaten, getestet, funktionierend, zertifikatsfehler, nicht_erreichbar,
    schnellste (Liste von {proxy, ms}), verdaechtige_quellen, dauer_s.
    """
    started = time.monotonic()
    now = time.time()
    fetch = fetch or fetch_list
    sources = SOURCES if sources is None else sources
    with _lock:
        previous = _read(root)
    bans = _fresh_bans(previous.get('banned'), now)
    bans.update({u: t for u, t in _memory_ban.items() if now - t < BAN_SECONDS})
    source_info = {name: dict(info) for name, info in (previous.get('sources') or {}).items()}

    def say(text):
        if progress:
            progress(text)
        log.info('%s', text)

    stats = {'quellen_ok': 0, 'quellen_fehler': [], 'quellen_uebersprungen': [], 'kandidaten': 0, 'getestet': 0,
             'funktionierend': 0, 'zertifikatsfehler': 0, 'nicht_erreichbar': 0, 'schnellste': [],
             'verdaechtige_quellen': [], 'dauer_s': 0.0}
    pool, origin = [], {}
    for name, url, scheme in sources:
        info = source_info.get(name) or {}
        if now - (info.get('tls_blocked_at') or 0) < BAN_SECONDS:
            stats['quellen_uebersprungen'].append(name)
            continue
        try:
            found = parse_list(fetch(url), scheme)
        except Exception as error:
            stats['quellen_fehler'].append(f'{name}: {type(error).__name__}')
            continue
        stats['quellen_ok'] += 1
        random.shuffle(found)
        found = [u for u in found if u not in bans][:PER_SOURCE_SAMPLE]
        info['kandidaten'] = len(found)
        source_info[name] = info
        for u in found:
            if u not in origin:
                origin[u] = name
                pool.append(u)
    random.shuffle(pool)
    pool = pool[:max_candidates]
    stats['kandidaten'] = len(pool)
    say(f'{len(pool)} Kandidaten aus {stats["quellen_ok"]} Listen, teste parallel ...')

    if tester is None:
        ip_now = own_ip if own_ip is not None else real_ip(timeout)
        tester = lambda u: probe(u, ip_now, timeout)   # noqa: E731
    good, tls_by_source = [], {}
    if pool:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, len(pool)))) as executor:
            futures = {executor.submit(tester, u): u for u in pool}
            for future in concurrent.futures.as_completed(futures):
                u = futures[future]
                stats['getestet'] += 1
                try:
                    result = future.result()
                except Exception:
                    result = {'ok': False, 'error': 'proxy'}
                if result.get('ok'):
                    good.append({'url': u, 'ms': int(result.get('ms', 0)), 'ip': result.get('ip'), 'ok': 0, 'fail': 0})
                else:
                    stats['nicht_erreichbar'] += 1
                    if result.get('error') == 'tls':
                        stats['zertifikatsfehler'] += 1
                        bans[u] = now
                        name = origin.get(u)
                        tls_by_source[name] = tls_by_source.get(name, 0) + 1
    stats['nicht_erreichbar'] -= stats['zertifikatsfehler']
    good.sort(key=lambda item: item['ms'])
    stats['funktionierend'] = len(good)
    keep = good[:max(0, limit)]
    stats['schnellste'] = [{'proxy': item['url'], 'ms': item['ms']} for item in keep]
    for name, count in tls_by_source.items():
        info = source_info.setdefault(name, {})
        info['tls_funde'] = info.get('tls_funde', 0) + count
        stats['verdaechtige_quellen'].append(f'{name} ({count} Zertifikatsfehler)')
        if count >= TLS_SOURCE_LIMIT:
            info['tls_blocked_at'] = now
    stats['dauer_s'] = round(time.monotonic() - started, 1)
    stats['von'] = origin_summary(keep, origin)
    data = {'fetched_at': now, 'proxies': keep, 'banned': bans, 'sources': source_info,
            'stats': {k: v for k, v in stats.items() if k != 'schnellste'}}
    with _lock:
        _write(root, data)
    say(f'{stats["funktionierend"]} funktionieren, {len(keep)} behalten, {stats["zertifikatsfehler"]} mit Zertifikatsfehler')
    return stats


def origin_summary(keep, origin):
    counts = {}
    for item in keep:
        name = origin.get(item['url'], '?')
        counts[name] = counts.get(name, 0) + 1
    return counts


# ---------------------------------------------------------------- Benutzen

def _to_proxy(item):
    proxy = Proxy.parse(item['url'])
    proxy.anonymous = True
    return proxy


def acquire(root=None, auto_refresh=True, **refresh_options):
    """Der nächste funktionierende Proxy (der schnellste nicht gesperrte). `None`, wenn keiner da ist.

    Ist die Liste älter als 20 Minuten oder leer, wird (bei `auto_refresh`) zuerst neu geladen und getestet.
    """
    with _lock:
        now = time.time()
        data = _read(root)
        stale = now - (data.get('fetched_at') or 0) > VALID_SECONDS
        usable = [item for item in data['proxies'] if item['url'] not in _memory_ban]
        if (stale or not usable) and auto_refresh:
            refresh(root, **refresh_options)
            data = _read(root)
            usable = [item for item in data['proxies'] if item['url'] not in _memory_ban]
        if not usable:
            return None
        return _to_proxy(usable[0])


def report_failure(proxy, root=None):
    """Proxy sperren (auch in der Datei); `acquire` liefert danach den nächsten."""
    if proxy is None:
        return
    url = proxy.server
    with _lock:
        _memory_ban[url] = time.time()
        data = _read(root)
        if not data['proxies'] and not data.get('fetched_at'):
            return
        for item in data['proxies']:
            if item['url'] == url:
                item['fail'] = item.get('fail', 0) + 1
        data['proxies'] = [item for item in data['proxies'] if item['url'] != url]
        data.setdefault('banned', {})[url] = time.time()
        try:
            _write(root, data)
        except OSError:
            log.warning('Proxy-Liste konnte nicht gespeichert werden')


def report_success(proxy, root=None):
    if proxy is None:
        return
    url = proxy.server
    with _lock:
        data = _read(root)
        for item in data['proxies']:
            if item['url'] == url:
                item['ok'] = item.get('ok', 0) + 1
                try:
                    _write(root, data)
                except OSError:
                    pass
                return


def status(root=None):
    """Gesundheitsübersicht für Oberfläche und `proxies status`."""
    with _lock:
        data = _read(root)
    now = time.time()
    age = now - (data.get('fetched_at') or 0) if data.get('fetched_at') else None
    usable = [item for item in data['proxies'] if item['url'] not in _memory_ban]
    return {'vorhanden': len(usable), 'alter_s': None if age is None else int(age),
            'gueltig': age is not None and age <= VALID_SECONDS and bool(usable),
            'gesperrt': len(_fresh_bans(data.get('banned'), now)),
            'proxys': [{'proxy': item['url'], 'ms': item.get('ms'), 'ok': item.get('ok', 0), 'fail': item.get('fail', 0)}
                       for item in usable],
            'quellen': data.get('sources') or {}, 'letzte_statistik': data.get('stats') or {}}


def clear_memory():
    """Nur für Tests: Sperren im Speicher vergessen."""
    _memory_ban.clear()
