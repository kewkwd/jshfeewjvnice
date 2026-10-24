"""Explicit, manual IP reputation diagnostics; never selects or rotates proxies."""
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
from urllib.parse import quote

from . import tunnel, profile_proxy
from .identity import validate_name

_CHECK_LOCK = threading.Lock()
_IP_URL = 'https://api.ipify.org'


class QualityError(ValueError):
    pass


def _key_path(cfg):
    return Path(cfg.db_path).parent / 'ipqs_key.json'


def _validate_key(key):
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', key):
        raise QualityError('Bitte einen gültigen IPQS-API-Schlüssel eingeben, keine URL.')
    return key


def _dpapi(data, *, decrypt=False):
    """Current-user DPAPI only. Never falls back to plaintext or machine scope."""
    if os.name != 'nt':
        raise QualityError('Der geschützte IPQS-Schlüsselspeicher benötigt Windows.')

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    protect = crypt.CryptProtectData
    protect.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob),
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    protect.restype = wintypes.BOOL
    unprotect = crypt.CryptUnprotectData
    unprotect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    unprotect.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming, outgoing = Blob(len(data), buffer), Blob()
    # CRYPTPROTECT_UI_FORBIDDEN: no unexpected authentication windows.
    if decrypt:
        ok = unprotect(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        ok = protect(ctypes.byref(incoming), 'Profilwerk IPQS', None, None, None, 1, ctypes.byref(outgoing))
    try:
        if not ok:
            raise QualityError('Windows konnte den API-Schlüssel nicht schützen oder entschlüsseln.')
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.memset(buffer, 0, len(data))
        if outgoing.pbData:
            ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
            kernel.LocalFree(outgoing.pbData)


def save_key(cfg, key):
    from ..accountvault import _write
    sealed = _dpapi(_validate_key(key.strip()).encode('ascii'))
    _write(_key_path(cfg), {'version': 1, 'dpapi_user': base64.b64encode(sealed).decode('ascii')})


def delete_key(cfg):
    _key_path(cfg).unlink(missing_ok=True)


def has_key(cfg):
    return _key_path(cfg).is_file()


def _load_key(cfg):
    try:
        path = _key_path(cfg)
        if path.stat().st_size > 16384:
            raise ValueError()
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 1:
            raise ValueError()
        sealed = base64.b64decode(data['dpapi_user'], validate=True)
        return _validate_key(_dpapi(sealed, decrypt=True).decode('ascii'))
    except FileNotFoundError:
        raise QualityError('Bitte zuerst den IPQS-API-Schlüssel lokal hinterlegen.') from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise QualityError('Der IPQS-Schlüssel fehlt oder lässt sich für dieses Windows-Konto nicht öffnen. Bitte neu hinterlegen.') from None


def _result_path(cfg, identity):
    if identity is None:
        return Path(cfg.db_path).parent / 'ipqs_diagnostics' / '_settings' / 'current.json'
    else:
        filename = validate_name(identity) + '.json'
    return Path(cfg.db_path).parent / 'ipqs_diagnostics' / filename


def _exit_ip(proxy):
    if proxy is None:
        raise QualityError('Kein Proxy eingerichtet. Keine direkte Ersatzverbindung verwendet.')
    status, body, _ = tunnel.https_get(proxy, _IP_URL, timeout=10, max_bytes=128)
    if status != 200:
        raise QualityError('Die Ausgangs-IP des Proxys konnte nicht bestätigt werden.')
    return profile_proxy._ip(body.strip())


def _lookup(proxy, key, ip):
    # Fixed HTTPS origin, no redirects, no user-supplied URL, bounded body.
    # Never log the URL, the key, the remote message or raw network exceptions.
    url = 'https://ipqualityscore.com/api/json/ip/' + quote(key, safe='') + '/' + quote(ip, safe='') + '?strictness=1'
    status, body, _ = tunnel.https_get(proxy, url, timeout=15, max_bytes=32768)
    if status in (401, 403):
        raise QualityError('IPQS hat den Zugriff abgelehnt. Schlüssel und Tarif im IPQS-Konto prüfen.')
    if status == 429:
        raise QualityError('IPQS-Abfragelimit erreicht. Es wird nicht automatisch erneut abgefragt.')
    if status != 200:
        raise QualityError('IPQS ist derzeit nicht verfügbar. Die IP wurde nicht bewertet.')
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError()
        if data.get('success') is not True:
            raise QualityError('IPQS konnte die IP nicht bewerten. Schlüssel oder verfügbares Kontingent im IPQS-Konto prüfen.')
        score = data['fraud_score']
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError()
        flags = {}
        for name in ('proxy', 'vpn', 'tor', 'recent_abuse', 'bot_status'):
            value = data.get(name)
            if value is not None and type(value) is not bool:
                raise ValueError()
            flags[name] = value
        return score, flags
    except QualityError:
        raise
    except (ValueError, KeyError, TypeError):
        raise QualityError('IPQS hat keine gültige Bewertung geliefert.') from None


def check(cfg, identity=None):
    """One lookup on user request, over the selected proxy, with no retries."""
    result = {'version': 1, 'identity': identity, 'source': 'IPQS', 'ok': False,
              'checked_at': datetime.now(timezone.utc).isoformat()}
    if not _CHECK_LOCK.acquire(blocking=False):
        result['error'] = 'Eine IPQS-Prüfung läuft bereits. Bitte deren Ergebnis abwarten.'
        return result
    try:
        _result_path(cfg, identity)  # Validate the identity before any network call.
        key = _load_key(cfg)
        proxy, _, _ = profile_proxy._resolve(cfg, identity)
        ip = _exit_ip(proxy)
        score, flags = _lookup(proxy, key, ip)
        # The score belongs to this explicit IP, not to the proxy gateway name.
        stable = _exit_ip(proxy) == ip
        result.update(ok=True, ip=ip, fraud_score=score, flags=flags, ip_stable=stable)
    except QualityError as error:
        result['error'] = str(error)
    except tunnel.TlsBroken:
        result['error'] = 'Die verschlüsselte Verbindung konnte nicht sicher bestätigt werden. Nicht bewertet.'
    except (OSError, ValueError, RuntimeError):
        result['error'] = 'Proxy oder Bewertungsdienst nicht sicher erreichbar. Nicht bewertet; keine Ersatzverbindung verwendet.'
    finally:
        _CHECK_LOCK.release()
    from ..accountvault import _write
    try:
        _write(_result_path(cfg, identity), result)
    except (OSError, ValueError):
        result['save_error'] = True
    return result


def load(cfg, identity=None):
    try:
        path = _result_path(cfg, identity)
        if path.stat().st_size > 16384:
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1 or data.get('identity') != identity:
            return None
        if type(data.get('ok')) is not bool or datetime.fromisoformat(data['checked_at']).tzinfo is None:
            return None
        if data['ok']:
            data['ip'] = profile_proxy._ip(data['ip'])
            score = data['fraud_score']
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
                return None
            if type(data.get('ip_stable')) is not bool or not isinstance(data.get('flags'), dict):
                return None
            for name in ('proxy', 'vpn', 'tor', 'recent_abuse', 'bot_status'):
                flag = data['flags'].get(name)
                if flag is not None and type(flag) is not bool:
                    return None
        else:
            data['error'] = profile_proxy._text(data['error'])
        return data
    except (OSError, ValueError, TypeError, KeyError):
        return None


def summary(result):
    if not result:
        return 'Noch keine IPQS-Prüfung gespeichert. Eine Prüfung verbraucht in der Regel einen IPQS-Credit.'
    stamp = datetime.fromisoformat(result['checked_at']).astimezone().strftime('%d.%m.%Y %H:%M:%S')
    text = 'Letzte Prüfung: ' + stamp + '\n'
    if not result['ok']:
        return text + 'Nicht bewertet: ' + result['error']
    score = result['fraud_score']
    level = 'hohes Risiko laut IPQS' if score >= 90 else 'auffällig laut IPQS' if score >= 75 else 'unter der IPQS-Auffälligkeitsschwelle'
    text += f"Geprüfte IP: {result['ip']}\nFraud Score: {score:g}/100 · {level}\n"
    for name, label in (('proxy', 'Proxy erkannt'), ('vpn', 'VPN erkannt'), ('tor', 'Tor erkannt'),
                        ('recent_abuse', 'Kürzlich gemeldeter Missbrauch'), ('bot_status', 'Bot-Aktivität gemeldet')):
        flag = result['flags'].get(name)
        text += label + ': ' + ('nicht angegeben' if flag is None else 'ja' if flag else 'nein') + '\n'
    if not result['ip_stable']:
        text += '\nAchtung: Die Ausgangs-IP hat sich während der Prüfung geändert. Der Score gilt nur für die oben genannte IP.\n'
    text += '\nMomentaufnahme; keine Sicherheitsgarantie oder Zusage einer erfolgreichen Registrierung.\nAbfrage mit strictness=1. Kein automatischer Proxy-Wechsel.'
    if result.get('save_error'):
        text += '\nDas Ergebnis konnte nicht gespeichert werden.'
    return text
