"""Kleine wiederverwendbare Bausteine für die Tabs: Dialog, Hinweisfenster, Farbpunkte, Verlaufsgrafik."""
from .background import run_background
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .. import repo
from .common import COLORS, FONT, FONT_B, track_after
from .logic import chart_layout, local_stamp

PAD = 10   # Standardabstand im ganzen Fenster
from .theme import PALETTE
ACCENT = PALETTE['accent']


def pad_label(text):
    """Beschriftung für Auswahlkästchen und Rundknöpfe: zwei Leerzeichen dahinter, sonst schneidet das Windows-Design
    das letzte Zeichen ab."""
    return text if text.endswith('  ') else text + '  '


def bind_wrap(label, container, margin=24, minimum=120):
    """Der Text von `label` bricht passend zur Breite von `container` um (statt am Rand abgeschnitten zu werden)."""
    def fit(event=None):
        width = (event.width if event is not None else container.winfo_width()) - margin
        label.configure(wraplength=max(width, minimum))
    container.bind('<Configure>', fit, add='+')


def center_over(win, parent):
    """Fenster mittig über das Hauptfenster (das Fenster, in dem `parent` liegt) setzen; bleibt auf dem Bildschirm."""
    win.update_idletasks()
    try:
        top = parent.winfo_toplevel()
        if not top.winfo_viewable():
            return False
        w, h = max(win.winfo_width(), win.winfo_reqwidth()), max(win.winfo_height(), win.winfo_reqheight())
        # Gemittet wird der Inhalt; `geometry` setzt aber den äußeren Rahmen (Titelleiste, Rand): den Unterschied abziehen
        # (bekannt, sobald das Fenster einmal gezeigt wurde).
        edge, title = win.winfo_rootx() - win.winfo_x(), win.winfo_rooty() - win.winfo_y()
        x = top.winfo_rootx() + (top.winfo_width() - w) // 2 - edge
        y = top.winfo_rooty() + (top.winfo_height() - h) // 2 - title
        x = min(max(x, 0), max(win.winfo_screenwidth() - w, 0))
        y = min(max(y, 0), max(win.winfo_screenheight() - h - 40, 0))
        win.geometry(f'+{x}+{y}')
        return True
    except tk.TclError:
        return False


def show_centered(win, parent):
    """Fenster zeigen und mittig über das Hauptfenster setzen, ohne dass es sichtbar springt."""
    try:
        before = win.attributes('-alpha')
    except tk.TclError:
        before = 1.0
    try:
        win.attributes('-alpha', 0.0)
    except tk.TclError:
        pass
    center_over(win, parent)
    win.deiconify()
    win.update_idletasks()
    center_over(win, parent)   # jetzt sind Titelleiste und Rand bekannt
    win.update_idletasks()
    try:
        win.attributes('-alpha', before)
    except tk.TclError:
        pass


def shorten_middle(text, font, max_px):
    """Kürzt `text` in der Mitte mit „…“, bis er in `max_px` Bildpunkte passt (Anfang und Ende bleiben lesbar)."""
    if max_px <= 0 or font.measure(text) <= max_px:
        return text
    lo, hi = 0, len(text)   # größte Zahl erhaltener Zeichen, die noch passt
    while lo < hi:
        mid = (lo + hi + 1) // 2
        head, tail = (mid + 1) // 2, mid // 2
        cand = text[:head] + '…' + (text[len(text) - tail:] if tail else '')
        if font.measure(cand) <= max_px:
            lo = mid
        else:
            hi = mid - 1
    head, tail = (lo + 1) // 2, lo // 2
    return text[:head] + '…' + (text[len(text) - tail:] if tail else '')


class PathLabel(ttk.Label):
    """Label für lange Pfade: passt sich der Breite an, kürzt in der Mitte und zeigt den ganzen Pfad als Hinweis."""

    def __init__(self, master, **kw):
        kw.setdefault('width', 8)   # nur eine kleine Wunschbreite, den Rest gibt das Layout
        super().__init__(master, **kw)
        self.full, self.suffix = '', ''
        self._font = tkfont.Font(font=FONT)
        self.tip = Tooltip(self, '')
        self.bind('<Configure>', lambda _e: self._shorten(), add='+')

    def set(self, text, suffix=''):
        """`text` ist der Pfad (wird bei Platzmangel in der Mitte gekürzt), `suffix` bleibt immer ganz stehen (z. B. die Größe)."""
        self.full, self.suffix = text, suffix
        self.tip.text = text
        self._shorten()

    def _shorten(self):
        width = self.winfo_width()
        if width <= 1:
            shown = self.full + self.suffix
        else:
            shown = shorten_middle(self.full, self._font, width - 6 - self._font.measure(self.suffix)) + self.suffix
        if shown != self.cget('text'):
            self.configure(text=shown)


