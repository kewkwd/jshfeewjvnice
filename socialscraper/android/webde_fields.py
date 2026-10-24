"""Recognise labelled WEB.DE fields; never infer targets from screen fractions."""
import datetime as dt
import re
from .adb import GRENZE
from .preview import InputRejected
from ..operation import check_cancelled
from .interaction import tap

PACKAGE = 'de.web.mobile.android.mail'
ALIASES = {
    'vorname': ('vorname', 'first name', 'firstname'),
    'nachname': ('nachname', 'last name', 'lastname', 'surname'),
    'tag': ('tag', 'day', 'birthday', 'birth day'),
    'monat': ('monat', 'month', 'birthmonth', 'birth month'),
    'jahr': ('jahr', 'year', 'birthyear', 'birth year'),
    'geburtsdatum': ('geburtsdatum', 'date of birth', 'birthdate'),
    'alias': ('wunschadresse', 'wunsch-adresse', 'wunschname', 'benutzername', 'alias', 'username'),
    'email': ('e-mail-adresse', 'e-mail adresse', 'e-mail', 'email address', 'email'),
    'plz': ('postleitzahl', 'plz', 'zip', 'postal code', 'zipcode'),
    'ort': ('ort', 'wohnort', 'city'),
    'strasse': ('straße', 'strasse', 'street'),
    'hausnummer': ('hausnummer', 'house number', 'housenumber'),
    'strasse_hausnummer': ('straße und hausnummer', 'street and house number'),
    'passwort': ('passwort', 'password', 'neues passwort', 'new password'),
    'passwort_wiederholen': ('passwort wiederholen', 'passwort bestätigen', 'repeat password',
                              'confirm password', 'password confirmation', 'passwordagain'),
}


def normal(value):
    return re.sub(r'\s+', ' ', value.strip().casefold().rstrip(':* '))


def own_nodes(tree):
    parents = {child: parent for parent in tree.iter() for child in parent}
    result = []
    for node in tree.iter('node'):
        if node.get('package') != PACKAGE:
            continue
        rectangle = GRENZE.fullmatch(node.get('bounds') or '')
        if rectangle is None:
            continue
        x1, y1, x2, y2 = map(int, rectangle.groups())
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


def registration_form(tree):
    nodes = own_nodes(tree)
    if not any(node.get('class') == 'android.webkit.WebView' for node in nodes):
        return False
    labels = {normal(node.get(key) or '') for node in nodes for key in ('text', 'content-desc')
              if not node.get('class', '').endswith('EditText') and node.get('password') != 'true'}
    progress = any(node.get('class') == 'android.widget.ProgressBar' and
                   any(word in normal(node.get('text') or '') for word in ('registrierung', 'registration progress'))
                   for node in nodes)
    return progress or ('web.de freemail' in labels and bool(labels & {'registrierung', 'registration'}))


def fields(tree):
    if not registration_form(tree):
        return {}
    nodes = own_nodes(tree)
    allowed = set(nodes)
    parents = {child: parent for parent in tree.iter() for child in parent}
    found = {}
    for node in nodes:
        if not node.get('class', '').endswith('EditText') or GRENZE.fullmatch(node.get('bounds') or '') is None:
            continue
        labels = {normal(node.get(key) or '') for key in ('hint', 'content-desc', 'text')}
        rid = (node.get('resource-id') or '').rpartition('/')[2]
        labels.add(normal(rid))
        parent = parents.get(node)
        if parent is not None:
            edits = [child for child in parent.iter('node') if child in allowed and child.get('class', '').endswith('EditText')]
            if len(edits) == 1:
                labels.update(normal(child.get(key) or '') for child in parent.iter('node')
                              if child in allowed and child is not node
                              for key in ('text', 'content-desc'))
            else:
                siblings = list(parent)
                index = siblings.index(node)
                # The observed WebView exposes each label immediately after its
                # EditText. Do not borrow labels across another input field.
                if index + 1 < len(siblings):
                    label = siblings[index + 1]
                    if label in allowed and label.get('class') in ('android.view.View', 'android.widget.TextView'):
                        labels.update(normal(label.get(key) or '') for key in ('text', 'content-desc'))
        keys = {key for key, aliases in ALIASES.items() if labels.intersection(aliases)}
        if len(keys) != 1:
            continue
        key = next(iter(keys))
        if key.startswith('passwort') and node.get('password') != 'true':
            continue
        if not key.startswith('passwort') and node.get('password') == 'true':
            continue
        found.setdefault(key, []).append(node)
    return {key: matches[0] for key, matches in found.items() if len(matches) == 1}


def form_signature(tree):
    """Only form structure and progress labels, never entered personal values."""
    if not registration_form(tree):
        return None
    progress = set()
    for node in own_nodes(tree):
        label = normal(node.get('text') or '')
        if node.get('class') != 'android.widget.ProgressBar' or not any(word in label for word in ('registrierung', 'registration progress')):
            continue
        match = re.search(r'(\d+)\s*(?:of|von|/)\s*(\d+)', label)
        if match and 1 <= int(match[1]) <= int(match[2]) <= 20:
            progress.add((int(match[1]), int(match[2])))
    # Keyboard visibility or focus must not look like a new registration step.
    # Unknown layouts stay in the manual view until the account is confirmed.
    return tuple(sorted(progress)) or None


def values(ctx):
    data = dict(ctx.registrierungsdaten)
    born = dt.date.fromisoformat(data['geburtsdatum'])
    data.update(tag=str(born.day), monat=str(born.month), jahr=str(born.year),
                geburtsdatum=born.strftime('%d.%m.%Y'), alias=ctx.adresse.partition('@')[0],
                email=ctx.adresse, passwort=ctx.passwort, passwort_wiederholen=ctx.passwort,
                strasse_hausnummer=' '.join(filter(None, (data.get('strasse'), data.get('hausnummer')))))
    return data


