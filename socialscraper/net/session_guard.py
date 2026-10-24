"""Conservative browser session checks with a short, scoped probe cache."""
import logging
import math
import os
import time
from .accountproxy import AccountProxyError
from .cookies import CookieJar
from .notify import notify

log = logging.getLogger(__name__)


class State:
    OK = "ok"
    EXPIRED = "expired"
    MISSING = "missing"
    ERROR = "error"


class SessionExpired(RuntimeError):
    retryable = False


HOME_URL = {"tiktok": "https://www.tiktok.com/", "instagram": "https://www.instagram.com/",
            "reddit": "https://www.reddit.com/", "x": "https://x.com/home"}
# A successful navigation alone does not prove that the account is logged in.
# Prefer language-independent markers (links, data attributes); the labels cover
# the English and German interface used by the identities in this project.
PROBE_JS = {
    "tiktok": """() => {
        if (document.querySelector('[data-e2e="top-login-button"], input[type="password"]')) return 'expired';
        if (document.querySelector('[data-e2e="profile-icon"], [data-e2e="profile-menu"], [data-e2e="nav-profile"], '
            + '[data-e2e="inbox-icon"], [data-e2e="upload-icon"], a[href^="/messages"]')) return 'ok';
        return 'error';
    }""",
    "instagram": """() => {
        if (document.querySelector('input[name="username"], input[name="password"]')) return 'expired';
        if (document.querySelector('a[href^="/direct/inbox"], a[href^="/accounts/edit"], a[href^="/accounts/activity"], '
            + 'svg[aria-label="New post"], svg[aria-label="Neuer Beitrag"], '
            + 'svg[aria-label="Notifications"], svg[aria-label="Benachrichtigungen"]')) return 'ok';
        return 'error';
    }""",
    # Reddit: /api/me.json liefert für einen angemeldeten Browser den Benutzer, sonst ein leeres Objekt. Das ist
    # unabhängig von Sprache und Aussehen der Seite; eine Sperrseite (HTML) bleibt "error".
    "reddit": """async () => {
        try {
            const r = await fetch('/api/me.json', {credentials: 'include', headers: {'Accept': 'application/json'}});
            if (r.status !== 200) return 'error';
            const j = JSON.parse(await r.text());
            if (j && j.data && j.data.name) return 'ok';
            if (j && typeof j === 'object' && !j.error) return 'expired';
            return 'error';
        } catch (_) { return 'error'; }
    }""",
    # X: data-testid-Marker statt Beschriftungen; auf /home führt "ausgeloggt" zur Anmeldeseite.
    "x": """() => {
        const p = location.pathname;
        if (/^\\/(i\\/flow\\/(login|signup)|login|logout)/.test(p)) return 'expired';
        if (document.querySelector('[data-testid="loginButton"], [data-testid="signupButton"], a[href="/login"], '
            + 'a[href="/i/flow/login"], input[autocomplete="username"], input[name="password"]')) return 'expired';
        if (document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="AppTabBar_Profile_Link"], '
            + '[data-testid="AppTabBar_Home_Link"], [data-testid="SideNav_NewTweet_Button"], a[href="/compose/post"]')) return 'ok';
        return 'error';
    }""",
}
HOME_URL.update(google="https://myaccount.google.com/", youtube="https://www.youtube.com/", trustpilot="https://www.trustpilot.com/users/connect")
PROBE_JS.update({
    'google': """() => { if (location.hostname === 'accounts.google.com' || document.querySelector('input[type=password]')) return 'expired'; return document.querySelector('a[href*=SignOut], a[href*=Logout], a[href*=myaccount], img[alt*=Account]') ? 'ok' : 'error'; }""",
    'youtube': """() => { return document.querySelector('button#avatar-btn, ytd-topbar-menu-button-renderer img#img') ? 'ok' : document.querySelector('a[href*=ServiceLogin]') ? 'expired' : 'error'; }""",
    'trustpilot': """() => { return document.querySelector('a[href*=logout], a[href*=signout], a[href*=my-reviews]') ? 'ok' : document.querySelector('input[type=password], input[type=email]') ? 'expired' : 'error'; }""",
})
_cache = {}
TTL = 600
PROBE_ATTEMPTS = 10
PROBE_INTERVAL_MS = 1000


def read_state(page, platform, attempts=PROBE_ATTEMPTS, interval_ms=PROBE_INTERVAL_MS):
    """Poll the page until it clearly shows a logged-in or logged-out state.

    Pages render their navigation late and may still be redirecting; a script
    evaluated during navigation fails, which only means "not decided yet".
    """
    state = State.ERROR
    for attempt in range(attempts):
        try:
            state = page.evaluate(PROBE_JS[platform])
        except Exception:
            state = State.ERROR
        if state in (State.OK, State.EXPIRED):
            return state
        if attempt + 1 < attempts:
            page.wait_for_timeout(interval_ms)
    return State.ERROR


def invalidate(identity, platform):
    for key in list(_cache):
        if key[:2] == (identity, platform):
            _cache.pop(key, None)


