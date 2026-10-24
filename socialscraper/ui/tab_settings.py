"""Tab "Einstellungen": Formulare statt Textdatei.

Links die Bereiche, rechts das Formular. Unten „Speichern“ und „Zurücksetzen“ für alles, was in
settings.toml und data/integration.json steht. Dienst, Autostart, Test und Sicherung wirken sofort
und sind entsprechend beschriftet. Die Logik liegt in socialscraper/formlogic.py.
"""
import os
import pathlib
import platform
import re
import secrets
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import aiconfig
from .. import autologic as al
from .. import formlogic as fl
from .. import guicore as core
from .. import proxylogic
from ..net import freeproxy, proxymode, tor
from . import common
from .common import COLORS, FONT_B, TITLE, Tab, system_check, track_after
from .forms import H1, ScrollFrame, ServicePanel, StringListEditor, friendly, run_async
from .widgets import HelpDot, PathLabel
from .settings_ai import AISettingsSection

SECTIONS = [('abruf', 'Abruf'), ('proxys', 'Proxys'), ('meldungen', 'Benachrichtigungen'),
            ('dienst', 'Automatik-Dienst'), ('pulse', 'PULSE-Anbindung'), ('cloud', 'Cloud-Datenbank'),
            ('ki', 'KI / Stimmung'), ('system', 'System')]
SIMPLE_VARS = ('proxy_mode', 'max_comments', 'max_retries', 'request_timeout', 'rotation_every', 'length_short_s', 'length_long_s',
               'snapshot', 'headless', 'session_max_age',
               'require_auth', 'notify_file', 'notify_webhook', 'api_enabled', 'api_port', 'api_token',
               'sync_url', 'sync_key', 'sync_auto')
BOOL_KEYS = ('snapshot', 'headless', 'require_auth', 'api_enabled', 'sync_auto')
CHOICES = {'log_level': fl.LOG_LEVELS, 'engine': fl.ENGINES, 'sync_kind': fl.SYNC_KINDS}


def _env_overrides(keys):
    """[(Schlüssel, Umgebungsvariable)] für Einstellungen, die gerade eine Variable SS_… festlegt (config.load legt sie über
    die Datei; eine gespeicherte Änderung bliebe dann ohne Wirkung)."""
    from .. import config
    variables = {key: env for env, (key, _cast) in config._ENV.items()}
    return [(key, variables[key]) for key in keys if key in variables and (os.environ.get(variables[key]) or '').strip()]


def _integration():
    try:
        from .. import integration
        return integration
    except ImportError:
        return None


