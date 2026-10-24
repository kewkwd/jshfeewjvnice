"""TikTok-Registrierung und Kontoprüfung über die App im Emulator."""
import logging, random, re, time
import xml.etree.ElementTree as ET
from ..operation import check_cancelled

log = logging.getLogger(__name__)
PAKET = 'com.zhiliaoapp.musically'
TEXTE = {
    'ueberspringen': ['Überspringen', 'Skip', 'Skip all'],
    'schliessen': ['Schließen', 'Close', 'Später', 'Not now'],
    'registrieren': ['Registrieren', 'Sign up'],
    'mit_email': ['E-Mail', 'Email', 'Mit E-Mail', 'Use email', 'Use phone or email'],
    'weiter': ['Weiter', 'Next', 'Continue'],
    'bestaetigen': ['Bestätigen', 'Confirm', 'Fertig', 'Done'],
    'profil': ['Profil', 'Profile'],
}


def registrieren(adb, ctx):
    """Run once; an unknown screen becomes a manual pause, never a retry."""
    from ..net.errors import FinalError
    handoff = getattr(ctx, 'android_handoff', None)
    try:
        result = _registrieren(adb, ctx)
    except FinalError:
        raise
    except RuntimeError:
        check_cancelled(getattr(ctx, 'stop', None))
        if handoff is None or not getattr(ctx, 'android_signup_prepared', False):
            raise
        result = False
    if result or handoff is None:
        return result
    def profile_ready(tree):
        state, observed, _code = _profil_status(adb, getattr(ctx, 'stop', None), tree=tree)
        if state == 'profile':
            ctx.tiktok_username = observed
            return True
        return False
    for _ in range(12):
        check_cancelled(getattr(ctx, 'stop', None))
        try:
            if profile_ready(adb.baum(timeout=5)):
                return True
        except RuntimeError:
            check_cancelled(getattr(ctx, 'stop', None))
        phone = _phone_challenge(adb)
        def ready(tree):
            if profile_ready(tree):
                return True
            if phone:
                from .tiktok_fields import code_channel
                return _own_view(tree) and code_channel(tree, ctx.email) != 'phone'
            return _can_resume_signup(tree, ctx)
        ctx.melden('TikTok benötigt deine Hilfe beim offenen Schritt. Sobald ein bekannter Schritt erscheint, geht es automatisch weiter.')
        handoff.wait('phone' if phone else 'completion', ready, resume_when_ready=True)
        if profile_ready(adb.baum(timeout=5)):
            return True
        try:
            # Continue the existing app session; never clear or register again.
            if _continue_signup(adb, ctx):
                return True
        except FinalError:
            raise
        except RuntimeError:
            check_cancelled(getattr(ctx, 'stop', None))
    raise FinalError('Zu viele unklare TikTok-Zwischenschritte. Der bestehende Versuch benötigt eine Prüfung; keine neue Registrierung gestartet.')


def _can_resume_signup(tree, ctx):
    from .tiktok_fields import FieldNotReady, code_channel, manual_gate, own_nodes, select
    from ..offer_policy import android_available
    if not _own_view(tree):
        return False
    if manual_gate(tree) or code_channel(tree, ctx.email) == 'phone':
        return False
    if android_available(tree, ctx, own_nodes):
        return True
    if _birthday_visible(tree) and not getattr(ctx, '_tiktok_birthday_attempted', False):
        return True
    entered = getattr(ctx, '_tiktok_entered_credentials', set())
    for kind in ('email', 'password', 'code'):
        if kind in entered or (kind == 'code' and (getattr(ctx, '_tiktok_code_submitted', False) or getattr(ctx, 'box', None) is None)):
            continue
        try:
            select(tree, kind, email=ctx.email)
            return True
        except FieldNotReady:
            pass
    return _finish_candidate(tree, ctx) is not None


