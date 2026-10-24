"""The AI settings section; storage and overall settings flow stay in SettingsTab."""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import aiconfig
from .. import formlogic as fl
from .common import COLORS, TITLE
from .forms import run_async


class AISettingsSection:
    # ---------- Bereich KI / Stimmung ----------

    AI_PRIVACY = ('Datenschutz und Kosten: Beim Bestimmen der Stimmung werden Kommentartexte an die Anthropic-API gesendet '
                  '(api.anthropic.com). Gesendet wird nur der Text des Kommentars – ohne Autorennamen, ohne Nutzerkennungen, ohne '
                  'Video-Links; Links und @Namen im Text werden ersetzt. Anthropic verarbeitet die Texte nach seinen eigenen Bedingungen. '
                  'Jede Anfrage kostet Geld bei Anthropic (dein Konto). Das Ergebnis ist eine KI-Einschätzung, keine Messung.')

    def _build_ki(self, b):
        self.ai_vars = {'api_key': tk.StringVar(), 'model': tk.StringVar(), 'max_per_run': tk.StringVar(),
                        'price_in': tk.StringVar(), 'price_out': tk.StringVar(), 'auto': tk.BooleanVar()}
        for var in self.ai_vars.values():
            var.trace_add('write', lambda *_a: self._edited('ai'))
        self.ai_problem = None
        self._heading(b, 'KI / Stimmung', 'Die Claude-KI schätzt, ob gespeicherte Kommentare positiv, neutral oder negativ sind. '
                                          'Bestimmt wird nur, wenn du im Tab „Gespeicherte Daten“ auf „Stimmung bestimmen …“ klickst '
                                          '(oder unten die Automatik einschaltest).')
        self.ai_warn = tk.Frame(b, bg=self.WARN_BG, highlightthickness=2, highlightbackground=self.WARN_LINE, highlightcolor=self.WARN_LINE)
        self.ai_warn.grid(row=self._row(b), column=0, sticky='ew', pady=(4, 4))
        self.ai_warn.columnconfigure(0, weight=1)
        self.ai_warn_label = tk.Label(self.ai_warn, text='⚠ ' + self.AI_PRIVACY, bg=self.WARN_BG, fg=self.WARN_FG, font=('Segoe UI', 9),
                                      wraplength=480, justify='left', anchor='w')
        self.ai_warn_label.grid(row=0, column=0, sticky='ew', padx=10, pady=8)
        self.card_labels.append(self.ai_warn_label)

        self._title(b, 'API-Schlüssel (Anthropic)')
        holder = ttk.Frame(b)
        holder.grid(row=self._row(b), column=0, sticky='ew')
        holder.columnconfigure(0, weight=1)
        self.ai_key_entry = ttk.Entry(holder, textvariable=self.ai_vars['api_key'], show='•', width=30)
        self.ai_key_entry.grid(row=0, column=0, sticky='ew')
        self._secret_row(holder, self.ai_key_entry, extra=[('Kopieren', self._copy_ai_key)])
        self._hint(b, 'Den Schlüssel erstellst du selbst in deinem Konto bei Anthropic. Er wird nur in data/ai.json gespeichert (nicht in '
                      'settings.toml) und nie ins Protokoll geschrieben. Alternativ kann die Umgebungsvariable ANTHROPIC_API_KEY gesetzt sein.',
                   pady=(2, 0))
        self.ai_key_state = ttk.Label(b, text='', foreground=COLORS['muted'], wraplength=520, justify='left')
        self.ai_key_state.grid(row=self._row(b), column=0, sticky='w', pady=(2, 0))
        self.hints.append(self.ai_key_state)

        self._title(b, 'Modell')
        self.ai_model_box = ttk.Combobox(b, textvariable=self.ai_vars['model'], values=list(aiconfig.MODELS), width=34)
        self.ai_model_box.grid(row=self._row(b), column=0, sticky='w')
        self._hint(b, 'Das Standardmodell ist das günstigste. Größere Modelle sind genauer, kosten aber deutlich mehr. Du kannst auch '
                      'einen anderen Modellnamen eintippen.', pady=(2, 0))

        self._title(b, 'Höchstens … Kommentare pro Lauf')
        ttk.Spinbox(b, textvariable=self.ai_vars['max_per_run'], width=9, from_=1, to=100000, increment=100
                    ).grid(row=self._row(b), column=0, sticky='w')
        self._hint(b, 'Schutz vor unerwarteten Kosten: mehr Kommentare schickt ein einzelner Lauf nicht. Der Rest bleibt offen und wird beim '
                      'nächsten Start erledigt. Standard: 5000.', pady=(2, 0))

        self._title(b, 'Preise für die Kostenschätzung (freiwillig)')
        row = ttk.Frame(b)
        row.grid(row=self._row(b), column=0, sticky='w')
        ttk.Label(row, text='Eingabe pro Million Tokens').grid(row=0, column=0, sticky='w')
        ttk.Entry(row, textvariable=self.ai_vars['price_in'], width=10).grid(row=0, column=1, padx=(8, 18))
        ttk.Label(row, text='Ausgabe pro Million Tokens').grid(row=0, column=2, sticky='w')
        ttk.Entry(row, textvariable=self.ai_vars['price_out'], width=10).grid(row=0, column=3, padx=(8, 0))
        self._hint(b, 'Nur wenn du vor dem Start einen Betrag sehen willst: trage die Preise aus der Preisliste von Anthropic selbst ein '
                      '(in der Währung deiner Wahl, z. B. 3,00). Das Programm kennt keine Preise; ohne Eintrag zeigt die Schätzung nur '
                      'Kommentare, Anfragen und Tokens.', pady=(2, 0))

        auto = ttk.Checkbutton(b, text='Neue Kommentare nach jedem Lauf automatisch einstufen  ', variable=self.ai_vars['auto'])
        auto.grid(row=self._row(b), column=0, sticky='w', pady=(14, 0))
        self._hint(b, 'Standard: aus, weil es Geld kostet. Ist es an, stuft der Hintergrunddienst nach einem Lauf nur die neuen Kommentare '
                      'ein (höchstens die Obergrenze pro Lauf). Ein Fehler dabei lässt den Lauf nie scheitern.', pady=(0, 0))
        self.ai_error = ttk.Label(b, text='', foreground=COLORS['err'], wraplength=520, justify='left')
        self.ai_error.grid(row=self._row(b), column=0, sticky='w', pady=(6, 0))
        self.ai_error.grid_remove()
        self.err_labels['ai'] = self.ai_error
        self.hints.append(self.ai_error)

        self._title(b, 'Verbindung testen')
        row = ttk.Frame(b)
        row.grid(row=self._row(b), column=0, sticky='w')
        self.ai_test_btn = ttk.Button(row, text='Verbindung testen', command=self.test_ai)
        self.ai_test_btn.pack(side='left')
        self._hint(b, 'Sendet eine winzige echte Anfrage an Anthropic (ein paar Wörter, kostet nur einen Bruchteil eines Cents) – nur, '
                      'wenn du hier klickst. Getestet wird mit den Werten aus diesem Formular, auch wenn sie noch nicht gespeichert sind.',
                   pady=(4, 0))
        self.ai_result = ttk.Label(b, text='', wraplength=520, justify='left')
        self.ai_result.grid(row=self._row(b), column=0, sticky='w', pady=(4, 0))
        self.hints.append(self.ai_result)
        self.ai_baseline = self._ai_snapshot()

    @staticmethod
    def _price_text(value):
        if value is None or value == '':
            return ''
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return str(value)   # Text aus einer von Hand bearbeiteten ai.json: anzeigen, die Prüfung beim Speichern meldet es
        return f'{value:g}'.replace('.', ',')

    def _ai_snapshot(self):
        v = self.ai_vars
        try:
            auto = bool(v['auto'].get())
        except tk.TclError:
            auto = False
        return (v['api_key'].get().strip(), v['model'].get().strip(), v['max_per_run'].get().strip(), v['price_in'].get().strip(),
                v['price_out'].get().strip(), auto)

    def _ai_changed(self):
        return hasattr(self, 'ai_baseline') and self._ai_snapshot() != self.ai_baseline

    def _ai_load(self):
        """Werte aus data/ai.json ins Formular holen (der Schlüssel bleibt verborgen, bis du auf „Anzeigen“ klickst)."""
        cfg, problem = aiconfig.load_with_status(self.root())
        self.ai_problem = problem
        before, self._loading = self._loading, True
        try:
            v = self.ai_vars
            for attempt in (cfg, aiconfig.default_config()):
                try:
                    v['api_key'].set(attempt['api_key'])
                    v['model'].set(attempt['model'])
                    v['max_per_run'].set(str(attempt['max_per_run']))
                    v['price_in'].set(self._price_text(attempt['price_in_per_mtok']))
                    v['price_out'].set(self._price_text(attempt['price_out_per_mtok']))
                    v['auto'].set(bool(attempt['auto_after_runs']))
                    break
                except (TypeError, ValueError, KeyError, tk.TclError) as error:   # von Hand bearbeitete ai.json mit ungültigen Werten
                    if attempt is cfg:
                        problem = self.ai_problem = (f'Die Datei {aiconfig.FILE_NAME} enthält ungültige Werte ({error}). '
                                                     'Es gelten die Standardwerte, bis du speicherst.')
                    else:
                        raise
        finally:
            self._loading = before
        self.ai_baseline = self._ai_snapshot()
        self._ai_key_state()
        if problem:
            self.ai_error.configure(text='✖ ' + problem)
            self.ai_error.grid()

    def _ai_key_state(self):
        key, source = aiconfig.api_key({'api_key': self.ai_vars['api_key'].get()})
        text = {'datei': 'Ein Schlüssel ist eingetragen.',
                'umgebung': 'Es ist kein Schlüssel eingetragen, aber die Umgebungsvariable ANTHROPIC_API_KEY ist gesetzt – sie wird benutzt.',
                'fehlt': 'Es ist noch kein Schlüssel eingetragen. Ohne Schlüssel lässt sich die Stimmung nicht bestimmen.'}[source]
        self.ai_key_state.configure(text=text, foreground=COLORS['ok'] if source != 'fehlt' else COLORS['warn'])

    def _ai_collect(self):
        """(Einstellungen als Wörterbuch, Fehlertext oder ''). Prüft nur die Eingaben, schreibt nichts."""
        v = self.ai_vars
        cfg = aiconfig.default_config()
        problems = []
        cfg['api_key'] = v['api_key'].get().strip()
        cfg['model'] = v['model'].get().strip()
        number, error = fl.parse_int(v['max_per_run'].get(), 1, aiconfig.MAX_MAX_PER_RUN, 'Höchstens … Kommentare pro Lauf')
        if error:
            problems.append(error)
        else:
            cfg['max_per_run'] = number
        for field, name, label in (('price_in', 'price_in_per_mtok', 'Eingabe'), ('price_out', 'price_out_per_mtok', 'Ausgabe')):
            text = v[field].get().strip().replace(',', '.')
            if not text:
                continue
            try:
                cfg[name] = float(text)
            except ValueError:
                problems.append(f'Der Preis pro Million {label}-Tokens muss eine Zahl sein (zum Beispiel 3,00) – oder leer.')
        cfg['auto_after_runs'] = bool(v['auto'].get())
        if not problems:
            problems = aiconfig.validate(cfg)
        return cfg, ' '.join(problems)

    def _copy_ai_key(self):
        key = self.ai_vars['api_key'].get().strip()
        if not key:
            messagebox.showinfo(TITLE, 'Es ist noch kein Schlüssel eingetragen.')
            return
        self._clipboard(key)
        self.app.set_status('API-Schlüssel in die Zwischenablage kopiert.', 'ok')

    def test_ai(self):
        cfg, problem = self._ai_collect()
        if problem:
            self.ai_result.configure(text='✖ ' + problem, foreground=COLORS['err'])
            return
        if not aiconfig.has_key(cfg):
            self.ai_result.configure(text='✖ Bitte zuerst den API-Schlüssel eintragen.', foreground=COLORS['err'])
            return
        from .. import sentiment
        self.ai_test_btn.configure(state='disabled')
        self.ai_result.configure(text='Prüfe … (eine winzige Anfrage an Anthropic)', foreground=COLORS['muted'])

        def done(result, error):
            self.ai_test_btn.configure(state='normal')
            if error is not None:
                text, ok = 'Der Test ist gescheitert (' + type(error).__name__ + ').', False
            else:
                ok, text = result
            text = aiconfig.scrub(text, cfg)
            self.last_result['ai'] = (ok, text)
            self.ai_result.configure(text=('✔ ' if ok else '✖ ') + text, foreground=COLORS['ok' if ok else 'err'])
        run_async(self, lambda: sentiment.test_connection(cfg), done)

