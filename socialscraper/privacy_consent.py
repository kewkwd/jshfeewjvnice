"""User-selected cookie consent, bounded and separate from account form submission."""
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from . import accountvault
from .net.errors import FinalError

FILE_NAME = 'browser_privacy_choice.json'
LABELS = re.compile(r'^\s*(?:Alle akzeptieren|Alles akzeptieren|Alle Cookies akzeptieren|'
                    r'Akzeptieren und weiter|Zustimmen und weiter|Accept all|Accept all cookies|'
                    r'Allow all cookies)\s*$', re.I)

# Inspect the nearest consent surface. Never click an account/payment form merely
# because a privacy link happens to occur elsewhere on the page.
_COOKIE_SURFACE = r'''el => {
 const fields = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]),textarea,select,iframe[src*="captcha" i]';
 const form = el.closest('form');
 if (form && form.querySelector(fields)) return false;
 for (let surface = el.parentElement; surface; surface = surface.parentElement) {
   if (surface.querySelector(fields)) return false;
   if (/\bcookies?|\btracking/i.test(surface.innerText || '')) return true;
 }
 return false;
}'''


def enabled(db_path):
    try:data = json.loads((Path(db_path).parent / FILE_NAME).read_text(encoding='utf8'))
    except FileNotFoundError:return False
    except (OSError, ValueError):raise ValueError('Die gespeicherte Datenschutzauswahl ist nicht lesbar.') from None
    if not isinstance(data, dict) or data.get('version') != 1 or type(data.get('accept')) is not bool:
        raise ValueError('Die gespeicherte Datenschutzauswahl ist ungültig.')
    return data['accept']


def save(db_path, accept):
    if type(accept) is not bool:raise ValueError('Datenschutzauswahl muss Ja oder Nein sein.')
    accountvault._write(Path(db_path).parent / FILE_NAME, {'version':1, 'accept':accept})


def _allowed(url):
    from .entry_rules import HOMES
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.port in (None, 443) and not parts.username and not parts.password
                and any(parts.hostname == root or (parts.hostname or '').endswith('.' + root)
                        for root in {*HOMES.values(), 'google.de'}))
    except (TypeError, ValueError):return False


def accept_known(page, ctx, *, timeout=1500):
    """At most one real consent click per call. No navigation, JS clicks or retries."""
    if getattr(ctx, 'accept_privacy', False) is not True:return False
    if not _allowed(page.url):return False
    from .entry_rules import visible_frames
    ctx.check_cancelled()
    attempts = getattr(ctx, '_privacy_attempts', None)
    if attempts is None:
        attempts = set(); ctx._privacy_attempts = attempts
    for frame in visible_frames(page):
        ctx.check_cancelled()
        frame_url = frame.url
        # Cross-origin consent managers are allowed only through provider-owned
        # domains. Unknown frames remain visible for a later explicit decision.
        if not _allowed(frame_url) and not (frame_url == 'about:blank' and frame.parent_frame is not None):continue
        for role in ('button', 'link'):
            nodes = frame.get_by_role(role, name=LABELS)
            for index in range(min(nodes.count(), 30)):
                node = nodes.nth(index)
                if not node.is_visible() or not node.is_enabled():continue
                if not node.evaluate(_COOKIE_SURFACE):continue
                label = node.inner_text(timeout=min(timeout, 1000)).strip()
                key = (page.url, frame_url, label)
                if key in attempts:continue
                if len(attempts) >= 8:
                    raise FinalError('Zu viele Datenschutzauswahlen in einem Auftrag; bitte den angezeigten Dialog prüfen.')
                ctx.check_cancelled()
                attempts.add(key)  # An ambiguous timeout must never cause repeat clicks.
                try:
                    from .net.input_behavior import click
                    click(page, node, check=ctx.check_cancelled, timeout=timeout)
                except Exception as error:
                    # Use the active engine's public exception type without
                    # importing optional browser engines just for error handling.
                    timeout_types = tuple(kind for name in ('patchright.sync_api',
                        'playwright.sync_api', 'rebrowser_playwright.sync_api')
                        if isinstance(kind := getattr(sys.modules.get(name), 'TimeoutError', None), type))
                    if not isinstance(error.__cause__ or error, timeout_types):
                        raise
                    ctx.check_cancelled()
                    # A timeout can happen before dispatch OR while waiting for
                    # navigation. Retain the attempt, reobserve, never reclick.
                    ctx.hinweis('Cookie-/Tracking-Auswahl noch nicht bestätigt. '
                                'Bitte den angezeigten Dialog prüfen; kein erneuter automatischer Klick.')
                    return False
                ctx.check_cancelled()
                ctx.hinweis('Cookie-/Tracking-Auswahl gemäß gespeicherter Regel akzeptiert.')
                return True
    return False