# ---------- Spalten, die zum Inhalt passen ----------

class ColumnFitter:
    """Spaltenbreiten einer Tabelle passend zum Inhalt (statt fester Zahlen).

    specs: {Spalte: (Mindestbreite, Gewicht)}. Gewicht 0 = feste Breite aus Kopf und Inhalt – so wird eine Zahl nie
    abgeschnitten; Gewicht > 0 = flexibel, bekommt den Rest nach Gewicht (nie unter der Mindestbreite).
    Reicht der Platz auch dann nicht, erscheint unten eine Bildlaufleiste (`hscroll`), nichts wird versteckt.
    Ist der Text einer Zelle trotzdem breiter als ihre Spalte (er wird mit „…“ gekürzt), zeigt ein Hinweis beim Darüberfahren
    den vollen Text."""

    PAD = 16          # Rand links/rechts in der Zelle
    ARROW = 16        # Platz für den Sortierpfeil im Spaltenkopf
    SAMPLE = 1500     # höchstens so viele Zeilen werden gemessen

    def __init__(self, tree, specs, hscroll=None, cap=420, arrow=None):
        self.tree, self.specs, self.hscroll, self.cap = tree, dict(specs), hscroll, cap
        self.arrow = self.ARROW if arrow is None else arrow   # nur sortierbare Tabellen brauchen Platz für den Pfeil
        self.font = tkfont.Font(font=FONT)
        self.measured = {}
        if hscroll is not None:
            tree.configure(xscrollcommand=self._xset)
        for key in self.specs:
            tree.column(key, stretch=False, minwidth=self.specs[key][0])
        tree.bind('<Configure>', lambda _e: self.layout(), add='+')
        self.tip, self._tip_after, self._tip_cell = None, None, None
        tree.bind('<Motion>', self._motion, add='+')
        tree.bind('<Leave>', self._tip_hide, add='+')
        tree.bind('<ButtonPress>', self._tip_hide, add='+')
        tree.bind('<MouseWheel>', self._tip_hide, add='+')
        tree.bind('<Destroy>', self._tip_hide, add='+')
        self.refit()

    # ---------- voller Text bei gekürzten Zellen ----------

    def cell_text(self, x, y):
        """(Zelle, voller Text) unter dem Mauszeiger, wenn der Text breiter ist als die Spalte; sonst (None, '')."""
        tree = self.tree
        if tree.identify_region(x, y) != 'cell':
            return None, ''
        row, col = tree.identify_row(y), tree.identify_column(x)
        shown = tree.cget('displaycolumns')
        shown = list(shown) if not isinstance(shown, str) else [shown]
        names = list(tree.cget('columns')) if shown in (['#all'], ['all']) or not shown else shown
        try:
            key = names[int(col.lstrip('#')) - 1]
        except (ValueError, IndexError):
            return None, ''
        if not row or key in ('#0',):
            return None, ''
        try:
            text = str(tree.set(row, key))
            width = int(tree.column(key, 'width'))
        except tk.TclError:
            return None, ''
        if text and self.font.measure(text) + self.PAD > width:
            return (row, key), text
        return None, ''

    def _motion(self, event):
        cell, text = self.cell_text(event.x, event.y)
        if cell == self._tip_cell and (cell is None or self.tip is not None or self._tip_after is not None):
            return
        self._tip_hide()
        self._tip_cell = cell
        if cell is not None:
            self._tip_after = self.tree.after(500, lambda: self._tip_show(text, event.x_root, event.y_root))

    def _tip_show(self, text, x, y):
        self._tip_after = None
        if self.tip is not None:
            return
        try:
            self.tip = tk.Toplevel(self.tree)
            self.tip.wm_overrideredirect(True)
            self.tip.wm_geometry(f'+{x + 12}+{y + 16}')
            tk.Label(self.tip, text=text, justify='left', background='#ffffe1', relief='solid', borderwidth=1,
                     wraplength=520, padx=6, pady=3).pack()
        except tk.TclError:
            self.tip = None

    def _tip_hide(self, _event=None):
        if self._tip_after is not None:
            try:
                self.tree.after_cancel(self._tip_after)
            except tk.TclError:
                pass
            self._tip_after = None
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None
        self._tip_cell = None

    def _xset(self, first, last):
        self.hscroll.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.hscroll.grid_remove()
        else:
            self.hscroll.grid()

    def refit(self):
        """Nach neuem Inhalt: Breiten neu messen und verteilen."""
        tree, font = self.tree, self.font
        rows = tree.get_children()[:self.SAMPLE]
        for key in self.specs:
            head = tree.heading(key, 'text') or ''
            need = font.measure(head) + self.arrow
            for iid in rows:
                text = str(tree.set(iid, key))
                if text:
                    need = max(need, font.measure(text))
            self.measured[key] = int(need) + self.PAD
        self.layout()

    def layout(self):
        tree = self.tree
        avail = tree.winfo_width()
        if avail <= 1:
            avail = max(tree.winfo_reqwidth(), 1)
        show = tree.cget('show')
        show = show.split() if isinstance(show, str) else [str(part) for part in show]
        if 'tree' in show:   # die Baumspalte #0 (Symbol/Einrückung) nimmt auch Platz
            avail -= int(tree.column('#0', 'width'))
        fixed = {k: self.measured.get(k, s[0]) for k, s in self.specs.items() if s[1] == 0}
        flex = {k: s for k, s in self.specs.items() if s[1] > 0}
        widths = dict(fixed)
        room = avail - sum(fixed.values()) - 2
        mins = {k: s[0] for k, s in flex.items()}
        want = {k: min(max(self.measured.get(k, mins[k]), mins[k]), self.cap) for k in flex}
        if not flex:
            pass
        elif room >= sum(want.values()):
            extra = room - sum(want.values())
            total = sum(s[1] for s in flex.values())
            for k, s in flex.items():
                widths[k] = want[k] + int(extra * s[1] / total)
        elif room >= sum(mins.values()):
            spare, span = room - sum(mins.values()), max(sum(want.values()) - sum(mins.values()), 1)
            for k in flex:
                widths[k] = mins[k] + int(spare * (want[k] - mins[k]) / span)
        else:
            widths.update(mins)
        for key, width in widths.items():
            if int(tree.column(key, 'width')) != int(width):
                tree.column(key, width=int(width))


