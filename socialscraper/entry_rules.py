"""Shared browser entry rules, including the explicitly saved privacy choice."""
import time
from urllib.parse import parse_qs, urlsplit
from .net.errors import FinalError
from .privacy_consent import accept_known

SEARCH_HOME = 'https://www.google.de/'
# Observed homepage redirects are intermediate gates, never homepage proof.
HOME_CONSENT_PATHS = {'web.de': frozenset(('/consent-management/', '/magazine/consent-management/'))}
GOOGLE_HOSTS = frozenset(('google.de', 'www.google.de', 'google.com', 'www.google.com',
                          'consent.google.de', 'consent.google.com'))
HOMES = {'web.de': 'web.de', 'gmx.net': 'gmx.net', 'gmail.com': 'google.com',
         'outlook.com': 'microsoft.com', 'google': 'google.com', 'youtube': 'youtube.com',
         'instagram': 'instagram.com', 'reddit': 'reddit.com', 'x': 'x.com',
         'trustpilot': 'trustpilot.com', 'tiktok': 'tiktok.com'}


def progress(ctx, stage):
    callback = getattr(ctx, 'progress', None)
    if callable(callback):callback(stage)


def https_location(url, hosts):
    try:
        parts = urlsplit(url)
        if (parts.scheme == 'https' and parts.hostname in hosts and parts.port in (None, 443)
                and parts.username is None and parts.password is None):
            return parts
    except (TypeError, ValueError):
        pass
    raise FinalError('Der Einstieg hat die erwartete HTTPS-Seite verlassen; der Auftrag wurde angehalten.')


def homepage_result(url, domain):
    """Accept exact official homepages, including observed Google redirect links."""
    try:
        parts = urlsplit(url)
        if parts.hostname in GOOGLE_HOSTS and parts.path == '/url':
            https_location(url, GOOGLE_HOSTS)
            params = parse_qs(parts.query)
            targets = params.get('q') or params.get('url') or []
            if len(targets) != 1:
                return False
            url = targets[0]
        parts = https_location(url, (domain, 'www.' + domain))
        return parts.path in ('', '/') and not parts.query and not parts.fragment
    except (FinalError, TypeError, ValueError):
        return False


def _browser_timeout(error):
    return (type(error).__name__ == 'TimeoutError' and
            type(error).__module__.startswith(('patchright.', 'playwright.', 'rebrowser_playwright.')))


def visible_frames(page):
    # Collect the whole snapshot before exposing any frame to a caller. A
    # CAPTCHA redirect can detach/replace an iframe while visibility is read.
    # Retrying only this read avoids repeating consent or navigation clicks.
    for attempt in range(3):
        try:
            url = page.url
            frames = tuple(page.frames)
            visible = []
            for frame in frames:
                ancestor = frame
                while ancestor.parent_frame is not None:
                    if not ancestor.frame_element().is_visible():
                        break
                    ancestor = ancestor.parent_frame
                else:
                    visible.append(frame)
            current = tuple(page.frames)
            if (page.url == url and len(frames) == len(current)
                    and all(a is b for a, b in zip(frames, current))):
                return tuple(visible)
        except Exception:
            pass
        if attempt < 2:
            try:
                page.wait_for_timeout(100)
            except Exception:
                break
    # Persistently unreadable state must never be treated as no consent/CAPTCHA.
    raise FinalError('Die Seitenfenster konnten nach dem Seitenwechsel nicht zuverlässig geprüft werden. '
                     'Bitte den angezeigten Browserzustand prüfen.') from None


def _page_replaced(error):
    """Recognize a replaced document; never suppress cancellation or closure."""
    if isinstance(error, FinalError):
        return False
    message = str(error).casefold()
    return any(part in message for part in (
        'frame was detached', 'frame has been detached',
        'execution context was destroyed', 'cannot find context with specified id'))


def manual_gate(page, *, consent_only=False):
    try:
        return _manual_gate(page, consent_only=consent_only)
    except Exception as error:
        # A challenge can disappear after visible_frames returned. Do not treat
        # an unreadable control scan as permission to navigate or fill fields.
        # The caller's bounded loop will reobserve, checking cancellation first.
        if _page_replaced(error):
            return True
        raise


