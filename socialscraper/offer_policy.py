"""Reject optional offers during authorised signup; never start a trial or buy."""
import re
from urllib.parse import urlsplit

from .net.errors import FinalError

OFFER = re.compile(r'\b(?:premium|upgrade|abonnement|abo|probeabo|testabo|zusatzangebot|zusatzpaket|'
                   r'kostenpflichtig\w*|kostenlos testen|gratis testen|probezeit|testphase|free trial|subscription|'
                   r'newsletter|werbeangebote|partnerangebote|optional(?:es|e)? angebot)\b|'
                   r'\b[1-9]\d*(?:[.,]\d{2})?\s*(?:€|eur\b)', re.I)
DECLINE = (
    'kostenlos fortfahren', 'kostenlos weiter', 'bei kostenlos bleiben', 'kostenlose version nutzen',
    'weiter mit freemail', 'ohne upgrade fortfahren', 'ohne zusatzleistung fortfahren',
    'ohne zusatzangebot fortfahren', 'ohne abo fortfahren', 'continue for free', 'continue with free',
    'angebot ablehnen', 'angebot überspringen', 'abo ablehnen', 'testangebot ablehnen', 'nein danke', 'nein, danke',
    'no thanks', 'no, thanks', 'ablehnen', 'decline', 'überspringen', 'skip', 'nicht jetzt', 'not now',
)
DECLINE_RE = re.compile(r'^\s*(?:' + '|'.join(re.escape(label) for label in DECLINE) + r')\s*$', re.I)
PURCHASE_RE = re.compile(r'^\s*(?:zahlungspflichtig bestellen|kostenpflichtig bestellen|kostenpflichtig anmelden|jetzt kaufen|'
                         r'jetzt abonnieren|testabo starten|probeabo starten|start free trial|'
                         r'(?:jetzt )?kostenlos testen|(?:jetzt )?gratis testen|buy now|subscribe now|'
                         r'.*\b[1-9]\d*(?:[.,]\d{2})?\s*(?:€|eur\b).*)\s*$', re.I)
_SCOPE_JS = r'''el => {
 const offer = /\b(premium|upgrade|abonnement|abo|probeabo|testabo|zusatzangebot|zusatzpaket|kostenpflichtig\w*|kostenlos testen|gratis testen|probezeit|testphase|free trial|subscription|newsletter|werbeangebote|partnerangebote)\b|\b[1-9]\d*([.,]\d{2})?\s*(€|eur\b)/i;
 for (let surface = el, depth = 0; surface && depth < 9; surface = surface.parentElement, depth++) {
   const text = surface.innerText || '';
   if (text.length > 6000) return false;
   const inputs = [...surface.querySelectorAll('input,textarea,select,[contenteditable="true"]')]
     .some(x => !['hidden','checkbox','radio','button','submit'].includes(x.type) && x.getClientRects().length);
   if (inputs) return false;
   if (surface.querySelector('iframe[src*="captcha" i],#captcha-form,[data-testid*="captcha" i]')) return false;
   const marked = surface.matches('[role="dialog"],dialog,[aria-modal="true"]') || /upsell|paywall|subscription|premium|trial|offer/i.test(surface.id + ' ' + surface.className);
   const heading = [...surface.querySelectorAll('h1,h2,h3,[role="heading"]')].some(x => offer.test(x.innerText || ''));
   if (offer.test(text) && (marked || heading)) return true;
   if (surface.matches('body,html')) return false;
 }
 return false;
}'''


def android_plan(tree, nodes, *, stage=None):
    """Choose a specific rejection inside a recognisable offer surface."""
    text = ' '.join(node.get(key) or '' for node in nodes for key in ('text', 'content-desc')
                    if not node.get('class', '').endswith('EditText'))
    if re.search(r'captcha|ich bin ein mensch|ich bin kein roboter|verify you are human', text, re.I):
        return None
    allowed = set(nodes)
    parents = {child: parent for parent in tree.iter() for child in parent}
    candidates = []
    for node in nodes:
        if node.get('class', '').endswith('EditText'):
            continue
        labels = {(node.get(key) or '').strip().casefold() for key in ('text', 'content-desc')}
        labels &= set(DECLINE)
        if not labels:
            continue
        scope = node
        marked = False
        while scope in allowed:
            children = [child for child in scope.iter('node') if child in allowed]
            if len(children) > 70 or any(child.get('class', '').endswith('EditText') for child in children):
                break
            captions = ' '.join(child.get(key) or '' for child in children for key in ('text', 'content-desc'))
            if OFFER.search(captions):
                marked = True
                break
            scope = parents.get(scope)
        if not marked:
            continue
        target = node
        while target in allowed:
            if target.get('clickable') == 'true' or target.get('class') == 'android.widget.Button':
                if any(PURCHASE_RE.fullmatch(child.get(key) or '') for child in target.iter('node')
                       if child in allowed for key in ('text', 'content-desc')):
                    break  # a container holding both choices is not a reject button
                label = min(labels, key=DECLINE.index)
                candidates.append((DECLINE.index(label), label, node))
                break
            target = parents.get(target)
    if not candidates:
        return None
    rank = min(item[0] for item in candidates)
    matches = {(label, node) for priority, label, node in candidates if priority == rank}
    if len(matches) != 1:
        return None
    label, node = next(iter(matches))
    return ('decline_offer', stage, label, node.get('resource-id') or ''), node