# ---------- Hinweisfenster (sparsam benutzen) ----------

class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.tip, self._after = widget, text, None, None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, _e=None):
        self._hide()
        self._after = self.widget.after(600, self._show)

    def _show(self):
        if self.tip is not None:
            return
        try:
            x, y = self.widget.winfo_rootx() + 12, self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            self.tip = tk.Toplevel(self.widget)
            self.tip.wm_overrideredirect(True)
            self.tip.wm_geometry(f'+{x}+{y}')
            tk.Label(self.tip, text=self.text, justify='left', background='#ffffe1', relief='solid', borderwidth=1,
                     wraplength=320, padx=6, pady=3).pack()
        except tk.TclError:
            self.tip = None

    def _hide(self, _e=None):
        if self._after is not None:
            try: self.widget.after_cancel(self._after)
            except tk.TclError: pass
            self._after = None
        if self.tip is not None:
            try: self.tip.destroy()
            except tk.TclError: pass
            self.tip = None


# ---------- Farbpunkte für Gruppen ----------

def dot_image(master, color, size=12, gap=5):
    """Kleiner ausgefüllter Kreis als Bild (für Listen); rechts bleibt ein Abstand frei. Je Farbe nur einmal gebaut."""
    cache = master.__dict__.setdefault('_dot_cache', {})
    if color not in cache:
        img = tk.PhotoImage(master=master, width=size + gap, height=size)
        r = size / 2 - 0.5
        for x in range(size):
            for y in range(size):
                if (x - r) ** 2 + (y - r) ** 2 <= (size / 2 - 0.5) ** 2:
                    img.put(color, (x, y))
        cache[color] = img
    return cache[color]


# ---------- Dialog mit Eingabefeldern ----------

