"""Hilfe für Fenstertests der Tabs Einstellungen/Automatik: ein Mini-Hauptfenster mit Wegwerf-Ordner.

Kein echtes Programmfenster, keine echten Daten: `root` zeigt auf einen Wegwerf-Ordner, Aufträge werden
nur aufgezeichnet (oder von Tests selbst abgeschlossen)."""
import pathlib
import tkinter as tk
from tkinter import ttk

from socialscraper.formlogic import DEFAULT_SETTINGS_TEXT


class FakeApp(tk.Tk):
    def __init__(self, root, settings_text=DEFAULT_SETTINGS_TEXT):
        super().__init__()
        self.withdraw()
        self.root = pathlib.Path(root)
        (self.root / 'data').mkdir(parents=True, exist_ok=True)
        if settings_text is not None:
            (self.root / 'settings.toml').write_text(settings_text, encoding='utf-8', newline='')
        style = ttk.Style(self)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Big.TButton', padding=(14, 6))
        self.geometry('960x520')
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True)
        self.job = None
        self.tabs = {}
        self.logs, self.statuses, self.steps, self.opened, self.selected = [], [], [], [], []
        self.busy = []
        self.actions = []

    def run_action(self, action):
        """Wie im echten Hauptfenster: Lösungsknöpfe melden hier ihre Aktion (die Tests prüfen sie)."""
        self.actions.append(dict(action))
        return True

    def add(self, title, cls):
        tab = cls(self)
        self.tabs[title] = tab
        self.nb.add(tab, text=title)
        return tab

    def log(self, line, tag=''):
        self.logs.append((line, tag))

    def set_status(self, text, kind='muted'):
        self.statuses.append((text, kind))

    def register_busy(self, widget):
        self.busy.append(widget)

    def run_steps(self, steps, on_finish):
        self.steps.append((list(steps), on_finish))
        return True   # wie App.run_steps: True = gestartet (False hieße „es läuft schon ein Auftrag“)

    def select_tab(self, name):
        self.selected.append(name)

    def open_path(self, path, edit=False):
        self.opened.append((pathlib.Path(path), edit))

    def refresh_tabs(self):
        for tab in self.tabs.values():
            tab.refresh()

    def destroy(self):
        # nachlaufende after-Aufrufe sauber beenden (sonst meldet ein späteres Fenster „invalid command name …“)
        from socialscraper.ui.common import cancel_all_after
        cancel_all_after(self)
        super().destroy()

    def pump(self, until, timeout=10):
        import time
        end = time.time() + timeout
        while not until() and time.time() < end:
            self.update()
            time.sleep(0.02)
        return until()
