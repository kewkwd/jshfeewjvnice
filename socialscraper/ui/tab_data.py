"""Tab "Gespeicherte Daten": Gruppen, Videos, Personen (Ersteller, Kommentarschreiber), Kommentare, Stimmung, Verlauf, Export.

Oben die Ansicht (Videos | Ersteller | Kommentarschreiber), links die Gruppen. Eine Gruppe kann Videos UND Personen
enthalten. Die Stimmung ist eine KI-Einschätzung (Schätzung, nicht gemessen)."""
import datetime
import pathlib
import re
import tkinter as tk
from .. import repo
import webbrowser
from tkinter import filedialog, messagebox, ttk

from .. import aiconfig
from .. import guicore as core
from . import logic
from .common import COLORS, ROOT, TITLE, Tab, track_after
from .people_view import EMPTY as PEOPLE_EMPTY
from .people_view import PeopleList
from .sentiment_ui import SentimentDialog, ask_for_key
from .data_details import DataDetailsSection, COMMENT_FIT, METRICS, MOOD_TAGS
from .widgets import (PAD, ColumnFitter, FlowBar, HelpDot, SentimentBar, SortHeaders, TabHelpers, Tooltip,
                      ask_form, bind_wrap, dot_image, pad_label, run_in_thread)

COLUMNS = (('platform', 'Plattform', 78, 'w'), ('author', 'Autor', 90, 'w'), ('caption', 'Beschreibung', 140, 'w'),
           ('views', 'Aufrufe', 78, 'e'), ('likes', 'Likes', 62, 'e'), ('comments', 'Komm.', 56, 'e'),
           ('length', 'Länge', 62, 'e'), ('date', 'Datum', 84, 'center'), ('groups', 'Gruppen', 90, 'w'),
           ('tracked', 'Verfolgt', 62, 'center'))
SORTABLE = {key: title for key, title, _w, _a in COLUMNS if key != 'tracked'}
# Spaltenbreiten: Gewicht 0 = so breit wie der Inhalt (Zahlen werden nie abgeschnitten), sonst flexibel
FIT = {'platform': (72, 0), 'author': (56, 2), 'caption': (80, 3), 'views': (60, 0), 'likes': (50, 0), 'comments': (50, 0),
       'length': (50, 0), 'date': (70, 0), 'groups': (56, 2), 'tracked': (50, 0)}
OVERVIEW_FIT = {'name': (110, 1), 'videos': (50, 0), 'creators': (50, 0), 'commenters': (60, 0), 'comments': (60, 0), 'views': (80, 0),
                'likes': (70, 0), 'avg_len': (60, 0), 'min_len': (60, 0), 'max_len': (60, 0),
                'pos': (60, 0), 'neu': (60, 0), 'neg': (60, 0), 'members': (80, 0)}
LENGTH_FIT = {'klass': (110, 1), 'videos': (60, 0), 'avg_len': (70, 0), 'views': (90, 0), 'likes': (90, 0), 'comments': (100, 0)}
MODES = (('videos', 'Videos'), ('creator', 'Ersteller'), ('commenter', 'Kommentarschreiber'))
ROLE_PLURAL = {'creator': 'Ersteller', 'commenter': 'Kommentarschreiber'}
CONNECTOR = ' · '