class FormDialog(tk.Toplevel):
    """Kleiner Dialog mit Eingabefeldern. `run()` zeigt ihn und gibt ein Wörterbuch der Werte zurück (oder None).

    Felder (Liste von Wörterbüchern): key, label, kind = 'text' | 'choice' | 'radio' | 'color' | 'info',
    value (Vorgabe), choices = [(wert, anzeige), …], hint (graue Zeile), editable (nur choice: Eintippen erlaubt).
    `validator(werte)` gibt einen Fehlertext zurück (Dialog bleibt offen) oder None.
    """

    def __init__(self, parent, title, fields, intro='', ok_text='OK', validator=None, suggestion=None):
        super().__init__(parent)
        self.withdraw()   # erst zeigen, wenn es mittig über dem Hauptfenster steht (kein Springen)
        self.title(title)
        self.resizable(False, False)
        self.validator, self.result = validator, None
        self.vars, self._choice_map, self._color = {}, {}, {}
        body = ttk.Frame(self, padding=22)
        body.grid(row=0, column=0, sticky='nsew')
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text=title, style='Section.TLabel', wraplength=480).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 14))
        row = 1
        if intro:
            ttk.Label(body, text=intro, wraplength=420, justify='left').grid(row=row, column=0, columnspan=2, sticky='w', pady=(0, 10))
            row += 1
        self.first = None
        for f in fields:
            key, kind = f.get('key'), f.get('kind', 'text')
            if kind == 'info':
                ttk.Label(body, text=f['label'], wraplength=420, justify='left', foreground=COLORS['muted']
                          ).grid(row=row, column=0, columnspan=2, sticky='w', pady=(0, 6)); row += 1
                continue
            ttk.Label(body, text=f.get('label', '')).grid(row=row, column=0, sticky='nw', padx=(0, 10), pady=4)
            if kind == 'text':
                var = tk.StringVar(value=f.get('value', ''))
                w = ttk.Entry(body, textvariable=var, width=34, show=f.get('show', ''))
                w.grid(row=row, column=1, sticky='ew', pady=4)
            elif kind == 'choice':
                choices = f['choices']
                self._choice_map[key] = {label: value for value, label in choices}
                current = next((label for value, label in choices if value == f.get('value')), choices[0][1] if choices else '')
                var = tk.StringVar(value=current)
                w = ttk.Combobox(body, textvariable=var, values=[label for _v, label in choices], width=32,
                                 state='normal' if f.get('editable') else 'readonly')
                w.grid(row=row, column=1, sticky='ew', pady=4)
            elif kind == 'radio':
                var = tk.StringVar(value=f.get('value', f['choices'][0][0]))
                w = ttk.Frame(body)
                w.grid(row=row, column=1, sticky='w', pady=4)
                for value, label in f['choices']:
                    ttk.Radiobutton(w, text=pad_label(label), value=value, variable=var).pack(anchor='w')
            elif kind == 'color':
                var = tk.StringVar(value=f.get('value') or repo.DEFAULT_COLORS[0])
                w = tk.Canvas(body, height=28, width=len(repo.DEFAULT_COLORS) * 32, highlightthickness=0)
                w.grid(row=row, column=1, sticky='w', pady=4)
                self._color[key] = w
                self._draw_colors(w, var)
                w.bind('<Button-1>', lambda e, c=w, v=var: self._pick_color(e, c, v))
            else:
                raise ValueError(f'Unbekannte Feldart: {kind}')
            self.vars[key] = var
            if self.first is None and kind in ('text', 'choice'):
                self.first = w
            row += 1
            if f.get('hint'):
                ttk.Label(body, text=f['hint'], foreground=COLORS['muted'], wraplength=300, justify='left'
                          ).grid(row=row, column=1, sticky='w'); row += 1
        self.suggestion_btn = None
        if suggestion is not None:
            label, callback = suggestion
            self.suggestion_btn = ttk.Button(body, text=label, command=lambda: self.apply_suggestion(callback))
            self.suggestion_btn.grid(row=row, column=0, columnspan=2, sticky='w', pady=(10, 0))
            row += 1
        self.error = ttk.Label(body, text='', foreground=COLORS['err'], wraplength=420, justify='left')
        self.error.grid(row=row, column=0, columnspan=2, sticky='w', pady=(6, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky='e', pady=(12, 0))
        self.ok_btn = ttk.Button(buttons, text=ok_text, command=self.accept, style='Big.TButton')
        self.ok_btn.pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='Abbrechen', command=self.cancel).pack(side='left')
        self.bind('<Return>', self.accept)
        self.bind('<Escape>', self.cancel)
        self.protocol('WM_DELETE_WINDOW', self.cancel)

    def _draw_colors(self, canvas, var):
        canvas.delete('all')
        for i, color in enumerate(repo.DEFAULT_COLORS):
            x = 4 + i * 32
            chosen = var.get().lower() == color.lower()
            canvas.create_oval(x, 4, x + 22, 26, fill=color, outline='#111827' if chosen else color, width=3 if chosen else 1,
                               tags=(f'c{i}',))

    def _pick_color(self, event, canvas, var):
        i = (event.x - 4) // 32
        if 0 <= i < len(repo.DEFAULT_COLORS):
            var.set(repo.DEFAULT_COLORS[i])
            self._draw_colors(canvas, var)

    def values(self):
        out = {}
        for key, var in self.vars.items():
            raw = var.get()
            out[key] = self._choice_map[key].get(raw, raw) if key in self._choice_map else raw
        return out

    def apply_suggestion(self, callback):
        """Populate a draft only; validate all keys/choices before changing fields."""
        try:
            updates = callback(self.values())
            if not isinstance(updates, dict) or set(updates) - set(self.vars):
                raise ValueError('Die Vorschläge passen nicht zu diesem Formular.')
            displayed = {}
            for key, value in updates.items():
                if key in self._choice_map:
                    labels = [label for label, candidate in self._choice_map[key].items() if candidate == value]
                    if not labels:
                        raise ValueError('Ein Vorschlag passt nicht zur angebotenen Auswahl.')
                    value = labels[0]
                displayed[key] = value
        except ValueError as error:
            self.error.configure(text=str(error))
            return
        for key, value in displayed.items():
            self.vars[key].set(value)
            if key in self._color:
                self._draw_colors(self._color[key], self.vars[key])
        self.error.configure(text='')

    def accept(self, _event=None):
        if _event is not None and _event.widget is self.suggestion_btn:
            self.suggestion_btn.invoke()
            return 'break'
        values = self.values()
        if self.validator is not None:
            problem = self.validator(values)
            if problem:
                self.error.configure(text=problem)
                return
        self.result = values
        self.destroy()

    def cancel(self, _event=None):
        self.result = None
        self.destroy()

    def run(self):
        """Dialog mittig über dem Fenster zeigen, warten, Ergebnis zurückgeben."""
        parent = self.master
        self.update_idletasks()
        try:
            self.transient(parent.winfo_toplevel())
            show_centered(self, parent)
            self.grab_set()   # modal: das Hauptfenster bleibt gesperrt, bis der Dialog zu ist
        except tk.TclError:
            pass
        if self.first is not None:
            self.first.focus_set()
            try: self.first.selection_range(0, 'end')
            except (tk.TclError, AttributeError): pass
        else:
            self.ok_btn.focus_set()    # Dialog ohne Eingabefeld: Enter bestätigt, Esc bricht ab
        self.wait_window()
        return self.result


