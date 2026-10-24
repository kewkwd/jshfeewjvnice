"""Dialog „Kanal hinzufügen“: Link einfügen, Umfang, Tempo, Konto, Gruppe, Ruhezeit – mit ehrlicher Zeitschätzung.

Ein ganzer Kanal wird in kleinen Paketen mit Pausen abgerufen (siehe channels.py). Der Dialog zeigt vorher in
einfachen Worten, wie lange das etwa dauert und was das Tempo bedeutet."""
import datetime
import tkinter as tk
from tkinter import messagebox, ttk

from .. import autologic as al
from .. import chanlogic, channels, repo
from .common import COLORS, FONT_B, TITLE
from .group_choice import create_selected_group
from .forms import run_async
from .widgets import HelpDot, bind_wrap, show_centered

NO_GROUP, NEW_GROUP = '(keine Gruppe)', 'Neue Gruppe …'
NO_ACCOUNT, ROTATE_ACCOUNT = 'Standardkonto der Plattform', '🔄 Automatisch wechseln (Rotation)'
NO_ACCOUNT_NEEDED = 'Keine Anmeldung nötig'
SCOPES = (('all', 'Alle Videos'), ('latest', 'Nur die neuesten …'), ('since', 'Ab Datum …'))
EXAMPLE = 'z. B. https://www.youtube.com/@name – oder tiktok.com/@name, instagram.com/name, reddit.com/user/name, reddit.com/r/name, x.com/name'
DEFAULT_INTERVAL = 360


