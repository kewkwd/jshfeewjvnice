"""Bausteine für Formulare (Einstellungen, Automatik): Scrollbereich, Listen-Editor, Dienstleiste, Hintergrundarbeit.

Nur Tk-Widgets und Kleinkram; die Logik liegt in socialscraper/formlogic.py und autologic.py.
"""
from .background import run_background
import tkinter as tk
from tkinter import messagebox, ttk

from .. import autologic
from .common import COLORS, FONT_B, TITLE, track_after
from .widgets import HelpDot

H1 = ('Segoe UI', 13, 'bold')
HINT_WRAP = 520


def run_async(widget, work, done, interval=40, daemon=True):
    """Run work off Tk; daemon=False lets pending file writes finish on close."""
    run_background(widget, work, done, interval=interval, daemon=daemon, schedule=track_after)


def friendly(error):
    """Meldung ohne Stacktrace für beliebige Ausnahmen."""
    if isinstance(error, (ValueError, RuntimeError)):
        return str(error)
    if isinstance(error, PermissionError):
        return 'Zugriff verweigert (Datei gesperrt oder kein Recht).'
    if isinstance(error, OSError):
        return f'Dateifehler ({error.strerror or "Fehler"}).'
    return 'Das hat nicht geklappt (unerwarteter Fehler, Details im Protokoll).'