def ask_form(parent, title, fields, intro='', ok_text='OK', validator=None, suggestion=None):
    """Dialog zeigen und Werte (Wörterbuch) oder None bei „Abbrechen“ zurückgeben."""
    return FormDialog(parent, title, fields, intro=intro, ok_text=ok_text, validator=validator, suggestion=suggestion).run()


# ---------- Spaltenköpfe zum Sortieren ----------

class SortHeaders:
    """Klick auf einen Spaltenkopf sortiert; ein Pfeil zeigt Richtung. `on_change()` lädt neu."""

    NUMERIC = ('views', 'likes', 'comments', 'date', 'fetched', 'length')   # diese Spalten sortieren beim ersten Klick absteigend

    def __init__(self, tree, titles, on_change, state, numeric=None):
        self.tree, self.titles, self.on_change = tree, titles, on_change
        self.numeric = self.NUMERIC if numeric is None else tuple(numeric)
        self.state = state   # {'key': …, 'desc': …}
        for key in titles:
            tree.heading(key, text=titles[key], command=lambda k=key: self.click(k))
        self.mark()

    def click(self, key):
        if self.state['key'] == key:
            self.state['desc'] = not self.state['desc']
        else:
            self.state['key'], self.state['desc'] = key, key in self.numeric
        self.mark()
        self.on_change()

    def mark(self):
        for key, title in self.titles.items():
            arrow = (' ▼' if self.state['desc'] else ' ▲') if key == self.state['key'] else ''
            self.tree.heading(key, text=title + arrow)


# ---------- Verlaufsgrafik ----------

