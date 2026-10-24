"""Fenster „Stimmung bestimmen“: Umfang wählen, Schätzung sehen, bestätigen, Fortschritt und Abbrechen.

Die Bestimmung läuft in einem Hintergrund-Thread im selben Programm (kein Auftrag über run_steps: der Fortschritt
wird gebraucht, und es wird kein Browser benutzt). Das Ergebnis ist eine KI-Einschätzung (Schätzung, nicht gemessen).
"""
import threading
import tkinter as tk
from tkinter import ttk

from .. import aiconfig, sentiment
from .common import COLORS, TITLE, track_after
from .logic import friendly_error
from .widgets import PAD, ask_form, pad_label, show_centered

PRIVACY = ('Gesendet wird nur der Text der Kommentare an die Anthropic-API (api.anthropic.com): ohne Autorennamen, ohne Nutzerkennungen, '
           'ohne Video-Links; Links und @Namen im Text werden ersetzt. Das kostet Geld bei Anthropic (dein Konto).')


def ask_for_key(parent, goto_settings):
    """Freundlicher Hinweis, wenn der API-Schlüssel fehlt, mit Knopf „Zu den Einstellungen“. Gibt True zurück, wenn der Knopf gedrückt wurde."""
    res = ask_form(parent, 'Stimmung bestimmen', [], ok_text='Zu den Einstellungen',
                   intro='Für die Stimmung braucht das Programm einen API-Schlüssel von Anthropic (Claude). Er ist noch nicht eingetragen.\n\n'
                         'Trage ihn einmal unter Einstellungen → KI / Stimmung ein (oder setze die Umgebungsvariable ANTHROPIC_API_KEY). '
                         'Danach kannst du die Stimmung hier bestimmen.')
    if res is not None:
        goto_settings()
        return True
    return False