def _manual_gate(page, *, consent_only=False):
    import re
    labels = re.compile(r'^\s*(?:Alle akzeptieren|Alles akzeptieren|Alle ablehnen|Alles ablehnen|'
                        r'Akzeptieren und weiter|Zustimmen und weiter|Einstellungen speichern|'
                        r'Alle Cookies akzeptieren|Accept all cookies|Allow all cookies|'
                        r'Accept all|Reject all|Ich stimme zu)\s*$', re.I)
    for frame in visible_frames(page):
        for role in ('button', 'link'):
            nodes = frame.get_by_role(role, name=labels)
            if any(nodes.nth(i).is_visible() for i in range(min(nodes.count(), 40))):
                return True
        if not consent_only:
            nodes = frame.locator('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], #captcha-form, input[name="captcha"]')
            if any(nodes.nth(i).is_visible() for i in range(min(nodes.count(), 20))):
                return True
    return False


def remaining_ms(deadline, limit=30000):
    left = deadline - time.monotonic()
    if left <= 0:
        raise FinalError('Der Seiteneinstieg wurde nicht rechtzeitig abgeschlossen; kein direkter Ersatzaufruf.')
    return min(limit, max(1, int(left * 1000)))


def visit_home(page, domain, ctx):
    """Google page → actual search submission → actual official homepage link."""
    if not domain or not all(c.isascii() and (c.isalnum() or c in '.-') for c in domain):
        raise ValueError('Ungültige Suchdomain.')
    if not hasattr(page, 'frames') or not callable(getattr(page, 'locator', None)):
        raise FinalError('Dieser Browser unterstützt den geführten Seiteneinstieg nicht. '
                         'Bitte den Standard-Chrome oder Firefox in den Einstellungen wählen.')
    started = time.monotonic()
    deadline = started + min(ctx.timeout, 600)
    ctx.check_cancelled()
    progress(ctx, 'Google-Einstieg')
    ctx.hinweis('Einstiegsregel: google.de → offizielle Homepage → Registrierung. '
                'Offene Bestätigungen werden im Browser angezeigt.')
    page.goto(SEARCH_HOME, wait_until='domcontentloaded', timeout=remaining_ms(deadline))
    ctx.check_cancelled()
    searched = clicked = notified = provider_notified = False
    opaque_notified = False
    while time.monotonic() < deadline:
        ctx.check_cancelled()
        try:
            url = page.url
            host = urlsplit(url).hostname
            # In assisted mode the user may follow the search result themselves.
            # Accept the same official homepage after search, regardless of who
            # clicked; retain the HTTPS, host and homepage-path validation below.
            if searched and host in (domain, 'www.' + domain):
                parts = https_location(url, (domain, 'www.' + domain))
                if parts.path in HOME_CONSENT_PATHS.get(domain, ()):
                    progress(ctx, 'WEB.DE-Datenschutzauswahl vor der Homepage')
                    if not accept_known(page, ctx, timeout=remaining_ms(deadline, 1500)):
                        if not provider_notified:
                            ctx.hinweis('WEB.DE wartet auf deine Datenschutzauswahl im Browserfenster.')
                            provider_notified = True
                    ctx.warte(250)
                    continue
                if parts.path not in ('', '/'):
                    raise FinalError('Der Suchtreffer führte nicht zur offiziellen Homepage; Einstieg angehalten.')
                ctx.timeout = max(.001, ctx.timeout - (time.monotonic() - started))
                progress(ctx, 'Offizielle Homepage erreicht')
                return
            https_location(url, GOOGLE_HOSTS)
            if accept_known(page, ctx, timeout=remaining_ms(deadline, 1500)):
                ctx.warte(250)
                continue
            if manual_gate(page):
                if not notified:
                    ctx.hinweis('Google wartet auf deine Bestätigung im Browser. Der Auftrag bleibt an dieser Stelle.')
                    notified = True
            elif not searched:
                boxes = page.locator('textarea[name="q"], input[name="q"]')
                for i in range(min(boxes.count(), 10)):
                    box = boxes.nth(i)
                    if box.is_visible():
                        ctx.check_cancelled()
                        from .net.input_behavior import fill
                        fill(page, box, domain, check=ctx.check_cancelled, timeout=remaining_ms(deadline, 2000))
                        ctx.check_cancelled()
                        if manual_gate(page):
                            break
                        # Commit the intent before an action that can navigate and
                        # destroy its old document before the driver returns.
                        searched = True
                        try:
                            box.press('Enter', timeout=remaining_ms(deadline, 2000))
                        except Exception as error:
                            if not _browser_timeout(error):raise
                            # The submission may already have navigated. Keep
                            # the committed intent and observe without Enter again.
                            ctx.hinweis('Die Suchbestätigung dauert länger. Die Eingabe wird nicht wiederholt; '
                                        'der Auftrag prüft die folgende Seite weiter.')
                        progress(ctx, 'Google-Suchergebnisse prüfen')
                        break
            elif not clicked:
                links = page.locator('a:has(h3)')
                if not links.count():links = page.locator('a:has([role="heading"])')
                for i in range(min(links.count(), 100)):
                    link = links.nth(i)
                    if not link.is_visible():continue
                    href = link.evaluate('(x) => x.href')
                    parts = urlsplit(href)
                    if parts.hostname in GOOGLE_HOSTS and parts.path == '/goto' and not opaque_notified:
                        ctx.hinweis('Google verwendet verschlüsselte Weiterleitungslinks. Bitte den offiziellen '
                                    'Homepage-Treffer selbst öffnen; die Zieladresse wird danach geprüft.')
                        opaque_notified = True
                    if homepage_result(href, domain):
                        ctx.check_cancelled()
                        if manual_gate(page):
                            break
                        progress(ctx, 'Offizielle Homepage öffnen')
                        clicked = True
                        from .net.input_behavior import click
                        click(page, link, check=ctx.check_cancelled, timeout=remaining_ms(deadline, 5000))
                        ctx.check_cancelled()
                        break
        except Exception as error:
            if not _page_replaced(error):
                raise
            # Reobserve the new document within the original deadline. Flags
            # retain accepted search/click intents; never replay these actions.
            ctx.check_cancelled()
        ctx.warte(250)
    if provider_notified:
        raise FinalError('WEB.DE-Datenschutzauswahl oder Rückkehr zur Homepage nicht abgeschlossen; '
                         'kein direkter Ersatzaufruf gestartet.')
    raise FinalError('Google-Einstieg nicht abgeschlossen: passender Homepage-Treffer oder Bestätigung fehlt. '
                     'Kein direkter Ersatzaufruf gestartet.')


