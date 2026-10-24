from . import unitedinternet
SIGNUP_URL, DOMAENE = 'https://register.web.de/', 'web.de'

import re
import time
from urllib.parse import urlsplit
from ..net.errors import FinalError
from .. import entry_rules

_PRIVACY = re.compile(r'^\s*(?:Akzeptieren und weiter|Alle akzeptieren|Einstellungen speichern|Alles ablehnen|Alle ablehnen|Zustimmen und weiter)\s*$', re.I)
_ERROR = re.compile(r'es ist ein\s+fehler aufgetreten|ein fehler ist aufgetreten|registrierung fehlgeschlagen', re.I)
_COMPARISON_PATHS = frozenset(('/premium/tarifevergleich/', '/premium/tarifevergleich-mobile/'))
_PROVIDER_HOSTS = frozenset(('web.de', 'www.web.de', 'register.web.de', 'hilfe.web.de', 'produkte.web.de'))


def _observed_location(url):
    """Reading an owned intermediate page is separate from choosing its actions."""
    try:
        parts = urlsplit(url)
        if (parts.scheme == 'https' and parts.hostname in _PROVIDER_HOSTS
                and parts.port in (None, 443) and parts.username is None and parts.password is None):
            return parts
    except (TypeError, ValueError):pass
    raise FinalError('Der WEB.DE-Einstieg hat die erwartete HTTPS-Seite verlassen; der Auftrag wurde angehalten.')


def _comparison(parts):
    return ((parts.hostname in ('web.de', 'www.web.de') and parts.path == '/email/tarifvergleich/')
            or (parts.hostname == 'produkte.web.de' and parts.path in _COMPARISON_PATHS))


def _location(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme == 'https' and (parts.hostname in ('web.de', 'www.web.de', 'register.web.de', 'hilfe.web.de')
                or (parts.hostname=='produkte.web.de' and parts.path in _COMPARISON_PATHS))
                and parts.port in (None, 443) and parts.username is None and parts.password is None):
            return parts
    except (TypeError, ValueError):
        pass
    raise FinalError('Der WEB.DE-Einstieg hat die erwartete HTTPS-Seite verlassen; der Auftrag wurde angehalten.')


def _visible_frames(page):
    return entry_rules.visible_frames(page)


def _entry_state(page):
    """Read visible state without choosing privacy settings or submitting forms."""
    parts = _observed_location(page.url)
    unitedinternet.raise_if_rejected(page)
    # The provider fills ordinary form fields while leaving CAPTCHA manual.
    privacy = entry_rules.manual_gate(page, consent_only=True)
    for frame in _visible_frames(page):
        errors = frame.get_by_text(_ERROR)
        if any(errors.nth(i).is_visible() for i in range(min(errors.count(), 32))):
            raise FinalError('WEB.DE meldet einen Fehler beim Registrierungseinstieg; die Ursache ist nicht ersichtlich.')
        for role in ('button', 'link'):
            controls = frame.get_by_role(role, name=_PRIVACY)
            if any(controls.nth(i).is_visible() for i in range(min(controls.count(), 32))):
                privacy = True
    return parts, privacy


def _entry_link(page, *, tariff=False):
    selector = 'a[href*="tarifvergleich"], a[href*="tarifevergleich"]' if tariff else 'a[href*="register.web.de"]'
    links = page.locator(selector)
    for i in range(min(links.count(), 100)):
        link = links.nth(i)
        if not link.is_visible():continue
        url = link.evaluate('(x) => x.href')
        try: parts = _location(url)
        except FinalError: continue
        if tariff and _comparison(parts):
            return link
        # Observed public FreeMail entry. Do not guess paid editions or routes.
        if not tariff and parts.hostname == 'register.web.de' and parts.path == '/' and not parts.query:
            return link
    return None


def _homepage_registration_control(page):
    """The observed mobile header may expose a button instead of a direct link."""
    parts = _location(page.url)
    if parts.hostname not in ('web.de', 'www.web.de') or parts.path not in ('', '/'):
        return None
    for role in ('link', 'button'):
        nodes = page.get_by_role(role, name=re.compile(r'^\s*Registrieren\s*$', re.I))
        for index in range(min(nodes.count(), 30)):
            node = nodes.nth(index)
            if not node.is_visible() or not node.is_enabled():continue
            href = node.evaluate("el => el.tagName === 'A' ? el.href : ''")
            if href:
                try:destination = _location(href)
                except FinalError:continue
                if destination.hostname not in ('web.de', 'www.web.de', 'produkte.web.de', 'register.web.de'):continue
                if destination.hostname == 'register.web.de' and (destination.path != '/' or destination.query):continue
            return node
    return None


