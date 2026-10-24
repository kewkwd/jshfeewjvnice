"""Display stored profile relationships without starting browsers or Android."""
import tkinter as tk
from tkinter import ttk, messagebox
from .common import Tab
from .hintergrund import cfg_of
from ..profile_inventory import inventory


class ProfilesTab(Tab):
    def build(self):
        from .widgets import FlowBar
        from .forms import ScrollFrame
        self.configure(padding=0)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        scroll = ScrollFrame(self)
        scroll.grid(row=0, column=0, sticky='nsew')
        body = ttk.Frame(scroll.body, padding=20)
        body.pack(fill='both', expand=True)
        self._password_dialog = self._generator_dialog = self._quality_dialog = None
        self._proxy_check_running = False
        self._all_rows, self._inventory_warnings, self.rows = [], [], {}
        ttk.Label(body, text='Deine Profile', style='Section.TLabel').pack(anchor='w')
        ttk.Label(body, text='Wähle ein Profil, um Person, Passwort und Verbindung zu bearbeiten.',
                  style='Muted.TLabel').pack(anchor='w', pady=(5, 14))
        tools = FlowBar(body)
        tools.pack(fill='x', pady=(0, 14))
        tools.add(ttk.Button(tools, text='+ Neues Profil', command=lambda: self.edit_profile(new=True), style='Primary.TButton'))
        tools.add(ttk.Button(tools, text='Passwortvorrat …', command=self.show_password_supply))
        tools.add(ttk.Button(tools, text='Aktualisieren', command=self.refresh), right=True)
        search = ttk.Frame(body)
        search.pack(fill='x', pady=(0, 10))
        ttk.Label(search, text='Profile suchen', style='Muted.TLabel').pack(side='left', padx=(0, 10))
        self.profile_query = tk.StringVar()
        ttk.Entry(search, textvariable=self.profile_query).pack(side='left', fill='x', expand=True)
        self.profile_count = ttk.Label(search, text='', style='Muted.TLabel')
        self.profile_count.pack(side='right', padx=(12, 0))
        self.profile_query.trace_add('write', lambda *_: self._filter_profiles())
        frame = ttk.Frame(body)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ('identity', 'browser', 'slot', 'accounts', 'android', 'status')
        self.table = ttk.Treeview(frame, columns=columns,
                                 displaycolumns=('identity', 'accounts', 'android', 'status'),
                                 show='headings', selectmode='browse', height=6)
        for key, label, width in (('identity', 'Profil', 200), ('browser', 'Browser', 100),
                                  ('slot', 'Bereich', 80), ('accounts', 'Konten', 130),
                                  ('android', 'Android-Gerät', 210), ('status', 'Gespeicherter Status', 230)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=100, stretch=True)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.empty = ttk.Frame(self.table, padding=20)
        self.empty_title = ttk.Label(self.empty, text='Noch keine Profile hinterlegt', style='Section.TLabel')
        self.empty_title.pack()
        self.empty_hint = ttk.Label(self.empty, text='Mit „Neues Profil“ legst du den ersten Eintrag an.', style='Muted.TLabel')
        self.empty_hint.pack(pady=(6, 0))
        controls = FlowBar(body)
        controls.pack(fill='x', pady=(12, 8))
        self.edit_button = controls.add(ttk.Button(controls, text='Person & Eingabetempo', command=self.edit_profile, state='disabled'))
        self.assign_proxy_button = controls.add(ttk.Button(controls, text='Proxy zuordnen …', command=self.assign_proxy, state='disabled'))
        self.proxy_button = controls.add(ttk.Button(controls, text='Verbindung prüfen', command=self.check_proxy, state='disabled'))
        self.more_button = controls.add(ttk.Button(controls, text='Weitere Werkzeuge ▾', command=self._toggle_tools), right=True)
        self.extra_tools = FlowBar(body)
        self._tools_open = False
        self.settings_proxy_button = self.extra_tools.add(ttk.Button(self.extra_tools, text='Eingestellten Proxy prüfen', command=lambda: self.check_proxy(settings=True)))
        self.extra_tools.add(ttk.Button(self.extra_tools, text='IPQS-Bewertung …', command=self.show_proxy_quality))
        self.extra_tools.add(ttk.Button(self.extra_tools, text='Lokale Testdaten …', command=self.show_local_generator))
        self.diagnostic_button = self.extra_tools.add(ttk.Button(self.extra_tools, text='Diagnose anzeigen', command=self.show_diagnostics, state='disabled'))
        self.details_box = ttk.LabelFrame(body, text='Ausgewähltes Profil', padding=12)
        self.details_box.pack(fill='x', pady=(4, 0))
        self.detail = ttk.Label(self.details_box, text='Wähle einen Eintrag in der Liste.', wraplength=850, justify='left')
        self.detail.pack(fill='x')
        self.proxy_detail = ttk.Label(self.details_box, wraplength=850, justify='left', text='', style='Muted.TLabel')
        self.proxy_detail.pack(fill='x', pady=(6, 0))
        self.status = ttk.Label(body, wraplength=850, justify='left', style='Muted.TLabel')
        self.status.pack(fill='x', pady=(10, 0))
        def resize(event):
            if event.widget is not self:
                return
            width = max(200, event.width - 68)
            for label in (self.detail, self.proxy_detail, self.status):
                if int(float(label.cget('wraplength'))) != width:
                    label.configure(wraplength=width)
        self.bind('<Configure>', resize, add='+')
        self.table.bind('<<TreeviewSelect>>', self.selected)
        self.table.bind('<Return>', lambda _e: self.edit_profile())
        self.table.bind('<Double-1>', lambda e: self.edit_profile() if self.table.identify_region(e.x, e.y) == 'cell' else None)

    def _toggle_tools(self):
        self._tools_open = not self._tools_open
        if self._tools_open:
            self.extra_tools.pack(fill='x', before=self.details_box, pady=(0, 8))
        else:
            self.extra_tools.pack_forget()
        self.more_button.configure(text='Weniger Werkzeuge ▴' if self._tools_open else 'Weitere Werkzeuge ▾')

    def _filter_profiles(self):
        selected = self.table.selection()
        old = self.rows.get(selected[0]) if selected else None
        self.table.delete(*self.table.get_children())
        self.rows.clear()
        query = self.profile_query.get().strip().casefold()
        for row in self._all_rows:
            if query and query not in ' '.join((row.identity, row.browser, row.accounts, row.android, row.status)).casefold():
                continue
            key = self.table.insert('', 'end', values=(row.identity, row.browser, row.slot, row.accounts, row.android, row.status))
            self.rows[key] = row
            if old is not None and (row.identity, row.path, row.slot) == (old.identity, old.path, old.slot):
                self.table.selection_set(key)
        self.profile_count.configure(text=f'{len(self.rows)} Einträge')
        if self.rows:
            self.empty.place_forget()
        else:
            self.empty_title.configure(text='Keine passenden Profile' if query else 'Noch keine Profile hinterlegt')
            self.empty_hint.configure(text='Versuche einen anderen Suchbegriff.' if query else 'Mit „Neues Profil“ legst du den ersten Eintrag an.')
            self.empty.place(relx=.5, rely=.5, anchor='center')
        self.status.configure(text=' '.join(self._inventory_warnings))
        self.selected()

    def selected(self, event=None):
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        text = ('Browser: ' + row.browser + ' · Bereich: ' + row.slot + '\nOrdner: ' + (row.path or 'Noch nicht angelegt') + '\nAndroid: ' + row.android + '\nKonten: ' + row.accounts) if row else 'Wähle einen Eintrag in der Liste.'
        valid = bool(row and row.identity != 'Nicht zugeordnet')
        if valid:
            from ..net import profile_binding
            try:text += '\nFester Proxy: ' + profile_binding.display(cfg_of(self.app), row.identity)
            except ValueError:text += '\nFester Proxy: Zuordnung nicht lesbar'
            from .. import profile_settings
            try:
                data = profile_settings.load(cfg_of(self.app).profiles_dir, row.identity)
                person, tempo = data['person'], data['tempo']
                text += '\nPerson: ' + (' '.join(filter(None, (person['vorname'], person['nachname']))) or 'Name fehlt')
                text += ' · ' + (person['geschlecht'] or 'Geschlecht fehlt') + ' · ' + (person['wohnort'] or 'Wohnort fehlt')
                text += f"\nMaus: {tempo['mouse_px_s']} px/s · Klickpause: {tempo['click_ms']} ms · Tippen: {tempo['typing_cps']} Zeichen/s"
                from ..net.sticky_sessions import summary
                session_text=summary(cfg_of(self.app),row.identity)
                if session_text:text+='\n'+session_text
            except ValueError as error:text += '\n' + str(error)
        self.detail.configure(text=text)
        self.edit_button.configure(state='normal' if valid else 'disabled')
        self.proxy_button.configure(state='normal' if valid and not self._proxy_check_running else 'disabled')
        self.assign_proxy_button.configure(state='normal' if valid and not self._proxy_check_running else 'disabled')
        from ..net import profile_proxy
        if not self._proxy_check_running:
            self.proxy_detail.configure(text=profile_proxy.summary(profile_proxy.load(cfg_of(self.app).profiles_dir, row.identity if valid else None)))
        self.diagnostic_button.configure(state='normal' if row else 'disabled')

    def show_local_generator(self):
        from .local_generator import show
        if self._generator_dialog is not None and self._generator_dialog.winfo_exists():
            self._generator_dialog.lift()
            return
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        identity = row.identity if row and row.identity != 'Nicht zugeordnet' else None
        self._generator_dialog = show(self, cfg_of(self.app), identity)

    def show_password_supply(self):
        from .password_supply import show
        if self._password_dialog is not None and self._password_dialog.winfo_exists():
            self._password_dialog.lift()
            return
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        identity = row.identity if row and row.identity != 'Nicht zugeordnet' else None
        try:
            self._password_dialog = show(self, cfg_of(self.app), identity)
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Passwortvorrat', str(error), parent=self)

    def assign_proxy(self):
        from ..net import profile_binding, accountproxy
        from .widgets import ask_form
        from .workflow_components import busy
        if busy(self.app) or self._proxy_check_running:return
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        if row is None or row.identity == 'Nicht zugeordnet':return
        cfg = cfg_of(self.app)
        try:
            options = profile_binding.options(cfg, row.identity)
            mapping = {str(index):raw for index, (raw, _label) in enumerate(options)}
            choices = [(str(index), label) for index, (_raw, label) in enumerate(options)]
            choices.append(('custom', 'Neue Proxy-Adresse eingeben'))
            def parse(values):
                if values['choice'] == 'custom':
                    raw = values['custom'].strip()
                else:
                    if values['custom'].strip():
                        raise ValueError('Für die eingegebene Adresse bitte „Neue Proxy-Adresse eingeben“ auswählen.')
                    raw = mapping[values['choice']]
                return accountproxy.normalize(raw)
            def validate(values):
                try:parse(values)
                except (ValueError, KeyError):return 'Bitte einen gespeicherten Proxy auswählen oder eine vollständige neue Proxy-Adresse eingeben.'
            values = ask_form(self, 'Fester Proxy für ' + row.identity, [
                {'key':'choice', 'label':'Proxy auswählen', 'kind':'choice', 'choices':choices,
                 'value':'0' if options else 'custom'},
                {'key':'custom', 'label':'Neue Proxy-Adresse', 'value':'', 'show':'*',
                 'hint':'Vollständige Adresse einschließlich Zugangsdaten; nur bei „Neue Proxy-Adresse“ ausfüllen.'}],
                intro='Die Auswahl wird fest für dieses Profil und alle zugehörigen Konten gespeichert. '
                      'Sie gilt ab dem nächsten Start. Eine offene Browser- oder Android-Sitzung bitte zuerst beenden. '
                      'Weitere Proxys kannst du unter Einstellungen in deiner Proxy-Liste hinterlegen.',
                ok_text='Diesem Profil zuordnen', validator=validate)
            if values is None:return
            if busy(self.app) or self._proxy_check_running:return
            shown = profile_binding.assign(cfg, row.identity, parse(values))
            self.selected()
            self.status.configure(text='Profilproxy gespeichert: ' + shown + ' · Gilt beim nächsten Start dieses Profils.')
        except (ValueError, OSError, RuntimeError):
            messagebox.showerror('Profilproxy', 'Zuordnung konnte nicht gespeichert werden. '
                                 'Bitte vollständige Zugangsdaten, Dateizugriff und laufende Aufträge prüfen.', parent=self)

    def show_proxy_quality(self):
        from .proxy_quality import show
        if self._quality_dialog is not None and self._quality_dialog.winfo_exists():
            self._quality_dialog.lift()
            return
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        identity = row.identity if row and row.identity != 'Nicht zugeordnet' else None
        self._quality_dialog = show(self, cfg_of(self.app), identity)

    def check_proxy(self, settings=False):
        from ..net import profile_proxy
        from .widgets import run_in_thread
        if self._proxy_check_running:return
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        if not settings and (row is None or row.identity == 'Nicht zugeordnet'):return
        cfg = cfg_of(self.app)
        identity = None if settings else row.identity
        self._proxy_check_running = True
        self.proxy_button.configure(state='disabled')
        self.assign_proxy_button.configure(state='disabled')
        self.settings_proxy_button.configure(state='disabled')
        self.proxy_detail.configure(text='Proxy-Standort wird '+('für '+identity if identity else 'aus den Einstellungen')+' geprüft …\nAbfrage über ipwho.is; keine Standortfreigabe des Geräts.')
        def done(result, error):
            self._proxy_check_running = False
            self.settings_proxy_button.configure(state='normal')
            selected = self.table.selection()
            current = self.rows.get(selected[0]) if selected else None
            self.proxy_button.configure(state='normal' if current and current.identity != 'Nicht zugeordnet' else 'disabled')
            self.assign_proxy_button.configure(state='normal' if current and current.identity != 'Nicht zugeordnet' else 'disabled')
            if settings or (current and current.identity == identity):
                self.proxy_detail.configure(text='Proxy-Test konnte nicht abgeschlossen werden.' if error else profile_proxy.summary(result))
        try:
            run_in_thread(self, lambda:profile_proxy.probe_profile(cfg, identity), done)
        except Exception:
            done(None, True)

    def edit_profile(self, new=False):
        from .. import profile_settings as settings
        from ..net.identity import validate_name
        from .widgets import ask_form
        from .workflow_components import busy
        if busy(self.app):return
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        if not new and (row is None or row.identity == 'Nicht zugeordnet'):return
        cfg = cfg_of(self.app)
        try:
            identity = '' if new else row.identity
            data = settings.defaults('neues_profil') if new else settings.load(cfg.profiles_dir, identity)
            fields = []
            if new:fields.append({'key':'identity', 'label':'Profilkennung (Buchstaben, Zahlen, _ oder -)', 'value':''})
            labels = ('Vorname', 'Nachname', 'Wohnort', 'Geschlecht', 'Geburtsdatum (optional, TT.MM.JJJJ)')
            for key, label in zip(settings.PERSON_FIELDS, labels):
                item = {'key':key, 'label':label, 'value':data['person'][key]}
                if key == 'geschlecht':item.update(kind='choice', choices=[('', 'Bitte auswählen'), *((g,g) for g in settings.GENDERS)])
                fields.append(item)
            for key, label in (('mouse_px_s','Mausgeschwindigkeit (Pixel pro Sekunde)'),
                               ('click_ms','Pause vor Klick (Millisekunden)'), ('typing_cps','Tippgeschwindigkeit (Zeichen pro Sekunde)')):
                fields.append({'key':key, 'label':label, 'value':'' if new else str(data['tempo'][key])})
            def parse(values):
                ident = validate_name(values['identity'].strip()) if new else identity
                person = settings.normalize_person({k:values[k] for k in settings.PERSON_FIELDS}, required=True)
                tempo = {k:float(values[k].replace(',', '.')) if values[k].strip() else settings.defaults(ident)['tempo'][k] for k in settings.LIMITS}
                settings.validate({**data,'identity':ident,'person':person,'tempo':tempo}, ident)
                if new and settings._path(cfg.profiles_dir,ident).exists():raise ValueError('Diese Profilkennung existiert bereits; bitte den Eintrag bearbeiten.')
                return ident,person,tempo
            def validate(values):
                try:parse(values)
                except (ValueError, OverflowError) as error:return str(error)
            values = ask_form(self, 'Profil vorbereiten' if new else 'Person und Eingabetempo', fields,
                intro='Name und Geschlecht stehen vor der Registrierung fest. Jeder Eintrag behält seine eigenen Geschwindigkeiten. '
                      'Leere Tempofelder erhalten feste Standardwerte für diese Kennung. Änderungen gelten ab dem nächsten Auftrag. '
                      'Ein späteres Datentool kann diese Angaben über den vorbereiteten Import liefern.',
                ok_text='Profil speichern', validator=validate)
            if values is None:return
            ident,person,tempo = parse(values)
            from .. import password_supply, runlock
            with runlock.acquire(runlock.root_for_db(cfg.db_path), 'gui'):
                if new:
                    password_supply.profile_password(cfg.db_path, ident)
                settings.update(cfg.profiles_dir,ident,person=person,tempo=tempo,source='manuell')
            self.refresh()
        except (ValueError, OSError, RuntimeError) as error:
            messagebox.showerror('Profileinstellungen', str(error), parent=self)

    def show_diagnostics(self):
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        if row is None:return
        dialog = tk.Toplevel(self)
        dialog.title('Profil-Diagnose – ' + row.identity)
        dialog.geometry('850x550')
        dialog.minsize(400, 250)
        dialog.report = tk.Text(dialog, wrap='word', padx=10, pady=10)
        scroll = ttk.Scrollbar(dialog, orient='vertical', command=dialog.report.yview)
        dialog.report.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        dialog.report.pack(fill='both', expand=True)
        dialog.report.insert('1.0', row.diagnostics or 'Für diese Zuordnung ist noch keine Diagnose gespeichert.')
        dialog.report.configure(state='disabled')
        return dialog

    def refresh(self):
        cfg = cfg_of(self.app)
        try:
            self._all_rows, self._inventory_warnings = inventory(cfg.profiles_dir, cfg.db_path)
        except (OSError, ValueError):
            self._all_rows, self._inventory_warnings = [], ['Profilübersicht konnte nicht geladen werden.']
        self._filter_profiles()