class LineChart(tk.Canvas):
    """Einfache Linie mit Punkten, ohne zusätzliche Pakete. `show(werte, bezeichnung, leertext)`."""

    def __init__(self, master, **kw):
        kw.setdefault('background', '#ffffff')
        kw.setdefault('highlightthickness', 1)
        kw.setdefault('highlightbackground', '#d0d7de')
        super().__init__(master, **kw)
        self._data = ([], '', '')
        self.bind('<Configure>', lambda _e: self._draw())

    def show(self, values, label='', empty_text='Noch kein Verlauf.'):
        self._data = (list(values), label, empty_text)
        self._draw()

    def _draw(self):
        self.delete('all')
        values, label, empty = self._data
        w, h = max(self.winfo_width(), 10), max(self.winfo_height(), 10)
        pts = chart_layout(values, w, h)
        if not pts:
            self.create_text(w / 2, h / 2, text=empty, fill=COLORS['muted'], width=max(w - 40, 80), justify='center', font=FONT_B)
            return
        left, top, right, bottom = 46, 14, 14, 26
        for frac in (0, 0.5, 1):   # leichte Hilfslinien
            y = top + (h - top - bottom) * frac
            self.create_line(left, y, w - right, y, fill='#eaeef2')
        lo, hi = min(p[3] for p in pts), max(p[3] for p in pts)
        fmt = lambda v: f'{int(v):,}'.replace(',', '.')
        self.create_text(left - 6, top, text=fmt(hi), anchor='e', fill=COLORS['muted'], font=('Segoe UI', 8))
        if hi != lo:
            self.create_text(left - 6, h - bottom, text=fmt(lo), anchor='e', fill=COLORS['muted'], font=('Segoe UI', 8))
        if len(pts) > 1:
            self.create_line(*[c for p in pts for c in p[:2]], fill=ACCENT, width=2, smooth=False)
        for x, y, text, value in pts:
            self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=ACCENT, outline='#ffffff')
        self.create_text(pts[0][0], h - 10, text=local_stamp(pts[0][2]), anchor='w' if len(pts) > 1 else 'center',
                         fill=COLORS['muted'], font=('Segoe UI', 8))
        if len(pts) > 1:
            self.create_text(pts[-1][0], h - 10, text=local_stamp(pts[-1][2]), anchor='e',
                             fill=COLORS['muted'], font=('Segoe UI', 8))
        if label:
            count = len(pts)
            self.create_text(w - right - 8, 8, text=f'{label}: jetzt {fmt(pts[-1][3])}  ·  {count} Messpunkt' + ('e' if count != 1 else ''),
                             anchor='ne', fill=COLORS['muted'], font=('Segoe UI', 8))


# ---------- Stimmung (KI-Einschätzung) ----------

MOOD_COLORS = {'positiv': '#1a7f37', 'neutral': '#8c959f', 'negativ': '#cf222e'}
MOOD_NAMES = {'positiv': 'Positiv', 'neutral': 'Neutral', 'negativ': 'Negativ'}


def percent(n, total):
    return f'{round(100 * n / total)} %' if total else '0 %'


class SentimentBar(tk.Canvas):
    """Verteilungsbalken Positiv (grün) / Neutral (grau) / Negativ (rot) mit Zahlen und Prozent.

    Immer als „KI-Einschätzung“ beschriftet: es ist eine Schätzung, nicht gemessen. `show(dist, caption)` mit dist wie
    sentiment_repo.distribution (positive, neutral, negative, analyzed, total_comments)."""

    def __init__(self, master, **kw):
        kw.setdefault('height', 46)
        kw.setdefault('background', '#ffffff')
        kw.setdefault('highlightthickness', 1)
        kw.setdefault('highlightbackground', '#d0d7de')
        super().__init__(master, **kw)
        self._data = (None, '')
        self._hint = None
        self.bind('<Configure>', lambda _e: self._draw())

    def show(self, dist, caption='', hint=None):
        self._data = (dict(dist) if dist else None, caption)
        self._hint = hint
        self._draw()

    def _draw(self):
        self.delete('all')
        dist, caption = self._data
        w = max(self.winfo_width(), 60)
        head = 'KI-Einschätzung der Stimmung' + (f' · {caption}' if caption else '')
        self.create_text(8, 4, text=head, anchor='nw', fill=COLORS['muted'], font=('Segoe UI', 8, 'bold'), width=w - 16)
        analyzed = (dist or {}).get('analyzed') or 0
        if not analyzed:
            total = (dist or {}).get('total_comments') or 0
            text = self._hint or ('Noch nicht bestimmt. Klicke auf „Stimmung bestimmen …“.' if total else 'Keine Kommentare in dieser Auswahl.')
            self.create_text(8, 32, text=text, anchor='w', fill=COLORS['muted'], font=('Segoe UI', 9), width=w - 16)
            return
        x, left, right, top, bottom = 8, 8, w - 8, 18, 30
        span = max(right - left, 1)
        counts = [(k, dist[{'positiv': 'positive', 'neutral': 'neutral', 'negativ': 'negative'}[k]]) for k in ('positiv', 'neutral', 'negativ')]
        for key, n in counts:
            if not n:
                continue
            width = span * n / analyzed
            self.create_rectangle(x, top, x + width, bottom, fill=MOOD_COLORS[key], outline='')
            x += width
        parts = [f'{MOOD_NAMES[k]} {n} ({percent(n, analyzed)})' for k, n in counts]
        total = dist.get('total_comments') or analyzed
        legend = '   ·   '.join(parts) + f'   ·   eingestuft: {analyzed} von {total}'
        lx = 8
        self.create_text(lx, 38, text=legend, anchor='w', fill='#24292f', font=('Segoe UI', 8), width=w - 16)


