"""Use labelled numeric birthday fields, including identified native spinners."""
from .interaction import tap, snapshot
from .tiktok_fields import click, manual_gate, normal, own_nodes
from ..operation import check_cancelled

PARTS = {'day': {'tag', 'day', 'dd', 'tt'},
         'month': {'monat', 'month', 'mm'},
         'year': {'jahr', 'year', 'yyyy', 'jjjj'}}


def _fields(tree):
    nodes = own_nodes(tree)
    if manual_gate(tree):
        return None
    allowed = set(nodes)
    parents = {child: parent for parent in tree.iter() for child in parent}
    edits = [node for node in nodes if node.get('class', '').endswith('EditText')]
    found = {}
    for node in edits:
        if node.get('password') == 'true':
            return None
        labels = {normal(node.get(key) or '') for key in ('hint', 'content-desc')}
        current = node
        while current in allowed:
            resource = (current.get('resource-id') or '').rpartition('/')[2].casefold()
            labels.add(resource)
            if current is not node and current.get('class') != 'android.widget.NumberPicker':
                break
            current = parents.get(current)
        parent = parents.get(node)
        if parent is not None:
            children = [child for child in parent.iter('node') if child in allowed]
            if len(children) <= 6 and sum(child in edits for child in children) == 1:
                labels.update(normal(child.get(key) or '') for child in children if child is not node
                              for key in ('text', 'content-desc'))
        matches = [part for part, names in PARTS.items() if labels & names]
        if len(matches) != 1 or matches[0] in found:
            return None
        found[matches[0]] = node
    return found if len(edits) == 3 and set(found) == set(PARTS) else None


def _value(node):
    text = node.get('text') or ''
    if node.get('hint') and text == node.get('hint'):
        return ''
    return text


def enter(adb, born, *, stop=None):
    """One bounded attempt; unknown wheels need a supported selector first."""
    targets = {'year': born.year, 'month': born.month, 'day': born.day}
    initial = _fields(snapshot(adb, stop=stop))
    if initial is None:
        return False
    if any(_value(node) and (not _value(node).isascii() or not _value(node).isdecimal())
           for node in initial.values()):
        return False
    # Set the year and month before the day so a leap day is not clamped by
    # the picker while its other values still describe a different date.
    for part, expected in targets.items():
        check_cancelled(stop)
        fields = _fields(snapshot(adb, stop=stop))
        if fields is None:
            return False
        node = fields[part]
        text = _value(node)
        if text.isdecimal() and int(text) == expected:
            continue
        if text and (not text.isascii() or not text.isdecimal() or len(text) > 4):
            return False
        tap(adb, lambda tree: (_fields(tree) or {}).get(part), own_nodes, stop=stop)
        current = _fields(snapshot(adb, stop=stop))
        if current is None or current[part].get('focused') != 'true' or _value(current[part]) != text:
            return False
        if text:
            adb.taste('123')  # KEYCODE_MOVE_END
            for remaining in range(len(text) - 1, -1, -1):
                check_cancelled(stop)
                adb.taste('67')
                current = _fields(snapshot(adb, stop=stop))
                if (current is None or current[part].get('focused') != 'true'
                        or _value(current[part]) != text[:remaining]):
                    return False
            cleared = _fields(snapshot(adb, stop=stop))
            if cleared is None or _value(cleared[part]) or cleared[part].get('focused') != 'true':
                return False
        check_cancelled(stop)
        adb.tippen(str(expected))
        current = _fields(snapshot(adb, stop=stop))
        if current is None or not _value(current[part]).isdecimal() or int(_value(current[part])) != expected:
            return False
    # Commit the focused spinner through a fresh focus change, then read all
    # parts again. Typing into a spinner alone need not commit its model value.
    current = _fields(snapshot(adb, stop=stop))
    if current is None:
        return False
    check_cancelled(stop)
    tap(adb, lambda tree: (_fields(tree) or {}).get('year'), own_nodes, stop=stop)
    current = _fields(snapshot(adb, stop=stop))
    if current is None or any(not _value(current[part]).isdecimal() or int(_value(current[part])) != value
                              for part, value in targets.items()):
        return False
    return click(adb, ('Weiter', 'Next', 'Continue'), stop=stop)
