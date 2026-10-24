"""Tab „Übersicht“: Was ist los, und was musst du tun? Startseite mit Erste-Schritte-Liste, Schnellaktionen,
Hinweisen mit Lösungsknopf und Statuskarten.

Alle Zahlen und Hinweise kommen aus `socialscraper/homelogic.py` (ohne Tk, getestet). Hier steht nur die Darstellung.
Alles Langsame (Datenbank lesen, Systemprüfung) läuft im Hintergrund-Thread; aktualisiert wird alle paar Sekunden, aber nur,
solange der Tab sichtbar ist. Der Aufbau wird nur neu gezeichnet, wenn sich der Inhalt wirklich geändert hat (kein Flackern).
"""
import time
import tkinter as tk
from tkinter import ttk

from .. import autologic as al
from .. import homelogic
from . import common
from .common import COLORS, FONT, FONT_B, Tab, system_check, track_after
from .forms import H1, ScrollFrame, run_async
from .widgets import PAD, HelpDot

REFRESH_MS = 4000
SYSTEM_CHECK_EVERY = 600          # Sekunden: die Systemprüfung ist ein wenig langsam und ändert sich selten
LEVEL_COLOR = {'err': '#cf222e', 'warn': '#d4a72c', 'info': '#0969da'}
LEVEL_MARK = {'err': '✖', 'warn': '⚠', 'info': 'ℹ'}
CARD_BG = '#ffffff'
CARD_LINE = '#d0d7de'
WIDE = 800                         # ab dieser Breite stehen die Karten in zwei Spalten


