"""Manual website confirmation in the application's own dialog."""
import tkinter as tk
from tkinter import ttk
from ..net.browser_window import EmbeddedWindow, Windows, validate

class BrowserDialog(tk.Toplevel):
    def __init__(self,app,event):
        native=Windows()
        process=getattr(getattr(app,'job',None),'proc',None)
        if process is None or not validate(event,process.pid,native):
            raise ValueError('Das Browserfenster gehört nicht zum laufenden Auftrag.')
        super().__init__(app)
        self.app,self.hwnd,self.embedded=app,event['hwnd'],None
        self.title('Socialscraper – Anmeldung und Bestätigung')
        self.transient(app)
        width=min(1200,max(900,self.winfo_screenwidth()-100))
        height=min(900,max(650,self.winfo_screenheight()-100))
        self.geometry(f'{width}x{height}')
        self.minsize(850,620)
        header=ttk.Frame(self,padding=(14,10));header.pack(fill='x')
        ttk.Label(header,text='Anmeldung oder Bestätigung abschließen',font=('Segoe UI',14,'bold')).pack(anchor='w')
        route='Die Verbindung läuft über den zugeordneten Proxy.' if event['proxy'] else 'Verbindung gemäß deinen Programmeinstellungen.'
        ttk.Label(header,text=route+' Löse Rückfragen hier selbst. Der Auftrag erkennt den Abschluss automatisch.',wraplength=1100).pack(anchor='w',pady=(4,0))
        self.host=tk.Frame(self,background='white');self.host.pack(fill='both',expand=True)
        footer=ttk.Frame(self,padding=12);footer.pack(fill='x')
        ttk.Button(footer,text='Auftrag abbrechen',command=self.cancel).pack(side='right')
        ttk.Button(footer,text='Im eigenen Browserfenster anzeigen',command=self.external).pack(side='left')
        self.protocol('WM_DELETE_WINDOW',self.cancel)
        try:
            self.update_idletasks()
            self.embedded=EmbeddedWindow(self.hwnd,self.host.winfo_id(),native)
            self.host.bind('<Configure>',lambda e:self.embedded.resize(e.width,e.height) if self.embedded else None)
            self.embedded.resize(self.host.winfo_width(),self.host.winfo_height())
            self.lift()
        except BaseException:
            self.destroy()
            raise

    def cancel(self):
        self.app.cancel()

    def external(self):
        self.destroy()

    def destroy(self):
        try:
            if self.embedded:
                self.embedded.detach()
        finally:
            self.embedded=None
            super().destroy()
