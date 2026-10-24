"""A bounded manual pause on one owned AVD; no autonomous input while open."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from .adb import AdbError, GRENZE
from .preview import InputRejected, consume_inputs, screen_size
from ..net.errors import FinalError
from ..operation import check_cancelled, positive_timeout, wait

PREFIX = '[SS_ANDROID_HANDOFF] '
REASONS = {
    'birthday': 'Bitte das gespeicherte Geburtsdatum auswählen und bestätigen.',
    'captcha': 'Bitte das CAPTCHA selbst lösen.',
    'phone': 'Bitte die Telefon- oder SMS-Bestätigung übernehmen.',
    'completion': 'Bitte den offenen Schritt prüfen und das eigene TikTok-Profil öffnen.',
    'mail_registration': 'Dieser WEB.DE-Schritt benötigt deine Eingabe.',
    'mail_password': 'Bitte das tatsächlich verwendete WEB.DE-Kennwort bestätigen.',
}
MESSAGES = {
    'waiting': 'Automatische Eingaben pausieren. Nach einem erkennbar erledigten Schritt geht es selbstständig weiter. „Weiter prüfen“ ist zusätzlich möglich.',
    'pending': 'Der benötigte Schritt ist noch offen. Bitte im Android-Fenster abschließen.',
    'unreadable': 'Der Abschluss konnte noch nicht gelesen werden. Bitte erneut prüfen.',
    'input_rejected': 'Die Eingabe wurde nicht übernommen. Auf ein aktuelles Bild warten und erneut versuchen.',
    'working': 'Erkannte Felder werden eingetragen. Bitte kurz warten …',
}


def parse(line):
    if not isinstance(line, str) or not line.startswith(PREFIX) or len(line) > 4096:
        return None
    try:
        event = json.loads(line[len(PREFIX):])
        if (not isinstance(event, dict) or set(event) != {'version', 'pid', 'state', 'directory', 'request', 'reason'}
                or type(event['version']) is not int or event['version'] != 1
                or type(event['pid']) is not int or event['pid'] <= 0
                or event['state'] not in ('open', 'closed') or event['reason'] not in REASONS
                or not isinstance(event['request'], str) or not re.fullmatch('[0-9a-f]{32}', event['request'])
                or not isinstance(event['directory'], str) or len(event['directory']) > 2048):
            return None
        return event
    except (ValueError, TypeError, KeyError):
        return None


def _json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def _keeps_password(tree, command):
    """An ordinary Next tap or Back/Enter key does not edit the focused value."""
    kind = command.get('action')
    if kind == 'key' and command.get('key') in ('4', '66'):
        return True
    if kind != 'tap':
        return False
    x, y = command.get('x'), command.get('y')
    if type(x) is not int or type(y) is not int:
        return False
    labels = {'weiter', 'next', 'continue', 'registrieren', 'sign up', 'konto erstellen',
              'get started', 'anmelden', 'log in', 'bestätigen', 'confirm'}
    parents = {child: parent for parent in tree.iter() for child in parent}
    for node in tree.iter('node'):
        package = node.get('package')
        if package not in ('de.web.mobile.android.mail', 'com.zhiliaoapp.musically'):
            continue
        if not any((node.get(key) or '').strip().casefold() in labels for key in ('text', 'content-desc')):
            continue
        label_rectangle = GRENZE.fullmatch(node.get('bounds') or '')
        if label_rectangle is None:
            continue
        left, top, right, bottom = map(int, label_rectangle.groups())
        if not (left <= x < right and top <= y < bottom):
            continue
        current = node
        while current is not None and current.get('package') == package:
            if current.get('clickable') == 'true' or current.get('class') == 'android.widget.Button':
                rectangle = GRENZE.fullmatch(current.get('bounds') or '')
                if rectangle:
                    x1, y1, x2, y2 = map(int, rectangle.groups())
                    if x1 <= x < x2 and y1 <= y < y2:
                        return True
                break
            current = parents.get(current)
    return False


class ManualHandoff:
    def __init__(self, adb, ctx, cfg):
        self.adb, self.ctx = adb, ctx
        self.session = getattr(adb, '_socialscraper_avd_session', None)
        if self.session is None or self.session.adb is not adb:
            raise FinalError('Manuelle Eingaben benötigen eine eigene geprüfte AVD-Sitzung.')
        self.base = Path(cfg.profiles_dir).resolve().parent / 'android-sessions'
        self.frames = {}
        self.message = 'waiting'
        self.deadline = time.monotonic() + positive_timeout(ctx.timeout)
        self.adb._interaction_deadline = self.deadline

    def _check(self):
        check_cancelled(getattr(self.ctx, 'stop', None))
        self.session.check()

    def _emit(self, state):
        self.ctx.melden(PREFIX + json.dumps({'version': 1, 'pid': os.getpid(), 'state': state,
            'directory': str(self.folder), 'request': self.request, 'reason': self.reason}))

    def _capture(self):
        self._check()
        frame = self.adb._cmd('exec-out', 'screencap', '-p', roh=True, timeout=10)
        width, height = screen_size(frame)
        now = time.monotonic()
        frame_id = uuid.uuid4().hex
        self.frames = {key: value for key, value in self.frames.items() if now - value[0] <= 10}
        self.frames[frame_id] = now, width, height
        temporary = self.folder / 'frame.tmp'
        temporary.write_bytes(frame)
        temporary.replace(self.folder / 'frame.png')
        _json(self.folder / 'view.json', {'version': 1, 'request': self.request,
            'frame': frame_id, 'captured_at': now, 'width': width, 'height': height,
            'sha256': hashlib.sha256(frame).hexdigest(), 'message': self.message,
            'actions': sorted(self.actions), 'instructions': self.instructions})

    def _ready(self, tree=None):
        self._check()
        try:
            tree = self.adb.baum(timeout=5) if tree is None else tree
            ready = self.predicate(tree)
        except AdbError:
            self._check()
            self.message = 'unreadable'
            return False
        self._check()
        if type(ready) is not bool:
            raise FinalError('Der manuelle Abschluss liefert keinen eindeutigen Zustand.')
        return ready

    def _apply(self, command):
        self._check()
        if not isinstance(command, dict) or command.get('request') != self.request:
            raise InputRejected('Falscher manueller Auftrag.')
        frame_id = command.get('frame')
        frame = self.frames.get(frame_id) if isinstance(frame_id, str) else None
        if frame is None or not 0 <= time.monotonic() - frame[0] <= 10:
            raise InputRejected('Veraltetes Android-Bild.')
        _, width, height = frame
        if command.get('size') != [width, height]:
            raise InputRejected('Bildschirmgröße geändert.')
        kind = command.get('action')
        if not isinstance(kind, str):
            raise InputRejected('Ungültige Android-Aktion.')
        if kind == 'continue':
            self.continue_requested = True
            self.frames.clear()
            return
        try:
            tree = self.adb.baum(timeout=5)
        except AdbError:
            raise InputRejected('Aktueller Android-Zustand nicht lesbar.') from None
        # A resolved challenge must not receive a late click intended for its
        # previous screen, even if the next observation will resume the flow.
        if self._ready(tree):
            raise InputRejected('Der Schritt ist bereits erledigt; die Fortsetzung wird geprüft.')
        def point(x, y):
            if type(x) is not int or type(y) is not int or not (0 <= x < width and 0 <= y < height):
                raise InputRejected('Ungültige Berührungsposition.')
            return x, y
        self.frames.clear()  # queued inputs from before this action become stale
        self.message = 'waiting'
        if kind in ('tap', 'swipe', 'key', 'text'):
            # Never silently retain a stored password after manual edits to an
            # observed password field. The final save checks this flag.
            focused = [node for node in tree.iter('node') if node.get('focused') == 'true']
            if kind == 'text' and (not focused or any(node.get('password') == 'true' for node in focused)):
                raise InputRejected('Textfeld nicht eindeutig oder Kennwortfeld geöffnet.')
            if any(node.get('password') == 'true' for node in focused) and not _keeps_password(tree, command):
                self.ctx.android_credentials_uncertain = True
        if not 0 <= time.monotonic() - frame[0] <= 10:
            raise InputRejected('Android-Bild ist während der Zustandsprüfung veraltet.')
        if kind in self.actions:
            if kind == 'fill':
                self.message = 'working'
                self._capture()
                self.frames.clear()
            self.actions[kind](tree, command)
            self.message = 'waiting'
        elif kind == 'tap':
            self.adb.tap(*point(command.get('x'), command.get('y')))
        elif kind == 'swipe':
            start = point(command.get('x1'), command.get('y1'))
            end = point(command.get('x2'), command.get('y2'))
            duration = command.get('ms')
            if type(duration) is not int or not 100 <= duration <= 2000:
                raise InputRejected('Ungültige Gestendauer.')
            self.adb.swipe(*start, *end, ms=duration)
        elif kind == 'key' and command.get('key') in ('4', '66', '67'):
            self.adb.taste(command['key'])
        elif kind == 'text':
            value = command.get('value')
            if not isinstance(value, str) or not 1 <= len(value) <= 256 or any(not 32 <= ord(char) <= 126 for char in value):
                raise InputRejected('Nicht unterstützte Texteingabe.')
            self.adb.tippen(value)
        else:
            raise InputRejected('Unbekannte Android-Eingabe.')

    def wait(self, reason, predicate, *, actions=None, instructions='', resume_when_ready=False):
        if reason not in REASONS or not callable(predicate):
            raise ValueError('Unbekannter manueller Android-Schritt.')
        self._check()
        from .avd import _plain
        _plain(self.base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.reason, self.predicate = reason, predicate
        self.actions = dict(actions or {})
        if (set(self.actions) - {'fill', 'confirm_password'}
                or not all(callable(callback) for callback in self.actions.values())
                or not isinstance(instructions, str) or len(instructions) > 1500):
            raise ValueError('Ungültige Android-Rückfrage.')
        self.instructions = instructions
        self.request = uuid.uuid4().hex
        self.frames.clear()
        self.message, self.continue_requested = 'waiting', False
        deadline = self.deadline
        if time.monotonic() >= deadline:
            raise FinalError('Das Zeitlimit der Android-Registrierung ist erreicht; keine weitere Rückfrage gestartet.')
        with tempfile.TemporaryDirectory(prefix='session-', dir=self.base) as folder:
            self.folder = Path(folder).resolve()
            inputs = self.folder / 'inputs'
            inputs.mkdir()
            _json(self.folder / 'owner.json', {'version': 1, 'pid': os.getpid(), 'request': self.request})
            self._capture()
            from ..registration_progress import update
            update(self.ctx, 'waiting_confirmation')
            self.ctx.melden(REASONS[reason] + ' Automatische Eingaben pausieren.')
            self._emit('open')
            try:
                while time.monotonic() < deadline:
                    self._check()
                    def rejected(_message):
                        self.message = 'input_rejected'
                    try:
                        consume_inputs(inputs, self._apply, rejected)
                    except AdbError:
                        self.message = 'input_rejected'
                    if self.continue_requested or resume_when_ready:
                        explicit = self.continue_requested
                        self.continue_requested = False
                        if self._ready():
                            return
                        if explicit and self.message != 'unreadable':
                            self.message = 'pending'
                    self._capture()
                    wait(.4, getattr(self.ctx, 'stop', None))
                self._check()
                raise FinalError('Die manuelle Android-Bestätigung wurde nicht rechtzeitig abgeschlossen; kein Konto bestätigt.')
            finally:
                self.frames.clear()
                self._emit('closed')
                update(self.ctx, 'form')
