"""Gemeinsame Grundlagen des Fensters: Konstanten, Tab-Basisklasse, kleine Hilfen."""
import importlib.util
import os
import pathlib
import shutil
import tkinter as tk
from tkinter import ttk

from .. import guicore as core

from ..branding import NAME as TITLE
ROOT = core.ROOT
ICON = ROOT / 'assets' / 'socialscraper.ico'
GUI_LOG = ROOT / 'data' / 'gui.log'   # Tests biegen dieses Modulattribut um; Zugriff immer als common.GUI_LOG
from .theme import PALETTE
COLORS = {key: PALETTE[key] for key in ('ok', 'warn', 'err', 'muted')}
FONT = ('Segoe UI', 10)
FONT_B = ('Segoe UI', 10, 'bold')


class Tab(ttk.Frame):
    """Basis aller Tabs. `app` ist das Hauptfenster (App aus ui/app.py).

    build()   - baut die Widgets einmal auf (wird vom Konstruktor aufgerufen).
    refresh() - laedt Inhalte neu (beim Anzeigen des Tabs und nach jedem Auftrag).
    Den Notebook-Eintrag legt App an, nicht der Tab.
    """

    def __init__(self, app):
        super().__init__(app.nb)
        self.app = app
        self.build()

    def build(self):
        pass

    def refresh(self):
        pass


class ModeBanner(tk.Frame):
    """Gelbe Hinweiszeile „Proxy-Modus ‚Tor‘ aktiv: Konten sind gesperrt.“ – nur sichtbar in den Modi frei/tor.

    Der Aufrufer legt sie mit grid(...) an; `update_mode()` liest den gespeicherten Modus und zeigt oder versteckt sie.
    Mit `app` (Hauptfenster mit `run_action`) bekommt sie einen Lösungsknopf „Auf ‚Aus‘ stellen“ (mit Rückfrage)."""

    def __init__(self, parent, root_fn, app=None):
        super().__init__(parent, bg='#fff4ce', highlightthickness=1, highlightbackground='#d4a72c')
        self.root_fn, self.mode, self.app = root_fn, 'aus', app
        self.columnconfigure(0, weight=1)
        self.label = tk.Label(self, text='', bg='#fff4ce', fg='#5c4400', font=FONT_B, anchor='w', justify='left', wraplength=800)
        self.label.grid(row=0, column=0, sticky='ew', padx=8, pady=4)
        self.fix_btn = None
        if app is not None and hasattr(app, 'run_action'):
            self.fix_btn = ttk.Button(self, text='Auf „Aus“ stellen', command=self._fix)
            self.fix_btn.grid(row=0, column=1, padx=(0, 8), pady=4)
        self.bind('<Configure>', lambda e: self.label.configure(wraplength=max(200, e.width - (170 if self.fix_btn else 24))))

    def _fix(self):
        self.app.run_action({'id': 'set_mode', 'value': 'aus', 'from': self.mode})

    def update_mode(self, mode=None):
        from .. import proxylogic
        self.mode = mode or proxylogic.current_mode(self.root_fn())
        text = proxylogic.banner_text(self.mode)
        if text:
            self.label.configure(text='⚠ ' + text + ' ' + proxylogic.BANNER_HINT)
            self.grid()
        else:
            self.grid_remove()
        return self.mode


def flash_window(window, count=3):
    """Taskleiste blinken lassen (Windows, FlashWindowEx), damit Marc merkt, dass ein langer Auftrag fertig ist.
    Ohne Windows oder bei einem Fehler passiert nichts. Gibt True zurück, wenn ein Blinken ausgelöst wurde."""
    if os.name != 'nt':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.UINT), ('hwnd', wintypes.HWND), ('dwFlags', wintypes.DWORD),
                        ('uCount', wintypes.UINT), ('dwTimeout', wintypes.DWORD)]
        hwnd = int(window.wm_frame(), 16) if window.wm_frame() else window.winfo_id()
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x0003 | 0x000C, count, 0)   # Fenster + Taskleiste, bis es nach vorn kommt
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        return True
    except Exception:
        return False


def window_in_front(window):
    """Hat das Programm gerade den Fokus (und ist nicht minimiert)?"""
    try:
        if window.state() == 'iconic' or not window.winfo_viewable():
            return False
        return window.focus_displayof() is not None
    except tk.TclError:
        return True


def track_after(widget, ms, fn):
    """`widget.after(ms, fn)`, aber der Aufruf wird abgebrochen, sobald das Widget zerstört wird.

    Sonst läuft er danach ins Leere und Tcl meldet „invalid command name …“ (Hintergrund-Poller der Tabs, Hinweisfenster)."""
    pending = widget.__dict__.setdefault('_tracked_after', set())
    if not widget.__dict__.get('_tracked_after_bound'):
        widget.__dict__['_tracked_after_bound'] = True

        def cleanup(event):
            if event.widget is widget:
                for ident in list(pending):
                    try: widget.after_cancel(ident)
                    except Exception: pass
                pending.clear()
        widget.bind('<Destroy>', cleanup, add='+')
    holder = {}

    def run():
        pending.discard(holder.get('id'))
        fn()
    holder['id'] = widget.after(ms, run)
    pending.add(holder['id'])
    return holder['id']


def cancel_all_after(root):
    """Alle noch wartenden after-Aufrufe der Tk-Instanz abbrechen (vor dem Zerstören des Hauptfensters)."""
    try:
        pending = root.tk.splitlist(root.tk.call('after', 'info'))
    except tk.TclError:
        return
    for ident in pending:   # nur den Zeitgeber löschen; die Tcl-Befehle räumen die Widgets beim Zerstören selbst weg
        try: root.tk.call('after', 'cancel', ident)
        except tk.TclError: pass


def read_int(var, lo, hi):
    """Ganzzahl aus einer tk-Variablen, oder None bei Unsinn / ausserhalb von lo..hi."""
    try: value = int(var.get())
    except (tk.TclError, ValueError): return None
    return value if lo <= value <= hi else None


def system_check(cfg=None):
    """Prüft, ob alles vorhanden ist, was echte Abrufe brauchen. Gibt (Zeilen, alles_ok) zurück."""
    rows, ok = [], True
    def add(good, text):
        nonlocal ok
        ok = ok and good
        rows.append(('✔ ' if good else '✖ ') + text)
    firefox = getattr(cfg, 'engine', 'patchright') == 'firefox'
    modules = [('yt_dlp', 'yt-dlp (YouTube)')]
    if not firefox:
        modules.append(('patchright', 'Patchright (Browser-Steuerung)'))
    modules.extend((('instaloader', 'Instaloader (Instagram)'), ('curl_cffi', 'curl_cffi')))
    for module, label in modules:
        add(importlib.util.find_spec(module) is not None, label)
    if firefox:
        from ..net.firefox_runtime import installation_status
        ready, message = installation_status()
        add(ready, message)
    else:
        chrome = [pathlib.Path(p) / 'Google/Chrome/Application/chrome.exe'
                  for p in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'), os.environ.get('LOCALAPPDATA')) if p]
        add(any(c.is_file() for c in chrome) or bool(shutil.which('chrome')), 'Google Chrome')
    node = shutil.which('node')
    add(bool(node), 'Node.js (für YouTube)')
    return rows, ok
