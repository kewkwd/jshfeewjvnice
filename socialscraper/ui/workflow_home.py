"""Compact working overview with contextual actions and separate detail views."""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import workflowlogic
from .common import Tab
from .forms import ScrollFrame
from .hintergrund import cfg_of
from .overview_data import snapshot
from .widgets import FlowBar, show_centered
from .workflow_components import busy

TASKS = {
    'collect': ('Daten abrufen', 'Link vorbereiten und Ergebnisse ansehen.'),
    'account': ('Vorhandenes Konto verbinden', 'Konto hinzufügen und anmelden.'),
    'act': ('Aktionen vorbereiten', 'Gespeicherte Ziele auswählen und einen Plan prüfen.'),
    'results': ('Ergebnisse ansehen', 'Videos, Personen und Kommentare durchsuchen.'),
    'register': ('Neues Konto vorbereiten', 'Profil, Postfach und Registrierung einrichten.'),
}
VIEWS = {'profiles': 'Profile & Geräte', 'accounts': 'Konten', 'mailboxes': 'Postfächer'}
HEADINGS = {
    'profiles': ('Profil', 'Kontozuordnung', 'Android', 'Gespeicherter Stand'),
    'accounts': ('Konto', 'Plattform', 'Profil', 'Anmeldung'),
    'mailboxes': ('E-Mail-Adresse', 'Anbieter', 'Profil', 'Zuordnung'),
}