class SentimentDialog(tk.Toplevel):
    """options: Liste von {'key', 'label', 'scope'}; der erste ist vorgewählt (oder `default`)."""

    def __init__(self, parent, app, options, db_path, root, on_done=None, default=None, modal=True, cfg=None):
        super().__init__(parent)
        self.withdraw()
        self.title('Stimmung bestimmen')
        self.resizable(False, False)
        self.app, self.options, self.db_path, self.root_dir = app, options, db_path, root
        self.on_done, self.modal = on_done, modal
        self.cfg = cfg if cfg is not None else aiconfig.load(root)
        self.state_name = 'idle'           # idle | running | done
        self.cancel_event = threading.Event()
        self.box = {}
        self.result = None
        self._thread = None
        body = ttk.Frame(self, padding=14)
        body.grid(row=0, column=0, sticky='nsew')
        ttk.Label(body, text='Die Claude-KI schätzt für jeden gespeicherten Kommentar, ob er positiv, neutral oder negativ ist. '
                             'Das Ergebnis ist eine KI-Einschätzung – eine Schätzung, nicht gemessen.',
                  wraplength=460, justify='left').grid(row=0, column=0, sticky='w')
        ttk.Label(body, text='Umfang', font=('Segoe UI', 10, 'bold')).grid(row=1, column=0, sticky='w', pady=(10, 2))
        self.scope_var = tk.StringVar(value=default or (options[0]['key'] if options else ''))
        self.radios = []
        for opt in options:
            r = ttk.Radiobutton(body, text=pad_label(opt['label']), value=opt['key'], variable=self.scope_var, command=self._estimate)
            r.grid(sticky='w', column=0)
            self.radios.append(r)
        self.redo_var = tk.BooleanVar(value=False)
        self.redo_cb = ttk.Checkbutton(body, text=pad_label('Auch schon eingestufte Kommentare noch einmal einstufen (kostet erneut)'),
                                       variable=self.redo_var, command=self._estimate)
        self.redo_cb.grid(sticky='w', column=0, pady=(6, 0))
        ttk.Label(body, text='Schätzung vor dem Start', font=('Segoe UI', 10, 'bold')).grid(sticky='w', column=0, pady=(10, 2))
        self.estimate_label = ttk.Label(body, text='', wraplength=460, justify='left')
        self.estimate_label.grid(sticky='w', column=0)
        ttk.Label(body, text=PRIVACY, wraplength=460, justify='left', foreground=COLORS['muted']).grid(sticky='w', column=0, pady=(8, 0))
        self.question = ttk.Label(body, text='Jetzt bestimmen?', font=('Segoe UI', 10, 'bold'))
        self.question.grid(sticky='w', column=0, pady=(12, 0))
        self.progress = ttk.Progressbar(body, mode='determinate', length=460, maximum=100)
        self.progress.grid(sticky='ew', column=0, pady=(6, 0))
        self.progress.grid_remove()
        self.progress_label = ttk.Label(body, text='', foreground=COLORS['muted'], wraplength=460, justify='left')
        self.progress_label.grid(sticky='w', column=0, pady=(2, 0))
        self.result_label = ttk.Label(body, text='', wraplength=460, justify='left')
        self.result_label.grid(sticky='w', column=0, pady=(4, 0))
        buttons = ttk.Frame(body)
        buttons.grid(sticky='e', column=0, pady=(12, 0))
        self.start_btn = ttk.Button(buttons, text='Jetzt bestimmen', style='Big.TButton', command=self.start)
        self.start_btn.pack(side='left', padx=(0, 8))
        self.cancel_btn = ttk.Button(buttons, text='Abbrechen', command=self.cancel)
        self.cancel_btn.pack(side='left')
        self.bind('<Escape>', lambda _e: self.cancel())
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self._estimate()
        if not modal:
            self.deiconify()
        if modal:
            self.transient(parent.winfo_toplevel())
            show_centered(self, parent)
            try:
                self.grab_set()
            except tk.TclError:
                pass
            self.start_btn.focus_set()
            self.wait_window()

    # ---------- Schätzung ----------

    def scope(self):
        return next((o['scope'] for o in self.options if o['key'] == self.scope_var.get()), {})

    def _estimate(self):
        if self.state_name != 'idle':
            return
        try:
            self.est = sentiment.estimate(self.db_path, self.scope(), self.cfg, redo=self.redo_var.get())
        except Exception as error:   # gesperrt o. Ä.: sagen, nicht abstürzen
            self.est = None
            self.estimate_label.configure(text='Die Schätzung ist gerade nicht möglich: ' + friendly_error(error), foreground=COLORS['err'])
            self.start_btn.configure(state='disabled')
            return
        lines = [f'Modell: {self.est["model"]}'] + sentiment.estimate_lines(self.est)
        self.estimate_label.configure(text='\n'.join(lines), foreground='#24292f')
        can = bool(self.est['to_send'])
        self.start_btn.configure(state='normal' if can else 'disabled')
        self.question.configure(text='Jetzt bestimmen?' if can else 'Hier gibt es nichts zu bestimmen.')

    # ---------- Lauf ----------

    def start(self):
        if self.state_name != 'idle' or not self.est or not self.est['to_send']:
            return
        self.state_name = 'running'
        for w in self.radios + [self.redo_cb]:
            w.configure(state='disabled')
        self.start_btn.configure(state='disabled')
        self.question.configure(text='Läuft … du kannst jederzeit abbrechen. Schon fertige Kommentare bleiben gespeichert.')
        self.progress.grid()
        self.progress.configure(value=0)
        self.progress_label.configure(text='Starte …')
        self.result_label.configure(text='')
        scope, cfg, redo = self.scope(), self.cfg, self.redo_var.get()
        self.box = {'progress': (0, max(1, self.est['to_send'])), 'done': False}
        self.cancel_event.clear()

        def progress(done, total, info):
            self.box['progress'] = (done, total)
            self.box['info'] = info

        def work():
            try:
                self.box['result'] = sentiment.run(self.db_path, scope, cfg=cfg, cancel=self.cancel_event, progress=progress, redo=redo)
            except Exception as error:   # run wirft sonst nie; trotzdem nie den Thread still sterben lassen
                self.box['result'] = {'status': 'failed', 'analyzed': 0, 'message': 'Unerwartet gescheitert: ' + type(error).__name__}
            self.box['done'] = True
        self._thread = threading.Thread(target=work, name='stimmung', daemon=True)
        self._thread.start()
        track_after(self, 100, self._poll)

    def _poll(self):
        done, total = self.box.get('progress', (0, 1))
        self.progress.configure(value=100 * done / max(total, 1))
        info = self.box.get('info') or {}
        self.progress_label.configure(text=f'{done} von {total} Kommentaren · {info.get("requests", 0)} Anfragen')
        if self.box.get('done'):
            self._finished(self.box['result'])
        else:
            track_after(self, 100, self._poll)

    def _finished(self, result):
        self.state_name = 'done'
        self.result = result
        ok = result['status'] in ('ok',)
        kind = 'ok' if ok else ('warn' if result['status'] in ('partial', 'cancelled') else 'err')
        self.progress.configure(value=100 if ok else self.progress['value'])
        self.question.configure(text='Fertig.' if ok else 'Beendet.')
        self.progress_label.configure(text='')
        self.result_label.configure(text=result['message'], foreground=COLORS[kind])
        self.start_btn.configure(state='disabled')
        self.cancel_btn.configure(text='Schließen', state='normal')   # nach „Abbrechen“ war der Knopf gesperrt
        if self.app is not None:
            self.app.log('Stimmung (KI-Einschätzung): ' + result['message'], {'ok': 'OK', 'warn': 'WARNING', 'err': 'ERROR'}[kind])
            self.app.set_status(result['message'][:160], kind)
        if self.on_done:
            self.on_done(result)

    def cancel(self):
        """Läuft die Bestimmung: sauber abbrechen (der Dialog bleibt, bis der Thread fertig ist). Sonst: schließen."""
        if self.state_name == 'running':
            self.cancel_event.set()
            self.cancel_btn.configure(state='disabled')
            self.progress_label.configure(text='Breche ab … der laufende Schritt wird noch beendet.')
            return
        try:
            self.destroy()
        except tk.TclError:
            pass
