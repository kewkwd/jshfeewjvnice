"""Plan ordinary WEB.DE actions from a fresh native tree, without blind retries."""
import re

from .webde_fields import (PACKAGE, _choice_groups, fields, form_signature,
                           normal, own_nodes, registration_form, values)
from ..operation import check_cancelled
from ..offer_policy import android_plan, android_pending, android_purchase_visible
from .interaction import changed, tap
from .webde_controls import matches as selections_match

NEXT = {'weiter', 'next', 'fortfahren', 'continue'}
CREATE = {'kostenlos registrieren', 'zustimmen und kostenlos registrieren', 'freemail registrieren',
          'kostenloses postfach erstellen', 'kostenlos anmelden', 'create free email account'}
REQUIRED = ('allgemeine geschäftsbedingungen', 'agb', 'nutzungsbedingungen',
            'terms of service', 'terms and conditions', 'datenschutzerklärung', 'privacy policy')
OPTIONAL = ('newsletter', 'werbung', 'werbeangebote', 'marketing', 'angebote per',
            'partnerangebote', 'produktempfehlungen')


def captions(tree):
    return ' '.join(normal(node.get(key) or '') for node in own_nodes(tree)
                    if not node.get('class', '').endswith('EditText')
                    for key in ('text', 'content-desc'))


def blocked(tree):
    text = captions(tree)
    return any(word in text for word in (
        'captcha', 'ich bin ein mensch', 'ich bin kein roboter', 'verify you are human',
        'slide to verify', 'telefonnummer bestätigen', 'sms-code', 'sms code',
        'zahlungspflichtig', 'kostenpflichtig bestellen', 'kreditkartennummer',
        'keine internetverbindung', 'no internet', 'ungültig', 'nicht verfügbar',
        'bereits vergeben', 'stimmt nicht überein', 'fehler aufgetreten'))


def _target(tree, labels):
    nodes = own_nodes(tree)
    allowed = set(nodes)
    parents = {child: parent for parent in tree.iter() for child in parent}
    found = {}
    for node in nodes:
        if node.get('class', '').endswith('EditText'):
            continue
        names = {normal(node.get(key) or '') for key in ('text', 'content-desc')}
        names &= labels
        if not names:
            continue
        current = node
        while current in allowed:
            if current.get('clickable') == 'true' or current.get('class') == 'android.widget.Button':
                found.setdefault(current, set()).update(names)
                break
            current = parents.get(current)
    if len(found) != 1:
        return None
    node, names = next(iter(found.items()))
    return node, tuple(sorted(names))


def _checks(tree):
    nodes = own_nodes(tree)
    allowed = set(nodes)
    parents = {child: parent for parent in tree.iter() for child in parent}
    checks = [node for node in nodes if node.get('class') == 'android.widget.CheckBox'
              or (node.get('checkable') == 'true' and node.get('class') != 'android.widget.RadioButton')]
    result = []
    for node in checks:
        label = ' '.join(normal(node.get(key) or '') for key in ('text', 'content-desc'))
        if not label.strip():
            parent = parents.get(node)
            if parent is not None:
                children = [child for child in parent.iter('node') if child in allowed]
                if sum(child in checks for child in children) == 1 and len(children) <= 8:
                    label = ' '.join(normal(child.get(key) or '') for child in children
                                     if not child.get('class', '').endswith('EditText')
                                     for key in ('text', 'content-desc'))
        required = any(word in label for word in REQUIRED)
        optional = any(word in label for word in OPTIONAL)
        if normal(label) in {'passwort anzeigen', 'kennwort anzeigen', 'show password', 'hide password', 'passwort verbergen'}:
            continue  # a visibility toggle is neither a consent nor a required field
        if required == optional or node.get('checked') not in ('true', 'false'):
            return None
        result.append((node, required, label))
    return result


def _ready(tree, ctx):
    """Every visible edit and offered choice must agree with prepared data."""
    nodes = own_nodes(tree)
    recognised = fields(tree)
    edits = {node for node in nodes if node.get('class', '').endswith('EditText')}
    if any(node.get('class') == 'android.widget.NumberPicker' for node in nodes) or not selections_match(tree, ctx):
        return False
    checks = _checks(tree)
    if checks is None or any((node.get('checked') == 'true') != desired for node, desired, _label in checks):
        return False
    if not edits <= set(recognised.values()):
        return False
    data = values(ctx)
    ledger = getattr(ctx, '_webde_filled_passwords', set())
    for key, node in recognised.items():
        text = node.get('text') or ''
        expected = str(data.get(key) or '')
        if key.startswith('passwort'):
            if key not in ledger or not getattr(ctx, 'password_filled', False) or getattr(ctx, 'android_credentials_uncertain', False):
                return False
        elif not expected or not text or (node.get('hint') and text == node.get('hint')):
            return False
        elif key in ('tag', 'monat', 'jahr') and text.isdecimal() and expected.isdecimal():
            if int(text) != int(expected):
                return False
        elif text != expected:
            return False
    groups = _choice_groups(tree)
    radios = {node for node in nodes if node.get('class') == 'android.widget.RadioButton'}
    if not radios <= {node for group in groups.values() for node in group}:
        return False
    options = {'männlich': {'männlich', 'male'}, 'weiblich': {'weiblich', 'female'},
               'divers': {'divers', 'diverse'}, 'herr': {'herr'}, 'frau': {'frau'},
               'keine angabe': {'keine angabe', 'keine angabe machen', 'prefer not to say'}}
    for key, group in groups.items():
        checked = [node for node in group if node.get('checked') == 'true']
        expected = options.get(normal(ctx.registrierungsdaten.get(key) or ''), set())
        if len(checked) != 1 or normal(checked[0].get('text') or checked[0].get('content-desc') or '') not in expected:
            return False
    return True