def _confirm(backend, handle, identity, platform):
    """Die Sitzung wurde im Browser bestätigt: Cookie-Datei auffrischen, damit ihr Alter von vorn zählt.

    Bevorzugt werden die frischen Cookies aus der laufenden Sitzung gespeichert; geht das nicht (oder fehlt das
    Anmelde-Cookie darin), wird nur der Zeitstempel der vorhandenen Datei erneuert („zuletzt bestätigt“).
    """
    try:
        fresh = backend.read_cookies(handle)
        if CookieJar.authenticated(fresh, platform):
            CookieJar.save(identity, platform, fresh)
            return
    except Exception:
        log.warning("Frische Cookies konnten nicht gespeichert werden; Bestätigungszeit wird nur vermerkt")
    try:
        os.utime(CookieJar.path(identity, platform))
    except OSError:
        log.warning("Bestätigungszeit der Anmeldung konnte nicht vermerkt werden")


def _probe(backend, im, identity, platform, country, mobile, refresh=False):
    if platform not in HOME_URL:
        raise ValueError("Session-Plattform wird nicht unterstützt")
    if not CookieJar.is_authenticated(identity, platform):
        return State.MISSING
    try:
        ident = im.get(identity, country, mobile)
        with backend.session(ident, im.proxy_for(ident), headless=True, platform=platform) as handle:
            page = backend.page(handle)
            page.goto(HOME_URL[platform], wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            state = read_state(page, platform)
            if refresh and state == State.OK:
                _confirm(backend, handle, identity, platform)
    except AccountProxyError:
        raise   # fester Proxy des Kontos nicht erreichbar: nicht als "Prüfung fehlgeschlagen" verstecken
    except Exception as error:
        # Der Grund gehört ins Protokoll (Land/Gerät der Identität passt nicht, kein Proxy im gewählten Modus): eigene
        # Fehler haben deutsche Meldungen ohne Geheimnisse. Von Browserfehlern steht nur die Art da – ihr Text kann
        # Eingaben oder Sitzungswerte enthalten.
        from .errors import NetworkError
        from .proxymode import ProxyUnavailable
        own = isinstance(error, (ValueError, NetworkError, ProxyUnavailable))
        reason = ' '.join(str(error).split()) if own else type(error).__name__
        log.warning("Session-Prüfung fehlgeschlagen; Anmeldung konnte nicht bestätigt werden (%s)", reason)
        return State.ERROR
    return state


def _notify_problem(state, identity, platform):
    if state == State.MISSING:
        notify("session_missing", f"Keine gültigen Cookies für {identity}/{platform}; Login ausführen",
               identity=identity, platform=platform)
    elif state == State.EXPIRED:
        notify("session_expired", f"Session für {identity}/{platform} abgelaufen; erneute Anmeldung nötig",
               identity=identity, platform=platform)
    elif state == State.ERROR:
        notify("session_error", f"Anmeldung für {identity}/{platform} konnte nicht geprüft werden",
               identity=identity, platform=platform)


def ensure(backend, im, identity, platform="tiktok", country="us", mobile=False,
           force=False, max_age=None):
    """Check authentication.

    max_age (Stunden; None schaltet es aus) ist kein hartes Ablaufdatum: Die Cookie-Datei wird nur bei Anmeldung,
    Import oder Bestätigung neu geschrieben, ihr Alter sagt also nichts darüber, ob die Sitzung noch gilt. Ist die
    Datei älter, wird die Sitzung deshalb im Browser neu geprüft (ohne Zwischenspeicher). Bestätigt die Prüfung die
    Anmeldung, werden die Cookies aufgefrischt und das Alter beginnt von vorn (OK); zeigt der Browser „ausgeloggt“,
    ist das Ergebnis EXPIRED, bei einem Prüffehler ERROR.
    """
    from . import proxymode
    if proxymode.is_anonymous_im(im):
        proxymode.refuse_accounts()
    if platform not in HOME_URL:
        raise ValueError("Session-Plattform wird nicht unterstützt")
    limit = None
    if max_age is not None:
        if isinstance(max_age, bool):
            raise ValueError("session_max_age muss eine positive Stundenangabe sein")
        try:
            limit = float(max_age) * 3600
        except (TypeError, ValueError):
            raise ValueError("session_max_age muss eine positive Stundenangabe sein") from None
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("session_max_age muss eine positive Stundenangabe sein")
    path = CookieJar.path(identity, platform)
    now = time.time()
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        stat, stamp = None, None
    except OSError:
        _notify_problem(State.ERROR, identity, platform)
        return State.ERROR
    aged = stat is not None and limit is not None and now - stat.st_mtime >= limit
    key = (identity, platform, country, bool(mobile), id(backend), id(im), limit)
    if not CookieJar.is_authenticated(identity, platform):
        invalidate(identity, platform)
        _notify_problem(State.MISSING, identity, platform)
        return State.MISSING
    cached = _cache.get(key)
    if not force and (not aged or cached and cached[0] != State.OK) and cached and stamp == cached[2] and 0 <= now - cached[1] < TTL:
        return cached[0]
    state = _probe(backend, im, identity, platform, country, mobile, refresh=aged)
    if state != State.ERROR:
        if aged and state == State.OK:   # Datei wurde aufgefrischt: Zeitstempel für den Zwischenspeicher neu lesen
            try:
                fresh = path.stat()
                stamp = (fresh.st_mtime_ns, fresh.st_size)
            except OSError:
                stamp = None
        _cache[key] = (state, now, stamp)
    else:
        _cache.pop(key, None)
    _notify_problem(state, identity, platform)
    return state

