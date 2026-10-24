import tkinter as tk
from tkinter import ttk, messagebox
from ..working_rules import RULES, VERSION
from .common import Tab
from .forms import ScrollFrame


class WorkingRulesTab(Tab):
    def build(self):
        self.configure(padding=16)
        ttk.Label(self, text='Unsere Arbeitsregeln', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(self, text='Gemeinsame Vorgaben und aktueller Umsetzungsstand · ' + VERSION).pack(anchor='w', pady=(5, 12))
        self.accept_privacy = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text='Cookie- und Tracking-Dialoge bei Browserregistrierungen automatisch akzeptieren',
                        variable=self.accept_privacy, command=self.save_privacy).pack(anchor='w', pady=(0, 10))
        scroll = ScrollFrame(self)
        scroll.pack(fill='both', expand=True)
        wrapped_labels = []
        for title, rule, status in RULES:
            card = ttk.Frame(scroll.body, padding=(16, 14), style='Card.TFrame')
            card.pack(fill='x', pady=(0, 10))
            ttk.Label(card, text=title, font=('Segoe UI', 11, 'bold')).pack(anchor='w')
            for text, color in ((rule, '#18263b'), (status, '#64748b')):
                label = ttk.Label(card, text=text, foreground=color, wraplength=690, justify='left')
                label.pack(fill='x', pady=(5, 0))
                wrapped_labels.append(label)
        # Only the viewport controls wrapping. Content width depends on the
        # labels themselves and can feed back into hidden notebook geometry.
        def wrap(viewport_width):
            width = max(240, viewport_width - 24)
            for item in wrapped_labels:
                if int(item.cget('wraplength')) != width:
                    item.configure(wraplength=width)
        scroll._on_width = wrap

    def refresh(self):
        from .hintergrund import cfg_of
        from ..privacy_consent import enabled
        try:self.accept_privacy.set(enabled(cfg_of(self.app).db_path))
        except Exception as error:
            self.accept_privacy.set(False)
            self.app.log(str(error), 'ERROR')

    def save_privacy(self):
        from .hintergrund import cfg_of
        from ..privacy_consent import save
        try:
            save(cfg_of(self.app).db_path, self.accept_privacy.get())
            self.app.log('Datenschutzauswahl gespeichert; gilt für neue Registrierungsaufträge.')
        except Exception as error:
            self.refresh()
            messagebox.showerror('Datenschutzauswahl', str(error), parent=self)
