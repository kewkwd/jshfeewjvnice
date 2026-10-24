import tkinter as tk
from tkinter import messagebox, ttk
from .. import repo


class KontoDialog(tk.Toplevel):
    def __init__(self, parent, app, on_fertig=None):
        super().__init__(parent)
        self.app, self.on_fertig = app, on_fertig
        self.title('Konto erzeugen')
        self.transient(parent.winfo_toplevel())
        box = ttk.Frame(self, padding=16); box.pack(fill='both', expand=True)
        self.platform = tk.StringVar(value='reddit')
        self.name, self.email = tk.StringVar(), tk.StringVar()
        self.mode = tk.StringVar(value='assisted')
        self.warmup = tk.StringVar(value='normal')
        for row, (label, variable, choices) in enumerate([
            ('Plattform', self.platform, repo.ACCOUNT_PLATFORMS),
            ('Kontoschlüssel (leer = automatisch)', self.name, None),
            ('E-Mail (leer = freies Postfach)', self.email, None),
            ('Modus', self.mode, ('assisted', 'auto')),
            ('Aufwärmen', self.warmup, ('aus', 'schonend', 'normal', 'zuegig')),
        ]):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky='w', padx=4, pady=5)
            if choices:
                widget = ttk.Combobox(box, textvariable=variable, values=choices, state='readonly')
            else:
                widget = ttk.Entry(box, textvariable=variable, width=35)
            widget.grid(row=row, column=1, sticky='ew', padx=4, pady=5)
        ttk.Label(box, text='Die Registrierung öffnet einen Browser oder den TikTok-Emulator.\nDas erzeugte Passwort wird in der Geheimnisdatei gespeichert.', wraplength=420).grid(row=5, column=0, columnspan=2, pady=10)
        ttk.Button(box, text='Konto erzeugen', command=self.start).grid(row=6, column=1, sticky='e')

    def start(self):
        from ..net import proxymode
        from .hintergrund import cfg_of
        try:
            proxymode.require_no_anon(cfg_of(self.app), 'Konto anlegen')
            args = ['konto', self.platform.get(), '--modus', self.mode.get(), '--aufwaermen', self.warmup.get()]
            if self.name.get().strip(): args += ['--name', self.name.get().strip()]
            if self.email.get().strip(): args += ['--mail', self.email.get().strip()]
            self.app.run_steps([('Konto erzeugen', args)], lambda results, cancelled: self.on_fertig() if self.on_fertig else None)
            self.destroy()
        except Exception as error:
            messagebox.showerror('Konto erzeugen', str(error), parent=self)
