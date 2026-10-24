"""Read and select labelled native dropdowns in the known WEB.DE form."""
import datetime as dt

from .interaction import NotReady, changed, rectangle, scroll, signature, snapshot, tap
from .webde_fields import normal, own_nodes, registration_form, form_signature
from ..operation import check_cancelled
from ..offer_policy import android_pending, android_purchase_visible

ALIASES = {'land': {'land', 'country', 'country of residence'},
           'geschlecht': {'geschlecht', 'gender'}, 'anrede': {'anrede', 'salutation', 'title'},
           'tag': {'tag', 'day', 'birthday', 'birth day'},
           'monat': {'monat', 'month', 'birthmonth', 'birth month'},
           'jahr': {'jahr', 'year', 'birthyear', 'birth year'}}
MONTHS = (('januar', 'january', 'jan'), ('februar', 'february', 'feb'), ('märz', 'march', 'mär', 'mar'),
          ('april', 'apr'), ('mai', 'may'), ('juni', 'june', 'jun'), ('juli', 'july', 'jul'),
          ('august', 'aug'), ('september', 'sep', 'sept'), ('oktober', 'october', 'okt', 'oct'),
          ('november', 'nov'), ('dezember', 'december', 'dez', 'dec'))


def selections(tree):
    if not registration_form(tree):
        return {}
    nodes = own_nodes(tree)
    allowed = set(nodes)
    parents = {child: parent for parent in tree.iter() for child in parent}
    found = {}
    for node in nodes:
        if node.get('class') != 'android.widget.Spinner':
            continue
        labels = {normal(node.get(key) or '') for key in ('hint', 'content-desc', 'text')}
        labels.add(normal((node.get('resource-id') or '').rpartition('/')[2].replace('_', ' ')))
        parent = parents.get(node)
        if parent is not None:
            children = [child for child in parent.iter('node') if child in allowed]
            if len(children) <= 10 and sum(child.get('class') == 'android.widget.Spinner' for child in children) == 1:
                labels.update(normal(child.get(key) or '') for child in children if child is not node
                              and not child.get('class', '').endswith('EditText') for key in ('text', 'content-desc'))
        keys = {key for key, names in ALIASES.items() if labels & names}
        if len(keys) == 1:
            found.setdefault(next(iter(keys)), []).append(node)
    return {key: group[0] for key, group in found.items() if len(group) == 1}


def expected(ctx, key):
    data = ctx.registrierungsdaten
    if key in ('tag', 'monat', 'jahr'):
        born = dt.date.fromisoformat(data['geburtsdatum'])
        number = {'tag': born.day, 'monat': born.month, 'jahr': born.year}[key]
        result = {str(number), str(number).zfill(2)}
        if key == 'monat':
            result.update(MONTHS[number - 1])
        return result
    if key == 'land':
        return {'DE': {'de', 'deutschland', 'germany'}, 'AT': {'at', 'österreich', 'austria'},
                'CH': {'ch', 'schweiz', 'switzerland'}}.get(data.get('land'), set())
    value = normal(data.get(key) or '')
    return {'männlich': {'männlich', 'male'}, 'weiblich': {'weiblich', 'female'},
            'divers': {'divers', 'diverse'}, 'keine angabe': {'keine angabe', 'keine angabe machen', 'prefer not to say'},
            'herr': {'herr', 'mr', 'mr.'}, 'frau': {'frau', 'ms', 'ms.', 'mrs', 'mrs.'}}.get(value, set())


def value(node):
    direct = normal(node.get('text') or '')
    placeholders = set().union(*ALIASES.values())
    if direct and direct not in placeholders:
        return direct.rstrip('.')
    texts = {normal(child.get('text') or '').rstrip('.') for child in node.iter('node')
             if child.get('visible-to-user') != 'false' and child.get('class') in ('android.widget.TextView', 'android.widget.CheckedTextView')}
    texts -= placeholders | {''}
    return next(iter(texts)) if len(texts) == 1 else ''


def matches(tree, ctx):
    selected = selections(tree)
    spinners = {node for node in own_nodes(tree) if node.get('class') == 'android.widget.Spinner'}
    return spinners <= set(selected.values()) and all(value(node) in expected(ctx, key) for key, node in selected.items())


def _list(tree):
    nodes = own_nodes(tree)
    candidates = [node for node in nodes if node.get('class', '').endswith(('ListView', 'RecyclerView'))]
    return candidates[0] if len(candidates) == 1 else None


def _option(tree, options):
    container = _list(tree)
    if container is None:
        return None
    allowed = set(own_nodes(tree))
    candidates = [node for node in container.iter('node') if node in allowed and
                  normal(node.get('text') or node.get('content-desc') or '').rstrip('.') in options
                  and node.get('class') in ('android.widget.TextView', 'android.widget.CheckedTextView')]
    return candidates[0] if len(candidates) == 1 else None