def _choice_groups(tree):
    if not registration_form(tree):
        return {}
    nodes = own_nodes(tree)
    allowed = set(nodes)
    parents = {child: parent for parent in tree.iter() for child in parent}
    groups = {}
    for node in nodes:
        if node.get('class') != 'android.widget.RadioButton':
            continue
        parent = parents.get(node)
        while parent is not None:
            radios = [child for child in parent.iter('node') if child in allowed
                      and child.get('class') == 'android.widget.RadioButton']
            if len(radios) >= 2:
                groups[parent] = radios
                break
            parent = parents.get(parent)
    recognised = {}
    for radios in groups.values():
        labels = {normal(node.get('text') or node.get('content-desc') or '') for node in radios}
        if {'männlich', 'weiblich'} <= labels or {'male', 'female'} <= labels:
            key = 'geschlecht'
        elif {'herr', 'frau'} <= labels:
            key = 'anrede'
        else:
            continue
        recognised.setdefault(key, []).append(radios)
    return {key: entries[0] for key, entries in recognised.items() if len(entries) == 1}


def _fill_choices(adb, ctx):
    desired = {'männlich': ('männlich', 'male'), 'weiblich': ('weiblich', 'female'),
               'divers': ('divers', 'diverse'), 'keine angabe': ('keine angabe', 'keine angabe machen', 'prefer not to say'),
               'herr': ('herr',), 'frau': ('frau',)}
    done = 0
    stage = form_signature(adb.baum(timeout=5))
    for key in ('geschlecht', 'anrede'):
        check_cancelled(ctx.stop)
        radios = _choice_groups(adb.baum(timeout=5)).get(key, [])
        if not radios or any(node.get('checked') == 'true' for node in radios):
            continue
        options = desired.get(normal(ctx.registrierungsdaten.get(key) or ''), ())
        matches = [node for node in radios if normal(node.get('text') or node.get('content-desc') or '') in options]
        if len(matches) != 1:
            continue  # an unavailable choice is never replaced with another gender
        def choice(current):
            if form_signature(current) != stage:
                return None
            group = _choice_groups(current).get(key, [])
            selected = [node for node in group if normal(node.get('text') or node.get('content-desc') or '') in options]
            return selected[0] if len(selected) == 1 else None
        tap(adb, choice, own_nodes, stop=ctx.stop)
        check_cancelled(ctx.stop)
        checked = [node for node in _choice_groups(adb.baum(timeout=5)).get(key, []) if node.get('checked') == 'true']
        if len(checked) != 1 or normal(checked[0].get('text') or checked[0].get('content-desc') or '') not in options:
            raise InputRejected('Gespeicherte Auswahl wurde nicht bestätigt.')
        done += 1
    return done


def fill_known(adb, ctx):
    """Fill only empty recognised inputs, re-reading geometry after every field."""
    stop = getattr(ctx, 'stop', None)
    initial = adb.baum(timeout=5)
    stage = form_signature(initial)
    targets = fields(initial)
    data = values(ctx)
    ledger = getattr(ctx, '_webde_filled_passwords', set())
    ctx._webde_filled_passwords = ledger
    done = 0
    for key in targets:
        check_cancelled(stop)
        value = data.get(key)
        if not value or any(not 32 <= ord(char) <= 126 for char in value):
            continue  # ADB cannot reliably type arbitrary Unicode here.
        tree = adb.baum(timeout=5)
        if form_signature(tree) != stage:
            raise InputRejected('Die Formularseite hat sich während der Eingabe geändert.')
        current = fields(tree)
        node = current.get(key)
        if node is None:
            continue
        existing = node.get('text') or ''
        placeholder = bool(node.get('hint')) and existing == node.get('hint')
        if existing and not placeholder:
            if key.startswith('passwort') and key not in ledger:
                ctx.android_credentials_uncertain = True
            continue  # preserve all already-entered values, including manual edits
        tap(adb, lambda tree: fields(tree).get(key) if form_signature(tree) == stage else None, own_nodes, stop=stop)
        check_cancelled(stop)
        focused = fields(adb.baum(timeout=5)).get(key)
        if focused is None or focused.get('focused') != 'true':
            raise InputRejected('Feldfokus nicht bestätigt; keine Texteingabe.')
        focused_text = focused.get('text') or ''
        if focused_text and not (focused.get('hint') and focused_text == focused.get('hint')):
            continue
        if key.startswith('passwort'):
            ctx.password_filled = False
        adb.tippen(value)
        check_cancelled(stop)
        updated = fields(adb.baum(timeout=5)).get(key)
        if updated is None:
            raise InputRejected('Feld nach der Eingabe nicht eindeutig.')
        if key.startswith('passwort'):
            if updated.get('password') != 'true':
                raise InputRejected('Kennwortfeld nach Eingabe nicht bestätigt.')
            ledger.add(key)
        else:
            observed = updated.get('text') or ''
            matches = observed == value
            if key in ('tag', 'monat', 'jahr') and observed.isdecimal():
                matches = int(observed) == int(value)
            if not matches:
                raise InputRejected('Eingetragener Wert nicht bestätigt.')
        done += 1
    password_keys = {key for key in fields(adb.baum(timeout=5)) if key.startswith('passwort')}
    if password_keys:
        ctx.password_filled = password_keys <= ledger and not getattr(ctx, 'android_credentials_uncertain', False)
    return done + _fill_choices(adb, ctx)