def open_signup(page, ctx):
    """Follow public provider links once, within the same proxy session."""
    entry_rules.visit_home(page, 'web.de', ctx)
    entry_rules.progress(ctx, 'WEB.DE-Homepage: Registrierungsweg suchen')
    started = time.monotonic()
    deadline = started + min(ctx.timeout, 600)
    phase = 'homepage'; loading_since = started; privacy_notified = False; link_notified = False
    def navigate(link, stage, *, homepage_control=False):
        ctx.check_cancelled()
        href = link.evaluate("el => el.tagName === 'A' ? el.href : ''") if homepage_control else link.evaluate('(x) => x.href')
        if href:_location(href)
        elif not homepage_control:raise FinalError('Der WEB.DE-Einstiegslink hat kein gültiges Ziel.')
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise FinalError('Der WEB.DE-Einstieg wurde nicht rechtzeitig abgeschlossen.')
        if entry_rules.manual_gate(page):return False
        entry_rules.progress(ctx, stage)
        from ..net.input_behavior import click
        click(page, link, check=ctx.check_cancelled, timeout=min(5000, max(1, int(remaining * 1000))))
        ctx.check_cancelled()
        return True
    while time.monotonic() < deadline:
        ctx.check_cancelled()
        parts, privacy = _entry_state(page)
        ctx.check_cancelled()
        if privacy:
            if entry_rules.accept_known(page, ctx, timeout=entry_rules.remaining_ms(deadline, 1500)):
                loading_since = time.monotonic()
                ctx.warte(250)
                continue
            if not privacy_notified:
                ctx.hinweis('WEB.DE wartet auf deine Datenschutzauswahl im Browserfenster.')
                privacy_notified = True
            loading_since = time.monotonic()
        elif parts.hostname == 'register.web.de' and unitedinternet._erstes(page,
                unitedinternet.SEL['vorname'] + unitedinternet.SEL['adresse'] + unitedinternet.SEL['passwort']):
            ctx.timeout = max(0.001, ctx.timeout - (time.monotonic() - started))
            return
        else:
            # A manually followed comparison link must advance the same phase
            # as an automatic click, without reopening the comparison page.
            if _comparison(parts):
                if phase != 'tariff':
                    entry_rules.progress(ctx, 'WEB.DE-Tarifübersicht: kostenlosen Registrierungsweg suchen')
                    link_notified = False
                phase = 'tariff'
            elif not (parts.hostname in ('web.de', 'www.web.de') and parts.path in ('', '/')):
                # An unknown provider page may be a new intermediate step.
                # Observe and hand off without guessing links or submitting.
                if not link_notified:
                    ctx.hinweis('Eine weitere WEB.DE-Zwischenseite ist geöffnet. Bitte den kostenlosen '
                                'Registrierungsweg im Fenster fortsetzen. Der Auftrag wartet auf eine '
                                'erkannte Tarifübersicht oder das Registrierungsformular.')
                    link_notified = True
                ctx.warte(500)
                continue
            target = (_entry_link(page, tariff=True) or _entry_link(page)) if phase in ('homepage', 'landing') else (
                _entry_link(page) if phase == 'tariff' else None)
            if target:
                destination = _location(target.evaluate('(x) => x.href'))
                next_phase = 'registration' if destination.hostname == 'register.web.de' else 'tariff'
                if navigate(target, 'WEB.DE-Tarifübersicht öffnen' if next_phase == 'tariff' else 'WEB.DE-Registrierung öffnen'):
                    phase = next_phase
                    loading_since = time.monotonic()
                    link_notified = False
                    continue
            if phase == 'homepage':
                control = _homepage_registration_control(page)
                if control is not None and navigate(control, 'WEB.DE: Registrieren öffnen', homepage_control=True):
                    # Dispatch once. The next observation validates the actual
                    # destination; an unchanged header must not be reclicked.
                    phase = 'landing'
                    loading_since = time.monotonic()
                    link_notified = False
                    continue
            if not link_notified and time.monotonic() - loading_since >= 10:
                ctx.hinweis('Der passende WEB.DE-Registrierungslink ist noch nicht erkennbar. '
                            'Bitte auf der sichtbaren WEB.DE-Seite den kostenlosen Registrierungsweg öffnen. '
                            'Der Auftrag wartet weiter und prüft die erreichte Seite.')
                link_notified = True
        ctx.warte(500)
    raise FinalError('Der WEB.DE-Einstieg wurde nicht rechtzeitig abgeschlossen; kein Postfach angelegt.')
def run(page, ctx):
    return unitedinternet.run(page, ctx)
def wunsch_adresse(vorname, nachname, kennung):
    return unitedinternet.wunsch_adresse(vorname, nachname, kennung, domaene=DOMAENE)