class ScrollFrame(ttk.Frame):
    """Senkrecht scrollbarer Bereich. Inhalt kommt in `.body`; `.on_width(w)` meldet die nutzbare Breite."""

    def __init__(self, parent, on_width=None):
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        from .theme import PALETTE
        self.canvas.configure(background=PALETTE['surface'])
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.bar.grid(row=0, column=1, sticky='ns')
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self._on_width = on_width
        self.body.bind('<Configure>', self._body_changed)
        self.canvas.bind('<Configure>', self._canvas_changed)
        self.canvas.bind('<Enter>', self._enter)
        self.canvas.bind('<Leave>', self._leave)
        self.body.bind('<Enter>', self._enter)
        self.body.bind('<Leave>', self._leave)

    def _body_changed(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        self._toggle_bar()

    def _canvas_changed(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        if self._on_width:
            self._on_width(event.width)
        self._toggle_bar()

    def _toggle_bar(self):
        try:
            need = self.body.winfo_reqheight() > self.canvas.winfo_height()
        except tk.TclError:
            return
        # Hidden notebook tabs are never mapped, even when the scrollbar is
        # already managed by grid. Re-gridding there can keep geometry events
        # running indefinitely before the main window becomes visible.
        gridded = self.bar.winfo_manager() == 'grid'
        if need and not gridded:
            self.bar.grid()
        elif not need and gridded:
            self.bar.grid_remove()
            self.canvas.yview_moveto(0)

    def _enter(self, _event=None):
        self.canvas.bind_all('<MouseWheel>', self._wheel)

    def _leave(self, event=None):
        try:
            x, y = self.winfo_pointerxy()
            if self.winfo_containing(x, y) is not None and str(self.winfo_containing(x, y)).startswith(str(self)):
                return
        except (tk.TclError, KeyError):
            pass
        self.canvas.unbind_all('<MouseWheel>')

    def _wheel(self, event):
        if event.widget.winfo_class() in ('Text', 'Treeview', 'Listbox', 'TCombobox', 'TSpinbox'):
            return  # Let the focused list/input scroll without moving its page too.
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-2 if event.delta > 0 else 2, 'units')

    def see(self, widget):
        """Scrollt, bis das Widget sichtbar ist (für Tastaturbedienung)."""
        try:
            self.update_idletasks()
            top = widget.winfo_rooty() - self.body.winfo_rooty()
            bottom = top + widget.winfo_height()
            total = max(1, self.body.winfo_height())
            view_h = self.canvas.winfo_height()
            offset = self.canvas.canvasy(0)
            if top < offset:
                self.canvas.yview_moveto(max(0, top - 8) / total)
            elif bottom > offset + view_h:
                self.canvas.yview_moveto(min(total, bottom - view_h + 8) / total)
        except tk.TclError:
            pass

    def to_top(self):
        self.canvas.yview_moveto(0)


class StringListEditor(ttk.Frame):
    """Liste von Texten mit Eingabefeld, Hinzufügen, Entfernen und Formatprüfung beim Tippen."""

    def __init__(self, parent, validate, display=None, example='', on_change=None, height=4, add_text='Hinzufügen'):
        super().__init__(parent)
        self._validate, self._display = validate, display or (lambda v: v)
        self._items, self._on_change = [], on_change
        self.columnconfigure(0, weight=1)
        box = ttk.Frame(self)
        box.grid(row=0, column=0, sticky='ew')
        box.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(box, height=height, activestyle='dotbox', exportselection=False, relief='solid', borderwidth=1)
        sb = ttk.Scrollbar(box, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.grid(row=0, column=0, sticky='ew')
        sb.grid(row=0, column=1, sticky='ns')
        self.remove_btn = ttk.Button(self, text='Entfernen', command=self.remove_selected)
        self.remove_btn.grid(row=0, column=1, sticky='n', padx=(8, 0))
        self.listbox.bind('<Delete>', lambda _e: self.remove_selected())
        self.entry_var = tk.StringVar()
        row = ttk.Frame(self)
        row.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        row.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(row, textvariable=self.entry_var)
        self.entry.grid(row=0, column=0, sticky='ew')
        self.add_btn = ttk.Button(row, text=add_text, command=self.add_from_entry)
        self.add_btn.grid(row=0, column=1, padx=(8, 0))
        self.entry.bind('<Return>', lambda _e: self.add_from_entry())
        self.check_label = ttk.Label(self, text=example, foreground=COLORS['muted'], wraplength=HINT_WRAP, justify='left')
        self.check_label.grid(row=2, column=0, columnspan=2, sticky='w', pady=(2, 0))
        self._example = example
        self.entry_var.trace_add('write', lambda *_: self._live())

    def _live(self):
        text = self.entry_var.get()
        if not text.strip():
            self.check_label.configure(text=self._example, foreground=COLORS['muted'])
            return
        _value, error = self._validate(text)
        if error:
            self.check_label.configure(text=error, foreground=COLORS['err'])
        else:
            self.check_label.configure(text='✔ Format passt – mit „Hinzufügen“ übernehmen.', foreground=COLORS['ok'])

    def set_wrap(self, width):
        self.check_label.configure(wraplength=max(240, width))

    def get(self):
        return list(self._items)

    def set(self, items):
        self._items = list(items)
        self.listbox.delete(0, 'end')
        for item in self._items:
            self.listbox.insert('end', self._display(item))

    def add_from_entry(self):
        text = self.entry_var.get()
        if not text.strip():
            return False
        value, error = self._validate(text)
        if error:
            self.check_label.configure(text=error, foreground=COLORS['err'])
            self.entry.focus_set()
            return False
        if value in self._items:
            self.check_label.configure(text='Diesen Eintrag gibt es schon.', foreground=COLORS['err'])
            return False
        self._items.append(value)
        self.listbox.insert('end', self._display(value))
        self.entry_var.set('')
        self._changed()
        return True

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        for index in reversed(sel):
            del self._items[index]
            self.listbox.delete(index)
        self._changed()

    def _changed(self):
        if self._on_change:
            self._on_change()


class ServicePanel(ttk.LabelFrame):
    """Status und Start/Stopp des Hintergrunddienstes (gemeinsam für Einstellungen und Automatik)."""

    def __init__(self, parent, root_fn, targets_fn=None, with_autostart=False, on_change=None, text='Hintergrunddienst'):
        super().__init__(parent, text=text, padding=10)
        self._root_fn, self._targets_fn, self._on_change = root_fn, targets_fn or (lambda: []), on_change
        self.columnconfigure(0, weight=1)
        title_row = ttk.Frame(self)
        title_row.grid(row=0, column=0, sticky='w')
        self.state_label = ttk.Label(title_row, text='…', font=FONT_B)
        self.state_label.pack(side='left')
        HelpDot(title_row, 'daemon').pack(side='left', padx=(6, 0))
        self.detail = ttk.Label(self, text='', foreground=COLORS['muted'], wraplength=HINT_WRAP, justify='left')
        self.detail.grid(row=1, column=0, sticky='w', pady=(2, 0))
        btns = ttk.Frame(self)
        btns.grid(row=0, column=1, rowspan=2, sticky='e', padx=(10, 0))
        self.start_btn = ttk.Button(btns, text='Dienst starten', command=self.start)
        self.stop_btn = ttk.Button(btns, text='Dienst stoppen', command=self.stop)
        self.start_btn.pack(side='left')
        self.stop_btn.pack(side='left', padx=(6, 0))
        self.autostart = tk.BooleanVar(value=False)
        self.autostart_cb = None
        self._autostart_busy = False
        if with_autostart:
            self.autostart_cb = ttk.Checkbutton(self, text='Dienst automatisch mit Windows starten  ', variable=self.autostart,
                                                command=self._toggle_autostart)
            self.autostart_cb.grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))
            self.auto_hint = ttk.Label(self, text='Legt nur eine Verknüpfung im Autostart-Ordner deines Benutzers an – '
                                                  'keine Systemeinstellung wird geändert. Wirkt sofort, ohne „Speichern“.',
                                       foreground=COLORS['muted'], wraplength=HINT_WRAP, justify='left')
            self.auto_hint.grid(row=3, column=0, columnspan=2, sticky='w')
        self.busy = False
        self.last_status = None
        self.last_message = ''

    def compact(self, yes):
        """Im niedrigen Fenster nur eine Zeile: Zustand und Knöpfe, ohne die Erklärzeile darunter."""
        if bool(yes) == getattr(self, '_compact', False):
            return
        self._compact = bool(yes)
        (self.detail.grid_remove if yes else self.detail.grid)()
        self.configure(padding=(10, 2) if yes else (10, 6))

    def set_wrap(self, width):
        for label in (self.detail, getattr(self, 'auto_hint', None)):
            if label is not None:
                label.configure(wraplength=max(240, width))

    def refresh(self):
        root = self._root_fn()
        status = autologic.service_status(root)
        self.last_status = status
        if self.busy:
            return   # „Dienst startet/stoppt …“ bleibt stehen, bis die Arbeit fertig ist (die Tabs rufen refresh alle 5 s auf)
        color, text, detail = autologic.describe_service(status, self._targets_fn())
        self.state_label.configure(text=text, foreground=COLORS[color])
        self.detail.configure(text=detail)
        unavailable = bool(status.get('unavailable'))
        running = bool(status.get('running'))
        self.start_btn.configure(state='disabled' if running or unavailable else 'normal')
        self.stop_btn.configure(state='normal' if running else 'disabled')
        if self.autostart_cb is not None and not self._autostart_busy:
            try:
                from .. import daemon
                self.autostart.set(bool(daemon.autostart_enabled()))
                self.autostart_cb.configure(state='normal')
            except Exception:
                self.autostart_cb.configure(state='disabled')

    def _set_busy(self, text):
        self.busy = bool(text)
        if text:
            self.state_label.configure(text=text, foreground=COLORS['warn'])
            self.start_btn.configure(state='disabled')
            self.stop_btn.configure(state='disabled')

    def start(self):
        try:
            from .. import daemon
        except ImportError:
            messagebox.showinfo(TITLE, 'Funktion noch nicht verfügbar: Der Hintergrunddienst fehlt in dieser Installation.')
            return
        root = self._root_fn()
        self._set_busy('Dienst startet …')
        run_async(self, lambda: daemon.start_detached(root), self._started)

    def _started(self, _result, error):
        self.busy = False
        self.last_message = friendly(error) if error else 'Dienst gestartet.'
        if error:
            messagebox.showerror(TITLE, self.last_message)
        self.refresh()
        if self._on_change:
            self._on_change(not error, self.last_message)

    def stop(self, ask=True):
        try:
            from .. import daemon
        except ImportError:
            return
        if ask and not messagebox.askyesno(TITLE, 'Hintergrunddienst beenden?\n\nEin laufender Abruf bekommt bis zu 30 Sekunden, um sauber zu enden; '
                                                  'danach wird er beendet und als unterbrochen vermerkt. '
                                                  'Danach finden keine automatischen Abrufe mehr statt.'):
            return
        root = self._root_fn()
        self._set_busy('Dienst stoppt … (kann bis zu einer halben Minute dauern)')
        run_async(self, lambda: daemon.stop(root), self._stopped)

    def _stopped(self, result, error):
        self.busy = False
        if error:
            self.last_message = friendly(error)
            messagebox.showerror(TITLE, self.last_message)
        elif result is False:
            self.last_message = 'Der Dienst reagiert nicht und läuft noch. Beende ihn im Task-Manager (Prozess „pythonw.exe“).'
            messagebox.showwarning(TITLE, self.last_message)
        else:
            self.last_message = 'Dienst gestoppt.'
        self.refresh()
        if self._on_change:
            self._on_change(not error and result is not False, self.last_message)

    def _toggle_autostart(self):
        wanted = bool(self.autostart.get())
        try:
            from .. import daemon
        except ImportError:
            self.autostart.set(not wanted)
            messagebox.showinfo(TITLE, 'Funktion noch nicht verfügbar.')
            return
        root = self._root_fn()
        self._autostart_busy = True
        self.autostart_cb.configure(state='disabled')

        def done(result, error):
            self._autostart_busy = False
            if error:
                self.autostart.set(not wanted)
                self.last_message = friendly(error)
                messagebox.showerror(TITLE, 'Der automatische Start ließ sich nicht ändern.\n\n' + self.last_message)
            else:
                self.last_message = 'Der Dienst startet jetzt mit Windows.' if wanted else 'Der Dienst startet nicht mehr mit Windows.'
            self.refresh()
            if self._on_change:
                self._on_change(not error, self.last_message)

        run_async(self, lambda: daemon.set_autostart(wanted, root), done)

