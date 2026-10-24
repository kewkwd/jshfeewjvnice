"""Hauptfenster: Gerüst aus Tabs, Statusleiste, Protokoll und Auftragsablauf."""
import datetime
import os
import pathlib
import queue
import re
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .. import autologic as al
from .. import guicore as core
from .. import problemlogic, repo, uistate
from . import common
from .actions import Actions
from .plain_log import PlainLog
from .common import COLORS, FONT, FONT_B, ICON, TITLE, system_check
from .tab_accounts import AccountsTab
from .tab_auto import AutoTab
from .aktionen import AktionenTab
from .warmup_page import AufwaermenTab
from .streuung import StreuungTab
from .postfaecher import PostfaecherTab
from .working_rules import WorkingRulesTab
from .profiles import ProfilesTab
from .tab_data import DataTab
from .tab_fetch import FetchTab
from .tab_help import HelpTab
HelpTab.kuerzel = '<Control-Key-7>'
from .workflow_home import WorkflowHome
from .workflow_design import NAV, DESCRIPTIONS, LABELS
from .tab_settings import SettingsTab
from .forms import run_async
from .theme import PALETTE, install as install_theme
from .background import background_tasks_running

# Reihenfolge = Reihenfolge im Fenster. Neuer Tab: Klasse (Unterklasse von common.Tab) hier eintragen.
# Die Übersicht kommt zuerst (Startseite), die Hilfe zuletzt (F1). Strg+1 bis Strg+7 springen in dieser Reihenfolge.
TABS = [
    ('Übersicht', WorkflowHome),
    ('Abrufen', FetchTab),
    ('Konten', AccountsTab),
    ('Gespeicherte Daten', DataTab),
    ('Automatik', AutoTab),
    ('Einstellungen', SettingsTab),
    ('Aktionen', AktionenTab),
    ('Aufwärmen', AufwaermenTab),
    ('Postfächer', PostfaecherTab),
    ('Streuung', StreuungTab),
    ('Hilfe', HelpTab),
    ('Arbeitsregeln', WorkingRulesTab),
    ('Profile', ProfilesTab),
]
PROBLEM_CHECK_MS = 30000
LOG_OPEN_MIN_HEIGHT = 800     # darunter bleibt das Protokoll beim ersten Start eingeklappt (mehr Platz für die Tabs)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.ui_state = uistate.UiState(common.GUI_LOG.parent / uistate.FILE_NAME)   # Fenstergröße, letzter Tab (nie Geheimnisse)
        self._restore_geometry()
        self.minsize(1120, 720)
        self._normal_geometry = None
        self._problems_window = None
        self._browser_dialog = None
        self._avd_handoff_dialog = None
        self._avd_handoff_events = []
        self._problem_loading = False
        self.problem_count = 0
        try: self.iconbitmap(default=str(ICON))
        except Exception: pass
        install_theme(self)

        self.events = queue.Queue()
        self.job = None
        self.steps, self.results, self.on_finish = [], [], None
        self.started = 0.0
        self._busy_widgets = []
        self.current_label = ''
        self.cancelled = False
        self._closing = False
        self._destroyed = False
        self.tabs = {}   # Titel -> Tab-Instanz
        self._plain_log = PlainLog()
        self.actions = Actions(self)
        self.run_action = self.actions.run

        self._build()
        self.protocol('WM_DELETE_WINDOW', self._close)
        self._bind_keys()
        self._after_ids = {}
        self._after_ids['poll'] = self.after(80, self._poll)
        self._after_ids['tick'] = self.after(1000, self._tick)
        self.refresh_tabs()
        self._startup_check()
        self._restore_tab()
        self._after_ids['problems'] = self.after(1500, self._problem_tick)

    # ---------- Fenstergröße, Tab und Tastenkürzel merken ----------

    def _screen_bounds(self):
        """(x0, y0, Breite, Höhe) des ganzen Desktops – unter Windows über alle Bildschirme (ein zweiter Monitor liegt
        rechts, links oder oben vom Hauptbildschirm, x0/y0 können negativ sein). Sonst nur der Hauptbildschirm."""
        if os.name == 'nt':
            try:
                import ctypes
                metrics = ctypes.windll.user32.GetSystemMetrics
                x0, y0, w, h = (int(metrics(n)) for n in (76, 77, 78, 79))   # SM_XVIRTUALSCREEN … SM_CYVIRTUALSCREEN
                if w > 0 and h > 0:
                    return x0, y0, w, h
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _restore_geometry(self):
        default = f'{min(1380, self.winfo_screenwidth() - 60)}x{min(860, self.winfo_screenheight() - 110)}'
        x0, y0, screen_w, screen_h = self._screen_bounds()
        geo = uistate.clamp_geometry(self.ui_state.get('geometry'), screen_w, screen_h, screen_x=x0, screen_y=y0)
        if geo is None:
            self.geometry(default)
            return
        w, h, x, y = geo
        h = min(h, max(720, screen_h - 110))
        if y is not None:
            y = max(y0, min(y, y0 + screen_h - h - 90))
        self.geometry(f'{w}x{h}' + (f'+{x}+{y}' if x is not None else ''))
        if (self.ui_state.get('geometry') or {}).get('zoomed'):
            self.after_idle(lambda: self.state('zoomed'))

    def _remember_geometry(self, event=None):
        if event is not None and event.widget is not self:
            return
        try:
            if self.state() == 'normal':
                m = re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', self.geometry())
                if m:
                    self._normal_geometry = tuple(int(g) for g in m.groups())
        except tk.TclError:
            pass

    def _save_window_state(self):
        try:
            zoomed = self.state() == 'zoomed'
            self._remember_geometry()
            if self._normal_geometry:
                w, h, x, y = self._normal_geometry
                self.ui_state.set('geometry', {'w': w, 'h': h, 'x': x, 'y': y, 'zoomed': zoomed}, save_now=False)
            title = self._current_tab_title()
            if title:
                self.ui_state.set('tab', title, save_now=False)
            self.ui_state.flush()
        except (tk.TclError, OSError):
            pass

    def _current_tab_title(self):
        try:
            widget = self.nametowidget(self.nb.select())
        except (KeyError, tk.TclError):
            return None
        return next((title for title, tab in self.tabs.items() if tab is widget), None)

    def _restore_tab(self):
        # The compact overview is the user's default working area.
        self.nb.select(self.tabs['Übersicht'])

    def _bind_keys(self):
        self.bind('<F1>', lambda _e: (self.select_tab('Hilfe'), 'break')[1])
        for number, (title, _cls) in enumerate(TABS, 1):
            self.bind(getattr(_cls, 'kuerzel', f'<Control-Key-{number}>' if number <= 9 else f'<Control-F{number}>'), lambda _e, t=title: (self.select_tab(t), 'break')[1])
        self.bind('<Configure>', self._remember_geometry, add='+')
        self.bind('<Configure>', self._auto_log, add='+')
        self.bind('<FocusIn>', self._focus_in, add='+')

    def _focus_in(self, event):
        """Das Fenster hat wieder den Fokus: der Abrufen-Tab schaut (nur jetzt) nach einem Link in der Zwischenablage."""
        if event.widget is not self:
            return
        fetch = self.tabs.get('Abrufen')
        if fetch is not None and hasattr(fetch, 'check_clipboard'):
            fetch.check_clipboard()

    # ---------- Aufbau ----------

    def _build(self):
        p = PALETTE
        self.columnconfigure(0, weight=0, minsize=196)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(2, weight=0)
        sidebar = tk.Frame(self, bg=p['sidebar'], width=196)
        sidebar.grid(row=0, column=0, rowspan=3, sticky='nsew')
        brand = tk.Frame(sidebar, bg=p['sidebar'])
        brand.pack(fill='x', padx=14, pady=(14, 12))
        mark = tk.Label(brand, text='P', width=2, bg=p['accent'], fg='white',
                        font=('Segoe UI', 16, 'bold'), padx=2, pady=2)
        mark.pack(side='left', padx=(0, 10))
        tk.Label(brand, text=TITLE, bg=p['sidebar'], fg='white',
                 font=('Segoe UI', 14, 'bold')).pack(anchor='w')
        tk.Label(brand, text='Dein Arbeitsbereich', bg=p['sidebar'], fg=p['nav_muted'],
                 font=('Segoe UI', 9)).pack(anchor='w', pady=(1, 0))
        search = tk.Frame(sidebar, bg=p['sidebar'])
        search.pack(fill='x', padx=12, pady=(0, 8))
        tk.Label(search, text='BEREICH SUCHEN  ·  STRG+K', font=('Segoe UI', 8),
                 bg=p['sidebar'], fg=p['nav_muted'], anchor='w').pack(fill='x', pady=(0, 5))
        self.nav_query = tk.StringVar()
        self.nav_search_entry = tk.Entry(search, textvariable=self.nav_query, bg=p['nav_hover'],
                                         fg='white', insertbackground='white', relief='flat',
                                         highlightthickness=1, highlightbackground=p['nav_hover'],
                                         highlightcolor='#5cc3b8', font=FONT)
        self.nav_search_entry.pack(fill='x', ipady=6)
        self.nav_search_entry.bind('<Escape>', self._clear_nav_search)
        self.bind('<Control-k>', self._focus_nav_search)
        self.bind('<Control-K>', self._focus_nav_search)
        nav_area = tk.Frame(sidebar, bg=p['sidebar'])
        nav_area.pack(fill='both', expand=True)
        nav_canvas = tk.Canvas(nav_area, bg=p['sidebar'], borderwidth=0, highlightthickness=0, width=196)
        nav_scroll = ttk.Scrollbar(nav_area, command=nav_canvas.yview, style='Nav.Vertical.TScrollbar')
        nav_canvas.configure(yscrollcommand=nav_scroll.set)
        nav_scroll.pack(side='right', fill='y')
        nav_canvas.pack(side='left', fill='both', expand=True)
        nav_body = tk.Frame(nav_canvas, bg=p['sidebar'])
        nav_body.columnconfigure(0, weight=1)
        nav_id = nav_canvas.create_window((0, 0), window=nav_body, anchor='nw')
        def update_nav_region(_event=None):
            nav_canvas.configure(scrollregion=nav_canvas.bbox('all'))
            need = nav_body.winfo_reqheight() > nav_canvas.winfo_height()
            shown = nav_scroll.winfo_manager() == 'pack'
            if need and not shown:
                nav_scroll.pack(side='right', fill='y')
            elif shown and not need:
                nav_scroll.pack_forget()
        nav_body.bind('<Configure>', update_nav_region)
        nav_canvas.bind('<Configure>', lambda e: nav_canvas.itemconfigure(nav_id, width=e.width))
        nav_canvas.bind('<Configure>', update_nav_region, add='+')
        self.nav_buttons, self.nav_rows, self.nav_groups, self.nav_marks = {}, {}, [], {}
        self._current_navigation = None
        self._expanded_navigation = bool(self.ui_state.get('home', 'navigation_expanded', False))
        for group_index, (group, items) in enumerate(NAV):
            section = tk.Frame(nav_body, bg=p['sidebar'])
            section.grid(row=group_index, column=0, sticky='ew', padx=9, pady=(3, 9))
            section.columnconfigure(0, weight=1)
            tk.Label(section, text=group, bg=p['sidebar'], fg=p['nav_muted'],
                     font=('Segoe UI', 8, 'bold'), anchor='w').grid(row=0, column=0, sticky='ew', padx=12, pady=(3, 5))
            self.nav_groups.append((section, [key for key, _label in items]))
            for index, (key, label) in enumerate(items, 1):
                row = tk.Frame(section, bg=p['sidebar'])
                row.grid(row=index, column=0, sticky='ew', pady=1)
                row.columnconfigure(1, weight=1)
                stripe = tk.Frame(row, width=3, bg=p['sidebar'])
                stripe.grid(row=0, column=0, sticky='ns')
                button = tk.Button(row, text=label, bg=p['sidebar'], fg=p['nav_text'],
                                   activebackground=p['nav_hover'], activeforeground='white',
                                   relief='flat', bd=0, anchor='w', padx=10, pady=4,
                                   highlightthickness=1, highlightbackground=p['sidebar'],
                                   highlightcolor='#5cc3b8', font=FONT, cursor='hand2',
                                   command=lambda name=key: self.navigate(name))
                button.grid(row=0, column=1, sticky='ew')
                button.bind('<Enter>', lambda _e, k=key: self._nav_hover(k, True))
                button.bind('<Leave>', lambda _e, k=key: self._nav_hover(k, False))
                for widget in (button, row):
                    widget.bind('<MouseWheel>', lambda e: (nav_canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'), 'break')[1])
                self.nav_buttons[key], self.nav_rows[key], self.nav_marks[key] = button, row, stripe
        self.nav_no_results = tk.Label(nav_body, text='Kein passender Bereich.', bg=p['sidebar'],
                                       fg=p['nav_muted'], font=FONT, wraplength=170)
        self.nav_more = tk.Button(sidebar, text='Weniger Bereiche ▴' if self._expanded_navigation else 'Weitere Bereiche ▾',
                                  command=self._toggle_navigation, bg=p['sidebar'], fg=p['nav_muted'],
                                  activebackground=p['nav_hover'], activeforeground='white', relief='flat',
                                  borderwidth=0, anchor='w', padx=14, pady=6, font=('Segoe UI', 9), cursor='hand2')
        self.nav_more.pack(fill='x', padx=8, pady=(6, 0))
        self.nav_query.trace_add('write', self._filter_navigation)
        self._filter_navigation()
        tk.Label(sidebar, text='Profilwerk  ·  Hilfe mit F1', bg=p['sidebar'], fg=p['nav_muted'],
                 font=('Segoe UI', 8), anchor='w').pack(fill='x', padx=18, pady=(10, 16))
        content = ttk.Frame(self, style='Canvas.TFrame')
        content.grid(row=0, column=1, sticky='nsew', padx=14, pady=(10, 6))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        header = ttk.Frame(content, style='Canvas.TFrame', padding=(0, 0, 0, 10))
        header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(0, weight=1)
        self.page_category = ttk.Label(header, text='DEIN ARBEITSBEREICH', style='Eyebrow.TLabel')
        self.page_category.grid(row=0, column=0, sticky='w', pady=(0, 4))
        self.page_title = ttk.Label(header, text='Start', style='Title.TLabel')
        self.page_title.grid(row=1, column=0, sticky='w')
        self.page_description = ttk.Label(header, text='', style='PageHint.TLabel')
        self.page_description.grid(row=2, column=0, sticky='w', pady=(6, 0))
        ttk.Button(header, text='Schritt für Schritt →', command=self.show_workflow).grid(row=1, column=1, padx=(16, 0))
        header.bind('<Configure>', lambda e: self.page_description.configure(wraplength=max(240, e.width - 165)))
        panel = ttk.Frame(content, style='Card.TFrame', padding=1)
        panel.grid(row=1, column=0, sticky='nsew')
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        self.nb = ttk.Notebook(panel, style='Workspace.TNotebook')
        self.nb.grid(row=0, column=0, sticky='nsew')

        bar = ttk.Frame(self, style='Status.TFrame', padding=(0, 6))
        bar.grid(row=1, column=1, sticky='ew', padx=14, pady=(0, 4))
        bar.columnconfigure(2, weight=1)
        self.progress = ttk.Progressbar(bar, mode='indeterminate', length=70)
        self.progress.grid(row=0, column=0, padx=(0, 10))
        self.progress.grid_remove()
        self.activity_badge = ttk.Label(bar, text='●  Bereit', style='Badge.TLabel')
        self.activity_badge.grid(row=0, column=1, padx=(0, 10))
        self.status = ttk.Label(bar, text='Wähle einen Bereich, um zu beginnen.', style='Status.TLabel')
        self.status.grid(row=0, column=2, sticky='w')
        self.problems_btn = ttk.Button(bar, text='Hinweise', command=self.show_problems)
        self.problems_btn.grid(row=0, column=3, padx=(8, 0))
        self.log_btn = ttk.Button(bar, text='Protokoll ▾', command=self.toggle_log)
        self.log_btn.grid(row=0, column=4, padx=(6, 0))
        self.cancel_btn = ttk.Button(bar, text='Abbrechen', command=self.cancel, state='disabled', style='Danger.TButton')
        self.cancel_btn.grid(row=0, column=5, padx=(6, 0))
        self.android_request_btn = ttk.Button(bar, text='Eingabe erforderlich …',
                                             command=self._show_avd_handoff, style='Primary.TButton')
        self.android_request_btn.grid(row=1, column=2, sticky='w', pady=(5, 0))
        self.android_request_btn.grid_remove()

        logf = ttk.LabelFrame(self, text='Protokoll – Ablauf und Hinweise', padding=10)
        logf.grid(row=2, column=1, sticky='nsew', padx=22, pady=(4, 12))
        self.logf = logf
        self._log_open = self.ui_state.get('log_open')
        self._log_decided = self._log_open is not None
        self._set_log(False if self._log_open is None else self._log_open)
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.log_text = tk.Text(logf, height=5, wrap='word', state='disabled', font=FONT, relief='flat')
        sb = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.grid(row=0, column=0, sticky='nsew')
        sb.grid(row=0, column=1, sticky='ns')
        for tag, color in (('ERROR', COLORS['err']), ('WARNING', COLORS['warn']), ('OK', COLORS['ok']), ('MUTED', COLORS['muted'])):
            self.log_text.tag_configure(tag, foreground=color)
        row = ttk.Frame(logf)
        row.grid(row=1, column=0, columnspan=2, sticky='e', pady=(8, 0))
        ttk.Button(row, text='Protokoll leeren', command=self._clear_log, style='Quiet.TButton').pack(side='left', padx=4)
        ttk.Button(row, text='Verlauf als Datei öffnen', command=lambda: self.open_path(common.GUI_LOG)).pack(side='left')
        ttk.Button(row, text='Technische Details', command=lambda: self.open_path(common.GUI_LOG.with_name('gui-details.log'))).pack(side='left', padx=4)

        for title, cls in TABS:
            tab = cls(self)
            self.tabs[title] = tab
            self.nb.add(tab, text=title)
        self.nb.bind('<<NotebookTabChanged>>', self._tab_changed)

    def _tab_changed(self, _event=None):
        try: tab = self.nametowidget(self.nb.select())
        except (KeyError, tk.TclError): return
        title = self._current_tab_title()
        if isinstance(tab, common.Tab):
            self._refresh_one(title, tab)
        if title is not None:
            self._update_navigation(title)
            self.ui_state.set('tab', title, save_now=False)

    # ---------- Protokoll ein- und ausklappen ----------

    def _auto_log(self, event=None):
        # Details open only when requested or when an error needs attention.
        return

    def _set_log(self, wanted):
        self._log_open = bool(wanted)
        if self._log_open:
            self.logf.grid()
            self.rowconfigure(2, weight=0, minsize=130)
        else:
            self.logf.grid_remove()
            self.rowconfigure(2, weight=0, minsize=0)
        self.log_btn.configure(text='Protokoll ▴' if self._log_open else 'Protokoll ▾')

    def toggle_log(self):
        self._log_decided = True
        self._set_log(not self._log_open)
        self.ui_state.set('log_open', self._log_open)

    def show_log(self):
        """Protokoll aufklappen (zum Beispiel, wenn ein Fehler auftrat und man nachsehen will)."""
        if not self._log_open:
            self._set_log(True)

    # ---------- Probleme (Zähler in der Statusleiste, kleines Fenster) ----------

    def show_problems(self):
        from .problems_window import ProblemsWindow
        window = self._problems_window
        if window is not None:
            try:
                if window.winfo_exists():
                    window.deiconify(); window.lift(); window.reload()
                    return window
            except tk.TclError:
                pass
        self._problems_window = ProblemsWindow(self)
        return self._problems_window

    def update_problem_count(self):
        """Zähler „Probleme (n)“ neu bestimmen (liest im Hintergrund; zählt, was neuer ist als das letzte Öffnen des Fensters)."""
        if self._problem_loading or self._destroyed:
            return
        self._problem_loading = True
        root = getattr(self, 'root', None) or common.ROOT
        db = al.resolve_db(root)
        def done(items, error):
            self._problem_loading = False
            if error is not None or items is None:
                return
            # The user can open the list while this background read is in flight.
            # Compare against the current acknowledgement, not the old snapshot.
            items = problemlogic.with_session_failures(items, getattr(self, '_job_problems', ()))
            self.set_problem_count(problemlogic.unseen_count(items, self.ui_state.get('problems_seen_at')))
        run_async(self, lambda: problemlogic.collect(root, db), done)

    def set_problem_count(self, n):
        self.problem_count = n
        self.problems_btn.configure(text=f'Hinweise ({n})' if n else 'Hinweise')

    def _problem_tick(self):
        if self._destroyed:
            return
        self.update_problem_count()
        self._after_ids['problems'] = self.after(PROBLEM_CHECK_MS, self._problem_tick)

    def refresh_tabs(self):
        """Alle Tabs neu laden (nach jedem Auftrag und beim Start)."""
        for title, tab in self.tabs.items():
            self._refresh_one(title, tab)

    def _refresh_one(self, title, tab):
        """Ein Tab neu laden; ein Fehler dabei (z. B. gesperrte Datenbank) bleibt bei diesem Tab und landet im Protokoll –
        er darf weder den Start noch den Abschluss eines Auftrags abbrechen."""
        try:
            tab.refresh()
        except Exception as error:   # noqa: BLE001 – jeder Fehler eines Tabs wird gemeldet, keiner stoppt das Fenster
            self.log(f'Der Bereich „{title}“ konnte nicht neu geladen werden: {error}', 'ERROR')

    def navigate(self,title):
        self.select_tab(title)
        if title=='Übersicht':self.tabs[title].reset()

    def _focus_nav_search(self, _event=None):
        self.nav_search_entry.focus_set()
        self.nav_search_entry.selection_range(0, 'end')
        return 'break'

    def _clear_nav_search(self, _event=None):
        self.nav_query.set('')
        self.nb.focus_set()
        return 'break'

    def _toggle_navigation(self):
        self._expanded_navigation = not self._expanded_navigation
        self.ui_state.update_in('home', navigation_expanded=self._expanded_navigation)
        self.nav_more.configure(text='Weniger Bereiche ▴' if self._expanded_navigation else 'Weitere Bereiche ▾')
        self._filter_navigation()

    def _filter_navigation(self, *_args):
        query = self.nav_query.get().strip().casefold()
        aliases = {'Profile': 'avd emulator geräte passwort', 'Postfächer': 'mail email passwort',
                   'Konten': 'anmelden registrieren login', 'Einstellungen': 'proxy verbindung'}
        visible = set()
        for key, row in self.nav_rows.items():
            main = key in ('Übersicht', 'Profile', 'Konten', 'Postfächer', 'Abrufen', 'Gespeicherte Daten', 'Einstellungen', 'Hilfe')
            matches = query in (key + ' ' + LABELS[key] + ' ' + aliases.get(key, '')).casefold()
            if matches and (bool(query) or main or self._expanded_navigation):
                row.grid()
                visible.add(key)
            else:
                row.grid_remove()
        for section, keys in self.nav_groups:
            section.grid() if any(key in visible for key in keys) else section.grid_remove()
        if visible:
            self.nav_no_results.grid_remove()
        else:
            self.nav_no_results.grid(row=len(NAV), column=0, padx=14, pady=14)

    def _nav_hover(self, key, entered):
        if key != self._current_navigation:
            self.nav_buttons[key].configure(bg=PALETTE['nav_hover'] if entered else PALETTE['sidebar'])

    def _update_navigation(self, title):
        self._current_navigation = title
        if not self.nav_rows[title].winfo_manager() and not self.nav_query.get():
            self._expanded_navigation = True
            self.nav_more.configure(text='Weniger Bereiche ▴')
            self._filter_navigation()
        for key, button in self.nav_buttons.items():
            selected = key == title
            button.configure(bg=PALETTE['nav_hover'] if selected else PALETTE['sidebar'],
                             fg='white' if selected else PALETTE['nav_text'],
                             font=FONT_B if selected else FONT)
            self.nav_marks[key].configure(bg='#5cc3b8' if selected else PALETTE['sidebar'])
        self.page_category.configure(text=next(group for group, items in NAV if any(key == title for key, _label in items)))
        self.page_title.configure(text=LABELS[title])
        self.page_description.configure(text=DESCRIPTIONS[title])

    def show_workflow(self):
        current=self._current_tab_title()
        task=self.tabs['Übersicht'].task if current=='Übersicht' else 'collect' if current in ('Abrufen','Automatik') else 'account' if current in ('Konten','Aufwärmen') else 'register' if current in ('Postfächer','Profile') else 'act' if current in ('Aktionen','Streuung') else 'results' if current=='Gespeicherte Daten' else 'collect'
        self.select_tab('Übersicht')
        self.tabs['Übersicht'].choose(task)

    def select_tab(self, name_or_index):
        """Tab nach Titel (z. B. 'Konten') oder Nummer anzeigen."""
        if isinstance(name_or_index, str):
            if self.nav_query.get():
                self.nav_query.set('')
            self.nb.select(self.tabs[name_or_index])
        else:
            self.nb.select(name_or_index)

    # ---------- Protokoll / Status ----------

    def log(self, line, tag=''):
        line = str(line)
        self._write_log(common.GUI_LOG.with_name('gui-details.log'), line)
        from ..android.handoff import PREFIX as HANDOFF_PREFIX, parse as parse_handoff
        if line.startswith(HANDOFF_PREFIX):
            event = parse_handoff(line)
            if event:
                self._avd_handoff_event(event)
            return
        from ..registration_progress import PREFIX as REG_PREFIX,parse as parse_registration,LABELS as REG_LABELS
        if line.startswith(REG_PREFIX):
            phase=parse_registration(line)
            process=getattr(getattr(self,'job',None),'proc',None)
            if phase and process is not None:
                from ..net.deadline import descendants
                allowed={process.pid,*[pid for pid,_name in descendants(process.pid)]}
                if phase['pid'] in allowed:
                    self.current_registration_phase=phase['phase']
                    self.set_status(self.current_label+': '+REG_LABELS[phase['phase']]+' …','warn')
                    from ..registration_progress import description
                    provider = {'web.de': 'WEB.DE', 'gmx.net': 'GMX', 'tiktok': 'TikTok'}.get(phase['provider'], phase['provider'])
                    level = 'OK' if phase['phase'] == 'complete' else 'WARNING' if phase['phase'] in ('waiting_confirmation', 'pending_mail', 'review_required') else 'MUTED'
                    self.log(provider + ': ' + description(phase['phase'], phase['provider']), level)
            return
        from ..android.progress import PREFIX as PHASE_PREFIX,parse as parse_phase,LABELS as PHASE_LABELS
        if line.startswith(PHASE_PREFIX):
            phase=parse_phase(line)
            process=getattr(getattr(self,'job',None),'proc',None)
            if phase and process is not None:
                from ..net.deadline import descendants
                allowed={process.pid,*[pid for pid,_name in descendants(process.pid)]}
                if phase['pid'] in allowed:
                    self.current_phase=phase['phase']
                    self.current_registration_phase=None
                    self.set_status(self.current_label+': '+PHASE_LABELS[self.current_phase]+' …','warn')
                    self.log(PHASE_LABELS[self.current_phase] + '.', 'MUTED')
            return
        from ..android.google_mail import PREFIX as ANDROID_PREFIX, parse as parse_android
        if line.startswith(ANDROID_PREFIX):
            event=parse_android(line)
            if event:self._android_event(event)
            return
        from ..net.browser_window import PREFIX, parse
        if line.startswith(PREFIX):
            event=parse(line)
            if event:
                self._browser_event(event)
            return
        shown, inferred_tag = self._plain_log.format(line, self._target_name)
        if shown is None:
            return
        tag = tag or inferred_tag
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        category = {'ERROR': 'Problem: ', 'CRITICAL': 'Problem: ', 'WARNING': 'Hinweis: ', 'OK': 'Erledigt: '}.get(tag, '')
        if shown.startswith(('Fertig:', 'Erledigt:', 'Hinweis:', 'Deine Hilfe', 'Problem:')):
            category = ''
        text = f'{stamp} · {category}{shown}'
        self.log_text.configure(state='normal')
        self.log_text.insert('end', text + '\n', tag)
        if int(self.log_text.index('end-1c').split('.')[0]) > 3000:
            self.log_text.delete('1.0', '800.0')
        self.log_text.configure(state='disabled'); self.log_text.see('end')
        self._write_log(common.GUI_LOG, text)

    def _browser_event(self,event):
        dialog=getattr(self,'_browser_dialog',None)
        if event['state']=='closed':
            if dialog and dialog.hwnd==event['hwnd']:
                self._close_browser_dialog()
            return
        if os.environ.get('SS_REQUIRE_PROXY')=='1' and not event['proxy']:
            self.cancel()
            self.log('Bestätigungsfenster ohne Proxy wurde im Testmodus abgebrochen.','ERROR')
            return
        if dialog and dialog.winfo_exists() and dialog.hwnd==event['hwnd']:
            dialog.lift();return
        self._close_browser_dialog()
        try:
            from .browser_dialog import BrowserDialog
            self._browser_dialog=BrowserDialog(self,event)
        except Exception as error:
            self.log('Das Bestätigungsfenster konnte nicht eingebettet werden. Der vorhandene Browser bleibt geöffnet. '
                     f'({type(error).__name__})','WARNING')

    def _android_event(self,event):
        dialog=getattr(self,'_android_dialog',None)
        if event['state']=='closed':
            if dialog and str(dialog.directory)==event['directory']:
                dialog.destroy();self._android_dialog=None
            return
        self._close_browser_dialog()
        try:
            from .android_dialog import AndroidDialog
            self._android_dialog=AndroidDialog(self,event)
        except Exception as error:
            self.cancel()
            self.log('Android-Testfenster konnte nicht geöffnet werden; Auftrag abgebrochen. '+str(error),'ERROR')

    def _close_browser_dialog(self):
        process = getattr(getattr(self, 'job', None), 'proc', None)
        if process is not None and process.poll() is not None:
            from .avd_handoff import cleanup
            for event in self._avd_handoff_events:
                try:cleanup(self, event)
                except (OSError, ValueError, RuntimeError):
                    self.log('Temporäre Android-Daten konnten nicht vollständig entfernt werden.', 'WARNING')
            self._avd_handoff_events.clear()
        handoff = getattr(self, '_avd_handoff_dialog', None)
        self._avd_handoff_dialog = None
        if handoff and handoff.winfo_exists():
            try:handoff.destroy()
            except tk.TclError:pass
        if hasattr(self, 'android_request_btn'):
            self.android_request_btn.grid_remove()
        android=getattr(self,'_android_dialog',None)
        self._android_dialog=None
        if android and android.winfo_exists():
            try:android.destroy()
            except Exception:pass
        dialog=getattr(self,'_browser_dialog',None)
        self._browser_dialog=None
        if dialog and dialog.winfo_exists():
            try:dialog.destroy()
            except Exception:pass

    def _avd_handoff_event(self, event):
        dialog = self._avd_handoff_dialog
        if event['state'] == 'closed':
            if dialog and dialog.event == {**event, 'state': 'open'}:
                dialog.destroy()
                self._avd_handoff_dialog = None
                self.android_request_btn.grid_remove()
                self.log('Die Geräteansicht wurde geschlossen. Der Auftragsstatus wird aktualisiert.', 'MUTED')
            return
        try:
            from .avd_handoff import AvdHandoffDialog, validate
            if not validate(self, event):
                raise ValueError('Android-Rückfrage gehört nicht zum laufenden Auftrag.')
            if event not in self._avd_handoff_events:
                self._avd_handoff_events.append(event)
            if dialog and dialog.winfo_exists() and dialog.event == event:
                self._show_avd_handoff()
                return
            self._close_browser_dialog()
            self._avd_handoff_dialog = AvdHandoffDialog(self, event)
            self.android_request_btn.grid()
            self.set_status('Deine Eingabe ist erforderlich. Der Auftrag pausiert.', 'warn')
            from ..android.handoff import REASONS
            self.log('Deine Hilfe ist nötig: ' + REASONS[event['reason']], 'WARNING')
            common.flash_window(self)
        except Exception as error:
            self.cancel()
            self.log('Die Android-Rückfrage konnte nicht geöffnet werden; Auftrag angehalten. '
                     f'({type(error).__name__})', 'ERROR')

    def _show_avd_handoff(self):
        dialog = self._avd_handoff_dialog
        if dialog and dialog.winfo_exists():
            dialog.deiconify()
            dialog.lift()

    @staticmethod
    def _write_log(log_file, text):
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            if log_file.exists() and log_file.stat().st_size > 2_000_000:
                log_file.write_text('', encoding='utf-8')
            with log_file.open('a', encoding='utf-8') as out:
                out.write(text + '\n')
        except OSError:
            pass

    def _clear_log(self):
        self._plain_log.reset()
        self.log_text.configure(state='normal'); self.log_text.delete('1.0', 'end'); self.log_text.configure(state='disabled')

    def set_status(self, text, kind='muted'):
        self.status.configure(text=text, foreground=COLORS[kind])

    def _startup_check(self):
        try:
            cfg = core.read_settings(common.ROOT)
        except (OSError, ValueError):
            cfg = None
        rows, ok = system_check(cfg)
        if not ok:
            self.log('Systemprüfung: Es fehlt etwas – Einrichten.cmd ausführen.', 'WARNING')
            for r in rows:
                if r.startswith('✖'): self.log('  ' + r, 'WARNING')
            self.set_status('Einrichtung unvollständig – siehe Protokoll.', 'warn')

    # ---------- Jobs ----------

    def register_busy(self, widget):
        """Widget merken, das während eines Auftrags gesperrt wird (Knopf, Eingabefeld …)."""
        self._busy_widgets.append(widget)

    def _busy(self, busy):
        previous = getattr(self, '_busy_previous_states', {})
        if busy:
            self._busy_previous_states = {b: str(b.cget('state')) for b in self._busy_widgets if b.winfo_exists()}
        for b in self._busy_widgets:
            if b.winfo_exists():
                b.configure(state='disabled' if busy else previous.get(b, 'normal'))
        if not busy:
            self._busy_previous_states = {}
        self.activity_badge.configure(text='●  Auftrag läuft' if busy else '●  Bereit')
        self.cancel_btn.configure(state='normal' if busy else 'disabled')
        if busy:
            self.progress.grid(); self.progress.start(12)
        else:
            self.progress.stop(); self.progress.grid_remove()

    def run_steps(self, steps, on_finish):
        """steps: Liste von (Bezeichnung, Argumente). Läuft nacheinander; nur ein Auftrag gleichzeitig.
        on_finish(results, cancelled) wird danach im Fenster-Thread aufgerufen."""
        if self.job is not None or any(getattr(getattr(t, '_auftrag', None), 'laeuft', False) for t in self.tabs.values()):
            messagebox.showinfo(TITLE, 'Es läuft bereits ein Auftrag.'); return False
        self.steps, self.results, self.on_finish = list(steps), [], on_finish
        self._plain_log.reset()
        self.started = time.monotonic(); self.cancelled = False
        self._busy(True); self._next_step()
        return True

    def _next_step(self):
        if not self.steps or self.cancelled:
            return self._finish()
        self.current_label, args = self.steps.pop(0)
        self.current_phase=None
        self.current_registration_phase=None
        self.set_status(self.current_label+' wird vorbereitet …','warn')
        self.log(f'▶ {self.current_label}', 'MUTED')
        try:
            self.job = core.Job(args, lambda l: self.events.put(('line', l)), lambda c: self.events.put(('done', c)))
            self.job.start()
        except Exception as error:   # OSError, ValueError (z. B. NUL-Zeichen im Link) …: Zustand sauber zurücksetzen
            self.job = None
            self.log(f'Programm konnte nicht gestartet werden: {error}', 'ERROR')
            self.results.append((self.current_label, 1, []))
            self._finish()

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'line':
                    self.log(value)
                else:
                    if self.job is None:
                        continue
                    finished = self.job
                    self._close_browser_dialog()
                    lines, self.job = finished.lines, None
                    if self.cancelled and not finished.cancelled and not self.steps:
                        self.cancelled = False  # Der letzte Schritt war vor der Abbruchanforderung schon fertig.
                    self.results.append((self.current_label, value, lines))
                    self._next_step()
                    if self._destroyed:
                        return
        except queue.Empty:
            pass
        except Exception as error:   # noqa: BLE001 – die Schleife darf nie stehen bleiben, sonst kommt kein Ergebnis mehr an
            if not self._destroyed:
                self.log(f'Fehler beim Abschluss eines Auftrags: {error}', 'ERROR')
        if not self._destroyed:
            self._after_ids['poll'] = self.after(80, self._poll)

    def _target_name(self, target_id):
        """Lesbarer Name eines Ziels für die Protokollzeilen (None, wenn es ihn nicht gibt)."""
        from .. import autologic as al
        from .. import repo
        with core._ro(al.resolve_db(common.ROOT)) as db:
            row = repo.get_target(db, target_id)
        return al.target_title(row) if row else None

    def _tick(self):
        if self.job is not None:
            s = int(time.monotonic() - self.started)
            if self.cancelled:
                self.set_status('Abbruch läuft – Browser und Auftrag werden beendet …', 'warn')
            else:
                from ..android.progress import LABELS
                from ..registration_progress import LABELS as REG_LABELS
                detail=REG_LABELS.get(getattr(self,'current_registration_phase',None),'') or LABELS.get(getattr(self,'current_phase',None),'')
                if self._avd_handoff_dialog is not None:
                    detail = 'Deine Eingabe ist erforderlich; automatische Eingaben pausieren'
                self.set_status(f'{self.current_label}: {detail} … {s // 60}:{s % 60:02d}' if detail else f'{self.current_label} … {s // 60}:{s % 60:02d}', 'warn')
        self._after_ids['tick'] = self.after(1000, self._tick)

    def cancel(self):
        for tab in getattr(self, 'tabs', {}).values():
            task = getattr(tab, '_auftrag', None)
            if task and task.laeuft:
                task.abbrechen()
        if self.job is not None:
            if self.job.cancel():
                self.cancelled = True   # die restlichen Schritte bleiben stehen: _next_step lässt sie aus, solange cancelled gilt
                self.cancel_btn.configure(state='disabled')
                self.set_status('Abbruch läuft – Browser und Auftrag werden beendet …', 'warn')
                self.log('Abbruch angefordert. Bereits gespeicherte Ergebnisse bleiben erhalten.', 'WARNING')

    def _finish(self):
        if self._closing:
            self.steps, self.on_finish = [], None
            self._save_window_state()   # auch beim Schließen während eines Auftrags Fenstergröße und Tab merken
            self.destroy()
            return
        self._busy(False)
        cb, results = self.on_finish, self.results
        self.steps, self.on_finish = [], None
        self.refresh_tabs()
        if cb:
            try:
                cb(results, self.cancelled)
            except Exception as error:   # noqa: BLE001 – der Rückruf eines Tabs darf den Abschluss nicht abbrechen
                self.log(f'Das Ergebnis des Auftrags konnte nicht angezeigt werden: {error}', 'ERROR')
        self._notify_finished(results, self.cancelled)
        self.update_problem_count()

    def _notify_finished(self, results, cancelled):
        """Ist der lange Auftrag fertig (oder gescheitert), während das Fenster nicht vorn ist: Taskleiste blinkt, Statuszeile sagt es."""
        if cancelled or not results:
            return
        foreground = common.window_in_front(self)
        bad = [label for label, code, _lines in results if code != 0]
        stamp = datetime.datetime.now().strftime('%H:%M')
        if bad:
            self._job_problems = problemlogic.with_session_failures(
                problemlogic.from_job_results(results, repo.now()), getattr(self, '_job_problems', ()))
            self.set_status(f'{stamp} Nicht alles hat geklappt: {bad[0]}. Siehe „Probleme“ unten rechts.', 'err')
        elif not foreground:
            self.set_status(f'{stamp} Fertig: {results[-1][0]}.', 'ok')
        if not foreground:common.flash_window(self)

    def _close(self):
        running = [getattr(tab, '_auftrag', None) for tab in self.tabs.values()
                   if getattr(getattr(tab, '_auftrag', None), 'laeuft', False)]
        if running:
            if not self._closing:
                if not messagebox.askyesno(TITLE, 'Es läuft noch ein Auftrag. Abbrechen und beenden?'):
                    return
                self._closing = True
                for task in running:
                    task.abbrechen()
                self.set_status('Abbruch läuft – das Fenster schließt sich, sobald der Auftrag beendet ist …', 'warn')
                self.log('Abbruch angefordert. Das Fenster schließt sich, sobald der Auftrag beendet ist.', 'WARNING')
            self._after_ids['close'] = self.after(150, self._close)   # warten, bis der Auftrag seinen Prüfpunkt erreicht hat
            return
        if self.job is not None:
            if not messagebox.askyesno(TITLE, 'Es läuft noch ein Auftrag. Abbrechen und beenden?'):
                return
            self._closing = True
            self.steps.clear()
            self.cancel()
            return  # Erst das bestätigte Prozessende schließt das Fenster.
        self._save_window_state()
        self.destroy()

    def destroy(self):
        if not getattr(self, '_destroyed', False) and background_tasks_running(self):
            # Keep the Tcl interpreter on its owning thread until background
            # helpers have released closures that may retain Tk widgets.
            try:
                if not getattr(self, '_background_close_waiting', False):
                    self._background_close_waiting = True
                    self.set_status('Ein Hintergrundvorgang wird noch beendet; das Fenster schließt sich danach.', 'warn')
                self._after_ids['background_close'] = self.after(100, self.destroy)
                return
            except tk.TclError:
                pass
        self._background_close_waiting = False
        self._destroyed = True
        self._close_browser_dialog()
        for key, ident in list(getattr(self, '_after_ids', {}).items()):
            try: self.after_cancel(ident)
            except Exception: pass
        self._after_ids = {}
        common.cancel_all_after(self)   # auch Aufrufe der Tabs und Hintergrund-Poller: nichts läuft nach dem Zerstören ins Leere
        super().destroy()

    # ---------- Hilfen ----------

    def open_path(self, path, edit=False):
        """Datei/Ordner im Windows-Standardprogramm öffnen (edit=True: im Editor)."""
        path = pathlib.Path(path)
        try:
            if edit and os.name == 'nt':
                import subprocess
                proc = subprocess.Popen(['notepad.exe', str(path)])
                if proc.returncode is None:
                    proc.returncode = 0   # bewusst nicht abwarten: der Editor bleibt offen; verhindert eine ResourceWarning (wie daemon.start_detached)
            elif path.exists():
                os.startfile(str(path))  # noqa: S606 – Windows-Standardprogramm
            else:
                messagebox.showinfo(TITLE, f'Noch nicht vorhanden: {path}')
        except OSError as error:
            messagebox.showerror(TITLE, f'Konnte nicht geöffnet werden: {error}')