class HomeTab(Tab):
    def build(self):
        self.snap = None
        self.cards = {}
        self.items = []
        self.steps = []
        self._loading, self._again = False, False
        self._check_expanded = None      # None = automatisch (klein: nur der nächste Schritt), sonst Marcs Wahl für diese Sitzung
        self._last_compact = None
        self._sigs = {}
        self._system = (None, 0.0)       # (Ergebnis, Zeitpunkt)
        self._width = 1000
        self._after_id = None
        self.configure(padding=0)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.scroll = ScrollFrame(self, on_width=self._on_width)
        self.scroll.grid(row=0, column=0, sticky='nsew')
        body = self.scroll.body
        body.columnconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure('Quick.TButton', font=('Segoe UI', 11, 'bold'), padding=(14, 12))

        head = ttk.Frame(body)
        head.grid(row=0, column=0, sticky='ew', padx=PAD, pady=(PAD, 4))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text='Übersicht', font=H1).grid(row=0, column=0, sticky='w')
        self.stamp = ttk.Label(head, text='', foreground=COLORS['muted'])
        self.stamp.grid(row=0, column=1, sticky='e')
        self.sub = ttk.Label(head, text='Hier siehst du, was gerade los ist – und was du tun musst.', foreground=COLORS['muted'])
        self.sub.grid(row=1, column=0, columnspan=2, sticky='w')

        self.check_box = ttk.Frame(body)
        self.check_box.grid(row=1, column=0, sticky='ew', padx=PAD, pady=(6, 0))
        self.check_box.columnconfigure(0, weight=1)
        self.quick = ttk.Frame(body)
        self.quick.grid(row=2, column=0, sticky='ew', padx=PAD, pady=(10, 0))
        self.quick_btns = []
        for number, (text, action) in enumerate((('Video-Link abrufen', 'goto_fetch'), ('Kanal hinzufügen', 'add_channel'),
                                                  ('Stimmung bestimmen', 'mood'), ('Daten ansehen', 'goto_data'))):
            btn = ttk.Button(self.quick, text=text, style='Quick.TButton', command=lambda a=action: self.act({'id': a}))
            self.quick_btns.append(btn)
        self._place_quick()

        self.attn_box = ttk.Frame(body)
        self.attn_box.grid(row=3, column=0, sticky='ew', padx=PAD, pady=(14, 0))
        self.attn_box.columnconfigure(0, weight=1)
        self.cards_box = ttk.Frame(body)
        self.cards_box.grid(row=4, column=0, sticky='ew', padx=PAD, pady=(14, PAD))
        self.cards_box.columnconfigure(0, weight=1, uniform='card')
        self.cards_box.columnconfigure(1, weight=1, uniform='card')
        self._draw_attention(loading=True)
        self.bind('<Destroy>', self._destroyed, add='+')
        self.bind('<Configure>', self._on_height, add='+')
        self._after_id = track_after(self, REFRESH_MS, self._auto)

    # ---------- Zugriff auf Zustand und Aktionen ----------

    def root(self):
        return getattr(self.app, 'root', None) or common.ROOT

    def state(self):
        state = getattr(self.app, 'ui_state', None)
        return (state.get('home') or {}) if state is not None else {}

    def act(self, action):
        runner = getattr(self.app, 'run_action', None)
        if runner is not None:
            runner(action)

    def _destroyed(self, event):
        pass

    # ---------- Laden im Hintergrund ----------

    def refresh(self):
        if self._loading:
            self._again = True
            return
        self._loading, self._again = True, False
        root, db = self.root(), al.resolve_db(self.root())
        cached, at = self._system
        state = getattr(self.app, 'ui_state', None)
        ui = dict(state.data) if state is not None else {}

        def work():
            ok = cached
            if ok is None or time.time() - at > SYSTEM_CHECK_EVERY:
                try:
                    ok = bool(system_check(common.core.read_settings(root))[1])
                except Exception:
                    ok = None
            return ok, homelogic.collect(root, db, system_ok=ok, ui_state=ui)

        def done(result, error):
            self._loading = False
            if error is None and result is not None:
                ok, snap = result
                self._system = (ok, time.time())
                self.snap = snap
                self.show(snap)
            if self._again:
                self.refresh()
        run_async(self, work, done)

    def _auto(self):
        try:
            if self.winfo_ismapped():
                self.refresh()
        finally:
            try:
                self._after_id = track_after(self, REFRESH_MS, self._auto)
            except tk.TclError:
                pass

    # ---------- Anzeige ----------

    def show(self, snap):
        state = self.state()
        self.cards = homelogic.cards(snap)
        self.items = homelogic.attention(snap)
        self.steps = homelogic.checklist(snap, state)
        self.stamp.configure(text='Stand ' + time.strftime('%H:%M:%S'))
        self._draw_checklist(state)
        self._draw_attention()
        self._draw_cards()

    def _wrap_for(self, kind, width=None):
        """Umbruchbreite je Art von Text, passend zur Fensterbreite (Platz für die Knöpfe daneben abgezogen)."""
        width = self._width if width is None else width
        if kind == 'check':
            return max(240, width - 480)
        if kind == 'attn':
            return max(260, width - 2 * PAD - 250)
        return max(220, (width // 2 - 60) if width >= WIDE else width - 140)

    def _check_compact(self):
        """Im niedrigen Fenster zeigt die Erste-Schritte-Karte nur den nächsten Schritt (sonst rutschen die Hinweise aus dem Bild)."""
        if self._check_expanded is not None:
            return not self._check_expanded
        height = self.winfo_height()
        if height <= 50:
            try:
                height = int(self.winfo_toplevel().geometry().split('x')[1].split('+')[0]) - 70
            except (tk.TclError, ValueError, IndexError):
                pass
        return 50 < height < 640

    def _on_height(self, _event=None):
        if self.snap is not None and self._check_compact() != self._last_compact:
            self._draw_checklist(self.state())

    def _toggle_check(self):
        self._check_expanded = self._check_compact()      # war kompakt -> jetzt ausgeklappt und umgekehrt
        self._draw_checklist(self.state())

    def _on_width(self, width):
        if abs(width - self._width) < 4:
            return
        self._width = width
        self._place_quick()
        for label, kind in getattr(self, '_wrapping', []):
            try:
                label.configure(wraplength=self._wrap_for(kind, width))
            except tk.TclError:
                pass
        if self.snap is not None and self._sigs.get('cols') != (width >= WIDE):
            self._draw_cards(force=True)

    def _place_quick(self):
        for btn in self.quick_btns:
            btn.grid_forget()
        two_rows = self._width < 880
        for number, btn in enumerate(self.quick_btns):
            row, col = (number // 2, number % 2) if two_rows else (0, number)
            btn.grid(row=row, column=col, sticky='ew', padx=(0 if col == 0 else 8, 0), pady=(0 if row == 0 else 8, 0))
        for col in range(4):
            self.quick.columnconfigure(col, weight=1 if col < (2 if two_rows else 4) else 0, uniform='quick' if col < (2 if two_rows else 4) else '')

    def _sig_changed(self, key, value):
        if self._sigs.get(key) == value:
            return False
        self._sigs[key] = value
        return True

    def _clear(self, frame):
        for child in frame.winfo_children():
            child.destroy()
        self._wrapping = [(w, k) for w, k in getattr(self, '_wrapping', []) if w.winfo_exists()]

    def _panel(self, parent, level_color=None):
        panel = tk.Frame(parent, bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_LINE)
        return panel

    # --- Erste Schritte ---

    def _draw_checklist(self, state):
        visible = homelogic.checklist_visible(self.steps, state)
        compact = self._check_compact()
        self._last_compact = compact
        sig = (visible, compact, tuple((s['key'], s['done'], s['text']) for s in self.steps))
        if not self._sig_changed('check', sig):
            return
        self._clear(self.check_box)
        if not visible:
            self.check_box.grid_remove()
            return
        self.check_box.grid()
        panel = self._panel(self.check_box)
        panel.grid(row=0, column=0, sticky='ew')
        panel.columnconfigure(0, weight=1)
        done = sum(1 for s in self.steps if s['done'])
        top = tk.Frame(panel, bg=CARD_BG)
        top.grid(row=0, column=0, sticky='ew', padx=12, pady=(10, 4))
        top.columnconfigure(0, weight=1)
        tk.Label(top, text=f'Erste Schritte – {done} von {len(self.steps)} erledigt', bg=CARD_BG, font=('Segoe UI', 11, 'bold'),
                 anchor='w').grid(row=0, column=0, sticky='w')
        ttk.Button(top, text='Alle Schritte zeigen' if compact else 'Nur den nächsten zeigen', command=self._toggle_check
                   ).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(top, text='Ausblenden', command=lambda: self.act({'id': 'hide_checklist'})).grid(row=0, column=2)
        first_open = next((s['key'] for s in self.steps if not s['done']), None)
        for number, step in enumerate(self.steps, 1):
            if compact and step['key'] != first_open:
                continue
            row = tk.Frame(panel, bg=CARD_BG)
            row.grid(row=number, column=0, sticky='ew', padx=12, pady=1)
            row.columnconfigure(1, weight=1)
            tk.Label(row, text='✔' if step['done'] else '○', bg=CARD_BG, fg=COLORS['ok'] if step['done'] else COLORS['muted'],
                     font=('Segoe UI', 12, 'bold'), width=2).grid(row=0, column=0, rowspan=2, sticky='n')
            tk.Label(row, text=f'{number}. {step["title"]}', bg=CARD_BG, font=FONT_B if not step['done'] else FONT,
                     fg='#111111' if not step['done'] else COLORS['muted'], anchor='w').grid(row=0, column=1, sticky='w')
            if not step['done']:      # erledigte Schritte brauchen nur eine Zeile
                note = tk.Label(row, text=step['text'], bg=CARD_BG, fg=COLORS['muted'], anchor='w', justify='left', wraplength=self._wrap_for('check'))
                note.grid(row=1, column=1, sticky='w')
                self._wrapping.append((note, 'check'))
            if not step['done']:
                buttons = ttk.Frame(row)
                buttons.grid(row=0, column=2, rowspan=2, sticky='e', padx=(8, 0))
                if step.get('action'):
                    ttk.Button(buttons, text=step['action']['label'], command=lambda a=step['action']: self.act(a)).pack(side='left')
                if step.get('skip'):
                    ttk.Button(buttons, text=step['skip']['label'], command=lambda a=step['skip']: self.act(a)).pack(side='left', padx=(6, 0))
        tk.Frame(panel, bg=CARD_BG, height=6).grid(row=len(self.steps) + 1, column=0)

    # --- Hinweise ---

    def _draw_attention(self, loading=False):
        items = [] if loading else self.items
        sig = (loading, tuple((i['key'], i['level'], i['text'], (i.get('action') or {}).get('label')) for i in items))
        if not self._sig_changed('attn', sig):
            return
        self._clear(self.attn_box)
        title = 'Das braucht deine Aufmerksamkeit' + (f' ({len(items)})' if items else '')
        ttk.Label(self.attn_box, text=title, font=('Segoe UI', 12, 'bold')).grid(row=0, column=0, sticky='w', pady=(0, 6))
        if loading:
            ttk.Label(self.attn_box, text='Wird geladen …', foreground=COLORS['muted']).grid(row=1, column=0, sticky='w')
            return
        if not items:
            panel = self._panel(self.attn_box)
            panel.grid(row=1, column=0, sticky='ew')
            tk.Label(panel, text='✔ Alles in Ordnung. Im Moment musst du nichts tun.', bg=CARD_BG, fg=COLORS['ok'], font=FONT_B,
                     anchor='w').pack(fill='x', padx=12, pady=10)
            return
        for number, item in enumerate(items, 1):
            panel = self._panel(self.attn_box)
            panel.grid(row=number, column=0, sticky='ew', pady=(0, 6))
            panel.columnconfigure(1, weight=1)
            tk.Frame(panel, bg=LEVEL_COLOR[item['level']], width=6).grid(row=0, column=0, sticky='ns')
            inner = tk.Frame(panel, bg=CARD_BG)
            inner.grid(row=0, column=1, sticky='ew', padx=10, pady=6)
            inner.columnconfigure(0, weight=1)
            tk.Label(inner, text=f'{LEVEL_MARK[item["level"]]} {item["title"]}', bg=CARD_BG, font=FONT_B, anchor='w', justify='left',
                     fg='#111111', wraplength=self._wrap_for('attn')).grid(row=0, column=0, sticky='w')
            self._wrapping.append((inner.winfo_children()[-1], 'attn'))
            body = tk.Label(inner, text=item['text'], bg=CARD_BG, fg=COLORS['muted'], anchor='w', justify='left',
                            wraplength=self._wrap_for('attn'))
            body.grid(row=1, column=0, sticky='w')
            self._wrapping.append((body, 'attn'))
            action = item.get('action')
            if action:
                ttk.Button(panel, text=action['label'], command=lambda a=action: self.act(a)).grid(row=0, column=2, padx=(0, 10), pady=6)

    # --- Karten ---

    def _card(self, parent, row, col, title, help_key=None, span=1):
        panel = self._panel(parent)
        panel.grid(row=row, column=col, columnspan=span, sticky='nsew', padx=(0 if col == 0 else 4, 0 if col + span >= 2 else 4), pady=(0, 8))
        panel.columnconfigure(0, weight=1)
        head = tk.Frame(panel, bg=CARD_BG)
        head.grid(row=0, column=0, sticky='ew', padx=12, pady=(8, 2))
        head.columnconfigure(1, weight=1)
        tk.Label(head, text=title, bg=CARD_BG, fg=COLORS['muted'], font=('Segoe UI', 9, 'bold'), anchor='w').grid(row=0, column=0, sticky='w')
        if help_key:
            HelpDot(head, help_key).grid(row=0, column=1, sticky='w', padx=(2, 0))
        body = tk.Frame(panel, bg=CARD_BG)
        body.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 10))
        body.columnconfigure(0, weight=1)
        return body

    def _value(self, body, text, kind='muted', row=0, size=15):
        color = COLORS.get(kind, '#111111') if kind != 'plain' else '#111111'
        label = tk.Label(body, text=text, bg=CARD_BG, fg=color, font=('Segoe UI', size, 'bold'), anchor='w', justify='left')
        label.grid(row=row, column=0, sticky='w')
        return label

    def _note(self, body, text, row, color=None):
        label = tk.Label(body, text=text, bg=CARD_BG, fg=color or COLORS['muted'], anchor='w', justify='left', wraplength=self._wrap_for('card'))
        label.grid(row=row, column=0, sticky='w')
        self._wrapping.append((label, 'card'))
        return label

    def _card_button(self, body, spec, row=0):
        if spec:
            ttk.Button(body, text=spec[1], command=lambda a=spec[0]: self.act({'id': a})).grid(row=row, column=1, sticky='e', padx=(8, 0))

    def _draw_cards(self, force=False):
        wide = self._width >= WIDE
        cards = self.cards
        sig = (wide, repr(cards))
        if not force and not self._sig_changed('cards', sig):
            return
        self._sigs['cols'] = wide
        self._sigs['cards'] = sig
        self._clear(self.cards_box)
        if not cards:
            return
        self.cards_box.columnconfigure(1, weight=1 if wide else 0, uniform='card' if wide else '')
        positions = iter(range(100))

        def at():
            n = next(positions)
            return (n // 2, n % 2) if wide else (n, 0)

        fmt = homelogic._fmt_n
        svc = cards['service']
        body = self._card(self.cards_box, *at(), 'HINTERGRUNDDIENST', 'daemon')
        self._value(body, svc['value'], svc['kind'])
        self._card_button(body, svc.get('button'))
        self._note(body, svc['detail'], 1, COLORS['warn'] if svc['kind'] == 'warn' else None)

        px = cards['proxy']
        body = self._card(self.cards_box, *at(), 'VERBINDUNG (PROXY-MODUS)', 'proxy_mode')
        self._value(body, px['value'], px['kind'])
        self._card_button(body, px.get('button'))
        self._note(body, px['detail'], 1, COLORS['warn'] if px['kind'] == 'warn' else None)

        ac = cards['accounts']
        body = self._card(self.cards_box, *at(), 'KONTEN', 'cooldown')
        if ac['rows']:
            for number, text in enumerate(ac['rows']):
                warn = text.startswith('Gesperrt') or 'nicht angemeldet' in text or 'Pause' in text
                self._note(body, text, number, COLORS['warn'] if warn else '#111111')
        else:
            self._value(body, ac['value'], ac['kind'])
            self._note(body, ac['detail'], 1)
        self._card_button(body, ac.get('button'))

        lr = cards['last_run']
        body = self._card(self.cards_box, *at(), 'LETZTER LAUF')
        self._value(body, lr['value'], lr['kind'])
        self._note(body, lr['detail'], 1)

        d = cards['data']
        body = self._card(self.cards_box, *at(), 'DATEN')
        self._value(body, f'{fmt(d["videos"])} Videos · {fmt(d["comments"])} Kommentare', 'plain', size=12)
        self._note(body, f'{fmt(d["people"])} Personen (Kommentarschreiber)', 1)
        self._card_button(body, d.get('button'))

        body = self._card(self.cards_box, *at(), 'NEU IN DEN LETZTEN 24 STUNDEN', 'new_author')
        self._value(body, f'{fmt(d["new_comments"])} Kommentare', 'plain', size=12)
        self._note(body, f'{fmt(d["new_authors"])} neue Autoren · {fmt(d["new_videos"])} neue Videos', 1)

        ch = cards['channels']
        body = self._card(self.cards_box, 3 if wide else 6, 0, 'KANÄLE IN ARBEIT', 'channel', span=2 if wide else 1)
        if not ch['items']:
            text = 'Kein Kanal in Arbeit.' if not ch['total'] else f'Alle {ch["total"]} Kanäle sind fertig oder beobachten neue Videos.'
            self._note(body, text, 0)
        for number, item in enumerate(ch['items']):
            line = tk.Frame(body, bg=CARD_BG)
            line.grid(row=number, column=0, sticky='ew', pady=(0, 6))
            line.columnconfigure(1, weight=1)
            tk.Label(line, text=item['name'], bg=CARD_BG, font=FONT_B, anchor='w').grid(row=0, column=0, columnspan=3, sticky='w')
            bar = ttk.Progressbar(line, mode='determinate', maximum=100, value=round(item['frac'] * 100), length=160)
            bar.grid(row=1, column=0, sticky='w', pady=(2, 0))
            tk.Label(line, text=f'{round(item["frac"] * 100)} %  ·  {item["count"]}', bg=CARD_BG, fg=COLORS['muted'], anchor='w').grid(
                row=1, column=1, sticky='w', padx=(8, 0))
            tk.Label(line, text=' · '.join(x for x in (item['state'], item['next']) if x), bg=CARD_BG,
                     fg=COLORS['warn'] if item['kind'] == 'warn' else COLORS['muted'], anchor='w').grid(row=2, column=0, columnspan=3, sticky='w')
        if ch['items']:
            self._card_button(body, ch.get('button'), row=len(ch['items']))
        if ch['watching']:
            self._note(body, f'{ch["watching"]} weitere beobachten neue Videos.', len(ch['items']) + 1)
