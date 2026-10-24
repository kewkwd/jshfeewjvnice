"""Detailfenster eines Kanals: Zustand in einfachen Worten und die Warteschlange (Status, Fehler je Video)."""
import tkinter as tk
from tkinter import ttk

from .. import autologic as al
from .. import chanlogic, channels, repo
from ..util import format_duration
from .common import COLORS, FONT_B
from .widgets import ColumnFitter, bind_wrap, show_centered

STATUS_TEXT = {'pending': 'wartet', 'running': 'läuft', 'done': 'abgerufen', 'failed': 'fehlgeschlagen', 'skipped': 'schon gespeichert'}
FILTERS = [('Alle', None), ('Wartend', 'pending'), ('Abgerufen', 'done'), ('Fehlgeschlagen', 'failed'),
           ('Schon gespeichert', 'skipped')]
FIT = {'status': (90, 0), 'title': (200, 4), 'date': (90, 0), 'length': (60, 0), 'tries': (60, 0), 'error': (160, 3)}


class ChannelDetail(tk.Toplevel):
    def __init__(self, parent, db_path, target, on_action=None):
        super().__init__(parent)
        self.withdraw()
        self.db_path, self.target_id, self.on_action = db_path, target['id'], on_action
        self.title('Kanal – Einzelheiten')
        self.transient(parent.winfo_toplevel())
        self.geometry('760x460')
        body = ttk.Frame(self, padding=10)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        self.head = ttk.Label(body, text='', font=FONT_B, justify='left')
        self.head.grid(row=0, column=0, sticky='w')
        self.line = ttk.Label(body, text='', justify='left', wraplength=720)
        self.line.grid(row=1, column=0, sticky='w', pady=(2, 0))
        bind_wrap(self.line, body, margin=20)
        bar = ttk.Frame(body)
        bar.grid(row=2, column=0, sticky='ew', pady=(8, 4))
        ttk.Label(bar, text='Zeige:').pack(side='left')
        self.filter_var = tk.StringVar(value=FILTERS[0][0])
        cb = ttk.Combobox(bar, textvariable=self.filter_var, state='readonly', width=18, values=[t for t, _s in FILTERS])
        cb.pack(side='left', padx=6)
        cb.bind('<<ComboboxSelected>>', lambda _e: self.reload())
        self.count = ttk.Label(bar, text='', foreground=COLORS['muted'])
        self.count.pack(side='left', padx=8)
        cols = ('status', 'title', 'date', 'length', 'tries', 'error')
        self.tree = ttk.Treeview(body, columns=cols, show='headings', selectmode='browse')
        for c, h, w, a in (('status', 'Status', 100, 'w'), ('title', 'Titel / Link', 240, 'w'), ('date', 'Veröffentlicht', 90, 'center'),
                           ('length', 'Länge', 60, 'e'), ('tries', 'Versuche', 60, 'center'), ('error', 'Fehler', 200, 'w')):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor=a, stretch=(c in ('title', 'error')), minwidth=40)
        self.tree.grid(row=3, column=0, sticky='nsew')
        sb = ttk.Scrollbar(body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=3, column=1, sticky='ns')
        hs = ttk.Scrollbar(body, orient='horizontal', command=self.tree.xview)
        hs.grid(row=4, column=0, sticky='ew')
        hs.grid_remove()
        self.fit = ColumnFitter(self.tree, FIT, hs, arrow=0)
        self.tree.tag_configure('failed', foreground=COLORS['err'])
        self.tree.tag_configure('done', foreground=COLORS['ok'])
        self.tree.tag_configure('pending', foreground=COLORS['muted'])
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky='e', pady=(8, 0))
        ttk.Button(buttons, text='Fehlgeschlagene erneut', command=lambda: self._do('requeue')).pack(side='left', padx=(0, 6))
        ttk.Button(buttons, text='Aktualisieren', command=self.reload).pack(side='left', padx=(0, 6))
        ttk.Button(buttons, text='Schließen', command=self.destroy).pack(side='left')
        self.bind('<Escape>', lambda _e: self.destroy())
        self.reload()
        show_centered(self, parent)

    def _do(self, name):
        try:
            message = chanlogic.action(self.db_path, self.target_id, name)
        except Exception as error:   # Kanal inzwischen gelöscht, Datenbank belegt …: Meldung statt stillem Stacktrace
            from tkinter import messagebox
            from .logic import friendly_error
            messagebox.showwarning('Kanal', friendly_error(error), parent=self)
            self.reload()
            return
        if self.on_action:
            self.on_action(message)
        self.reload()

    def reload(self):
        try:
            rows = {r['id']: r for r in chanlogic.load(self.db_path, strict=True)}
        except ValueError as error:          # Datenbank belegt oder nicht lesbar: vorhandene Zeilen stehen lassen
            self.head.configure(text=str(error))
            return
        target = rows.get(self.target_id)
        if target is None:
            self.head.configure(text='Dieser Kanal existiert nicht mehr.')
            self.line.configure(text='')
            self.tree.delete(*self.tree.get_children())
            return
        name = target.get('display_name') or channels.value_display(target['platform'], target['value'])
        self.head.configure(text=f'{channels.PLATFORM_NAMES.get(target["platform"], target["platform"])} · {name}')
        lines = [channels.progress_line(target)]
        if target.get('list_note'):
            lines.append(target['list_note'])
        lines.append(f'Tempo: {channels.PROFILES[target.get("pace_profile") or "normal"]["name"]} · Ruhezeit: '
                     f'{channels.quiet_text(target.get("quiet_hours"))}')
        self.line.configure(text='\n'.join(lines))
        status = dict(FILTERS).get(self.filter_var.get())
        items = chanlogic.items(self.db_path, self.target_id, status, 1000)
        self.tree.delete(*self.tree.get_children())
        for i in items:
            self.tree.insert('', 'end', iid=str(i['id']), tags=(i['status'],), values=(
                STATUS_TEXT.get(i['status'], i['status']), i.get('title') or i['video_url'], (i.get('published_at') or '')[:10],
                format_duration(i.get('duration_s')), i.get('attempts') or 0, (i.get('last_error') or '').replace('\n', ' ')))
        self.fit.refit()
        total = target['progress']['total']
        self.count.configure(text=f'{len(items)} angezeigt' + (f' von {total}' if status is None and total > len(items) else '')
                             + (' (die Liste zeigt höchstens 1000 Einträge)' if len(items) >= 1000 else ''))