def android_pending(tree, nodes):
    captions = [node.get(key) or '' for node in nodes
                if not node.get('class', '').endswith('EditText') for key in ('text', 'content-desc')]
    return (any(PURCHASE_RE.fullmatch(text) for text in captions)
            or (not any(node.get('class', '').endswith('EditText') for node in nodes)
                and bool(OFFER.search(' '.join(captions)))))


def android_purchase_visible(nodes):
    captions = [node.get(key) or '' for node in nodes if not node.get('class', '').endswith('EditText')
                for key in ('text', 'content-desc')]
    return (any(PURCHASE_RE.fullmatch(text) for text in captions)
            or (not any(node.get('class', '').endswith('EditText') for node in nodes)
                and bool(re.search(r'\b(premium|upgrade|abo|abonnement|probeabo|testabo|kostenpflichtig\w*|'
                                   r'kostenlos testen|gratis testen|probezeit|testphase|free trial|subscription)\b', ' '.join(captions), re.I))))


def android_available(tree, ctx, own_nodes):
    action = android_plan(tree, own_nodes(tree))
    attempted = getattr(ctx, '_declined_android_offers', set())
    return bool(action is not None and len(attempted) < 12 and action[0] not in attempted)


def android_decline(adb, ctx, own_nodes):
    from .android.interaction import changed, tap
    from .operation import check_cancelled
    check_cancelled(getattr(ctx, 'stop', None))
    tree = adb.baum(timeout=5)
    action = android_plan(tree, own_nodes(tree))
    attempted = getattr(ctx, '_declined_android_offers', set())
    ctx._declined_android_offers = attempted
    if action is None or len(attempted) >= 12 or action[0] in attempted:
        return False
    key, node = action
    def target(current):
        selected = android_plan(current, own_nodes(current))
        return selected[1] if selected is not None and selected[0] == key else None
    before = tap(adb, target, own_nodes, stop=getattr(ctx, 'stop', None), before_dispatch=lambda: attempted.add(key))
    check_cancelled(getattr(ctx, 'stop', None))
    changed(adb, before, own_nodes, stop=getattr(ctx, 'stop', None))
    ctx.melden('Zusatzangebot: Die Schaltfläche zum Ablehnen wurde gewählt. Der kostenlose Weg wird fortgesetzt, sobald das Angebot geschlossen ist.')
    return True


def _frames(page):
    from .privacy_consent import _allowed
    from .entry_rules import visible_frames
    if not _allowed(page.url):
        return ()
    return tuple(frame for frame in visible_frames(page) if _allowed(frame.url)
                 or (frame.url == 'about:blank' and frame.parent_frame is not None))


def offer_pending(page):
    """A recognised offer or explicit purchase action blocks ordinary submit."""
    for frame in _frames(page):
        buttons = frame.get_by_role('button', name=PURCHASE_RE)
        if any(buttons.nth(i).is_visible() for i in range(min(buttons.count(), 30))):
            return True
        surfaces = frame.locator('[role="dialog"],dialog,[aria-modal="true"],main,[id*="upsell" i],[class*="paywall" i]')
        for index in range(min(surfaces.count(), 30)):
            surface = surfaces.nth(index)
            if surface.is_visible() and surface.evaluate(_SCOPE_JS):
                return True
    return False


def decline_known(page, ctx):
    ctx.check_cancelled()
    attempted = getattr(ctx, '_declined_browser_offers', set())
    ctx._declined_browser_offers = attempted
    if len(attempted) >= 12:
        return False
    for frame in _frames(page):
        matches = []
        for role in ('button', 'link'):
            controls = frame.get_by_role(role, name=DECLINE_RE)
            for index in range(min(controls.count(), 30)):
                control = controls.nth(index)
                if not control.is_visible() or not control.is_enabled() or not control.evaluate(_SCOPE_JS):
                    continue
                label = control.evaluate("el => (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase()")
                if label in DECLINE:
                    matches.append((DECLINE.index(label), label, control))
        if not matches:
            continue
        rank = min(item[0] for item in matches)
        chosen = [item for item in matches if item[0] == rank]
        if len(chosen) != 1:
            continue
        _rank, label, control = chosen[0]
        location = urlsplit(frame.url)
        key = (location.hostname, location.path, label)
        if key in attempted:
            continue
        ctx.check_cancelled()
        attempted.add(key)
        from .net.input_behavior import click
        click(page, control, check=ctx.check_cancelled, timeout=3000)
        ctx.check_cancelled()
        ctx.melden('Zusatzangebot: Die Schaltfläche zum Ablehnen wurde gewählt. Der kostenlose Weg wird fortgesetzt, sobald das Angebot geschlossen ist.')
        page.wait_for_timeout(600)
        ctx.check_cancelled()
        return True
    return False


def guard_submit(page, ctx):
    """Do not let a general submit selector choose a purchase or trial."""
    if not offer_pending(page):
        return False
    if decline_known(page, ctx):
        return True  # the caller must observe the changed page before submitting
    raise FinalError('Ein kostenpflichtiges oder optionales Zusatzangebot ist geöffnet. '
                     'Die passende Ablehnen-Schaltfläche wurde nicht sicher erkannt. '
                     'Der Auftrag wurde angehalten; es wurde keine Buchung ausgelöst.')
