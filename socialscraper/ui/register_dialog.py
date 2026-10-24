import tkinter as tk
from tkinter import ttk, messagebox
from .. import accountvault, guicore, repo
from ..net import proxymode
from .hintergrund import cfg_of

class KontoDialog(tk.Toplevel):
    def __init__(self,parent,app,on_fertig=None):
        super().__init__(parent)
        self.app,self.on_fertig=app,on_fertig
        self.title('Neues Plattformkonto registrieren')
        self.transient(parent.winfo_toplevel());self.resizable(False,False)
        box=ttk.Frame(self,padding=22);box.pack(fill='both',expand=True)
        ttk.Label(box,text='Registrierung vorbereiten',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(box,text='Wähle die Plattform und das Postfach. Anschließend öffnet sich der Registrierungsablauf.',wraplength=430,foreground='#64748b').pack(anchor='w',pady=(6,18))
        self.codes={guicore.PLATFORMS[p]['name']:p for p in repo.ACCOUNT_PLATFORMS}
        self.platform=tk.StringVar(value='Reddit');self.email=tk.StringVar();self.name=tk.StringVar()
        ttk.Label(box,text='Plattform').pack(anchor='w')
        ttk.Combobox(box,textvariable=self.platform,values=list(self.codes),state='readonly',width=35).pack(fill='x',pady=(4,12))
        ttk.Label(box,text='E-Mail-Adresse (optional)').pack(anchor='w')
        ttk.Entry(box,textvariable=self.email,width=42).pack(fill='x',pady=(4,6))
        cfg=cfg_of(app)
        try:free=sum(not e.get('belegt_von') for e in accountvault.postfaecher(cfg.db_path))
        except Exception:free=0
        hint=f'{free} freie Postfächer verfügbar. Leer lassen, um eines davon zu verwenden.' if free else 'Noch kein freies Postfach vorhanden. Füge zuerst eines unter Postfächer hinzu.'
        ttk.Label(box,text=hint,foreground='#64748b',wraplength=430).pack(anchor='w')
        ttk.Button(box,text='Postfächer öffnen',command=lambda:self.open_page('Postfächer')).pack(anchor='w',pady=(6,12))
        ttk.Label(box, text='Passwörter werden vor der Eingabe gespeichert. Die Quelle wählst du '
                  'unter Profile oder Postfächer → Passwortvorrat.', wraplength=430,
                  foreground='#64748b').pack(anchor='w', pady=(0, 6))
        advanced=ttk.Frame(box)
        advanced.pack(fill='x', pady=8)
        ttk.Label(advanced,text='Vorbereitete Profilkennung').pack(anchor='w')
        ttk.Entry(advanced,textvariable=self.name).pack(fill='x',pady=4)
        ttk.Button(advanced,text='Profil mit Name und Geschlecht vorbereiten',command=lambda:self.open_page('Profile')).pack(anchor='w')
        footer=ttk.Frame(box);footer.pack(fill='x',pady=(16,0))
        ttk.Label(footer,text='TikTok arbeitet im Hintergrund und zeigt dir Rückfragen bei Bedarf.\nAndere Plattformen öffnen weiterhin ihr Anmeldefenster.',foreground='#64748b',wraplength=430).pack(anchor='w',pady=(0,12))
        self.start_button=ttk.Button(footer,text='Registrierung öffnen',command=self.start,style='Primary.TButton')
        self.start_button.pack(side='right')
        if proxymode.is_anonymous(cfg):
            self.start_button.configure(state='disabled')
            ttk.Label(footer,text='Tor ist aktiv. Stelle die Verbindung zuerst auf „Aus“ oder eine eigene Proxy-Liste.',foreground='#9a6700',wraplength=430).pack(anchor='w')
            ttk.Button(footer,text='Verbindung einstellen',command=lambda:self.open_page('Einstellungen')).pack(anchor='w',pady=6)

    def open_page(self,name):
        self.app.select_tab(name);self.destroy()

    def start(self):
        try:
            from .workflow_components import busy
            if busy(self.app):
                messagebox.showinfo('Auftrag läuft','Warte, bis der laufende Auftrag beendet ist. Deine Eingaben bleiben erhalten.',parent=self)
                return
            proxymode.require_no_anon(cfg_of(self.app),'Registrierung')
            from .. import profile_settings
            identity = self.name.get().strip()
            if not identity:raise ValueError('Bitte zuerst unter Profile Name und Geschlecht hinterlegen und hier die Profilkennung auswählen.')
            profile_settings.normalize_person(profile_settings.load(cfg_of(self.app).profiles_dir, identity)['person'], required=True)
            if not self.email.get().strip() and not accountvault.nimm_freies(cfg_of(self.app).db_path):
                messagebox.showinfo('Postfach fehlt','Füge zuerst ein Postfach hinzu oder gib eine E-Mail-Adresse ein.',parent=self);return
            command=['konto',self.codes[self.platform.get()],'--modus','assisted','--aufwaermen','aus']
            if self.email.get().strip():command+=['--mail',self.email.get().strip()]
            if self.name.get().strip():command+=['--name',self.name.get().strip()]
            accepted=self.app.run_steps([('Registrierung öffnen',command)],lambda *_:self.on_fertig() if self.on_fertig else None)
            if accepted is not False:self.destroy()
        except Exception as error:messagebox.showerror('Registrierung vorbereiten',str(error),parent=self)
