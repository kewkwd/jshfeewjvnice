"""On-demand device view for a paused owned AVD registration."""
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid
import tkinter as tk
from tkinter import simpledialog, ttk

from ..android.handoff import MESSAGES, REASONS
from ..net.errors import FinalError
from .hintergrund import cfg_of


def validate(app, event):
    process = getattr(getattr(app, 'job', None), 'proc', None)
    if process is None or process.poll() is not None:
        return False
    from ..net.deadline import descendants
    from ..android.avd import _plain
    allowed = {process.pid, *[pid for pid, _name in descendants(process.pid)]}
    base = _plain(Path(cfg_of(app).profiles_dir).resolve().parent / 'android-sessions')
    folder = _plain(event['directory'])
    if event['pid'] not in allowed or folder.parent != base or not re.fullmatch(r'session-[\w-]+', folder.name):
        return False
    owner_path = _plain(folder / 'owner.json')
    if owner_path.stat().st_size > 512:
        return False
    owner = json.loads(owner_path.read_text(encoding='utf-8'))
    return owner == {'version': 1, 'pid': event['pid'], 'request': event['request']}


def cleanup(app, event):
    """Remove only a validated direct worker's temporary files after it exits."""
    process = getattr(getattr(app, 'job', None), 'proc', None)
    if process is None or process.pid != event['pid'] or process.poll() is None:
        return
    from ..android.avd import _plain
    base = _plain(Path(cfg_of(app).profiles_dir).resolve().parent / 'android-sessions')
    folder = _plain(event['directory'])
    if not folder.exists():
        return
    if folder.parent != base or not re.fullmatch(r'session-[\w-]+', folder.name):
        raise ValueError('Ungültiger temporärer Geräteordner.')
    owner_path = _plain(folder / 'owner.json')
    if owner_path.stat().st_size > 512:
        raise ValueError('Ungültiger Gerätebesitz.')
    owner = json.loads(owner_path.read_text(encoding='utf-8'))
    if owner != {'version': 1, 'pid': event['pid'], 'request': event['request']}:
        raise ValueError('Abweichender Gerätebesitz.')
    inputs = _plain(folder / 'inputs')
    if inputs.is_dir():
        for path in inputs.iterdir():
            if not re.fullmatch(r'\d{20}-[0-9a-f]{32}\.(json|tmp)', path.name):
                raise ValueError('Unbekannte temporäre Eingabedatei.')
            _plain(path).unlink()
        inputs.rmdir()
    for name in ('frame.png', 'frame.tmp', 'view.json', 'view.tmp'):
        _plain(folder / name).unlink(missing_ok=True)
    # Keep the ownership proof if an unknown file prevents removing the folder.
    if {path.name for path in folder.iterdir()} != {'owner.json'}:
        raise ValueError('Unbekannte Dateien im temporären Geräteordner.')
    owner_path.unlink()
    folder.rmdir()