def fill(adb, ctx):
    initial = snapshot(adb, stop=ctx.stop)
    stage = form_signature(initial)
    if stage is None or android_pending(initial, own_nodes(initial)) or android_purchase_visible(own_nodes(initial)):
        return 0
    attempted = getattr(ctx, '_webde_selection_attempts', set())
    ctx._webde_selection_attempts = attempted
    count = 0
    order = {'jahr': 0, 'monat': 1, 'tag': 2}
    for key in sorted(selections(initial), key=lambda key: order.get(key, 3)):
        check_cancelled(ctx.stop)
        tree = snapshot(adb, stop=ctx.stop)
        if form_signature(tree) != stage:
            raise NotReady('Die Formularseite hat sich während der Auswahl geändert.')
        node = selections(tree).get(key)
        options = expected(ctx, key)
        if node is None or not options or value(node) in options:
            continue
        identity = (stage, key)
        if identity in attempted:
            continue
        before = tap(adb, lambda current: selections(current).get(key) if form_signature(current) == stage else None,
                     own_nodes, stop=ctx.stop, before_dispatch=lambda: attempted.add(identity))
        changed(adb, before, own_nodes, stop=ctx.stop)
        seen = set()
        upward, reversed_once = False, False
        for _ in range(7):
            tree = snapshot(adb, stop=ctx.stop)
            target = _option(tree, options)
            if target is not None:
                before = tap(adb, lambda current: _option(current, options), own_nodes, stop=ctx.stop)
                changed(adb, before, own_nodes, stop=ctx.stop)
                current = snapshot(adb, stop=ctx.stop)
                node = selections(current).get(key)
                if form_signature(current) != stage or node is None or value(node) not in options:
                    raise NotReady('Die gespeicherte Auswahl wurde noch nicht bestätigt. Keine zweite Auswahl versucht.')
                count += 1
                ctx.melden('WEB.DE: Die gespeicherte Angabe wurde aus einer Auswahlliste übernommen.')
                break
            mark = signature(tree, own_nodes)
            if mark in seen or _list(tree) is None:
                raise NotReady('Die angebotenen Auswahlmöglichkeiten konnten nicht eindeutig gelesen werden.')
            seen.add(mark)
            container = _list(tree)
            visible_numbers = [int(node.get('text')) for node in container.iter('node') if (node.get('text') or '').isdecimal()]
            desired_numbers = [int(option) for option in options if option.isdecimal()]
            if visible_numbers and desired_numbers and not reversed_once:
                upward = min(desired_numbers) < min(visible_numbers)
            if not scroll(adb, _list, own_nodes, stop=ctx.stop, up=upward):
                if reversed_once:
                    raise NotReady('Die benötigte Auswahl ist in dieser Liste nicht erkennbar.')
                upward, reversed_once = not upward, True
                seen.clear()
        else:
            raise NotReady('Die Auswahlliste wurde nicht weiter durchsucht; bitte den gespeicherten Wert prüfen.')
    return count


def keyboard_visible(tree):
    return any('inputmethod' in (node.get('package') or '').casefold()
               and node.get('visible-to-user') != 'false' and rectangle(node) is not None for node in tree.iter('node'))


def reveal(adb, ctx):
    """A bounded scroll inside the verified registration area, never a retry."""
    from .webde_steps import _ready, blocked
    tree = snapshot(adb, stop=ctx.stop)
    stage = form_signature(tree)
    if (stage is None or blocked(tree) or android_pending(tree, own_nodes(tree))
            or android_purchase_visible(own_nodes(tree)) or not _ready(tree, ctx)):
        return False
    limits = getattr(ctx, '_webde_form_scrolls', {})
    ctx._webde_form_scrolls = limits
    if limits.get(stage, 0) >= 4:
        return False
    if keyboard_visible(tree):
        # Escape dismisses the observed input surface. Never issue Back and
        # never continue a changed form as though it were the same page.
        check_cancelled(ctx.stop)
        adb.taste('111')
        changed(adb, tree, own_nodes, stop=ctx.stop, timeout=3)
        current = snapshot(adb, stop=ctx.stop)
        if keyboard_visible(current) or form_signature(current) != stage:
            limits[stage] = 4
            return False
    def area(current):
        if (form_signature(current) != stage or blocked(current) or android_pending(current, own_nodes(current))
                or android_purchase_visible(own_nodes(current))):
            return None
        nodes = own_nodes(current)
        roots = [node for node in nodes if node.get('class') == 'android.webkit.WebView']
        allowed = set(nodes)
        found = [node for root in roots for node in root.iter('node') if node in allowed and node.get('scrollable') == 'true']
        return found[0] if len(found) == 1 else None
    current = snapshot(adb, stop=ctx.stop)
    if area(current) is None:
        return False
    moved = scroll(adb, area, own_nodes, stop=ctx.stop, before_dispatch=lambda: limits.__setitem__(stage, limits.get(stage, 0) + 1))
    if not moved:
        limits[stage] = 4
    else:
        ctx.melden('WEB.DE: Der nächste Abschnitt des Formulars wird sichtbar gemacht.')
    return moved