def _registrieren(adb, ctx):
    from ..operation import check_cancelled
    from ..registration_progress import update
    def act(function,*args,**kwargs):
        check_cancelled(getattr(ctx,'stop',None))
        result=function(*args,**kwargs)
        check_cancelled(getattr(ctx,'stop',None))
        return result
    check_cancelled(getattr(ctx,'stop',None))
    if getattr(ctx,'platform',None)=='tiktok':update(ctx,'opening')
    # Existing app data may contain an interrupted or completed registration.
    # Keep it available for recovery, including attempts made before the guard.
    ctx.android_signup_prepared = True
    act(adb.app_starten, PAKET)
    act(_pause, random.uniform(8, 12), stop=getattr(ctx,'stop',None))
    act(_wegklicken, adb, 'ueberspringen', versuche=4, stop=getattr(ctx,'stop',None))
    act(_wegklicken, adb, 'schliessen', versuche=4, stop=getattr(ctx,'stop',None))
    act(_pause, 2, stop=getattr(ctx,'stop',None))
    if not act(_tippe, adb, 'profil', stop=getattr(ctx,'stop',None)):
        raise RuntimeError('TikTok: Die eigene Profilnavigation wurde nicht eindeutig erkannt. Bitte den bestehenden App-Stand prüfen.')
    act(_pause, 2, stop=getattr(ctx,'stop',None))
    state, _handle, _code = _profil_status(adb, getattr(ctx, 'stop', None))
    if state in ('profile', 'ambiguous'):
        from ..net.errors import FinalError
        raise FinalError('TikTok zeigt bereits ein angemeldetes Profil. Bitte den vorhandenen Zugang übernehmen; keine App-Daten gelöscht und keine neue Registrierung gestartet.')
    if state != 'logged_out':
        raise RuntimeError('TikTok: Eine abgemeldete Ausgangsansicht wurde noch nicht bestätigt. Bitte den bestehenden App-Stand prüfen.')
    if not act(_tippe, adb, 'registrieren', stop=getattr(ctx,'stop',None)):
        raise RuntimeError('TikTok-App: „Registrieren“ nicht gefunden.')
    if not act(_tippe, adb, 'mit_email', stop=getattr(ctx,'stop',None)):
        raise RuntimeError('TikTok-App: „Mit E-Mail“ nicht gefunden.')
    act(_pause, 2, stop=getattr(ctx,'stop',None))
    return _continue_signup(adb, ctx)


def _continue_signup(adb, ctx):
    from ..registration_progress import update
    def act(function, *args, **kwargs):
        check_cancelled(getattr(ctx, 'stop', None))
        result = function(*args, **kwargs)
        check_cancelled(getattr(ctx, 'stop', None))
        return result
    act(_credentials, adb, ctx)
    act(_geburtsdatum, adb, ctx=ctx, stop=getattr(ctx,'stop',None))
    if getattr(ctx,'platform',None)=='tiktok':update(ctx,'form')
    # A human check can hold the flow before TikTok exposes the email-code
    # screen. Resolve that explicit handoff before waiting for the code.
    if act(_raetsel, adb):
        act(_warte_manuell, adb, ctx)
    code_step_done = act(_code_schritt, adb, ctx)
    if act(_raetsel, adb):
        act(_warte_manuell, adb, ctx)
        # Some signup sequences reveal the code step only after the challenge.
        # Recheck it once after the handoff, without rereading an already used code.
        if not code_step_done:
            act(_code_schritt, adb, ctx)
    act(_credentials, adb, ctx)  # some app versions ask for a password after email verification
    if getattr(ctx, 'android_handoff', None) is not None and act(_phone_challenge, adb):
        return False  # retain the verification screen for the manual handoff
    return act(_finish_profile, adb, ctx)


def _finish_profile(adb, ctx):
    """Skip recognised optional onboarding and open the own profile once."""
    from .tiktok_fields import click, manual_gate, own_nodes
    from ..offer_policy import android_decline
    stop = getattr(ctx, 'stop', None)
    attempted = getattr(ctx, '_tiktok_finish_actions', set())
    ctx._tiktok_finish_actions = attempted
    for _ in range(8):
        check_cancelled(stop)
        tree = adb.baum(timeout=5)
        if not manual_gate(tree) and android_decline(adb, ctx, own_nodes):
            _pause(1, stop=stop)
            continue
        state, handle, _code = _profil_status(adb, stop, tree=tree)
        if state == 'profile':
            ctx.tiktok_username = handle
            return True
        if state == 'ambiguous':
            return False
        action = _finish_candidate(tree, ctx)
        if action is not None:
            key, labels = action
            attempted.add(key)
            if click(adb, labels, stop=stop):
                _pause(2, stop=stop)
                continue
        return False
    return False