class AvdHandoffDialog(tk.Toplevel):
    def __init__(self, app, event):
        if not validate(app, event):
            raise ValueError('Die Android-Rückfrage gehört nicht zum laufenden Auftrag.')
        super().__init__(app)
        self.app, self.event = app, event
        self.directory = Path(event['directory']).resolve()
        self.request = event['request']
        self.timer = self.photo = self.original = self.frame = self.press = None
        self.factor = 1
        self.display_ready = False
        self.sent_frame = None
        self.available_actions = set()
        self.title('Profilwerk – Deine Eingabe ist erforderlich')
        self.transient(app)
        self.minsize(360, 500)
        self.geometry(f'530x{min(850, max(500, self.winfo_screenheight() - 130))}')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self, padding=12)
        header.grid(row=0, column=0, sticky='ew')
        title = ttk.Label(header, text=REASONS[event['reason']], wraplength=480,
                          font=('Segoe UI', 12, 'bold'))
        title.pack(anchor='w')
        header.bind('<Configure>', lambda e: title.configure(wraplength=max(260, e.width - 24)))
        ttk.Label(header, text='Der Auftrag pausiert. Du bedienst hier das laufende Gerät.',
                  wraplength=480, style='Muted.TLabel').pack(anchor='w', pady=(5, 0))
        if event['reason'] == 'birthday':
            ttk.Label(header, text='Das hinterlegte Datum steht im Auftragsprotokoll.',
                      style='Muted.TLabel').pack(anchor='w', pady=(4, 0))
        self.instructions = ttk.Label(header, text='', wraplength=480, style='Muted.TLabel')
        self.instructions.pack(anchor='w', pady=(4, 0))
        self.message = ttk.Label(self, text=MESSAGES['waiting'], wraplength=480, padding=(12, 4))
        self.message.grid(row=1, column=0, sticky='ew')
        self.canvas = tk.Canvas(self, background='#17212c', highlightthickness=0)
        self.canvas.grid(row=2, column=0, sticky='nsew', padx=12, pady=7)
        self.image_id = self.canvas.create_image(0, 0, anchor='nw')
        self.canvas.bind('<Configure>', lambda _e: self._render())
        self.canvas.bind('<ButtonPress-1>', self._down)
        self.canvas.bind('<ButtonRelease-1>', self._up)
        controls = ttk.Frame(self, padding=(10, 4))
        controls.grid(row=3, column=0, sticky='ew')
        self.input_buttons = []
        for index, (label, command) in enumerate((
                ('Zurück', lambda: self.send('key', key='4')),
                ('Text eingeben', self.text), ('Löschen', lambda: self.send('key', key='67')),
                ('Enter', lambda: self.send('key', key='66')))):
            controls.columnconfigure(index, weight=1)
            button = ttk.Button(controls, text=label, command=command)
            button.grid(row=0, column=index, sticky='ew', padx=2)
            self.input_buttons.append(button)
        footer = ttk.Frame(self, padding=12)
        footer.grid(row=4, column=0, sticky='ew')
        footer.columnconfigure(1, weight=1)
        ttk.Button(footer, text='Ausblenden', command=self.withdraw).grid(row=0, column=0, padx=(0, 6))
        self.continue_button = ttk.Button(footer, text='Weiter prüfen', style='Primary.TButton',
                                          command=lambda: self.send('continue'))
        self.continue_button.grid(row=0, column=1, sticky='ew')
        self.input_buttons.append(self.continue_button)
        self.fill_button = ttk.Button(footer, text='Gespeicherte Felder eintragen', command=lambda: self.send('fill'))
        self.fill_button.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(7, 0))
        self.confirm_button = ttk.Button(footer, text='Verwendetes Kennwort bestätigen', command=self.confirm_password)
        self.confirm_button.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(7, 0))
        self.input_buttons.extend((self.fill_button, self.confirm_button))
        ttk.Button(footer, text='Auftrag abbrechen', command=app.cancel).grid(row=3, column=0, columnspan=2, pady=(8, 0))
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self.refresh()
        self.lift()
        from ..window_notification import window_opened
        window_opened(self)

    def _render(self):
        if self.original is None or self.press is not None:
            return
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        self.factor = max(1, math.ceil(self.original.width() / width), math.ceil(self.original.height() / height))
        self.photo = self.original.subsample(self.factor)
        self.canvas.itemconfigure(self.image_id, image=self.photo)

    def _down(self, event):
        if (self.display_ready and self.photo is not None
                and 0 <= event.x < self.photo.width() and 0 <= event.y < self.photo.height()):
            self.press = (int(event.x * self.factor), int(event.y * self.factor), time.monotonic(), self.frame)

    def _up(self, event):
        press, self.press = self.press, None
        if press is None or self.photo is None:
            return
        x, y = int(event.x * self.factor), int(event.y * self.factor)
        x1, y1, started, frame = press
        if not 0 <= x < frame['width'] or not 0 <= y < frame['height']:
            return
        elapsed = time.monotonic() - started
        if elapsed > 2:
            self.message.configure(text='Bitte Gesten innerhalb von zwei Sekunden ausführen.')
            return
        if abs(x - x1) + abs(y - y1) <= 10 and elapsed < .5:
            self.send('tap', frame=frame, x=x, y=y)
        else:
            self.send('swipe', frame=frame, x1=x1, y1=y1, x2=x, y2=y, ms=max(100, int(elapsed * 1000)))

    def send(self, action, frame=None, **values):
        frame = frame or self.frame
        if not self.display_ready or frame is None or not self.directory.is_dir():
            return
        if action in ('fill', 'confirm_password') and action not in self.available_actions:
            return
        if not 0 <= time.monotonic() - frame['captured_at'] <= 10 or self.sent_frame == frame['frame']:
            return
        inputs = self.directory / 'inputs'
        from ..android.avd import _plain
        path = inputs / f'{time.time_ns():020d}-{uuid.uuid4().hex}.json'
        temporary = path.with_suffix('.tmp')
        try:
            _plain(inputs)
            command = {'request': self.request, 'frame': frame['frame'],
                       'size': [frame['width'], frame['height']], 'action': action, **values}
            if action in ('text', 'confirm_password'):
                from ..accountvault import _write
                _write(path, command)
            else:
                temporary.write_text(json.dumps(command), encoding='utf-8')
                temporary.replace(path)
        except (OSError, FinalError):
            self.message.configure(text='Die Rückfrage ist nicht mehr verfügbar. Bitte den Auftragsstatus prüfen.')
            return
        self.sent_frame = frame['frame']
        self.display_ready = False
        for button in self.input_buttons:
            button.state(['disabled'])

    def text(self):
        value = simpledialog.askstring('Android-Eingabe', 'Text für das ausgewählte Feld:', show='*', parent=self)
        if value is not None:
            self.send('text', value=value)

    def confirm_password(self):
        if 'confirm_password' not in self.available_actions:
            return
        value = simpledialog.askstring('WEB.DE-Zugang speichern',
            'Die Zieladresse ist in der App bestätigt. Welches Kennwort hast du tatsächlich verwendet?',
            show='*', parent=self)
        if value is not None:
            self.send('confirm_password', value=value)

    def refresh(self):
        try:
            if self.press is None:
                from ..android.avd import _plain
                view_path = _plain(self.directory / 'view.json')
                if view_path.stat().st_size > 8192:
                    raise ValueError('Ungültige Ansicht.')
                view = json.loads(view_path.read_text(encoding='utf-8'))
                if not isinstance(view, dict):
                    raise ValueError('Ungültige Ansicht.')
                captured = view.get('captured_at')
                if (view.get('version') != 1 or view.get('request') != self.request
                        or type(captured) not in (int, float) or not math.isfinite(captured)
                        or not 0 <= time.monotonic() - captured <= 10
                        or type(view.get('width')) is not int or type(view.get('height')) is not int
                        or not 100 <= view['width'] <= 8192 or not 100 <= view['height'] <= 8192
                        or not isinstance(view.get('frame'), str) or not re.fullmatch('[0-9a-f]{32}', view['frame'])
                        or view.get('message') not in MESSAGES
                        or not isinstance(view.get('actions'), list)
                        or any(not isinstance(action, str) or action not in ('fill', 'confirm_password') for action in view['actions'])
                        or not isinstance(view.get('instructions'), str) or len(view['instructions']) > 1500):
                    raise ValueError('Veraltete Ansicht.')
                path = _plain(self.directory / 'frame.png')
                if path.stat().st_size > 16_000_000:
                    raise ValueError('Android-Bild zu groß.')
                frame = path.read_bytes()
                if hashlib.sha256(frame).hexdigest() != view.get('sha256'):
                    raise ValueError('Bild wird gerade aktualisiert.')
                photo = tk.PhotoImage(data=base64.b64encode(frame))
                if (photo.width(), photo.height()) != (view['width'], view['height']):
                    raise ValueError('Bildschirmgröße geändert.')
                self.frame, self.original = view, photo
                self.available_actions = set(view['actions'])
                self.instructions.configure(text=view['instructions'])
                self._render()
                self.display_ready = self.sent_frame != view['frame'] and view['message'] != 'working'
                self.message.configure(text=MESSAGES[view['message']])
        except (OSError, ValueError, TypeError, KeyError, FinalError, tk.TclError):
            self.display_ready = False
            self.message.configure(text='Warte auf ein aktuelles Android-Bild …')
        for button in self.input_buttons:
            button.state(['!disabled'] if self.display_ready else ['disabled'])
        for action, button in (('fill', self.fill_button), ('confirm_password', self.confirm_button)):
            if action in self.available_actions:
                button.grid()
            else:
                button.grid_remove()
        if self.directory.is_dir():
            self.timer = self.after(300, self.refresh)
        else:
            self.display_ready = False
            for button in self.input_buttons:
                button.state(['disabled'])

    def destroy(self):
        if self.timer is not None:
            try:
                self.after_cancel(self.timer)
            except tk.TclError:
                pass
            self.timer = None
        self.photo = self.original = None
        super().destroy()