class SentimentChart(tk.Canvas):
    """Kleiner Stimmungsverlauf: Anteil positiv (grün) und negativ (rot) je Tag/Woche, 0 bis 100 %. KI-Einschätzung."""

    def __init__(self, master, **kw):
        kw.setdefault('background', '#ffffff')
        kw.setdefault('highlightthickness', 1)
        kw.setdefault('highlightbackground', '#d0d7de')
        super().__init__(master, **kw)
        self._data = ([], '')
        self.points = []
        self.bind('<Configure>', lambda _e: self._draw())

    def show(self, series, empty_text='Noch keine Stimmung bestimmt.'):
        self._data = (list(series), empty_text)
        self._draw()

    def _draw(self):
        self.delete('all')
        series, empty = self._data
        self.points = []
        w, h = max(self.winfo_width(), 10), max(self.winfo_height(), 10)
        if not series:
            self.create_text(w / 2, h / 2, text=empty, fill=COLORS['muted'], width=max(w - 40, 80), justify='center', font=FONT_B)
            return
        left, top, right, bottom = 46, 18, 14, 26
        pw, ph = max(w - left - right, 1), max(h - top - bottom, 1)
        for frac, label in ((0, '100 %'), (0.5, '50 %'), (1, '0 %')):
            y = top + ph * frac
            self.create_line(left, y, w - right, y, fill='#eaeef2')
            self.create_text(left - 6, y, text=label, anchor='e', fill=COLORS['muted'], font=('Segoe UI', 8))
        n = len(series)
        xs = [left + pw / 2] if n == 1 else [left + pw * i / (n - 1) for i in range(n)]
        for key, color in (('positive', MOOD_COLORS['positiv']), ('negative', MOOD_COLORS['negativ'])):
            coords = []
            for x, row in zip(xs, series):
                share = (row.get('share') or {}).get(key)
                if share is None:
                    continue
                coords.append((x, top + ph * (1 - share)))
            if len(coords) > 1:
                self.create_line(*[c for p in coords for c in p], fill=color, width=2)
            for x, y in coords:
                self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline='#ffffff')
        self.points = list(zip(xs, series))
        self.create_text(xs[0], h - 10, text=series[0]['date'], anchor='w' if n > 1 else 'center', fill=COLORS['muted'], font=('Segoe UI', 8))
        if n > 1:
            self.create_text(xs[-1], h - 10, text=series[-1]['date'], anchor='e', fill=COLORS['muted'], font=('Segoe UI', 8))
        total = sum(r['analyzed'] for r in series)
        self.create_text(left, 6, anchor='w', fill=COLORS['muted'], font=('Segoe UI', 8),
                         text=f'KI-Einschätzung · Anteil je Tag: grün positiv, rot negativ · {total} eingestufte Kommentare, {n} Messpunkt' + ('e' if n != 1 else ''))


# ---------- Zeile aus Bedienelementen, die umbricht ----------