def _finish_candidate(tree, ctx):
    from .tiktok_fields import code_channel, manual_gate, normal, own_nodes
    nodes = own_nodes(tree)
    from ..offer_policy import android_pending
    if (manual_gate(tree) or _birthday_visible(tree) or code_channel(tree, ctx.email) == 'phone'
            or android_pending(tree, nodes)
            or any(node.get('class', '').endswith('EditText') for node in nodes)):
        return None
    attempted = getattr(ctx, '_tiktok_finish_actions', set())
    captions = {normal(node.get(key) or '') for node in nodes for key in ('text', 'content-desc')}
    text = ' '.join(captions)
    categories = ('benachrichtigung', 'notification', 'kontakte', 'contacts',
                  'interessen', 'interests', 'tutorial', 'einführung', 'freunden folgen', 'follow friends')
    category = next((word for word in categories if word in text), None)
    skip = ('Überspringen', 'Nicht jetzt', 'Später', 'Skip', 'Skip all', 'Not now')
    if category is not None and ('skip', category) not in attempted and captions & {normal(label) for label in skip}:
        return ('skip', category), skip
    if ('open_profile',) not in attempted and captions & {'profil', 'profile'}:
        return ('open_profile',), ('Profil', 'Profile')
    return None


def _pause(seconds, *, stop=None):
    check_cancelled(stop)
    if stop is None:
        time.sleep(seconds)
    else:
        while seconds>0:
            interval=min(0.2,seconds)
            time.sleep(interval)
            check_cancelled(stop)
            seconds-=interval
    check_cancelled(stop)


def _tippe(adb, schluessel, *, stop=None):
    from .tiktok_fields import click, own_nodes
    from ..offer_policy import android_pending
    check_cancelled(stop)
    tree = adb.baum(timeout=5)
    if android_pending(tree, own_nodes(tree)):
        return False
    return click(adb, TEXTE.get(schluessel, ()), stop=stop,
                 guard=lambda current: not android_pending(current, own_nodes(current)))


def _wegklicken(adb, schluessel, versuche=3, *, stop=None):
    # The old count remains accepted by callers. A missing response must not
    # cause another startup dismissal on the same possibly unchanged surface.
    if versuche > 0 and _tippe(adb,schluessel,stop=stop):
        _pause(random.uniform(0.8,1.6),stop=stop)


def _weiter(adb, *, stop=None):
    from .tiktok_fields import click
    return click(adb, TEXTE['weiter'], stop=stop)


def _feld_fuellen(adb, wert, *, kind, ctx=None, stop=None):
    from .tiktok_fields import fill
    try:
        result = fill(adb, kind, wert, email=getattr(ctx, 'email', None), stop=stop)
        if ctx is not None:
            if result and kind in ('email', 'password'):
                verified = getattr(ctx, '_tiktok_verified_credentials', set())
                ctx._tiktok_verified_credentials = verified
                verified.add(kind)
            ctx.melden({'email': 'TikTok: Die gespeicherte E-Mail-Adresse wurde eingetragen.',
                        'password': 'TikTok: Das gespeicherte Kennwort wurde eingetragen.',
                        'code': 'TikTok: Der Bestätigungscode aus der E-Mail wurde eingetragen.'}[kind])
        return result
    except RuntimeError:
        if kind == 'password' and ctx is not None:
            ctx.android_credentials_uncertain = True
        raise


