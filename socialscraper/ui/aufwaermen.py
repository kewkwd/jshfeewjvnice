import tkinter as tk
from tkinter import messagebox, ttk
from .. import warmup
from .hintergrund import Auftrag, Protokoll, cfg_of

from .common import Tab

class AufwaermenTab(Tab):
    titel, kuerzel = 'Aufwärmen', '<Control-Key-9>'

    def __init__(self, app):
        ttk.Frame.__init__(self, app.nb, padding=8)
        self.app, self._auftrag = app, None
        self._bauen(); self.aktualisieren()

    def _bauen(self):
        oben = ttk.LabelFrame(self, text='Aufwärm-Art für das markierte Konto', padding=6)
        oben.pack(fill='x')
        self.modus, self.umfang, self.tage = tk.StringVar(value='normal'), tk.StringVar(value='liken'), tk.StringVar()
        ttk.Label(oben, text='Intensität:').grid(row=0, column=0, sticky='w')
        ttk.Combobox(oben, textvariable=self.modus, values=list(warmup.MODI), width=12,
                     state='readonly').grid(row=0, column=1, padx=4)
        ttk.Label(oben, text='Umfang:').grid(row=0, column=2, sticky='w')
        ttk.Combobox(oben, textvariable=self.umfang, values=list(warmup.UMFANG), width=14,
                     state='readonly').grid(row=0, column=3, padx=4)
        ttk.Label(oben, text='Dauer (Tage, leer = Vorgabe):').grid(row=0, column=4, sticky='w')
        ttk.Entry(oben, textvariable=self.tage, width=6).grid(row=0, column=5, padx=4)
        ttk.Button(oben, text='Übernehmen', command=self.uebernehmen).grid(row=0, column=6, padx=4)
        leiste = ttk.Frame(self); leiste.pack(fill='x', pady=(8, 4))
        self.start_knopf = ttk.Button(leiste, text='Fällige jetzt aufwärmen', command=self.starten)
        self.start_knopf.pack(side='left')
        ttk.Button(leiste, text='Probe', command=lambda: self.starten(probe=True)).pack(side='left', padx=4)
        self.abbrechen_knopf = ttk.Button(leiste, text='Abbrechen', command=self.abbrechen, state='disabled')
        self.abbrechen_knopf.pack(side='left', padx=4)
        ttk.Button(leiste, text='Neu laden', command=self.aktualisieren).pack(side='left', padx=4)
        spalten = ('id', 'plattform', 'konto', 'modus', 'umfang', 'bis', 'phase', 'wechsel', 'sitzungen')
        kopf = {'id': 'Nr.', 'plattform': 'Plattform', 'konto': 'Konto', 'modus': 'Intensität',
                'umfang': 'Umfang', 'bis': 'aufwärmen bis', 'phase': 'Phase', 'wechsel': 'Wechsel',
                'sitzungen': 'Sitzungen'}
        breit = {'id': 50, 'plattform': 80, 'konto': 160, 'modus': 90, 'umfang': 100, 'bis': 110,
                 'phase': 60, 'wechsel': 100, 'sitzungen': 80}
        self.liste = ttk.Treeview(self, columns=spalten, show='headings', height=14)
        for name in spalten:
            self.liste.heading(name, text=kopf[name]); self.liste.column(name, width=breit[name], anchor='w')
        self.liste.pack(fill='both', expand=True)
        self.liste.bind('<<TreeviewSelect>>', self._auswahl_uebernehmen)
        ttk.Label(self, text='Protokoll:').pack(anchor='w', pady=(6, 0))
        feld = tk.Text(self, height=7); feld.pack(fill='both')
        self.protokoll = Protokoll(feld)

    def _cfg(self):
        return cfg_of(self.app)

    def _markiert(self):
        auswahl = self.liste.selection()
        return int(self.liste.item(auswahl[0], 'values')[0]) if auswahl else None

    def _auswahl_uebernehmen(self, _e=None):
        auswahl = self.liste.selection()
        if auswahl:
            werte = self.liste.item(auswahl[0], 'values')
            self.modus.set(werte[3]); self.umfang.set(werte[4])

    def uebernehmen(self):
        konto_id = self._markiert()
        if konto_id is None:
            messagebox.showinfo('Aufwärmen', 'Bitte zuerst ein Konto markieren.')
            return
        tage = int(self.tage.get()) if self.tage.get().strip().isdigit() else None
        try:
            warmup.setzen(self._cfg().db_path, konto_id, modus=self.modus.get(),
                          umfang=self.umfang.get(), tage=tage)
            self.protokoll.anhaengen(f'Konto {konto_id}: {self.modus.get()}/{self.umfang.get()} übernommen.')
            self.aktualisieren()
        except Exception as fehler:
            messagebox.showerror('Aufwärmen', str(fehler))

    def starten(self, probe=False):
        if self._auftrag and self._auftrag.laeuft:
            return
        cfg = self._cfg()
        self._auftrag = Auftrag(self, lambda melden, stop: warmup.run(
            cfg.db_path, cfg=cfg, trocken=probe, melden=melden, stop=stop),
            bei_zeile=self.protokoll.anhaengen, bei_ende=self._fertig)
        self.start_knopf.configure(state='disabled')
        self.abbrechen_knopf.configure(state='normal')
        if not self._auftrag.starten():
            self.start_knopf.configure(state='normal')
            self.abbrechen_knopf.configure(state='disabled')

    def abbrechen(self):
        if self._auftrag:
            self._auftrag.abbrechen()

    def _fertig(self, ergebnis, fehler):
        self.start_knopf.configure(state='normal')
        self.abbrechen_knopf.configure(state='disabled')
        self.protokoll.anhaengen(f'Fehler: {fehler}' if fehler else f'fertig: {ergebnis}')
        self.aktualisieren()

    def aktualisieren(self):
        self.liste.delete(*self.liste.get_children())
        try:
            for z in warmup.zustand(self._cfg().db_path):
                self.liste.insert('', 'end', values=(z['id'], z['platform'], z['label'], z['warmup_modus'],
                                                     z['warmup_umfang'], z['warmup_until'],
                                                     z.get('phase') or 'web', (z.get('app_bis') or '')[:10],
                                                     z.get('warmup_sitzungen') or 0))
        except Exception as fehler:
            self.protokoll.anhaengen(f'Liste nicht ladbar: {fehler}')

    def refresh(self):
        self.aktualisieren()
