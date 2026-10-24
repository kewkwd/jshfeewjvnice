"""Manage password sources locally; existing passwords stay hidden by default."""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import password_supply, runlock
from .forms import ScrollFrame


class PasswordSupplyDialog(tk.Toplevel):
    def __init__(self, parent, cfg, identity=None):
        super().__init__(parent)
        self.cfg, self.identity = cfg, identity
        self.title('Passwortvorrat')
        self.transient(parent.winfo_toplevel())
        self.geometry('680x650')
        self.minsize(680, 490)
        content = ScrollFrame(self)
        content.pack(fill='both', expand=True)
        box = ttk.Frame(content.body, padding=16)
        box.pack(fill='x')
        ttk.Label(box, text='Passwörter für neue Profile und Registrierungen',
                  font=('Segoe UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(box, text='Jedes Profil behält sein zugeordnetes Passwort. Postfachregistrierungen erhalten '
                  'ein eigenes Passwort. Ein Abbruch gibt zugeordnete Einträge nicht wieder frei.',
                  wraplength=610).pack(fill='x', pady=(6, 10))
        self.mode = tk.StringVar(value='generated')
        for value, label in password_supply.MODES.items():
            ttk.Radiobutton(box, text=label, variable=self.mode, value=value).pack(anchor='w')
        self.counts = ttk.Label(box)
        self.counts.pack(anchor='w', pady=8)
        ttk.Label(box, text='Vorrat ergänzen: ein Passwort pro Zeile, in gewünschter Reihenfolge. '
                  '12–128 ASCII-Zeichen; keine Leerzeichen am Rand. Doppelte Einträge werden übersprungen.',
                  wraplength=610).pack(fill='x')
        area = ttk.Frame(box)
        area.pack(fill='both', expand=True, pady=8)
        self.input = tk.Text(area, height=8, wrap='none', undo=False)
        scroll = ttk.Scrollbar(area, command=self.input.yview)
        self.input.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.input.pack(side='left', fill='both', expand=True)
        actions = ttk.Frame(box)
        actions.pack(fill='x')
        ttk.Button(actions, text='Textdatei einfügen …', command=self.import_file).pack(side='left')
        ttk.Button(actions, text='Quelle und Ergänzungen speichern', command=self.save).pack(side='right')
        self.result = ttk.Label(box, text='', wraplength=610)
        self.result.pack(fill='x', pady=8)
        ttk.Label(box, text='Im Listenmodus hält eine leere Liste den Auftrag an. Die Speicherung erfolgt '
                  'lokal mit denselben Dateiberechtigungen wie die übrigen Zugangsdaten.',
                  wraplength=610).pack(fill='x')
        self.password = tk.StringVar()
        if identity is not None:
            profile = ttk.LabelFrame(box, text='Ausgewähltes Profil: ' + identity, padding=8)
            profile.pack(fill='x', pady=10)
            ttk.Button(profile, text='Gespeichertes Passwort laden / fehlendes zuordnen',
                       command=self.load_profile).pack(anchor='w')
            self.entry = ttk.Entry(profile, textvariable=self.password, show='*', state='readonly')
            self.entry.pack(fill='x', pady=5)
            self.visible = tk.BooleanVar(value=False)
            ttk.Checkbutton(profile, text='Passwort anzeigen', variable=self.visible,
                            command=lambda: self.entry.configure(show='' if self.visible.get() else '*')).pack(anchor='w')
        ttk.Button(box, text='Schließen', command=self.close).pack(anchor='e')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        try:
            self.refresh(reset_mode=True)
        except (OSError, ValueError, RuntimeError):
            self.destroy()
            raise

    def refresh(self, reset_mode=False):
        status = password_supply.summary(self.cfg.db_path)
        if reset_mode:
            self.mode.set(status['mode'])
        self.counts.configure(text=f"{status['available']} freie Listenpasswörter · {status['assigned']} feste Zuordnungen")

    def import_file(self):
        path = filedialog.askopenfilename(parent=self, title='Passwortliste auswählen',
                                         filetypes=[('Textdateien', '*.txt'), ('Alle Dateien', '*.*')])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8-sig', newline='') as stream:
                text = stream.read(2_000_001)
            password_supply.parse_lines(text)
            current = self.input.get('1.0', 'end-1c')
            self.input.insert('end', ('\n' if current and not current.endswith('\n') else '') + text)
            self.result.configure(text='Datei eingefügt. Mit „Quelle und Ergänzungen speichern“ übernehmen.')
        except (OSError, UnicodeError, ValueError):
            messagebox.showerror('Passwortliste', 'Datei konnte nicht übernommen werden. '
                                 'Bitte UTF-8-Text mit 12–128 ASCII-Zeichen je Passwort verwenden.', parent=self)

    def save(self):
        try:
            with runlock.acquire(runlock.root_for_db(self.cfg.db_path), 'gui'):
                result = password_supply.configure(self.cfg.db_path, self.mode.get(),
                                                   self.input.get('1.0', 'end-1c'))
            self.input.delete('1.0', 'end')
            self.refresh()
            self.result.configure(text=f"Gespeichert: {result['added']} hinzugefügt, {result['duplicates']} doppelte übersprungen.")
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Passwortvorrat', str(error), parent=self)

    def load_profile(self):
        try:
            if (self.mode.get() != password_supply.summary(self.cfg.db_path)['mode']
                    or self.input.get('1.0', 'end-1c')):
                raise ValueError('Bitte zuerst die gewählte Quelle und die eingefügten Passwörter speichern.')
            with runlock.acquire(runlock.root_for_db(self.cfg.db_path), 'gui'):
                value = password_supply.profile_password(self.cfg.db_path, self.identity)
            self.password.set(value)
            self.refresh()
            self.result.configure(text='Passwort für dieses Profil gespeichert. Ein Kontoabschluss ist damit noch nicht bestätigt.')
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror('Profilpasswort', str(error), parent=self)

    def close(self):
        self.password.set('')
        self.destroy()


def show(parent, cfg, identity=None):
    return PasswordSupplyDialog(parent, cfg, identity)
