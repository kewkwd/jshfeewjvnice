"""Deutsche, verständliche Fehlermeldungen für Netz- und Browserfehler.

    text = explain(error)      # eine Meldung mit nächstem Schritt, nie abgeschnitten
    wrap(error)                # NetworkError mit der deutschen Meldung; Rohtext steht bei Logstufe DEBUG im Protokoll

Eigene, schon deutsche Meldungen (z. B. „Mit kostenlosen Proxys/Tor werden keine Konten benutzt ...“) bleiben unverändert.
"""
import logging
import re

log = logging.getLogger(__name__)
_SECRET = re.compile(r'''(?i)((?:token|password|passwd|secret|sessionid\w*|authorization|api[_-]?key|cookie)["']?\s*[=:]\s*)[^\s,;&"']+''')
_URLCRED = re.compile(r'(?<=://)[^/\s:@]+:[^/\s@]+@')
_LOCAL = re.compile(r'(?:127\.\d+\.\d+\.\d+|localhost|\[?::1\]?)', re.I)
_ENGLISH_HINT = re.compile(r'(curl|yt-dlp|yt_dlp|playwright|patchright|net::|ERR_[A-Z_]+|traceback|'
                           r'urllib|http error|connection|timed out|timeout|certificate|ssl)', re.I)


class NetworkError(RuntimeError):
    """Fehler mit deutscher Meldung. `retryable=False`, wenn Wiederholen sinnlos ist."""

    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class FinalMessage(str):
    """Schon fertige deutsche Meldung des Programms: wird nie erneut anhand von Stichwörtern (CAPTCHA, 429 ...) übersetzt."""

class FinalError(NetworkError):
    """Raise a completed message without converting it into a string exception."""
    def __init__(self, message):
        super().__init__(message, retryable=False)


def _raw(error):
    text = str(error).strip()
    return text if text else type(error).__name__


def _has(text, *needles):
    return any(n in text for n in needles)


def _classify(error):
    """(Schlüssel, hoffnungslos) – Schlüssel None, wenn nichts passt."""
    raw = _raw(error)
    low = raw.lower().replace('’', "'")
    refused = _has(low, 'connection refused', 'curl: (7)', 'err_connection_refused', 'winerror 10061',
                   'err_proxy_connection_failed', 'actively refused', 'verbindung verweigert')
    proxied = _has(low, 'proxy')
    if refused or (proxied and _has(low, 'unable to connect', 'tunnel failed', 'could not connect')):
        local = bool(_LOCAL.search(raw))
        return ('proxy' if proxied or local else 'refused'), (local and refused)
    if _has(low, 'sign in to confirm', 'not a bot', 'confirm you are not a bot'):
        return 'bot', False
    if _has(low, 'account suspended', 'account has been suspended', 'account has been locked', 'account is locked',
            'konto gesperrt', 'konto wurde gesperrt', 'konto ist gesperrt'):
        return 'konto', False
    if _has(low, 'blocked by network security', 'blocked by mistake'):
        return 'sperre', False
    if _has(low, 'certificate', 'curl: (60)', 'curl: (35)', 'err_cert', 'ssl: ', 'sslerror'):
        return 'zertifikat', False
    if _has(low, 'getaddrinfo', 'name or service not known', 'nodename nor servname', 'curl: (6)',
            'err_name_not_resolved', 'temporary failure in name resolution', 'could not resolve host'):
        return 'dns', False
    if _has(low, 'timed out', 'timeout', 'curl: (28)', 'err_timed_out', 'time out'):
        return 'zeit', False
    if _has(low, 'too many requests', 'rate limit', 'rate-limited', 'try again later') or re.search(r'\bhttp(?: error)?[ :]*429\b', low):
        return '429', False
    if _has(low, 'captcha', 'verify you are human', 'prove your humanity', 'menschentest', 'sicherheitsabfrage'):
        return 'captcha', False
    if _has(low, 'login required', 'log in to continue', 'log in to view', 'sign in to continue', 'redirected to login',
            'anmeldeseite', 'zur anmeldung weitergeleitet'):
        return 'anmeldung', False
    if _has(low, 'private video', 'video unavailable', 'has been removed', 'video is not available',
            'this video is unavailable', 'members-only', 'terminated',
            'not available in your country', 'blocked it in your country', 'geo-restricted', 'geo restricted',
            'nicht verfügbar in deinem land', 'in deinem land nicht verfügbar', 'ist privat', 'wurde gelöscht', 'gelöscht'):
        return 'privat', False
    if _has(low, 'forbidden') or re.search(r'\bhttp(?: error)?[ :]*(?:403|401)\b', low):
        return '403', False
    if _has(low, 'err_internet_disconnected', 'network is unreachable', 'curl: (56)', 'curl: (55)',
            'connection reset', 'connection aborted', 'remote end closed', 'err_connection', 'failed to connect',
            'unable to download', 'transporterror', 'max retries', 'net::'):
        return 'verbindung', False
    return None, False


