"""Tab „Hilfe“: Erste Schritte, häufige Probleme mit Lösung, Begriffe in einfachem Deutsch, Tastenkürzel.

Als eigener Tab (statt eines Fensters), weil er wie alles andere im Programm aussieht, scrollt, mit Strg+7 oder F1 erreichbar ist
und Marc beim Zurückspringen nicht sein Fenster verliert. Alle Texte stehen in `socialscraper/helptexts.py`.
"""
import tkinter as tk
from tkinter import ttk

from .. import helptexts
from .common import COLORS, FONT_B, Tab
from .forms import H1, ScrollFrame
from .widgets import PAD


class HelpTab(Tab):
    def build(self):
        self.configure(padding=0)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._wrap = []
        self._anchors = {}
        self.scroll = ScrollFrame(self, on_width=self._on_width)
        self.scroll.grid(row=0, column=0, sticky='nsew')
        body = self.scroll.body
        body.columnconfigure(0, weight=1)
        r = 0
        head = ttk.Frame(body)
        head.grid(row=r, column=0, sticky='ew', padx=PAD, pady=(PAD, 4))
        ttk.Label(head, text='Hilfe', font=H1).pack(anchor='w')
        self._text(head, 'Alles in einfachen Worten. Diese Seite öffnest du jederzeit mit F1.', muted=True)
        jump = ttk.Frame(head)
        jump.pack(anchor='w', pady=(8, 0))
        self._jump_btns = {}
        for key, text in (('steps', 'Erste Schritte'), ('problems', 'Häufige Probleme'), ('terms', 'Begriffe'), ('keys', 'Tastenkürzel')):
            btn = ttk.Button(jump, text=text, command=lambda k=key: self.show_section(k))
            btn.pack(side='left', padx=(0, 6))
            self._jump_btns[key] = btn
        ttk.Button(jump, text='Geführten Start öffnen',
                   command=lambda: self.app.navigate('Übersicht')).pack(side='left', padx=(12, 0))
        r += 1

        # Erste Schritte
        r = self._heading(body, r, 'steps', 'Erste Schritte')
        for title, text in helptexts.FIRST_STEPS:
            box = ttk.Frame(body)
            box.grid(row=r, column=0, sticky='ew', padx=PAD, pady=(0, 8))
            ttk.Label(box, text=title, font=FONT_B).pack(anchor='w')
            self._text(box, text)
            r += 1

        # Probleme
        r = self._heading(body, r, 'problems', 'Häufige Probleme und Lösungen')
        for question, answers, action in helptexts.PROBLEMS:
            box = ttk.Frame(body)
            box.grid(row=r, column=0, sticky='ew', padx=PAD, pady=(0, 10))
            ttk.Label(box, text=question, font=FONT_B).pack(anchor='w')
            for sentence in answers:
                self._text(box, '•  ' + sentence)
            if action:
                label, tab, sub = action
                spec = {'id': 'open_problems'} if tab == 'Probleme' else {'id': 'goto', 'tab': tab, 'sub': sub}
                ttk.Button(box, text=label, command=lambda a=spec: self._act(a)).pack(anchor='w', pady=(4, 0))
            r += 1

        # Begriffe
        r = self._heading(body, r, 'terms', 'Begriffe')
        for key, (name, text) in sorted(helptexts.GLOSSARY.items(), key=lambda kv: kv[1][0].lower()):
            box = ttk.Frame(body)
            box.grid(row=r, column=0, sticky='ew', padx=PAD, pady=(0, 6))
            ttk.Label(box, text=name, font=FONT_B).pack(anchor='w')
            self._text(box, text)
            r += 1

        # Tastenkürzel
        r = self._heading(body, r, 'keys', 'Tastenkürzel')
        table = ttk.Frame(body)
        table.grid(row=r, column=0, sticky='w', padx=PAD, pady=(0, PAD))
        for number, (keys, what) in enumerate(helptexts.SHORTCUTS):
            ttk.Label(table, text=keys, font=FONT_B).grid(row=number, column=0, sticky='w', padx=(0, 20), pady=1)
            ttk.Label(table, text=what).grid(row=number, column=1, sticky='w', pady=1)

    # ---------- Bausteine ----------

    def _heading(self, body, row, key, title):
        label = ttk.Label(body, text=title, font=('Segoe UI', 12, 'bold'))
        label.grid(row=row, column=0, sticky='w', padx=PAD, pady=(16, 6))
        self._anchors[key] = label
        return row + 1

    def _text(self, parent, text, muted=False):
        label = ttk.Label(parent, text=text, wraplength=700, justify='left', foreground=COLORS['muted'] if muted else '')
        label.pack(anchor='w', pady=(1, 0))
        self._wrap.append(label)
        return label

    def _on_width(self, width):
        wrap = max(300, width - 2 * PAD - 40)
        for label in self._wrap:
            try:
                label.configure(wraplength=wrap)
            except tk.TclError:
                pass

    def _act(self, action):
        runner = getattr(self.app, 'run_action', None)
        if runner is not None:
            runner(action)

    def show_section(self, key):
        """Zu einem Abschnitt der Hilfe scrollen."""
        widget = self._anchors.get(key)
        if widget is None:
            return False
        self.update_idletasks()
        try:
            canvas, body = self.scroll.canvas, self.scroll.body
            total = max(1, body.winfo_height())
            top = widget.winfo_rooty() - body.winfo_rooty()
            canvas.yview_moveto(max(0, top - 8) / total)
        except tk.TclError:
            return False
        return True

    def focus_item(self, spec):
        return self.show_section((spec or {}).get('section', 'steps'))
