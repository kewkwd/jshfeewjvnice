"""Manual browser login and optional credential fill for the user's own account."""
import logging
import math
import time
from .net.cookies import CookieJar
from .net.credentials import get as get_creds, MissingCreds
from .net.notify import notify
from .net.session_guard import State, invalidate, read_state

log = logging.getLogger(__name__)

class LoginError(RuntimeError):
    pass
LOGIN_URL = {"tiktok": "https://www.tiktok.com/login", "instagram": "https://www.instagram.com/accounts/login/",
             "reddit": "https://www.reddit.com/login/", "x": "https://x.com/i/flow/login"}
EMAIL_URL = {"tiktok": "https://www.tiktok.com/login/phone-or-email/email", "instagram": LOGIN_URL["instagram"],
             "reddit": LOGIN_URL["reddit"], "x": LOGIN_URL["x"]}
# Bei Reddit und X meldest du dich im sichtbaren Fenster selbst an: Die Anmeldeseiten haben mehrere Schritte
# (X) oder verstecken die Felder (Reddit), und Passwörter trägt das Programm dort nicht ein.
MANUAL_ONLY = ("reddit", "x", "google", "trustpilot", "youtube")
# YouTube: Nach der Google-Anmeldung muss das Fenster auf youtube.com landen, sonst findet die Prüfung (PROBE_JS
# sucht YouTube-Elemente) nie „angemeldet“ und die Anmeldung läuft ins Zeitlimit.
LOGIN_URL.update(google="https://accounts.google.com/",
                 youtube="https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/",
                 trustpilot="https://www.trustpilot.com/users/connect")
EMAIL_URL.update({p: LOGIN_URL[p] for p in ("google", "youtube", "trustpilot")})
SEL = {
    "tiktok": {"user": ['input[name="username"]', 'input[type="text"]', '#login-username'],
               "pass": ['input[type="password"]', '#login-password'],
               "submit": ['button[type="submit"]', 'button[data-e2e="login-button"]']},
    "instagram": {"user": ['input[name="username"]'], "pass": ['input[name="password"]'],
                  "submit": ['button[type="submit"]']},
    "reddit": {"user": ['input[name="username"]', '#login-username'], "pass": ['input[name="password"]', '#login-password'],
               "submit": ['button[type="submit"]']},
    "x": {"user": ['input[autocomplete="username"]', 'input[name="text"]'], "pass": ['input[name="password"]'],
          "submit": ['button[data-testid="LoginForm_Login_Button"]']},
}
# Only declining answers: optional cookies are refused and prompts postponed.
# Accepting buttons ("allow all") are never clicked on the user's behalf.
_DECLINE = ["decline optional cookies", "optionale cookies ablehnen",
            "only allow essential cookies", "nur erforderliche cookies erlauben",
            "not now", "jetzt nicht", "nicht jetzt"]
# Reddit und X fragen nach Cookies mit „Nicht notwendige ablehnen“; angeklickt wird nur Ablehnen, nie Zustimmen.
_DECLINE_MORE = ["reject non-essential", "reject all non-essential", "nicht notwendige ablehnen",
                 "refuse non-essential cookies", "nicht notwendige cookies ablehnen"]
DISMISS = {"tiktok": _DECLINE, "instagram": _DECLINE,
           "reddit": _DECLINE + _DECLINE_MORE, "x": _DECLINE + _DECLINE_MORE}
SUBMIT_TIMEOUT_MS = 5000
_FILL_JS = """([sels, val]) => {
  for (const s of sels) {
    const el = document.querySelector(s);
    if (!el) continue;
    el.focus();
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return s;
  }
  return null;
}"""
_CLICK_TEXT_JS = """(texts) => {
  const els = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
  for (const t of texts) {
    const el = els.find(x => (x.innerText || '').trim().toLowerCase() === t);
    if (el) { el.click(); return t; }
  }
  return null;
}"""


def _authed(cookies, platform):
    return CookieJar.authenticated(cookies, platform)


def _dismiss(page, platform):
    for _ in range(3):
        try:
            clicked = page.evaluate(_CLICK_TEXT_JS, DISMISS.get(platform, _DECLINE + _DECLINE_MORE))
        except Exception:
            # The page is navigating (e.g. after submit); try again next round.
            return
        if not clicked:
            break
        page.wait_for_timeout(1200)


def _fill(page, selectors, value):
    return page.evaluate(_FILL_JS, [selectors, value])


