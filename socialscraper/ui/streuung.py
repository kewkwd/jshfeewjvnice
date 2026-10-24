import tkinter as tk
from tkinter import messagebox, ttk
from .. import streuung
from ..store import Store
from .hintergrund import Protokoll, cfg_of
FARBE = {'hoch': '#b00020', 'mittel': '#b06000', 'niedrig': '#555555'}

from .common import Tab

class StreuungTab(Tab):
    titel, kuerzel = 'Streuung', '<Control-Key-F2>'

    def __init__(self, app):
        ttk.Frame.__init__(self, app.nb, padding=8)
        self.app = app
        self._bauen(); self.pruefen()

    def _bauen(self):
        from .workflow_components import steps, table, log
        steps(self,'Optional: vorbereitete Aktionen verteilen und Konto-Zuordnungen prüfen')
        self.status=ttk.Label(self,text='Plane zuerst Aktionen. Hier kannst du sie anschließend über einen Zeitraum verteilen.',foreground='#64748b',wraplength=700)
        self.status.pack(anchor='w',pady=(0,12))
        bar=ttk.Frame(self);bar.pack(fill='x')
        ttk.Button(bar,text='Zeitplan einstellen …',command=self.streuen,style='Primary.TButton').pack(side='left')
        ttk.Button(bar,text='Zuordnungen prüfen',command=self.pruefen).pack(side='left',padx=8)
        ttk.Button(bar,text='Zur Aktionsliste',command=lambda:self.app.select_tab('Aktionen')).pack(side='right')
        self.spanne=tk.IntVar(value=240)
        self.liste=table(self,[('schwere','Priorität',80),('art','Bereich',110),('wert','Zuordnung',170),('konten','Betroffene Konten',170),('hinweis','Was bedeutet das?',300)])
        for severity,color in FARBE.items():self.liste.tag_configure(severity,foreground=color)
        ttk.Button(self,text='Aktivitätszeiten für markierte Konten neu festlegen',command=self.fenster).pack(anchor='w',pady=(8,0))
        self.protokoll=log(self)

    def _cfg(self):
        return cfg_of(self.app)

    def pruefen(self):
        self.liste.delete(*self.liste.get_children())
        try:
            befunde = streuung.audit(self._cfg().db_path)
        except Exception as error:
            self.status.configure(text=f'Prüfung nicht möglich: {error}')
            self.protokoll.anhaengen(self.status.cget('text'))
            return
        for b in befunde:
            self.liste.insert('', 'end', tags=(b['schwere'],),
                              values=(b['schwere'], b['art'], b['wert'],
                                      ', '.join(b.get('konten') or []), b['hinweis']))
        self.status.configure(text='Keine Hinweise zu mehrfach genutzten Zuordnungen gefunden.' if not befunde else f'{len(befunde)} Hinweise gefunden. Die letzte Spalte erklärt ihre Bedeutung.')

    def streuen(self):
        from .workflow_components import busy
        if busy(self.app):
            self.status.configure(text='Warte mit Zeitplanänderungen, bis der laufende Auftrag beendet ist.');return
        from .widgets import ask_form
        result=ask_form(self,'Zeitplan einstellen',[{'key':'hours','label':'Über wie viele Stunden verteilen?','value':'4'}],intro='Nur vorbereitete, noch offene Aktionen werden verteilt.',ok_text='Zeitplan speichern')
        if not result:return
        try:
            minutes=int(float(result['hours'].replace(',', '.'))*60)
            if not 10<=minutes<=2880:raise ValueError('Bitte zwischen 10 Minuten und 48 Stunden wählen.')
            self.spanne.set(minutes)
        except ValueError as error:
            messagebox.showerror('Zeitplan einstellen',str(error));return
        anzahl = streuung.streuen(self._cfg().db_path, spanne_minuten=self.spanne.get())
        self.status.configure(text=f'{anzahl} vorbereitete Aktionen über {self.spanne.get()/60:g} Stunden verteilt. Den Plan siehst du in der Aktionsliste.')
        self.protokoll.anhaengen(self.status.cget('text'))

    def fenster(self):
        from .workflow_components import busy
        if busy(self.app):
            self.status.configure(text='Warte mit Zeitplanänderungen, bis der laufende Auftrag beendet ist.');return
        auswahl = self.liste.selection()
        konten = self.liste.item(auswahl[0], 'values')[3] if auswahl else ''
        if not konten:
            messagebox.showinfo('Streuung', 'Bitte eine Zeile mit betroffenen Konten markieren.')
            return
        with Store(self._cfg().db_path) as store:
            for name in str(konten).split(','):
                zeile = store.db.execute('SELECT id FROM accounts WHERE identity=?', (name.strip(),)).fetchone()
                if zeile:
                    streuung.setze_fenster(self._cfg().db_path, zeile[0])
        self.protokoll.anhaengen(f'Fenster für {konten} neu gesetzt.')
        self.pruefen()

    def refresh(self):
        self.pruefen()