class ChannelDialog(tk.Toplevel):
    """`submit(ref, options)` legt den Kanal an und wirft ValueError (Meldung im Dialog). `preview_fn(ref)` holt Name und
    Anzahl im Hintergrund (Standard: chanlogic.preview). `result` ist nach dem Speichern das Ziel."""

    def __init__(self, parent, submit, accounts, groups, db_path, root=None, link='', modal=True, preview_fn=None):
        super().__init__(parent)
        self.withdraw()
        self.submit, self.db_path, self.root_dir = submit, db_path, root
        self.accounts, self.groups = list(accounts), list(groups)
        self.preview_fn = preview_fn or (lambda ref: chanlogic.preview(ref, self.root_dir))
        self.result, self.ref, self.total, self._preview_gen = None, None, None, 0
        self.title('Kanal hinzufügen')
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        body = ttk.Frame(self, padding=(14, 10))
        body.grid(row=0, column=0, sticky='nsew')
        body.columnconfigure(1, weight=1)
        self.body = body
        self.link_var = tk.StringVar(value=link)
        self.scope_var, self.latest_var, self.since_var = tk.StringVar(value='all'), tk.StringVar(value='100'), tk.StringVar(value='')
        self.comments_var = tk.StringVar(value='0')
        self.group_var, self.account_var = tk.StringVar(value=NO_GROUP), tk.StringVar(value=NO_ACCOUNT)
        self.profile_var = tk.StringVar(value=chanlogic.PROFILE_LABELS['normal'])
        self.quiet_on, self.quiet_from, self.quiet_to = tk.BooleanVar(value=False), tk.StringVar(value='23'), tk.StringVar(value='7')
        self.interval_var = tk.StringVar(value=next(t for m, t in al.INTERVALS if m == DEFAULT_INTERVAL))
        self.observe_var = tk.BooleanVar(value=True)
        self.shorts_var, self.onlyvideo_var = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        self.sort_var = tk.StringVar(value='Neueste zuerst')
        self.owner_var = tk.BooleanVar(value=False)
        r = 0

        def label(text, row):
            ttk.Label(body, text=text, font=FONT_B).grid(row=row, column=0, sticky='nw', pady=(8, 0), padx=(0, 12))

        label('Kanal-Link', r)
        row = ttk.Frame(body)
        row.grid(row=r, column=1, sticky='ew', pady=(8, 0))
        row.columnconfigure(0, weight=1)
        self.link_entry = ttk.Entry(row, textvariable=self.link_var, width=58)
        self.link_entry.grid(row=0, column=0, sticky='ew')
        self.check_btn = ttk.Button(row, text='Prüfen', command=self.run_preview)
        self.check_btn.grid(row=0, column=1, padx=(6, 0))
        r += 1
        self.recognized = ttk.Label(body, text=EXAMPLE, foreground=COLORS['muted'], wraplength=520, justify='left')
        self.recognized.grid(row=r, column=1, sticky='w')
        r += 1
        label('Umfang', r)
        scope = ttk.Frame(body)
        scope.grid(row=r, column=1, sticky='w', pady=(8, 0))
        ttk.Radiobutton(scope, text='Alle Videos', value='all', variable=self.scope_var, command=self._update).pack(side='left')
        ttk.Radiobutton(scope, text='Nur die neuesten', value='latest', variable=self.scope_var, command=self._update).pack(side='left', padx=(12, 0))
        self.latest_spin = ttk.Spinbox(scope, from_=1, to=100000, increment=50, width=7, textvariable=self.latest_var, command=self._update)
        self.latest_spin.pack(side='left', padx=(4, 0))
        ttk.Radiobutton(scope, text='Ab Datum', value='since', variable=self.scope_var, command=self._update).pack(side='left', padx=(12, 0))
        self.since_entry = ttk.Entry(scope, textvariable=self.since_var, width=11)
        self.since_entry.pack(side='left', padx=(4, 0))
        ttk.Label(scope, text='(JJJJ-MM-TT)', foreground=COLORS['muted']).pack(side='left', padx=(4, 0))
        r += 1
        self.scope_hint = ttk.Label(body, text='', foreground=COLORS['muted'], wraplength=520, justify='left')
        self.scope_hint.grid(row=r, column=1, sticky='w')
        r += 1
        label('Kommentare pro Video', r)
        self.comments_spin = ttk.Spinbox(body, from_=0, to=5000, increment=10, width=8, textvariable=self.comments_var, command=self._update)
        self.comments_spin.grid(row=r, column=1, sticky='w', pady=(8, 0))
        HelpDot(body, 'comments_per_video').grid(row=r, column=2, sticky='w', pady=(8, 0))
        r += 1
        label('Gruppe', r)
        self.group_cb = ttk.Combobox(body, textvariable=self.group_var, state='readonly', width=40,
                                     values=[NO_GROUP] + [g['name'] for g in self.groups] + [NEW_GROUP])
        self.group_cb.grid(row=r, column=1, sticky='w', pady=(8, 0))
        self.group_cb.bind('<<ComboboxSelected>>', lambda _e: self._group_changed())
        r += 1
        label('Konto', r)
        self.account_cb = ttk.Combobox(body, textvariable=self.account_var, state='readonly', width=40)
        self.account_cb.grid(row=r, column=1, sticky='w', pady=(8, 0))
        self.account_cb.bind('<<ComboboxSelected>>', lambda _e: self._update())
        HelpDot(body, 'rotation').grid(row=r, column=2, sticky='w', pady=(8, 0))
        r += 1
        label('Tempo', r)
        self.profile_cb = ttk.Combobox(body, textvariable=self.profile_var, state='readonly', width=18,
                                       values=[chanlogic.PROFILE_LABELS[k] for k in chanlogic.PROFILE_ORDER])
        self.profile_cb.grid(row=r, column=1, sticky='w', pady=(8, 0))
        self.profile_cb.bind('<<ComboboxSelected>>', lambda _e: self._update())
        HelpDot(body, 'pace').grid(row=r, column=2, sticky='w', pady=(8, 0))
        r += 1
        self.profile_hint = ttk.Label(body, text='', foreground=COLORS['muted'], wraplength=520, justify='left')
        self.profile_hint.grid(row=r, column=1, sticky='w')
        r += 1
        label('Ruhezeit', r)
        quiet = ttk.Frame(body)
        quiet.grid(row=r, column=1, sticky='w', pady=(8, 0))
        ttk.Checkbutton(quiet, text='Nachts nichts abrufen, von ', variable=self.quiet_on, command=self._update).pack(side='left')
        self.qfrom = ttk.Spinbox(quiet, from_=0, to=23, width=3, textvariable=self.quiet_from, command=self._update, wrap=True)
        self.qfrom.pack(side='left')
        ttk.Label(quiet, text=' bis ').pack(side='left')
        self.qto = ttk.Spinbox(quiet, from_=0, to=23, width=3, textvariable=self.quiet_to, command=self._update, wrap=True)
        self.qto.pack(side='left')
        ttk.Label(quiet, text=' Uhr (Standard: aus)', foreground=COLORS['muted']).pack(side='left')
        HelpDot(quiet, 'quiet_hours').pack(side='left', padx=(4, 0))
        r += 1
        label('Danach', r)
        after = ttk.Frame(body)
        after.grid(row=r, column=1, sticky='w', pady=(8, 0))
        ttk.Checkbutton(after, text='Neue Videos danach automatisch beobachten, alle ', variable=self.observe_var,
                        command=self._update).pack(side='left')
        self.interval_cb = ttk.Combobox(after, textvariable=self.interval_var, state='readonly', width=11,
                                        values=[t for _m, t in al.INTERVALS])
        self.interval_cb.pack(side='left')
        self.interval_cb.bind('<<ComboboxSelected>>', lambda _e: self._update())
        r += 1
        self.extra = ttk.Frame(body)
        self.extra.grid(row=r, column=1, sticky='w', pady=(6, 0))
        self.shorts_cb = ttk.Checkbutton(self.extra, text='Shorts mitnehmen', variable=self.shorts_var)
        self.onlyvideo_cb = ttk.Checkbutton(self.extra, text='Nur Beiträge mit Video', variable=self.onlyvideo_var, command=self._update)
        self.sort_cb = ttk.Combobox(self.extra, textvariable=self.sort_var, state='readonly', width=16,
                                    values=['Neueste zuerst', 'Beste zuerst'])
        self.owner_cb = ttk.Checkbutton(self.extra, text='Kanal-Inhaber auch als Ersteller in die Gruppe', variable=self.owner_var)
        r += 1
        box = ttk.LabelFrame(body, text='Wie lange dauert das?', padding=8)
        box.grid(row=r, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        box.columnconfigure(0, weight=1)
        self.estimate = ttk.Label(box, text='', wraplength=560, justify='left')
        self.estimate.grid(row=0, column=0, sticky='w')
        self.formula = ttk.Label(box, text='', foreground=COLORS['muted'], wraplength=560, justify='left')
        self.formula.grid(row=1, column=0, sticky='w', pady=(2, 0))
        self.warnings = ttk.Label(box, text='', foreground=COLORS['warn'], wraplength=560, justify='left')
        self.warnings.grid(row=2, column=0, sticky='w', pady=(2, 0))
        r += 1
        self.error = ttk.Label(body, text='', foreground=COLORS['err'], wraplength=560, justify='left')
        self.error.grid(row=r, column=0, columnspan=2, sticky='w', pady=(6, 0))
        r += 1
        btns = ttk.Frame(body)
        btns.grid(row=r, column=0, columnspan=2, sticky='e', pady=(10, 0))
        self.save_btn = ttk.Button(btns, text='Kanal hinzufügen', command=self.save, default='active')
        self.save_btn.pack(side='left')
        ttk.Button(btns, text='Abbrechen', command=self.destroy).pack(side='left', padx=(8, 0))
        self.bind('<Escape>', lambda _e: self.destroy())
        self.link_var.trace_add('write', lambda *_: self._link_typed())
        for var in (self.latest_var, self.since_var, self.comments_var, self.quiet_from, self.quiet_to):
            var.trace_add('write', lambda *_: self._update())
        self._link_typed()
        self.link_entry.focus_set()
        show_centered(self, parent)
        if modal:
            self.update_idletasks()
            self.wait_visibility()
            self.grab_set()
            self.wait_window(self)

    # ---------- Erkennen ----------

    def _link_typed(self):
        text = self.link_var.get().strip()
        self.total = None
        self._preview_gen += 1
        if not text:
            self.ref = None
            self.recognized.configure(text=EXAMPLE, foreground=COLORS['muted'])
        else:
            try:
                self.ref = channels.parse_channel_link(text)
            except ValueError as error:
                self.ref = None
                self.recognized.configure(text='✖ ' + str(error), foreground=COLORS['err'])
            else:
                name = channels.PLATFORM_NAMES[self.ref.platform]
                kind = {'subreddit': 'Subreddit', 'user': 'Benutzer'}.get(self.ref.kind, 'Kanal' if self.ref.platform == 'youtube' else 'Profil')
                self.recognized.configure(text=f'✔ {name} · {kind} {self.ref.display}', foreground=COLORS['ok'])
        self._platform_widgets()
        self._update()

    def _platform_widgets(self):
        ref = self.ref
        platform = ref.platform if ref else None
        for w in (self.shorts_cb, self.onlyvideo_cb, self.sort_cb, self.owner_cb):
            w.pack_forget()
        if platform == 'youtube':
            self.shorts_cb.pack(side='left', padx=(0, 14))
        if platform in ('reddit', 'x'):
            self.onlyvideo_cb.pack(side='left', padx=(0, 14))
        if ref is not None and ref.kind == 'subreddit':
            self.sort_cb.pack(side='left', padx=(0, 14))
        if ref is not None and ref.kind != 'subreddit':
            self.owner_cb.pack(side='left')
        if platform is None or platform == 'youtube':
            self.account_cb.configure(values=[NO_ACCOUNT_NEEDED], state='disabled')
            self.account_var.set(NO_ACCOUNT_NEEDED)
        else:
            mine = [a for a in self.accounts if a['platform'] == platform]
            options = [NO_ACCOUNT] + [a['label'] for a in mine] + [ROTATE_ACCOUNT]
            self.account_cb.configure(values=options, state='readonly')
            if self.account_var.get() not in options:
                self.account_var.set(NO_ACCOUNT)

    def run_preview(self):
        """Name und Zahl der Videos holen (YouTube: kleine Netzabfrage im Hintergrund)."""
        if self.ref is None:
            self.error.configure(text='✖ Bitte zuerst einen gültigen Kanal-Link einfügen.')
            return
        ref, gen = self.ref, self._preview_gen
        self.error.configure(text='')
        self.check_btn.configure(state='disabled')
        self.recognized.configure(text=f'… {channels.PLATFORM_NAMES[ref.platform]}: {ref.display} wird geprüft', foreground=COLORS['muted'])

        def done(result, error):
            try:
                self.check_btn.configure(state='normal')   # immer freigeben, auch wenn der Link inzwischen geändert wurde
            except tk.TclError:
                return
            if gen != self._preview_gen or self.ref is not ref:
                return
            if error is not None:
                self.recognized.configure(text=f'⚠ Vorschau nicht möglich: {_short(error)}', foreground=COLORS['warn'])
                return
            self.total = result.get('total')
            name = result.get('name') or ref.display
            self.recognized.configure(text=f'✔ {channels.PLATFORM_NAMES[ref.platform]} · {name} ({ref.display}). {result.get("note", "")}',
                                      foreground=COLORS['ok'])
            self._update()
        run_async(self, lambda: self.preview_fn(ref), done)

    # ---------- Auswahl / Schätzung ----------

    def _group_changed(self):
        create_selected_group(self, no_group=NO_GROUP, new_group=NEW_GROUP)

    def profile_key(self):
        return next((k for k, t in chanlogic.PROFILE_LABELS.items() if t == self.profile_var.get()), 'normal')

    def quiet_text(self):
        if not self.quiet_on.get():
            return None
        return f'{self.quiet_from.get().strip()}-{self.quiet_to.get().strip()}'

    def _account_count(self):
        if self.ref is None or self.ref.platform not in repo.LOGIN_PLATFORMS:
            return 1
        if self.account_var.get() == ROTATE_ACCOUNT:
            return max(1, len([a for a in self.accounts if a['platform'] == self.ref.platform and a.get('rotation_enabled', 1)]))
        return 1

    def _count_for_estimate(self):
        """(Zahl, ob sie nur ein Beispiel ist)."""
        scope = self.scope_var.get()
        if scope == 'latest':
            try:
                return max(1, int(self.latest_var.get())), False
            except ValueError:
                pass
        if self.total and scope != 'latest':
            return int(self.total), False
        return 1000, True

    def _update(self):
        scope = self.scope_var.get()
        self.latest_spin.configure(state='normal' if scope == 'latest' else 'disabled')
        self.since_entry.configure(state='normal' if scope == 'since' else 'disabled')
        for w in (self.qfrom, self.qto):
            w.configure(state='normal' if self.quiet_on.get() else 'disabled')
        self.interval_cb.configure(state='readonly' if self.observe_var.get() else 'disabled')
        hint = ''
        if self.ref is not None and self.ref.kind == 'subreddit' and scope == 'all':
            hint = f'Bei einem Subreddit gilt „alle“ nie unbegrenzt: Standard sind die neuesten {channels.DEFAULT_SUBREDDIT_LIMIT} Beiträge (Reddit zeigt höchstens etwa 1.000).'
        elif self.ref is not None and self.ref.platform in channels.LIST_LIMITS and scope == 'all':
            hint = f'{channels.PLATFORM_NAMES[self.ref.platform]} zeigt höchstens etwa {channels.LIST_LIMITS[self.ref.platform]:,}'.replace(',', '.') + ' Einträge zurück – ältere sind nicht erreichbar.'
        elif scope == 'all':
            hint = 'Die Plattform bestimmt, wie weit die Liste zurückreicht. Wie viele Videos wirklich gefunden wurden, zeigt der Kanal nach dem ersten Lauf – nie als „alle“, wenn die Plattform die Liste begrenzt.'
        self.scope_hint.configure(text=hint)
        key = self.profile_key()
        self.profile_hint.configure(text=chanlogic.profile_text(key))
        platform = self.ref.platform if self.ref else 'youtube'
        count, example = self._count_for_estimate()
        try:
            comments = int(self.comments_var.get() or 0)
        except ValueError:
            comments = 0
        try:
            quiet = self.quiet_text()
            repo.check_quiet_hours(quiet)
        except ValueError:
            quiet = None
        e = channels.estimate(count, platform, key, accounts=self._account_count(), comments=comments, quiet_hours=quiet)
        what = f'Beispiel: {count:,} Videos'.replace(',', '.') if example else f'{e["videos"]:,} Videos'.replace(',', '.')
        self.estimate.configure(text=f'{what} bei Tempo „{self.profile_var.get()}“: {e["text"]}.'
                                + (' Die echte Zahl steht nach dem ersten Lauf fest (oder klicke auf „Prüfen“).' if example else ''))
        self.formula.configure(text='Rechnung: ' + e['formula'] + '. Das sind die Grenzen, die sich das Programm selbst setzt – '
                               'keine Messung; die Plattform kann früher oder später bremsen.')
        self.warnings.configure(text=' '.join(e['notes']))

    # ---------- Speichern ----------

    def options(self):
        """Eingaben prüfen und als Optionen für channels.create_channel zurückgeben (ValueError mit deutscher Meldung)."""
        if self.ref is None:
            raise ValueError('Bitte einen gültigen Kanal-Link einfügen.')
        scope = self.scope_var.get()
        scope_value = None
        if scope == 'latest':
            try:
                scope_value = int(self.latest_var.get())
            except ValueError:
                raise ValueError('Bitte die Zahl der neuesten Videos als ganze Zahl angeben.') from None
            if not 1 <= scope_value <= 100000:
                raise ValueError('Die Zahl der neuesten Videos muss zwischen 1 und 100000 liegen.')
        elif scope == 'since':
            text = self.since_var.get().strip()
            try:
                datetime.date.fromisoformat(text)
            except ValueError:
                raise ValueError('Bitte das Datum so schreiben: 2026-01-31 (Jahr-Monat-Tag).') from None
            scope_value = text
        try:
            comments = int(self.comments_var.get().strip() or 0)
        except ValueError:
            raise ValueError('Kommentare pro Video: bitte eine ganze Zahl von 0 bis 5000.') from None
        if not 0 <= comments <= 5000:
            raise ValueError('Kommentare pro Video: 0 bis 5000.')
        quiet = self.quiet_text()
        repo.check_quiet_hours(quiet)
        platform = self.ref.platform
        mine = {a['label']: a['id'] for a in self.accounts if a['platform'] == platform}
        account_id, mode = None, 'default'
        if platform != 'youtube':
            if self.account_var.get() == ROTATE_ACCOUNT:
                mode = 'rotate'
            elif mine.get(self.account_var.get()):
                account_id, mode = mine[self.account_var.get()], 'fixed'
        group_id = next((g['id'] for g in self.groups if g['name'] == self.group_var.get()), None)
        interval = next((m for m, t in al.INTERVALS if t == self.interval_var.get()), DEFAULT_INTERVAL)
        if self.owner_var.get() and group_id is None and self.ref.kind != 'subreddit':
            raise ValueError('„Kanal-Inhaber auch als Ersteller“ braucht eine Gruppe. Bitte oben eine Gruppe wählen.')
        return {'scope': scope, 'scope_value': scope_value, 'comments': comments, 'group_id': group_id, 'account_mode': mode,
                'account_id': account_id, 'pace_profile': self.profile_key(), 'interval_min': interval,
                'observe': bool(self.observe_var.get()), 'quiet_hours': quiet, 'include_shorts': bool(self.shorts_var.get()),
                'only_video': bool(self.onlyvideo_var.get()) and platform in ('reddit', 'x'),
                'list_sort': 'top' if self.sort_var.get() == 'Beste zuerst' and self.ref.kind == 'subreddit' else 'new',
                'owner_as_creator': bool(self.owner_var.get()) and self.ref.kind != 'subreddit',
                'platform_total': self.total}

    def save(self):
        try:
            options = self.options()
            target = self.submit(self.ref, options)
        except ValueError as error:
            self.error.configure(text='✖ ' + str(error))
            return False
        self.result = target
        self.destroy()
        return True


def _short(error):
    from ..scheduler import clean_error
    return clean_error(error, limit=160)


def open_dialog(parent, root, link='', modal=True, preview_fn=None):
    """Öffnet den Dialog mit den Konten und Gruppen aus der Datenbank. Gibt den Dialog zurück (`result` = neues Ziel)."""
    db = al.resolve_db(root)
    snap = al.load_snapshot(db, 1, 1)
    if snap.get('error'):
        # Sonst öffnet sich der Dialog stumm ohne die eigenen Gruppen und Konten (nur „(keine Gruppe)“ und „Standardkonto“).
        text = snap['error'] + ' Der Dialog zeigt deshalb deine Gruppen und Konten nicht. Schließe ihn und versuche es gleich noch einmal.'
        app = getattr(parent, 'app', None)
        if app is not None and hasattr(app, 'log'):
            app.log(f'Kanal-Dialog: Gruppen und Konten nicht geladen – {snap["error"]}', 'ERROR')
        messagebox.showwarning(TITLE, text)

    def submit(ref, options):
        return chanlogic.create(db, ref, **options)
    return ChannelDialog(parent, submit, snap['accounts'], snap['groups'], db, root=root, link=link, modal=modal, preview_fn=preview_fn)
