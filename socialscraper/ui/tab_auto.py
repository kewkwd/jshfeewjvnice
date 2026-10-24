"""Tab "Automatik": Hintergrunddienst, Ziele, Laufprotokoll und Feed neuer Kommentare."""
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from .. import aiconfig
from .. import autologic as al
from .. import chanlogic, channels
from .. import guicore as core
from .. import proxylogic, repo
from . import common
from .common import COLORS, FONT_B, TITLE, ModeBanner, Tab
from .forms import ServicePanel, friendly, run_async
from .widgets import ColumnFitter, FlowBar, ask_form, bind_wrap
from .target_dialog import TargetDialog
from . import channel_dialog
from .channel_detail import ChannelDetail

REFRESH_MS = 5000
LOW_TAB = 580      # Tabs, die niedriger sind (Fenster 860 x 620), sparen Platz: weniger Erklärtext, eine Zeile für den Dienst
SOURCE_TEXT = {'manual': 'von Hand', 'daemon': 'Dienst'}
# Spaltenbreiten: Gewicht 0 = so breit wie der Inhalt (nie abgeschnitten), sonst flexibel (Mindestbreite, Gewicht)
TARGET_FIT = {'kind': (70, 0), 'platform': (66, 0), 'value': (110, 4), 'group': (50, 1), 'account': (60, 1), 'interval': (70, 0),
              'last': (80, 0), 'status': (60, 0), 'next': (90, 0), 'active': (44, 0)}
RUN_FIT = {'time': (70, 0), 'target': (260, 6), 'source': (60, 0), 'status': (60, 0), 'videos': (90, 0), 'comments': (90, 0),
           'authors': (80, 0), 'error': (60, 1), 'accounts': (60, 1)}
FEED_FIT = {'time': (70, 0), 'platform': (66, 0), 'author': (70, 1), 'text': (140, 4), 'video': (70, 1)}
# Kanalliste: Zahlen und Fortschritt behalten ihre Breite, der Zustand darf schmal werden (voller Text als Hinweis beim Darüberfahren)
CHANNEL_FIT = {'platform': (66, 0), 'name': (90, 1), 'bar': (160, 0), 'count': (110, 0), 'state': (100, 2), 'next': (70, 0)}