class WorkflowHome(Tab):
    def build(self):
        self.configure(padding=12)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.task, self.focused = 'register', False
        self._snapshot = {'profiles': [], 'accounts': [], 'mailboxes': [], 'state': {}}
        self._items, self._guide_window = {}, None
        self._password_dialog = None
        saved_view = self.app.ui_state.get('home', 'overview_view', 'profiles')
        self.view = tk.StringVar(value=saved_view if saved_view in VIEWS else 'profiles')
        self.task = 'account' if self.view.get() == 'accounts' else 'register'
        self.query = tk.StringVar()

        top = FlowBar(self, gap=5)
        top.grid(row=0, column=0, sticky='ew', pady=(0, 9))
        new_profile = top.add(ttk.Button(top, text='+ Profil', style='Primary.TButton', command=self.new_profile))
        account_menu = ttk.Menubutton(top, text='+ Konto')
        menu = tk.Menu(account_menu, tearoff=False)
        menu.add_command(label='Vorhandenes Konto hinzufügen …', command=self.add_account)
        menu.add_command(label='Neues Konto registrieren …', command=self.register_account)
        account_menu.configure(menu=menu)
        top.add(account_menu)
        add_mail = top.add(ttk.Button(top, text='+ Postfach', command=self.add_mailbox))
        top.add(ttk.Button(top, text='Passwortvorrat …', command=self.show_password_supply))
        top.add(ttk.Button(top, text='Aktualisieren', command=self.refresh), right=True)
        for widget in (new_profile, account_menu, add_mail):
            self.app.register_busy(widget)

        strip = ttk.Frame(self, style='Soft.TFrame', padding=(7, 5))
        strip.grid(row=1, column=0, sticky='ew', pady=(0, 10))
        self.metrics = {}
        for index, (key, label) in enumerate((('profiles', 'Profile'), ('accounts', 'Konten'), ('mailboxes', 'Postfächer'), ('videos', 'Videos'))):
            strip.columnconfigure(index, weight=1, uniform='overview_counts')
            command = (lambda k=key: self.set_view(k)) if key in VIEWS else lambda: self.app.select_tab('Gespeicherte Daten')
            button = ttk.Button(strip, text='— ' + label, command=command, style='Quiet.TButton')
            button.grid(row=0, column=index, sticky='ew', padx=2)
            self.metrics[key] = button

        work = ttk.Frame(self)
        work.grid(row=2, column=0, sticky='nsew')
        work.columnconfigure(0, weight=1)
        work.columnconfigure(1, weight=0, minsize=250)
        work.rowconfigure(0, weight=1)
        listing = ttk.Frame(work)
        listing.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(2, weight=1)
        switch = FlowBar(listing, gap=4)
        switch.grid(row=0, column=0, sticky='ew', pady=(0, 7))
        for key, title in VIEWS.items():
            switch.add(ttk.Radiobutton(switch, text=title, value=key, variable=self.view, command=self._view_changed))
        search = ttk.Frame(listing)
        search.grid(row=1, column=0, sticky='ew', pady=(0, 7))
        search.columnconfigure(1, weight=1)
        ttk.Label(search, text='Suche', style='Muted.TLabel').grid(row=0, column=0, padx=(0, 7))
        entry = ttk.Entry(search, textvariable=self.query)
        entry.grid(row=0, column=1, sticky='ew')
        entry.bind('<Escape>', self._clear_search)
        self.count = ttk.Label(search, style='Muted.TLabel')
        self.count.grid(row=0, column=2, padx=(8, 0))
        self.query.trace_add('write', lambda *_: self._render_list())
        table_box = ttk.Frame(listing)
        table_box.grid(row=2, column=0, sticky='nsew')
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_box, columns=('name', 'type', 'relation', 'state'),
                                  show='headings', selectmode='browse', height=8, style='Compact.Treeview')
        for key, width in (('name', 165), ('type', 140), ('relation', 120), ('state', 195)):
            self.table.column(key, width=width, minwidth=85, stretch=True)
        self.table.grid(row=0, column=0, sticky='nsew')
        y = ttk.Scrollbar(table_box, command=self.table.yview)
        y.grid(row=0, column=1, sticky='ns')
        x = ttk.Scrollbar(table_box, orient='horizontal', command=self.table.xview)
        x.grid(row=1, column=0, sticky='ew')
        self.table.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.empty = ttk.Frame(self.table, padding=15)
        self.empty_title = ttk.Label(self.empty, text='', font=('Segoe UI', 11, 'bold'), wraplength=340, justify='center')
        self.empty_title.pack()
        self.empty_hint = ttk.Label(self.empty, text='', style='Muted.TLabel', wraplength=340, justify='center')
        self.empty_hint.pack(pady=(6, 0))
        self.table.bind('<<TreeviewSelect>>', lambda _e: self._selected())
        self.table.bind('<Return>', lambda _e: self.open_details())
        self.table.bind('<Double-1>', lambda e: self.open_details() if self.table.identify_region(e.x, e.y) == 'cell' else None)

        context = ttk.Frame(work, style='Card.TFrame', padding=12, width=260)
        context.grid(row=0, column=1, sticky='nsew')
        context.grid_propagate(False)
        context.columnconfigure(0, weight=1)
        context.rowconfigure(2, weight=1)
        ttk.Label(context, text='AUSWAHL', font=('Segoe UI', 8, 'bold'), style='Muted.TLabel').grid(row=0, column=0, sticky='w')
        self.selection_title = ttk.Label(context, text='Dein Arbeitsbereich', font=('Segoe UI', 12, 'bold'), wraplength=226)
        self.selection_title.grid(row=1, column=0, sticky='w', pady=(5, 10))
        description = ScrollFrame(context)
        description.grid(row=2, column=0, sticky='nsew')
        self.selection_body = ttk.Label(description.body, text='Wähle links einen Eintrag. Hier erscheinen die passenden Aktionen.',
                                        wraplength=218, justify='left', style='Muted.TLabel')
        self.selection_body.pack(anchor='w', fill='x')
        self.context_buttons = []
        for index in range(3):
            button = ttk.Button(context, text='', state='disabled')
            button.grid(row=3 + index, column=0, sticky='ew', pady=(6, 0))
            self.context_buttons.append(button)

        bottom = ttk.Frame(self)
        bottom.grid(row=3, column=0, sticky='ew', pady=(9, 0))
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text='Video-Link', style='Muted.TLabel').grid(row=0, column=0, padx=(0, 7))
        self.link = tk.StringVar()
        link_entry = ttk.Entry(bottom, textvariable=self.link)
        link_entry.grid(row=0, column=1, sticky='ew')
        link_entry.bind('<Return>', lambda _e: self.prepare_fetch())
        ttk.Button(bottom, text='Abruf vorbereiten →', command=self.prepare_fetch).grid(row=0, column=2, padx=(7, 0))
        ttk.Button(bottom, text='Ergebnisse', command=lambda: self.app.select_tab('Gespeicherte Daten')).grid(row=0, column=3, padx=(6, 0))
        self.summary = ttk.Label(self, text='Gespeicherte Angaben; keine Liveprüfung der Anmeldung oder Verbindung.',
                                 style='Muted.TLabel', wraplength=900)
        self.summary.grid(row=4, column=0, sticky='w', pady=(7, 0))

    def reset(self):
        self.focused = False
        self.refresh()

    def set_view(self, view):
        self.view.set(view)
        self._view_changed()

    def _view_changed(self):
        self.task = 'account' if self.view.get() == 'accounts' else 'register'
        self.app.ui_state.update_in('home', overview_view=self.view.get())
        self._render_list()

    def _clear_search(self, _event=None):
        self.query.set('')
        return 'break'

    def refresh(self):
        account_page = self.app.tabs.get('Konten')
        self._snapshot = snapshot(cfg_of(self.app), account_page._state if account_page is not None else None)
        for key, title in (('profiles', 'Profile'), ('accounts', 'Konten'), ('mailboxes', 'Postfächer'), ('videos', 'Videos')):
            number = self._snapshot['counts'][key]
            self.metrics[key].configure(text=('—' if number is None else f'{number:,}'.replace(',', '.')) + '  ' + title)
        warnings = self._snapshot['warnings']
        self.summary.configure(text=' · '.join(warnings) if warnings else
                               'Gespeicherte Angaben; Anmeldung und Verbindung werden bei Bedarf separat geprüft.')
        self._render_list()

    def _render_list(self):
        selected = self.table.selection()
        keep = selected[0] if selected else None
        self.table.delete(*self.table.get_children())
        self._items.clear()
        view, query = self.view.get(), self.query.get().strip().casefold()
        for key, title in zip(('name', 'type', 'relation', 'state'), HEADINGS[view]):
            self.table.heading(key, text=title)
        for item in self._snapshot[view]:
            if query and query not in (' '.join(item['values']) + ' ' + str(item['identity'] or '')).casefold():
                continue
            self._items[item['key']] = item
            self.table.insert('', 'end', iid=item['key'], values=item['values'])
        self.count.configure(text=str(len(self._items)))
        if self._items:
            self.empty.place_forget()
            if keep in self._items:
                self.table.selection_set(keep)
        else:
            title = 'Keine Treffer' if query else {'profiles': 'Noch keine Profile hinterlegt', 'accounts': 'Noch keine Konten hinterlegt',
                                                   'mailboxes': 'Noch keine Postfächer hinterlegt'}[view]
            hint = 'Versuche einen anderen Suchbegriff.' if query else 'Nutze die Schaltflächen oben, um einen Eintrag hinzuzufügen.'
            self.empty_title.configure(text=title)
            self.empty_hint.configure(text=hint)
            self.empty.place(relx=.5, rely=.5, anchor='center')
        self._selected()

    def _current(self):
        selected = self.table.selection()
        return self._items.get(selected[0]) if selected else None

    def _selected(self):
        item = self._current()
        self.selection_title.configure(text=item['title'] if item else 'Dein Arbeitsbereich')
        self.selection_body.configure(text=item['body'] if item else 'Wähle links einen Eintrag. Häufige Aufgaben erledigst du direkt hier; ausführliche Einstellungen öffnen sich bei Bedarf.')
        actions = []
        if item:
            actions.append(('Details öffnen →', self.open_details))
            if item['view'] == 'profiles' and item['editable']:
                actions.extend((('Person bearbeiten …', lambda: self.profile_action('edit_profile')),
                                ('Proxy zuordnen …', lambda: self.profile_action('assign_proxy'))))
            elif item['view'] == 'accounts':
                actions.extend((('Anmeldung öffnen …', lambda: self.account_action('login')),
                                ('Status prüfen', lambda: self.account_action('check'))))
            elif item['view'] == 'mailboxes' and item['identity']:
                actions.append(('Zugeordnetes Profil öffnen', self.open_mail_profile))
        for index, button in enumerate(self.context_buttons):
            if index < len(actions):
                title, command = actions[index]
                button.configure(text=title, command=command,
                                 state='disabled' if index > 0 and busy(self.app) else 'normal',
                                 style='Primary.TButton' if index == 0 else 'TButton')
                button.grid()
            else:
                button.grid_remove()

    def _run_local(self, callback):
        if busy(self.app):
            messagebox.showinfo('Auftrag läuft', 'Bitte warte, bis der laufende Auftrag beendet ist.', parent=self)
            return
        try:
            callback()
            self.refresh()
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Übersicht', str(error), parent=self)

    def new_profile(self):
        self._run_local(lambda: self.app.tabs['Profile'].edit_profile(new=True))

    def add_account(self):
        self._run_local(self.app.tabs['Konten'].add)

    def add_mailbox(self):
        self._run_local(self.app.tabs['Postfächer'].add)

    def register_account(self):
        if busy(self.app):
            return
        from .register_dialog import KontoDialog
        dialog = KontoDialog(self, self.app, on_fertig=self.refresh)
        item = self._current()
        if item and item['identity'] and item['identity'] != 'Nicht zugeordnet':
            dialog.name.set(item['identity'])
        dialog.grab_set()

    def _profile_page(self, identity):
        page = self.app.tabs['Profile']
        page.profile_query.set(identity)
        page.refresh()
        matches = [key for key, row in page.rows.items() if row.identity == identity]
        if not matches:
            raise ValueError('Dieses Profil ist nicht mehr vorhanden. Bitte die Übersicht aktualisieren.')
        page.table.selection_set(matches[0])
        page.table.see(matches[0])
        page.selected()
        return page

    def profile_action(self, method):
        item = self._current()
        if item and item['view'] == 'profiles' and item['editable']:
            self._run_local(lambda: getattr(self._profile_page(item['identity']), method)())

    def account_action(self, method):
        item = self._current()
        if not item or item['view'] != 'accounts':
            return
        def run():
            page = self.app.tabs['Konten']
            page.refresh()
            if not page.tree.exists(item['account_id']):
                raise ValueError('Dieses Konto ist nicht mehr vorhanden. Bitte die Übersicht aktualisieren.')
            page.tree.selection_set(item['account_id'])
            page._update_buttons()
            page.login('qr') if method == 'login' else page.check()
        self._run_local(run)

    def open_mail_profile(self):
        item = self._current()
        if item and item['identity']:
            try:
                self._profile_page(item['identity'])
                self.app.select_tab('Profile')
            except (OSError, ValueError, RuntimeError) as error:
                messagebox.showerror('Profil öffnen', str(error), parent=self)

    def open_details(self):
        item = self._current()
        if not item:
            return
        try:
            if item['view'] == 'profiles':
                if item['editable']:
                    self._profile_page(item['identity'])
                else:
                    page = self.app.tabs['Profile']
                    page.profile_query.set(item['row'].android)
                    page.refresh()
                    for key, row in page.rows.items():
                        if row.path == item['row'].path:
                            page.table.selection_set(key)
                            page.selected()
                            break
                self.app.select_tab('Profile')
            elif item['view'] == 'accounts':
                page = self.app.tabs['Konten']
                page.refresh()
                if page.tree.exists(item['account_id']):
                    page.tree.selection_set(item['account_id'])
                    page.tree.see(item['account_id'])
                    page._update_buttons()
                self.app.select_tab('Konten')
            else:
                page = self.app.tabs['Postfächer']
                page.mail_query.set(item['address'])
                page.refresh()
                for key in page.tree.get_children():
                    if page.tree.item(key, 'values')[0] == item['address']:
                        page.tree.selection_set(key)
                self.app.select_tab('Postfächer')
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Details öffnen', str(error), parent=self)

    def show_password_supply(self):
        from .password_supply import show
        if self._password_dialog is not None and self._password_dialog.winfo_exists():
            self._password_dialog.lift()
            return
        item = self._current()
        identity = item['identity'] if item and item['identity'] != 'Nicht zugeordnet' else None
        try:
            self._password_dialog = show(self, cfg_of(self.app), identity)
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Passwortvorrat', str(error), parent=self)

    def prepare_fetch(self):
        page = self.app.tabs['Abrufen']
        value = self.link.get().strip()
        if value:
            existing = page.links.get('1.0', 'end-1c').strip()
            lines = existing.splitlines() if existing else []
            if value not in lines:
                page.links.insert('end', ('\n' if existing else '') + value)
        self.app.select_tab('Abrufen')

    def choose(self, task):
        self.task = task
        if self._guide_window is not None and self._guide_window.winfo_exists():
            self._guide_window.destroy()
        window = tk.Toplevel(self)
        self._guide_window = window
        window.title(TASKS[task][0] + ' – Anleitung')
        window.transient(self.winfo_toplevel())
        window.geometry('650x540')
        window.minsize(520, 350)
        scroll = ScrollFrame(window)
        scroll.pack(fill='both', expand=True)
        body = ttk.Frame(scroll.body, padding=18)
        body.pack(fill='x')
        ttk.Label(body, text=TASKS[task][0], style='Section.TLabel').pack(anchor='w', pady=(0, 12))
        items = workflowlogic.steps(task, self._snapshot.get('state', {}))
        current = workflowlogic.next_step(items)
        for index, item in enumerate(items):
            row = ttk.Frame(body, style='Card.TFrame', padding=12)
            row.pack(fill='x', pady=(0, 8))
            ttk.Label(row, text=f'{index + 1}. ' + item['title'], font=('Segoe UI', 10, 'bold')).pack(anchor='w')
            detail = ttk.Label(row, text=item['body'], style='Muted.TLabel', wraplength=550, justify='left')
            detail.pack(fill='x', pady=(5, 8))
            row.bind('<Configure>', lambda e, label=detail: label.configure(wraplength=max(240, e.width - 26)))
            def open_step(target=item['target']):
                self.app.select_tab(target)
                window.destroy()
            ttk.Button(row, text='Weiter →' if index == current else 'Öffnen', command=open_step).pack(anchor='w')
        ttk.Button(body, text='Schließen', command=window.destroy).pack(anchor='e', pady=(8, 0))
        window.bind('<Escape>', lambda _e: window.destroy())
        show_centered(window, self)
