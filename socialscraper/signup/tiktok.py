"""TikTok: Registrierung im Emulator (App), Web-Anmeldung beim Wechsel."""
import time
from . import _dismiss                                          # FIX A6
from ..net.errors import FinalError
from ..net.form_guard import required
WEG, APP_TAGE = 'app', 3
REGISTER_URL = 'https://www.tiktok.com/signup/phone-or-email/email'
SEL = {'email': ['input[name="email"]', '#email', 'input[type="email"]'],
       'pass': ['input[type="password"]', '#password'],
       'monat': ['select[name="month"]', '#month'], 'tag': ['select[name="day"]', '#day'],
       'jahr': ['select[name="year"]', '#jahr'],
       'weiter': ['button[type="submit"]', 'button:has-text("Weiter")', 'button:has-text("Next")'],
       'otp': ['input[name="code"]', 'input[autocomplete="one-time-code"]']}
RAETSEL = ['#captcha-verify-image', '.captcha_verify_container', 'img[alt*="captcha" i]']
LOGIN_URL = 'https://www.tiktok.com/login/phone-or-email/email'
LOGIN_SEL = {'email': ['input[name="username"]', 'input[type="text"]', 'input[autocomplete="username"]'],
             'pass': ['input[type="password"]', 'input[autocomplete="current-password"]'],
             'senden': ['button[type="submit"]', 'button:has-text("Anmelden")', 'button:has-text("Log in")']}

def run(page, ctx):
    """Web-Registrierung (Rückfallweg)."""
    start = time.time()
    if not ctx.fill(SEL['email'], ctx.email):
        raise RuntimeError('TikTok: E-Mail-Feld nicht gefunden')
    password_filled = bool(ctx.fill(SEL['pass'], ctx.password))
    from ..profile_settings import birthdate
    born = birthdate(ctx.person)
    for auswahl, wert in ((SEL['jahr'], born.year), (SEL['monat'], born.month), (SEL['tag'], born.day)):
        required(_waehle(page, auswahl, wert, check=getattr(ctx, 'check_cancelled', None)), 'Die Datumsangabe')
    ctx.warte(400)
    ctx.dismiss()
    if not ctx.click(SEL['weiter']):
        raise RuntimeError('TikTok: „Weiter“ nicht gefunden')
    ctx.warte(2500)
    # Mobile signup starts with email only; password is on the next step.
    if not password_filled and ctx.fill(SEL['pass'], ctx.password):
        ctx.click(SEL['weiter'])
        ctx.warte(2500)
    captcha_notified = bool(_erstes(page, RAETSEL))
    if captcha_notified:
        ctx.melden('TikTok zeigt ein CAPTCHA – bitte selbst im Fenster lösen.')
    ctx.warte(2000)
    code = None
    submitted = False

    def pending_code(remaining):
        nonlocal captcha_notified, code, submitted
        deadline = time.monotonic() + remaining
        if _erstes(page, RAETSEL):
            if not captcha_notified:
                ctx.melden('TikTok zeigt ein CAPTCHA – bitte selbst im Fenster lösen.')
                captcha_notified = True
            return
        selector = _erstes(page, SEL['otp'], enabled=True)
        if submitted or not selector:
            return
        if code is None:
            code = ctx.code_holen(timeout=min(240, remaining), ab=start - 30)
        if ctx.stop is not None and ctx.stop():
            raise FinalError('Abgebrochen.')
        if time.monotonic() >= deadline:
            return
        # The page can change while mail is being retrieved. Check again before
        # entering anything; retain the code for a later editable field.
        if _erstes(page, RAETSEL):
            return
        selector = _erstes(page, SEL['otp'], enabled=True)
        if not selector or not ctx.fill([selector], code):
            return
        if ctx.stop is not None and ctx.stop():
            raise FinalError('Abgebrochen.')
        if time.monotonic() >= deadline:
            return
        submitted = True
        if ctx.click(SEL['weiter']):
            return True
        ctx.melden('Der E-Mail-Code ist eingetragen. Bitte im Fenster selbst auf Weiter tippen.')

    return ctx.warte_auf_cookies(still=False, tick=pending_code)

def registrieren_app(cfg, ctx, *, proxy=None, avd=None):
    import os
    from ..android import geraet, tiktok_app
    from ..android import avd as owned
    from ..android.avd_session import proxy_value
    from .. import signup_attempts
    ctx.check_cancelled()
    signup_attempts.ensure_available(cfg.db_path, ctx.identity, ctx.email)
    if (not isinstance(ctx.password, str) or not 1 <= len(ctx.password) <= 128
            or any(not 32 <= ord(char) <= 126 for char in ctx.password)):
        raise FinalError('Das gespeicherte Kennwort kann nicht vollständig an die Android-App übergeben werden. Bitte den Passwortvorrat prüfen; keine Registrierung gestartet.')
    proxy_value(proxy)
    expected = owned.name_for(ctx.identity)
    if avd is not None and avd != expected:
        raise FinalError('Das AVD gehört nicht zu dieser Kontoidentität.')
    avd = expected
    owned.prepare_profile(avd)
    ctx.check_cancelled()
    interactive_background = ctx.mode == 'assisted' and os.environ.get('SS_ANDROID_HANDOFF') == '1'
    with geraet.mit_geraet(avd, proxy=proxy, headless=ctx.mode != 'assisted' or interactive_background,
                          stop_requested=ctx.stop) as adb:
        adb.input_tempo = getattr(ctx, 'profile_tempo', None)
        geraet.vorbereiten(adb, proxy=proxy)
        ctx.notizen.append(f'Gerät: {adb.modell()}')
        if interactive_background:
            from ..android.handoff import ManualHandoff
            ctx.android_handoff = ManualHandoff(adb, ctx, cfg)
        try:
            signup_attempts.begin(cfg.db_path, ctx.identity, ctx.email)
            ctx.check_cancelled()
            ok = tiktok_app.registrieren(adb, ctx)
            if ok:
                # Preserve the observed handle before device cleanup or later
                # cancellation. This is not a claim that the password is valid.
                signup_attempts.profile_seen(cfg.db_path, ctx.identity, ctx.email, ctx.tiktok_username)
            return ok, avd
        finally:
            ctx.android_handoff = None

