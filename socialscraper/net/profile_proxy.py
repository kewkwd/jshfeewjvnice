"""Measure a profile's proxy exit IP and approximate location in one HTTPS request."""
from datetime import datetime, timezone
import ipaddress
import json
import math
from pathlib import Path
from types import SimpleNamespace

from . import tunnel
from .identity import validate_name

LOOKUP_URL = 'https://ipwho.is/?fields=success,ip,country,country_code,region,city,connection.isp'


class ProbeError(ValueError):
    pass


def _path(profiles_dir, identity):
    if identity is None:
        return Path(profiles_dir).parent / 'proxy_diagnostics' / '_settings' / 'current.json'
    validate_name(identity)
    return Path(profiles_dir).parent / 'proxy_diagnostics' / (identity + '.json')


def _ip(value):
    address = ipaddress.ip_address(value)
    if not address.is_global:raise ValueError('Keine öffentliche IP-Adresse.')
    return str(address)


def _text(value):
    if value is None:return ''
    if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Ungültige Standortangabe.')
    return value.strip()


def load(profiles_dir, identity):
    """Return a past measurement, never label it as a current connection."""
    try:
        data = json.loads(_path(profiles_dir, identity).read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or data.get('identity') != identity or data.get('version') != 1:return None
        if type(data.get('ok')) is not bool:return None
        if datetime.fromisoformat(data['checked_at']).tzinfo is None:return None
        if data['ok']:
            data['ip'] = _ip(data['ip'])
            for field in ('country', 'country_code', 'region', 'city', 'isp'):
                data[field] = _text(data.get(field))
            if type(data.get('fixed_proxy')) is not bool:return None
            if type(data.get('latency_ms')) is not int or data['latency_ms'] < 0:return None
            data['profile_country'] = _text(data.get('profile_country'))
        else:
            data['error'] = _text(data['error'])
            if not data['error']:return None
        return data
    except (OSError, ValueError, KeyError, TypeError):return None


def _resolve(cfg, identity):
    from .factory import build_identity_manager
    from . import proxymode
    if identity is None:
        if not proxymode.is_anonymous(cfg) and len(getattr(cfg, 'proxies', [])) > 1:
            raise ProbeError('Mehrere Proxys sind eingestellt. Wähle ein Profil mit festem Proxy, um dessen Verbindung zu prüfen.')
        proxy = proxymode.make_pool(cfg).acquire(proxymode.ANON_IDENTITY, country=None)
        if proxy is None:
            raise ProbeError('Kein Proxy eingestellt. Bitte zuerst in den Einstellungen einen Proxy hinterlegen.')
        return proxy, None, False
    if identity != proxymode.ANON_IDENTITY:
        proxymode.require_no_anon(cfg, 'Profilproxy prüfen')
    manager = build_identity_manager(cfg)
    country, _mobile = manager.stored(identity)
    resolver = getattr(manager, 'account_proxies', None)
    fixed = resolver.proxy_for(identity) if resolver is not None else None
    if fixed is not None:return fixed, country, True
    if len(getattr(cfg, 'proxies', [])) > 1:
        raise ProbeError('Dieses Profil hat keinen festen Proxy und mehrere Proxys stehen zur Auswahl. '
                         'Ordne ihm zuerst unter Konten einen festen Proxy zu, damit der Test eindeutig ist.')
    if country is None and identity != proxymode.ANON_IDENTITY:
        raise ProbeError('Für dieses vorbereitete Profil fehlt noch eine feste Proxy- oder Gerätezuordnung. '
                         'Bitte zuerst einen festen Profilproxy zuordnen.')
    proxy = manager.proxy_for(SimpleNamespace(name=identity, country=country))
    if proxy is None:raise ProbeError('Für dieses Profil ist kein Proxy eingerichtet. Keine direkte Verbindung getestet.')
    return proxy, country, False


def probe_profile(cfg, identity=None, *, timeout=10):
    """No browser, credentials submission, profile creation or direct fallback."""
    path = _path(cfg.profiles_dir, identity)
    old = load(cfg.profiles_dir, identity)
    previous = None
    if old:
        try:previous = _ip(old.get('ip') if old['ok'] else old.get('previous_ip'))
        except (ValueError, TypeError):pass
    result = {'version':1, 'identity':identity, 'checked_at':datetime.now(timezone.utc).isoformat(),
              'source':'ipwho.is', 'ok':False, 'previous_ip':previous}
    try:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ProbeError('Ungültiges Zeitlimit für den Proxy-Test.')
        proxy, country, fixed = _resolve(cfg, identity)
        result.update(proxy_server=proxy.server, fixed_proxy=fixed, profile_country=country)
        status, body, seconds = tunnel.https_get(proxy, LOOKUP_URL, timeout=timeout, max_bytes=16384)
        if status == 429:raise ProbeError('Der Standortdienst hat sein Abfragelimit erreicht. Bitte später erneut prüfen.')
        if status != 200:raise ProbeError('Der Standortdienst antwortet nicht erfolgreich. Kein Standort bestätigt.')
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or data.get('success') is not True:raise ValueError()
            ip = _ip(data.get('ip'))
            fields = {name:_text(data.get(name)) for name in ('country', 'country_code', 'region', 'city')}
            code = fields['country_code']
            if code and (len(code) != 2 or not code.isascii() or not code.isalpha()):raise ValueError()
            connection = data.get('connection') or {}
            if not isinstance(connection, dict):raise ValueError()
            isp = _text(connection.get('isp'))
            if not math.isfinite(seconds) or seconds < 0:raise ValueError()
        except (ValueError, TypeError):
            raise ProbeError('Der Standortdienst hat keine gültigen IP-/Standortdaten geliefert.') from None
        result.update(ok=True, ip=ip, **fields, isp=isp, latency_ms=round(seconds*1000),
                      ip_changed=(ip != previous) if previous else None)
    except ProbeError as error:result['error'] = str(error)
    except tunnel.TlsBroken:result['error'] = 'Die verschlüsselte Verbindung konnte nicht sicher bestätigt werden.'
    except (tunnel.TunnelError, OSError):result['error'] = 'Der Profilproxy oder der Standortdienst ist nicht erreichbar. Keine Ersatzverbindung verwendet.'
    except (ValueError, RuntimeError):result['error'] = 'Die Proxy-Zuordnung des Profils konnte nicht geladen werden. Keine Ersatzverbindung verwendet.'
    from ..accountvault import _write
    try:_write(path, result)
    except OSError:result['save_error'] = True
    return result


def summary(result):
    if not result:return 'Noch kein Proxy-Standorttest gespeichert.'
    checked = datetime.fromisoformat(result['checked_at']).astimezone().strftime('%d.%m.%Y %H:%M:%S')
    if not result['ok']:
        return 'Letzter Test: '+checked+'\n'+result['error']
    location = ', '.join(filter(None, (result['city'], result['region'], result['country']))) or 'Standort nicht verfügbar'
    text = ('Letzter Test: '+checked+' · Quelle: ipwho.is\nÖffentliche IP: '+result['ip']+
            '\nUngefährer Standort: '+location+'\nInternetanbieter: '+(result['isp'] or 'nicht angegeben')+
            '\nVerbindung: '+('fester Profilproxy' if result['fixed_proxy'] else 'Proxy aus Programmeinstellungen')+
            ' · '+str(result['latency_ms'])+' ms')
    if result.get('ip_changed') is True:text += '\nDie IP hat sich gegenüber dem letzten erfolgreichen Test geändert.'
    if result.get('profile_country') and result['country_code'] and result['profile_country'].upper() != result['country_code'].upper():
        text += '\nDas gemessene Land weicht vom gespeicherten Profilland ab.'
    text += '\nMomentaufnahme der Testverbindung; eine bereits offene Sitzung kann eine andere Ausgangs-IP haben.'
    if result.get('save_error'):text += '\nDas Ergebnis konnte nicht dauerhaft gespeichert werden.'
    return text
