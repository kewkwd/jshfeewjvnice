import tkinter as tk
from tkinter import messagebox, ttk
from .. import accountvault, mailbox
from .common import Tab
from .hintergrund import cfg_of
from .widgets import ask_form


class PostfaecherTab(Tab):
    kuerzel = '<Control-F3>'

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
        self._password_dialog = None
        self._mailboxes = []
        ttk.Label(body, text='Deine E-Mail-Zugänge', style='Section.TLabel').pack(anchor='w')
        ttk.Label(body, text='Hinterlege ein Postfach, damit Profilwerk Bestätigungscodes abrufen kann.',
                  style='Muted.TLabel', wraplength=760).pack(anchor='w', pady=(5, 14))
        bar = FlowBar(body)
        bar.pack(fill='x', pady=(0, 16))
        add = bar.add(ttk.Button(bar, text='+ Vorhandenes Postfach', command=self.add, style='Primary.TButton'))
        self.registration_toggle = bar.add(ttk.Button(bar, text='Neues Postfach registrieren ▾', command=self._toggle_registration))
        bar.add(ttk.Button(bar, text='Passwortvorrat …', command=self.show_password_supply), right=True)
        self.registration_panel = ttk.LabelFrame(body, text='Neues Postfach vorbereiten', padding=14)
        self._registration_open = False
        options = FlowBar(self.registration_panel)
        options.pack(fill='x')
        self.provider = tk.StringVar(value='gmx.net')
        self.route = tk.StringVar(value='Browser')
        options.add(ttk.Label(options, text='Anbieter'))
        options.add(ttk.Combobox(options, textvariable=self.provider,
                                values=('gmx.net', 'web.de', 'gmail.com', 'outlook.com'), state='readonly', width=13))
        options.add(ttk.Label(options, text='Registrierungsweg'))
        options.add(ttk.Combobox(options, textvariable=self.route, values=('Browser', 'Android'), state='readonly', width=13))
        start = options.add(ttk.Button(options, text='Registrierung öffnen →', command=self.create, style='Primary.TButton'))
        fields = ttk.Frame(self.registration_panel)
        fields.pack(fill='x', pady=(10, 0))
        fields.columnconfigure(1, weight=1)
        self.registration_profile = tk.StringVar()
        self.target_address = tk.StringVar()
        ttk.Label(fields, text='Profilkennung').grid(row=0, column=0, sticky='w', padx=(0, 10))
        self.profile_choice = ttk.Combobox(fields, textvariable=self.registration_profile, width=35)
        self.profile_choice.grid(row=0, column=1, sticky='ew')
        ttk.Label(fields, text='Wunschadresse (optional)').grid(row=1, column=0, sticky='w', padx=(0, 10), pady=(6, 0))
        ttk.Entry(fields, textvariable=self.target_address).grid(row=1, column=1, sticky='ew', pady=(6, 0))
        ttk.Label(self.registration_panel, text='WEB.DE mit Android verwendet das ausgewählte Profil und dessen Proxy. '
                  'Gmail bietet einen begleiteten Android-Kontodialog; andere Anbieter verwenden den Browser.',
                  style='Muted.TLabel', wraplength=720).pack(anchor='w', pady=(8, 0))
        self.provider.trace_add('write', self._provider_changed)
        ttk.Button(self.registration_panel, text='Personendaten für diesen Anbieter …', command=self.registration_details).pack(anchor='w', pady=(10, 0))
        ttk.Label(self.registration_panel, text='Nach einem Abbruch bleiben Adresse und Passwort erhalten. '
                  'Ist das Konto bereits bestätigt, wird beim Fortsetzen nur der Mailzugang geprüft.',
                  style='Muted.TLabel', wraplength=720).pack(anchor='w', pady=(10, 0))
        self.search_bar = ttk.Frame(body)
        self.search_bar.pack(fill='x', pady=(0, 10))
        ttk.Label(self.search_bar, text='Postfächer suchen', style='Muted.TLabel').pack(side='left', padx=(0, 10))
        self.mail_query = tk.StringVar()
        ttk.Entry(self.search_bar, textvariable=self.mail_query).pack(side='left', fill='x', expand=True)
        self.mail_count = ttk.Label(self.search_bar, style='Muted.TLabel')
        self.mail_count.pack(side='right', padx=(12, 0))
        self.mail_query.trace_add('write', lambda *_: self._filter_mailboxes())
        frame = ttk.Frame(body)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=('email', 'provider', 'account'), show='headings', selectmode='browse', height=7)
        for key, label, width in (('email', 'E-Mail-Adresse', 320), ('provider', 'Anbieter', 150), ('account', 'Zugeordnetes Profil', 230)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=120, stretch=True)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.empty = ttk.Frame(self.tree, padding=24)
        self.empty_title = ttk.Label(self.empty, text='Noch kein Postfach in Profilwerk', style='Section.TLabel')
        self.empty_title.pack()
        self.empty_hint = ttk.Label(self.empty, text='Ein bestehendes WEB.DE-, GMX- oder anderes Postfach kannst du hier hinzufügen.',
                                    style='Muted.TLabel', wraplength=440, justify='center')
        self.empty_hint.pack(pady=(8, 14))
        ttk.Button(self.empty, text='Postfach hinzufügen …', command=self.add).pack()
        actions = FlowBar(body)
        actions.pack(fill='x', pady=(12, 8))
        self.release_button = actions.add(ttk.Button(actions, text='Zuordnung freigeben', command=self.release, state='disabled'))
        actions.add(ttk.Button(actions, text='Letzten Fehler ansehen', command=self.failure_report))
        actions.add(ttk.Button(actions, text='Weiter zu den Konten →', command=lambda: self.app.select_tab('Konten')), right=True)
        self.selection_hint = ttk.Label(body, text='Wähle ein Postfach, um seine Zuordnung zu sehen.', style='Muted.TLabel', wraplength=760)
        self.selection_hint.pack(anchor='w')
        self.advanced_toggle = ttk.Button(body, text='Weitere Möglichkeiten ▾', command=self._toggle_advanced, style='Quiet.TButton')
        self.advanced_toggle.pack(anchor='w', pady=(10, 0))
        self.advanced = FlowBar(body)
        self._advanced_open = False
        self.advanced.add(ttk.Button(self.advanced, text='Offenen Versuch zurücksetzen …', command=self.reset_attempt))
        self.advanced.add(ttk.Button(self.advanced, text='Eingabetempo je Profil …', command=lambda: self.app.select_tab('Profile')))
        self.advanced.add(ttk.Button(self.advanced, text='Arbeitsregeln', command=lambda: self.app.select_tab('Arbeitsregeln')))
        ttk.Label(body, text='Für Gmail oder Outlook kann ein separates App-Passwort nötig sein.',
                  style='Muted.TLabel').pack(anchor='w', pady=(12, 0))
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self._mailbox_selected())
        for widget in (add, start, self.release_button):
            self.app.register_busy(widget)

    def _toggle_registration(self):
        self._registration_open = not self._registration_open
        if self._registration_open:
            self._refresh_profile_choices()
            self.registration_panel.pack(fill='x', before=self.search_bar, pady=(0, 14))
        else:
            self.registration_panel.pack_forget()
        self.registration_toggle.configure(text='Registrierung ausblenden ▴' if self._registration_open else 'Neues Postfach registrieren ▾')

    def _provider_changed(self, *_):
        self.route.set('Android' if self.provider.get() == 'web.de' else 'Browser')

    def _refresh_profile_choices(self):
        from pathlib import Path
        from ..net.identity import validate_name
        choices = []
        for path in (Path(cfg_of(self.app).profiles_dir).parent / 'profile_settings').glob('*.json'):
            try:
                choices.append(validate_name(path.stem))
            except ValueError:
                continue
        self.profile_choice.configure(values=sorted(choices))

    def _toggle_advanced(self):
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self.advanced.pack(fill='x', after=self.advanced_toggle, pady=(8, 0))
        else:
            self.advanced.pack_forget()
        self.advanced_toggle.configure(text='Weniger Möglichkeiten ▴' if self._advanced_open else 'Weitere Möglichkeiten ▾')

    def _mailbox_selected(self):
        selected = self.tree.selection()
        values = self.tree.item(selected[0], 'values') if selected else ()
        assigned = bool(values and values[2] and values[2] != 'frei')
        from .workflow_components import busy
        self.release_button.configure(state='normal' if assigned and not busy(self.app) else 'disabled')
        self.selection_hint.configure(text=(values[0] + (' · Zugeordnet zu ' + values[2] if assigned else ' · Für eine Registrierung verfügbar.'))
                                      if values else 'Wähle ein Postfach, um seine Zuordnung zu sehen.')

    def _filter_mailboxes(self):
        selected = self.tree.selection()
        old = self.tree.item(selected[0], 'values')[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        query = self.mail_query.get().strip().casefold()
        count = 0
        for entry in self._mailboxes:
            values = (entry['adresse'], entry.get('anbieter', ''), entry.get('belegt_von') or 'frei')
            if query and query not in ' '.join(values).casefold():
                continue
            key = self.tree.insert('', 'end', values=values)
            count += 1
            if old == entry['adresse']:
                self.tree.selection_set(key)
        self.mail_count.configure(text=f'{count} Postfächer')
        if count:
            self.empty.place_forget()
        else:
            self.empty_title.configure(text='Keine passenden Postfächer' if query else 'Noch kein Postfach in Profilwerk')
            self.empty_hint.configure(text='Versuche einen anderen Suchbegriff.' if query else
                                      'Füge den Zugang zu deinem bestehenden Postfach hinzu, um Bestätigungscodes abzurufen.')
            self.empty.place(relx=.5, rely=.5, anchor='center')
        self._mailbox_selected()

    def refresh(self):
        try:
            self._mailboxes = accountvault.postfaecher(cfg_of(self.app).db_path)
            self._filter_mailboxes()
        except Exception as error:
            self.app.log(str(error), 'ERROR')

    def show_password_supply(self):
        from .password_supply import show
        if self._password_dialog is not None and self._password_dialog.winfo_exists():
            self._password_dialog.lift()
            return
        try:
            self._password_dialog = show(self, cfg_of(self.app))
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Passwortvorrat', str(error), parent=self)

    def add(self):
        from .workflow_components import busy
        if busy(self.app):return
        result=ask_form(self,'Postfach hinzufügen',[
            {'key':'email','label':'E-Mail','value':''},
            {'key':'password','label':'IMAP-Passwort','value':'','show':'*'},
            {'key':'host','label':'IMAP-Server (leer = Vorgabe)','value':''},
        ])
        if not result: return
        try:
            email=result['email'].strip()
            if '@' not in email or not result['password']: raise ValueError('Bitte E-Mail und IMAP-Passwort eingeben.')
            settings=mailbox.vorlage(email)
            if result['host'].strip(): settings['host']=result['host'].strip()
            if not settings.get('host'): raise ValueError('Bitte einen IMAP-Server eingeben.')
            accountvault.add_postfach(cfg_of(self.app).db_path,{'adresse':email,'passwort':result['password'],'anbieter':email.rpartition('@')[2],'imap':settings})
            self.refresh()
        except Exception as error:
            messagebox.showerror('Postfach hinzufügen',str(error))

    def create(self):
        try:
            from .workflow_components import busy
            if busy(self.app):return
            from ..net import proxymode
            proxymode.require_no_anon(cfg_of(self.app),'Postfach anlegen')
            route='android' if self.route.get()=='Android' else 'browser'
            if route=='android' and self.provider.get() not in ('gmail.com', 'web.de'):
                raise ValueError('Der Android-Weg ist für WEB.DE und Gmail verfügbar.')
            identity = self.registration_profile.get().strip()
            if route=='android' and self.provider.get()=='web.de' and not identity:
                raise ValueError('Für die WEB.DE-App bitte ein vorbereitetes Profil mit Personendaten und Proxy auswählen.')
            if self.provider.get() in ('web.de', 'gmx.net', 'gmail.com', 'outlook.com'):
                from .. import registration_data
                try:
                    registration_data.normalize(registration_data.load(cfg_of(self.app).db_path, self.provider.get()), required=True)
                except ValueError:
                    if not self.registration_details():return
            command=['mailkonto','neu',self.provider.get(),'--modus','assisted','--weg',route]
            if identity:command+=['--profil',identity]
            if self.target_address.get().strip():command+=['--adresse',self.target_address.get().strip()]
            if route=='browser':command+=['--fehlerfenster','300']
            self.app.run_steps([('Postfach erzeugen',command)],lambda *_:self.refresh())
        except Exception as error:
            messagebox.showerror('Postfach erzeugen',str(error))

    def failure_report(self):
        try:
            from ..failure_reports import latest, render
            text = render(latest(cfg_of(self.app).db_path, self.provider.get()))
            messagebox.showinfo('Letzter Registrierungsfehler', text, parent=self)
        except Exception as error:
            messagebox.showerror('Fehlerbericht', str(error), parent=self)

    def reset_attempt(self):
        from .workflow_components import busy
        if busy(self.app):return
        provider = self.provider.get()
        if not messagebox.askyesno('Neuen Versuch vorbereiten',
                'Offene Versuche für ' + provider + ' zurücksetzen? Beim nächsten Start werden eine neue Zieladresse '
                'und ein neues Profil vorbereitet. Alte Browserdateien bleiben erhalten. '
                'Falls das Konto bereits erstellt wurde, füge es stattdessen als vorhandenes Postfach hinzu.', parent=self):return
        try:
            from ..registration_attempts import reset
            reset(cfg_of(self.app).db_path, provider)
            self.app.log('Offene Registrierungsversuche zurückgesetzt; noch kein neuer Auftrag gestartet.')
        except Exception as error:
            messagebox.showerror('Versuch zurücksetzen', str(error), parent=self)

    def input_tempo(self):
        from .workflow_components import busy
        if busy(self.app):return
        from .. import form_pacing
        try:
            db_path=cfg_of(self.app).db_path
            current=form_pacing.load(db_path)
            result=ask_form(self,'Ausfülltempo',[{'key':'tempo','label':'Pause vor Textfeldern',
                'kind':'choice','value':str(current),
                'choices':[(str(value),label) for value,label in form_pacing.OPTIONS.items()]}],
                intro='Gilt für neue WEB.DE- und GMX-Browseraufträge. '
                      'Die Pause macht die Feldwechsel leichter verfolgbar und gilt für alle Profile gleich.')
            if result is None:return
            form_pacing.save(db_path,int(result['tempo']))
            self.app.log('Ausfülltempo für neue Postfachaufträge gespeichert.')
        except Exception as error:
            messagebox.showerror('Ausfülltempo',str(error),parent=self)

    def registration_details(self):
        from .workflow_components import busy
        if busy(self.app):return False
        from .. import registration_data as details
        try:
            provider = self.provider.get()
            current = details.load(cfg_of(self.app).db_path, provider)
            fields = []
            for key, label in zip(details.FIELDS, details.LABELS):
                field = {'key': key, 'label': label, 'value': current.get(key, '')}
                if key == 'geburtsdatum':
                    field['label'] = 'Geburtsdatum (TT.MM.JJJJ)'
                    if field['value']:
                        year, month, day = field['value'].split('-')
                        field['value'] = f'{day}.{month}.{year}'
                if key in ('anrede', 'land', 'geschlecht'):
                    from ..profile_settings import GENDERS
                    values = details.COUNTRIES.items() if key == 'land' else ((x, x) for x in (GENDERS if key == 'geschlecht' else details.SALUTATIONS))
                    field.update(kind='choice', choices=[('', 'Noch nicht hinterlegt'), *values])
                fields.append(field)
            def validate(values):
                try:details.normalize(values, required=True)
                except ValueError as error:return str(error)
            result = ask_form(self, 'Registrierungsdaten – ' + provider, fields,
                intro='Diese Angaben werden lokal gespeichert und beim nächsten Registrierungsauftrag automatisch eingetragen. '
                      'Name, Geschlecht und Geburtsdatum sind erforderlich. Die Angaben werden vor Browserstart fest mit dem Profil verbunden. „Testdaten ergänzen“ füllt nur leere Felder mit fiktiven '
                      'Beispielen. Die Musteranschrift ist nicht zustellfähig; Anbieter können sie ablehnen.',
                ok_text='Lokal speichern', validator=validate,
                suggestion=('Testdaten ergänzen', details.suggest_test_data))
            if result is None:return False
            details.save(cfg_of(self.app).db_path, provider, result)
            return True
        except Exception as error:
            messagebox.showerror('Registrierungsdaten', str(error), parent=self)
            return False

    def release(self):
        from .workflow_components import busy
        if busy(self.app):return
        selected=self.tree.selection()
        if not selected: return
        account=self.tree.item(selected[0],'values')[2]
        if account and account != 'frei':
            try:
                accountvault.freigeben(cfg_of(self.app).db_path,account)
                self.refresh()
            except Exception as error:messagebox.showerror('Postfach freigeben',str(error),parent=self)