def web_anmelden(cfg, konto, *, melden=print, stop=None, timeout=300):
    """Log in with this account's credentials and require a new browser session.

    A rotated session after verified credential entry is stronger than stale
    cookies, but does not independently prove the provider's account handle.
    """
    def check_cancelled():
        from ..operation import check_cancelled as check_stop
        check_stop(stop)

    check_cancelled()
    from ..operation import positive_timeout
    positive_timeout(timeout)
    from .. import accountvault
    from ..net.accountproxy import Resolver
    from ..net.cookies import CookieJar
    from ..net.factory import build as build_backend
    from ..net.identity import IdentityManager
    from ..net.proxy import ProxyPool
    from ..net import proxymode
    daten = accountvault.get(cfg.db_path, konto['identity'])
    if (not isinstance(daten.get('email'), str) or not daten['email'].strip() or
            not isinstance(daten.get('passwort'), str) or not daten['passwort']):
        raise RuntimeError('Für den Web-Nachlauf fehlen E-Mail oder Passwort im Tresor.')
    email = daten['email'].strip()
    pool = proxymode.make_pool(cfg) if hasattr(proxymode, 'make_pool') else ProxyPool([])
    im = IdentityManager(pool, cfg.profiles_dir)
    if not proxymode.is_anonymous(cfg):
        im.account_proxies = Resolver(cfg.db_path)                  # FIX A4
    person = im.get(konto['identity'], konto.get('country') or 'de', bool(im.stored(konto['identity'])[1]))
    check_cancelled()
    proxy = im.proxy_for(person)
    check_cancelled()
    if proxy is None:
        raise FinalError('Die Browserübergabe benötigt den Proxy dieses Kontos.')
    if getattr(cfg, 'backend', 'local') == 'local' and getattr(cfg, 'engine', None) == 'firefox':
        person = im.browser_context(person, engine='firefox', create=True)
    check_cancelled()
    backend = build_backend(cfg, im)
    try:
        check_cancelled()
        with backend.session(person, proxy, headless=cfg.headless, platform='tiktok') as handle:
            check_cancelled()
            page = backend.page(handle)
            page.goto(LOGIN_URL, wait_until='domcontentloaded')
            check_cancelled()
            page.wait_for_timeout(3000)
            check_cancelled()
            _dismiss(page, check=check_cancelled)
            entered = []
            for schluessel, wert in (('email', email), ('pass', daten['passwort'])):
                selected = None
                for sel in LOGIN_SEL[schluessel]:
                    check_cancelled()
                    try:
                        page.fill(sel, wert)
                    except Exception:
                        check_cancelled()
                        continue
                    check_cancelled()
                    if _login_value(page, sel, wert, check_cancelled):
                        selected = sel
                        break
                if selected is None:
                    melden('Web-Anmeldung offen – ein Anmeldefeld konnte nicht bestätigt werden.')
                    return False
                entered.append((selected, wert))
            if not all(_login_value(page, sel, wert, check_cancelled) for sel, wert in entered):
                melden('Web-Anmeldung offen – Anmeldedaten wurden im Fenster geändert.')
                return False
            # Include cookies created/refreshed during navigation before submit.
            before = backend.read_cookies(handle)
            check_cancelled()
            old_tokens = _login_tokens(before)
            submitted = False
            for sel in LOGIN_SEL['senden']:
                check_cancelled()
                try:
                    page.click(sel)
                except Exception:
                    check_cancelled()
                    continue
                check_cancelled()
                submitted = True
                break
            if not submitted:
                melden('Web-Anmeldung offen – die Anmeldung wurde nicht abgesendet.')
                return False
            frist = time.monotonic() + timeout
            while time.monotonic() < frist:
                check_cancelled()
                cookies = backend.read_cookies(handle)
                check_cancelled()
                if CookieJar.authenticated(cookies, 'tiktok') and _login_tokens(cookies) - old_tokens:
                    check_cancelled()
                    CookieJar.save(konto['identity'], 'tiktok', CookieJar.for_platform(cookies, 'tiktok'))
                    melden('Web-Anmeldung steht.')
                    return True
                page.wait_for_timeout(2000)
            melden('Web-Anmeldung nicht bestätigt – bitte im Fenster prüfen.')
            return False
    finally:
        from ..cli import shutdown_runtime
        shutdown_runtime(backend, None)


def _login_value(page, selector, expected, check):
    """Read the current field value; never set a value via this verification."""
    check()
    try:
        actual = page.evaluate('(selector) => { const field = document.querySelector(selector); '
                               'return field ? field.value : null; }', selector)
    except Exception:
        check()
        return False
    check()
    return actual == expected


def _login_tokens(cookies):
    from ..net.cookies import CookieJar, AUTH
    return {item['value'] for item in CookieJar.for_platform(cookies, 'tiktok')
            if item['name'] in AUTH['tiktok'] and item.get('value')}


def _erstes(page, auswahl, *, enabled=False):
    for sel in auswahl:
        try:
            field = page.query_selector(sel)
            if field and field.is_visible() and (not enabled or field.is_enabled()):
                return sel
        except Exception:
            continue
    return None

def _waehle(page, auswahl, wert, *, check=None):
    from . import _select
    return _select(page, auswahl, wert, check=check)

