"""Read actual TikTok input controls before entering stored credentials."""
import re
from .adb import GRENZE
from ..operation import check_cancelled
from .interaction import NotReady, changed, tap

PACKAGE = 'com.zhiliaoapp.musically'
LABELS = {
    'email': {'e-mail', 'email', 'e-mail-adresse', 'email address', 'enter email',
              'e-mail-adresse eingeben', 'deine e-mail-adresse', 'your email address'},
    'password': {'passwort', 'password', 'passwort eingeben', 'enter password',
                 'passwort erstellen', 'create password', 'create a password'},
    'code': {'code', 'verification code', 'bestätigungscode', 'verifizierungscode',
             'enter code', 'code eingeben', 'enter 6-digit code', '6-stelligen code eingeben'},
}


class FieldNotReady(RuntimeError):
    """An input is unknown or occupied; stop automatic form input."""


def normal(value):
    return re.sub(r'\s+', ' ', value.strip().casefold().rstrip(':* '))


def own_nodes(tree):
    parents = {child: parent for parent in tree.iter() for child in parent}
    result = []
    for node in tree.iter('node'):
        bounds = GRENZE.fullmatch(node.get('bounds') or '')
        if node.get('package') != PACKAGE or bounds is None:
            continue
        x1, y1, x2, y2 = map(int, bounds.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        current = node
        while current is not None:
            if (current.get('visible-to-user') == 'false' or current.get('enabled') == 'false'
                    or current.get('package') not in (None, '', PACKAGE)):
                break
            current = parents.get(current)
        else:
            result.append(node)
    return result


def code_channel(tree, email=None):
    captions = ' '.join(normal(node.get(key) or '') for node in own_nodes(tree)
                        if not node.get('class', '').endswith('EditText') and node.get('password') != 'true'
                        for key in ('text', 'content-desc'))
    # Explicit phone evidence has priority over an email option in the footer.
    code_prompt = bool(re.search(r'enter[^.]{0,50}code|code[^.]{0,30}eingeben|gib[^.]{0,50}code', captions))
    if (any(word in captions for word in ('request phone call', 'telefonnummer bestätigen',
                                         'verify phone number', 'verify your phone', 'code per anruf'))
            or (code_prompt and 'sms' in captions)
            or re.search(r'(?:sent to|gesendet an|gesendet wurde an)\s*\+?[\d*•][\d\s*•()-]{4,}', captions)):
        return 'phone'
    addresses = set(re.findall(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}', captions))
    if email and addresses:
        return 'email' if addresses == {email.casefold()} else 'unknown'
    if any(word in captions for word in ('e-mail', 'email')) and any(word in captions for word in ('code', 'bestätig', 'verifizier')):
        return 'email'
    return 'unknown'


def manual_gate(tree):
    return any(any(word in normal(node.get(key) or '') for word in
                   ('captcha', 'slide to verify', 'fit the puzzle', 'schieberegler',
                    'verify you are human', 'ich bin ein mensch', 'schieben', 'wischen'))
               for node in own_nodes(tree) if not node.get('class', '').endswith('EditText')
               for key in ('text', 'content-desc'))


def _labels(tree, node, nodes):
    parents = {child: parent for parent in tree.iter() for child in parent}
    allowed = set(nodes)
    labels = {normal(node.get(key) or '') for key in ('hint', 'text', 'content-desc')}
    resource = (node.get('resource-id') or '').rpartition('/')[2]
    labels.add(normal(resource.replace('_', ' ')))
    parent = parents.get(node)
    if parent is not None:
        siblings = list(parent)
        edits = [child for child in parent.iter('node') if child in allowed and child.get('class', '').endswith('EditText')]
        if len(edits) == 1 and len(list(parent.iter('node'))) <= 8:
            labels.update(normal(child.get(key) or '') for child in parent.iter('node')
                          if child in allowed and child is not node for key in ('text', 'content-desc'))
        else:
            index = siblings.index(node)
            for neighbour in siblings[max(0, index - 1):index] + siblings[index + 1:index + 2]:
                if neighbour in allowed and neighbour.get('class') in ('android.widget.TextView', 'android.view.View'):
                    labels.update(normal(neighbour.get(key) or '') for key in ('text', 'content-desc'))
    return labels


def select(tree, kind, *, email=None):
    if kind not in LABELS:
        raise ValueError('Unbekannte TikTok-Feldart.')
    nodes = own_nodes(tree)
    if manual_gate(tree):
        raise FieldNotReady('TikTok benötigt eine manuelle Sicherheitsbestätigung.')
    edits = [node for node in nodes if node.get('class', '').endswith('EditText')]
    headings = {normal(node.get(key) or '') for node in nodes if node not in edits
                for key in ('text', 'content-desc')}
    if kind != 'code' and headings & LABELS['code']:
        raise FieldNotReady('Eine Codeabfrage ist kein E-Mail- oder Kennwortfeld.')
    if kind == 'code':
        if code_channel(tree, email) != 'email':
            raise FieldNotReady('Die Abfrage ist nicht eindeutig als E-Mail-Code bestätigt.')
        candidates = [node for node in edits if node.get('password') != 'true']
        headings = {normal(node.get(key) or '') for node in nodes if node not in edits
                    for key in ('text', 'content-desc')}
        if candidates and (headings & LABELS['code'] or any(_labels(tree, node, nodes) & LABELS['code'] for node in candidates)):
            parents = {child: parent for parent in tree.iter() for child in parent}
            rectangles = [tuple(map(int, GRENZE.fullmatch(node.get('bounds')).groups())) for node in candidates]
            if len(candidates) == 1:
                return candidates
            if (len({parents.get(node) for node in candidates}) == 1
                    and len({(box[1], box[3]) for box in rectangles}) == 1
                    and len({box[0] for box in rectangles}) == len(candidates)):
                return [node for _, node in sorted(zip(rectangles, candidates), key=lambda pair: pair[0][0])]
        raise FieldNotReady('Code-Eingabefeld nicht eindeutig erkannt.')
    if kind == 'password':
        headings = {normal(node.get(key) or '') for node in nodes if node not in edits
                    for key in ('text', 'content-desc')}
        candidates = [node for node in edits if node.get('password') == 'true'
                      and (_labels(tree, node, nodes) & LABELS['password'] or headings & LABELS['password'])]
    else:
        candidates = [node for node in edits if node.get('password') != 'true'
                      and _labels(tree, node, nodes) & LABELS['email']
                      and not (_labels(tree, node, nodes) & {'telefonnummer', 'phone number', 'mobile number', 'phone'})]
    if len(candidates) != 1:
        raise FieldNotReady('TikTok-Eingabefeld fehlt oder ist nicht eindeutig.')
    return candidates


def _password_readback(node, expected):
    """A visible mask confirms length only; it never proves the whole secret."""
    text = node.get('text') or ''
    if node.get('password') != 'true' or not text or (node.get('hint') and text == node.get('hint')):
        return False
    if text == expected:
        return True
    if len(text) != len(expected):
        return False
    masks = {'•', '●', '*', '·', '∙', '○'}
    return set(text) <= masks or (set(text[:-1]) <= masks and text[-1:] == expected[-1:])


def credentials_ready(tree, ctx):
    """Recheck the whole visible credential surface immediately before submit."""
    from ..offer_policy import android_pending
    nodes = own_nodes(tree)
    captions = ' '.join(normal(node.get(key) or '') for node in nodes
                        if not node.get('class', '').endswith('EditText') for key in ('text', 'content-desc'))
    if (manual_gate(tree) or code_channel(tree, ctx.email) == 'phone' or android_pending(tree, nodes)
            or any(word in captions for word in ('ungültige e-mail', 'e-mail ist ungültig', 'invalid email',
                'invalid password', 'passwort ist zu kurz', 'password is too short', 'bereits registriert',
                'already registered', 'zu viele versuche', 'too many attempts', 'no internet', 'keine internetverbindung'))):
        raise FieldNotReady('Die Zugangseingabe ist noch nicht bestätigt oder die App meldet ein Problem. Bitte die Geräteansicht prüfen.')
    edits = {node for node in nodes if node.get('class', '').endswith('EditText')}
    recognised = set()
    entered = getattr(ctx, '_tiktok_verified_credentials', set())
    for kind in ('email', 'password'):
        try:
            controls = select(tree, kind, email=ctx.email)
        except FieldNotReady:
            continue
        recognised.update(controls)
        node = controls[0]
        if kind not in entered:
            raise FieldNotReady('Ein sichtbares Zugangsfeld wurde noch nicht bestätigt. Kein Weiter-Klick ausgeführt.')
        if kind == 'email':
            matches = node.get('text') == ctx.email
        else:
            matches = not getattr(ctx, 'android_credentials_uncertain', False) and _password_readback(node, ctx.password)
        if not matches:
            raise FieldNotReady('Die sichtbaren Zugangsdaten stimmen noch nicht mit der vorbereiteten Eingabe überein. Kein Weiter-Klick ausgeführt.')
    if not edits or not edits <= recognised:
        raise FieldNotReady('Das Formular enthält eine unbekannte oder fehlende Eingabe. Kein Weiter-Klick ausgeführt.')
    return True


def fill(adb, kind, value, *, email=None, stop=None):
    if not isinstance(value, str) or not value or any(not 32 <= ord(char) <= 126 for char in value):
        raise FieldNotReady('Der gespeicherte Wert kann nicht zuverlässig übergeben werden.')
    check_cancelled(stop)
    nodes = select(adb.baum(timeout=5), kind, email=email)
    if kind == 'code' and len(nodes) > 1:
        if not value.isdecimal() or len(nodes) != len(value) or not 4 <= len(value) <= 8:
            raise FieldNotReady('Die Codefelder passen nicht zum E-Mail-Code.')
        order = range(len(nodes))
        values = list(value)
    else:
        if len(nodes) != 1:
            raise FieldNotReady('Mehrere unklare Eingabefelder.')
        order, values = [0], [value]
    for index, part in zip(order, values):
        check_cancelled(stop)
        current = select(adb.baum(timeout=5), kind, email=email)
        if len(current) != len(nodes):
            raise FieldNotReady('Die Formularansicht hat sich geändert.')
        node = current[index]
        existing = node.get('text') or ''
        if kind != 'password' and existing == part:
            continue
        if existing and not (node.get('hint') and existing == node.get('hint')):
            raise FieldNotReady('Das Eingabefeld enthält bereits einen Wert; nichts überschrieben.')
        identity = node.get('resource-id') or ''
        def target(tree):
            controls = select(tree, kind, email=email)
            return controls[index] if len(controls) == len(nodes) else None
        try:
            tap(adb, target, own_nodes, stop=stop)
        except NotReady as error:
            raise FieldNotReady('Das Eingabefeld ist verdeckt oder noch nicht bereit. Bitte die Geräteansicht prüfen.') from error
        focused = select(adb.baum(timeout=5), kind, email=email)
        if (len(focused) != len(nodes) or focused[index].get('focused') != 'true'
                or (identity and focused[index].get('resource-id') != identity)):
            raise FieldNotReady('Feldfokus nicht bestätigt; keine Texteingabe.')
        text = focused[index].get('text') or ''
        if text and not (focused[index].get('hint') and text == focused[index].get('hint')):
            raise FieldNotReady('Feld wurde zwischenzeitlich ausgefüllt; nichts ergänzt.')
        check_cancelled(stop)
        adb.tippen(part)
        check_cancelled(stop)
        observed = select(adb.baum(timeout=5), kind, email=email)
        if len(observed) != len(nodes) or (kind != 'password' and observed[index].get('text') != part):
            raise FieldNotReady('Die Texteingabe konnte nicht bestätigt werden; kein erneuter Versuch.')
        if kind == 'password' and not _password_readback(observed[index], part):
            raise FieldNotReady('Die vollständige Kennworteingabe ist nicht lesbar bestätigt. Bitte die Geräteansicht prüfen; kein erneuter Eingabeversuch.')
    return True


def _click_target(tree, labels):
    nodes = own_nodes(tree)
    if manual_gate(tree):
        return None
    allowed = set(nodes)
    parents = {child: parent for parent in tree.iter() for child in parent}
    labels = {normal(label) for label in labels}
    matches = set()
    for node in nodes:
        if node.get('class', '').endswith('EditText') or not any(normal(node.get(key) or '') in labels for key in ('text', 'content-desc')):
            continue
        current = node
        while current in allowed:
            if current.get('clickable') == 'true' or current.get('class') == 'android.widget.Button':
                matches.add(current)
                break
            current = parents.get(current)
    if len(matches) != 1:
        return None
    return next(iter(matches))


def click(adb, labels, *, stop=None, guard=None):
    check_cancelled(stop)
    def target(tree):
        if guard is not None and guard(tree) is not True:
            return None
        return _click_target(tree, labels)
    try:
        before = tap(adb, target, own_nodes, stop=stop)
    except NotReady:
        return False
    changed(adb, before, own_nodes, stop=stop)
    return True
