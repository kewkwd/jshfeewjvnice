"""Comment lists, sentiment scope and history panels used by DataTab."""
import tkinter as tk
from tkinter import ttk

from .. import guicore as core
from . import logic
from .common import COLORS
from .widgets import ColumnFitter, FlowBar, LineChart, SentimentChart, Tooltip, pad_label

COMMENT_FIT = {'author': (100, 1), 'text': (140, 4), 'mood': (80, 0), 'likes': (50, 0), 'seen': (80, 0), 'when': (70, 0), 'video': (80, 1)}

METRICS = (('views', 'Aufrufe', 'view_count'), ('likes', 'Likes', 'like_count'), ('comments', 'Kommentare', 'comment_count'))

MOOD_TAGS = {'positiv': 'mood_pos', 'neutral': 'mood_neu', 'negativ': 'mood_neg'}


class DataDetailsSection:
    def _build_comments(self):
        page = ttk.Frame(self.nb, padding=(0, 6, 0, 0)); page.columnconfigure(0, weight=1); page.rowconfigure(1, weight=1)
        self.nb.add(page, text='  Kommentare  ')
        bar = FlowBar(page); bar.grid(row=0, column=0, columnspan=2, sticky='ew')
        self.cbar = bar
        bar.add(ttk.Label(bar, text='Nur neue seit:'))
        self.since = tk.StringVar(value=logic.SINCE_PRESETS[0][0])
        self.since_box = ttk.Combobox(bar, textvariable=self.since, width=22, values=[p[0] for p in logic.SINCE_PRESETS])
        bar.add(self.since_box)
        self.since_box.bind('<<ComboboxSelected>>', lambda _e: self._show_comments())
        self.since_box.bind('<Return>', lambda _e: self._show_comments())
        self.since_box.bind('<FocusOut>', lambda _e: self._show_comments())
        Tooltip(self.since_box, 'Aus der Liste wählen oder ein Datum eintippen, z. B. 31.12.2026 oder 31.12.2026 18:30.')
        bar.add(ttk.Label(bar, text='Stimmung (KI):'))
        self.mood_filter = tk.StringVar(value=logic.MOOD_FILTERS[0][0])
        self.mood_box = ttk.Combobox(bar, textvariable=self.mood_filter, width=18, state='readonly', values=[m[0] for m in logic.MOOD_FILTERS])
        bar.add(self.mood_box)
        self.mood_box.bind('<<ComboboxSelected>>', lambda _e: self._show_comments())
        self.members_only = tk.BooleanVar(value=False)
        self.members_cb = ttk.Checkbutton(bar, text=pad_label('Nur Mitglieder der Gruppe'), variable=self.members_only,
                                          command=self._show_comments)
        bar.add(self.members_cb)
        self.cbtn_people = ttk.Button(bar, text='Schreiber zur Gruppe …', command=self.add_commenters)
        bar.add(self.cbtn_people, right=True)
        Tooltip(self.cbtn_people, 'Ordnet die Kommentarschreiber der gewählten Kommentare (Strg+Klick oder Strg+A) einer Gruppe zu.')
        self.comment_info = ttk.Label(bar, text='', foreground=COLORS['muted'], justify='left', wraplength=520)
        bar.add(self.comment_info)
        cols = ('author', 'text', 'mood', 'likes', 'seen', 'when', 'video')
        self.ctree = ttk.Treeview(page, columns=cols, show='headings', height=4, selectmode='extended')
        for c, h, w, a in (('author', 'Autor', 110, 'w'), ('text', 'Kommentar', 380, 'w'), ('mood', 'Stimmung (KI)', 90, 'w'),
                           ('likes', 'Likes', 50, 'e'), ('seen', 'Zuerst gesehen', 110, 'center'), ('when', 'Geschrieben', 90, 'center'),
                           ('video', 'Video', 120, 'w')):
            self.ctree.heading(c, text=h); self.ctree.column(c, width=w, anchor=a, stretch=(c == 'text'), minwidth=40)
        self.ctree.tag_configure('mood_pos', foreground=COLORS['ok'])
        self.ctree.tag_configure('mood_neg', foreground=COLORS['err'])
        self.ctree.tag_configure('mood_neu', foreground=COLORS['muted'])
        self.ctree.grid(row=1, column=0, sticky='nsew', pady=(4, 0))
        sb = ttk.Scrollbar(page, command=self.ctree.yview); self.ctree.configure(yscrollcommand=sb.set); sb.grid(row=1, column=1, sticky='ns', pady=(4, 0))
        hs = ttk.Scrollbar(page, orient='horizontal', command=self.ctree.xview); hs.grid(row=2, column=0, sticky='ew'); hs.grid_remove()
        self.cfit = ColumnFitter(self.ctree, COMMENT_FIT, hs, arrow=0)
        self.ctree.bind('<Control-a>', lambda _e: (self.ctree.selection_set(self.ctree.get_children()), 'break')[1])
        self.ctree.bind('<<TreeviewSelect>>', lambda _e: self._update_buttons())
        self._set_comment_columns(False)

    def _set_comment_columns(self, with_video):
        cols = ['author', 'text', 'mood', 'likes', 'seen', 'when'] + (['video'] if with_video else [])
        self.ctree.configure(displaycolumns=cols)

    def _build_history(self):
        page = ttk.Frame(self.nb, padding=(0, 6, 0, 0)); page.columnconfigure(0, weight=1); page.rowconfigure(1, weight=1)
        self.nb.add(page, text='  Verlauf  ')
        bar = ttk.Frame(page); bar.grid(row=0, column=0, sticky='ew')
        self.metric = tk.StringVar(value='views')
        for key, label, _col in METRICS:
            ttk.Radiobutton(bar, text=pad_label(label), value=key, variable=self.metric, command=self._show_history).pack(side='left', padx=(0, 12))
        ttk.Radiobutton(bar, text=pad_label('Stimmung (KI)'), value='mood', variable=self.metric, command=self._show_history).pack(side='left', padx=(0, 12))
        self.chart = LineChart(page, height=110)
        self.chart.grid(row=1, column=0, sticky='nsew', pady=(4, 0))
        self.mood_chart = SentimentChart(page, height=110)
        self.mood_chart.grid(row=1, column=0, sticky='nsew', pady=(4, 0))
        self.mood_chart.grid_remove()

    def _mood_filter_value(self):
        return dict(logic.MOOD_FILTERS).get(self.mood_filter.get())

    def _show_comments(self):
        self.ctree.delete(*self.ctree.get_children())
        self.comment_rows = {}
        mode = self.mode.get()
        self._set_comment_columns(mode == 'commenter')
        if mode == 'creator':
            self.comment_info.configure(text='Kommentare siehst du in den Ansichten „Videos“ und „Kommentarschreiber“.', foreground=COLORS['muted'])
            self._update_buttons()
            return
        try:
            since = logic.parse_since(self.since.get())
        except ValueError as error:
            self.comment_info.configure(text=str(error), foreground=COLORS['err']); return
        mood = self._mood_filter_value()
        group = self.view if (isinstance(self.view, int) and self.members_only.get()) else None
        db = core.db_path()
        row = None
        try:
            if mode == 'commenter':
                refs = self._selected_people()
                if not refs:
                    self.comment_info.configure(text='Wähle Kommentarschreiber, um ihre Kommentare zu sehen.', foreground=COLORS['muted'])
                    self._update_buttons()
                    return
                comments = logic.query_person_comments(db, [(p['platform'], p['author_key']) for p in refs], since, limit=2000, mood=mood)
                with_video = True
            else:
                sel = self._selected_uids()
                group_comments = (not sel and isinstance(self.view, int)
                                  and (self.group_stats.get(self.view) or {}).get('commenters'))
                if len(sel) == 1:
                    row = self.video_rows[sel[0]]
                    comments = logic.query_comments(db, row['platform'], row['video_id'], since, limit=2000, mood=mood, group=group)
                    with_video = False
                elif group_comments:
                    comments = logic.query_group_comments(db, self.view, since, limit=2000, mood=mood)
                    with_video = True
                else:
                    self.comment_info.configure(text='Wähle ein Video, um seine Kommentare zu sehen.' if not sel
                                                else f'{len(sel)} Videos gewählt – wähle eines für die Kommentare.', foreground=COLORS['muted'])
                    self._update_buttons()
                    return
        except Exception as error:
            self.comment_info.configure(text='Kommentare nicht lesbar', foreground=COLORS['err'])
            self.app.log(f'Kommentare: {logic.friendly_error(error)}', 'ERROR'); return
        self._set_comment_columns(with_video)
        try:
            logic.mark_new_authors(db, comments, self._new_since(db))
        except Exception:   # reine Zusatzangabe: nie die Kommentarliste stören
            pass
        for i, c in enumerate(comments):
            iid = f'k{i}'
            self.comment_rows[iid] = c
            video = ''
            if with_video:
                video = ' '.join(f'{c.get("video_author") or ""}: {c.get("video_caption") or ""}'.split())[:60]
            self.ctree.insert('', 'end', iid=iid, tags=(MOOD_TAGS.get(c.get('mood'), ''),) if c.get('mood') else (), values=(
                (c['author'] or '') + ('  ★ neu' if c.get('author_new') else ''), ' '.join((c['text'] or '').split()), logic.mood_text(c.get('mood')),
                logic.fmt_number(c['like_count']), logic.local_stamp(c['first_seen_at']), logic.short_date(c['created_at']), video))
        self.cfit.refit()
        filt = f' · Filter: {self.mood_filter.get()}' if mood else ''
        if comments:
            text = core.plural(len(comments), 'Kommentar', 'Kommentare') + (' – neu seit dem gewählten Zeitpunkt' if since else '') + filt
            if mode == 'commenter':
                text += ' der gewählten Kommentarschreiber'
            elif with_video:
                text += f' der Kommentarschreiber der Gruppe „{(self._selected_group() or {}).get("name", "")}“ (über alle Videos)'
            elif group is not None:
                text += ' von Kommentarschreibern der Gruppe'
        elif mood:
            text = 'Keine Kommentare mit diesem Filter.' + (' Die Stimmung bestimmst du mit „Stimmung bestimmen …“.' if mood != 'offen' else '')
        elif since:
            text = 'Keine neuen Kommentare seit diesem Zeitpunkt.'
        elif row is not None and row['saved_comments'] == 0:
            text = 'Zu diesem Video sind keine Kommentare gespeichert (beim Abrufen die Zahl der Kommentare > 0 wählen).'
        else:
            text = 'Keine Kommentare.'
        self.comment_info.configure(text=text, foreground=COLORS['muted'])
        self._update_buttons()

    def _mood_scope(self):
        """(Umfang, Beschriftung) passend zur Ansicht und Auswahl; Umfang None = nicht anzeigen."""
        mode = self.mode.get()
        if mode == 'commenter':
            refs = self._selected_people()
            if refs:
                return {'people': [(p['platform'], p['author_key']) for p in refs]}, core.plural(len(refs), 'Kommentarschreiber', 'Kommentarschreiber') + ' gewählt'
            return {}, 'alle Kommentare'
        if mode == 'creator':
            return None, ''
        sel = self._selected_uids()
        if sel:
            return {'videos': sel}, core.plural(len(sel), 'Video', 'Videos') + ' gewählt'
        everything = self.view == logic.ALL and not (self.search.get() or '').strip() and not self._platform_filter()
        if everything:
            return {}, 'alle Kommentare'
        group = self._selected_group()
        label = f'Gruppe „{group["name"]}“' if group else ('Videos ohne Gruppe' if self.view == logic.UNGROUPED else 'Videos in der Liste')
        return {'videos': list(self.video_rows)}, f'{label} ({core.plural(len(self.video_rows), "Video", "Videos")})'

    def _update_mood_bar(self):
        scope, caption = self._mood_scope()
        if scope is None:
            return
        try:
            dist = logic.mood_distribution(core.db_path(), scope)
        except Exception as error:
            self.mood_bar.show(None, caption)
            self.app.log(f'Stimmung: {logic.friendly_error(error)}', 'ERROR')
            return
        self.mood_bar.show(dist, caption)

    def _show_history(self):
        if self.metric.get() == 'mood':
            self._show_mood_history()
            return
        self.mood_chart.grid_remove(); self.chart.grid()
        row = self._single()
        if row is None:
            self.chart.show([], '', 'Wähle ein Video, um den Verlauf seiner Zahlen zu sehen.'); return
        column = next(c for k, _l, c in METRICS if k == self.metric.get())
        label = next(l for k, l, _c in METRICS if k == self.metric.get())
        try:
            hist = logic.history(core.db_path(), row['uid'])
        except Exception as error:
            self.chart.show([], '', 'Verlauf nicht lesbar'); self.app.log(f'Verlauf: {logic.friendly_error(error)}', 'ERROR'); return
        values = [(h['captured_at'], h[column]) for h in hist]
        self.chart.show(values, label, 'Noch kein Verlauf.\nMit „Verlauf verfolgen …“ ruft der Hintergrunddienst das Video regelmäßig ab – '
                                       'jeder Abruf ergibt einen Punkt.')

    def _show_mood_history(self):
        self.chart.grid_remove(); self.mood_chart.grid()
        scope, _caption = self._mood_scope()
        if scope is None:
            self.mood_chart.show([], 'Wähle die Ansicht „Videos“ oder „Kommentarschreiber“, um den Stimmungsverlauf zu sehen.'); return
        try:
            series = logic.mood_series(core.db_path(), scope)
            if len(series) > 60:
                series = logic.mood_series(core.db_path(), scope, 'week')
        except Exception as error:
            self.mood_chart.show([], 'Stimmungsverlauf nicht lesbar'); self.app.log(f'Stimmungsverlauf: {logic.friendly_error(error)}', 'ERROR'); return
        self.mood_chart.show(series, 'Noch keine Stimmung bestimmt.\nKlicke oben auf „Stimmung bestimmen …“ – dann erscheint hier der Verlauf '
                                     '(KI-Einschätzung, Anteil positiv und negativ je Tag).')