class FlowBar(ttk.Frame):
    """Zeile von Bedienelementen, die bei Platzmangel in die nächste Zeile umbricht (statt rechts abgeschnitten zu werden).

    `add(widget, right=False)`: das Widget muss `self` als Elternteil haben. Elemente mit right=True stehen rechtsbündig
    am Ende der letzten Zeile. Jede Zeile ist ein eigener Rahmen, in den die Elemente gepackt werden."""

    def __init__(self, master, gap=6, **kw):
        super().__init__(master, **kw)
        self.gap, self.items, self._plan, self._rows = gap, [], None, []
        self.hidden = set()
        self.columnconfigure(0, weight=1)
        self.bind('<Configure>', lambda _e: self.layout())

    def add(self, widget, right=False):
        self.items.append((widget, right))
        self._plan = None
        self.after_idle(self.layout)
        return widget

    def set_visible(self, widget, visible):
        """Blendet ein Element aus oder ein (der Platz wird neu verteilt)."""
        if (widget not in self.hidden) == bool(visible):
            return
        self.hidden.discard(widget) if visible else self.hidden.add(widget)
        self._plan = None
        self.layout()

    def layout(self):
        width = self.winfo_width()
        if width <= 1:
            return
        rows, cur, used = [], [], 0
        lefts = [w for w, r in self.items if not r and w not in self.hidden]
        rights = [w for w, r in self.items if r and w not in self.hidden]
        for w in lefts:
            need = w.winfo_reqwidth() + self.gap
            if cur and used + need > width:
                rows.append(cur)
                cur, used = [], 0
            cur.append(w)
            used += need
        right_need = sum(w.winfo_reqwidth() + self.gap for w in rights)
        if rights and cur and used + right_need > width:
            rows.append(cur)
            cur = []
        rows.append(cur)
        plan = (width, tuple(tuple(id(w) for w in row) for row in rows))
        if plan == self._plan:
            return
        self._plan = plan
        for w, _r in self.items:
            w.pack_forget()
        for frame in self._rows:
            frame.destroy()
        self._rows = []
        for r, row in enumerate(rows):
            frame = ttk.Frame(self)
            frame.grid(row=r, column=0, sticky='ew')
            self._rows.append(frame)
            for w in row:
                w.pack(in_=frame, side='left', padx=(0, self.gap), pady=1)
                w.lift()
            if r == len(rows) - 1:
                for w in reversed(rights):
                    w.pack(in_=frame, side='right', padx=(self.gap, 0), pady=1)
                    w.lift()


# ---------- Kleine „?“-Hilfe an Fachwörtern ----------

class HelpDot(ttk.Label):
    """Kleiner „?“-Punkt neben einem Fachwort: Hinweis beim Darüberfahren, Klick (oder Enter/Leertaste) zeigt die Erklärung.

    `key` ist ein Begriff aus helptexts.GLOSSARY; alternativ `title` und `text` selbst angeben."""

    def __init__(self, master, key=None, title=None, text=None, **kw):
        from .. import helptexts
        if key is not None:
            title, text = helptexts.GLOSSARY[key]
        self.title_text, self.body = title or 'Hilfe', text or ''
        kw.setdefault('text', '?')
        kw.setdefault('foreground', ACCENT)
        kw.setdefault('font', ('Segoe UI', 9, 'bold'))
        kw.setdefault('cursor', 'hand2')
        kw.setdefault('takefocus', True)
        kw.setdefault('padding', (3, 0))
        super().__init__(master, **kw)
        self.tip = Tooltip(self, f'{self.title_text}: {self.body}')
        for event in ('<Button-1>', '<Return>', '<space>'):
            self.bind(event, self._show, add='+')

    def _show(self, _event=None):
        from tkinter import messagebox
        messagebox.showinfo(self.title_text, self.body, parent=self.winfo_toplevel())
        return 'break'


# ---------- Hilfen für Tabs ----------

class TabHelpers:
    """Mischklasse für Tabs: Fehler verständlich anzeigen und ins Protokoll schreiben (nichts lautlos verschlucken)."""

    def report(self, error, what):
        from tkinter import messagebox
        from .common import TITLE
        from .logic import friendly_error
        text = friendly_error(error)
        if isinstance(error, ValueError):
            self.app.log(f'{what}: {text}', 'WARNING')
            messagebox.showwarning(TITLE, text)
        else:
            self.app.log(f'{what}: {type(error).__name__}: {text}', 'ERROR')
            messagebox.showerror(TITLE, f'{what} hat nicht geklappt.\n\n{text}')

    def attempt(self, what, fn, *args, **kwargs):
        """Führt fn aus. Gibt (True, Ergebnis) zurück; bei Fehler Meldung zeigen und (False, None) zurückgeben."""
        try:
            return True, fn(*args, **kwargs)
        except Exception as error:   # bewusst breit: jede Störung wird gemeldet und protokolliert
            self.report(error, what)
            return False, None


def run_in_thread(widget, fn, done, poll_ms=80, daemon=True):
    """Compatibility entry point; keep its existing polling interval and arguments."""
    run_background(widget, fn, done, interval=poll_ms, daemon=daemon, schedule=track_after)
