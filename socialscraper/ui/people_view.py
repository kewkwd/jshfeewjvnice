"""Liste der Personen (Ersteller von Videos oder Kommentarschreiber) mit Kennzahlen – eine Ansicht im Tab „Gespeicherte Daten“."""
import tkinter as tk
from tkinter import ttk

from . import logic
from ..platforms import PLATFORM_NAMES
from .common import COLORS
from .widgets import ColumnFitter, SortHeaders

COLUMNS = {
    'creator': (('name', 'Name', 150, 'w'), ('platform', 'Plattform', 78, 'w'), ('seen', 'Zuerst gesehen', 118, 'center'),
                ('videos', 'Videos', 56, 'e'), ('views', 'Aufrufe', 78, 'e'), ('likes', 'Likes', 62, 'e'), ('groups', 'Gruppen', 110, 'w')),
    'commenter': (('name', 'Name', 150, 'w'), ('platform', 'Plattform', 78, 'w'), ('seen', 'Zuerst gesehen', 118, 'center'),
                  ('comments', 'Kommentare', 80, 'e'),
                  ('videos', 'Unter Videos', 84, 'e'), ('likes', 'Likes', 56, 'e'), ('first', 'Erste Aktivität', 96, 'center'),
                  ('last', 'Letzte Aktivität', 96, 'center'), ('positive', 'Positiv (KI)', 88, 'e'),
                  ('negative', 'Negativ (KI)', 88, 'e'), ('groups', 'Gruppen', 110, 'w')),
}
FIT = {
    'creator': {'name': (90, 2), 'platform': (72, 0), 'videos': (50, 0), 'views': (60, 0), 'likes': (50, 0), 'seen': (100, 0), 'groups': (60, 2)},
    'commenter': {'name': (90, 2), 'platform': (72, 0), 'comments': (60, 0), 'videos': (70, 0), 'likes': (50, 0), 'first': (80, 0),
                  'last': (80, 0), 'positive': (70, 0), 'negative': (70, 0), 'seen': (100, 0), 'groups': (60, 2)},
}
TEXT_SORTS = ('name', 'platform', 'groups')
EMPTY = {
    'creator': 'Noch keine Ersteller.\nRuf im Tab „Abrufen“ ein Video ab – sein Ersteller erscheint dann hier.',
    'commenter': 'Noch keine Kommentarschreiber.\nRuf im Tab „Abrufen“ ein Video mit Kommentaren ab (Kommentare pro Video größer als 0).',
}


def share_text(n, analyzed):
    """„3 (25 %)“ für die KI-Einschätzung; „–“, wenn noch nichts eingestuft ist."""
    if not analyzed:
        return '–'
    return f'{n} ({round(100 * (n or 0) / analyzed)} %)'


class PeopleList(ttk.Frame):
    """Eine Liste für eine Rolle. `on_select()` wird bei jeder Änderung der Auswahl gerufen, `on_sort()` nach einem Klick auf einen Spaltenkopf."""

    def __init__(self, master, role, on_select, on_sort, on_delete=None):
        super().__init__(master)
        self.role, self.rows = role, {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        cols = COLUMNS[role]
        self.sort = {'key': 'videos' if role == 'creator' else 'comments', 'desc': True}
        self.tree = ttk.Treeview(self, columns=[c[0] for c in cols], show='headings', selectmode='extended', height=6)
        for key, _t, width, anchor in cols:
            self.tree.column(key, width=width, anchor=anchor, stretch=key in ('name', 'groups'), minwidth=40)
        titles = {key: title for key, title, _w, _a in cols}
        self.headers = SortHeaders(self.tree, titles, on_sort, self.sort, numeric=[k for k in titles if k not in TEXT_SORTS])
        self.tree.grid(row=0, column=0, sticky='nsew')
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky='ns')
        hs = ttk.Scrollbar(self, orient='horizontal', command=self.tree.xview)
        hs.grid(row=1, column=0, sticky='ew')
        hs.grid_remove()
        self.fit = ColumnFitter(self.tree, FIT[role], hs)
        self.tree.bind('<<TreeviewSelect>>', lambda _e: on_select())
        self.tree.bind('<Control-a>', self._select_all)
        if on_delete:
            self.tree.bind('<Delete>', lambda _e: on_delete())
        self.empty = ttk.Label(self.tree, text='', justify='center', foreground=COLORS['muted'], background='white')

    def _select_all(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        return 'break'

    def fill(self, rows, empty_text=None, new_since=None):
        """new_since: Zeit (UTC); wer zum ersten Mal danach gesehen wurde, ist „neu“ (blau, mit Vermerk in „Zuerst gesehen“)."""
        cut = (new_since or '').replace(' ', 'T')[:19]
        self.tree.tag_configure('new', foreground='#0b5ed7')
        keep = {(r['platform'], r['author_key']) for r in self.selected()}
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        fmt = logic.fmt_number
        for i, r in enumerate(rows):
            iid = f'p{i}'
            self.rows[iid] = r
            platform = PLATFORM_NAMES.get(r['platform'], r['platform'])
            seen_raw = (r.get('first_seen_at') or '').replace(' ', 'T')[:19]
            is_new = bool(cut and seen_raw and seen_raw >= cut)
            seen = logic.local_stamp(r.get('first_seen_at')) + (' ★ neu' if is_new else '')
            if self.role == 'creator':
                values = (r['display_name'] or r['author_key'], platform, seen, fmt(r['videos']), fmt(r['views']), fmt(r['likes']), r['group_names'] or '')
            else:
                values = (r['display_name'] or r['author_key'], platform, seen, fmt(r['comments']), fmt(r['videos']), fmt(r['likes']),
                          logic.short_date(r['first_activity']), logic.short_date(r['last_activity']),
                          share_text(r['positive'], r['analyzed']), share_text(r['negative'], r['analyzed']), r['group_names'] or '')
            self.tree.insert('', 'end', iid=iid, values=values, tags=('new',) if is_new else ())
            if (r['platform'], r['author_key']) in keep:
                self.tree.selection_add(iid)
        self.fit.refit()
        if rows:
            self.empty.place_forget()
        else:
            self.empty.configure(text=empty_text or EMPTY[self.role])
            self.empty.place(relx=0.5, rely=0.5, anchor='center')

    def selected(self):
        """Gewählte Personen als [{'platform', 'author_key', 'display_name'}]."""
        return [{'platform': self.rows[i]['platform'], 'author_key': self.rows[i]['author_key'],
                 'display_name': self.rows[i]['display_name'] or self.rows[i]['author_key']}
                for i in self.tree.selection() if i in self.rows]
