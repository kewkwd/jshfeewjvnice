"""Bounded readiness checks and fresh hit positions for ordinary Android input."""
import hashlib
import time

from .adb import AdbError, GRENZE
from ..net.errors import FinalError
from ..operation import check_cancelled, positive_timeout, wait


class NotReady(AdbError):
    pass


def rectangle(node):
    match = GRENZE.fullmatch(node.get('bounds') or '')
    if match is None:
        return None
    box = tuple(map(int, match.groups()))
    return box if box[2] > box[0] and box[3] > box[1] else None


def _deadline(adb, timeout):
    end = time.monotonic() + positive_timeout(timeout)
    end = min(end, getattr(adb, '_interaction_deadline', end))
    if time.monotonic() >= end:
        raise FinalError('Die verfügbare Zeit für diesen Schritt ist abgelaufen.')
    return end


def snapshot(adb, *, stop=None, timeout=5):
    stop = stop if stop is not None else getattr(adb, 'stop_requested', None)
    check_cancelled(stop)
    end = _deadline(adb, timeout)
    tree = adb.baum(timeout=max(.05, end - time.monotonic()))
    check_cancelled(stop)
    return tree


def signature(tree, own_nodes):
    """An in-memory digest, never a report containing field values."""
    rows = []
    for node in own_nodes(tree):
        row = [node.get(key) or '' for key in ('class', 'resource-id', 'bounds', 'checked', 'selected', 'enabled')]
        if not node.get('class', '').endswith('EditText') and node.get('password') != 'true':
            row.extend(node.get(key) or '' for key in ('text', 'content-desc'))
        rows.append(tuple(row))
    return hashlib.sha256(repr(rows).encode('utf-8')).digest()


def _identity(node):
    return tuple(node.get(key) or '' for key in ('package', 'class', 'resource-id', 'bounds', 'content-desc', 'hint'))


def _clear_point(tree, node, nodes, *, scroll=False, point=None):
    allowed = set(nodes)
    if node not in allowed or rectangle(node) is None:
        return None
    parents = {child: parent for parent in tree.iter() for child in parent}
    x1, y1, x2, y2 = rectangle(node)
    x, y = point if point is not None else ((x1 + x2) // 2, (y1 + y2) // 2)
    ancestors, current, actionable = set(), node, False
    while current is not None:
        ancestors.add(current)
        box = rectangle(current)
        if box and not (box[0] <= x < box[2] and box[1] <= y < box[3]):
            return None
        if current.get('visible-to-user') == 'false' or current.get('enabled') == 'false':
            return None
        actionable |= (current.get('clickable') == 'true' or current.get('class', '').endswith(('EditText', 'Button', 'Spinner'))
                       or (scroll and current.get('scrollable') == 'true'))
        current = parents.get(current)
    if not actionable:
        return None
    descendants = set(node.iter('node'))
    ordered = list(tree.iter('node'))
    index = ordered.index(node)
    for other_index, other in enumerate(ordered):
        if other in ancestors or other in descendants or other.get('visible-to-user') == 'false':
            continue
        current = other
        while current is not None and current.get('visible-to-user') != 'false':
            current = parents.get(current)
        if current is not None:
            continue
        package = other.get('package') or ''
        if package and package != node.get('package') and any(word in package for word in ('permissioncontroller', 'packageinstaller')):
            return None
        box = rectangle(other)
        if box is None or not (box[0] <= x < box[2] and box[1] <= y < box[3]):
            continue
        foreign_leaf = package and package != node.get('package') and not list(other)
        later_control = (package == node.get('package') and other_index > index
                         and (other.get('clickable') == 'true' or other.get('class', '').endswith(('EditText', 'Button'))))
        if foreign_leaf or later_control:
            return None
    return x, y


def ready(adb, select, own_nodes, *, stop=None, timeout=7, scroll=False):
    stop = stop if stop is not None else getattr(adb, 'stop_requested', None)
    end = _deadline(adb, timeout)
    previous = None
    while time.monotonic() < end:
        check_cancelled(stop)
        try:
            tree = snapshot(adb, stop=stop, timeout=min(3, max(.05, end - time.monotonic())))
        except AdbError:
            previous = None
        else:
            node = select(tree)
            point = _clear_point(tree, node, own_nodes(tree), scroll=scroll) if node is not None else None
            current = (_identity(node), point) if point is not None else None
            if current is not None and current == previous:
                return tree, node, point
            previous = current
        wait(min(.15, max(0, end - time.monotonic())), stop)
    check_cancelled(stop)
    raise NotReady('Das Eingabefeld oder die Schaltfläche ist noch nicht sicher bedienbar. Keine Berührung ausgeführt.')


def tap(adb, select, own_nodes, *, stop=None, timeout=7, before_dispatch=None):
    stop = stop if stop is not None else getattr(adb, 'stop_requested', None)
    settings = getattr(adb, 'input_tempo', None)
    if settings:
        wait(settings['click_ms'] / 1000, stop)
    tree, _node, point = ready(adb, select, own_nodes, stop=stop, timeout=timeout)
    check_cancelled(stop)
    if before_dispatch is not None:
        before_dispatch()
    # The configured pause has already happened. Avoid another delay between
    # the final view check and dispatching the touch through the owned ADB.
    adb.sh('input', 'tap', *point, timeout=5)
    check_cancelled(stop)
    return tree


def changed(adb, before, own_nodes, *, stop=None, timeout=5):
    stop = stop if stop is not None else getattr(adb, 'stop_requested', None)
    end = _deadline(adb, timeout)
    original, previous = signature(before, own_nodes), None
    while time.monotonic() < end:
        check_cancelled(stop)
        tree = snapshot(adb, stop=stop, timeout=min(3, max(.05, end - time.monotonic())))
        current = signature(tree, own_nodes)
        if current != original and current == previous:
            return True
        previous = current
        wait(min(.2, max(0, end - time.monotonic())), stop)
    check_cancelled(stop)
    return False


def scroll(adb, select, own_nodes, *, stop=None, up=False, before_dispatch=None):
    stop = stop if stop is not None else getattr(adb, 'stop_requested', None)
    tree, node, _point = ready(adb, select, own_nodes, stop=stop, scroll=True)
    x1, y1, x2, y2 = rectangle(node)
    x = (x1 + x2) // 2
    start, end = y1 + (y2 - y1) * 3 // 4, y1 + (y2 - y1) // 3
    if up:
        start, end = end, start
    # Inspect every intersecting rectangle along the gesture, including the
    # endpoints. A clear centre alone does not exclude a keyboard or overlay.
    low, high = sorted((start, end))
    probes = {start, end, (start + end) // 2}
    for other in tree.iter('node'):
        box = rectangle(other)
        if box and box[0] <= x < box[2]:
            top, bottom = max(low, box[1]), min(high, box[3] - 1)
            if top <= bottom:
                probes.add((top + bottom) // 2)
    nodes = own_nodes(tree)
    if any(_clear_point(tree, node, nodes, scroll=True, point=(x, y)) is None for y in probes):
        raise NotReady('Der Wischbereich ist verdeckt. Keine Wischbewegung ausgeführt.')
    check_cancelled(stop)
    if before_dispatch is not None:
        before_dispatch()
    adb.sh('input', 'swipe', x, start, x, end, 300, timeout=5)
    check_cancelled(stop)
    return changed(adb, tree, own_nodes, stop=stop)