def _credentials(adb, ctx):
    """Fill each recognised credential once, together if both are visible."""
    from .tiktok_fields import FieldNotReady, click, code_channel, credentials_ready, manual_gate, own_nodes, select
    from ..offer_policy import android_decline, android_pending
    entered = getattr(ctx, '_tiktok_entered_credentials', set())
    ctx._tiktok_entered_credentials = entered
    stop = getattr(ctx, 'stop', None)
    for _ in range(5):
        check_cancelled(stop)
        _geburtsdatum(adb, ctx=ctx, stop=stop)
        tree = adb.baum(timeout=5)
        if not manual_gate(tree) and android_decline(adb, ctx, own_nodes):
            _pause(1, stop=stop)
            continue
        if android_pending(tree, own_nodes(tree)):
            raise FieldNotReady('Ein Zusatzangebot ist geöffnet; die passende Ablehnen-Schaltfläche wird noch nicht erkannt. Es wird nichts gebucht.')
        state, _handle, _code = _profil_status(adb, stop, tree=tree)
        if state in ('profile', 'ambiguous'):
            return
        if manual_gate(tree):
            _warte_manuell(adb, ctx)
            continue
        if code_channel(tree, ctx.email) == 'phone':
            return
        available = []
        for kind in ('email', 'password'):
            if kind in entered:
                continue
            try:
                select(tree, kind, email=ctx.email)
            except FieldNotReady:
                continue
            available.append(kind)
        if not available:
            if 'email' not in entered and not getattr(ctx, '_tiktok_email_tab_opened', False):
                ctx._tiktok_email_tab_opened = True
                if click(adb, ('E-Mail', 'Email'), stop=stop):
                    _pause(1, stop=stop)
                    continue
            return
        for kind in available:
            entered.add(kind)  # a partial failure must never cause an automatic second entry
            value = ctx.email if kind == 'email' else ctx.password
            _feld_fuellen(adb, value, kind=kind, ctx=ctx, stop=stop)
        if not click(adb, (*TEXTE['weiter'], 'Registrieren', 'Sign up'), stop=stop,
                     guard=lambda tree: credentials_ready(tree, ctx)):
            raise FieldNotReady('TikTok zeigt keinen eindeutigen aktiven Weiter-Knopf. Bitte im Fenster prüfen.')
        _pause(2, stop=stop)


def _geburtsdatum(adb, *, ctx=None, stop=None):
    check_cancelled(stop)
    visible=adb.finde(teil='Geburtsdatum')
    check_cancelled(stop)
    if not visible:
        visible=adb.finde(teil='Birthday')
        check_cancelled(stop)
    if not visible:return
    from ..profile_settings import birthdate
    from ..net.errors import FinalError
    if ctx is None:raise FinalError('Für die Geburtstagsauswahl fehlt das vorbereitete Profil.')
    born = birthdate(ctx.person)
    if not getattr(ctx, '_tiktok_birthday_attempted', False):
        from .birthday_fields import enter
        ctx._tiktok_birthday_attempted = True
        try:
            if enter(adb, born, stop=stop):
                _pause(2, stop=stop)
                if not _birthday_visible(adb.baum(timeout=5)):
                    return
        except RuntimeError:
            check_cancelled(stop)
    handoff = getattr(ctx, 'android_handoff', None)
    if handoff is not None:
        ctx.melden('Gespeichertes Geburtsdatum: ' + born.strftime('%d.%m.%Y') + '. Bitte im Android-Fenster auswählen.')
        handoff.wait('birthday', lambda tree: _own_view(tree) and not _birthday_visible(tree), resume_when_ready=True)
        return
    if getattr(getattr(adb, '_socialscraper_avd_session', None), 'headless', False):
        raise FinalError('Die Geburtstagsauswahl benötigt ein sichtbares Fenster. Bitte die begleitete Registrierung in Profilwerk verwenden.')
    ctx.melden('Bitte im Emulator das gespeicherte Geburtsdatum '+born.strftime('%d.%m.%Y')+
               ' auswählen und selbst bestätigen. Keine automatische Ersatzwahl.')
    until = time.monotonic() + ctx.timeout
    # Unknown native wheel layouts cannot safely be set with blind swipes.
    while time.monotonic() < until:
        check_cancelled(stop)
        visible = adb.finde(teil='Geburtsdatum')
        check_cancelled(stop)
        if not visible:visible = adb.finde(teil='Birthday')
        check_cancelled(stop)
        if not visible:return
        _pause(1,stop=stop)
    raise FinalError('Die manuelle Auswahl des gespeicherten Geburtsdatums wurde nicht abgeschlossen.')


def _own_view(tree):
    return any(node.get('package') == PAKET and node.get('visible-to-user') != 'false'
               for node in tree.iter('node'))


def _birthday_visible(tree):
    return any(node.get('package') == PAKET and
               any(word in (node.get(key) or '').casefold()
                   for key in ('text', 'content-desc') for word in ('geburtsdatum', 'birthday'))
               for node in tree.iter('node'))


def _raetsel(adb, *, tree=None):
    from .tiktok_fields import manual_gate
    if tree is None:
        tree = adb.baum(timeout=5)
    return manual_gate(tree)


