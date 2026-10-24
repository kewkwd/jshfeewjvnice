"""Dialog „Ziel hinzufügen / bearbeiten“ für die Automatik."""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import autologic as al
from .common import COLORS, FONT_B, TITLE
from .group_choice import create_selected_group
from .widgets import show_centered

NO_ACCOUNT = 'Standardkonto der Plattform'
ROTATE_ACCOUNT = '🔄 Automatisch wechseln (Rotation)'
ROTATE_HINT = ('Die Konten dieser Plattform werden abwechselnd benutzt: nach so vielen Videos, wie unter Einstellungen → Abruf '
               'steht, und sofort bei einem Fehler des Kontos.')
NO_GROUP = '(keine Gruppe)'
NEW_GROUP = 'Neue Gruppe …'
CUSTOM_INTERVAL = 'Eigene Zeit …'
VALUE_LABEL = {'video': 'Video-Link', 'hashtag': 'Hashtag (ohne #)', 'search': 'Suchbegriff', 'profile': 'Benutzername (ohne @)'}
VALUE_EXAMPLE = {'video': 'z. B. https://www.tiktok.com/@name/video/123456', 'hashtag': 'z. B. tanzen',
                 'search': 'z. B. lofi hip hop', 'profile': 'z. B. name.beispiel'}
# Beispiel für Video-Links passend zur gewählten Plattform
VIDEO_EXAMPLE = {'tiktok': 'z. B. https://www.tiktok.com/@name/video/7123456789012345678',
                 'youtube': 'z. B. https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                 'instagram': 'z. B. https://www.instagram.com/reel/AbCdEfGh123/',
                 'reddit': 'z. B. https://www.reddit.com/r/python/comments/abc123/titel/',
                 'x': 'z. B. https://x.com/name/status/1234567890123456789'}
# Bezeichnung und Beispiel hängen bei Reddit und X von der Plattform ab (Subreddit: r/name, Profil: u/name).
LABEL_BY_PLATFORM = {('search', 'reddit'): 'Suchbegriff oder Subreddit', ('profile', 'reddit'): 'Benutzername (u/name)',
                     ('profile', 'x'): 'Kontoname (ohne @)', ('search', 'x'): 'Suchbegriff', ('hashtag', 'x'): 'Hashtag (ohne #)'}
EXAMPLE_BY_PLATFORM = {('search', 'reddit'): 'Subreddit: r/name (neue Beiträge), z. B. r/python – oder ein Suchbegriff',
                       ('profile', 'reddit'): 'z. B. u/spez',
                       ('profile', 'x'): 'z. B. name_beispiel (braucht eine Anmeldung)',
                       ('search', 'x'): 'z. B. lofi hip hop (braucht eine Anmeldung)',
                       ('hashtag', 'x'): 'z. B. tanzen (braucht eine Anmeldung)'}


def value_example(kind, platform):
    """Beispieltext unter dem Eingabefeld: bei Video-Links passend zur Plattform (YouTube-Beispiel bei YouTube)."""
    if kind == 'video':
        return VIDEO_EXAMPLE.get(platform, VALUE_EXAMPLE['video'])
    return EXAMPLE_BY_PLATFORM.get((kind, platform), VALUE_EXAMPLE[kind])


def value_label(kind, platform):
    return LABEL_BY_PLATFORM.get((kind, platform), VALUE_LABEL[kind])