def open_registration(page, site, signup_url, ctx):
    """Other providers share the rule; unfamiliar intermediate links remain manual."""
    if site not in HOMES:
        raise FinalError('Für diese Website fehlt eine geprüfte Einstiegsregel; kein direkter Aufruf.')
    domain = HOMES[site]
    target = urlsplit(signup_url)
    https_location(signup_url, (target.hostname,))
    visit_home(page, domain, ctx)
    progress(ctx, 'Registrierungslink auf der Homepage')
    started = time.monotonic()
    deadline = started + min(ctx.timeout, 600)
    ctx.hinweis('Bitte auf der offiziellen Homepage zur Registrierung gehen, '
                'falls kein eindeutiger Registrierungslink gefunden wird.')
    clicked = False
    while time.monotonic() < deadline:
        ctx.check_cancelled()
        parts = https_location(page.url, (domain, 'www.' + domain, target.hostname))
        if accept_known(page, ctx, timeout=remaining_ms(deadline, 1500)):
            ctx.warte(250)
            continue
        if parts.hostname == target.hostname and parts.path.rstrip('/') == target.path.rstrip('/'):
            ctx.timeout = max(.001, ctx.timeout - (time.monotonic() - started))
            return
        if not clicked and not manual_gate(page):
            links = page.locator('a[href]')
            for i in range(min(links.count(), 200)):
                link = links.nth(i)
                if not link.is_visible():continue
                href = link.evaluate('(x) => x.href')
                if href.rstrip('/') != signup_url.rstrip('/'):continue
                ctx.check_cancelled()
                if manual_gate(page):break
                from .net.input_behavior import click
                click(page, link, check=ctx.check_cancelled, timeout=remaining_ms(deadline, 5000))
                clicked = True
                break
        ctx.warte(250)
    raise FinalError('Der Registrierungslink wurde nicht erreicht. Kein direkter Ersatzaufruf gestartet.')
