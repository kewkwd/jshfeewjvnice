"""Tab "Abrufen": Video-Links und TikTok-Profil abrufen, Konto wählen, in Gruppe einsortieren, Verlauf verfolgen."""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import clipboardlogic
from .. import guicore as core
from .. import proxylogic
from .. import repo
from . import logic
from . import fetch_feedback as feedback
from .common import COLORS, FONT_B, TITLE, ModeBanner, Tab, read_int
from .forms import ScrollFrame
from .widgets import PAD, HelpDot, TabHelpers, Tooltip, ask_form, bind_wrap, pad_label
from .workflow_components import busy as app_busy

NO_GROUP, NEW_GROUP = '(keine Gruppe)', 'Neue Gruppe …'
DEFAULT_ACCOUNT, ROTATE_ACCOUNT = 'Standardkonto', '🔄 Automatisch wechseln (Rotation)'
ROTATE = {'rotate': True}      # Eintrag „Rotation“ in der Kontenauswahl (kein echtes Konto)
ACCOUNT_PLATFORMS = ('tiktok', 'instagram', 'reddit', 'x')     # Plattformen mit Konto-Auswahl
COMMENTS_HINT = 'Nur für diesen Abruf. Den Standard stellst du unter Einstellungen ein.'


class FetchTab(TabHelpers, Tab):
    def build(self):
        # In kleinen Fenstern (860 x 620) passt nicht alles untereinander: die Seite scrollt, damit Ergebnis und Knöpfe
        # unten immer erreichbar sind (vorher blieb das Ergebnis eines Abrufs außerhalb des Fensters).
        self.configure(padding=0)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.scroll = ScrollFrame(self)
        self.scroll.grid(row=0, column=0, sticky='nsew')
        f = self.scroll.body
        f.configure(padding=PAD)
        f.columnconfigure(0, weight=1)
        self._accounts = {}        # Plattform -> {Anzeigetext: Konto}
        self._groups = {}          # Anzeigetext -> Gruppen-Nummer
        self._pending = None
        self._retry_steps, self._retry_plan, self._result_uids = [], None, []

        self.mode_banner = ModeBanner(f, lambda: core.ROOT, app=self.app)
        self.mode_banner.grid(row=0, column=0, sticky='ew', pady=(0, PAD))
        self.mode_banner.grid_remove()
        modes = ttk.Notebook(f); modes.grid(row=1, column=0, sticky='ew')
        self.modes = modes
        links = ttk.Frame(modes, padding=8); links.columnconfigure(0, weight=1)
        modes.add(links, text='  Video-Links  ')
        self._clip, self._clip_dismissed = None, set()
        self.clip_bar = tk.Frame(links, bg='#e7f1ff', highlightthickness=1, highlightbackground='#9ec5fe')
        self.clip_bar.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 6))
        self.clip_text = tk.Label(self.clip_bar, text='', bg='#e7f1ff', fg='#0a3069', font=FONT_B, anchor='w')
        self.clip_text.pack(side='left', padx=8, pady=4)
        self.clip_no = ttk.Button(self.clip_bar, text='Nein danke', command=self._clip_dismiss)
        self.clip_no.pack(side='right', padx=(0, 6), pady=3)
        self.clip_paste = ttk.Button(self.clip_bar, text='Nur einfügen', command=lambda: self._clip_accept(False))
        self.clip_paste.pack(side='right', padx=(0, 6), pady=3)
        self.clip_go = ttk.Button(self.clip_bar, text='Einfügen und abrufen', command=lambda: self._clip_accept(True))
        self.clip_go.pack(side='right', padx=(0, 6), pady=3)
        for button in (self.clip_go, self.clip_paste):
            self.app.register_busy(button)
        self.clip_bar.grid_remove()
        ttk.Label(links, text='Video- oder Beitragslinks · ein Link pro Zeile', font=FONT_B).grid(row=1, column=0, columnspan=2, sticky='w')
        self.links = tk.Text(links, height=4, wrap='none', undo=True, relief='flat', borderwidth=0)
        self.links.grid(row=2, column=0, sticky='ew', pady=(4, 6))
        self.links.bind('<Control-Return>', lambda _e: (self.start_fetch(), 'break')[1])
        self.links.bind('<Return>', self._return_pressed)
        self.links.bind('<Shift-Return>', lambda _e: (self.links.insert('insert', chr(10)), 'break')[1])
        self.links.bind('<<Paste>>', self._paste_event)
        row = ttk.Frame(links); row.grid(row=3, column=0, sticky='ew')
        ttk.Button(row, text='Aus Zwischenablage einfügen', command=self._paste_links).pack(side='left')
        ttk.Button(row, text='Leeren', command=lambda: self.links.delete('1.0', 'end')).pack(side='left', padx=6)
        ttk.Label(links, text='Enter startet den Abruf · Umschalt+Enter macht eine neue Zeile', foreground=COLORS['muted']
                  ).grid(row=4, column=0, sticky='w', pady=(4, 0))
        self.fetch_btn = ttk.Button(row, text='Abrufen starten', style='Big.TButton', command=self.start_fetch)
        self.fetch_btn.pack(side='right')
        self.app.register_busy(self.fetch_btn)

        prof = ttk.Frame(modes, padding=8)
        modes.add(prof, text='  TikTok-Profil  ')
        ttk.Label(prof, text='Die neuesten Videos eines TikTok-Profils abrufen', font=FONT_B).grid(row=0, column=0, columnspan=6, sticky='w', pady=(0, 6))
        ttk.Label(prof, text='@Benutzername').grid(row=1, column=0)
        self.profile_user = tk.StringVar()
        e = ttk.Entry(prof, textvariable=self.profile_user, width=26); e.grid(row=1, column=1, padx=8)
        e.bind('<Return>', lambda _e: self.start_profile())
        ttk.Label(prof, text='Videos:').grid(row=1, column=2)
        self.profile_limit = tk.IntVar(value=10)
        ttk.Spinbox(prof, from_=1, to=200, width=5, textvariable=self.profile_limit).grid(row=1, column=3, padx=8)
        self.profile_btn = ttk.Button(prof, text='Profil abrufen', style='Big.TButton', command=self.start_profile)
        self.profile_btn.grid(row=1, column=4, padx=(8, 0))
        self.app.register_busy(self.profile_btn)

        chan = ttk.Frame(modes, padding=8)
        chan.columnconfigure(1, weight=1)
        modes.add(chan, text='  Kanal  ')
        ttk.Label(chan, text='Einen ganzen Kanal abrufen (YouTube, TikTok, Instagram, Reddit, X)', font=FONT_B).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
        ttk.Label(chan, text='Kanal-Link').grid(row=1, column=0)
        self.channel_link = tk.StringVar()
        ce = ttk.Entry(chan, textvariable=self.channel_link, width=52)
        ce.grid(row=1, column=1, padx=8, sticky='ew')
        ce.bind('<Return>', lambda _e: self.add_channel())
        self.channel_btn = ttk.Button(chan, text='Kanal hinzufügen …', style='Big.TButton', command=self.add_channel)
        self.channel_btn.grid(row=1, column=2, padx=(8, 0))
        ttk.Label(chan, text='Konto, Kommentare pro Video und Zeitplan legst du im nächsten Schritt fest. '
                             'Den Fortschritt findest du unter „Kanäle beobachten“.',
                  foreground=COLORS['muted'], wraplength=720, justify='left').grid(
            row=2, column=0, columnspan=3, sticky='w', pady=(6, 0))

        opts = ttk.LabelFrame(f, text='Einstellungen für diesen Abruf', padding=8)
        opts.grid(row=2, column=0, sticky='ew', pady=(PAD, 0))
        opts.columnconfigure(1, weight=1); opts.columnconfigure(3, weight=1)
        ttk.Label(opts, text='Kommentare pro Video:').grid(row=0, column=0, sticky='w', pady=3)
        box = ttk.Frame(opts); box.grid(row=0, column=1, sticky='w')
        # Startwert = Standard aus den Einstellungen. Eine Änderung hier gilt nur für diesen Abruf und wird nie gespeichert.
        self._comments_touched, self._setting_default = False, False
        self.max_comments = tk.IntVar(value=self._default_comments())
        self.max_comments.trace_add('write', lambda *_: self._comments_edited())
        self.spin = ttk.Spinbox(box, from_=0, to=5000, increment=10, width=7, textvariable=self.max_comments)
        self.spin.grid(row=0, column=0, sticky='w')
        ttk.Label(box, text='  (0 = nur Videodaten)', foreground=COLORS['muted']).grid(row=0, column=1, sticky='w')
        self.comments_hint = ttk.Label(box, text=COMMENTS_HINT, foreground=COLORS['muted'], wraplength=230, justify='left')
        self.comments_hint.grid(row=1, column=0, columnspan=2, sticky='w')
        self.acc_vars = {plat: tk.StringVar() for plat in ACCOUNT_PLATFORMS}
        self.acc_boxes, self.acc_labels = {}, {}
        for r, plat in enumerate(ACCOUNT_PLATFORMS):
            self.acc_labels[plat] = ttk.Label(opts, text=f'{core.PLATFORMS[plat]["name"]}-Konto:')
            self.acc_labels[plat].grid(row=r, column=2, sticky='w', padx=(14, 6))
            cb = ttk.Combobox(opts, state='readonly', width=29, textvariable=self.acc_vars[plat])
            cb.grid(row=r, column=3, sticky='w', pady=3); self.acc_boxes[plat] = cb
            cb.bind('<<ComboboxSelected>>', self._remember, add='+')
            self.app.register_busy(cb)
        HelpDot(opts, 'rotation').grid(row=0, column=4, sticky='w', padx=(4, 0))
        ttk.Label(opts, text='In Gruppe einsortieren:').grid(row=1, column=0, sticky='w', pady=3)
        self.group_var = tk.StringVar(value=NO_GROUP)
        self.group_box = ttk.Combobox(opts, state='readonly', width=26, textvariable=self.group_var)
        self.group_box.grid(row=1, column=1, sticky='w')
        self.group_box.bind('<<ComboboxSelected>>', self._group_chosen)
        self.app.register_busy(self.group_box)
        self.track_var = tk.BooleanVar(value=False)
        track_row = ttk.Frame(opts)
        track_row.grid(row=2, column=0, columnspan=2, sticky='w', pady=3)
        ttk.Checkbutton(track_row, text=pad_label('Verlauf dieser Videos automatisch verfolgen'), variable=self.track_var,
                        command=self._toggle_track).pack(side='left')
        HelpDot(track_row, 'follow').pack(side='left')
        self.interval_var = tk.StringVar(value=next(t for t, m in logic.INTERVALS if m == logic.DEFAULT_INTERVAL))
        self.interval_box = ttk.Combobox(opts, state='disabled', width=22, textvariable=self.interval_var,
                                         values=[t for t, _m in logic.INTERVALS])
        self.interval_box.grid(row=3, column=0, columnspan=2, sticky='w')
        Tooltip(self.interval_box, 'Der Hintergrunddienst ruft diese Videos dann regelmäßig neu ab (neue Kommentare, Verlauf der Zahlen).')
        self.need_auth = tk.BooleanVar(value=False)
        self.need_auth_check = ttk.Checkbutton(opts, text=pad_label('TikTok und X nur mit bestätigter Anmeldung abrufen'),
                                               variable=self.need_auth)
        self.need_auth_check.grid(row=4, column=0, columnspan=4, sticky='w', pady=(3, 0))

        self.opts = opts
        modes.bind('<<NotebookTabChanged>>', lambda _e: self._mode_changed())
        self._mode_changed()

        self.result = ttk.Label(f, text='', wraplength=860, justify='left')
        self.result.grid(row=3, column=0, sticky='w', pady=(PAD, 0))
        bind_wrap(self.result, f, margin=2 * PAD)
        self.result_actions = ttk.Frame(f)
        self.result_actions.grid(row=4, column=0, sticky='w', pady=(8, 0))
        self.open_results_btn = ttk.Button(self.result_actions, text='Gespeicherte Ergebnisse öffnen', command=self.open_results)
        self.retry_btn = ttk.Button(self.result_actions, text='Erneut versuchen', command=self.retry_fetch)
        self.accounts_btn = ttk.Button(self.result_actions, text='Zu Konten', command=lambda: self.app.select_tab('Konten'))
        for button in (self.open_results_btn, self.retry_btn, self.accounts_btn):
            self.app.register_busy(button)
        self.result_actions.grid_remove()

    # ---------- Einstellungen je Reiter ----------

    def _mode_changed(self):
        """Zeigt nur die Einstellungen, die zum gewählten Reiter gehören.

        Video-Links: alles. TikTok-Profil: nur das TikTok-Konto (die anderen Plattformen gibt es dort nicht).
        Kanal: gar nichts – Kommentarzahl, Konto, Gruppe und Tempo wählt der Kanal-Dialog selbst."""
        try:
            index = self.modes.index(self.modes.select())
        except tk.TclError:
            index = 0
        if index == 2:
            self.opts.grid_remove()
            return
        self.opts.grid()
        self.need_auth_check.configure(text=pad_label('TikTok nur mit bestätigter Anmeldung abrufen' if index == 1
                                                      else 'TikTok und X nur mit bestätigter Anmeldung abrufen'))
        for plat, label in self.acc_labels.items():
            shown = index == 0 or plat == 'tiktok'
            for widget in (label, self.acc_boxes[plat]):
                widget.grid() if shown else widget.grid_remove()

    # ---------- Kommentarzahl: nur für diesen Abruf ----------

    def _default_comments(self):
        try: return min(core.read_settings(core.ROOT).max_comments, 5000)
        except Exception: return 20

    def _comments_edited(self):
        if not self._setting_default:
            self._comments_touched = True

    # ---------- Auswahlfelder ----------

    def refresh(self):
        self.mode_banner.update_mode()
        self._load_accounts()
        self._load_groups()
        if not getattr(self, '_restored', False):
            self._restored = True
            self._restore_choices()
        if not self._comments_touched:   # noch nicht angefasst: der Standard aus den Einstellungen gilt (auch nach einer Änderung dort)
            self._setting_default = True
            try: self.max_comments.set(self._default_comments())
            finally: self._setting_default = False

    def _load_accounts(self):
        try:
            accounts = logic.load_accounts(core.db_path())
        except Exception as error:
            self.app.log(f'Konten konnten nicht geladen werden: {logic.friendly_error(error)}', 'ERROR')
            return   # bisherige Liste und Auswahl behalten (sonst sprang die Wahl still auf „Rotation“)
        for plat, cb in self.acc_boxes.items():
            rows = [a for a in accounts if a['platform'] == plat]
            default = next((a for a in rows if a['is_default']), rows[0] if rows else None)
            names = {DEFAULT_ACCOUNT: default} if default else {}
            names.update({(a['label'] + ('  (Standard)' if a['is_default'] else '')): a for a in rows})
            if rows:
                names[ROTATE_ACCOUNT] = ROTATE
            chosen = self.acc_vars[plat].get()
            old = self._accounts.get(plat, {}).get(chosen)
            self._accounts[plat] = names
            cb.configure(values=list(names))
            if chosen in (DEFAULT_ACCOUNT, ROTATE_ACCOUNT) or not chosen:
                pick = chosen if chosen in names else DEFAULT_ACCOUNT
            else:
                pick = next((n for n, a in names.items() if old and a is not ROTATE and a['id'] == old['id']), DEFAULT_ACCOUNT)
            self.acc_vars[plat].set(pick if pick in names else next(iter(names), ''))

    def _load_groups(self):
        try:
            groups = logic.group_overview(core.db_path())['groups']
        except Exception as error:
            self.app.log(f'Gruppen konnten nicht geladen werden: {logic.friendly_error(error)}', 'ERROR')
            groups = []
        old = self._groups.get(self.group_var.get())
        self._groups = {g['name']: g['id'] for g in groups}
        self.group_box.configure(values=[NO_GROUP, *self._groups, NEW_GROUP])
        name = next((n for n, i in self._groups.items() if i == old), NO_GROUP)
        self.group_var.set(name)

    # ---------- Zuletzt benutzte Auswahl merken (Konto, Gruppe; nie die Kommentarzahl) ----------

    def _remember(self, _event=None):
        state = getattr(self.app, 'ui_state', None)
        if state is None:
            return
        group = self.group_var.get()
        state.update_in('fetch', accounts={p: self.acc_vars[p].get() for p in ACCOUNT_PLATFORMS},
                        group=group if group in self._groups else NO_GROUP)

    def _restore_choices(self):
        state = getattr(self.app, 'ui_state', None)
        if state is None:
            return
        accounts = state.get('fetch', 'accounts') or {}
        for plat in ACCOUNT_PLATFORMS:
            name = accounts.get(plat) if isinstance(accounts, dict) else None
            if name and name in self._accounts.get(plat, {}):
                self.acc_vars[plat].set(name)
        group = state.get('fetch', 'group')
        if group and group in self._groups:
            self.group_var.set(group)

    def _group_chosen(self, _event=None):
        if self.group_var.get() != NEW_GROUP:
            self._remember()
            return
        made = {}

        def validate(values):
            try:
                made['g'] = logic.create_group(core.db_path(), values['name'])
            except Exception as error:
                return logic.friendly_error(error)
            return None
        res = ask_form(self, 'Neue Gruppe', [{'key': 'name', 'label': 'Name'}], ok_text='Anlegen', validator=validate)
        self._load_groups()
        if res is not None and 'g' in made:
            self.group_var.set(made['g']['name'])
            self.app.log(f'Gruppe angelegt: {made["g"]["name"]}', 'OK')
        else:
            self.group_var.set(NO_GROUP)
        self._remember()

    def _toggle_track(self):
        self.interval_box.configure(state='readonly' if self.track_var.get() else 'disabled')

    def _account(self, plat):
        """Das gewählte Konto (bei „Standardkonto“ das Standardkonto); bei „Rotation“ None – siehe `_rotating`."""
        account = self._accounts.get(plat, {}).get(self.acc_vars[plat].get())
        return None if account is ROTATE else account

    def _rotating(self, plat):
        """Ist für diese Plattform „Automatisch wechseln (Rotation)“ gewählt?"""
        return self._accounts.get(plat, {}).get(self.acc_vars[plat].get()) is ROTATE

    @staticmethod
    def _rotation_every():
        try:
            return core.read_settings(core.ROOT).rotation_every
        except Exception:
            return 5

    def _rotation_problem(self, platforms):
        """Vor dem Start: Rotation gewählt, aber gerade kein Konto verfügbar (oder Proxy-Modus gesperrt)? Text oder ''."""
        rotating = [p for p in platforms if p in repo.LOGIN_PLATFORMS and self._rotating(p)]
        if not rotating:
            return ''
        mode = self.mode_banner.update_mode()
        if proxylogic.is_anonymous(mode):
            return (f'Proxy-Modus „{proxylogic.MODE_TITLES[mode]}“: Die Rotation braucht Konten, und die sind in diesem Modus '
                    'gesperrt.\n\nDen Modus änderst du unter Einstellungen → Proxys.')
        for plat in rotating:
            try:
                problem = logic.rotation_problem(core.db_path(), plat)
            except Exception as error:
                self.app.log(f'Rotation konnte nicht geprüft werden: {logic.friendly_error(error)}', 'WARNING')
                continue
            if problem:
                return problem
        return ''

    def _paste_links(self):
        """Zwischenablage ins Linkfeld: Web-Adressen je in eine eigene Zeile, schon vorhandene nicht doppelt; anderer Text unverändert."""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo(TITLE, 'Die Zwischenablage enthält keinen Text.')
            return False
        found = clipboardlogic.split_links(text)
        current = self.links.get('1.0', 'end-1c')
        if found:
            merged = clipboardlogic.merge_into(current, found)
            self.links.delete('1.0', 'end')
            self.links.insert('1.0', merged)
        else:
            self.links.insert('end', ('\n' if current.strip() else '') + text.strip())
        self.links.see('end')
        self._clip_hide()
        return True

    def _paste_event(self, _event=None):
        """Strg+V im Linkfeld: wie der Knopf (eine Auswahl im Feld wird dabei ersetzt)."""
        try:
            if self.links.tag_ranges('sel'):
                self.links.delete('sel.first', 'sel.last')
        except tk.TclError:
            pass
        self._paste_links()
        return 'break'

    def _return_pressed(self, _event=None):
        """Enter im Linkfeld startet den Abruf, wenn dort nur gültige Links stehen; sonst gibt es eine neue Zeile."""
        groups, bad, _merged = core.parse_links_detailed(self.links.get('1.0', 'end'))
        if groups and not bad:
            self.start_fetch()
            return 'break'
        return None

    # ---------- Hinweis „Link in der Zwischenablage“ (nur beim Fensterfokus, nur für erkannte Links, nichts wird gespeichert) ----------

    def check_clipboard(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ''
        info = clipboardlogic.inspect(text) if text else None
        if info is not None:
            fingerprint = clipboardlogic.fingerprint(text)
            if fingerprint in self._clip_dismissed or (info['kind'] == 'video' and clipboardlogic.all_present(self.links.get('1.0', 'end-1c'), info['links'])):
                info = None
            else:
                info['fp'] = fingerprint
        text = ''      # den Inhalt nicht weiter festhalten
        self._clip = info
        self._update_clip_bar()
        return info

    def _update_clip_bar(self):
        info = self._clip
        if info is None:
            self.clip_bar.grid_remove()
            return
        self.clip_text.configure(text=info['text'] + ' Abrufen?' if info['kind'] == 'video' else info['text'] + ' Als Kanal hinzufügen?')
        if info['kind'] == 'channel':
            self.clip_go.configure(text='Kanal hinzufügen …')
            self.clip_paste.pack_forget()
        else:
            self.clip_go.configure(text='Einfügen und abrufen')
            self.clip_paste.pack_forget()
            self.clip_paste.pack(side='right', padx=(0, 6), pady=3, before=self.clip_no)
            self.clip_go.pack_forget()
            self.clip_go.pack(side='right', padx=(0, 6), pady=3, before=self.clip_paste)
        self.clip_bar.grid()

    def _clip_hide(self):
        if self._clip is not None:
            self._clip_dismissed.add(self._clip.get('fp'))
        self._clip = None
        self.clip_bar.grid_remove()

    def _clip_dismiss(self):
        self._clip_hide()

    def _clip_accept(self, start):
        info = self._clip
        if info is None:
            return
        links = list(info['links'])
        kind = info['kind']
        self._clip_hide()
        if kind == 'channel':
            self.modes.select(2)
            self.channel_link.set(links[0])
            self.add_channel()
            return
        self.links.delete('1.0', 'end')
        self.links.insert('1.0', clipboardlogic.merge_into('', links))
        if start:
            self.start_fetch()

    def focus_item(self, spec):
        """Sprung von der Übersicht: Reiter „Video-Links“ und Cursor ins Linkfeld."""
        if (spec or {}).get('focus') == 'links':
            self.modes.select(0)
            self.links.focus_set()
            self.check_clipboard()
            return True
        return False

    # ---------- Start ----------

    def _post_plan(self, platforms, links_by_platform, merged=0):
        """Merkt sich, was nach dem Lauf mit den abgerufenen Videos geschehen soll."""
        group_id = self._groups.get(self.group_var.get())
        interval = next((m for t, m in logic.INTERVALS if t == self.interval_var.get()), logic.DEFAULT_INTERVAL)
        self._pending = {'platforms': list(platforms), 'links': links_by_platform, 'group_id': group_id,
                         'group_name': self.group_var.get() if group_id else None,
                         'track': bool(self.track_var.get()), 'interval': interval,
                         'accounts': {p: ('rotate' if self._rotating(p) else a['id']) for p in ACCOUNT_PLATFORMS
                                      if self._rotating(p) or (a := self._account(p))},
                         'started': repo.now(), 'merged': merged,
                         'wanted': read_int(self.max_comments, 0, 5000) or 0}

    def _start(self, steps):
        self._pending['steps'] = [(label, list(cmd)) for label, cmd in steps]
        self._retry_steps, self._retry_plan, self._result_uids = [], None, []
        self._show_fixes([])
        self.result_actions.grid_remove()
        self.result.configure(text='Abruf läuft. Bereits gespeicherte Daten bleiben bei einem Abbruch erhalten.', foreground=COLORS['muted'])
        self._show_result_area()
        if not self.app.run_steps(steps, self._fetch_done):
            # run_steps lehnt ab (z. B. Hintergrund-Auftrag auf einer anderen Seite): nichts läuft, also auch nichts anzeigen
            self._pending = None
            self.result.configure(text='Der Abruf wurde nicht gestartet: Es läuft bereits ein Auftrag.', foreground=COLORS['warn'])

    def _already_running(self):
        if app_busy(self.app):   # auch Hintergrund-Aufträge anderer Seiten (Aufwärmen, Aktionen), die run_steps ebenfalls ablehnt
            messagebox.showinfo(TITLE, 'Es läuft bereits ein Auftrag. Bitte warten oder abbrechen.')
            return True
        return False

    def start_fetch(self):
        if self._already_running():
            return
        n = read_int(self.max_comments, 0, 5000)
        if n is None:
            messagebox.showwarning(TITLE, 'Kommentare pro Video: bitte eine ganze Zahl von 0 bis 5000.'); return
        groups, bad, merged = core.parse_links_detailed(self.links.get('1.0', 'end'))
        if bad:
            messagebox.showwarning(TITLE, 'Diese Zeilen sind kein Link von TikTok, Instagram, YouTube, Reddit oder X:\n\n' + '\n'.join(bad[:8])); return
        if not groups:
            messagebox.showinfo(TITLE, 'Bitte mindestens einen Video-Link einfügen.'); return
        problem = self._mode_problem(groups, n) or self._rotation_problem(groups)
        if problem:
            return self._refuse(problem)
        if 'instagram' in groups and not self._rotating('instagram'):
            acc = self._account('instagram')
            if acc is not None and core.account_status('instagram', core.ROOT, acc['identity'])[0] != 'saved':
                if len(groups) == 1:
                    if messagebox.askyesno(TITLE, f'Instagram funktioniert nur mit Anmeldung, und das Konto „{acc["label"]}“ ist nicht angemeldet.\n\nJetzt zu „Konten“ wechseln?'):
                        self.app.select_tab('Konten')
                    return
                self.app.log('Instagram benötigt eine Anmeldung. Die übrigen Plattformen werden unabhängig davon abgerufen.', 'WARNING')
        if 'x' in groups and n and not self._rotating('x'):
            acc = self._account('x')
            if acc is None or core.account_status('x', core.ROOT, acc['identity'])[0] != 'saved':
                self.app.log('X: Ohne Anmeldung werden nur die Angaben zum Beitrag abgerufen – Antworten brauchen eine Anmeldung '
                             '(Tab „Konten“).', 'WARNING')
        if self._busy(groups.keys()):
            return
        steps = []
        try:
            for p, links in groups.items():
                rotate = p in repo.LOGIN_PLATFORMS and self._rotating(p)
                acc = self._account(p) if p in repo.LOGIN_PLATFORMS and not rotate else None
                steps.append((f'{core.PLATFORMS[p]["name"]}: {core.plural(len(links), "Link", "Links")} abrufen'
                              + (f' mit „{acc["label"]}“' if acc else ' mit Konto-Rotation' if rotate else ''),
                              core.scrape_command(p, links, n, require_auth=(p == 'instagram') or (p in ('tiktok', 'x') and self.need_auth.get()),
                                                  identity=acc['identity'] if acc else None, country=acc['country'] if acc else None,
                                                  rotate=rotate, rotation_every=self._rotation_every() if rotate else None)))
        except ValueError as error:
            self.report(error, 'Abrufen'); return
        self._post_plan(groups.keys(), groups, merged)
        if merged:
            self.app.log(f'{core.plural(merged, "doppelter Link wurde", "doppelte Links wurden")} zusammengefasst – '
                         'jedes Video wird nur einmal abgerufen.', 'MUTED')
        self._start(steps)

    def add_channel(self):
        """Öffnet den Kanal-Dialog mit dem eingefügten Link; danach geht es im Tab „Automatik“ weiter."""
        from . import channel_dialog
        dialog = channel_dialog.open_dialog(self, getattr(self.app, 'root', None) or core.ROOT, self.channel_link.get().strip(),
                                            modal=getattr(self, 'modal_dialogs', True))
        if dialog.result:
            self.channel_link.set('')
            self.app.log('Kanal hinzugefügt – der Fortschritt steht im Tab „Automatik“ unter „Kanäle“.', 'OK')
            self.app.set_status('Kanal hinzugefügt. Der Fortschritt steht im Tab „Automatik“.', 'ok')
            try:
                self.app.select_tab('Automatik')
                auto = getattr(self.app, 'tabs', {}).get('Automatik')
                if auto is not None:
                    auto.refresh()
            except Exception:
                pass
        return dialog

    def start_profile(self):
        if self._already_running():
            return
        n = read_int(self.max_comments, 0, 5000)
        lim = read_int(self.profile_limit, 1, 200)
        if n is None or lim is None:
            messagebox.showwarning(TITLE, 'Bitte gültige Zahlen eintragen (Videos 1–200, Kommentare 0–5000).'); return
        rotate = self._rotating('tiktok')
        acc = None if rotate else self._account('tiktok')
        try:
            cmd = core.profile_command(self.profile_user.get(), lim, n, require_auth=self.need_auth.get(),
                                       identity=acc['identity'] if acc else None, country=acc['country'] if acc else None,
                                       rotate=rotate, rotation_every=self._rotation_every() if rotate else None)
        except ValueError as error:
            messagebox.showwarning(TITLE, str(error)); return
        problem = self._mode_problem({'tiktok': []}, n, 'profile') or self._rotation_problem(['tiktok'])
        if problem:
            return self._refuse(problem)
        if self._busy(['tiktok']):
            return
        self._post_plan(['tiktok'], None)
        self._start([(f'TikTok-Profil @{self.profile_user.get().strip().lstrip("@")} abrufen', cmd)])

    def retry_fetch(self):
        if self._already_running() or not self._retry_steps or not self._retry_plan:
            return
        steps = [(label, list(cmd)) for label, cmd in self._retry_steps]
        plan = dict(self._retry_plan)
        platforms = set()
        links = {}
        for _label, cmd in steps:
            if cmd[0] == 'profile':
                platforms.add('tiktok')
            else:
                end = next((i for i, arg in enumerate(cmd[1:], 1) if arg.startswith('--')), len(cmd))
                for url in cmd[1:end]:
                    platform = core.detect_platform(url)
                    platforms.add(platform)
                    links.setdefault(platform, []).append(url)
            if '--require-auth' in cmd and '--rotate' not in cmd:
                platform = 'tiktok' if cmd[0] == 'profile' else core.detect_platform(cmd[1])
                ident = cmd[cmd.index('--identity') + 1]
                if core.account_status(platform, core.ROOT, ident)[0] != 'saved':
                    self._refuse('Eine Anmeldung fehlt. Bitte unter „Konten“ anmelden und danach „Erneut versuchen“ wählen.')
                    self.accounts_btn.pack(side='left', padx=(0, 8)); self.result_actions.grid()
                    return
        problem = self._mode_problem(platforms, plan['wanted'], 'profile' if steps[0][1][0] == 'profile' else 'video')
        if problem:
            return self._refuse(problem)
        if self._busy(platforms):
            return
        plan.update(started=repo.now(), platforms=list(platforms), links=links or None, merged=0)
        self._pending = plan
        self._start(steps)

    def open_results(self):
        self.app.select_tab('Gespeicherte Daten')
        data = self.app.tabs.get('Gespeicherte Daten')
        if data is not None:
            data.show_group(self._retry_plan.get('group_id') if self._retry_plan else None)
            data.focus_uids(self._result_uids)

    def _actions(self, plan, results, events, cancelled, uids):
        self._retry_plan = plan
        self._result_uids = uids
        self._retry_steps = feedback.retry_steps(plan.get('steps', []), events, results, cancelled) if plan else []
        for button in (self.open_results_btn, self.retry_btn, self.accounts_btn):
            button.pack_forget()
        if uids:
            self.open_results_btn.pack(side='left', padx=(0, 8))
        if self._retry_steps:
            self.retry_btn.pack(side='left', padx=(0, 8))
        if any('anmeld' in event.get('message', '').lower() for event in events) or any(code in (2, 3) for _, code, _ in results):
            self.accounts_btn.pack(side='left', padx=(0, 8))
        if self.result_actions.winfo_children() and (uids or self._retry_steps or self.accounts_btn.winfo_manager()):
            self.result_actions.grid()
        else:
            self.result_actions.grid_remove()

    def _mode_problem(self, platforms, comments, kind='video'):
        """Im Proxy-Modus frei/tor gesperrte Abrufe vor dem Start melden (Regel aus net/proxymode.py)."""
        mode = self.mode_banner.update_mode()
        for plat in platforms:
            problem = proxylogic.fetch_problem(mode, plat, comments, kind)
            if problem:
                return (f'Proxy-Modus „{proxylogic.MODE_TITLES[mode]}“: {problem}\n\nKlicke unten auf „Auf „Aus“ stellen“ '
                        'oder ändere den Modus unter Einstellungen → Proxys.')
        return ''

    def _show_result_area(self):
        """Die Seite scrollt (kleines Fenster): nach einem Ergebnis nach unten rollen, damit Text und Lösungsknöpfe zu sehen sind."""
        try:
            self.update_idletasks()
            self.scroll.canvas.yview_moveto(1.0)
        except tk.TclError:
            pass

    def _show_fixes(self, fixes):
        """Lösungsknöpfe unter dem Ergebnis (zum Beispiel „Auf ‚Aus‘ stellen“); die alten verschwinden."""
        for button in getattr(self, 'fix_buttons', []):
            button.destroy()
        self.fix_buttons = []
        runner = getattr(self.app, 'run_action', None)
        for label, action in fixes:
            button = ttk.Button(self.result_actions, text=label, command=lambda a=action: runner(a) if runner else None)
            button.pack(side='left', padx=(0, 8))
            self.fix_buttons.append(button)
        if fixes:
            self.result_actions.grid()

    def _refuse(self, text):
        head = text.split('\n')[0]
        self.result.configure(text='⚠ ' + head, foreground=COLORS['warn'])
        self.app.set_status(head, 'warn')
        if head.startswith('Proxy-Modus'):
            self._show_fixes([('Auf „Aus“ stellen', {'id': 'set_mode', 'value': 'aus', 'from': self.mode_banner.mode})])
        elif 'Konto' in head or 'Anmeld' in head:
            self._show_fixes([('Zu den Konten', {'id': 'goto_accounts'})])
        self._show_result_area()
        messagebox.showwarning(TITLE, text)

    def _busy(self, platforms):
        """Browserauftrag schon in Arbeit (Hintergrunddienst)? TikTok, Reddit und X brauchen den Browser (Sperre data/run.lock)."""
        if not {'tiktok', 'reddit', 'x'} & set(platforms):
            return False
        note = logic.describe_busy(core.ROOT)
        if note:
            self.result.configure(text='⚠ ' + note, foreground=COLORS['warn'])
            self._show_fixes([('Dienst stoppen', {'id': 'stop_service'}), ('Zur Automatik', {'id': 'goto', 'tab': 'Automatik', 'sub': None})])
            self._show_result_area()
            messagebox.showinfo(TITLE, note)
        return bool(note)

    # ---------- Ergebnis ----------

    def _fetch_done(self, results, cancelled):
        try:
            self._fetch_done_inner(results, cancelled)
        finally:
            self._show_result_area()

    def _fetch_done_inner(self, results, cancelled):
        app = self.app
        plan, self._pending = self._pending, None
        events = feedback.read_events(results)
        if events:
            saved = [event for event in events if event['video_saved']]
            failed = [event for event in events if not event['video_saved']]
            incomplete = [event for event in saved if not feedback.complete(event)]
            comments = sum(event['comments_saved'] for event in saved)
            if cancelled:
                text, kind = 'Abgebrochen.', 'warn'
                if saved:
                    text += f' {core.plural(len(saved), "Video", "Videos")} und {core.plural(comments, "Kommentar", "Kommentare")} bleiben gespeichert.'
            elif failed or incomplete or any(code != 0 for _, code, _ in results):
                text, kind = f'⚠ {core.plural(len(saved), "Video", "Videos")} gespeichert. Der Abruf ist unvollständig.', 'warn' if saved else 'err'
                if failed:
                    text += f' {core.plural(len(failed), "Video", "Videos")} nicht abgerufen.'
            else:
                text, kind = f'✔ {core.plural(len(saved), "Video", "Videos")} abgerufen und gespeichert.', 'ok'
            text += '\n' + '\n'.join(feedback.describe(event) for event in events[:6])
            if len(events) > 6:
                text += '\nWeitere Ergebnisse stehen im Protokoll.'
            uids = list(dict.fromkeys(event['uid'] for event in saved if event.get('uid')))
            if saved and plan:
                extra, uids, extra_kind = self._after_fetch(plan, uids)
                if extra:
                    text += '\n' + extra
                if extra_kind == 'warn':
                    kind = 'warn'
                if plan.get('merged'):
                    text += '\n' + core.plural(plan['merged'], 'doppelter Link wurde', 'doppelte Links wurden') + ' zusammengefasst.'
            text += self._used_text(results)
            self._actions(plan, results, events, cancelled, uids)
            self.result.configure(text=text, foreground=COLORS[kind]); app.set_status(text.splitlines()[0], kind)
            if kind == 'ok':
                self.open_results()
            return
        if cancelled:
            self._actions(plan, results, [], True, [])
            app.set_status('Abgebrochen.', 'warn'); self.result.configure(text='Abgebrochen.', foreground=COLORS['warn']); return
        ok = fail = 0; worst = 0; unknown = False; no_comments = 0; last_error = ''
        wanted = plan.get('wanted', 0) if plan else 0
        for _label, code, lines in results:
            summary = core.parse_summary(lines)
            if summary: ok += summary[0]; fail += summary[1]
            else: unknown = True
            worst = max(worst, code)
            no_comments += sum(1 for l in lines if l.endswith('| 0 Kommentare'))
            errors = [l for l in lines if core.log_level(l) == 'ERROR']
            if errors: last_error = errors[-1].split('| ', 1)[-1][:200]
        if worst == 0 and not unknown:
            text, kind = f'✔ {core.plural(ok, "Video", "Videos")} abgerufen und gespeichert.', 'ok'
            if wanted and no_comments:
                text += (f'\nHinweis: Bei {core.plural(no_comments, "Video", "Videos")} kamen keine Kommentare zurück. TikTok liefert sie oft nur '
                         'mit Anmeldung (Tab „Konten“); manche Videos haben auch keine öffentlichen Kommentare.')
                kind = 'warn'
        elif ok and not unknown:
            text, kind = f'⚠ {ok} abgerufen, {fail} fehlgeschlagen – Details im Protokoll.', 'warn'
        elif worst in core.EXIT_TEXT and worst != 1:
            text, kind = '✖ ' + core.EXIT_TEXT[worst], 'err'
        else:
            text, kind = '✖ Nichts abgerufen' + (f': {last_error}' if last_error else ' – Details im Protokoll.'), 'err'
        uids = []
        if ok and plan and plan.get('merged'):
            text += '\n' + core.plural(plan['merged'], 'doppelter Link wurde', 'doppelte Links wurden') + ' zusammengefasst.'
        if ok and plan:
            extra, uids, extra_kind = self._after_fetch(plan)
            if extra:
                text += '\n' + extra
                if extra_kind == 'warn' and kind == 'ok':
                    kind = 'warn'
        text += self._used_text(results)
        self.result.configure(text=text, foreground=COLORS[kind]); app.set_status(text.splitlines()[0], kind)
        self._actions(plan, results, [], False, uids)
        if ok and kind == 'ok':
            app.select_tab('Gespeicherte Daten')
            data = app.tabs.get('Gespeicherte Daten')
            if data is not None:
                data.show_group(plan['group_id'] if plan else None)
                data.focus_uids(uids)

    @staticmethod
    def _used_text(results):
        """Zeile „Benutzte Konten: …“ für das Ergebnis, wenn der Abruf Konten gewechselt oder gewählt hat."""
        names = []
        for _label, _code, lines in results:
            names += [n for n in core.parse_used_accounts(lines) if n not in names]
        return ('\nBenutzte Konten: ' + ', '.join(names)) if names else ''

    def _after_fetch(self, plan, confirmed_uids=None):
        """Neu abgerufene Videos einsortieren und ggf. verfolgen. Gibt (Text, UIDs, Art) zurück; Fehler bleiben nicht still."""
        lines, kind = [], 'ok'
        if not (plan['group_id'] or plan['track']):
            try:
                return '', confirmed_uids if confirmed_uids is not None else logic.find_fetched_uids(core.db_path(), plan['started'], plan['platforms'], plan['links']), 'ok'
            except Exception as error:
                self.app.log(f'Abgerufene Videos nicht gefunden: {logic.friendly_error(error)}', 'WARNING'); return '', [], 'ok'
        try:
            uids = confirmed_uids if confirmed_uids is not None else logic.find_fetched_uids(core.db_path(), plan['started'], plan['platforms'], plan['links'])
        except Exception as error:
            msg = logic.friendly_error(error)
            self.app.log(f'Abgerufene Videos nicht gefunden: {msg}', 'ERROR')
            return f'Hinweis: Die Videos konnten nicht einsortiert werden ({msg})', [], 'warn'
        if not uids:
            self.app.log('Nach dem Abruf wurden keine passenden Videos in der Datenbank gefunden – nichts einsortiert.', 'WARNING')
            return 'Hinweis: Die abgerufenen Videos wurden in der Datenbank nicht gefunden – nichts einsortiert.', [], 'warn'
        if plan['group_id']:
            try:
                n = logic.add_to_group(core.db_path(), plan['group_id'], uids)
                lines.append(f'In Gruppe „{plan["group_name"]}“ einsortiert: {core.plural(len(uids), "Video", "Videos")}' + (f' ({n} neu in der Gruppe).' if n < len(uids) else '.'))
            except Exception as error:
                msg = logic.friendly_error(error); kind = 'warn'
                self.app.log(f'Einsortieren fehlgeschlagen: {msg}', 'ERROR')
                lines.append(f'Hinweis: Einsortieren in die Gruppe hat nicht geklappt: {msg}')
        if plan['track']:
            try:
                out = logic.track_videos(core.db_path(), uids, plan['interval'], plan['accounts'])
                part = f'Verlauf wird verfolgt: {core.plural(out["added"], "Video", "Videos")}' + (f', {out["existing"]} schon vorher' if out['existing'] else '') + '.'
                if out['skipped']:
                    kind = 'warn'; part += f' {len(out["skipped"])} übersprungen (siehe Protokoll).'
                    for s in out['skipped'][:5]: self.app.log(f'Verfolgen übersprungen: {s}', 'WARNING')
                lines.append(part)
            except Exception as error:
                msg = logic.friendly_error(error); kind = 'warn'
                self.app.log(f'Verfolgen fehlgeschlagen: {msg}', 'ERROR')
                lines.append(f'Hinweis: Verfolgen hat nicht geklappt: {msg}')
        for l in lines:
            self.app.log(l, 'OK' if kind == 'ok' else 'WARNING')
        return '\n'.join(lines), uids, kind