class AutoTab(Tab):
    def build(self):
        self.snapshot = {'targets': [], 'runs': [], 'comments': [], 'groups': [], 'accounts': [], 'channels': [], 'error': None}
        self.feed_urls, self._generation, self._after_id = {}, 0, None
        self.loading = False
        self._again = False
        self.feed_hours = 24
        self.last_run_message = ''
        self.configure(padding=(10, 10, 10, 6))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.service = ServicePanel(self, self.root, targets_fn=lambda: self.snapshot['targets'],
                                    on_change=lambda ok, msg: (self.app.set_status(msg, 'ok' if ok else 'err'), self.reload()))
        self.service.grid(row=0, column=0, sticky='ew')
        self.service.configure(padding=(10, 6))

        self.pane = ttk.PanedWindow(self, orient='vertical')
        self.pane.grid(row=1, column=0, sticky='nsew', pady=(8, 0))
        self.mode_banner = ModeBanner(self, self.root, app=self.app)
        self.mode_banner.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        self.mode_banner.grid_remove()
        self._build_targets()
        self._build_lower()
        self._sash_by_user = False
        self.pane.bind('<ButtonRelease-1>', lambda _e: setattr(self, '_sash_by_user', True), add='+')
        self.pane.bind('<Configure>', self._fit_sash, add='+')
        self._build_ai_switch()
        self.bind('<Destroy>', self._destroyed, add='+')
        self._after_id = self.after(REFRESH_MS, self._auto)

    def root(self):
        return getattr(self.app, 'root', None) or common.ROOT

    def db_path(self):
        return al.resolve_db(self.root())

    # ---------- Aufbau ----------

    def _build_targets(self):
        box = ttk.Frame(self.pane)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(2, weight=1)
        self.pane.add(box, weight=3)
        bar = ttk.Frame(box)
        bar.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 4))
        self.add_btn = ttk.Button(bar, text='+ Kanal hinzufügen', command=self.add_target, style='Primary.TButton')
        self.edit_btn = ttk.Button(bar, text='Bearbeiten …', command=self.edit_target)
        self.toggle_btn = ttk.Button(bar, text='Pausieren', command=self.toggle_target)
        self.run_btn = ttk.Button(bar, text='Jetzt ausführen', command=self.run_now)
        self.delete_btn = ttk.Button(bar, text='Entfernen', command=self.delete_target, style='Danger.TButton')
        for b in (self.add_btn, self.edit_btn, self.toggle_btn, self.run_btn, self.delete_btn):
            b.pack(side='left', padx=(0, 6))
        self.result_label = ttk.Label(bar, text='', foreground=COLORS['muted'])
        self.result_label.pack(side='left', padx=(10, 0))
        self.explain = explain = ttk.Label(box, text=al.EXPLAIN, foreground=COLORS['muted'], wraplength=820, justify='left')
        explain.grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 4))
        bind_wrap(explain, box, margin=12)
        cols = ('kind', 'platform', 'value', 'group', 'account', 'interval', 'last', 'status', 'next', 'active')
        heads = ('Art', 'Plattform', 'Wert', 'Gruppe', 'Konto', 'Intervall', 'Letzter Lauf', 'Status', 'Nächster Lauf', 'Aktiv')
        widths = (76, 68, 150, 72, 72, 76, 84, 72, 92, 46)
        self.tree = ttk.Treeview(box, columns=cols, show='headings', selectmode='browse', height=5)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, minwidth=40, anchor='w' if c in ('value', 'group', 'account') else 'center', stretch=(c == 'value'))
        self.tree.grid(row=2, column=0, sticky='nsew')
        sb = ttk.Scrollbar(box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=2, column=1, sticky='ns')
        hs = ttk.Scrollbar(box, orient='horizontal', command=self.tree.xview)
        hs.grid(row=3, column=0, sticky='ew'); hs.grid_remove()
        self.tfit = ColumnFitter(self.tree, TARGET_FIT, hs, arrow=0)
        self.tree.tag_configure('failed', foreground=COLORS['err'])
        self.tree.tag_configure('partial', foreground=COLORS['warn'])
        self.tree.tag_configure('paused', foreground=COLORS['muted'])
        self.tree.bind('<<TreeviewSelect>>', lambda _e: self._update_buttons())
        self.tree.bind('<Double-1>', lambda _e: self.edit_target())
        self.tree.bind('<Return>', lambda _e: self.edit_target())
        self.tree.bind('<Delete>', lambda _e: self.delete_target())
        self.empty_label = ttk.Label(self.tree, text='Noch nichts automatisiert.\nFüge einen Hashtag, ein Profil oder ein Video hinzu –\n'
                                                    'klicke dafür oben auf „Hinzufügen …“.',
                                     foreground=COLORS['muted'], justify='center', background='white')
        self.empty_label.place(relx=0.5, rely=0.55, anchor='center')
        self.app.register_busy(self.run_btn)

    def _fit_sash(self, event=None):
        """Bei niedrigem Fenster nicht die Liste der Ziele auf null quetschen: unten Platz für die Kanäle, oben für die Ziele.
        Zieht der Nutzer den Trenner selbst, bleibt seine Wahl."""
        if self._sash_by_user:
            return
        try:
            height = self.pane.winfo_height()
            outer = self.winfo_height()             # die Höhe des ganzen Tabs (nicht die der Listen): sonst schaukelt sich das Layout auf
            low = 50 < outer < LOW_TAB
            self.service.compact(low)               # im niedrigen Fenster nur eine Zeile für den Dienst
            if low and self.explain.winfo_manager():
                self.explain.grid_remove()          # die Erklärzeile ist im niedrigen Fenster entbehrlich
            elif not low and not self.explain.winfo_manager():
                self.explain.grid()
            if height > 200 and self.pane.sashpos(0) < 150:
                self.pane.sashpos(0, int(height * 0.4))
        except tk.TclError:
            pass

    def _build_lower(self):
        self.nb = ttk.Notebook(self.pane)
        self.pane.add(self.nb, weight=2)
        self._build_channels()
        runs = ttk.Frame(self.nb, padding=4)
        runs.columnconfigure(0, weight=1)
        runs.rowconfigure(0, weight=1)
        self.nb.add(runs, text='  Laufprotokoll  ')
        cols = ('time', 'target', 'source', 'status', 'videos', 'comments', 'authors', 'error', 'accounts')
        heads = ('Zeit', 'Ziel', 'Quelle', 'Status', 'Videos neu/gesehen', 'Neue Kommentare', 'Neue Autoren', 'Fehler', 'Konten')
        widths = (82, 210, 70, 80, 120, 110, 90, 200, 120)
        self.runs_tree = ttk.Treeview(runs, columns=cols, show='headings', selectmode='browse', height=4)
        for c, h, w in zip(cols, heads, widths):
            self.runs_tree.heading(c, text=h)
            self.runs_tree.column(c, width=w, minwidth=40, anchor='w' if c in ('target', 'error', 'accounts') else 'center', stretch=(c == 'error'))
        self.runs_tree.grid(row=0, column=0, sticky='nsew')
        hs = ttk.Scrollbar(runs, orient='horizontal', command=self.runs_tree.xview)
        hs.grid(row=1, column=0, sticky='ew'); hs.grid_remove()
        self.rfit = ColumnFitter(self.runs_tree, RUN_FIT, hs, arrow=0)
        self.runs_tree.bind('<Double-1>', lambda _e: self.show_run_details())
        self.runs_tree.bind('<Return>', lambda _e: self.show_run_details())
        sb = ttk.Scrollbar(runs, command=self.runs_tree.yview)
        self.runs_tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky='ns')
        for tag, color in (('ok', 'ok'), ('partial', 'warn'), ('failed', 'err'), ('running', 'warn'), ('cancelled', 'muted')):
            self.runs_tree.tag_configure(tag, foreground=COLORS[color])
        self.runs_empty = ttk.Label(self.runs_tree, text='Noch keine Läufe. Sie erscheinen hier, sobald ein Ziel abgerufen wurde.',
                                    foreground=COLORS['muted'], background='white')
        self.runs_empty.place(relx=0.5, rely=0.5, anchor='center')

        feed = ttk.Frame(self.nb, padding=4)
        feed.columnconfigure(0, weight=1)
        feed.rowconfigure(1, weight=1)
        self.nb.add(feed, text='  Neue Kommentare  ')
        top = ttk.Frame(feed)
        top.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 4))
        ttk.Label(top, text='Zeitraum:').pack(side='left')
        self.range_var = tk.StringVar(value=al.FEED_RANGES[1][0])
        cb = ttk.Combobox(top, textvariable=self.range_var, values=[t for t, _h in al.FEED_RANGES], state='readonly', width=18)
        cb.pack(side='left', padx=8)
        cb.bind('<<ComboboxSelected>>', lambda _e: self._range_changed())
        self.feed_count = ttk.Label(top, text='', foreground=COLORS['muted'])
        self.feed_count.pack(side='left')
        ttk.Label(top, text='Doppelklick öffnet das Video', foreground=COLORS['muted']).pack(side='right')
        cols = ('time', 'platform', 'author', 'text', 'video')
        heads = ('Gesehen', 'Plattform', 'Autor', 'Kommentar', 'Video von')
        widths = (82, 70, 110, 380, 110)
        self.feed_tree = ttk.Treeview(feed, columns=cols, show='headings', selectmode='browse', height=4)
        for c, h, w in zip(cols, heads, widths):
            self.feed_tree.heading(c, text=h)
            self.feed_tree.column(c, width=w, minwidth=40, anchor='w' if c in ('text', 'author', 'video') else 'center', stretch=(c == 'text'))
        self.feed_tree.grid(row=1, column=0, sticky='nsew')
        hs = ttk.Scrollbar(feed, orient='horizontal', command=self.feed_tree.xview)
        hs.grid(row=2, column=0, sticky='ew'); hs.grid_remove()
        self.ffit = ColumnFitter(self.feed_tree, FEED_FIT, hs, arrow=0)
        sb2 = ttk.Scrollbar(feed, command=self.feed_tree.yview)
        self.feed_tree.configure(yscrollcommand=sb2.set)
        sb2.grid(row=1, column=1, sticky='ns')
        self.feed_tree.bind('<Double-1>', lambda _e: self.open_feed_video())
        self.feed_tree.bind('<Return>', lambda _e: self.open_feed_video())
        self.feed_empty = ttk.Label(self.feed_tree, text='', foreground=COLORS['muted'], background='white', justify='center')
        self.feed_empty.place(relx=0.5, rely=0.5, anchor='center')

    def _build_channels(self):
        """Seite „Kanäle“: ganze Kanäle mit Fortschritt, Pause und Warteschlange."""
        page = ttk.Frame(self.nb, padding=4)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        self.chan_page = page
        self.nb.add(page, text='  Kanäle  ')
        bar = FlowBar(page)
        bar.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 4))
        self.chan_btn = {}
        for key, text, fn in (('add', 'Kanal hinzufügen …', self.add_channel), ('detail', 'Einzelheiten …', self.channel_details),
                              ('toggle', 'Pausieren', self.channel_toggle), ('now', 'Jetzt weiter', lambda: self.channel_action('now')),
                              ('requeue', 'Fehlgeschlagene erneut', lambda: self.channel_action('requeue')),
                              ('pace', 'Tempo ändern …', self.channel_pace), ('remove', 'Entfernen', self.channel_remove)):
            self.chan_btn[key] = ttk.Button(bar, text=text, command=fn)
            bar.add(self.chan_btn[key])
        cols = ('platform', 'name', 'bar', 'count', 'state', 'next')
        self.ctree = ttk.Treeview(page, columns=cols, show='headings', selectmode='browse', height=4)
        for c, h, w, a in (('platform', 'Plattform', 70, 'center'), ('name', 'Kanal', 130, 'w'), ('bar', 'Fortschritt', 170, 'w'),
                           ('count', 'Videos', 150, 'w'), ('state', 'Zustand', 150, 'w'), ('next', 'Nächster Schritt', 110, 'w')):
            self.ctree.heading(c, text=h)
            self.ctree.column(c, width=w, minwidth=40, anchor=a, stretch=(c == 'state'))
        self.ctree.grid(row=1, column=0, sticky='nsew')
        sb = ttk.Scrollbar(page, command=self.ctree.yview)
        self.ctree.configure(yscrollcommand=sb.set)
        sb.grid(row=1, column=1, sticky='ns')
        hs = ttk.Scrollbar(page, orient='horizontal', command=self.ctree.xview)
        hs.grid(row=2, column=0, sticky='ew')
        hs.grid_remove()
        self.cfit = ColumnFitter(self.ctree, CHANNEL_FIT, hs, arrow=0)
        for tag, color in (('ok', 'ok'), ('warn', 'warn'), ('err', 'err'), ('muted', 'muted')):
            self.ctree.tag_configure(tag, foreground=COLORS[color])
        self.ctree.bind('<<TreeviewSelect>>', lambda _e: self._update_buttons())
        self.ctree.bind('<Double-1>', lambda _e: self.channel_details())
        self.chan_empty = ttk.Label(self.ctree, text='Noch kein Kanal.\nKlicke auf „Kanal hinzufügen …“ und füge einen Kanal-Link ein –\n'
                                                   'das Programm holt dann alle Videos in kleinen Paketen.',
                                    foreground=COLORS['muted'], justify='center', background='white')
        self.chan_empty.place(relx=0.5, rely=0.5, anchor='center')

    def _build_ai_switch(self):
        """Schalter „Neue Kommentare nach jedem Lauf automatisch einstufen“ (KI-Einschätzung; Standard aus, weil es Geld kostet)."""
        box = ttk.Frame(self)
        box.grid(row=3, column=0, sticky='ew', pady=(6, 0))
        self.ai_auto = tk.BooleanVar(value=False)
        self.ai_auto_cb = ttk.Checkbutton(box, text='Neue Kommentare nach jedem Lauf automatisch einstufen (KI, kostet Geld)  ',
                                          variable=self.ai_auto, command=self._ai_auto_toggled)
        self.ai_auto_cb.pack(side='left')
        self.ai_auto_note = ttk.Label(box, text='', foreground=COLORS['muted'])
        self.ai_auto_note.pack(side='left', padx=8)
        self.ai_fix_btn = ttk.Button(box, text='Schlüssel eintragen', command=lambda: self._run_action({'id': 'goto_ai'}))
        self._load_ai_switch()

    def _run_action(self, action):
        runner = getattr(self.app, 'run_action', None)
        if runner is not None:
            runner(action)

    def _load_ai_switch(self):
        cfg = aiconfig.load(self.root())
        self.ai_auto.set(bool(cfg['auto_after_runs']))
        self.ai_fix_btn.pack_forget()
        if cfg['auto_after_runs'] and not aiconfig.has_key(cfg):
            self.ai_auto_note.configure(text='Es fehlt noch ein API-Schlüssel (Einstellungen → KI / Stimmung).', foreground=COLORS['warn'])
            self.ai_fix_btn.pack(side='left', before=self.ai_auto_note)
        elif cfg['auto_after_runs']:
            self.ai_auto_note.configure(text=f'An: Der Dienst stuft nach einem Lauf nur neue Kommentare ein (höchstens {cfg["max_per_run"]} pro Lauf).',
                                        foreground=COLORS['muted'])
        else:
            self.ai_auto_note.configure(text='Aus.', foreground=COLORS['muted'])

    def _ai_auto_toggled(self):
        want = bool(self.ai_auto.get())
        try:
            aiconfig.update(self.root(), auto_after_runs=want)
        except ValueError as error:
            messagebox.showwarning(TITLE, f'Der Schalter konnte nicht gespeichert werden.\n\n{error}')
        else:
            self.app.log('Automatische Stimmung (KI-Einschätzung) ' + ('eingeschaltet.' if want else 'ausgeschaltet.'), 'OK')
            self.app.set_status('Automatische Stimmung ' + ('an.' if want else 'aus.'), 'ok')
        self._load_ai_switch()

    # ---------- Laden ----------

    def refresh(self):
        self.mode_banner.update_mode()
        self.service.refresh()
        self._load_ai_switch()
        self.reload()

    def _destroyed(self, event):
        if event.widget is self and self._after_id:
            try: self.after_cancel(self._after_id)
            except tk.TclError: pass

    def _auto(self):
        try:
            if self.winfo_ismapped():
                self.service.refresh()
                self.reload()
            if self._after_id:
                self.after_cancel(self._after_id)
            self._after_id = self.after(REFRESH_MS, self._auto)
        except tk.TclError:
            pass

    def _range_changed(self):
        self.feed_hours = dict(al.FEED_RANGES)[self.range_var.get()]
        self.reload()

    def reload(self):
        """Daten im Hintergrund lesen; die Anzeige wird danach im Fenster-Thread aktualisiert."""
        if self.loading:
            self._again = True
            return
        self.loading = True
        self._again = False
        self._generation += 1
        gen, db, hours = self._generation, self.db_path(), self.feed_hours

        def done(result, error):
            self.loading = False
            if error is None and gen == self._generation:
                self.snapshot = result
                self._show()
            if self._again:
                self.reload()
        root = self.root()
        run_async(self, lambda: {**al.load_snapshot(db, hours), 'proxy_mode': proxylogic.current_mode(root),
                                 'channels': chanlogic.load(db)}, done)

    # ---------- Anzeige ----------

    def _selected_id(self):
        sel = self.tree.selection()
        return int(sel[0][1:]) if sel else None

    def _target(self, target_id=None):
        target_id = target_id or self._selected_id()
        return next((t for t in self.snapshot['targets'] if t['id'] == target_id), None)

    def _show(self):
        snap = self.snapshot
        now = None
        selected = self._selected_id()
        mode = snap.get('proxy_mode', 'aus')
        self.mode_banner.update_mode(mode)
        top = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        for t in snap['targets']:
            if t['kind'] == 'channel':      # Kanäle haben ihre eigene Seite
                continue
            status = t.get('last_status')
            tags = [status] if status in ('partial', 'failed') else []
            if not t['enabled']:
                tags = ['paused']
            blocked = proxylogic.is_anonymous(mode) and proxylogic.target_problem(mode, t)
            if blocked and t['enabled']:
                tags = ['failed']
            account = ('Rotation' if t.get('account_mode') == 'rotate' else None) or t.get('account_label') or (
                'Standard' if t['platform'] != 'youtube' else '–')
            value = al.value_text(t['kind'], t['platform'], t['value'])
            self.tree.insert('', 'end', iid=f"t{t['id']}", tags=tags, values=(
                al.KIND_LABELS[t['kind']], al.PLATFORM_NAMES.get(t['platform'], t['platform']), value,
                t.get('group_name') or '–', account, al.interval_label(t['interval_min']),
                al.local_text(t.get('last_run_at')) if t.get('last_run_at') else 'noch nie',
                proxylogic.BLOCKED_TEXT if blocked else (al.STATUS_TEXT.get(status, '–') if status else '–'), al.next_text(t, now), 'ja' if t['enabled'] else 'nein'))
        if selected and self.tree.exists(f't{selected}'):
            self.tree.selection_set(f't{selected}')
        self.tree.yview_moveto(top)
        self.tfit.refit()
        if [t for t in snap['targets'] if t['kind'] != 'channel']:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.55, anchor='center')
        self._show_channels(snap.get('channels') or [])

        self.runs_tree.delete(*self.runs_tree.get_children())
        for r in snap['runs']:
            if r.get('target_kind'):
                title = al.target_title({'kind': r['target_kind'], 'platform': r['target_platform'], 'value': r['target_value']})
            else:
                title = al.deleted_title(r.get('label'))
            status = r['status']
            err = (r.get('error') or '').replace('\n', ' ')
            self.runs_tree.insert('', 'end', iid=f"r{r['id']}", tags=[status], values=(
                al.local_text(r.get('started_at')), title, SOURCE_TEXT.get(r['source'], r['source']), al.STATUS_TEXT.get(status, status),
                f"{r.get('videos_new') or 0} / {r.get('videos_seen') or 0}", r.get('comments_new') or 0, r.get('authors_new') or 0, err,
                ', '.join(repo.accounts_used_list(r.get('accounts_used')))))
        self.rfit.refit()
        if snap['runs']:
            self.runs_empty.place_forget()
        else:
            self.runs_empty.place(relx=0.5, rely=0.5, anchor='center')

        self.feed_tree.delete(*self.feed_tree.get_children())
        self.feed_urls = {}
        for i, c in enumerate(snap['comments']):
            text = (c.get('text') or '').replace('\n', ' ')
            self.feed_tree.insert('', 'end', iid=f'c{i}', values=(
                al.local_text(c.get('first_seen_at')), al.PLATFORM_NAMES.get(c.get('platform'), c.get('platform') or ''),
                (c.get('author') or '–') + ('  ★ neu' if c.get('author_new') else ''), text[:300], c.get('video_author') or '–'))
            self.feed_urls[f'c{i}'] = c.get('video_url') or ''
        self.ffit.refit()
        n = len(snap['comments'])
        self.feed_count.configure(text=core.plural(n, 'neuer Kommentar', 'neue Kommentare'))
        if n:
            self.feed_empty.place_forget()
        else:
            self.feed_empty.configure(text='Keine neuen Kommentare in diesem Zeitraum.\nSie erscheinen, sobald die Automatik unter\n'
                                           'bekannten Videos neue findet.')
            self.feed_empty.place(relx=0.5, rely=0.5, anchor='center')
        if snap.get('error'):
            self.app.set_status(snap['error'], 'warn')
        self._update_buttons()
        self.service.refresh()

    def focus_item(self, spec):
        """Sprung von der Übersicht: Seite „Kanäle“ zeigen und den Kanal `channel_id` markieren."""
        cid = (spec or {}).get('channel_id')
        if cid is None:
            return False
        try:
            self.nb.select(self.chan_page)
        except tk.TclError:
            pass
        self._pending_channel = f'c{cid}'
        if self.ctree.exists(self._pending_channel):
            self._show_channels(self.snapshot.get('channels', []))
        else:
            self.reload()          # die Liste kennt den Kanal noch nicht (zum Beispiel gerade angelegt); sie wählt ihn nach dem Laden aus
        self.ctree.focus_set()
        return True

    def _selected_channel_id(self):
        sel = self.ctree.selection()
        return int(sel[0][1:]) if sel else None

    def _channel(self):
        cid = self._selected_channel_id()
        return next((c for c in self.snapshot.get('channels', []) if c['id'] == cid), None)

    def _show_channels(self, rows):
        selected = self._selected_channel_id()
        top = self.ctree.yview()[0]
        self.ctree.delete(*self.ctree.get_children())
        for c in rows:
            pr = c['progress']
            state = c.get('backfill_state')
            tag = ('muted' if not c['enabled'] and state != 'done' else 'warn' if state == 'paused' else
                   'ok' if state in ('done', 'watching') else '')
            if pr['failed'] and not tag:
                tag = 'warn'
            percent = int(100 * pr['finished'] / pr['total']) if pr['total'] else 0
            name = c.get('display_name') or channels.value_display(c['platform'], c['value'])
            count, state_txt, when = channels.progress_parts(c)
            self.ctree.insert('', 'end', iid=f"c{c['id']}", tags=(tag,) if tag else (), values=(
                al.PLATFORM_NAMES.get(c['platform'], c['platform']), name,
                f"{channels.bar(pr['finished'], pr['total'], 14)} {percent} %" if pr['total'] else 'noch keine Liste',
                count, state_txt, when))
        if selected and self.ctree.exists(f'c{selected}'):
            self.ctree.selection_set(f'c{selected}')
        self.ctree.yview_moveto(top)
        wanted = getattr(self, '_pending_channel', None)
        if wanted and self.ctree.exists(wanted):      # Sprung von der Übersicht: den Kanal markieren
            self._pending_channel = None
            self.ctree.selection_set(wanted)
            self.ctree.focus(wanted)
            self.ctree.see(wanted)
        self.cfit.refit()
        if rows:
            self.chan_empty.place_forget()
        else:
            self.chan_empty.place(relx=0.5, rely=0.5, anchor='center')
        try:
            self.nb.tab(self.chan_page, text=f'  Kanäle ({len(rows)})  ' if rows else '  Kanäle  ')
        except tk.TclError:
            pass

    def _update_buttons(self):
        c = self._channel() if hasattr(self, 'ctree') else None
        if hasattr(self, 'chan_btn'):
            for key in ('detail', 'toggle', 'now', 'requeue', 'pace', 'remove'):
                self.chan_btn[key].configure(state='normal' if c is not None else 'disabled')
            self.chan_btn['requeue'].configure(state='normal' if c is not None and c['progress']['failed'] else 'disabled')
            self.chan_btn['toggle'].configure(text='Fortsetzen' if c is not None and (not c['enabled'] or c.get('backfill_state') == 'paused')
                                              else 'Pausieren')
        t = self._target()
        has = t is not None
        for b in (self.edit_btn, self.toggle_btn, self.run_btn, self.delete_btn):
            b.configure(state='normal' if has else 'disabled')
        self.toggle_btn.configure(text='Aktivieren' if has and not t['enabled'] else 'Pausieren')
        if self.app.job is not None:
            self.run_btn.configure(state='disabled')

    # ---------- Aktionen ----------

    def _warn(self, text):
        messagebox.showwarning(TITLE, text)

    def _login_hint(self, kind, platform):
        from ..discovery import needs_login
        if platform == 'instagram' and core.account_status('instagram', self.root())[0] != 'saved':
            return 'Hinweis: Instagram funktioniert nur mit Anmeldung. Melde dich im Tab „Konten“ an.'
        if platform == 'x':
            if core.account_status('x', self.root())[0] != 'saved':
                return 'Hinweis: X funktioniert nur mit Anmeldung. Melde dich im Tab „Konten“ an, sonst schlägt der Lauf fehl.'
            return 'Hinweis: Das Abrufen mit einem X-Konto verstößt gegen die Regeln von X. Nimm am besten ein Zweitkonto.'
        if platform == 'reddit':
            return 'Hinweis: Reddit wird im Browser gelesen. Manche Netze sperrt Reddit; dann hilft ein anderes Netz.'
        if needs_login(kind, platform) and core.account_status(platform, self.root())[0] != 'saved':
            return 'Hinweis: TikTok-Hashtags und -Suche brauchen eine Anmeldung. Melde dich im Tab „Konten“ an, sonst schlägt der Lauf fehl.'
        return ''

    def _default_comments(self):
        try:
            return min(int(core.read_settings(self.root()).max_comments), 5000)   # 0 ist eine bewusste Wahl („nur Videodaten“), kein Fehler
        except Exception:
            return 20

    def add_target(self, modal=True):
        db = self.db_path()

        def submit(clean, account_id, group_id):
            al.add_target(db, clean, account_id, group_id)
        dialog = TargetDialog(self, submit, self.snapshot['accounts'], self.snapshot['groups'], db,
                              default_comments=min(self._default_comments(), 100), hint_fn=self._login_hint, modal=modal)
        if dialog.result:
            self.app.log(f'Automatik: Ziel hinzugefügt – {al.target_title(dialog.result)}', 'OK')
            self.reload()
        return dialog

    def add_channel(self, link='', modal=True):
        """Dialog „Kanal hinzufügen“ (auch vom Tab „Abrufen“ aus)."""
        dialog = channel_dialog.open_dialog(self, self.root(), link, modal)
        if dialog.result:
            t = dialog.result
            name = channels.value_display(t['platform'], t['value'])
            self.app.log(f'Kanal hinzugefügt: {name} ({al.PLATFORM_NAMES.get(t["platform"], t["platform"])}) – '
                         f'Tempo {channels.PROFILES[t["pace_profile"]]["name"]}. Der Hintergrunddienst holt die Videos in kleinen Paketen.', 'OK')
            self.app.set_status('Kanal hinzugefügt. Der Hintergrunddienst holt die Videos in kleinen Paketen.', 'ok')
            try:
                self.nb.select(self.chan_page)
            except tk.TclError:
                pass
            self.reload()
        return dialog

    def channel_action(self, name, **kw):
        c = self._channel()
        if c is None:
            return
        done = {}

        def work():
            done['text'] = chanlogic.action(self.db_path(), c['id'], name, **kw)
        if self._guard(work):
            self.app.set_status(done['text'], 'ok')
            self.app.log('Kanal: ' + done['text'], 'OK')

    def channel_toggle(self):
        c = self._channel()
        if c is None:
            return
        paused = (not c['enabled']) or c.get('backfill_state') == 'paused'
        self.channel_action('resume' if paused else 'pause')

    def channel_remove(self):
        c = self._channel()
        if c is None:
            return
        name = c.get('display_name') or channels.value_display(c['platform'], c['value'])
        if messagebox.askyesno(TITLE, f'Kanal „{name}“ wirklich entfernen?\n\nDie Warteschlange wird gelöscht. Bereits gespeicherte '
                                      'Videos und Kommentare bleiben erhalten.'):
            self.channel_action('remove')

    def channel_pace(self):
        c = self._channel()
        if c is None:
            return None
        fields = [{'key': 'profile', 'label': 'Tempo', 'kind': 'choice', 'value': c.get('pace_profile') or 'normal',
                   'choices': [(k, chanlogic.profile_text(k)) for k in chanlogic.PROFILE_ORDER]},
                  {'key': 'quiet', 'label': 'Ruhezeit (von-bis, Uhr)', 'value': c.get('quiet_hours') or '',
                   'hint': 'z. B. 23-7 = nachts nichts abrufen. Leer lassen = keine Ruhezeit.'}]

        def validate(values):
            try:
                from .. import repo as _repo
                _repo.check_quiet_hours(values.get('quiet'))
            except ValueError as error:
                return str(error)
            return None
        values = ask_form(self, 'Tempo ändern', fields, ok_text='Speichern', validator=validate)
        if values is not None:
            self.channel_action('pace', profile=values['profile'], quiet_hours=(values.get('quiet') or '').strip() or None)
        return values

    def channel_details(self, modal=False):
        c = self._channel()
        if c is None:
            return None
        return ChannelDetail(self, self.db_path(), c, on_action=lambda text: self.app.set_status(text, 'ok'))

    def edit_target(self, modal=True):
        t = self._target()
        if t is None:
            return None
        db = self.db_path()

        def submit(clean, account_id, group_id):
            al.update_target(db, t['id'], account_id=account_id, account_mode=clean.get('account_mode'), group_id=group_id,
                             interval_min=clean['interval_min'], max_comments=clean['max_comments'], max_videos=clean['max_videos'])
        dialog = TargetDialog(self, submit, self.snapshot['accounts'], self.snapshot['groups'], db, edit=t,
                              hint_fn=self._login_hint, modal=modal)
        if dialog.result:
            self.reload()
        return dialog

    def _guard(self, work, ok_text=None):
        try:
            work()
        except ValueError as error:
            self._warn(str(error))
            return False
        except Exception as error:  # noqa: BLE001 - nie einen Stacktrace zeigen
            self.app.log(f'Automatik: {type(error).__name__}', 'ERROR')
            self._warn(friendly(error))
            return False
        if ok_text:
            self.app.set_status(ok_text, 'ok')
        self.reload()
        return True

    def toggle_target(self):
        t = self._target()
        if t is None:
            return
        enable = not t['enabled']
        self._guard(lambda: al.update_target(self.db_path(), t['id'], enabled=int(enable)),
                    'Ziel wird wieder abgerufen.' if enable else 'Ziel pausiert.')

    def delete_target(self):
        t = self._target()
        if t is None:
            return
        if not messagebox.askyesno(TITLE, f'„{al.target_title(t)}“ wirklich löschen?\n\nBereits gespeicherte Videos und Kommentare bleiben erhalten.'):
            return
        if self._guard(lambda: al.delete_target(self.db_path(), t['id']), 'Ziel gelöscht.'):
            self.app.log(f'Automatik: Ziel gelöscht – {al.target_title(t)}', 'MUTED')

    def run_now(self):
        t = self._target()
        if t is None:
            return
        status = self.service.last_status or al.service_status(self.root())
        if status.get('running') and t['enabled'] and not status.get('stale'):
            if self._guard(lambda: al.request_run_now(self.db_path(), t['id'])):
                msg = 'Der Hintergrunddienst führt das Ziel in den nächsten Sekunden aus.'
                self.result_label.configure(text=msg, foreground=COLORS['muted'])
                self.app.set_status(msg, 'ok')
            return
        title = al.target_title(t)
        self.result_label.configure(text='')
        self.app.run_steps([(f'Automatik: {title} jetzt abrufen', ['run-target', str(t['id'])])],
                           lambda results, cancelled: self._run_done(title, results, cancelled))

    def _run_done(self, title, results, cancelled):
        if cancelled:
            kind, text = 'warn', 'Abgebrochen.'
        else:
            code, lines = (results[-1][1], results[-1][2]) if results else (1, [])
            kind, text = al.run_target_message(code, lines)
        self.last_run_message = text
        self.result_label.configure(text=text, foreground=COLORS[kind])
        self.app.set_status(text, kind)

    def open_feed_video(self):
        sel = self.feed_tree.selection()
        url = self.feed_urls.get(sel[0], '') if sel else ''
        if core.detect_platform(url):
            webbrowser.open(url)
        elif sel:
            messagebox.showinfo(TITLE, 'Zu diesem Kommentar ist kein Video-Link gespeichert.')

    def show_run_details(self):
        sel = self.runs_tree.selection()
        run = next((r for r in self.snapshot['runs'] if sel and f"r{r['id']}" == sel[0]), None)
        if run is None:
            return
        title = al.target_title({'kind': run['target_kind'], 'platform': run['target_platform'], 'value': run['target_value']}) \
            if run.get('target_kind') else al.deleted_title(run.get('label'))
        lines = [title, f"Gestartet: {al.local_text(run.get('started_at'))}  ·  {al.STATUS_TEXT.get(run['status'], run['status'])}",
                 f"Videos: {run.get('videos_new') or 0} neu, {run.get('videos_seen') or 0} gesehen  ·  neue Kommentare: {run.get('comments_new') or 0}"
                 f"  ·  neue Autoren: {run.get('authors_new') or 0}"]
        used = repo.accounts_used_list(run.get('accounts_used'))
        if used:
            lines.append('Benutzte Konten: ' + ', '.join(used))
        if run.get('error'):
            lines += ['', run['error']]
        messagebox.showinfo(TITLE, chr(10).join(lines))