def _warte_manuell(adb, ctx):
    handoff = getattr(ctx, 'android_handoff', None)
    if handoff is not None:
        handoff.wait('captcha', lambda tree: _own_view(tree) and not _raetsel(adb, tree=tree), resume_when_ready=True)
        return
    from ..net.errors import FinalError
    if getattr(getattr(adb, '_socialscraper_avd_session', None), 'headless', False):
        raise FinalError('Das CAPTCHA benötigt deine Eingabe. Bitte die begleitete Registrierung in Profilwerk verwenden.')
    from ..operation import cancelled, positive_timeout, wait
    stop = getattr(ctx, 'stop', None)
    deadline = time.monotonic() + positive_timeout(ctx.timeout)
    if getattr(ctx,'platform',None)=='tiktok':
        from ..registration_progress import update
        update(ctx,'waiting_confirmation')
    ctx.melden('TikTok zeigt ein CAPTCHA – bitte selbst im Emulator lösen. Warte auf Bestätigung …')
    while True:
        if cancelled(stop):
            raise FinalError('Abgebrochen.')
        if not _raetsel(adb):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FinalError('Die manuelle CAPTCHA-Bestätigung wurde nicht abgeschlossen; kein Konto bestätigt.')
        if not wait(min(1, remaining), stop):
            raise FinalError('Abgebrochen.')


def _phone_challenge(adb):
    """Only explicit confirmation labels, not a generic phone signup option."""
    from .adb import AdbError
    try:
        tree = adb.baum(timeout=5)
    except AdbError:
        return False
    from .tiktok_fields import code_channel
    if code_channel(tree) == 'phone':
        return True
    labels = {'telefonnummer bestätigen', 'telefonnummer verifizieren', 'verify phone number',
              'verify your phone number', 'sms-bestätigung', 'sms verification'}
    return any(node.get('package') == PAKET and
               any((node.get(key) or '').strip().casefold() in labels for key in ('text', 'content-desc'))
               for node in tree.iter('node'))


def _code_schritt(adb, ctx):
    """Consume one visible code step, with cancellation after every external call."""
    from ..operation import check_cancelled
    def act(function,*args,**kwargs):
        check_cancelled(getattr(ctx,'stop',None))
        result=function(*args,**kwargs)
        check_cancelled(getattr(ctx,'stop',None))
        return result
    from .tiktok_fields import FieldNotReady, code_channel, select
    if getattr(ctx, '_tiktok_code_submitted', False) or getattr(ctx, 'box', None) is None:
        return False
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        tree = act(adb.baum, timeout=5)
        if code_channel(tree, ctx.email) == 'phone' or _raetsel(adb, tree=tree):
            return False
        if _profil_status(adb, getattr(ctx, 'stop', None), tree=tree)[0] in ('profile', 'ambiguous'):
            return False
        if _finish_candidate(tree, ctx) is not None:
            return False
        if 'password' not in getattr(ctx, '_tiktok_entered_credentials', set()):
            try:
                select(tree, 'password', email=ctx.email)
                return False  # continue with the known password step first
            except FieldNotReady:
                pass
        try:
            select(tree, 'code', email=ctx.email)
            break
        except FieldNotReady:
            _pause(.5, stop=getattr(ctx, 'stop', None))
    else:
        return False
    code = getattr(ctx, '_tiktok_email_code', None)
    if code is None:
        ctx.melden('TikTok: Der Bestätigungscode wird aus deinem E-Mail-Postfach abgerufen.')
        code = act(ctx.code_holen, timeout=240, ab=time.time()-30)
        ctx._tiktok_email_code = code
    # Mail retrieval can outlast the current app screen. Never type a mail
    # code into a later phone challenge or retry a partially entered code.
    try:
        select(act(adb.baum, timeout=5), 'code', email=ctx.email)
    except FieldNotReady:
        return False
    ctx._tiktok_code_submitted = True
    act(_feld_fuellen,adb,code,kind='code',ctx=ctx,stop=getattr(ctx,'stop',None))
    act(_pause,1,stop=getattr(ctx,'stop',None))
    act(_weiter,adb,stop=getattr(ctx,'stop',None))
    act(_pause,4,stop=getattr(ctx,'stop',None))
    return True