MESSAGES = {
    'proxy': 'Der Proxy ist nicht erreichbar (Verbindung verweigert oder abgebrochen). Bitte Proxy-Einstellung prüfen, '
             'den Proxy starten oder den Proxy-Modus ändern.',
    'refused': 'Die Verbindung wurde vom Zielserver abgelehnt. Später noch einmal versuchen.',
    'bot': 'YouTube verlangt eine Bestätigung („Bist du ein Mensch?“). '
           'Bitte die Anmeldung prüfen und die Abfrage im sichtbaren Browserfenster selbst bestätigen.',
    'zertifikat': 'Die verschlüsselte Verbindung konnte nicht verifiziert werden (Zertifikatsfehler). '
                  'Die Meldung belegt keinen Eingriff durch einen Proxy. Systemzeit und '
                  'Zertifikatskonfiguration des Servers beziehungsweise Proxys prüfen.',
    'dns': 'Der Servername konnte nicht aufgelöst werden (DNS). Internetverbindung und Proxy prüfen.',
    'zeit': 'Zeitüberschreitung: der Server hat nicht rechtzeitig geantwortet. Internetverbindung prüfen und später '
            'noch einmal versuchen.',
    '429': 'Zu viele Anfragen (HTTP 429): die Plattform bremst uns aus. Eine Weile warten und mit größerem Abstand '
           'zwischen den Abrufen wiederholen.',
    '403': 'Zugriff verweigert (HTTP 403/401): die Plattform lässt diesen Abruf nicht zu. '
           'Der konkrete Grund ist aus dem Statuscode allein nicht ersichtlich. '
           'Anmeldung, Berechtigung und Zieladresse prüfen.',
    'konto': 'Die Plattform hat das Konto gesperrt oder eingeschränkt. Am PC im Browser prüfen, ob sich das Konto noch '
             'anmelden lässt, und ggf. ein anderes Konto benutzen.',
    'anmeldung': 'Die Plattform verlangt eine Anmeldung (Anmeldeseite statt Video). Unter „Konten“ neu anmelden.',
    'captcha': 'Die Plattform zeigt eine Sicherheitsabfrage (CAPTCHA). '
               'Bitte die Abfrage im sichtbaren Browserfenster selbst bestätigen.',
    'privat': 'Das Video ist privat, gelöscht oder in diesem Land nicht verfügbar.',
    'sperre': 'Die Plattform hat den Abruf durch eine Sicherheitsprüfung gesperrt („blocked by network security“). '
              'Die Meldung nennt keine eindeutige Ursache; eine IP-Sperre ist damit nicht nachgewiesen. '
              'Anmeldung und Sitzung prüfen und später erneut versuchen.',
    'verbindung': 'Keine Verbindung zum Server. Internetverbindung (und Proxy) prüfen und noch einmal versuchen.',
}


def explain(error):
    """Deutsche Meldung mit nächstem Schritt. Rohtext nur bei Logstufe DEBUG."""
    if isinstance(error, (NetworkError, FinalMessage)):
        return ' '.join(str(error).split())      # schon übersetzt
    key, _ = _classify(error)
    raw = _raw(error)
    if key is None:
        if not _ENGLISH_HINT.search(raw):
            return ' '.join(raw.split())          # schon eigener (deutscher) Text: unverändert, nie gekürzt
        message = 'Der Abruf ist fehlgeschlagen (' + type(error).__name__ + '). Später noch einmal versuchen.'
    else:
        message = MESSAGES[key]
    log.debug('Rohmeldung: %s', _SECRET.sub(r'\1[entfernt]', _URLCRED.sub('', raw)))
    return message


def precheck_local_proxy(proxy, timeout=2.0):
    """Lokaler Proxy (127.x/localhost) nicht erreichbar? Dann sofort mit klarer Meldung enden (kein Warten, keine Wiederholung)."""
    import socket
    host = getattr(proxy, 'host', None)
    if not host or not _LOCAL.fullmatch(str(host)):
        return
    try:
        socket.create_connection((host, proxy.port), timeout=timeout).close()
    except OSError:
        raise NetworkError(f'Der Proxy {host}:{proxy.port} auf diesem Rechner ist nicht erreichbar (Verbindung verweigert). '
                           'Bitte den Proxy starten, die Proxy-Einstellung prüfen oder den Proxy-Modus ändern.',
                           retryable=False) from None


def is_hopeless(error):
    """Wiederholen ist sinnlos (z. B. lokaler Proxy lehnt die Verbindung ab)."""
    if getattr(error, 'retryable', True) is False:
        return True
    return _classify(error)[1]


def wrap(error):
    """NetworkError mit deutscher Meldung (und passender `retryable`-Angabe) zu einem Rohfehler."""
    if isinstance(error, NetworkError):
        return error
    return NetworkError(explain(error), retryable=not is_hopeless(error))