class DataTab(DataDetailsSection, TabHelpers, Tab):
    def build(self):
        f = self
        f.configure(padding=PAD)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)
        self.view = logic.ALL             # ALL | UNGROUPED | Gruppen-Nummer
        self.mode = tk.StringVar(value='videos')   # videos | creator | commenter
        self.new_window = tk.StringVar(value=logic.NEW_WINDOWS[1][0])    # ab wann ein Autor „neu“ heißt
        self.show_filter = tk.StringVar(value=logic.SHOW_FILTERS[0][0])  # Alle | Neue | Bekannte | Ohne Gruppe
        self.sort = {'key': 'fetched', 'desc': True}
        self.length_key = None            # None | short | medium | long (Filter „Länge“)
        self.video_rows = {}              # uid -> Wörterbuch aus query_videos
        self.comment_rows = {}            # iid -> Kommentar (Wörterbuch)
        self.groups = {}                  # Nummer -> Gruppe
        self.group_stats = {}             # Nummer -> Kennzahlen der Gruppe
        self.modal_dialogs = True         # Tests schalten das ab
        self._detail_job = None
        self._build_groups(f)
        self._build_right(f)
        self.bind('<Destroy>', self._on_destroy, add='+')

    def _on_destroy(self, event):
        if event.widget is self and self._detail_job is not None:
            try: self.after_cancel(self._detail_job)
            except tk.TclError: pass
            self._detail_job = None

    def root(self):
        return getattr(self.app, 'root', None) or ROOT

    # ---------- Aufbau ----------

    def _build_groups(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=0, sticky='ns', padx=(0, PAD))
        box.rowconfigure(2, weight=1)
        title = ttk.Frame(box)
        title.grid(row=0, column=0, sticky='w')
        ttk.Label(title, text='Gruppen', font=('Segoe UI', 10, 'bold')).pack(side='left')
        HelpDot(title, 'groups').pack(side='left', padx=(4, 0))
        ttk.Label(box, text='Videos und Personen organisieren', foreground=COLORS['muted'], font=('Segoe UI', 8)
                  ).grid(row=1, column=0, sticky='w', pady=(0, 4))
        self.gtree = ttk.Treeview(box, show='tree', selectmode='browse', height=8)
        self.gtree.column('#0', width=202, stretch=True)
        self.gtree.grid(row=2, column=0, sticky='ns')
        self.gtree.bind('<<TreeviewSelect>>', lambda _e: self._group_selected())
        self.gtree.bind('<Delete>', lambda _e: self.group_delete())
        self.gtree.bind('<F2>', lambda _e: self.group_edit())
        row = ttk.Frame(box); row.grid(row=3, column=0, sticky='w', pady=(6, 0))
        self.gbtn = {}
        for key, text, fn in (('new', 'Neu …', self.group_new), ('edit', 'Ändern', self.group_edit), ('del', 'Löschen', self.group_delete)):
            self.gbtn[key] = ttk.Button(row, text=text, width=7, command=fn); self.gbtn[key].pack(side='left', padx=(0, 4))
        Tooltip(self.gbtn['del'], 'Löscht nur die Gruppe. Videos und Personen bleiben gespeichert.')
        ttk.Button(box, text='Datenordner öffnen', command=lambda: self.app.open_path(ROOT / 'data')).grid(row=4, column=0, sticky='w', pady=(6, 0))
        self.data_info = ttk.Label(box, text='', foreground=COLORS['muted'], wraplength=232, justify='left')
        self.data_info.grid(row=5, column=0, sticky='w', pady=(6, 0))

    def _build_right(self, parent):
        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky='nsew')
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        top = FlowBar(right); top.grid(row=0, column=0, sticky='ew', pady=(0, 4))
        self.top = top
        for value, text in MODES:
            top.add(ttk.Radiobutton(top, text=pad_label(text), value=value, variable=self.mode, command=self._mode_changed))
        top.add(HelpDot(top, title='Ersteller und Kommentarschreiber',
                        text='Ersteller sind die Personen, die ein Video veröffentlicht haben. Kommentarschreiber sind Personen, '
                             'die darunter etwas geschrieben haben. Beide kannst du in Gruppen sortieren.'))
        self.filter_platform = tk.StringVar(value='Alle')
        cb = ttk.Combobox(top, state='readonly', width=10, textvariable=self.filter_platform,
                          values=['Alle'] + [core.PLATFORMS[p]['name'] for p in repo.PLATFORMS])
        self.filter_platform_cb = cb
        top.add(cb); cb.bind('<<ComboboxSelected>>', lambda _e: self.refresh())
        self.length_var = tk.StringVar(value=logic.LENGTH_FILTERS[0][0])
        self.length_box = ttk.Combobox(top, state='readonly', width=25, textvariable=self.length_var,
                                       values=[t for t, _k in logic.length_filter_labels()])
        top.add(self.length_box); self.length_box.bind('<<ComboboxSelected>>', lambda _e: self._length_changed())
        Tooltip(self.length_box, 'Zeigt nur Videos einer Längenklasse. Videos ohne bekannte Länge fallen dabei heraus. '
                                 'Die Grenzen stellst du unter Einstellungen → Abruf ein.')
        self.search = tk.StringVar()
        top.add(ttk.Label(top, text='Suche', foreground=COLORS['muted']))
        e = ttk.Entry(top, textvariable=self.search, width=22); top.add(e)
        e.bind('<Return>', lambda _e: self.refresh())
        top.add(ttk.Button(top, text='Suchen', command=self.refresh))
        self.mood_btn = ttk.Button(top, text='Stimmung bestimmen …', command=self.mood_start)
        top.add(self.mood_btn, right=True)
        top.add(HelpDot(top, 'ai_rating'), right=True)
        Tooltip(self.mood_btn, 'Die Claude-KI schätzt, ob die gespeicherten Kommentare positiv, neutral oder negativ sind (KI-Einschätzung).')
        self.view_label = ttk.Label(right, text='', foreground=COLORS['muted'], justify='left')
        self.view_label.grid(row=1, column=0, sticky='w'); self.view_label.grid_remove()
        bind_wrap(self.view_label, right, margin=12)

        panes = ttk.PanedWindow(right, orient='vertical')
        panes.grid(row=2, column=0, sticky='nsew')
        upper = ttk.Frame(panes); upper.columnconfigure(0, weight=1); upper.rowconfigure(0, weight=1)
        panes.add(upper, weight=3)
        self.upper = upper
        self._build_videos(upper)
        self._build_people(upper)

        lower = ttk.Frame(panes); lower.columnconfigure(0, weight=1); lower.rowconfigure(1, weight=1)
        panes.add(lower, weight=2)
        self._panes = panes
        panes.bind('<Map>', lambda _e: track_after(self, 50, self._place_sash))
        self._sash_user = False
        panes.bind('<ButtonRelease-1>', lambda _e: setattr(self, '_sash_user', True), add='+')
        panes.bind('<Configure>', lambda _e: track_after(self, 50, self._keep_sash), add='+')
        self.mood_bar = SentimentBar(lower, height=46)
        self.mood_bar.grid(row=0, column=0, sticky='ew', pady=(6, 0))
        self.nb = ttk.Notebook(lower); self.nb.grid(row=1, column=0, sticky='nsew', pady=(6, 0))
        self._build_comments(); self._build_history(); self._build_overview(); self._build_length()
        self._update_buttons()

    def _build_videos(self, upper):
        vpage = ttk.Frame(upper); vpage.grid(row=0, column=0, sticky='nsew')
        vpage.columnconfigure(0, weight=1); vpage.rowconfigure(0, weight=1)
        self.vpage = vpage
        self.tree = ttk.Treeview(vpage, columns=[c[0] for c in COLUMNS], show='headings', selectmode='extended', height=6)
        for key, _t, width, anchor in COLUMNS:
            self.tree.column(key, width=width, anchor=anchor, stretch=(key in ('caption', 'groups')), minwidth=40)
        self.headers = SortHeaders(self.tree, {k: t for k, t in SORTABLE.items()}, self.refresh, self.sort)
        self.tree.heading('tracked', text='Verfolgt')
        self.tree.grid(row=0, column=0, sticky='nsew')
        sb = ttk.Scrollbar(vpage, command=self.tree.yview); self.tree.configure(yscrollcommand=sb.set); sb.grid(row=0, column=1, sticky='ns')
        hs = ttk.Scrollbar(vpage, orient='horizontal', command=self.tree.xview); hs.grid(row=1, column=0, sticky='ew'); hs.grid_remove()
        self.fit = ColumnFitter(self.tree, FIT, hs)
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self._selection_changed())
        self.tree.bind('<Double-1>', lambda _e: self.open_selected())
        self.tree.bind('<Return>', lambda _e: self.open_selected())
        self.tree.bind('<Control-a>', self._select_all)
        self.tree.bind('<Delete>', lambda _e: self.remove_from_group())
        self._build_empty(vpage)

        acts = FlowBar(vpage); acts.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        self.vbtn = {}
        for key, text, fn in (('add', 'Zu Gruppe hinzufügen …', self.add_to_group), ('creator', 'Ersteller zur Gruppe …', self.add_creators),
                              ('remove', 'Aus Gruppe entfernen', self.remove_from_group),
                              ('track', 'Verlauf verfolgen …', self.track), ('open', 'Im Browser öffnen', self.open_selected)):
            self.vbtn[key] = ttk.Button(acts, text=text, command=fn); acts.add(self.vbtn[key])
        Tooltip(self.vbtn['track'], 'Der Hintergrunddienst ruft diese Videos regelmäßig neu ab: neue Kommentare und der Verlauf der Zahlen.')
        Tooltip(self.vbtn['creator'], 'Ordnet die Ersteller der gewählten Videos einer Gruppe zu. Danach gehören auch ihre anderen Videos zur Gruppe.')
        acts.add(ttk.Button(acts, text='Export …', command=self.export), right=True)
        self.video_info = ttk.Label(vpage, text='', foreground=COLORS['muted'], justify='left')
        self.video_info.grid(row=3, column=0, columnspan=2, sticky='w', pady=(4, 0))
        bind_wrap(self.video_info, vpage, margin=12)

    def _build_people(self, upper):
        ppage = ttk.Frame(upper); ppage.grid(row=0, column=0, sticky='nsew')
        ppage.columnconfigure(0, weight=1); ppage.rowconfigure(1, weight=1)
        ppage.grid_remove()
        self.ppage = ppage
        pbar = FlowBar(ppage); pbar.grid(row=0, column=0, sticky='ew', pady=(0, 4))
        pbar.add(ttk.Label(pbar, text='Zeige:'))
        self.show_box = ttk.Combobox(pbar, textvariable=self.show_filter, width=21, state='readonly', values=[s[0] for s in logic.SHOW_FILTERS])
        pbar.add(self.show_box); self.show_box.bind('<<ComboboxSelected>>', lambda _e: self.refresh())
        pbar.add(ttk.Label(pbar, text='Neu heißt:'))
        self.window_box = ttk.Combobox(pbar, textvariable=self.new_window, width=21, values=[w[0] for w in logic.NEW_WINDOWS])
        pbar.add(self.window_box)
        for ev in ('<<ComboboxSelected>>', '<Return>', '<FocusOut>'):
            self.window_box.bind(ev, lambda _e: self.refresh())
        Tooltip(self.window_box, 'Neu ist ein Autor, den die Datenbank vorher noch nie gesehen hat. Aus der Liste wählen oder ein Datum eintippen, z. B. 31.12.2026.')
        self.new_counter = ttk.Label(pbar, text='', foreground=COLORS['muted'])
        pbar.add(self.new_counter)
        holder = ttk.Frame(ppage); holder.grid(row=1, column=0, sticky='nsew')
        holder.columnconfigure(0, weight=1); holder.rowconfigure(0, weight=1)
        self.plist = {}
        for role in ('creator', 'commenter'):
            lst = PeopleList(holder, role, self._people_selection_changed, self.refresh, on_delete=self.people_remove)
            lst.grid(row=0, column=0, sticky='nsew')
            lst.grid_remove()
            self.plist[role] = lst
        acts = FlowBar(ppage); acts.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        self.pbtn = {}
        for key, text, fn in (('add', 'Zu Gruppe hinzufügen …', self.people_add), ('remove', 'Aus Gruppe entfernen', self.people_remove),
                              ('new', 'Neue Gruppe …', self.people_new_group)):
            self.pbtn[key] = ttk.Button(acts, text=text, command=fn); acts.add(self.pbtn[key])
        Tooltip(self.pbtn['remove'], 'Nimmt die gewählten Personen aus der links gewählten Gruppe. Gespeichert bleiben sie.')
        Tooltip(self.pbtn['new'], 'Legt eine neue Gruppe an und ordnet die gewählten Personen gleich zu.')
        acts.add(ttk.Button(acts, text='Export …', command=self.export), right=True)

    def _place_sash(self):
        """Beim ersten Anzeigen: oben (Videoliste) etwas mehr Platz als unten (Details)."""
        if getattr(self, '_sash_done', False):
            return
        h = self._panes.winfo_height()
        if h > 150:
            try: self._panes.sashpos(0, int(h * getattr(self, '_sash_ratio', 0.5)))
            except tk.TclError: return
            self._sash_done = True

    def _keep_sash(self):
        """Die Videoliste soll beim Vergrößern oder Verkleinern des Fensters nicht auf zwei Zeilen schrumpfen (solange Marc den Trenner nicht selbst zieht)."""
        if self._sash_user:
            return
        h = self._panes.winfo_height()
        low = 50 < self.winfo_height() < 580     # niedriges Fenster: die Videoliste bekommt den Platz, der Stimmungsbalken entfällt
        if low != getattr(self, '_low', False):
            self._low = low
            (self.mood_bar.grid_remove if low else self.mood_bar.grid)()
        if h > 150:
            try:
                self._panes.sashpos(0, int(h * (0.62 if low else getattr(self, '_sash_ratio', 0.5))))
            except tk.TclError:
                pass

    def _build_empty(self, parent):
        self.empty = ttk.Frame(parent)
        self.empty_text = ttk.Label(self.empty, text='', justify='center', foreground=COLORS['muted'], wraplength=380)
        self.empty_text.pack()
        self.empty_btn = ttk.Button(self.empty, text='Zum Tab „Abrufen“', command=lambda: self.app.select_tab('Abrufen'))
        self.empty_btn.pack(pady=(8, 0))


    def _build_overview(self):
        page = ttk.Frame(self.nb, padding=(0, 6, 0, 0)); page.columnconfigure(0, weight=1); page.rowconfigure(0, weight=1)
        self.nb.add(page, text='  Gruppenübersicht  ')
        cols = ('name', 'videos', 'creators', 'commenters', 'comments', 'views', 'likes', 'avg_len', 'min_len', 'max_len',
                'pos', 'neu', 'neg', 'members')
        self.otree = ttk.Treeview(page, columns=cols, show='tree headings', height=4)
        self.otree.column('#0', width=38, stretch=False)
        for c, h, w, a in (('name', 'Gruppe', 160, 'w'), ('videos', 'Videos', 56, 'e'), ('creators', 'Ersteller', 66, 'e'),
                           ('commenters', 'Schreiber', 70, 'e'), ('comments', 'Kommentare', 82, 'e'),
                           ('views', 'Aufrufe', 80, 'e'), ('likes', 'Likes', 62, 'e'),
                           ('avg_len', 'Ø Länge', 66, 'e'), ('min_len', 'Kürzestes', 70, 'e'), ('max_len', 'Längstes', 70, 'e'),
                           ('pos', 'Positiv (KI)', 80, 'e'), ('neu', 'Neutral (KI)', 82, 'e'), ('neg', 'Negativ (KI)', 82, 'e'),
                           ('members', 'Mitglieder (KI)', 120, 'center')):
            self.otree.heading(c, text=h); self.otree.column(c, width=w, anchor=a, stretch=(c == 'name'))
        hs = ttk.Scrollbar(page, orient='horizontal', command=self.otree.xview); hs.grid(row=1, column=0, sticky='ew'); hs.grid_remove()
        self.otree.configure(xscrollcommand=lambda a, b: (hs.set(a, b), hs.grid_remove() if float(a) <= 0 and float(b) >= 1 else hs.grid()))
        self.otree.grid(row=0, column=0, sticky='nsew')
        note = ttk.Label(page, text='Summen je Gruppe. „Schreiber“ = zugeordnete Kommentarschreiber. Stimmung = KI-Einschätzung (Schätzung) der Kommentare unter den '
                             'Videos der Gruppe; „Mitglieder“ zählt ▲ positiv, ● neutral, ▼ negativ aus den Kommentaren der Gruppenmitglieder.',
                  foreground=COLORS['muted'], wraplength=700, justify='left')
        note.grid(row=2, column=0, sticky='w', pady=(4, 0)); bind_wrap(note, page, margin=12)
        self.ofit = ColumnFitter(self.otree, OVERVIEW_FIT, hs, arrow=0)
        self.otree.bind('<Double-1>', self._overview_open)
        self.overview_hint = ttk.Label(page, text='Noch keine Gruppen. Lege links mit „Neu …“ eine an, um Videos und Personen zu sortieren.',
                                       foreground=COLORS['muted'], justify='center')
        bind_wrap(self.overview_hint, page, margin=40)

    def _build_length(self):
        page = ttk.Frame(self.nb, padding=(0, 6, 0, 0)); page.columnconfigure(0, weight=1); page.rowconfigure(0, weight=1)
        self.nb.add(page, text='  Länge im Vergleich  ')
        cols = ('klass', 'videos', 'avg_len', 'views', 'likes', 'comments')
        self.ltree = ttk.Treeview(page, columns=cols, show='headings', height=4, selectmode='browse')
        for c, h, w, a in (('klass', 'Längenklasse', 160, 'w'), ('videos', 'Videos', 70, 'e'), ('avg_len', 'Ø Länge', 80, 'e'),
                           ('views', 'Ø Aufrufe', 100, 'e'), ('likes', 'Ø Likes', 100, 'e'), ('comments', 'Ø Kommentare', 110, 'e')):
            self.ltree.heading(c, text=h); self.ltree.column(c, width=w, anchor=a, stretch=(c == 'klass'), minwidth=40)
        self.ltree.grid(row=0, column=0, sticky='nsew')
        hs = ttk.Scrollbar(page, orient='horizontal', command=self.ltree.xview); hs.grid(row=1, column=0, sticky='ew'); hs.grid_remove()
        self.lfit = ColumnFitter(self.ltree, LENGTH_FIT, hs, arrow=0)
        self.length_note = ttk.Label(page, text='', foreground=COLORS['muted'], justify='left')
        self.length_note.grid(row=2, column=0, sticky='w', pady=(4, 0))
        bind_wrap(self.length_note, page, margin=12)

    def _length_limits(self):
        try:
            cfg = core.read_settings(self.root())
            return int(cfg.length_short_s), int(cfg.length_long_s)
        except Exception:   # kaputte settings.toml: mit den Standardwerten weiterarbeiten
            return 60, 600

    def _length_changed(self):
        short, long = self._length_limits()
        self.length_key = dict(logic.length_filter_labels(short, long)).get(self.length_var.get())
        self.refresh()

    def _fill_length(self, short, long):
        """Tabelle „Länge im Vergleich“ für die aktuelle Ansicht (Gruppe, Plattform, Suche); Mittelwerte nur aus gemessenen Werten."""
        self.ltree.delete(*self.ltree.get_children())
        try:
            rows = logic.length_comparison(core.db_path(), self.search.get(), self._platform_filter(), self.view, short, long)
        except Exception as error:
            self.length_note.configure(text='Längenvergleich nicht lesbar', foreground=COLORS['err'])
            self.app.log(f'Länge im Vergleich: {logic.friendly_error(error)}', 'ERROR')
            return
        names = {'short': f'kurz (bis {logic._span(short)})', 'medium': f'mittel ({logic._span(short)} bis {logic._span(long)})',
                 'long': f'lang (über {logic._span(long)})', 'none': 'ohne bekannte Länge'}

        def avg(value):
            return '' if value is None else logic.fmt_number(round(value))
        for r in rows:
            self.ltree.insert('', 'end', iid=r['key'], values=(
                names[r['key']], logic.fmt_number(r['videos']), logic.format_duration(r['avg_length']) if r['key'] != 'none' else '',
                avg(r['avg_views']), avg(r['avg_likes']), avg(r['avg_comments'])))
        self.lfit.refit()
        total = sum(r['videos'] for r in rows)
        missing = rows[-1]['videos'] if rows else 0   # keine Datenbankdatei: leere Liste
        self.length_note.configure(
            text=(f'{total} Videos in dieser Ansicht, bei {missing} ist keine Länge bekannt (sie zählen nicht in den Mittelwerten). '
                  'Mittelwerte stammen nur aus gemessenen Werten; ein leerer Wert zählt nicht als 0.') if total else
            'Noch keine Videos in dieser Ansicht.', foreground=COLORS['muted'])

    # ---------- Laden ----------

    def _platform_filter(self):
        name = self.filter_platform.get()
        return next((k for k, v in core.PLATFORMS.items() if v['name'] == name), '')

    def _people_group(self):
        if self.view == logic.ALL:
            return None
        return 'none' if self.view == logic.UNGROUPED else self.view

    def refresh(self):
        db = core.db_path()
        mode = self.mode.get()
        keep = set(self.tree.selection())
        people = None
        try:
            overview = logic.group_overview(db)
            rows = []
            short, long = self._length_limits()
            labels = logic.length_filter_labels(short, long)
            self.length_box.configure(values=[t for t, _k in labels])
            self.length_var.set(next((t for t, k in labels if k == self.length_key), labels[0][0]))
            if mode == 'videos':
                rows = logic.query_videos(db, self.search.get(), self._platform_filter(), self.view, self.sort['key'], self.sort['desc'],
                                          length=self.length_key, short=short, long=long)
            else:
                lst = self.plist[mode]
                since, show = self._new_since(db), dict(logic.SHOW_FILTERS).get(self.show_filter.get())
                group = 'none' if show == 'none' else self._people_group()
                people = logic.people_list(db, mode, platform=self._platform_filter(), group=group, search=self.search.get(),
                                           sort=lst.sort['key'], desc=lst.sort['desc'],
                                           first_seen_since=since if show in ('new', 'known') else None, known=(show == 'known') if show in ('new', 'known') else None)
            videos, comments = logic.count_videos(db)
        except Exception as error:   # gesperrt, kaputt, Platte weg: sagen, nicht abstürzen
            self.data_info.configure(text='Datenbank nicht lesbar', foreground=COLORS['err'])
            self.app.log(f'Gespeicherte Daten: {logic.friendly_error(error)}', 'ERROR')
            self._show_empty('Die gespeicherten Daten lassen sich gerade nicht lesen.\n' + logic.friendly_error(error), None)
            return
        self._fill_groups(overview)
        self._fill_overview(overview)
        self._show_mode_pages()
        self.tree.delete(*self.tree.get_children()); self.video_rows.clear()
        fmt = logic.fmt_number
        for r in rows:
            self.video_rows[r['uid']] = r
            self.tree.insert('', 'end', iid=r['uid'], values=(
                core.PLATFORMS.get(r['platform'], {}).get('name', r['platform']), r['author'] or '',
                ' '.join((r['caption'] or '').split())[:140], fmt(r['view_count']), fmt(r['like_count']), fmt(r['comment_count']),
                logic.format_duration(r['duration_s']), logic.short_date(r['created_at']), r['group_names'] or '',
                '✓' if r['tracked'] else ''))
        self.fit.refit()
        self._fill_length(short, long)
        for uid in keep:
            if self.tree.exists(uid):
                self.tree.selection_add(uid)
        if videos is None:   # Zählen gescheitert (z. B. offenes Journal), der Rest ging
            self.data_info.configure(text='Datenbank gerade nicht lesbar', foreground=COLORS['err'])
        else:
            self.data_info.configure(text=f'{videos} Videos, {comments} Kommentare gesamt', foreground=COLORS['muted'])
        if mode != 'videos':
            self._fill_people(mode, people, videos)
        elif not rows:
            if videos is None:
                self._show_empty('Die gespeicherten Daten lassen sich gerade nicht lesen.\nBitte gleich noch einmal aktualisieren.', None)
            elif videos == 0:
                self._show_empty('Noch keine Videos gespeichert.\nRuf im Tab „Abrufen“ ein Video ab – es erscheint dann hier.', True)
            elif self.view == logic.UNGROUPED:
                self._show_empty('Alle Videos sind schon in einer Gruppe.', None)
            elif self.view != logic.ALL:
                self._show_empty('In dieser Gruppe sind noch keine Videos.\nWähle unter „Alle Videos“ Videos aus und klicke auf „Zu Gruppe hinzufügen …“.', None)
            else:
                self._show_empty('Keine Treffer. Suche oder Plattform-Filter ändern.' if not self.length_key else
                             'Keine Videos in dieser Längenklasse. Videos ohne bekannte Länge zeigt der Filter nicht.', None)
        else:
            self.empty.place_forget()
        self._update_view_label()
        self._selection_changed()

    def _fill_people(self, role, people, videos):
        rows, total = people if people is not None else ([], 0)
        text = None
        if not rows:
            if videos == 0:
                text = PEOPLE_EMPTY[role]
            elif self.view == logic.UNGROUPED:
                text = f'Alle {ROLE_PLURAL[role]} sind schon in einer Gruppe.'
            elif self.view != logic.ALL:
                text = (f'In dieser Gruppe sind noch keine {ROLE_PLURAL[role]}.\nWechsle links zu „Alle Videos“, wähle hier Personen '
                        'und klicke auf „Zu Gruppe hinzufügen …“.')
            elif (self.search.get() or '').strip() or self._platform_filter():
                text = 'Keine Treffer. Suche oder Plattform-Filter ändern.'
        self.plist[role].fill(rows, text, self._new_since(core.db_path()))
        self._people_total = total
        self._update_new_counter(role)

    def _new_since(self, db=None):
        """UTC-Zeit, ab der ein Autor „neu“ heißt (None, wenn die Auswahl nicht verstanden wurde – dann steht der Grund am Zähler)."""
        try:
            return logic.new_since(db or core.db_path(), self.new_window.get())
        except ValueError as error:
            self._new_error = str(error)
            return None
        finally:
            pass

    def _update_new_counter(self, role):
        since = self._new_since(core.db_path())
        if since is None:
            self.new_counter.configure(text=self._new_error, foreground=COLORS['err'])
            return
        try:
            n = logic.new_people_count(core.db_path(), role, since)
        except Exception as error:
            self.new_counter.configure(text='Zähler nicht lesbar', foreground=COLORS['err'])
            self.app.log(f'Neue Autoren: {logic.friendly_error(error)}', 'ERROR')
            return
        noun = 'neuer Ersteller' if (role == 'creator' and n == 1) else 'neue Ersteller' if role == 'creator' else \
            'neuer Kommentarschreiber' if n == 1 else 'neue Kommentarschreiber'
        self.new_counter.configure(text=f'{n} {noun} {self.new_window.get()}', foreground=COLORS['ok'] if n else COLORS['muted'])

    def _show_mode_pages(self):
        mode = self.mode.get()
        if mode != getattr(self, '_last_mode', 'videos'):   # Personenlisten brauchen oben mehr Platz, Videos unten (Kommentare)
            self._last_mode = mode
            self._sash_done = False
            self._sash_ratio = 0.5 if mode == 'videos' else 0.58
            track_after(self, 50, self._place_sash)
        if mode == 'videos':
            self.ppage.grid_remove(); self.vpage.grid()
        else:
            self.vpage.grid_remove(); self.ppage.grid()
            for role, lst in self.plist.items():
                if role == mode:
                    lst.grid()
                else:
                    lst.grid_remove()
            self.empty.place_forget()
        if mode == 'creator':
            self.mood_bar.grid_remove()
        else:
            self.mood_bar.grid()

    def _mode_changed(self):
        self.refresh()

    def _update_view_label(self):
        mode, group = self.mode.get(), self._selected_group()
        text = ''
        if group is not None:
            if mode == 'videos':
                text = f'Gruppe „{group["name"]}“: Videos, die in der Gruppe sind, und Videos von Erstellern der Gruppe.'
            else:
                text = f'Gruppe „{group["name"]}“: nur {ROLE_PLURAL[mode]}, die du dieser Gruppe zugeordnet hast.'
        elif self.view == logic.UNGROUPED:
            text = 'Ohne Gruppe: Videos, die in keiner Gruppe sind und deren Ersteller in keiner Gruppe ist.' if mode == 'videos' \
                else f'Ohne Gruppe: {ROLE_PLURAL[mode]}, die in keiner Gruppe sind.'
        if text:
            self.view_label.configure(text=text); self.view_label.grid()
        else:
            self.view_label.grid_remove()

    def _show_empty(self, text, with_button):
        self.empty_text.configure(text=text)
        if with_button:
            self.empty_btn.pack(pady=(8, 0))
        else:
            self.empty_btn.pack_forget()
        self.vpage.grid(); self.ppage.grid_remove()
        self.empty.place(relx=0.5, rely=0.5, anchor='center')
        self.empty.lift()

    def _fill_groups(self, ov):
        want = self.view
        self.gtree.delete(*self.gtree.get_children())
        self.groups = {g['id']: g for g in ov['groups']}
        self.group_stats = {s['id']: s for s in ov['stats']}
        self.gtree.insert('', 'end', iid=logic.ALL, text=f'Alle Videos ({ov["total"]})')
        self.gtree.insert('', 'end', iid=logic.UNGROUPED, text=f'Ohne Gruppe ({ov["ungrouped"]})')
        for g in ov['groups']:
            s = self.group_stats.get(g['id'], {})
            counts = CONNECTOR.join(str(n) for n in (g['video_count'], s.get('creators', 0), s.get('commenters', 0)))
            self.gtree.insert('', 'end', iid=str(g['id']), text=f'{g["name"]} ({counts})', image=dot_image(self, g['color']))
        iid = str(want)
        if want not in (logic.ALL, logic.UNGROUPED) and not self.gtree.exists(iid):   # Gruppe wurde gelöscht
            self.view, iid = logic.ALL, logic.ALL
        self.gtree.selection_set(iid)
        self._update_buttons()

    def _fill_overview(self, ov):
        self.otree.delete(*self.otree.get_children())
        colors = {g['id']: g['color'] for g in ov['groups']}
        for s in ov['stats']:
            mm = s.get('member_mood') or {}
            members = f'▲ {mm.get("positiv", 0)}  ● {mm.get("neutral", 0)}  ▼ {mm.get("negativ", 0)}' if sum(mm.values()) else ''
            m = s.get('mood') or {}
            show = bool(sum(m.values()))
            self.otree.insert('', 'end', iid=str(s['id']), image=dot_image(self, colors.get(s['id'], '#2563eb')),
                              values=(s['name'], logic.fmt_number(s['videos']), logic.fmt_number(s.get('creators', 0)),
                                      logic.fmt_number(s.get('commenters', 0)), logic.fmt_number(s['comments']),
                                      logic.fmt_number(s['views']), logic.fmt_number(s['likes']),
                                      logic.format_duration(s.get('avg_duration')), logic.format_duration(s.get('min_duration')),
                                      logic.format_duration(s.get('max_duration')),
                                      m.get('positiv', 0) if show else '', m.get('neutral', 0) if show else '', m.get('negativ', 0) if show else '',
                                      members))
        self.ofit.refit()
        if ov['stats']:
            self.overview_hint.grid_remove()
        else:
            self.overview_hint.grid(row=0, column=0)
            self.overview_hint.lift()

    # ---------- Auswahl ----------

    def _group_selected(self):
        sel = self.gtree.selection()
        if not sel:
            return
        iid = sel[0]
        view = iid if iid in (logic.ALL, logic.UNGROUPED) else int(iid)
        if view == self.view:   # auch das programmierte Setzen der Auswahl löst dieses Ereignis aus
            return
        self.view = view
        self.refresh()

    def show_group(self, group_id):
        """Von außen (nach einem Abruf): diese Gruppe anzeigen."""
        self.view = group_id if group_id is not None else logic.ALL
        self.mode.set('videos')
        self.search.set(''); self.filter_platform.set('Alle')
        self.refresh()

    def focus_uids(self, uids):
        """Gewählte Videos markieren und sichtbar machen (nach einem Abruf)."""
        found = [u for u in uids if self.tree.exists(u)]
        if found:
            self.tree.selection_set(found)
            self.tree.see(found[0])

    def _select_all(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        return 'break'

    def _selected_uids(self):
        return [u for u in self.tree.selection() if u in self.video_rows]

    def _selected_group(self):
        return self.groups.get(self.view) if isinstance(self.view, int) else None

    def _selected_people(self):
        mode = self.mode.get()
        return self.plist[mode].selected() if mode in self.plist else []

    def _update_buttons(self):
        have = bool(self.tree.selection()) if hasattr(self, 'tree') else False
        if hasattr(self, 'vbtn'):
            for key in ('add', 'creator', 'track', 'open'):
                self.vbtn[key].configure(state='normal' if have else 'disabled')
            self.vbtn['remove'].configure(state='normal' if have and self._selected_group() else 'disabled')
            self.vbtn['open'].configure(state='normal' if len(self.tree.selection()) == 1 else 'disabled')
        if hasattr(self, 'pbtn'):
            people = bool(self._selected_people())
            for key in ('add', 'new'):
                self.pbtn[key].configure(state='normal' if people else 'disabled')
            self.pbtn['remove'].configure(state='normal' if people and self._selected_group() else 'disabled')
        if hasattr(self, 'gbtn'):
            for key in ('edit', 'del'):
                self.gbtn[key].configure(state='normal' if self._selected_group() else 'disabled')
        if hasattr(self, 'cbtn_people'):   # diese zwei erscheinen nur, wenn sie etwas tun können (spart Platz)
            self.cbar.set_visible(self.cbtn_people, bool(self.ctree.selection()) and self.mode.get() != 'creator')
            self.cbar.set_visible(self.members_cb, self._selected_group() is not None and self.mode.get() == 'videos')

    def _selection_changed(self):
        self._update_buttons()
        if self._detail_job is not None:
            self.after_cancel(self._detail_job)
        self._detail_job = self.after(60, self._show_details)

    def _people_selection_changed(self):
        self._selection_changed()

    def _show_video_info(self):
        row = self._single()
        if row is None:
            self.video_info.configure(text='')
            return
        short, long = self._length_limits()
        seconds = row.get('duration_s')
        klass = logic.duration_class(seconds, short, long)
        text = ('Länge: ' + logic.format_duration(seconds) + f' ({logic.LENGTH_NAMES[klass]})') if klass else \
            'Länge: unbekannt (die Plattform hat keine Länge geliefert)'
        self.video_info.configure(text=text + (f'  ·  veröffentlicht {logic.short_date(row["created_at"])}' if row.get('created_at') else ''))

    def _show_details(self):
        self._detail_job = None
        self._show_video_info()
        self._show_comments()
        self._update_mood_bar()
        self._show_history()

    def _single(self):
        sel = self._selected_uids()
        return self.video_rows[sel[0]] if len(sel) == 1 else None

    # Kommentar- und Verlaufsansichten: data_details.DataDetailsSection.


    def _overview_open(self, _event=None):
        sel = self.otree.selection()
        if sel and self.gtree.exists(sel[0]):
            self.gtree.selection_set(sel[0])

    # ---------- Stimmung bestimmen ----------

    def _goto_mood_settings(self):
        self.app.select_tab('Einstellungen')
        settings = getattr(self.app, 'tabs', {}).get('Einstellungen')
        if settings is not None and hasattr(settings, 'show_section'):
            settings.show_section('ki')

    def _mood_options(self):
        mode, group = self.mode.get(), self._selected_group()
        options = []
        if mode == 'commenter' and self._selected_people():
            refs = self._selected_people()
            options.append({'key': 'people', 'label': f'Kommentare der gewählten Kommentarschreiber ({len(refs)})',
                            'scope': {'people': [(p['platform'], p['author_key']) for p in refs]}})
        if mode == 'videos' and self._selected_uids():
            sel = self._selected_uids()
            options.append({'key': 'selection', 'label': f'Gewählte Videos ({len(sel)})', 'scope': {'videos': sel}})
        if group is not None:
            options.append({'key': 'group', 'label': f'Aktuelle Gruppe „{group["name"]}“ (Videos der Gruppe und ihrer Ersteller)',
                            'scope': {'videos': list(self.video_rows)} if mode == 'videos' else {'group': group['id']}})
            if (self.group_stats.get(group['id']) or {}).get('commenters'):
                options.append({'key': 'commenters', 'label': f'Kommentare der Kommentarschreiber der Gruppe „{group["name"]}“',
                                'scope': {'commenter_group': group['id']}})
        elif mode == 'videos' and (self.view != logic.ALL or (self.search.get() or '').strip() or self._platform_filter()):
            options.append({'key': 'list', 'label': f'Alle Videos in der Liste ({len(self.video_rows)})', 'scope': {'videos': list(self.video_rows)}})
        total = (logic.count_videos(core.db_path()) or (0, 0))[1]
        options.append({'key': 'all', 'label': f'Alle gespeicherten Kommentare ({total if total is not None else "?"})', 'scope': {}})
        return options

    def mood_start(self):
        """Der Knopf „Stimmung bestimmen …“: Schlüssel prüfen, Umfang und Schätzung zeigen, nach Bestätigung im Hintergrund bestimmen."""
        if not core.db_path().is_file():
            messagebox.showinfo(TITLE, 'Es sind noch keine Kommentare gespeichert. Ruf zuerst im Tab „Abrufen“ ein Video mit Kommentaren ab.')
            return None
        cfg = aiconfig.load(self.root())
        if not aiconfig.has_key(cfg):
            ask_for_key(self, self._goto_mood_settings)
            return None
        dialog = SentimentDialog(self, self.app, self._mood_options(), core.db_path(), self.root(), on_done=lambda _r: self.refresh(),
                                 modal=self.modal_dialogs, cfg=cfg)
        return dialog

    # ---------- Gruppen verwalten ----------

    def _group_dialog(self, title, group=None):
        done = {}

        def validate(values):
            try:
                if group is None:
                    done['group'] = logic.create_group(core.db_path(), values['name'], values['color'])
                else:
                    logic.update_group(core.db_path(), group['id'], values['name'], values['color'])
                    done['group'] = group
            except Exception as error:
                return logic.friendly_error(error)
            return None
        res = ask_form(self, title, [{'key': 'name', 'label': 'Name', 'value': group['name'] if group else ''},
                                     {'key': 'color', 'label': 'Farbe', 'kind': 'color', 'value': group['color'] if group else None}],
                       ok_text='Speichern', validator=validate)
        return done.get('group') if res is not None else None

    def group_new(self):
        g = self._group_dialog('Neue Gruppe')
        if g:
            self.app.log(f'Gruppe angelegt: {g["name"]}', 'OK')
            self.view = g['id']; self.refresh()

    def group_edit(self):
        g = self._selected_group()
        if g is None:
            messagebox.showinfo(TITLE, 'Bitte links zuerst eine Gruppe wählen.'); return
        if self._group_dialog('Gruppe ändern', g):
            self.refresh()

    def group_delete(self):
        g = self._selected_group()
        if g is None:
            return
        n = g.get('video_count', 0)
        s = self.group_stats.get(g['id']) or {}
        people = (s.get('creators', 0), s.get('commenters', 0))
        extra = f', {people[0]} Ersteller und {people[1]} Kommentarschreiber' if any(people) else ''
        if not messagebox.askyesno(TITLE, f'Gruppe „{g["name"]}“ löschen?\n\nAlle Videos und Personen bleiben gespeichert (in dieser Gruppe: {n} Videos{extra}) – sie sind danach nur nicht mehr in dieser Gruppe.'):
            return
        ok, _ = self.attempt('Gruppe löschen', logic.delete_group, core.db_path(), g['id'])
        if ok:
            self.app.log(f'Gruppe gelöscht: {g["name"]}', 'OK')
            self.view = logic.ALL; self.refresh()

    # ---------- Aktionen für gewählte Videos ----------

    def _need_videos(self):
        uids = self._selected_uids()
        if not uids:
            messagebox.showinfo(TITLE, 'Bitte zuerst ein oder mehrere Videos in der Liste anklicken (Strg+A wählt alle).')
        return uids

    def _ask_group(self, title, intro, adder, count, noun):
        """Dialog „zu welcher Gruppe?“ (vorhandene oder neue). adder(gid) -> (neu, schon drin, ...) oder Zahl. Gibt (Gruppen-Nr, Name, Ergebnis) oder None zurück."""
        groups = list(self.groups.values())
        done = {}

        def validate(values):
            new_name = (values.get('new') or '').strip()
            try:
                if new_name:
                    gid = logic.create_group(core.db_path(), new_name)['id']
                elif values.get('group'):
                    gid = int(values['group'])
                else:
                    return 'Bitte eine Gruppe wählen oder unten einen Namen für eine neue Gruppe eintippen.'
                done['gid'], done['result'] = gid, adder(gid)
            except Exception as error:
                return logic.friendly_error(error)
            return None
        fields = []
        if groups:
            fields.append({'key': 'group', 'label': 'Gruppe', 'kind': 'choice',
                           'value': str(self.view) if isinstance(self.view, int) else str(groups[0]['id']),
                           'choices': [(str(g['id']), g['name']) for g in groups]})
        fields.append({'key': 'new', 'label': 'Oder neue Gruppe' if groups else 'Neue Gruppe', 'hint': 'Leer lassen, um eine vorhandene zu nehmen.' if groups else ''})
        res = ask_form(self, title, fields, intro=intro, ok_text='Hinzufügen', validator=validate)
        if res is None:
            return None
        name = (self.groups.get(done['gid']) or {}).get('name') or (res.get('new') or '').strip()
        return done['gid'], name, done['result']

    def add_to_group(self):
        uids = self._need_videos()
        if not uids:
            return
        got = self._ask_group('Zu Gruppe hinzufügen', f'{core.plural(len(uids), "Video", "Videos")} einer Gruppe zuordnen. Ein Video kann in mehreren Gruppen sein.',
                              lambda gid: logic.add_to_group(core.db_path(), gid, uids), len(uids), 'Video')
        if got is None:
            return
        _gid, name, added = got
        msg = f'{core.plural(added, "Video", "Videos")} zur Gruppe „{name}“ hinzugefügt' + (f' ({len(uids) - added} waren schon drin).' if added < len(uids) else '.')
        self.app.set_status(msg, 'ok'); self.app.log(msg, 'OK')
        self.refresh()

    def _add_people_dialog(self, role, refs, title='Zu Gruppe hinzufügen'):
        """Gemeinsamer Weg für alle Stellen, an denen Personen einer Gruppe zugeordnet werden."""
        if not refs:
            messagebox.showinfo(TITLE, 'Bitte zuerst Personen auswählen.')
            return None
        noun = ROLE_PLURAL[role]
        got = self._ask_group(title, f'{len(refs)} {noun} einer Gruppe zuordnen. Eine Person kann in mehreren Gruppen sein; ihre Videos und '
                                     'Kommentare bleiben unverändert. Das Einordnen von Personen ist eine Auswertung von Personen – '
                                     'du entscheidest, wofür du sie nutzt.',
                              lambda gid: logic.add_people_to_group(core.db_path(), gid, role, refs), len(refs), noun)
        if got is None:
            return None
        _gid, name, (added, already, unusable) = got
        msg = f'{added} {noun} zur Gruppe „{name}“ hinzugefügt' + (f' ({already} waren schon drin)' if already else '') \
            + (f', {unusable} ohne Namen übersprungen' if unusable else '') + '.'
        self.app.set_status(msg, 'ok'); self.app.log(msg, 'OK')
        self.refresh()
        return msg

    def add_creators(self):
        """Aus der Videoauswahl: die Ersteller der gewählten Videos einer Gruppe zuordnen."""
        uids = self._need_videos()
        if not uids:
            return None
        ok, refs = self.attempt('Ersteller ermitteln', logic.person_refs, core.db_path(), 'creator', uids=uids)
        return self._add_people_dialog('creator', refs, 'Ersteller zur Gruppe hinzufügen') if ok else None

    def add_commenters(self):
        """Aus der Kommentarliste (Mehrfachauswahl): die Kommentarschreiber der gewählten Kommentare einer Gruppe zuordnen."""
        uids = [self.comment_rows[i]['uid'] for i in self.ctree.selection() if i in self.comment_rows]
        if not uids:
            messagebox.showinfo(TITLE, 'Bitte zuerst einen oder mehrere Kommentare in der Liste anklicken (Strg+A wählt alle).')
            return None
        ok, refs = self.attempt('Kommentarschreiber ermitteln', logic.person_refs, core.db_path(), 'commenter', comment_uids=uids)
        return self._add_people_dialog('commenter', refs, 'Kommentarschreiber zur Gruppe hinzufügen') if ok else None

    def people_add(self):
        mode = self.mode.get()
        return self._add_people_dialog(mode, self._selected_people()) if mode in self.plist else None

    def people_new_group(self):
        mode = self.mode.get()
        refs = self._selected_people()
        if mode not in self.plist or not refs:
            messagebox.showinfo(TITLE, 'Bitte zuerst Personen auswählen.')
            return None
        g = self._group_dialog('Neue Gruppe')
        if not g:
            return None
        ok, out = self.attempt('Zu Gruppe hinzufügen', logic.add_people_to_group, core.db_path(), g['id'], mode, refs)
        if ok:
            msg = f'Gruppe „{g["name"]}“ angelegt, {out[0]} {ROLE_PLURAL[mode]} hinzugefügt.'
            self.app.set_status(msg, 'ok'); self.app.log(msg, 'OK')
            self.view = g['id']
            self.refresh()
            return msg
        self.refresh()
        return None

    def people_remove(self):
        mode = self.mode.get()
        refs = self._selected_people()
        group = self._selected_group()
        if mode not in self.plist:
            return
        if group is None:
            if refs:
                self.app.set_status('Zum Entfernen links zuerst die Gruppe wählen, aus der die Personen raus sollen.', 'warn')
            return
        if not refs:
            messagebox.showinfo(TITLE, 'Bitte zuerst Personen in der Liste anklicken.'); return
        noun = ROLE_PLURAL[mode]
        if not messagebox.askyesno(TITLE, f'{len(refs)} {noun} aus der Gruppe „{group["name"]}“ entfernen?\n\nSie bleiben gespeichert, ebenso ihre Videos und Kommentare.'):
            return
        ok, n = self.attempt('Aus Gruppe entfernen', logic.remove_people_from_group, core.db_path(), group['id'], mode, refs)
        if ok:
            self.app.set_status(f'{n} {noun} aus „{group["name"]}“ entfernt.', 'ok')
            self.refresh()

    def remove_from_group(self):
        uids = self._selected_uids()
        group = self._selected_group()
        if group is None:
            if uids:
                self.app.set_status('Zum Entfernen links zuerst die Gruppe wählen, aus der die Videos raus sollen.', 'warn')
            return
        if not uids:
            messagebox.showinfo(TITLE, 'Bitte zuerst Videos in der Liste anklicken.'); return
        if not messagebox.askyesno(TITLE, f'{core.plural(len(uids), "Video", "Videos")} aus der Gruppe „{group["name"]}“ entfernen?\n\nDie Videos bleiben gespeichert. '
                                          'Videos, die nur über ihren Ersteller zur Gruppe gehören, bleiben drin – nimm dafür den Ersteller in der Ansicht „Ersteller“ aus der Gruppe.'):
            return
        ok, n = self.attempt('Aus Gruppe entfernen', logic.remove_from_group, core.db_path(), group['id'], uids)
        if ok:
            if n:
                self.app.set_status(f'{core.plural(n, "Video", "Videos")} aus „{group["name"]}“ entfernt.', 'ok')
            else:
                self.app.set_status('Nichts entfernt: Diese Videos gehören nur über ihren Ersteller zur Gruppe.', 'warn')
            self.refresh()

    def track(self):
        uids = self._need_videos()
        if not uids:
            return
        already = sum(1 for u in uids if self.video_rows[u]['tracked'])
        hint = f'{already} davon werden schon verfolgt und bleiben unverändert.' if already else ''
        res = ask_form(self, 'Verlauf verfolgen', [
            {'key': 'interval', 'label': 'Wie oft?', 'kind': 'choice', 'value': logic.DEFAULT_INTERVAL,
             'choices': [(m, t) for t, m in logic.INTERVALS], 'hint': hint}],
            intro=f'Für {core.plural(len(uids), "Video", "Videos")} wird ein automatisches Ziel angelegt. Der Hintergrunddienst ruft sie regelmäßig neu ab: '
                  'neue Kommentare und der Verlauf der Zahlen werden gespeichert. Ob der Dienst läuft, siehst du im Tab „Automatik“.',
            ok_text='Verfolgen')
        if res is None:
            return
        ok, out = self.attempt('Verlauf verfolgen', logic.track_videos, core.db_path(), uids, int(res['interval']))
        if not ok:
            return
        parts = []
        if out['added']: parts.append(f'{out["added"]} neu verfolgt')
        if out['existing']: parts.append(f'{out["existing"]} wurde(n) schon verfolgt')
        if out['skipped']:
            parts.append(f'{len(out["skipped"])} übersprungen')
            for line in out['skipped'][:5]:
                self.app.log(f'Verfolgen übersprungen: {line}', 'WARNING')
        msg = (', '.join(parts) or 'Nichts zu tun') + '.'
        self.app.set_status(msg, 'ok' if out['added'] else 'warn'); self.app.log(msg, 'OK' if out['added'] else 'WARNING')
        if out['skipped'] and not out['added'] and not out['existing']:
            messagebox.showwarning(TITLE, msg + '\n\n' + '\n'.join(out['skipped'][:5]))
        self.refresh()

    def open_selected(self):
        sel = self._selected_uids()
        if len(sel) != 1:
            messagebox.showinfo(TITLE, 'Bitte genau ein Video wählen.'); return
        url = self.video_rows[sel[0]]['url'] or ''
        if core.detect_platform(url):
            webbrowser.open(url)
        else:
            messagebox.showinfo(TITLE, 'Zu diesem Video ist kein gültiger Link gespeichert.')

    # ---------- Export ----------

    def export(self):
        if not core.db_path().is_file():
            messagebox.showinfo(TITLE, 'Es sind noch keine Daten gespeichert.'); return
        group, sel = self._selected_group(), self._selected_uids()
        scopes = [('all', 'Alle gespeicherten Daten')]
        if group:
            scopes.append(('group', f'Nur die Gruppe „{group["name"]}“'))
        if sel:
            scopes.append(('selection', ('Nur das gewählte Video' if len(sel) == 1 else f'Nur die {len(sel)} gewählten Videos')))
        res = ask_form(self, 'Exportieren', [
            {'key': 'fmt', 'label': 'Format', 'kind': 'radio', 'value': 'csv-excel',
             'choices': [(key, text) for key, text in core.EXPORT_FORMATS.items()]},
            {'key': 'scope', 'label': 'Umfang', 'kind': 'radio', 'value': 'group' if group else 'all', 'choices': scopes}],
            intro='Videos, Kommentare (mit Stimmung als KI-Einschätzung), Personen, Gruppen und der Verlauf der Zahlen werden als einzelne Dateien in einen neuen Ordner '
                  'geschrieben. „CSV für Excel“ öffnet sich im deutschen Excel sofort richtig (Semikolon, Umlaute).', ok_text='Weiter …')
        if res is None:
            return
        folder = filedialog.askdirectory(title='Zielordner für den Export', initialdir=str(pathlib.Path.home() / 'Documents'))
        if not folder:
            return
        scope = res['scope']
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        tag = re.sub(r'[^A-Za-z0-9_-]+', '_', group['name']).strip('_') if scope == 'group' else ('auswahl' if scope == 'selection' else 'alles')
        target = pathlib.Path(folder) / f'socialscraper-{tag or "gruppe"}-{stamp}'
        kwargs = {'group_id': group['id'] if scope == 'group' else None, 'uids': sel if scope == 'selection' else None}
        self.app.set_status('Export läuft …', 'warn')
        run_in_thread(self, lambda: core.export_data(core.db_path(), target, res['fmt'], **kwargs), self._export_done,
                      daemon=False)   # Export schreibt Dateien: beim Schließen des Fensters zu Ende laufen lassen

    def _export_done(self, files, error):
        if error is not None:
            msg = logic.friendly_error(error)
            self.app.log(f'Export fehlgeschlagen: {msg}', 'ERROR'); self.app.set_status('Export fehlgeschlagen.', 'err')
            messagebox.showerror(TITLE, f'Export fehlgeschlagen.\n\n{msg}'); return
        self.app.log(f'Export fertig: {len(files)} Dateien in {files[0].parent}', 'OK'); self.app.set_status('Export fertig.', 'ok')
        self.app.open_path(files[0].parent)
