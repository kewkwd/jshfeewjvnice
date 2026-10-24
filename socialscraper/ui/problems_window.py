"""Fenster „Probleme“: das, was zuletzt schiefging, in einfacher Sprache – mit „Dorthin“-Knopf, statt im Protokoll zu suchen.

Daten aus `socialscraper/problemlogic.py` (Läufe, Kanäle, Konten, Hintergrunddienst). Nicht modal: Marc kann es offen lassen und
im Programm weiterarbeiten. Das Lesen läuft im Hintergrund-Thread.
"""
import tkinter as tk
from tkinter import ttk

from .. import autologic as al
from .. import problemlogic, repo
from . import common
from .common import COLORS, FONT_B
from .forms import ScrollFrame, run_async
from .widgets import PAD, show_centered

KIND_NAME = {'run': 'Lauf', 'channel': 'Kanal', 'account': 'Konto', 'service': 'Dienst', 'job': 'Auftrag'}


class ProblemsWindow(tk.Toplevel):
    def __init__(self, app, items=None):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.title('Probleme – was zuletzt schiefging')
        self.geometry('760x500')
        self.minsize(560, 320)
        try:
            self.iconbitmap(default=str(common.ICON))
        except Exception:
            pass
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.items = []
        self._wrap = []
        head = ttk.Frame(self, padding=(PAD, PAD, PAD, 4))
        head.grid(row=0, column=0, sticky='ew')
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text='Das ist zuletzt schiefgegangen', font=('Segoe UI', 12, 'bold')).grid(row=0, column=0, sticky='w')
        self.info = ttk.Label(head, text='Wird geladen …', foreground=COLORS['muted'])
        self.info.grid(row=1, column=0, sticky='w')
        ttk.Button(head, text='Aktualisieren', command=self.reload).grid(row=0, column=1, rowspan=2, padx=(8, 0))
        self.scroll = ScrollFrame(self, on_width=self._on_width)
        self.scroll.grid(row=1, column=0, sticky='nsew', padx=PAD)
        self.scroll.body.columnconfigure(0, weight=1)
        foot = ttk.Frame(self, padding=PAD)
        foot.grid(row=2, column=0, sticky='ew')
        foot.columnconfigure(0, weight=1)
        ttk.Label(foot, text='Die genauen Meldungen stehen im Hauptfenster unter „Technische Details“.', foreground=COLORS['muted']
                  ).grid(row=0, column=0, sticky='w')
        self.close_btn = ttk.Button(foot, text='Schließen', style='Big.TButton', command=self.close)
        self.close_btn.grid(row=0, column=1)
        self.bind('<Escape>', lambda _e: self.close())
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.transient(app)
        show_centered(self, app)
        self.close_btn.focus_set()
        if items is not None:
            self.show(items)
        else:
            self.reload()

    def _on_width(self, width):
        wrap = max(260, width - 210)
        for label in self._wrap:
            try:
                label.configure(wraplength=wrap)
            except tk.TclError:
                pass

    def root(self):
        return getattr(self.app, 'root', None) or common.ROOT

    def reload(self):
        root = self.root()
        db = al.resolve_db(root)
        self.info.configure(text='Wird geladen …')
        run_async(self, lambda: problemlogic.collect(root, db), lambda result, error: self.show(result or [], error))

    def show(self, items, error=None):
        self.items = problemlogic.with_session_failures(items, getattr(self.app, '_job_problems', ()))
        for child in self.scroll.body.winfo_children():
            child.destroy()
        self._wrap = []
        if error is not None:
            self.info.configure(text='Die Liste ließ sich nicht lesen. Versuche es noch einmal.', foreground=COLORS['err'])
            if not self.items:return
        if not self.items:
            self.info.configure(text='In den letzten 7 Tagen ist nichts Auffälliges passiert.', foreground=COLORS['ok'])
            ttk.Label(self.scroll.body, text='✔ Keine Probleme.', font=FONT_B, foreground=COLORS['ok']).grid(row=0, column=0, sticky='w', pady=12)
            self._mark_seen()
            return
        if error is None:
            self.info.configure(text=f'{len(self.items)} Einträge aus den letzten 7 Tagen, neueste zuerst.', foreground=COLORS['muted'])
        now = repo.now()
        for number, item in enumerate(self.items):
            box = tk.Frame(self.scroll.body, bg='#ffffff', highlightthickness=1, highlightbackground='#d0d7de')
            box.grid(row=number, column=0, sticky='ew', pady=(0, 6))
            box.columnconfigure(1, weight=1)
            color = {'account': '#d4a72c', 'channel': '#d4a72c'}.get(item['kind'], '#cf222e')
            tk.Frame(box, bg=color, width=6).grid(row=0, column=0, rowspan=3, sticky='ns')
            when = problemlogic.when_text(item['time'], now)
            tk.Label(box, text=f'{when} · {KIND_NAME.get(item["kind"], "")}', bg='#ffffff', fg=COLORS['muted'], anchor='w',
                     font=('Segoe UI', 9)).grid(row=0, column=1, sticky='w', padx=10, pady=(6, 0))
            tk.Label(box, text=item['what'], bg='#ffffff', font=FONT_B, anchor='w', justify='left', wraplength=500
                     ).grid(row=1, column=1, sticky='w', padx=10)
            self._wrap.append(box.winfo_children()[-1])
            text = tk.Label(box, text=f'{item["cause"]}\nWas tun: {item["todo"]}', bg='#ffffff', anchor='w', justify='left', wraplength=500)
            text.grid(row=2, column=1, sticky='w', padx=10, pady=(0, 6))
            self._wrap.append(text)
            goto = item.get('goto')
            if goto:
                ttk.Button(box, text='Dorthin', command=lambda g=goto: self.go(g)).grid(row=0, column=2, rowspan=3, padx=10)
        self._mark_seen()

    def _mark_seen(self):
        state = getattr(self.app, 'ui_state', None)
        if state is not None:
            state.set('problems_seen_at', repo.iso())
        update = getattr(self.app, 'update_problem_count', None)
        if update is not None:
            update()

    def go(self, goto):
        tab, sub = goto
        runner = getattr(self.app, 'run_action', None)
        if runner is not None:
            runner({'id': 'goto', 'tab': tab, 'sub': sub})
        try:
            self.app.lift()
        except tk.TclError:
            pass

    def close(self):
        window = getattr(self.app, '_problems_window', None)
        if window is self:
            self.app._problems_window = None
        self.destroy()