def _ist_angemeldet(adb, ctx):
    """Confirm the signup from the own app profile and retain its actual handle."""
    stop = getattr(ctx, 'stop', None)
    ctx.tiktok_username = ''
    check_cancelled(stop)
    adb.taste('4')
    _pause(1, stop=stop)
    for text in ('Profil', 'Profile'):
        check_cancelled(stop)
        clicked = adb.tippe_auf(text=text, timeout=3)
        check_cancelled(stop)
        if clicked:
            break
    _pause(2, stop=stop)
    state, observed, _ = _profil_status(adb, stop)
    check_cancelled(stop)
    if state != 'profile':
        return False
    ctx.tiktok_username = observed
    return True


def _profil_status(adb, stop, *, tree=None):
    """Read account evidence from one own-package UI snapshot, never a bio mention."""
    from ..accountvault import normalize_tiktok_username
    from ..net.errors import FinalError
    from ..operation import check_cancelled
    check_cancelled(stop)
    root = adb.baum() if tree is None else tree
    check_cancelled(stop)
    if not isinstance(root, ET.Element):
        raise FinalError('Die TikTok-Profilansicht konnte nicht eindeutig gelesen werden.')
    parents = {child: parent for parent in root.iter() for child in parent}

    def bounds(node):
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
        if match:
            x1, y1, x2, y2 = map(int, match.groups())
            if x2 > x1 and y2 > y1:
                return x1, y1, x2, y2

    def visible(node):
        if node.get('package') != PAKET or bounds(node) is None:
            return False
        current = node
        while current is not None:
            if (current.get('visible-to-user') == 'false' or current.get('enabled') == 'false'
                    or current.get('package') not in (None, '', PAKET)):
                return False
            current = parents.get(current)
        return True

    def action(node):
        current = node
        while current is not None and current.get('package') == PAKET:
            if current.get('clickable') == 'true' or current.get('class') == 'android.widget.Button':
                return True
            current = parents.get(current)
        return False

    def labels(node):
        return {node.get(key, '').strip().casefold() for key in ('text', 'content-desc')}

    nodes = [node for node in root.iter('node') if visible(node)]
    edits = [node for node in nodes if action(node)
             and labels(node) & {'profil bearbeiten', 'edit profile'}]
    logins = [node for node in nodes if action(node)
              and labels(node) & {'anmelden', 'log in'}]
    code = any(labels(node) & {'code', 'verification code', 'bestätigungscode'} for node in nodes)
    handles, invalid = set(), False
    header_bottom = min((bounds(node)[1] for node in edits), default=0)
    semantic_ids = {'username', 'user_name', 'unique_id', 'profile_username', 'profile_unique_id'}
    for node in nodes:
        if bounds(node)[3] > header_bottom or node.get('class', '').endswith('EditText'):
            continue
        candidates = []
        resource = node.get('resource-id', '')
        resource_name = resource.rsplit('/', 1)[-1].casefold()
        if resource_name in {'bio', 'biography', 'signature', 'profile_bio', 'display_name',
                             'profile_name', 'nickname', 'nick_name'}:
            continue
        if (resource.startswith(PAKET + ':id/') and resource_name in semantic_ids
                and node.get('text', '').strip()):
            candidates.append(node.get('text', '').strip())
        # Arbitrary text (including a display name) can say "Username: @...".
        # Only a dedicated accessibility label may supplement a semantic ID;
        # any accompanying text must be the same bare handle.
        match = re.fullmatch(r'(?:username|benutzername)\s*:\s*(@[^\s]+)',
                             node.get('content-desc', '').strip(), re.IGNORECASE)
        if match:
            try:
                text_value = node.get('text', '').strip()
                if text_value and normalize_tiktok_username(text_value, required=True) != \
                        normalize_tiktok_username(match.group(1), required=True):
                    invalid = True
                candidates.append(match.group(1))
            except ValueError:
                invalid = True
        for candidate in candidates:
            try:
                handles.add(normalize_tiktok_username(candidate, required=True))
            except ValueError:
                invalid = True
    if edits:
        if len(handles) == 1 and not invalid and not logins:
            return 'profile', next(iter(handles)), code
        return 'ambiguous', None, code
    return ('logged_out' if logins else 'unknown'), None, code