def plan(tree, ctx):
    """Return one known action and a value-free identity, or no action."""
    nodes = own_nodes(tree)
    text = captions(tree)
    if not any(word in text for word in ('captcha', 'ich bin ein mensch', 'ich bin kein roboter', 'verify you are human')):
        offer = android_plan(tree, nodes, stage=form_signature(tree))
        if offer is not None:
            return offer
    if blocked(tree):
        return None
    if android_purchase_visible(nodes):
        return None
    edits = [node for node in nodes if node.get('class', '').endswith('EditText')]
    signature = form_signature(tree)
    if (not edits and any(word in text for word in ('registrierung erfolgreich', 'postfach erfolgreich', 'konto wurde erstellt', 'registration successful'))):
        target = _target(tree, {'zum postfach', 'zum posteingang', 'postfach öffnen', 'open inbox'})
        if target:
            return ('open_inbox',), target[0]
    if registration_form(tree):
        if signature is None:
            return None
        checks = _checks(tree)
        if checks is None:
            return None
        for index, (node, desired, _label) in enumerate(checks):
            if (node.get('checked') == 'true') != desired:
                return ('checkbox', signature, index, desired), node
        if not _ready(tree, ctx):
            return None
        target = _target(tree, NEXT | CREATE)
        if target:
            node, labels = target
            # Alias checking and choosing the now available alias are two
            # distinct observed steps, never an unconditional second submit.
            available = bool(re.search(r'(?:adresse|wunschname|alias)[^.]{0,70}(?:ist verfügbar|is available)', text))
            return ('submit', signature, labels, available), node
        return None
    if android_pending(tree, nodes):
        return None
    if edits or any(node.get('class') == 'android.webkit.WebView' for node in nodes) and not any(word in text for word in ('cookie', 'tracking')):
        return None
    if any(word in text for word in ('cookie', 'tracking')):
        labels = ({'alle akzeptieren', 'alles akzeptieren', 'akzeptieren und weiter',
                   'zustimmen und weiter', 'accept all', 'accept all cookies'}
                  if getattr(ctx, 'accept_privacy', False) is True else
                  {'alle ablehnen', 'alles ablehnen', 'nur notwendige cookies', 'nur erforderliche cookies', 'reject all'})
        target = _target(tree, labels)
        if target:
            return ('privacy', target[1]), target[0]
        return None
    if any(node.get('resource-id') == PACKAGE + ':id/drawer_layout' for node in nodes):
        if any(node.get('resource-id') == PACKAGE + ':id/navigation_drawer_container' for node in nodes):
            return None  # leave an already open account drawer available for proof
        target = _target(tree, {'navigationsmenü öffnen', 'navigationsleiste öffnen', 'menü öffnen', 'open navigation drawer'})
        if target:
            return ('open_account_menu',), target[0]
    elif not any(node.get('class') == 'android.webkit.WebView' for node in nodes):
        target = _target(tree, CREATE)
        if target:
            return ('registration_entry',), target[0]
    if any(word in text for word in ('benachrichtigung', 'notification', 'einführung', 'tutorial')):
        target = _target(tree, {'nicht jetzt', 'später', 'überspringen', 'not now', 'skip'})
        if target:
            return ('skip_optional', target[1]), target[0]
    return None


def can_advance(tree, ctx, attempted):
    if len(attempted) >= 40:
        return False
    action = plan(tree, ctx)
    return action is not None and action[0] not in attempted


def advance(adb, ctx, attempted):
    check_cancelled(ctx.stop)
    action = plan(adb.baum(timeout=5), ctx)
    if action is None or action[0] in attempted:
        return False
    key, node = action
    if len(attempted) >= 40:
        return False
    def target(tree):
        current = plan(tree, ctx)
        return current[1] if current is not None and current[0] == key else None
    before = tap(adb, target, own_nodes, stop=ctx.stop, before_dispatch=lambda: attempted.add(key))
    check_cancelled(ctx.stop)
    progressed = changed(adb, before, own_nodes, stop=ctx.stop)
    if not progressed:
        ctx.melden('WEB.DE: Die Antwort auf den letzten Schritt ist noch nicht eindeutig sichtbar. Es wird nicht erneut geklickt.')
    if key[0] == 'decline_offer':
        ctx.melden('Zusatzangebot: Die Schaltfläche zum Ablehnen wurde gewählt. Der kostenlose Weg wird fortgesetzt, sobald das Angebot geschlossen ist.')
    elif key[0] == 'submit':
        ctx.melden('WEB.DE: „Weiter“ wurde gewählt. Der nächste Schritt wird geprüft.' if set(key[2]) & NEXT else
                   'WEB.DE: Die kostenlose Registrierung wurde gewählt. Die Antwort wird abgewartet.')
    elif key[0] == 'checkbox':
        ctx.melden('WEB.DE: Die Auswahl einer erforderlichen Bedingung wird geprüft.' if key[-1] else
                   'WEB.DE: Die Abwahl einer optionalen Zusatzleistung wird geprüft.')
    return True
