"""German language in the owned Android guest, preserving Nox host resources."""
import re
import time
from ..net.errors import FinalError


def ensure_avd_german(adb):
    """Configure German in the owned Android 15 guest, then verify it."""
    from .adb import AdbError
    from ..operation import check_cancelled, wait
    session = getattr(adb, '_socialscraper_avd_session', None)
    if session is None:
        raise FinalError('Keine geprüfte eigene AVD-Sitzung für die Sprachwahl.')
    stop = getattr(adb, 'stop_requested', None)
    deadline = time.monotonic() + 180
    configured = False
    while True:
        check_cancelled(stop)
        session.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FinalError('Die aktive AVD-Sprache ist nicht Deutsch; keine App gestartet.')
        try:
            last_config = adb.sh('am', 'get-config', timeout=min(10, remaining))
        except AdbError:
            # Changing the locale can restart Android's framework after boot.
            pass
        else:
            if german_config(last_config):
                return
            if not configured:
                _select_avd_german(adb, session, deadline)
                configured = True
        wait(.5, stop)


def _select_avd_german(adb, session, deadline):
    """Handle the SDK image's English default without root or account resets."""
    from .adb import AdbError, GRENZE
    from ..operation import check_cancelled, wait
    stop = getattr(adb, 'stop_requested', None)
    package = 'com.android.settings'
    label_id = package + ':id/label'

    def remaining():
        check_cancelled(stop)
        session.check()
        value = deadline - time.monotonic()
        if value <= 0:
            raise FinalError('Deutsche AVD-Sprache konnte nicht bestätigt werden; keine App gestartet.')
        return value

    def observe():
        while True:
            try:
                tree = adb.baum(timeout=min(10, remaining()))
                if any(n.get('package') == package for n in tree.iter('node')):
                    return tree
            except AdbError:
                pass
            remaining()
            wait(.3, stop)

    def target(criteria):
        while True:
            tree = observe()
            matches = [n for n in tree.iter('node') if n.get('package') == package
                       and n.get('enabled') == 'true'
                       and all(n.get(key) == value for key, value in criteria.items())]
            if len(matches) == 1:
                return tree, matches[0]
            if len(matches) > 1:
                raise FinalError('Android-Sprachwahl ist nicht eindeutig; nichts bestätigt.')
            remaining()
            wait(.3, stop)

    def tap(criteria):
        tree, node = target(criteria)
        match = GRENZE.fullmatch(node.get('bounds', ''))
        if match is None:
            raise FinalError('Android-Sprachwahl hat keine gültige Position.')
        x1, y1, x2, y2 = map(int, match.groups())
        if x2 <= x1 or y2 <= y1:
            raise FinalError('Android-Sprachwahl ist nicht sichtbar.')
        remaining()
        adb.tap((x1+x2)//2, (y1+y2)//2)
        return tree

    adb.sh('am', 'start', '-a', 'android.settings.LOCALE_SETTINGS', timeout=min(15, remaining()))
    target({'resource-id': package + ':id/add_language'})
    tree = observe()
    german = {'resource-id': label_id, 'text': 'Deutsch (Deutschland)'}
    if not any(all(n.get(k) == v for k, v in german.items()) for n in tree.iter('node')):
        tap({'resource-id': package + ':id/add_language'})
        tap({'resource-id': 'android:id/locale_search_menu'})
        tap({'resource-id': 'android:id/search_src_text'})
        remaining()
        adb.tippen('Deutsch')
        tap({'resource-id': 'android:id/locale', 'text': 'Deutsch'})
        tap({'resource-id': 'android:id/locale', 'text': 'Deutschland'})
    tree, _ = target(german)
    labels = [n.get('text') for n in tree.iter('node') if n.get('resource-id') == label_id]
    if labels and labels[0] == 'Deutsch (Deutschland)':
        return
    # Only retire the known SDK default. Preserve unfamiliar language lists.
    if labels != ['English (United States)', 'Deutsch (Deutschland)']:
        raise FinalError('Bitte Deutsch (Deutschland) in Android an die erste Stelle setzen; '
                         'eine unbekannte Sprachliste wird nicht automatisch verändert.')
    tap({'content-desc': 'More options'})
    tap({'text': 'Remove', 'resource-id': 'android:id/title'})
    tap({'text': 'English (United States)', 'resource-id': label_id})
    tree = observe()
    checked = [n for n in tree.iter('node') if n.get('resource-id') == package + ':id/checkbox'
               and n.get('checked') == 'true']
    parents = {child: parent for parent in tree.iter() for child in parent}
    row = checked[0] if len(checked) == 1 else None
    while row is not None and row.get('class') != 'android.widget.RelativeLayout':
        row = parents.get(row)
    selected = [n.get('text') for n in row.iter('node') if n.get('resource-id') == label_id] if row is not None else []
    if selected != ['English (United States)']:
        raise FinalError('Englische Standardsprache ist nicht eindeutig ausgewählt; nichts entfernt.')
    tap({'content-desc': 'Remove'})
    target({'resource-id': package + ':id/alertTitle', 'text': 'Remove selected language?'})
    tap({'resource-id': 'android:id/button1', 'text': 'Remove'})
    tree = observe()
    if any(n.get('resource-id') == package + ':id/dialog_title'
           and n.get('text') == 'Systemsprache in Deutsch (Deutschland) ändern?'
           for n in tree.iter('node')):
        tap({'resource-id': 'android:id/button1', 'text': 'Ändern'})


def german_config(raw):
    first=raw.splitlines()[0] if raw else ''
    if not first.startswith('config:'):return False
    return bool(re.search(r'(?:^|-)(?:de-rDE|b\+de(?:\+[A-Za-z]{4})?\+DE)(?:-|,|$)',
                          first.partition(':')[2].strip()))

def ensure_german(adb):
    if german_config(adb.sh('am','get-config')):return
    for key,value in (('persist.sys.locale','de-DE'),('persist.sys.language','de'),('persist.sys.country','DE')):
        adb.sh('setprop',key,value)
    adb.sh('settings','put','system','system_locales','de-DE')
    adb.sh('am','broadcast','-a','android.intent.action.LOCALE_CHANGED')
    if german_config(adb.sh('am','get-config')):return
    adb.sh('am','start','-a','android.settings.LOCALE_SETTINGS')
    def observe():
        tree=adb.baum()
        if not any(n.get('package')=='com.android.settings' for n in tree.iter('node')):
            raise FinalError('Android-Spracheinstellungen sind nicht erreichbar.')
        return tree
    def tap(attribute,value):
        tree=observe()
        match=next((n for n in tree.iter('node') if n.get('package')=='com.android.settings' and n.get(attribute)==value),None)
        if match is None:raise FinalError('Deutsche Android-Sprache konnte nicht automatisch aktiviert werden.')
        selector={'text':'text','resource-id':'rid','content-desc':'beschreibung'}[attribute]
        target=adb.finde(**{selector:value})
        if not target:raise FinalError('Android-Sprachelement ist nicht bedienbar.')
        adb.tap(*target)
    tree=observe()
    if not any(n.get('resource-id')=='com.android.settings:id/label' and n.get('text')=='Deutsch (Deutschland)' for n in tree.iter('node')):
        tap('resource-id','com.android.settings:id/add_language')
        tap('text','Deutsch (Deutschland)')
    tree=observe()
    labels=[n.get('text') for n in tree.iter('node') if n.get('resource-id')=='com.android.settings:id/label']
    if 'Deutsch (Deutschland)' not in labels or any(label not in ('Deutsch (Deutschland)','中文（中国）') for label in labels):
        raise FinalError('Unerwartete Android-Sprachliste; keine Sprache entfernt.')
    if '中文（中国）' in labels:
        tap('content-desc','更多选项')
        tap('text','移除')
        tap('text','中文（中国）')
        tree=observe()
        selected=[n.get('text') for n in tree.iter('node') if n.get('resource-id')=='com.android.settings:id/checkbox' and n.get('checked')=='true']
        if selected!=['中文（中国）']:
            raise FinalError('Android-Sprachwahl ist nicht eindeutig; nichts entfernt.')
        tap('content-desc','移除')
        tap('resource-id','android:id/button1')
    if not german_config(adb.sh('am','get-config')):
        raise FinalError('Die aktive Android-Sprache ist noch nicht Deutsch.')
    adb.zurueck()