class TargetDialog(tk.Toplevel):
    """submit(clean, account_id, group_id) wird beim Speichern aufgerufen; wirft ValueError → Meldung im Dialog.
    `edit` ist ein vorhandenes Ziel (Art, Plattform und Wert bleiben dann fest)."""

    def __init__(self, parent, submit, accounts, groups, db_path, edit=None, default_comments=20, hint_fn=None, modal=True):
        super().__init__(parent)
        self.withdraw()   # erst zeigen, wenn es mittig über dem Hauptfenster steht
        self.submit, self.db_path, self.edit, self.hint_fn = submit, db_path, edit, hint_fn
        self.accounts, self.groups = list(accounts), list(groups)
        self.result = None
        self.title('Ziel bearbeiten' if edit else 'Ziel hinzufügen')
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        body = ttk.Frame(self, padding=14)
        body.grid(row=0, column=0, sticky='nsew')
        body.columnconfigure(1, weight=1)
        self.kind_var = tk.StringVar(value=al.KIND_LABELS[edit['kind']] if edit else al.KIND_LABELS['hashtag'])
        self.platform_var = tk.StringVar()
        self.value_var = tk.StringVar(value=edit['value'] if edit else '')
        self.account_var, self.group_var = tk.StringVar(), tk.StringVar()
        self.interval_var, self.custom_var = tk.StringVar(), tk.StringVar(value='60')
        self.comments_var = tk.StringVar(value=str(edit['max_comments'] if edit else default_comments))
        self.videos_var = tk.StringVar(value=str(edit['max_videos'] if edit else 20))
        r = 0

        def label(text, row):
            ttk.Label(body, text=text, font=FONT_B).grid(row=row, column=0, sticky='nw', pady=(8, 0), padx=(0, 12))

        label('Art', r)
        self.kind_cb = ttk.Combobox(body, textvariable=self.kind_var, values=[al.KIND_LABELS[k] for k in al.KIND_ORDER],
                                    state='disabled' if edit else 'readonly', width=26)
        self.kind_cb.grid(row=r, column=1, sticky='w', pady=(8, 0))
        self.kind_cb.bind('<<ComboboxSelected>>', lambda _e: self._kind_changed())
        r += 1
        self.help_label = ttk.Label(body, text='', foreground=COLORS['muted'], wraplength=380, justify='left')
        self.help_label.grid(row=r, column=1, sticky='w')
        r += 1
        label('Plattform', r)
        self.platform_cb = ttk.Combobox(body, textvariable=self.platform_var, state='readonly', width=26)
        self.platform_cb.grid(row=r, column=1, sticky='w', pady=(8, 0))
        r += 1
        self.value_title = ttk.Label(body, text='', font=FONT_B)
        self.value_title.grid(row=r, column=0, sticky='nw', pady=(8, 0), padx=(0, 12))
        self.value_entry = ttk.Entry(body, textvariable=self.value_var, width=48, state='disabled' if edit else 'normal')
        self.value_entry.grid(row=r, column=1, sticky='ew', pady=(8, 0))
        r += 1
        self.value_hint = ttk.Label(body, text='', foreground=COLORS['muted'])
        self.value_hint.grid(row=r, column=1, sticky='w')
        r += 1
        label('Konto', r)
        self.account_cb = ttk.Combobox(body, textvariable=self.account_var, state='readonly', width=40)
        self.account_cb.grid(row=r, column=1, sticky='w', pady=(8, 0))
        self.account_cb.bind('<<ComboboxSelected>>', lambda _e: self._account_changed())
        r += 1
        self.account_hint = ttk.Label(body, text='', foreground=COLORS['muted'], wraplength=380, justify='left')
        self.account_hint.grid(row=r, column=1, sticky='w')
        r += 1
        label('Gruppe', r)
        self.group_cb = ttk.Combobox(body, textvariable=self.group_var, state='readonly', width=40)
        self.group_cb.grid(row=r, column=1, sticky='w', pady=(8, 0))
        self.group_cb.bind('<<ComboboxSelected>>', lambda _e: self._group_changed())
        r += 1
        self.group_hint = ttk.Label(body, text='Gefundene Videos werden in diese Gruppe einsortiert.', foreground=COLORS['muted'])
        self.group_hint.grid(row=r, column=1, sticky='w')
        r += 1
        label('Wiederholen alle', r)
        iv = ttk.Frame(body)
        iv.grid(row=r, column=1, sticky='w', pady=(8, 0))
        self.interval_cb = ttk.Combobox(iv, textvariable=self.interval_var, state='readonly', width=18,
                                        values=[t for _m, t in al.INTERVALS] + [CUSTOM_INTERVAL])
        self.interval_cb.pack(side='left')
        self.interval_cb.bind('<<ComboboxSelected>>', lambda _e: self._interval_changed())
        self.custom_frame = ttk.Frame(iv)
        self.custom_spin = ttk.Spinbox(self.custom_frame, from_=5, to=60 * 24 * 30, increment=15, width=8, textvariable=self.custom_var)
        self.custom_spin.pack(side='left', padx=(10, 4))
        ttk.Label(self.custom_frame, text='Minuten').pack(side='left')
        r += 1
        label('Kommentare pro Video', r)
        self.comments_spin = ttk.Spinbox(body, from_=0, to=5000, increment=10, width=8, textvariable=self.comments_var)
        self.comments_spin.grid(row=r, column=1, sticky='w', pady=(8, 0))
        r += 1
        self.videos_title = ttk.Label(body, text='Videos pro Lauf', font=FONT_B)
        self.videos_title.grid(row=r, column=0, sticky='nw', pady=(8, 0), padx=(0, 12))
        self.videos_spin = ttk.Spinbox(body, from_=1, to=200, increment=5, width=8, textvariable=self.videos_var)
        self.videos_spin.grid(row=r, column=1, sticky='w', pady=(8, 0))
        r += 1
        self.notice = ttk.Label(body, text='', foreground=COLORS['warn'], wraplength=420, justify='left')
        self.notice.grid(row=r, column=0, columnspan=2, sticky='w', pady=(10, 0))
        r += 1
        self.error = ttk.Label(body, text='', foreground=COLORS['err'], wraplength=420, justify='left')
        self.error.grid(row=r, column=0, columnspan=2, sticky='w', pady=(6, 0))
        r += 1
        btns = ttk.Frame(body)
        btns.grid(row=r, column=0, columnspan=2, sticky='e', pady=(14, 0))
        self.save_btn = ttk.Button(btns, text='Speichern', command=self.save, default='active')
        self.save_btn.pack(side='left')
        ttk.Button(btns, text='Abbrechen', command=self.destroy).pack(side='left', padx=(8, 0))
        self.bind('<Return>', lambda _e: self.save())
        self.bind('<Escape>', lambda _e: self.destroy())

        self._fill_choices(edit)
        self._kind_changed(initial=True)
        self.platform_cb.bind('<<ComboboxSelected>>', lambda _e: self._platform_changed())
        self.value_var.trace_add('write', lambda *_: self._value_typed())
        self.value_entry.focus_set()
        show_centered(self, parent)
        if modal:
            self.update_idletasks()
            self.wait_visibility()
            self.grab_set()   # modal: das Hauptfenster bleibt gesperrt, bis der Dialog zu ist
            self.wait_window(self)

    # ---------- Auswahllisten ----------

    def _kind(self):
        return next((k for k, t in al.KIND_LABELS.items() if t == self.kind_var.get()), 'hashtag')

    def _platform(self):
        return next((k for k, t in al.PLATFORM_NAMES.items() if t == self.platform_var.get()), '')

    def _fill_choices(self, edit):
        groups = [NO_GROUP] + [g['name'] for g in self.groups] + [NEW_GROUP]
        self.group_cb.configure(values=groups)
        current = next((g['name'] for g in self.groups if edit and g['id'] == edit.get('group_id')), NO_GROUP)
        self.group_var.set(current)
        minutes = edit['interval_min'] if edit else 360
        label = next((t for m, t in al.INTERVALS if m == minutes), None)
        if label:
            self.interval_var.set(label)
        else:
            self.interval_var.set(CUSTOM_INTERVAL)
            self.custom_var.set(str(minutes))
        self._interval_changed()
        if edit:
            self.platform_var.set(al.PLATFORM_NAMES[edit['platform']])

    def _kind_changed(self, initial=False):
        kind = self._kind()
        self.help_label.configure(text=al.KIND_HELP[kind])
        self.value_title.configure(text=value_label(kind, self._platform()))
        self.value_hint.configure(text=value_example(kind, self._platform()))
        names = [al.PLATFORM_NAMES[p] for p in al.PLATFORMS_FOR_KIND[kind]]
        self.platform_cb.configure(values=names, state='disabled' if self.edit else 'readonly')
        if self.platform_var.get() not in names:
            self.platform_var.set(names[0])
        finder = kind != 'video'
        self.videos_spin.configure(state='normal' if finder else 'disabled')
        self.videos_title.configure(foreground='' if finder else COLORS['muted'])
        self.group_hint.configure(text='Gefundene Videos werden in diese Gruppe einsortiert.' if finder
                                  else 'Optional: das Video wird dieser Gruppe zugeordnet, wenn der Dienst es abruft.')
        self._platform_changed()

    def _platform_changed(self):
        platform = self._platform()
        if platform == 'youtube':
            self.account_cb.configure(values=['Keine Anmeldung nötig'], state='disabled')
            self.account_var.set('Keine Anmeldung nötig')
        else:
            mine = [a for a in self.accounts if a['platform'] == platform]
            options = [NO_ACCOUNT] + [a['label'] for a in mine] + [ROTATE_ACCOUNT]
            self.account_cb.configure(values=options, state='readonly')
            current = None
            if self.edit and self.edit.get('platform') == platform:
                if self.edit.get('account_mode') == 'rotate':
                    current = ROTATE_ACCOUNT
                else:
                    current = next((a['label'] for a in mine if a['id'] == self.edit.get('account_id')), None)
            if self.account_var.get() not in options or current:
                self.account_var.set(current or NO_ACCOUNT)
        self._account_changed()
        self.value_title.configure(text=value_label(self._kind(), platform))
        self.value_hint.configure(text=value_example(self._kind(), platform))
        self.notice.configure(text=(self.hint_fn(self._kind(), platform) if self.hint_fn else '') or '')

    def _account_changed(self):
        self.account_hint.configure(text=ROTATE_HINT if self.account_var.get() == ROTATE_ACCOUNT else '')

    def _value_typed(self):
        if self._kind() != 'video' or self.edit:
            return
        from .. import guicore as core
        found = core.detect_platform(core.normalize_link(self.value_var.get()))
        if found and al.PLATFORM_NAMES[found] != self.platform_var.get():
            self.platform_var.set(al.PLATFORM_NAMES[found])
            self._platform_changed()

    def _interval_changed(self):
        if self.interval_var.get() == CUSTOM_INTERVAL:
            self.custom_frame.pack(side='left')
        else:
            self.custom_frame.pack_forget()

    def _group_changed(self):
        create_selected_group(self, no_group=NO_GROUP, new_group=NEW_GROUP)

    # ---------- Speichern ----------

    def _minutes(self):
        label = self.interval_var.get()
        if label == CUSTOM_INTERVAL:
            return self.custom_var.get().strip()
        return next((m for m, t in al.INTERVALS if t == label), 360)

    def save(self):
        kind, platform = self._kind(), self._platform()
        try:
            clean = al.check_target(kind, platform, self.value_var.get(), self._minutes(), self.comments_var.get().strip() or '0',
                                    self.videos_var.get().strip() or '0')
            mine = {a['label']: a['id'] for a in self.accounts if a['platform'] == platform}
            account_id = mine.get(self.account_var.get()) if platform != 'youtube' else None
            if platform != 'youtube' and self.account_var.get() == ROTATE_ACCOUNT:
                clean['account_mode'] = 'rotate'
            else:
                clean['account_mode'] = 'fixed' if account_id else 'default'
            group_id = next((g['id'] for g in self.groups if g['name'] == self.group_var.get()), None)
            self.submit(clean, account_id, group_id)
        except ValueError as error:
            self.error.configure(text='✖ ' + str(error))
            return False
        self.result = clean
        self.destroy()
        return True
