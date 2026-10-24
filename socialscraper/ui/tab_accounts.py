"""Tab "Konten": mehrere Konten je Plattform, Anmeldung, Status, Zugangsdaten."""
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import guicore as core
from .. import proxylogic, repo
from . import logic
from .common import COLORS, FONT_B, TITLE, ModeBanner, Tab
from .proxy_dialog import ProxyDialog
from .widgets import PAD, ColumnFitter, HelpDot, TabHelpers, Tooltip, ask_form
from .workflow_components import busy as app_busy

STATE_COLOR = {'verified': 'ok', 'saved': 'muted', 'missing': 'muted', 'none': 'muted', 'expired': 'warn', 'error': 'err'}
# Farbe der Rotationszeile je Zustand (rotation.account_state)
ROT_COLOR = {'bereit': 'ok', 'abkuehlung': 'warn', 'tageslimit': 'warn', 'keine_anmeldung': 'muted'}
CHECKED, UNCHECKED = '☑', '☐'


class AccountsTab(TabHelpers, Tab):
    def build(self):
        f = self
        f.configure(padding=PAD)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1, minsize=96)     # die Liste bleibt auch im kleinsten Fenster sichtbar
        self.accounts = {}      # iid -> Konto
        self.rotation = {'rows': {}, 'summary': {}, 'hints': {}}   # Rotationsstatus (logic.load_rotation)
        self.verified = {}     # Kontoschlüssel -> Zeitpunkt der letzten bestätigten Prüfung (nur diese Sitzung)
        self._verified_files = {}
        self._needs_login = set()
        self._failed_files = {}
        self._last_result = {}
        self._last_files = {}
        self._primary_action = None
        head = ttk.Frame(f); head.grid(row=0, column=0, sticky='ew')
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text='Deine Konten', style='Section.TLabel').grid(row=0, column=0, sticky='w')
        self.info = ttk.Label(head, text='', foreground=COLORS['muted'])
        self.info.grid(row=0, column=1, sticky='e')
        sub = ttk.Frame(head)
        sub.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Label(sub, text='Konto auswählen → nächsten Schritt öffnen.   Begriffe:',
                  foreground=COLORS['muted']).pack(side='left')
        for key, word in (('rotation', 'Rotation'), ('cooldown', 'Abkühlung'), ('login', 'Anmeldung')):
            ttk.Label(sub, text=word, foreground=COLORS['muted']).pack(side='left', padx=(8, 0))
            HelpDot(sub, key).pack(side='left')
        self.btn = {}
        self.btn['add'] = ttk.Button(head, text='+ Vorhandenes Konto', command=self.add, style='Primary.TButton')
        self.btn['add'].grid(row=0, column=2, padx=(12, 0))
        self.app.register_busy(self.btn['add'])
        self.btn['create'] = ttk.Button(head, text='Neues Konto registrieren …', command=self.create_account)
        self.btn['create'].grid(row=1, column=2, padx=(12, 0))
        self.app.register_busy(self.btn['create'])
        self.mode_banner = ModeBanner(head, lambda: core.ROOT, app=self.app)
        self.mode_banner.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(6, 0))
        self.mode_banner.grid_remove()
        # Rotationsübersicht je Plattform (eine Zeile pro Plattform, zwei Spalten)
        self.overview = ttk.Frame(head)
        self.overview.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(4, 0))
        self.overview.columnconfigure(0, weight=1, uniform='ov')
        self.overview.columnconfigure(1, weight=1, uniform='ov')
        self.overview_labels = {}
        for number, platform in enumerate(repo.LOGIN_PLATFORMS):
            label = ttk.Label(self.overview, text='', foreground=COLORS['muted'])
            label.grid(row=number // 2, column=number % 2, sticky='w', padx=(0, 10))
            self.overview_labels[platform] = label
        self.overview.bind('<Configure>', lambda event: [lab.configure(wraplength=max(180, event.width // 2 - 14))
                                                         for lab in self.overview_labels.values()])

        cols = ('platform', 'name', 'country', 'default', 'status', 'rot', 'proxy', 'rstate', 'today')
        tree_box = ttk.Frame(f)
        tree_box.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(4, 4))
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_box, columns=cols, show='headings', selectmode='browse', height=4)
        self.rot_column = f'#{cols.index("rot") + 1}'
        for c, h, w, a, minw, stretch in (
                ('platform', 'Plattform', 72, 'w', 60, False), ('name', 'Name', 115, 'w', 70, False),
                ('country', 'Land', 38, 'center', 34, False), ('default', 'Standard', 74, 'center', 60, False),
                ('status', 'Anmeldung', 140, 'w', 100, True), ('rot', 'In Rotation', 74, 'center', 66, False),
                ('proxy', 'Proxy', 140, 'w', 80, True), ('rstate', 'Rotation', 150, 'w', 96, True),
                ('today', 'Heute', 44, 'center', 40, False)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor=a, minwidth=minw, stretch=stretch)
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.empty = ttk.Frame(self.tree, padding=20)
        ttk.Label(self.empty, text='Noch keine Konten hinterlegt', style='Section.TLabel').pack()
        ttk.Label(self.empty, text='Füge ein vorhandenes Konto hinzu oder bereite eine Registrierung vor.',
                  style='Muted.TLabel', wraplength=440).pack(pady=(6, 0))
        sb = ttk.Scrollbar(tree_box, command=self.tree.yview); self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky='ns')
        hs = ttk.Scrollbar(tree_box, orient='horizontal', command=self.tree.xview)
        hs.grid(row=1, column=0, sticky='ew'); hs.grid_remove()    # nur wenn die Spalten nicht passen
        # Name und Anmeldung sind das Wichtigste und behalten mehr Platz; Proxy und Rotation dürfen schmaler werden
        # (der volle Text erscheint als Hinweis beim Darüberfahren und im Feld „Ausgewähltes Konto“).
        self.fit = ColumnFitter(self.tree, {'platform': (60, 0), 'name': (100, 3), 'country': (34, 0), 'default': (60, 0),
                                            'status': (140, 3), 'rot': (60, 0), 'proxy': (80, 2), 'rstate': (130, 2),
                                            'today': (40, 0)}, hs, arrow=0)
        for key, color in STATE_COLOR.items():
            self.tree.tag_configure(key, foreground=COLORS[color])
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self._update_buttons())
        self._low = False
        self.bind('<Configure>', self._fit_height, add='+')
        self.tree.bind('<Delete>', lambda _e: self.remove())
        self.tree.bind('<Double-1>', self._double_click, add='+')
        self.tree.bind('<Return>', lambda _e: self.primary())
        self.tree.bind('<Control-a>', lambda _e: 'break')   # eine Auswahl reicht; kein Mehrfach-Markieren
        self.tree.bind('<Button-1>', self._click_cell, add='+')

        detail = ttk.LabelFrame(f, text='Ausgewähltes Konto', padding=8)
        detail.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(0, 4))
        detail.columnconfigure(0, weight=1)
        detail.columnconfigure(1, weight=1)
        self.account_name = ttk.Label(detail, text='Noch kein Konto ausgewählt', font=FONT_B, wraplength=450, justify='left')
        self.account_name.grid(row=0, column=0, sticky='w')
        self.account_state = ttk.Label(detail, text='', foreground=COLORS['muted'], wraplength=300, justify='left')
        self.account_state.grid(row=0, column=1, sticky='w', padx=(14, 0))
        self.next_step = ttk.Label(detail, text='Klicke oben ein Konto an.', wraplength=650, justify='left')
        self.next_step.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        # Rotation und fester Proxy des gewählten Kontos
        rot_row = ttk.Frame(detail); rot_row.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(5, 0))
        self.rot_var = tk.BooleanVar(value=True)
        self.rot_check = ttk.Checkbutton(rot_row, text='In Rotation  ', variable=self.rot_var, command=self.toggle_rotation)
        self.rot_check.pack(side='left')
        self.app.register_busy(self.rot_check)
        self.rot_state = ttk.Label(rot_row, text='', foreground=COLORS['muted'], wraplength=330, justify='left')
        self.rot_state.pack(side='left', padx=(6, 0))
        self.proxy_line = ttk.Label(rot_row, text='', foreground=COLORS['muted'], wraplength=330, justify='right')
        self.proxy_line.pack(side='right', padx=(10, 0))
        self.hint_line = ttk.Label(detail, text='', foreground=COLORS['warn'], wraplength=650, justify='left')
        self.hint_line.grid(row=3, column=0, columnspan=2, sticky='ew')
        self.hint_line.grid_remove()    # nur sichtbar, wenn es einen Hinweis gibt (spart Platz)
        self.fix_row = ttk.Frame(detail)    # Lösungsknöpfe, wenn etwas blockiert (zum Beispiel „Dienst stoppen“)
        self.fix_row.grid(row=5, column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.fix_row.grid_remove()
        detail.bind('<Configure>', lambda event: (
            self.next_step.configure(wraplength=max(200, event.width - 24)),
            self.proxy_line.configure(wraplength=max(150, int((event.width - 24) * 0.42))),
            self.rot_state.configure(wraplength=max(150, int((event.width - 24) * 0.42))),
            self.hint_line.configure(wraplength=max(200, event.width - 24)),
            self.account_name.configure(wraplength=max(200, int((event.width - 24) * 0.55))),
            self.account_state.configure(wraplength=max(120, int((event.width - 24) * 0.4)))))
        actions = ttk.Frame(detail); actions.grid(row=4, column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.btn['primary'] = ttk.Button(actions, text='Konto auswählen', style='Big.TButton', command=self.primary)
        self.btn['primary'].pack(side='left')
        self.app.register_busy(self.btn['primary'])
        self.more_btn = ttk.Button(actions, text='Weitere Möglichkeiten ▾', command=self.toggle_more)
        self.more_btn.pack(side='left', padx=(10, 0))
        self.app.register_busy(self.more_btn)
        for key, text, fn in (('proxy', 'Proxy festlegen …', self.set_proxy), ('cooldown', 'Abkühlung aufheben', self.clear_cooldown),
                              ('limit', 'Tageslimit …', self.set_limit)):
            self.btn[key] = ttk.Button(actions, text=text, command=fn)
            self.btn[key].pack(side='left', padx=(10 if key == 'proxy' else 6, 0))
            self.app.register_busy(self.btn[key])
        self.more = ttk.Frame(f)
        self.more.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(0, 6))
        rows = [
            [('rename', 'Umbenennen …', self.rename), ('default', 'Als Standard festlegen', self.make_default),
             ('remove', 'Konto entfernen', self.remove), ('logout', 'Abmelden', self.logout)],
            [('login', 'Im Browser anmelden', lambda: self.login('qr')), ('check', 'Status prüfen', self.check)],
            [('login_pw', 'Mit gespeicherten Zugangsdaten', lambda: self.login('password')),
             ('import', 'Anmeldung aus Datei übernehmen …', self.import_file), ('creds', 'Zugangsdaten-Datei öffnen', self.open_credentials)],
            [('tiktok_username', 'TikTok-Benutzername …', self.set_tiktok_username)],
        ]
        for number, options in enumerate(rows):
            row = ttk.Frame(self.more); row.grid(row=number, column=0, sticky='w', pady=(0, 4))
            for key, text, fn in options:
                self.btn[key] = ttk.Button(row, text=text, command=fn)
                self.btn[key].pack(side='left', padx=(0, 6))
                self.app.register_busy(self.btn[key])
        self.more.grid_remove()
        Tooltip(self.btn['login'], 'Öffnet einmalig ein sichtbares Browserfenster. Dort meldest du dich selbst an.')
        Tooltip(self.btn['import'], 'Für Server ohne Bildschirm: Cookie-Datei (JSON) aus einem angemeldeten Browser übernehmen.')
        Tooltip(self.btn['creds'], 'Legt für das gewählte Konto einen leeren Eintrag an und öffnet die Datei im Editor. '
                                   'Passwörter werden nie angezeigt oder protokolliert.')
        ttk.Label(f, text='YouTube braucht hier kein Konto. Für Instagram, X und TikTok-Kommentare meldest du dich an; bei Reddit ist die Anmeldung freiwillig.',
                  foreground=COLORS['muted'], wraplength=800).grid(row=4, column=0, columnspan=2, sticky='w', pady=(2, 0))
        self._update_buttons()

    # ---------- Anzeige ----------

    @staticmethod
    def _db():
        return core.db_path()

    def refresh(self):
        self.mode_banner.update_mode()
        keep = self.tree.selection()
        try:
            accounts = logic.load_accounts(self._db())
        except Exception as error:
            self.tree.delete(*self.tree.get_children())
            self.accounts.clear()
            self.info.configure(text='Konten nicht lesbar', foreground=COLORS['err'])
            self.app.log(f'Konten konnten nicht geladen werden: {logic.friendly_error(error)}', 'ERROR')
            self._update_buttons()
            return
        self.tree.delete(*self.tree.get_children())
        order = list(core.PLATFORMS)
        accounts = sorted(accounts, key=lambda a: (order.index(a['platform']) if a['platform'] in order else len(order),
                                                   not a['is_default'], a['label'].lower()))
        self.accounts = {str(a['id']): a for a in accounts}
        self.rotation = self._load_rotation()
        for a in accounts:
            state, text = self._state(a)
            rot = self.rotation['rows'].get(a['id'])
            self.tree.insert('', 'end', iid=str(a['id']), tags=(state,), values=(
                core.PLATFORMS[a['platform']]['name'], a['label'], a['country'].upper(),
                '★ Standard' if a['is_default'] else '', ('● ' if state == 'verified' else '○ ') + text,
                CHECKED if (rot is None or rot['in_rotation']) else UNCHECKED,
                (rot or {}).get('proxy') or 'keiner', (rot or {}).get('text', ''), str((rot or {}).get('used_today', 0))))
        self.fit.refit()
        for platform, label in self.overview_labels.items():
            summary = self.rotation['summary'].get(platform)
            label.configure(text=f'{core.PLATFORMS[platform]["name"]}: {summary}' if summary else '')
        for iid in keep:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
        n = len(accounts)
        if n:
            self.empty.place_forget()
        else:
            self.empty.place(relx=.5, rely=.5, anchor='center')
        states = [self._state(a)[0] for a in accounts]
        logged_in, checked = sum(x in ('verified', 'saved') for x in states), sum(x == 'verified' for x in states)
        self.info.configure(text=core.plural(n, 'Konto', 'Konten') + f' · {logged_in} angemeldet' + (f' · {checked} bestätigt' if checked else ''),
                            foreground=COLORS['muted'])
        self._update_buttons()

    def _load_rotation(self):
        """Rotationsstatus aller Konten; bei einem Lesefehler bleibt die Liste benutzbar (nur ohne Rotationsspalten)."""
        try:
            return logic.load_rotation(self._db())
        except Exception as error:
            self.app.log(f'Rotationsstatus konnte nicht gelesen werden: {logic.friendly_error(error)}', 'WARNING')
            return {'rows': {}, 'summary': {}, 'hints': {}}

    @staticmethod
    def _file_signature(acc):
        try:
            stat = core.cookie_file(acc['platform'], acc['identity']).stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def _state(self, acc):
        state, text = core.account_status(acc['platform'], core.ROOT, acc['identity'])
        identity = acc['identity']
        signature = self._file_signature(acc)
        if identity in self._last_files and self._last_files[identity] != signature:
            self._last_result.pop(identity, None)
            self._last_files.pop(identity, None)
        if identity in self.verified and (state != 'saved' or self._verified_files.get(identity) != signature):
            self.verified.pop(identity, None)
            self._verified_files.pop(identity, None)
        if identity in self._needs_login:
            if self._failed_files.get(identity) != signature:
                self._needs_login.discard(identity)
                self._failed_files.pop(identity, None)
                self._last_result.pop(identity, None)
            elif state == 'saved':
                return 'expired', 'Erneut anmelden'
        if state == 'saved' and identity in self.verified:
            return 'verified', 'Anmeldung bestätigt'
        if state == 'saved':
            return 'saved', 'Anmeldung gespeichert · noch nicht geprüft'
        if state == 'error':
            return 'error', 'Anmeldung nicht lesbar'
        return state, text

    def focus_item(self, spec):
        """Sprung von der Übersicht: das Konto `account_id` in der Liste markieren."""
        iid = str((spec or {}).get('account_id'))
        if not self.tree.exists(iid):
            self.refresh()
        if not self.tree.exists(iid):
            return False
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.tree.focus_set()
        return True

    def current(self):
        sel = self.tree.selection()
        return self.accounts.get(sel[0]) if sel else None

    def _update_buttons(self):
        if app_busy(self.app):   # includes new background operations
            self._update_detail()   # die Liste bleibt bedienbar: der Bereich „Ausgewähltes Konto“ folgt der Auswahl, die Knöpfe bleiben gesperrt
            return
        has = self.current() is not None
        for key in ('rename', 'default', 'remove', 'login', 'login_pw', 'check', 'logout', 'import', 'creds'):
            self.btn[key].configure(state='normal' if has else 'disabled')
        acc = self.current()
        self.btn['tiktok_username'].configure(state='normal' if acc and acc['platform'] == 'tiktok' else 'disabled')
        if acc is not None and core.PLATFORMS.get(acc['platform'], {}).get('manual_login'):
            for key in ('login_pw', 'creds'):    # Reddit und X: Anmeldung nur von Hand im Browserfenster
                self.btn[key].configure(state='disabled')
        self.btn['primary'].configure(state='normal' if has else 'disabled')
        self.more_btn.configure(state='normal' if has else 'disabled')
        for key in ('proxy', 'cooldown', 'limit'):
            self.btn[key].configure(state='normal' if has else 'disabled')
        self.rot_check.configure(state='normal' if has else 'disabled')
        self._update_detail()

    def _update_detail(self):
        acc = self.current()
        if acc is None:
            self.account_name.configure(text='Noch kein Konto ausgewählt')
            self.account_state.configure(text='')
            self.next_step.configure(text='Klicke oben ein Konto an. Falls dein Konto noch fehlt, klicke auf „Konto hinzufügen“.')
            self.btn['primary'].configure(text='Konto auswählen')
            self._primary_action = None
            self.rot_state.configure(text='')
            self.proxy_line.configure(text='')
            self._show_hint('')
            return
        self._update_rotation_detail(acc)
        state, text = self._state(acc)
        self.account_name.configure(text=f'{core.PLATFORMS[acc["platform"]]["name"]} · {acc["label"]}')
        self.account_state.configure(text=text, foreground=COLORS[STATE_COLOR[state]])
        if state == 'verified':
            hint, button, action = 'Die Anmeldung wurde geprüft. Du kannst jetzt Daten abrufen.', 'Zum Abrufen', lambda: self.app.select_tab('Abrufen')
        elif state == 'saved':
            hint, button, action = 'Die Anmeldung ist gespeichert. Prüfe jetzt, ob sie noch funktioniert.', 'Anmeldung prüfen', self.check
        else:
            hint, button, action = 'Ein Browserfenster öffnet sich. Melde dich dort selbst an.', 'Im Browser anmelden', lambda: self.login('qr')
        note = core.PLATFORMS[acc['platform']].get('note')
        if note and state != 'verified':
            hint = hint + ' ' + note
        previous = self._last_result.get(acc['identity'])
        if previous:
            hint = previous[0] + ' ' + hint
        if proxylogic.is_anonymous(self.mode_banner.mode):
            hint = 'In diesem Verbindungsmodus werden keine Konten benutzt. Klicke oben auf „Auf „Aus“ stellen“ (oder wähle unter Einstellungen → Proxys „Aus“).'
            self.btn['primary'].configure(state='disabled')
        self.next_step.configure(text=hint)
        self.btn['primary'].configure(text=button)
        self._primary_action = action

    def _update_rotation_detail(self, acc):
        """Rotation, Tagesstand und fester Proxy des gewählten Kontos."""
        row = self.rotation['rows'].get(acc['id'])
        if row is None:
            self.rot_state.configure(text='')
            self.proxy_line.configure(text='')
            self._show_hint('')
            return
        self.rot_var.set(bool(row['in_rotation']))
        used = f'Heute benutzt: {row["used_today"]}' + (f' von {row["daily_limit"]} (Tageslimit)' if row['daily_limit'] else '')
        self.rot_state.configure(text=f'{row["text"]} · {used}', foreground=COLORS[ROT_COLOR.get(row['state'], 'muted')])
        if row['has_proxy']:
            proxy = f'Fester Proxy: {row["proxy"]}'
            if proxylogic.is_anonymous(self.mode_banner.mode):
                proxy += ' – gilt nicht in diesem Verbindungsmodus (Konten sind gesperrt).'
        else:
            proxy = 'Kein fester Proxy (normale Verbindung bzw. Proxy-Liste)'
        self.proxy_line.configure(text=proxy)
        hints = self.rotation['hints'].get(acc['platform'], []) if row['in_rotation'] else []
        self._show_hint(('Hinweis: ' + ' '.join(hints)) if hints else '')

    def _show_hint(self, text):
        self.hint_line.configure(text=text)
        (self.hint_line.grid if text else self.hint_line.grid_remove)()

    # ---------- Rotation und fester Proxy ----------

    def _fit_height(self, _event=None):
        """Im niedrigen Fenster (860 x 620) fehlt der Platz für die Liste: die Übersicht je Plattform und der Hinweis darunter
        entfallen dann (die Zahlen stehen oben rechts und beim Konto)."""
        height = self.winfo_height()
        low = 50 < height < 580
        if low != self._low:
            self._low = low
            (self.overview.grid_remove if low else self.overview.grid)()

    def _double_click(self, event):
        """Doppelklick auf eine Zeile = der empfohlene nächste Schritt (anmelden, prüfen oder zum Abrufen); nicht auf dem Häkchen."""
        if self.tree.identify_region(event.x, event.y) != 'cell' or self.tree.identify_column(event.x) == self.rot_column:
            return
        row = self.tree.identify_row(event.y)
        if row and self.tree.exists(row):
            self.tree.selection_set(row)
            self.primary()

    def _click_cell(self, event):
        """Ein Klick auf das Häkchen in der Spalte „In Rotation“ schaltet das Konto um (wird sofort gespeichert)."""
        if self.tree.identify_region(event.x, event.y) != 'cell' or self.tree.identify_column(event.x) != self.rot_column:
            return
        acc = self.accounts.get(self.tree.identify_row(event.y))
        if acc is None or app_busy(self.app):
            return
        row = self.rotation['rows'].get(acc['id'])
        self.tree.selection_set(str(acc['id']))
        self._set_rotation(acc, not (row['in_rotation'] if row else True))

    def toggle_rotation(self):
        acc = self.current()
        if acc is not None and not app_busy(self.app):
            self._set_rotation(acc, bool(self.rot_var.get()))

    def _set_rotation(self, acc, enabled):
        ok, _ = self.attempt('Rotation umschalten', logic.set_rotation, self._db(), acc['id'], enabled)
        if ok:
            self.app.log(f'{self._acc_name(acc)}: ' + ('nimmt an der Rotation teil.' if enabled else 'ist nicht mehr in der Rotation.'))
            self.app.set_status(f'{self._acc_name(acc)}: ' + ('in Rotation.' if enabled else 'nicht in Rotation.'), 'ok')
        self.refresh()

    def set_proxy(self):
        acc = self._need_account()
        if acc is None:
            return
        row = self.rotation['rows'].get(acc['id']) or {}
        current = row.get('proxy') or None
        db = self._db()

        def tester(raw):
            # läuft im Hintergrund-Thread; ohne Eingabe wird der gespeicherte Proxy getestet (sein Passwort sieht das Fenster nie)
            return logic.test_proxy(raw) if raw else logic.test_saved_proxy(db, acc['identity'])
        answer = ProxyDialog(self, self._acc_name(acc), current, tester=tester).run()
        if answer is None:
            return
        action, raw = answer
        if action == 'remove':
            if not messagebox.askyesno(TITLE, f'Den festen Proxy von {self._acc_name(acc)} entfernen?\n\n'
                                              'Danach benutzt das Konto wieder die normale Verbindung (oder die Proxy-Liste).'):
                return
            ok, _ = self.attempt('Proxy entfernen', logic.clear_proxy, db, acc['id'])
            if ok:
                self.app.log(f'{self._acc_name(acc)}: fester Proxy entfernt.', 'OK')
                self.app.set_status(f'Fester Proxy von {self._acc_name(acc)} entfernt.', 'ok')
        else:
            ok, shown = self.attempt('Proxy festlegen', logic.set_proxy, db, acc['id'], raw)
            if ok:
                self.app.log(f'{self._acc_name(acc)}: fester Proxy festgelegt ({shown}).', 'OK')
                self.app.set_status(f'{self._acc_name(acc)} benutzt jetzt nur noch diesen Proxy.', 'ok')
        self.refresh()

    def clear_cooldown(self):
        acc = self._need_account()
        if acc is None:
            return
        row = self.rotation['rows'].get(acc['id']) or {}
        if row.get('state') != 'abkuehlung':
            messagebox.showinfo(TITLE, f'{self._acc_name(acc)} ist gerade nicht in Abkühlung.')
            return
        ok, _ = self.attempt('Abkühlung aufheben', logic.clear_cooldown, self._db(), acc['id'])
        if ok:
            self.app.log(f'{self._acc_name(acc)}: Abkühlung aufgehoben.', 'OK')
            self.app.set_status(f'{self._acc_name(acc)} ist wieder dran.', 'ok')
        self.refresh()

    def set_limit(self):
        acc = self._need_account()
        if acc is None:
            return
        row = self.rotation['rows'].get(acc['id']) or {}

        def validate(values):
            try:
                logic.parse_daily_limit(values['limit'])
            except Exception as error:
                return logic.friendly_error(error)
            return None
        answer = ask_form(self, 'Tageslimit', [
            {'key': 'limit', 'label': 'Abrufe pro Tag', 'value': str(row.get('daily_limit') or ''),
             'hint': 'So viele Videos holt das Programm pro Tag höchstens mit diesem Konto. Leer lassen = unbegrenzt.'},
        ], intro=f'Tageslimit für {self._acc_name(acc)}. Ist es erreicht, wechselt die Rotation zum nächsten Konto.',
            ok_text='Speichern', validator=validate)
        if answer is None:
            return
        ok, limit = self.attempt('Tageslimit speichern', logic.set_daily_limit, self._db(), acc['id'], answer['limit'])
        if ok:
            self.app.set_status(f'{self._acc_name(acc)}: ' + (f'Tageslimit {limit}.' if limit else 'kein Tageslimit.'), 'ok')
        self.refresh()

    def primary(self):
        if not app_busy(self.app) and self._primary_action is not None:
            self._primary_action()

    def toggle_more(self):
        if self.more.winfo_manager():
            self.more.grid_remove()
            self.more_btn.configure(text='Weitere Möglichkeiten ▾')
        else:
            self.more.grid()
            self.more_btn.configure(text='Weitere Möglichkeiten ▴')

    def _need_account(self):
        acc = self.current()
        if acc is None:
            messagebox.showinfo(TITLE, 'Bitte zuerst ein Konto in der Liste anklicken.')
        return acc

    def _acc_name(self, acc):
        return f'{core.PLATFORMS[acc["platform"]]["name"]} „{acc["label"]}“'

    # ---------- Konten verwalten ----------

    def add(self):
        made = {}

        def validate(values):
            try:
                made['acc'] = logic.add_account(self._db(), values['platform'], values['name'], values['country'])
            except Exception as error:
                return logic.friendly_error(error)
            return None
        result = ask_form(self, 'Konto hinzufügen', [
            {'key': 'name', 'label': 'Name', 'hint': 'Zum Beispiel „Zweitkonto“ oder der Benutzername.'},
            {'key': 'platform', 'label': 'Plattform', 'kind': 'radio', 'value': 'tiktok',
             'choices': [(p, core.PLATFORMS[p]['name']) for p in repo.ACCOUNT_PLATFORMS]},
            {'key': 'country', 'label': 'Land', 'kind': 'choice', 'editable': True, 'value': 'de',
             'choices': [(c, f'{c} – {n}') for c, n in logic.COUNTRIES],
             'hint': 'Zwei Buchstaben für die Ländereinstellung des Browsers.'},
        ], intro='Ein neues Konto bekommt eine eigene Anmeldung und ein eigenes Browserprofil.',
            ok_text='Hinzufügen', validator=validate)
        if result is None or 'acc' not in made:
            return
        acc = made['acc']
        self.app.log(f'Konto angelegt: {self._acc_name(acc)}', 'OK')
        self.refresh()
        if self.tree.exists(str(acc['id'])):
            self.tree.selection_set(str(acc['id']))
        self.app.set_status(f'{self._acc_name(acc)} angelegt. Als Nächstes: „Im Browser anmelden“.', 'ok')

    def rename(self):
        acc = self._need_account()
        if acc is None:
            return

        def validate(values):
            try:
                logic.rename_account(self._db(), acc['id'], values['name'])
            except Exception as error:
                return logic.friendly_error(error)
            return None
        if ask_form(self, 'Konto umbenennen', [{'key': 'name', 'label': 'Neuer Name', 'value': acc['label']}],
                    intro='Der Name steht nur in dieser Liste. Die Anmeldung bleibt erhalten.',
                    ok_text='Umbenennen', validator=validate) is None:
            return
        self.refresh()

    def set_tiktok_username(self):
        from .. import accountvault
        acc = self._need_account()
        if acc is None or acc['platform'] != 'tiktok' or app_busy(self.app):
            return
        ok, saved = self.attempt('TikTok-Benutzername lesen', accountvault.get, self._db(), acc['identity'])
        if not ok:
            return

        def validate(values):
            try:
                accountvault.normalize_tiktok_username(values['username'], required=True)
            except ValueError as error:
                return str(error)
            return None

        answer = ask_form(self, 'TikTok-Konto zuordnen', [
            {'key': 'username', 'label': 'TikTok-Benutzername', 'value': saved.get('tiktok_username') or '',
             'hint': 'Den @Namen aus deinem TikTok-Profil eintragen.'},
        ], intro='Die App prüft diesen Namen vor der Wiederverwendung einer angemeldeten Android-Sitzung.',
            ok_text='Speichern', validator=validate)
        if answer is None or app_busy(self.app):
            return
        ok, _ = self.attempt('TikTok-Benutzername speichern', accountvault.put,
                             self._db(), acc['identity'], tiktok_username=answer['username'])
        if ok:
            self.app.set_status('TikTok-Benutzername gespeichert. Die angemeldete App-Sitzung wird beim nächsten Lauf geprüft.', 'ok')

    def make_default(self):
        acc = self._need_account()
        if acc is None:
            return
        if acc['is_default']:
            self.app.set_status(f'{self._acc_name(acc)} ist schon das Standardkonto.', 'muted'); return
        ok, _ = self.attempt('Standardkonto festlegen', logic.set_default, self._db(), acc['id'])
        if ok:
            self.app.set_status(f'{self._acc_name(acc)} ist jetzt das Standardkonto.', 'ok')
            self.refresh()

    def remove(self):
        acc = self._need_account()
        if acc is None or app_busy(self.app):
            return
        same = [a for a in self.accounts.values() if a['platform'] == acc['platform']]
        if len(same) <= 1:
            messagebox.showinfo(TITLE, f'Bei {core.PLATFORMS[acc["platform"]]["name"]} muss mindestens ein Konto bleiben. '
                                       'Lege zuerst ein anderes Konto an oder benenne dieses um.')
            return
        ok, used = self.attempt('Konto prüfen', logic.account_target_count, self._db(), acc['id'])
        if not ok:
            return
        text = f'{self._acc_name(acc)} entfernen?\n\nDie gespeicherte Anmeldung und das Browserprofil dieses Kontos werden gelöscht.'
        if used:
            text += f'\n\n{used} automatische(s) Ziel(e) nutzen dieses Konto und haben danach kein festes Konto mehr.'
        text += '\n\nGespeicherte Videos und Kommentare bleiben erhalten.'
        if not messagebox.askyesno(TITLE, text):
            return
        started_logout = self._has_login_data(acc)
        if not started_logout:
            self._delete_entry(acc); return
        self.app.run_steps([(f'{self._acc_name(acc)}: abmelden', core.logout_command(acc['platform'], acc['identity']))],
                           lambda r, c: self._after_logout_for_remove(acc, r, c))

    def _has_login_data(self, acc):
        profile = core.ROOT / 'data' / 'profiles' / acc['identity']
        return core.cookie_file(acc['platform'], acc['identity']).exists() or profile.exists()

    def _after_logout_for_remove(self, acc, results, cancelled):
        code = results[-1][1] if results else 1
        if cancelled or code != 0:
            self.app.set_status('Konto nicht entfernt: Abmelden hat nicht geklappt.', 'warn')
            messagebox.showwarning(TITLE, 'Das Konto wurde nicht entfernt, weil sich die Anmeldung nicht löschen ließ. '
                                          'Meist ist noch ein Browserfenster dieses Kontos offen – bitte schließen und noch einmal versuchen.')
            return
        self._delete_entry(acc)

    def _delete_entry(self, acc):
        ok, _ = self.attempt('Konto entfernen', logic.remove_account, self._db(), acc['id'])
        if ok:
            self.verified.pop(acc['identity'], None)
            self._verified_files.pop(acc['identity'], None)
            self._needs_login.discard(acc['identity'])
            self._failed_files.pop(acc['identity'], None)
            self._last_result.pop(acc['identity'], None)
            self._last_files.pop(acc['identity'], None)
            self.app.log(f'Konto entfernt: {self._acc_name(acc)}', 'OK')
            self.app.set_status(f'{self._acc_name(acc)} entfernt.', 'ok')
            self.refresh()

    # ---------- Anmeldung ----------

    def _show_fixes(self, fixes):
        """Lösungsknöpfe unter dem gewählten Konto; ohne Eintrag verschwindet die Zeile."""
        for child in self.fix_row.winfo_children():
            child.destroy()
        runner = getattr(self.app, 'run_action', None)
        for label, action in fixes:
            ttk.Button(self.fix_row, text=label, command=lambda a=action: runner(a) if runner else None).pack(side='left', padx=(0, 8))
        (self.fix_row.grid if fixes else self.fix_row.grid_remove)()

    def _busy_note(self):
        note = logic.describe_busy(core.ROOT)
        if note:
            self._show_fixes([('Dienst stoppen', {'id': 'stop_service'}), ('Zur Automatik', {'id': 'goto', 'tab': 'Automatik', 'sub': None})])
            messagebox.showinfo(TITLE, note)
        else:
            self._show_fixes([])
        return note

    def _mode_blocked(self, what):
        """Im Proxy-Modus frei/tor sind Anmeldung, Prüfung und Cookie-Import gesperrt – vor dem Start melden."""
        mode = self.mode_banner.update_mode()
        if not proxylogic.is_anonymous(mode):
            return False
        messagebox.showinfo(TITLE, f'{what} ist im Proxy-Modus „{proxylogic.MODE_TITLES[mode]}“ gesperrt: Konten werden mit '
                                   'kostenlosen Proxys oder Tor nicht benutzt – aus Sicherheitsgründen.\n\n'
                                   'Klicke oben auf „Auf „Aus“ stellen“ oder wähle den Modus „Aus“ oder „Eigene Proxy-Liste“ unter Einstellungen → Proxys.')
        return True

    def login(self, mode):
        acc = self._need_account()
        if acc is None or self._mode_blocked('Die Anmeldung') or self._busy_note():
            return
        name = self._acc_name(acc)
        if mode == 'password':
            state = core.credentials_state(acc['platform'], acc['identity'], core.ROOT)
            if state != 'ready':
                if messagebox.askyesno(TITLE, f'Für {name} sind noch keine Zugangsdaten gespeichert.\n\n'
                                              'Jetzt die Zugangsdaten-Datei öffnen und Benutzername und Passwort eintragen?'):
                    self.open_credentials()
                return
            if not messagebox.askyesno(TITLE, 'Das Programm trägt die gespeicherten Zugangsdaten im Browserfenster ein. '
                                              'Bestätigungscodes und CAPTCHA löst du selbst.\n\nFortfahren?'):
                return
        self.app.set_status('Browserfenster öffnet sich – dort anmelden (bis zu 10 Minuten).', 'warn')
        try:
            cmd = core.login_command(acc['platform'], mode, acc['identity'], acc['country'])
        except ValueError as error:
            self.report(error, 'Anmelden'); return
        if not self.app.run_steps([(f'{name}: Anmeldung im Browserfenster', cmd)], lambda r, c: self._done(acc, 'login', r, c)):
            self.app.set_status('Die Anmeldung wurde nicht gestartet – es läuft bereits ein Auftrag.', 'warn')

    def check(self):
        acc = self._need_account()
        if acc is None:
            return
        if self._mode_blocked('Die Prüfung der Anmeldung'):
            return
        if core.account_status(acc['platform'], core.ROOT, acc['identity'])[0] != 'saved':
            messagebox.showinfo(TITLE, f'{self._acc_name(acc)}: Es ist keine Anmeldung gespeichert. Bitte zuerst anmelden.'); return
        if self._busy_note():
            return
        self.app.run_steps([(f'{self._acc_name(acc)}: Anmeldung prüfen', core.check_command(acc['platform'], acc['identity'], acc['country']))],
                           lambda r, c: self._done(acc, 'check', r, c))

    def logout(self):
        acc = self._need_account()
        if acc is None:
            return
        if not self._has_login_data(acc):
            messagebox.showinfo(TITLE, f'{self._acc_name(acc)} ist nicht angemeldet – es gibt nichts zu löschen.'); return
        if not messagebox.askyesno(TITLE, f'{self._acc_name(acc)}: gespeicherte Anmeldung und Browserprofil löschen?'):
            return
        self.app.run_steps([(f'{self._acc_name(acc)}: abmelden', core.logout_command(acc['platform'], acc['identity']))],
                           lambda r, c: self._done(acc, 'logout', r, c))

    def import_file(self):
        acc = self._need_account()
        if acc is None or self._mode_blocked('Das Übernehmen einer Anmeldung'):
            return
        path = filedialog.askopenfilename(title='Cookie-Datei wählen (JSON oder Text)',
                                          filetypes=[('Cookie-Dateien', '*.json *.txt'), ('Alle Dateien', '*.*')])
        if not path:
            return
        if not messagebox.askyesno(TITLE, f'Die Anmeldung aus dieser Datei für {self._acc_name(acc)} übernehmen?\n\n{path}\n\n'
                                          'Eine schon gespeicherte Anmeldung dieses Kontos wird ersetzt.'):
            return
        try:
            cmd = core.import_cookies_command(acc['platform'], acc['identity'], path, acc['country'])
        except ValueError as error:
            self.report(error, 'Anmeldung übernehmen'); return
        self.app.run_steps([(f'{self._acc_name(acc)}: Anmeldung aus Datei übernehmen', cmd)],
                           lambda r, c: self._done(acc, 'import', r, c))

    def open_credentials(self):
        acc = self._need_account()
        if acc is None:
            return
        path = core.credentials_file(core.ROOT)
        try:
            created = core.ensure_credentials_entry(acc['platform'], acc['identity'], core.ROOT)
        except (ValueError, OSError) as error:
            self.app.log(f'Zugangsdaten-Datei: {logic.friendly_error(error)}', 'WARNING')
            messagebox.showwarning(TITLE, logic.friendly_error(error) + ('\n\nDie Datei wird jetzt zum Reparieren geöffnet.'
                                                                      if path.exists() else ''))
            if path.exists():
                self.app.open_path(path, edit=True)
            return
        if created:
            self.app.log(f'Leerer Eintrag für {self._acc_name(acc)} in credentials.json angelegt – bitte Benutzername und Passwort eintragen.')
        self.app.set_status('Zugangsdaten-Datei geöffnet: Benutzername und Passwort eintragen, speichern, dann „Mit gespeicherten Zugangsdaten“.', 'muted')
        self.app.open_path(path, edit=True)

    def _done(self, acc, action, results, cancelled):
        name = self._acc_name(acc)
        if cancelled:
            self._last_result[acc['identity']] = ('Abgebrochen. Es gibt keine neue Bestätigung.', 'warn')
            self._last_files[acc['identity']] = self._file_signature(acc)
            self.app.set_status('Abgebrochen.', 'warn')
            self.refresh()
            return
        code = results[-1][1] if results else 1
        ok_text = {'login': 'Anmeldung bestätigt und gespeichert.', 'check': 'Anmeldung ist gültig.',
                   'logout': 'Abgemeldet. Die gespeicherte Anmeldung wurde entfernt.',
                   'import': 'Anmeldung übernommen. Mit „Status prüfen“ kannst du sie bestätigen.'}
        bad_text = {'login': 'Anmeldung nicht bestätigt – Details im Protokoll.',
                    'check': 'Die Anmeldung konnte nicht geprüft werden. Prüfe deine Internetverbindung und versuche es erneut.',
                    'logout': 'Abmelden nicht abgeschlossen – Browserfenster schließen und erneut versuchen.',
                    'import': 'Die Anmeldung konnte aus dieser Datei nicht übernommen werden. Wähle eine neue Datei aus einem angemeldeten Browser.'}
        if code == 0:
            if action in ('login', 'check'):
                self.verified[acc['identity']] = time.time()
                self._verified_files[acc['identity']] = self._file_signature(acc)
                self._needs_login.discard(acc['identity'])
                self._failed_files.pop(acc['identity'], None)
            elif action in ('logout', 'import'):
                self.verified.pop(acc['identity'], None)
                self._verified_files.pop(acc['identity'], None)
                self._needs_login.discard(acc['identity'])
                self._failed_files.pop(acc['identity'], None)
            msg, kind = ok_text[action], 'ok'
        else:
            self.verified.pop(acc['identity'], None)
            self._verified_files.pop(acc['identity'], None)
            if action == 'check' and code in (2, 3):
                self._needs_login.add(acc['identity'])
                self._failed_files[acc['identity']] = self._file_signature(acc)
                bad_text['check'] = 'Die Anmeldung ist nicht mehr gültig. Melde dich unter „Konten“ erneut an.'
            msg, kind = (core.EXIT_TEXT[4] if code == 4 else bad_text[action]), 'err'
        self._last_result[acc['identity']] = (msg, kind)
        self._last_files[acc['identity']] = self._file_signature(acc)
        self.app.set_status(f'{name}: {msg}', kind)
        self.refresh()
        (messagebox.showinfo if kind == 'ok' else messagebox.showwarning)(TITLE, f'{name}: {msg}')

    def create_account(self):
        from .register_dialog import KontoDialog
        KontoDialog(self, self.app, on_fertig=self.refresh).grab_set()
