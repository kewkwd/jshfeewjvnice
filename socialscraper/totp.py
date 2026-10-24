import base64, hashlib, hmac, struct, time
import math
from urllib.parse import parse_qs, urlsplit
_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'

class TotpError(ValueError):
    pass

def otpauth_secret(url):
    werte = parse_qs(urlsplit(url).query).get('secret') or []
    if not werte:
        raise TotpError('In der otpauth-Adresse fehlt das Feld „secret“.')
    return werte[0]

def normalize_secret(secret):
    if not isinstance(secret, str):
        raise TotpError('Das Zwei-Faktor-Geheimnis muss Text sein.')
    text = secret.strip()
    if text.lower().startswith('otpauth://'):
        text = otpauth_secret(text)
    text = text.replace(' ', '').replace('-', '').replace('=', '').upper()
    if not text or any(z not in _ALPHABET for z in text):
        raise TotpError('Das Zwei-Faktor-Geheimnis ist kein gültiger Base32-Wert.')
    return text

def _decode(secret):
    text = normalize_secret(secret)
    polster = '=' * ((8 - len(text) % 8) % 8)
    try:
        return base64.b32decode(text + polster, casefold=True)
    except Exception:
        raise TotpError('Das Zwei-Faktor-Geheimnis lässt sich nicht lesen.') from None

def code(secret, *, at=None, digits=6, period=30, algo='sha1'):
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise TotpError('Der Zeitraum muss eine positive ganze Zahl sein.')
    at = time.time() if at is None else at
    if not isinstance(at, (int, float)) or not math.isfinite(at) or at < 0:
        raise TotpError('Der Zeitpunkt muss eine nichtnegative endliche Zahl sein.')
    if digits not in (6, 8):
        raise TotpError('Nur 6 oder 8 Stellen sind üblich.')
    if algo not in ('sha1', 'sha256', 'sha512'):
        raise TotpError('Unbekanntes Verfahren.')
    digest = hmac.new(_decode(secret), struct.pack('>Q', int((time.time() if at is None else at) // period)),
                      getattr(hashlib, algo)).digest()
    versatz = digest[-1] & 0x0F
    zahl = struct.unpack('>I', digest[versatz:versatz + 4])[0] & 0x7FFFFFFF
    return str(zahl % (10 ** digits)).zfill(digits)

def seconds_left(*, at=None, period=30):
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise TotpError('Der Zeitraum muss eine positive ganze Zahl sein.')
    at = time.time() if at is None else at
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or at < 0:
        raise TotpError('Der Zeitpunkt muss eine nichtnegative endliche Zahl sein.')
    return period - int(at % period)

def code_with_freshness(secret, **kwargs):
    at = kwargs.get('at')
    at = time.time() if at is None else at
    rest = seconds_left(at=at, period=kwargs.get('period', 30))
    if rest < 4:
        return code(secret, **{**kwargs, 'at': at + rest}), rest
    return code(secret, **{**kwargs, 'at': at}), rest

def verify(secret, given, *, at=None, digits=6, period=30, fenster=1, algo='sha1'):
    jetzt = time.time() if at is None else at
    if isinstance(fenster, bool) or not isinstance(fenster, int) or fenster < 0:
        raise TotpError('Das Zeitfenster muss eine nichtnegative ganze Zahl sein.')
    expected = code(secret, at=jetzt, digits=digits, period=period, algo=algo)
    supplied = str(given).strip()
    if len(supplied) != len(expected) or any(c not in '0123456789' for c in supplied):
        return False
    return any(hmac.compare_digest(code(secret, at=jetzt + i * period, digits=digits, period=period, algo=algo), supplied)
               for i in range(-fenster, fenster + 1) if jetzt + i * period >= 0)