def _wait_for_auth(page, backend, handle, platform, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            cookies = backend.read_cookies(handle)
        except Exception:
            cookies = []
        if _authed(cookies, platform) and read_state(page, platform, attempts=1) == State.OK:
            return CookieJar.for_platform(cookies, platform)
        _dismiss(page, platform)
        page.wait_for_timeout(min(2000, max(1, int((deadline - time.monotonic()) * 1000))))
    return None


def _finish(identity, platform, cookies):
    if not _authed(cookies, platform):
        raise LoginError("Anmeldung wurde nicht bestätigt")
    CookieJar.save(identity, platform, cookies)
    invalidate(identity, platform)
    notify("login_ok", f"Login erfolgreich für {identity}/{platform}",
           identity=identity, platform=platform, count=len(cookies))
    log.info("Eingeloggt; %s Cookies gespeichert", len(cookies))


def _qr_flow(backend, im, identity, platform, country, mobile, timeout):
    log.info("Login-Fenster öffnet; Anmeldung dort abschließen (max. %ss)", timeout)
    ident = im.get(identity, country, mobile)
    with backend.session(ident, im.proxy_for(ident), headless=False, platform=platform) as handle:
        page = backend.page(handle)
        page.goto(LOGIN_URL[platform], wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        cookies = _wait_for_auth(page, backend, handle, platform, timeout)
        if not cookies:
            raise TimeoutError(f"Login nicht abgeschlossen ({timeout}s)")
    # A failed browser shutdown must not emit login_ok or replace the saved cookie file.
    _finish(identity, platform, cookies)
    return cookies


def _password_flow(backend, im, identity, platform, country, mobile, timeout, credentials_path):
    creds = get_creds(platform, identity, credentials_path)
    log.info("Login-Fenster öffnet; Bestätigungscode oder Captcha dort manuell lösen (max. %ss)", timeout)
    ident = im.get(identity, country, mobile)
    with backend.session(ident, im.proxy_for(ident), headless=False, platform=platform) as handle:
        page = backend.page(handle)
        page.goto(EMAIL_URL[platform], wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        _dismiss(page, platform)
        selectors = SEL[platform]
        if not _fill(page, selectors["user"], creds.user):
            raise LoginError("Benutzernamen-Feld nicht gefunden")
        if not _fill(page, selectors["pass"], creds.password):
            raise LoginError("Passwort-Feld nicht gefunden")
        for selector in selectors["submit"]:
            try:
                page.click(selector, timeout=SUBMIT_TIMEOUT_MS)
                break
            except Exception:
                continue
        else:
            raise LoginError("Anmelde-Schaltfläche nicht gefunden")
        cookies = _wait_for_auth(page, backend, handle, platform, timeout)
        if not cookies:
            raise TimeoutError(f"Passwort-Login nicht abgeschlossen ({timeout}s)")
    _finish(identity, platform, cookies)
    return cookies


def login(backend, im, identity, platform="tiktok", mode="auto", country="us", mobile=False,
          timeout=300, cfg=None):
    CookieJar.path(identity, platform)
    from .net import proxymode
    if proxymode.is_anonymous_im(im) or proxymode.is_anonymous(cfg):
        proxymode.refuse_accounts()
    if mode not in ("auto", "qr", "manual", "password"):
        raise ValueError("Login-Modus wird nicht unterstützt")
    if platform in MANUAL_ONLY:
        if mode == "password":
            raise LoginError("Bei Reddit und X meldest du dich im Browserfenster selbst an; "
                             "die Passwort-Eingabe durch das Programm gibt es hier nicht.")
        mode = "qr"
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Login-Zeitlimit muss positiv sein")
    credentials_path = getattr(cfg, "credentials_file", "data/credentials.json")
    if mode == "auto":
        try:
            get_creds(platform, identity, credentials_path)
            mode = "password"
        except MissingCreds:
            mode = "qr"
    from .net.accountproxy import AccountProxyError
    from .net.errors import NetworkError
    try:
        if mode == "password":
            return _password_flow(backend, im, identity, platform, country, mobile, timeout, credentials_path)
        return _qr_flow(backend, im, identity, platform, country, mobile, timeout)
    except (MissingCreds, LoginError):
        raise
    except (AccountProxyError, proxymode.ProxyUnavailable, proxymode.AccountsForbidden, NetworkError, ValueError):
        # Eigene, schon deutsche Meldungen ohne Geheimnisse (fester Proxy des Kontos nicht erreichbar, kein Proxy im
        # gewählten Modus, Land/Gerät der Identität passt nicht): Den Grund bekommt der Nutzer, nicht die Floskel.
        raise
    except TimeoutError:
        raise TimeoutError("Anmeldung nicht innerhalb des Zeitlimits abgeschlossen") from None
    except Exception as error:
        # Browser errors can include autofill arguments or session tokens: nur die Fehlerart ins Protokoll.
        log.warning("Browser-Anmeldung fehlgeschlagen (%s); Anmeldung wurde nicht gespeichert", type(error).__name__)
        raise LoginError("Browser-Anmeldung fehlgeschlagen; Anmeldung wurde nicht gespeichert") from None


def check(backend, im, identity, platform="tiktok", country="us", mobile=False, cfg=None):
    from .net.session_guard import ensure
    state = ensure(backend, im, identity, platform, country, mobile, force=True,
                   max_age=getattr(cfg, "session_max_age", None))
    if state == State.ERROR:
        raise LoginError('Die Anmeldung konnte nicht geprüft werden. Prüfe deine Internetverbindung und versuche es erneut.')
    return state == State.OK


authenticate = login
check_session = check


def refresh_login(*args, **kwargs):
    return login(*args, **kwargs)