class SettingsTab(AISettingsSection, Tab):
    # ---------- Aufbau ----------

    def build(self):
        self.vars, self.choice_vars, self.errors = {}, {}, {}
        self.err_labels, self.hints = {}, []
        self.baseline, self.dirty, self._loading = fl.default_raw(), False, True
        self.settings_problem = self.integration_problem = None
        self.integration_missing = False
        self.sections, self._rows, self.current = {}, {}, 'abruf'
        self.host = '127.0.0.1'
        self._tick_id = None
        self.last_result = {}
        self.configure(padding=(10, 10, 10, 6))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.banner = ttk.Frame(self)
        self.banner.grid(row=0, column=0, sticky='ew')
        self.banner.grid_remove()

        main = ttk.Frame(self)
        main.grid(row=1, column=0, sticky='nsew', pady=(6, 6))
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)
        self.nav = tk.Listbox(main, width=22, exportselection=False, activestyle='none', relief='flat',
                              highlightthickness=1, highlightbackground='#c8ccd0', font=('Segoe UI', 10),
                              selectbackground='#e7f4f1', selectforeground='#115e59', background='#f7f9fb',
                              foreground='#193247', borderwidth=0)
        self.nav.grid(row=0, column=0, sticky='ns', padx=(0, 10))
        self.nav.bind('<<ListboxSelect>>', self._nav_selected)
        self.content = ttk.Frame(main)
        self.content.grid(row=0, column=1, sticky='nsew')
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        for key, _title in SECTIONS:
            sf = ScrollFrame(self.content, on_width=self._set_wrap)
            sf.grid(row=0, column=0, sticky='nsew')
            sf.grid_remove()
            sf.body.columnconfigure(0, weight=1)
            self.sections[key] = sf
        self._build_abruf(self.sections['abruf'].body)
        self._build_proxys(self.sections['proxys'].body)
        self._build_meldungen(self.sections['meldungen'].body)
        self._build_dienst(self.sections['dienst'].body)
        self._build_pulse(self.sections['pulse'].body)
        self._build_cloud(self.sections['cloud'].body)
        self._build_ki(self.sections['ki'].body)
        self._build_system(self.sections['system'].body)
        for key, sf in self.sections.items():
            self._watch_focus(sf.body, sf)

        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, sticky='ew')
        bar.columnconfigure(0, weight=1)
        self.state_label = ttk.Label(bar, text='Alles gespeichert.', foreground=COLORS['muted'])
        self.state_label.grid(row=0, column=0, sticky='w')
        self.reset_btn = ttk.Button(bar, text='Zurücksetzen', command=self.reset, state='disabled')
        self.reset_btn.grid(row=0, column=1, padx=(0, 6))
        self.save_btn = ttk.Button(bar, text='Speichern', style='Big.TButton', command=self.save, state='disabled')
        self.save_btn.grid(row=0, column=2)

        self.nav.bind('<Right>', lambda _e: self._focus_first())
        top = self.winfo_toplevel()
        top.bind('<Control-s>', self._ctrl_s, add='+')
        self.bind('<Destroy>', self._destroyed, add='+')
        self._render_nav()
        self.show_section('abruf')
        self._load_from_disk()
        self._loading = False
        self._tick_id = self.after(5000, self._tick)

    def _destroyed(self, event):
        if event.widget is self and self._tick_id:
            try: self.after_cancel(self._tick_id)
            except tk.TclError: pass

    def _ctrl_s(self, _event=None):
        if self.winfo_ismapped() and self.dirty:
            self.save()
            return 'break'

    def _tick(self):
        try:
            if self.winfo_ismapped():
                self.service.refresh()
            if self._tick_id:
                self.after_cancel(self._tick_id)
            self._tick_id = self.after(5000, self._tick)
        except tk.TclError:
            pass

    def _set_wrap(self, width):
        wrap = max(300, width - 56)
        for label in self.hints:
            try: label.configure(wraplength=wrap)
            except tk.TclError: pass
        for label in getattr(self, 'card_labels', []):
            try: label.configure(wraplength=max(260, wrap - 40))
            except tk.TclError: pass
        if hasattr(self, 'warn_label'):
            try: self.warn_label.configure(wraplength=max(260, wrap - 30))
            except tk.TclError: pass
        if hasattr(self, 'status_label'):
            try: self.status_label.configure(wraplength=max(240, wrap - 110))
            except tk.TclError: pass
        for editor in (getattr(self, 'proxy_editor', None), getattr(self, 'origin_editor', None)):
            if editor is not None:
                editor.set_wrap(wrap)
        for panel in (getattr(self, 'service', None),):
            if panel is not None:
                panel.set_wrap(wrap)

    def _watch_focus(self, widget, sf):
        for child in widget.winfo_children():
            if child.winfo_class() in ('TEntry', 'TCombobox', 'TSpinbox', 'TCheckbutton', 'TButton', 'Listbox'):
                child.bind('<FocusIn>', lambda e, s=sf: s.see(e.widget), add='+')
            self._watch_focus(child, sf)

    def root(self):
        return getattr(self.app, 'root', None) or common.ROOT

    # ---------- Bausteine ----------

    def _row(self, body):
        r = self._rows.get(body, 0)
        self._rows[body] = r + 1
        return r

    def _heading(self, body, title, intro):
        ttk.Label(body, text=title, font=H1).grid(row=self._row(body), column=0, sticky='w', pady=(4, 2))
        self._hint(body, intro)

    def _hint(self, body, text, pady=(0, 4)):
        label = ttk.Label(body, text=text, foreground=COLORS['muted'], wraplength=520, justify='left')
        label.grid(row=self._row(body), column=0, sticky='w', pady=pady)
        self.hints.append(label)
        return label

    def _error_label(self, body, key):
        label = ttk.Label(body, text='', foreground=COLORS['err'], wraplength=520, justify='left')
        label.grid(row=self._row(body), column=0, sticky='w')
        label.grid_remove()
        self.err_labels[key] = label
        self.hints.append(label)

    def _title(self, body, text, help=None):
        if help is None:
            ttk.Label(body, text=text, font=FONT_B).grid(row=self._row(body), column=0, sticky='w', pady=(14, 2))
            return
        row = ttk.Frame(body)       # mit „?“-Punkt, der den Begriff erklärt
        row.grid(row=self._row(body), column=0, sticky='w', pady=(14, 2))
        ttk.Label(row, text=text, font=FONT_B).pack(side='left')
        HelpDot(row, help).pack(side='left', padx=(6, 0))

    def _var(self, key):
        var = tk.BooleanVar() if key in BOOL_KEYS else tk.StringVar()
        var.trace_add('write', lambda *_a, k=key: self._edited(k))
        self.vars[key] = var
        return var

    def _text_field(self, body, key, label, hint=None, width=None, show=None, widget='entry', help=None, **opts):
        self._title(body, label, help)
        holder = ttk.Frame(body)
        holder.grid(row=self._row(body), column=0, sticky='ew')
        holder.columnconfigure(0, weight=1 if width is None else 0)
        var = self._var(key)
        if widget == 'spin':
            w = ttk.Spinbox(holder, textvariable=var, width=width or 8, **opts)
        else:
            w = ttk.Entry(holder, textvariable=var, width=width or 30, show=show or '')
        w.grid(row=0, column=0, sticky='w' if width else 'ew')
        if hint:
            self._hint(body, hint, pady=(2, 0))
        self._error_label(body, key)
        return w, holder

    def _check_field(self, body, key, text, hint=None, command=None):
        var = self._var(key)
        cb = ttk.Checkbutton(body, text=text + '  ', variable=var, command=command)  # Leerzeichen: das Windows-Design schneidet sonst das letzte Zeichen ab
        cb.grid(row=self._row(body), column=0, sticky='w', pady=(12, 0))
        if hint:
            self._hint(body, hint, pady=(0, 0))
        self._error_label(body, key)
        return cb

    def _choice_field(self, body, key, label, hint=None, command=None):
        self._title(body, label)
        options = [text for text, _v in CHOICES[key]]
        var = tk.StringVar()
        var.trace_add('write', lambda *_a, k=key: self._edited(k))
        self.choice_vars[key] = var
        cb = ttk.Combobox(body, textvariable=var, values=options, state='readonly', width=34)
        cb.grid(row=self._row(body), column=0, sticky='w')
        if command:
            cb.bind('<<ComboboxSelected>>', lambda _e: command())
        if hint:
            self._hint(body, hint, pady=(2, 0))
        self._error_label(body, key)
        return cb

    def _secret_row(self, holder, entry, extra=()):
        """„Anzeigen“-Umschalter neben einem maskierten Feld."""
        state = {'shown': False}
        btn = ttk.Button(holder, text='Anzeigen')

        def set_shown(shown):
            """Feld und Knopftext zusammen umstellen – auch von außen (z. B. „Neu erzeugen“ zeigt den neuen Token)."""
            state['shown'] = bool(shown)
            entry.configure(show='' if state['shown'] else '•')
            btn.configure(text='Verbergen' if state['shown'] else 'Anzeigen')

        def toggle():
            set_shown(not state['shown'])
        btn.configure(command=toggle)
        btn.set_shown = set_shown
        col = 1
        btn.grid(row=0, column=col, padx=(8, 0))
        for text, fn in extra:
            col += 1
            b = ttk.Button(holder, text=text, command=fn)
            b.grid(row=0, column=col, padx=(6, 0))
        return btn

    # ---------- Bereich Abruf ----------

    def _build_abruf(self, b):
        self._heading(b, 'Abruf', 'Wie das Programm Videos und Kommentare holt. Gilt für alle Abrufe, auch die automatischen.')
        self._text_field(b, 'max_comments', 'Kommentare pro Video (Standard)',
                         '0 = nur Videodaten ohne Kommentare. Höchstens 5000.', width=8, widget='spin', from_=0, to=5000, increment=10)
        self._text_field(b, 'max_retries', 'Wiederholungen bei Fehlern',
                         'Wie oft ein fehlgeschlagener Abruf erneut versucht wird (1 bis 10).', width=8, widget='spin', from_=1, to=10)
        self._text_field(b, 'request_timeout', 'Zeitlimit pro Anfrage (Sekunden)',
                         'Danach gilt eine Anfrage als gescheitert (5 bis 300). Bei langsamem Netz höher stellen.',
                         width=8, widget='spin', from_=5, to=300, increment=5)
        self._text_field(b, 'rotation_every', 'Konto wechseln nach … Videos',
                         'Bei „Automatisch wechseln (Rotation)“ nimmt das Programm nach so vielen Videos das nächste Konto der '
                         'Plattform (1 bis 100). Bei einem Fehler des Kontos wechselt es sofort.',
                         width=8, widget='spin', from_=1, to=100, help='rotation')
        self._text_field(b, 'length_short_s', 'Kurze Videos: bis … Sekunden',
                         'Für den Filter „Länge“ im Tab „Gespeicherte Daten“: Videos bis zu dieser Länge gelten als kurz (Standard 60).',
                         width=8, widget='spin', from_=1, to=86400, increment=15)
        self._text_field(b, 'length_long_s', 'Lange Videos: über … Sekunden',
                         'Videos über dieser Länge gelten als lang, dazwischen als mittel (Standard 600 = 10 Minuten). '
                         'Muss größer sein als die Grenze für kurze Videos.',
                         width=8, widget='spin', from_=2, to=86400, increment=60)
        self._check_field(b, 'snapshot', 'Bei jedem Abruf einen Verlaufspunkt speichern',
                          'So siehst du, wie sich Aufrufe, Likes und Kommentarzahl über die Zeit entwickeln. '
                          'Gilt für Abrufe von Hand; die Automatik speichert immer einen Verlaufspunkt.')
        self._choice_field(b, 'log_level', 'Genauigkeit des Protokolls',
                           '„Normal“ reicht fast immer. „Ausführlich“ hilft nur bei der Fehlersuche und macht das Protokoll lang.')
        self._check_field(b, 'headless', 'Browser im Hintergrund (unsichtbar)',
                          'Abrufe öffnen dann kein Browserfenster und stören die Arbeit am PC nicht. Nur die Anmeldung unter '
                          '„Konten“ öffnet immer kurz ein sichtbares Fenster, damit du dich selbst anmelden kannst. '
                          'Ausschalten nur zur Fehlersuche.')
        self._choice_field(b, 'engine', 'Browser-Engine',
                           'Chrome / Patchright bleibt die Standardauswahl. Firefox wird mit dem offiziellen Playwright '
                           'gesteuert und unterstützt derzeit nur Desktop-Identitäten sowie Abrufe über einen Proxy. '
                           'Für mobile Identitäten Chrome / Patchright wählen.')
        self._text_field(b, 'session_max_age', 'Gültigkeit einer Anmeldung (Stunden)',
                         'Nach dieser Zeit gilt eine gespeicherte Anmeldung als zu alt und wird neu geprüft (1 bis 720).',
                         width=8, widget='spin', from_=1, to=720, increment=6)
        self._check_field(b, 'require_auth', 'Anmeldung verlangen',
                          'Abrufe, die eine Anmeldung brauchen, starten nur mit bestätigter Anmeldung – '
                          'sonst bekommst du eine klare Meldung statt unvollständiger Daten.')

    # ---------- Bereich Proxys ----------

    CARD_ON, CARD_OFF = '#2563eb', '#c8ccd0'
    WARN_BG, WARN_LINE, WARN_FG = '#fff4ce', '#d4a72c', '#5c4400'

    def _build_proxys(self, b):
        self._heading(b, 'Proxys', 'Hier legst du fest, über welche Internetverbindung die Abrufe laufen. Im Zweifel: „Aus“.')
        self._title(b, 'Welche Verbindung soll benutzt werden?', 'proxy_mode')
        ttk.Style(self).configure('Card.TRadiobutton', font=('Segoe UI', 11, 'bold'))
        var = self._var('proxy_mode')
        cards = ttk.Frame(b)
        cards.grid(row=self._row(b), column=0, sticky='ew', pady=(2, 0))
        cards.columnconfigure(0, weight=1)
        self.mode_cards, self.card_labels = {}, []
        for i, (value, title, text, _warn) in enumerate(proxylogic.MODE_CARDS):
            card = tk.Frame(cards, highlightthickness=2, highlightbackground=self.CARD_OFF, highlightcolor=self.CARD_OFF, cursor='hand2')
            card.grid(row=i, column=0, sticky='ew', pady=(0, 6))
            card.columnconfigure(0, weight=1)
            radio = ttk.Radiobutton(card, text=title + '  ', value=value, variable=var, style='Card.TRadiobutton')
            radio.grid(row=0, column=0, sticky='w', padx=(10, 6), pady=(6, 0))
            desc = ttk.Label(card, text=text, foreground=COLORS['muted'], wraplength=480, justify='left')
            desc.grid(row=1, column=0, sticky='w', padx=(34, 10), pady=(0, 7))
            self.card_labels.append(desc)
            for w in (card, desc):
                w.bind('<Button-1>', lambda _e, v=value: var.set(v))
            self.mode_cards[value] = card
        self._error_label(b, 'proxy_mode')

        self.warn_box = tk.Frame(b, bg=self.WARN_BG, highlightthickness=2, highlightbackground=self.WARN_LINE, highlightcolor=self.WARN_LINE)
        self.warn_box.grid(row=self._row(b), column=0, sticky='ew', pady=(6, 4))
        self.warn_box.columnconfigure(0, weight=1)
        self.warn_label = tk.Label(self.warn_box, text='⚠ ' + proxylogic.WARN_TEXT, bg=self.WARN_BG, fg=self.WARN_FG,
                                   font=('Segoe UI', 10, 'bold'), wraplength=480, justify='left', anchor='w')
        self.warn_label.grid(row=0, column=0, sticky='ew', padx=10, pady=8)

        self._title(b, 'Zustand')
        status = ttk.Frame(b)
        status.grid(row=self._row(b), column=0, sticky='ew')
        status.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status, text='Prüfe …', foreground=COLORS['muted'], wraplength=420, justify='left')
        self.status_label.grid(row=0, column=0, sticky='w')
        self.status_box = status
        self.check_btn = ttk.Button(status, text='Prüfen', command=self.proxy_check)
        self.check_btn.grid(row=0, column=1, padx=(8, 0), sticky='n')
        self.proxy_progress = ttk.Progressbar(b, mode='indeterminate', length=200)
        self.proxy_progress.grid(row=self._row(b), column=0, sticky='w', pady=(6, 0))
        self.proxy_progress.grid_remove()
        self.proxy_note = ttk.Label(b, text='', foreground=COLORS['muted'], wraplength=520, justify='left')
        self.proxy_note.grid(row=self._row(b), column=0, sticky='w')
        self.proxy_note.grid_remove()
        self.hints.append(self.proxy_note)
        self.check_result = ttk.Label(b, text='', wraplength=520, justify='left')
        self.check_result.grid(row=self._row(b), column=0, sticky='w', pady=(2, 0))
        self.check_result.grid_remove()
        self.hints.append(self.check_result)

        # Eigene Proxy-Liste (nur im Modus „liste“ sichtbar)
        self.list_panel = ttk.Frame(b)
        self.list_panel.grid(row=self._row(b), column=0, sticky='ew')
        self.list_panel.columnconfigure(0, weight=1)
        self._title(self.list_panel, 'Eingetragene Proxys')
        self.proxy_editor = StringListEditor(
            self.list_panel, fl.parse_proxy, display=fl.mask_proxy, on_change=lambda: self._edited('proxies'),
            example='Beispiel: http://benutzer:passwort@host.beispiel.de:8000 (auch https, socks4, socks5)')
        self.proxy_editor.grid(row=self._row(self.list_panel), column=0, sticky='ew')
        self._error_label(self.list_panel, 'proxies')
        self._hint(self.list_panel, 'Passwörter werden in der Liste als ***** angezeigt und nie ins Protokoll geschrieben.', pady=(6, 0))
        self._hint(self.list_panel, 'Sessionvorlagen: Unterstützt dein Anbieter feste Adressen je Sitzung, schreibe im Benutzernamen '
                                    '{identity} und {country}, z. B. http://user-session-{identity}-country-{country}:passwort@gw.anbieter.de:8000. '
                                    'Jedes Konto bekommt dann seine eigene feste Adresse.')

        # Hinweis im Modus „aus“, wenn noch eine Liste eingetragen ist
        self.aus_note = ttk.Label(b, text=proxylogic.LIST_NOTE, foreground=COLORS['warn'], wraplength=520, justify='left')
        self.aus_note.grid(row=self._row(b), column=0, sticky='w', pady=(10, 0))
        self.hints.append(self.aus_note)

        # Tor
        self.tor_panel = ttk.Frame(b)
        self.tor_panel.grid(row=self._row(b), column=0, sticky='ew')
        self.tor_panel.columnconfigure(0, weight=1)
        self._title(self.tor_panel, 'Tor')
        self._hint(self.tor_panel, proxylogic.TOR_HINT, pady=(0, 6))
        row = ttk.Frame(self.tor_panel)
        row.grid(row=self._row(self.tor_panel), column=0, sticky='w')
        self.tor_start_btn = ttk.Button(row, text='Tor starten', command=self.tor_start)
        self.tor_start_btn.pack(side='left')
        self.tor_stop_btn = ttk.Button(row, text='Tor beenden', command=self.tor_stop)
        self.tor_stop_btn.pack(side='left', padx=(8, 0))
        self.tor_test_btn = ttk.Button(row, text='Verbindung testen', command=self.tor_test)
        self.tor_test_btn.pack(side='left', padx=(8, 0))
        self.tor_result = ttk.Label(self.tor_panel, text='', wraplength=520, justify='left')
        self.tor_result.grid(row=self._row(self.tor_panel), column=0, sticky='w', pady=(6, 0))
        self.tor_result.grid_remove()
        self.hints.append(self.tor_result)

        # Kostenlose Proxys
        self.free_panel = ttk.Frame(b)
        self.free_panel.grid(row=self._row(b), column=0, sticky='ew')
        self.free_panel.columnconfigure(0, weight=1)
        self._title(self.free_panel, 'Liste der kostenlosen Proxys')
        self.free_info = ttk.Label(self.free_panel, text='', wraplength=520, justify='left')
        self.free_info.grid(row=self._row(self.free_panel), column=0, sticky='w')
        self.hints.append(self.free_info)
        self.free_refresh_btn = ttk.Button(self.free_panel, text='Liste jetzt aktualisieren', command=self.free_refresh)
        self.free_refresh_btn.grid(row=self._row(self.free_panel), column=0, sticky='w', pady=(6, 0))
        self.free_result = ttk.Label(self.free_panel, text='', wraplength=520, justify='left')
        self.free_result.grid(row=self._row(self.free_panel), column=0, sticky='w', pady=(6, 0))
        self.free_result.grid_remove()
        self.hints.append(self.free_result)
        self._hint(self.free_panel, proxylogic.FREE_HINT, pady=(6, 0))

        self._proxy_busy, self._desc_gen, self._desc_key, self._progress = False, 0, None, {}
        self._apply_mode_view(describe=False)

    # ---------- Proxy-Modus: Anzeige und Hintergrundarbeit ----------

    def _proxy_inputs(self):
        mode = self.vars['proxy_mode'].get() if 'proxy_mode' in self.vars else 'aus'
        mode = mode if mode in proxylogic.MODE_TITLES else 'aus'
        return mode, list(self.proxy_editor.get())

    def _apply_mode_view(self, describe=True):
        """Zeigt je nach gewähltem Modus die passenden Teile und stößt (nur bei Änderung) die Statusabfrage an."""
        if not hasattr(self, 'warn_box'):
            return
        mode, proxies = self._proxy_inputs()
        for value, card in self.mode_cards.items():
            color = self.CARD_ON if value == mode else self.CARD_OFF
            card.configure(highlightbackground=color, highlightcolor=color)

        def show(widget, wanted):
            if wanted:
                widget.grid()
            else:
                widget.grid_remove()
        show(self.warn_box, proxylogic.is_anonymous(mode))
        show(self.list_panel, mode == 'liste')
        show(self.aus_note, mode == 'aus' and bool(proxies))
        show(self.tor_panel, mode == 'tor')
        show(self.free_panel, mode == 'frei')
        if describe and (mode, tuple(proxies)) != self._desc_key:
            self._proxy_describe()

    def _proxy_describe(self):
        """Statuszeile im Hintergrund holen (describe kann bis 0,5 s warten) und im Fenster-Thread anzeigen."""
        mode, proxies = self._proxy_inputs()
        self._desc_key = (mode, tuple(proxies))
        self._desc_gen += 1
        gen, root = self._desc_gen, self.root()
        cfg = proxylogic.make_cfg(mode, proxies, root)

        def work():
            info = freeproxy.status(root) if mode == 'frei' else None
            return proxymode.describe(cfg, root), info

        def done(result, error):
            if gen != self._desc_gen:
                return
            if error is not None:
                self.status_label.configure(text='Der Zustand ließ sich nicht ermitteln.', foreground=COLORS['warn'])
                return
            (_mode, text, ok), info = result
            if mode != (self.baseline or {}).get('proxy_mode', 'aus'):
                text += ' (Noch nicht gespeichert – gilt erst nach „Speichern“.)'
            self.status_label.configure(text='● ' + text, foreground=COLORS[proxylogic.status_kind(mode, ok)])
            if info is not None:
                self.free_info.configure(text=proxylogic.free_summary(info))
        self.status_label.configure(foreground=COLORS['muted'])
        run_async(self, work, done)

    def _proxy_buttons(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for btn in (self.check_btn, self.tor_start_btn, self.tor_stop_btn, self.tor_test_btn, self.free_refresh_btn):
            btn.configure(state=state)

    def _proxy_run(self, work, done, note, result_label):
        """Eine längere Proxy-Arbeit im Hintergrund; währenddessen Fortschrittsbalken, Knöpfe gesperrt."""
        if self._proxy_busy:
            return False
        self._proxy_busy = True
        self._progress = {}
        self._proxy_buttons(False)
        self.proxy_progress.grid()
        self.proxy_progress.start(60)
        self.proxy_note.configure(text=note)
        self.proxy_note.grid()
        result_label.configure(text='')
        result_label.grid_remove()

        def tick():
            if self._proxy_busy:
                text = self._progress.get('text')
                self.proxy_note.configure(text=f'{note}\n{text}' if text else note)
                track_after(self, 400, tick)

        def finished(result, error):
            self._proxy_busy = False
            self.proxy_progress.stop()
            self.proxy_progress.grid_remove()
            self.proxy_note.configure(text='')
            self.proxy_note.grid_remove()
            self._proxy_buttons(True)
            done(result, error)
            self._proxy_describe()
        run_async(self, work, finished)
        track_after(self, 400, tick)
        return True

    @staticmethod
    def _show_result(label, ok, text, kind=None):
        label.configure(text=('✔ ' if ok else '✖ ') + text, foreground=COLORS[kind or ('ok' if ok else 'err')])
        label.grid()

    def proxy_check(self):
        mode, proxies = self._proxy_inputs()
        root = self.root()
        cfg = proxylogic.make_cfg(mode, proxies, root)
        note = {'frei': 'Teste Proxys … das dauert bis zu einer Minute.',
                'tor': 'Prüfe Tor, bei Bedarf wird es gestartet … das dauert bis zu 90 Sekunden.'}.get(mode, 'Prüfe …')

        def done(result, error):
            if error is not None:
                self._show_result(self.check_result, False, friendly(error))
            else:
                self._show_result(self.check_result, result[0], result[1], None if result[0] else 'err')
        self._proxy_run(lambda: proxymode.ensure_ready(cfg, root), done, note, self.check_result)

    def tor_start(self):
        root = self.root()

        def done(result, error):
            if error is not None:
                self._show_result(self.tor_result, False, friendly(error))
            else:
                self._show_result(self.tor_result, bool(result['ok']), result['message'])
        self._proxy_run(lambda: tor.ensure_running(root), done, 'Starte Tor … das dauert bis zu 90 Sekunden.', self.tor_result)

    def tor_stop(self):
        root = self.root()

        def done(result, error):
            if error is not None:
                self._show_result(self.tor_result, False, friendly(error))
                return
            ok, message = proxylogic.stop_tor_message(*result)
            self._show_result(self.tor_result, ok, message, None if ok else 'warn')
        self._proxy_run(lambda: tor.stop(root), done, 'Beende Tor …', self.tor_result)

    def tor_test(self):
        def done(result, error):
            if error is not None:
                self._show_result(self.tor_result, False, friendly(error))
                return
            ok, text = proxylogic.tor_connection_text(result)
            self._show_result(self.tor_result, ok, text, None if ok else 'warn')
        self._proxy_run(lambda: tor.find(verify=True), done, 'Teste die Verbindung (Ausgangs-IP über check.torproject.org) …', self.tor_result)

    def free_refresh(self):
        root = self.root()

        def work():
            return freeproxy.refresh(root, progress=lambda text: self._progress.__setitem__('text', str(text)[:120]))

        def done(result, error):
            if error is not None:
                self._show_result(self.free_result, False, friendly(error))
                return
            ok, text = proxylogic.refresh_summary(result)
            self._show_result(self.free_result, ok, text, 'ok' if ok else 'warn')
        self._proxy_run(work, done, 'Teste Proxys … das dauert bis zu einer Minute.', self.free_result)

    # ---------- Bereich Benachrichtigungen ----------

    def _build_meldungen(self, b):
        self._heading(b, 'Benachrichtigungen', 'Das Programm meldet Probleme (z. B. abgelaufene Anmeldung) in eine Datei '
                                               'und, wenn du magst, an eine Webadresse.')
        w, holder = self._text_field(b, 'notify_file', 'Meldedatei',
                                     'Hier landen die Meldungen. Relative Pfade gelten ab dem Programmordner.')
        ttk.Button(holder, text='Auswählen…', command=self._pick_notify_file).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(holder, text='Öffnen', command=self._open_notify_file).grid(row=0, column=2, padx=(6, 0))
        w, holder = self._text_field(b, 'notify_webhook', 'Webhook (optional)',
                                     'Eine Adresse, an die jede Meldung geschickt wird (z. B. von Discord oder Slack), muss mit https:// '
                                     'beginnen. Leer lassen = aus. Die Adresse enthält ein Geheimnis und wird verborgen.', show='•')
        self._secret_row(holder, w)

    def _pick_notify_file(self):
        start = self.vars['notify_file'].get().strip() or 'data/alerts.log'
        path = filedialog.asksaveasfilename(title='Meldedatei wählen', initialdir=str(self.root()),
                                            initialfile=os.path.basename(start), defaultextension='.log',
                                            confirmoverwrite=False,
                                            filetypes=[('Protokolldatei', '*.log'), ('Alle Dateien', '*.*')])
        if not path:
            return
        try:
            path = pathlib.Path(path).resolve().relative_to(pathlib.Path(self.root()).resolve()).as_posix()
        except ValueError:
            pass
        self.vars['notify_file'].set(str(path))

    def _open_notify_file(self):
        p = pathlib.Path(self.vars['notify_file'].get().strip() or 'data/alerts.log')
        if not p.is_absolute():
            p = pathlib.Path(self.root()) / p
        self.app.open_path(p)

    # ---------- Bereich Dienst ----------

    def _build_dienst(self, b):
        self._heading(b, 'Automatik-Dienst', 'Der Dienst arbeitet im Hintergrund die Ziele aus dem Tab „Automatik“ ab – auch wenn dieses Fenster zu ist.')
        self.service = ServicePanel(b, self.root, with_autostart=True, on_change=lambda ok, msg: self.app.set_status(msg, 'ok' if ok else 'err'))
        self.service.grid(row=self._row(b), column=0, sticky='ew', pady=(8, 0))
        self._hint(b, 'Der Dienst läuft unsichtbar. Er startet auch die lokale Schnittstelle für PULSE, wenn sie eingeschaltet ist. '
                      'Änderungen an den Einstellungen gelten für den Dienst erst nach einem Neustart.', pady=(8, 0))
        btn = ttk.Button(b, text='Zum Tab „Automatik“ (Ziele verwalten)', command=lambda: self.app.select_tab('Automatik'))
        btn.grid(row=self._row(b), column=0, sticky='w', pady=(8, 0))

    # ---------- Bereich PULSE ----------

    def _build_pulse(self, b):
        self._heading(b, 'PULSE-Anbindung', 'Eine lokale Schnittstelle, über die PULSE (oder ein anderes Programm auf diesem PC) '
                                            'deine gespeicherten Daten nur lesen kann. Sie läuft zusammen mit dem Hintergrunddienst.')
        self.api_cb = self._check_field(b, 'api_enabled', 'Schnittstelle einschalten', command=self._api_toggled,
                                        hint='Erreichbar nur auf diesem Rechner (127.0.0.1), geschützt durch den Token unten.')
        self._text_field(b, 'api_port', 'Port', 'Die Nummer der Schnittstelle (1024 bis 65535). Ändere sie nur, wenn sie schon belegt ist.',
                         width=8, widget='spin', from_=1024, to=65535)
        token, holder = self._text_field(b, 'api_token', 'Token (Zugangsschlüssel)',
                                         'PULSE schickt diesen Schlüssel bei jeder Anfrage mit. Gib ihn nur Programmen, denen du vertraust. '
                                         '„Neu erzeugen“ macht den alten ungültig.', show='•')
        self.token_entry = token
        self.token_toggle = self._secret_row(holder, token, extra=[('Kopieren', self._copy_token), ('Neu erzeugen', self._new_token)])
        self._title(b, 'Erlaubte Herkunftsadressen (CORS)')
        self.origin_editor = StringListEditor(b, fl.validate_origin, on_change=lambda: self._edited('api_origins'), height=3,
                                              example='Nur für PULSE als Webseite im Browser nötig, z. B. http://localhost:5173')
        self.origin_editor.grid(row=self._row(b), column=0, sticky='ew')
        self._error_label(b, 'api_origins')
        self._title(b, 'Prüfen')
        row = ttk.Frame(b)
        row.grid(row=self._row(b), column=0, sticky='ew')
        self.test_api_btn = ttk.Button(row, text='Schnittstelle testen', command=self.test_api)
        self.test_api_btn.pack(side='left')
        self.copy_url_btn = ttk.Button(row, text='Beispiel-Adresse kopieren', command=self._copy_example)
        self.copy_url_btn.pack(side='left', padx=(8, 0))
        self.example_label = ttk.Label(b, text='', foreground=COLORS['muted'])
        self.example_label.grid(row=self._row(b), column=0, sticky='w', pady=(6, 0))
        self.api_result = ttk.Label(b, text='', wraplength=520, justify='left')
        self.api_result.grid(row=self._row(b), column=0, sticky='w', pady=(4, 0))
        self.hints.append(self.api_result)
        self.vars['api_port'].trace_add('write', lambda *_: self._update_example())

    def _api_toggled(self):
        if self.vars['api_enabled'].get() and not self.vars['api_token'].get().strip():
            self.vars['api_token'].set(secrets.token_urlsafe(32))
            self.api_result.configure(text='Ein neuer Token wurde erzeugt. Mit „Speichern“ übernehmen.', foreground=COLORS['muted'])

    def _new_token(self):
        if self.vars['api_token'].get().strip() and not messagebox.askyesno(
                TITLE, 'Neuen Token erzeugen?\n\nPULSE verliert den Zugriff, bis du ihm den neuen Token gibst. '
                       'Der neue gilt erst nach „Speichern“.'):
            return
        self.vars['api_token'].set(secrets.token_urlsafe(32))
        self.token_toggle.set_shown(True)   # Feld und Umschalter („Verbergen“) zusammen – sonst stimmt der Knopftext nicht
        self.api_result.configure(text='Neuer Token erzeugt – mit „Speichern“ übernehmen und in PULSE eintragen.', foreground=COLORS['muted'])

    def _copy_token(self):
        token = self.vars['api_token'].get().strip()
        if not token:
            messagebox.showinfo(TITLE, 'Es gibt noch keinen Token. Klicke auf „Neu erzeugen“.')
            return
        self._clipboard(token)
        self.app.set_status('Token in die Zwischenablage kopiert.', 'ok')

    def _clipboard(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _port_value(self):
        try:
            return int(self.vars['api_port'].get().strip())
        except ValueError:
            return fl.DEFAULT_PORT

    def _update_example(self):
        self.example_label.configure(text='Beispiel: ' + fl.example_url(self._port_value(), self.host))

    def _copy_example(self):
        self._clipboard(fl.example_url(self._port_value(), self.host))
        self.app.set_status('Beispiel-Adresse kopiert.', 'ok')

    def test_api(self):
        port, error = fl.parse_int(self.vars['api_port'].get(), 1024, 65535, 'Port')
        if error:
            self._show_api_result(False, error)
            return
        token = self.vars['api_token'].get().strip()
        note = ''
        if any(k.startswith('api_') for k in fl.changed_keys(self.collect(), self.baseline)):
            note = ' (Es gibt ungespeicherte Änderungen – der Dienst nutzt die gespeicherten Werte.)'
        self.test_api_btn.configure(state='disabled')
        self.api_result.configure(text='Prüfe …', foreground=COLORS['muted'])
        host = self.host

        def done(result, err):
            self.test_api_btn.configure(state='normal')
            if err:
                self._show_api_result(False, friendly(err))
            else:
                self._show_api_result(result[0], result[1] + note)
        run_async(self, lambda: fl.check_api(port, token, host), done)

    def _show_api_result(self, ok, text):
        self.last_result['api'] = (ok, text)
        self.api_result.configure(text=('✔ ' if ok else '✖ ') + text, foreground=COLORS['ok' if ok else 'err'])

    # ---------- Bereich Cloud ----------

    def _build_cloud(self, b):
        self._heading(b, 'Cloud-Datenbank (Sync)', 'Spiegelt deine Daten in eine Cloud-Datenbank, damit PULSE sie auch unterwegs '
                                                   'oder von einem Server aus lesen kann. Die Tabellen legst du einmal mit '
                                                   'deploy/supabase_schema.sql in der Cloud an.')
        self._choice_field(b, 'sync_kind', 'Art', command=self._sync_kind_changed)
        self.url_entry, _ = self._text_field(b, 'sync_url', 'Adresse (URL)', 'Zum Beispiel https://abcdefgh.supabase.co – ein angehängtes „/“ oder „/rest/v1“ entfernt das Programm beim Speichern selbst.')
        self.key_entry, holder = self._text_field(b, 'sync_key', 'Schlüssel',
                                                  'Der „service_role“-Schlüssel deines Projekts. Er wird nur in data/integration.json '
                                                  'gespeichert und nie ins Protokoll geschrieben.', show='•')
        self._secret_row(holder, self.key_entry)
        self.auto_cb = self._check_field(b, 'sync_auto', 'Nach jedem Lauf automatisch hochladen',
                                         'Der Hintergrunddienst sendet neue Daten nach jedem Abruf selbst.')
        self._title(b, 'Jetzt ausführen')
        row = ttk.Frame(b)
        row.grid(row=self._row(b), column=0, sticky='w')
        self.cloud_btns = []
        for text, action in (('Verbindung testen', 'test'), ('Jetzt hochladen', 'push'), ('Aus Cloud aktualisieren', 'pull')):
            btn = ttk.Button(row, text=text, command=lambda a=action: self.cloud_action(a))
            btn.pack(side='left', padx=(0, 8))
            self.app.register_busy(btn)
            self.cloud_btns.append(btn)
        self.cloud_result = ttk.Label(b, text='', wraplength=520, justify='left')
        self.cloud_result.grid(row=self._row(b), column=0, sticky='w', pady=(8, 0))
        self.hints.append(self.cloud_result)
        self.cloud_state = ttk.Label(b, text='', foreground=COLORS['muted'], wraplength=520, justify='left')
        self.cloud_state.grid(row=self._row(b), column=0, sticky='w', pady=(4, 0))
        self.hints.append(self.cloud_state)

    def _sync_kind_changed(self):
        self._update_cloud_fields()

    def _update_cloud_fields(self):
        on = self._choice_value('sync_kind') == 'supabase'
        for w in (self.url_entry, self.key_entry, self.auto_cb):
            w.configure(state='normal' if on else 'disabled')

    def cloud_action(self, action):
        integration = _integration()
        if integration is None:
            messagebox.showinfo(TITLE, 'Funktion noch nicht verfügbar: Der Cloud-Abgleich fehlt in dieser Installation.')
            return
        if self.dirty:
            if not messagebox.askyesno(TITLE, 'Es gibt ungespeicherte Änderungen. Der Abgleich nutzt die gespeicherten Werte.\n\nJetzt speichern?'):
                return
            if not self.save():
                return
        if self.baseline['sync_kind'] != 'supabase':
            messagebox.showinfo(TITLE, 'Die Cloud-Datenbank ist ausgeschaltet. Wähle oben die Art „Supabase“ und trage Adresse und Schlüssel ein.')
            return
        labels = {'test': 'Cloud: Verbindung testen', 'push': 'Cloud: Daten hochladen', 'pull': 'Cloud: Daten aus der Cloud holen'}
        self.cloud_result.configure(text='Läuft …', foreground=COLORS['muted'])
        self.app.run_steps([(labels[action], ['sync', action])], lambda results, cancelled: self._cloud_done(action, results, cancelled))

    def _cloud_done(self, action, results, cancelled):
        if cancelled:
            text, kind = 'Abgebrochen.', 'warn'
        else:
            code, lines = (results[-1][1], results[-1][2]) if results else (1, [])
            text, kind = summarize_sync(action, code, lines)
        self.last_result['cloud'] = (kind, text)
        self.cloud_result.configure(text=text, foreground=COLORS[kind])
        self.app.set_status(text.splitlines()[0], kind)
        self._update_cloud_state()

    def _update_cloud_state(self):
        integration = _integration()
        if integration is None:
            return
        cfg, _problem = integration.load_with_status(self.root())
        s = cfg['sync']
        parts = []
        if s.get('last_push'):
            parts.append(f'Zuletzt hochgeladen: {al.local_text(s["last_push"])}')
        if s.get('last_pull'):
            parts.append(f'Zuletzt geholt: {al.local_text(s["last_pull"])}')
        if s.get('last_error'):
            parts.append(f'Letzter Fehler: {str(s["last_error"])[:200]}')
        self.cloud_state.configure(text='\n'.join(parts) if parts else 'Noch nichts gesendet.')

    # ---------- Bereich System ----------

    def _build_system(self, b):
        self._heading(b, 'System', 'Prüfung, Orte der Dateien, Sicherung und Version.')
        self._title(b, 'Systemprüfung')
        row = ttk.Frame(b)
        row.grid(row=self._row(b), column=0, sticky='w')
        self.syscheck_btn = ttk.Button(row, text='Systemprüfung (Chrome, Node.js, Pakete)', command=self.show_check)   # check_btn ist der Proxy-Knopf „Prüfen“
        self.syscheck_btn.pack(side='left')
        self.check_label = ttk.Label(b, text='', justify='left')
        self.check_label.grid(row=self._row(b), column=0, sticky='w', pady=(6, 0))

        self._title(b, 'Orte')
        self.path_labels = {}
        for key, text in (('program', 'Programmordner'), ('data', 'Datenordner (Datenbank, Anmeldungen, Protokolle)'),
                          ('db', 'Datenbankdatei')):
            ttk.Label(b, text=text, foreground=COLORS['muted']).grid(row=self._row(b), column=0, sticky='w', pady=(4, 0))
            line = ttk.Frame(b)
            line.grid(row=self._row(b), column=0, sticky='ew')
            line.columnconfigure(0, weight=1)
            label = PathLabel(line)   # lange Pfade werden in der Mitte gekürzt, der ganze Pfad steht im Hinweis
            label.grid(row=0, column=0, sticky='ew')
            self.path_labels[key] = label
            ttk.Button(line, text='Öffnen' if key != 'db' else 'Ordner öffnen',
                       command=lambda k=key: self._open_location(k)).grid(row=0, column=1, padx=(8, 0))

        self._title(b, 'Datensicherung')
        self._hint(b, 'Legt eine vollständige Kopie der Datenbank (Videos, Kommentare, Verlauf, Ziele) an einer Stelle deiner Wahl an – '
                      'auch während ein Abruf läuft.', pady=(0, 4))
        self.backup_btn = ttk.Button(b, text='Sicherungskopie anlegen…', command=self.backup)
        self.backup_btn.grid(row=self._row(b), column=0, sticky='w')
        self.backup_label = ttk.Label(b, text='', wraplength=520, justify='left')
        self.backup_label.grid(row=self._row(b), column=0, sticky='w', pady=(6, 0))
        self.hints.append(self.backup_label)

        self._title(b, 'Version')
        try:
            from .. import __version__ as version
        except ImportError:
            version = '?'
        ttk.Label(b, text=f'Socialscraper {version}  ·  Python {platform.python_version()}  ·  {platform.system()} {platform.release()}',
                  foreground=COLORS['muted']).grid(row=self._row(b), column=0, sticky='w')

        adv = ttk.LabelFrame(b, text='Erweitert', padding=8)
        adv.grid(row=self._row(b), column=0, sticky='ew', pady=(18, 8))
        adv.columnconfigure(0, weight=1)
        hint = ttk.Label(adv, text='Nur für Fortgeschrittene. Normalerweise brauchst du die Datei settings.toml nicht anzufassen.',
                         foreground=COLORS['muted'], wraplength=480, justify='left')
        hint.grid(row=0, column=0, sticky='w')
        self.hints.append(hint)
        btns = ttk.Frame(adv)
        btns.grid(row=1, column=0, sticky='w', pady=(6, 0))
        ttk.Button(btns, text='settings.toml im Editor öffnen',
                   command=lambda: self.app.open_path(pathlib.Path(self.root()) / 'settings.toml', edit=True)).pack(side='left')
        self.undo_btn = ttk.Button(btns, text='Letzte Änderung rückgängig', command=self.undo_last)
        self.undo_btn.pack(side='left', padx=(8, 0))
        ttk.Button(btns, text='Auf Standardwerte zurücksetzen', command=self.reset_to_defaults).pack(side='left', padx=(8, 0))

    def show_check(self):
        self.syscheck_btn.configure(state='disabled')
        self.check_label.configure(text='Prüfe …', foreground=COLORS['muted'])

        def done(result, err):
            self.syscheck_btn.configure(state='normal')
            if err:
                self.check_label.configure(text=friendly(err), foreground=COLORS['err'])
                return
            rows, ok = result
            self.last_result['check'] = (rows, ok)
            tail = '\nAlles bereit.' if ok else '\nBitte Einrichten.cmd erneut ausführen.'
            self.check_label.configure(text='\n'.join(rows) + tail, foreground=COLORS['ok' if ok else 'warn'])
        run_async(self, system_check, done)

    def _open_location(self, key):
        root = pathlib.Path(self.root())
        if key == 'program':
            target = root
        elif key == 'data':
            target = root / 'data'
            target.mkdir(parents=True, exist_ok=True)
        else:
            from .. import autologic
            target = autologic.resolve_db(root).parent
        self.app.open_path(target)

    def _update_paths(self):
        from .. import autologic
        root = pathlib.Path(self.root())
        db = autologic.resolve_db(root)
        size = ''
        try:
            if db.is_file():
                size = f'  ({fl.human_size(db.stat().st_size)})'
        except OSError:
            pass
        self.path_labels['program'].set(str(root))
        self.path_labels['data'].set(str(root / 'data'))
        self.path_labels['db'].set(str(db), size or '  (noch nicht angelegt)')

    def backup(self):
        from .. import autologic
        src = autologic.resolve_db(self.root())
        if not src.is_file():
            messagebox.showinfo(TITLE, 'Es gibt noch keine Datenbank – rufe zuerst ein Video ab.')
            return
        import datetime
        name = f'scrape-sicherung-{datetime.datetime.now():%Y-%m-%d_%H-%M}.db'
        dest = filedialog.asksaveasfilename(title='Sicherungskopie speichern unter', initialfile=name, defaultextension='.db',
                                            filetypes=[('SQLite-Datenbank', '*.db'), ('Alle Dateien', '*.*')])
        if not dest:
            return
        self.backup_btn.configure(state='disabled')
        self.backup_label.configure(text='Sicherung läuft …', foreground=COLORS['muted'])

        def done(result, err):
            self.backup_btn.configure(state='normal')
            if err:
                text, kind = '✖ ' + friendly(err), 'err'
                messagebox.showerror(TITLE, friendly(err))
            else:
                text = (f'✔ Gesichert: {result["videos"]} Videos, {result["comments"]} Kommentare '
                        f'({fl.human_size(result["bytes"])}) in {dest}')
                kind = 'ok'
                state = getattr(self.app, 'ui_state', None)
                if state is not None:
                    from .. import repo
                    state.set('last_backup_at', repo.iso())   # die Übersicht weiß so, wann zuletzt gesichert wurde
            self.last_result['backup'] = (kind, text)
            self.backup_label.configure(text=text, foreground=COLORS[kind])
            self.app.log(text if kind == 'ok' else 'Datensicherung fehlgeschlagen.', 'OK' if kind == 'ok' else 'ERROR')
        run_async(self, lambda: fl.backup_database(src, dest), done, daemon=False)   # schreibt eine Datei: läuft auch nach dem Schließen zu Ende

    def undo_last(self):
        if not messagebox.askyesno(TITLE, 'Die Einstellungen aus settings.toml auf den Stand vor der letzten Speicherung zurücksetzen?'):
            return
        try:
            core.restore_settings_backup(self.root())
        except ValueError as error:
            messagebox.showwarning(TITLE, str(error))
            return
        self.app.set_status('Vorige Einstellungen wiederhergestellt.', 'ok')
        self._load_from_disk()

    def reset_to_defaults(self):
        if not messagebox.askyesno(TITLE, 'settings.toml auf die Standardwerte zurücksetzen?\n\nDie jetzige Datei bleibt als '
                                          'settings.toml.defekt erhalten. Deine Cloud-/PULSE-Einstellungen bleiben unberührt.'):
            return
        try:
            core.reset_settings(fl.DEFAULT_SETTINGS_TEXT, self.root())
        except ValueError as error:
            messagebox.showerror(TITLE, str(error))
            return
        self.app.log('settings.toml auf Standardwerte zurückgesetzt (alte Datei: settings.toml.defekt).', 'WARNING')
        self._load_from_disk()

    # ---------- Navigation ----------

    def _render_nav(self):
        marks = self._marks()
        sel = self.current
        self.nav.delete(0, 'end')
        for key, title in SECTIONS:
            mark = marks.get(key, '')
            self.nav.insert('end', f'  {mark} {title}' if mark else f'     {title}')
        self.nav.selection_clear(0, 'end')
        index = [k for k, _t in SECTIONS].index(sel)
        self.nav.selection_set(index)
        self.nav.activate(index)

    def _marks(self):
        marks = {}
        if self.baseline is not None:
            for section in fl.changed_sections(self._collect_quiet(), self.baseline):
                marks[section] = '●'
        if self._ai_changed():
            marks['ki'] = '●'
        for key in self.errors:
            marks[fl.SECTION_OF.get(key, 'ki')] = '⚠'
        return marks

    def _nav_selected(self, _event=None):
        sel = self.nav.curselection()
        if sel:
            self.show_section(SECTIONS[sel[0]][0], from_nav=True)

    def show_section(self, key, from_nav=False):
        self.current = key
        for k, sf in self.sections.items():
            if k == key:
                sf.grid()
                sf.to_top()
            else:
                sf.grid_remove()
        if not from_nav:
            self._render_nav()
        if key == 'dienst':
            self.service.refresh()

    def _focus_first(self):
        for child in self.sections[self.current].body.winfo_children():
            if child.winfo_class() in ('TEntry', 'TCombobox', 'TSpinbox', 'TCheckbutton'):
                child.focus_set()
                return 'break'
            for sub in child.winfo_children():
                if sub.winfo_class() in ('TEntry', 'TCombobox', 'TSpinbox'):
                    sub.focus_set()
                    return 'break'

    # ---------- Werte ----------

    def _choice_value(self, key):
        shown = self.choice_vars[key].get()
        return next((v for t, v in CHOICES[key] if t == shown), CHOICES[key][0][1])

    def _collect_quiet(self):
        raw = {}
        for key in SIMPLE_VARS:
            try:
                raw[key] = self.vars[key].get()
            except tk.TclError:
                raw[key] = False if key in BOOL_KEYS else ''
        for key in CHOICES:
            raw[key] = self._choice_value(key)
        raw['proxies'] = self.proxy_editor.get()
        raw['api_origins'] = self.origin_editor.get()
        if not isinstance(raw['api_token'], str):
            raw['api_token'] = str(raw['api_token'])
        return raw

    collect = _collect_quiet

    def _set_raw(self, raw):
        self._loading = True
        try:
            for key in SIMPLE_VARS:
                self.vars[key].set(raw[key])
            for key, options in CHOICES.items():
                self.choice_vars[key].set(next((t for t, v in options if v == raw[key]), options[0][0]))
            self.proxy_editor.set(raw['proxies'])
            self.origin_editor.set(raw['api_origins'])
        finally:
            self._loading = False

    def _edited(self, key):
        if self._loading:
            return
        if key in self.err_labels and self.errors.pop(key, None) is not None:
            self.err_labels[key].grid_remove()
        self._update_state()

    def _update_state(self):
        keys = fl.changed_keys(self._collect_quiet(), self.baseline)
        self.dirty = bool(keys) or self._ai_changed()
        if self.dirty:
            self.state_label.configure(text='● Ungespeicherte Änderungen', foreground=COLORS['warn'])
        else:
            self.state_label.configure(text='Alles gespeichert.', foreground=COLORS['muted'])
        state = 'normal' if self.dirty else 'disabled'
        self.save_btn.configure(state=state)
        self.reset_btn.configure(state=state)
        self._render_nav()
        self._update_cloud_fields()
        self._apply_mode_view()

    # ---------- Laden ----------

    def _load_from_disk(self):
        root = self.root()
        raw = fl.default_raw()
        self.settings_problem = self.integration_problem = None
        try:
            raw.update(fl.settings_to_raw(core.read_settings(root)))
        except (ValueError, OSError) as error:
            self.settings_problem = str(error) or 'Die Datei lässt sich nicht lesen.'
        integration = _integration()
        self.integration_missing = integration is None
        if integration is not None:
            cfg, problem = integration.load_with_status(root)
            self.integration_problem = problem
            raw.update(fl.integration_to_raw(cfg))
            self.host = str(cfg['api'].get('host') or '127.0.0.1')
        self.baseline = raw
        self._desc_key = None   # Zustand der Proxys neu abfragen
        self._set_raw(raw)
        self.errors = {}
        for label in self.err_labels.values():
            label.grid_remove()
        self._ai_load()
        self._update_banner()
        self._update_state()
        self._update_example()
        self._update_paths()
        self._update_cloud_state()
        self.undo_btn.configure(state='normal' if (pathlib.Path(root) / core.SETTINGS_BACKUP).is_file() else 'disabled')
        self.cloud_toggle_state()

    def cloud_toggle_state(self):
        missing = self.integration_missing
        for w in [self.api_cb, self.test_api_btn, self.copy_url_btn] + self.cloud_btns:
            if missing:
                w.configure(state='disabled')
        if missing:
            self.api_result.configure(text='Funktion noch nicht verfügbar (integration.py fehlt).', foreground=COLORS['warn'])
            self.cloud_result.configure(text='Funktion noch nicht verfügbar (integration.py fehlt).', foreground=COLORS['warn'])

    def refresh(self):
        self.service.refresh()
        self._desc_key = None   # Zustand der Proxys neu abfragen
        self._update_paths()
        if not self.dirty:
            self._load_from_disk()
        else:
            self._update_cloud_state()

    # ---------- Von außen: Übersicht, Banner, Hilfe ----------

    def external_proxy_mode(self, mode):
        """Der Proxy-Modus wurde außerhalb des Formulars geändert (Knopf auf der Übersicht oder im Banner): Formular und
        Vergleichswert ziehen nach; andere, noch ungespeicherte Änderungen bleiben erhalten."""
        if self.baseline is not None:
            self.baseline['proxy_mode'] = mode
        self._loading = True
        try:
            self.vars['proxy_mode'].set(mode)
        finally:
            self._loading = False
        self._desc_key = None
        self._update_state()

    def focus_item(self, spec):
        """Zu einem Bereich oder Feld springen: {'section': 'proxys', 'field': 'proxy_mode' | 'ai_key'} und es markieren."""
        section = (spec or {}).get('section')
        if section in self.sections:
            self.show_section(section)
        field = (spec or {}).get('field')
        sf = self.sections.get(self.current)
        if field == 'proxy_mode':
            card = self.mode_cards.get(self.vars['proxy_mode'].get())
            if card is not None:
                if sf is not None:
                    sf.see(card)
                self._flash_card(card, 6)
        elif field == 'ai_key':
            self.ai_key_entry.focus_set()
            try:
                self.ai_key_entry.selection_range(0, 'end')
            except tk.TclError:
                pass
            if sf is not None:
                sf.see(self.ai_key_entry)
            self._ai_key_state()   # erst den Grundtext setzen, dann den Pfeil-Satz einmal davor (sonst steht er bei jedem Sprung erneut da)
            self.ai_key_state.configure(text='⬆ Hier trägst du den Schlüssel ein. ' + self.ai_key_state.cget('text'))
        return True

    def _flash_card(self, card, times):
        """Rahmen einer Karte kurz orange blinken lassen, damit man sieht, wohin die Übersicht gesprungen ist."""
        if times <= 0 or not card.winfo_exists():
            self._render_cards()
            return
        color = '#f59e0b' if times % 2 == 0 else self.CARD_ON
        try:
            card.configure(highlightbackground=color, highlightcolor=color)
        except tk.TclError:
            return
        track_after(self, 220, lambda: self._flash_card(card, times - 1))

    def _render_cards(self):
        try:
            self._apply_mode_view(describe=False)
        except tk.TclError:
            pass

    # ---------- Hinweis bei kaputten Dateien ----------

    def _update_banner(self):
        for child in self.banner.winfo_children():
            child.destroy()
        problems = []
        if self.settings_problem:
            problems.append('settings')
        if self.integration_problem:
            problems.append('integration')
        if not problems:
            self.banner.grid_remove()
            return
        self.banner.grid()
        self.banner.columnconfigure(0, weight=1)
        r = 0
        if self.settings_problem:
            box = ttk.Frame(self.banner)
            box.grid(row=r, column=0, sticky='ew', pady=(0, 6))
            box.columnconfigure(0, weight=1)
            ttk.Label(box, text='⚠ settings.toml ist beschädigt oder enthält ungültige Werte. Angezeigt werden Standardwerte. '
                                f'Grund: {self.settings_problem[:200]}',
                      foreground=COLORS['err'], wraplength=760, justify='left').grid(row=0, column=0, sticky='w')
            btns = ttk.Frame(box)
            btns.grid(row=1, column=0, sticky='w', pady=(4, 0))
            ttk.Button(btns, text='Auf Standardwerte zurücksetzen', command=self.reset_to_defaults).pack(side='left')
            if (pathlib.Path(self.root()) / core.SETTINGS_BACKUP).is_file():
                ttk.Button(btns, text='Letzte Sicherung zurückholen', command=self.undo_last).pack(side='left', padx=(8, 0))
            ttk.Button(btns, text='Datei im Editor öffnen',
                       command=lambda: self.app.open_path(pathlib.Path(self.root()) / 'settings.toml', edit=True)).pack(side='left', padx=(8, 0))
            r += 1
        if self.integration_problem:
            box = ttk.Frame(self.banner)
            box.grid(row=r, column=0, sticky='ew', pady=(0, 6))
            ttk.Label(box, text='⚠ ' + self.integration_problem, foreground=COLORS['err'], wraplength=760, justify='left'
                      ).grid(row=0, column=0, sticky='w')
            btns = ttk.Frame(box)
            btns.grid(row=1, column=0, sticky='w', pady=(4, 0))
            if 'beschädigt' in self.integration_problem:
                # Nur dann legt integration._write beim Speichern die Kopie .defekt an. Eine gerade nicht lesbare (gesperrte)
                # Datei würde ohne Kopie überschrieben – dafür gibt es nur „Erneut laden“.
                ttk.Button(btns, text='Datei ersetzen (Kopie bleibt als .defekt)', command=self.replace_integration).pack(side='left')
            else:
                ttk.Button(btns, text='Erneut laden', command=self._load_from_disk).pack(side='left')

    def replace_integration(self):
        integration = _integration()
        if integration is None:
            return
        if not messagebox.askyesno(TITLE, 'Die beschädigte Datei data/integration.json durch eine neue mit Standardwerten ersetzen?\n\n'
                                          'Eine Kopie bleibt als integration.json.defekt erhalten. Token und Cloud-Schlüssel musst du danach neu eintragen.'):
            return
        try:
            integration.save(integration.default_config(), self.root())
        except ValueError as error:
            messagebox.showerror(TITLE, str(error))
            return
        self.app.log('integration.json ersetzt (alte Datei: integration.json.defekt).', 'WARNING')
        self._load_from_disk()

    # ---------- Speichern ----------

    def _show_errors(self, errors):
        self.errors = dict(errors)
        for key, label in self.err_labels.items():
            if key in errors:
                label.configure(text='✖ ' + errors[key])
                label.grid()
            else:
                label.grid_remove()
        self._render_nav()

    def save(self):
        raw = self._collect_quiet()
        settings, patch, errors = fl.validate_form(raw)
        keys = fl.changed_keys(raw, self.baseline)
        integration = _integration()
        service_running = bool((self.service.last_status or {}).get('running'))
        if not errors and raw['api_enabled'] and ('api_port' in keys or 'api_enabled' in keys) and not service_running \
                and fl.port_in_use(patch['api'].get('port', 0), self.host):
            errors['api_port'] = (f'Der Port {patch["api"]["port"]} ist schon von einem anderen Programm belegt. '
                                  'Wähle einen anderen (z. B. 8766).')
        if not errors and integration is not None and any(k in fl.INTEGRATION_KEYS for k in keys):
            try:
                cfg = integration.load_for_update(self.root())
            except ValueError as error:
                errors['sync_url'] = str(error)
                cfg = integration.default_config()
            cfg['api'].update(patch['api'])
            cfg['sync'].update(patch['sync'])
            for problem in ([] if errors else integration.validate(integration._clean(cfg))):
                errors.setdefault('api_enabled' if 'chnittstelle' in problem or 'Token' in problem else 'sync_url', problem)
        if errors:
            self._show_errors(errors)
            first = next(k for k in fl.ALL_KEYS if k in errors)
            self.show_section(fl.SECTION_OF[first])
            self.app.set_status(f'Bitte {len(errors)} Eingabe(n) prüfen – Hinweise stehen direkt am Feld.', 'err')
            return False
        ai_cfg, ai_problem = self._ai_collect() if self._ai_changed() else (None, '')
        if ai_problem:
            self._show_errors({'ai': ai_problem})
            self.show_section('ki')
            self.app.set_status('Bitte die KI-Einstellungen prüfen – der Hinweis steht direkt am Formular.', 'err')
            return False
        self._show_errors({})
        if self.settings_problem and any(k in fl.SETTINGS_KEYS for k in keys):
            messagebox.showwarning(TITLE, 'settings.toml ist beschädigt. Setze sie zuerst zurück (Hinweis oben im Fenster).')
            return False
        if integration is None and any(k in fl.INTEGRATION_KEYS for k in keys):
            messagebox.showinfo(TITLE, 'Funktion noch nicht verfügbar: Die PULSE-Anbindung fehlt in dieser Installation.')
            return False
        done_settings = False
        try:
            changed_settings = {k: settings[k] for k in keys if k in fl.SETTINGS_KEYS}
            if changed_settings:
                core.write_settings(changed_settings, self.root())
                done_settings = True
        except ValueError as error:
            messagebox.showerror(TITLE, f'Die Einstellungen wurden nicht gespeichert.\n\n{error}')
            self.app.set_status('Speichern fehlgeschlagen.', 'err')
            return False
        if any(k in fl.INTEGRATION_KEYS for k in keys):
            try:
                cfg = integration.load_for_update(self.root())
                cfg['api'].update(patch['api'])
                cfg['sync'].update(patch['sync'])
                integration.save(cfg, self.root())
            except ValueError as error:
                note = 'Die Abruf-Einstellungen sind gespeichert, ' if done_settings else ''
                messagebox.showerror(TITLE, f'{note}die Anbindung (PULSE/Cloud) wurde nicht gespeichert.\n\n{error}')
                self.app.set_status('Speichern teilweise fehlgeschlagen.', 'err')
                self._load_from_disk()
                return False
        if ai_cfg is not None:
            try:
                aiconfig.save(ai_cfg, self.root())
            except ValueError as error:
                messagebox.showerror(TITLE, f'Die KI-Einstellungen wurden nicht gespeichert.\n\n{aiconfig.scrub(error, ai_cfg)}')
                self.app.set_status('Speichern teilweise fehlgeschlagen.', 'err')
                self._load_from_disk()
                return False
            self.app.log('KI-Einstellungen gespeichert (der Schlüssel steht nie im Protokoll).', 'OK')
        self.app.log(f'Einstellungen gespeichert ({fl.describe_changes(keys) if keys else "KI / Stimmung"}).', 'OK')
        self.app.set_status('Einstellungen gespeichert.', 'ok')
        self._load_from_disk()
        overridden = _env_overrides(changed_settings)
        if overridden:
            # Die Datei ist geschrieben, aber eine Umgebungsvariable SS_… legt den Wert fest – das Formular springt zurück.
            names = ', '.join(f'„{key}“ (Variable {env})' for key, env in overridden)
            self.app.log(f'Gespeichert, aber ohne Wirkung: {names} wird von einer Umgebungsvariable festgelegt.', 'WARNING')
            self.app.set_status('Gespeichert – aber eine Umgebungsvariable überstimmt die Einstellung.', 'warn')
            messagebox.showwarning(TITLE, f'Gespeichert, aber ohne Wirkung: {names} wird gerade von einer Umgebungsvariable '
                                          'festgelegt. Entferne sie und starte das Programm neu, damit deine Einstellung gilt.')
        if any(k.startswith('api_') for k in keys) and service_running:
            self._offer_restart()
        return True

    def _offer_restart(self):
        if messagebox.askyesno(TITLE, 'Der Hintergrunddienst läuft noch mit den alten Einstellungen.\n\nJetzt neu starten, damit die Änderung gilt?'):
            self.restart_service()

    def restart_service(self):
        panel = self.service
        panel.stop(ask=False)

        def when_stopped():
            if panel.busy:
                track_after(self, 300, when_stopped)
            elif (panel.last_status or {}).get('running'):
                # Der alte Dienst läuft noch (Stoppen hat nicht geklappt): kein Start, sonst hieße es „Dienst gestartet.“,
                # obwohl weiter die alten Einstellungen gelten.
                self.app.log('Neustart abgebrochen: Der Dienst ließ sich nicht beenden und läuft mit den alten Einstellungen weiter.', 'ERROR')
                self.app.set_status('Der Dienst ließ sich nicht beenden – die neuen Einstellungen gelten noch nicht.', 'err')
            else:
                panel.start()
        track_after(self, 300, when_stopped)

    def reset(self):
        if self.dirty and not messagebox.askyesno(TITLE, 'Ungespeicherte Änderungen verwerfen?'):
            return
        self._load_from_disk()
        self.app.set_status('Änderungen verworfen.', 'muted')


def summarize_sync(action, code, lines):
    """(Text, Farbe) aus der Ausgabe von `sync test|push|pull`; nur die eigenen Ergebniszeilen, nichts Geheimes."""
    shown = [l.rstrip() for l in lines if l.startswith(('OK:', 'Fehler:', 'Fertig:', '  ')) and l.strip()]
    if code == 0:
        head = {'test': 'Verbindung in Ordnung.', 'push': 'Hochladen fertig.', 'pull': 'Aktualisieren fertig.'}[action]
        detail = [l for l in shown if not l.startswith(('OK:',))]
        return '✔ ' + head + ('\n' + '\n'.join(detail) if detail else ''), 'ok'
    error = next((l for l in shown if l.startswith('Fehler:')), '')
    detail = [l for l in shown if l.startswith('  ') and not re.search(r': 0 ', l)]
    text = '✖ ' + (error or {'test': 'Verbindung fehlgeschlagen.', 'push': 'Hochladen fehlgeschlagen.',
                            'pull': 'Aktualisieren fehlgeschlagen.'}[action] + ' Details im Protokoll.')
    if detail:
        text += '\n' + '\n'.join(detail)
    return text, 'err'