def anmelden(adb, ctx):
    """Reuse or sign in only after proving the explicitly selected TikTok account."""
    from ..accountvault import normalize_tiktok_username
    from ..net.errors import FinalError
    from ..operation import check_cancelled, positive_timeout, wait
    stop = getattr(ctx, 'stop', None)
    check_cancelled(stop)
    try:
        expected = normalize_tiktok_username(getattr(ctx, 'tiktok_username', None), required=True)
    except ValueError as exc:
        raise FinalError(str(exc)) from None
    timeout = positive_timeout(ctx.timeout)

    def act(function, *args, **kwargs):
        check_cancelled(stop)
        result = function(*args, **kwargs)
        check_cancelled(stop)
        return result

    def pause(seconds):
        check_cancelled(stop)
        if not wait(seconds, stop):
            raise FinalError('Abgebrochen.')
        check_cancelled(stop)

    def confirmed(state, observed):
        if state == 'profile':
            if observed != expected:
                raise FinalError('In der TikTok-App ist ein anderes Konto geöffnet. '
                                 'Es wurde nicht verändert oder abgemeldet.')
            check_cancelled(stop)
            return True
        if state == 'ambiguous':
            raise FinalError('Die eigene TikTok-Profilansicht zeigt keinen eindeutigen Benutzernamen. '
                             'Das Konto wurde nicht bestätigt.')
        return False

    act(adb.app_starten, PAKET)
    pause(8)
    act(_wegklicken, adb, 'ueberspringen')
    state, observed, _ = _profil_status(adb, stop)
    if confirmed(state, observed):
        return True
    if state == 'unknown':
        # A substring match for "Profil" can click "Profil bearbeiten".
        if (act(adb.tippe_auf, text='Profil', timeout=3)
                or act(adb.tippe_auf, text='Profile', timeout=3)):
            state, observed, _ = _profil_status(adb, stop)
            if confirmed(state, observed):
                return True
    if state != 'logged_out':
        raise FinalError('Die TikTok-App zeigt weder eine eindeutige eigene Profilansicht '
                         'noch die Anmeldung. Es wurden keine Zugangsdaten eingegeben.')
    if not (act(adb.tippe_auf, teil='Anmelden', timeout=5)
            or act(adb.tippe_auf, teil='Log in', timeout=5)):
        raise FinalError('Die TikTok-Anmeldung konnte nicht geöffnet werden.')
    if not act(_tippe, adb, 'mit_email'):
        raise FinalError('Die TikTok-Anmeldung per E-Mail wurde nicht gefunden.')
    act(_feld_fuellen, adb, ctx.email, kind='email', ctx=ctx, stop=stop)
    act(_weiter, adb, stop=stop)
    pause(3)
    act(_feld_fuellen, adb, ctx.password, kind='password', ctx=ctx, stop=stop)
    if not (act(adb.tippe_auf, teil='Anmelden', timeout=5)
            or act(adb.tippe_auf, teil='Log in', timeout=5)):
        act(_weiter, adb, stop=stop)
    deadline = time.monotonic() + timeout
    code_requested = False
    while time.monotonic() < deadline:
        state, observed, code = _profil_status(adb, stop)
        if time.monotonic() >= deadline:
            break
        if confirmed(state, observed):
            return True
        if ctx.box and not code_requested:
            from .tiktok_fields import FieldNotReady, code_channel, select
            current = act(adb.baum, timeout=5)
            if code_channel(current, ctx.email) == 'phone':
                raise FinalError('TikTok verlangt eine Telefon- oder SMS-Bestätigung. Diese muss selbst '
                                 'im begleiteten Android-Fenster abgeschlossen werden; kein E-Mail-Code eingegeben.')
            try:
                select(current, 'code', email=ctx.email)
            except FieldNotReady:
                pause(min(1, max(0, deadline - time.monotonic())))
                continue
            code_requested = True
            value = act(ctx.code_holen, timeout=min(120, deadline - time.monotonic()), ab=time.time() - 30)
            if time.monotonic() >= deadline:
                break
            act(_feld_fuellen, adb, value, kind='code', ctx=ctx, stop=stop)
            act(_weiter, adb, stop=stop)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            pause(min(1, remaining))
    check_cancelled(stop)
    raise FinalError('Die Anmeldung hat das ausgewählte TikTok-Profil nicht rechtzeitig bestätigt.')
