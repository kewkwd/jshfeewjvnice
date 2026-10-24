import tkinter as tk
from tkinter import messagebox, ttk
from .. import aktionen, repo
from ..store import Store
from .hintergrund import Auftrag, Protokoll, cfg_of

from .common import Tab

class AktionenTab(Tab):
    titel, kuerzel = 'Aktionen', '<Control-Key-8>'

    def __init__(self, app):
        ttk.Frame.__init__(self, app.nb, padding=8)
        self.app, self._auftrag = app, None
        self._bauen(); self.aktualisieren()

    def _bauen(self):
        from .workflow_components import steps, table, log
        steps(self, '1  Aktion planen     →     2  Probelauf ansehen     →     3  Starten')
        self.plattform, self.art = tk.StringVar(value='tiktok'), tk.StringVar(value='like')
        self.nur_negativ, self.anzahl = tk.BooleanVar(value=False), tk.IntVar(value=20)
        self.status = ttk.Label(self,text='Plane zunächst eine Aktion aus gespeicherten Kommentaren.',foreground='#64748b',wraplength=700)
        self.status.pack(anchor='w',pady=(0,10))
        plan=ttk.Frame(self);plan.pack(fill='x')
        ttk.Button(plan,text='Aktion planen …',command=self.planen,style='Primary.TButton').pack(side='left')
        ttk.Button(plan,text='Bewertung vorbereiten …',command=self.bewerten).pack(side='left',padx=8)
        ttk.Button(plan,text='Kommentare abrufen',command=lambda:self.app.select_tab('Abrufen')).pack(side='right')
        leiste=ttk.Frame(self);leiste.pack(fill='x',pady=(14,0))
        self.probe_knopf=ttk.Button(leiste,text='Probelauf',command=lambda:self.starten(probe=True))
        self.probe_knopf.pack(side='left')
        self.start_knopf=ttk.Button(leiste,text='Vorbereitete Aktionen starten',command=self.starten)
        self.start_knopf.pack(side='left',padx=8)
        self.abbrechen_knopf=ttk.Button(leiste,text='Abbrechen',command=self.abbrechen,state='disabled')
        self.abbrechen_knopf.pack(side='left')
        self.zaehler=ttk.Label(leiste,text='');self.zaehler.pack(side='right')
        self.liste=table(self,[('id','Nr.',45),('plattform','Plattform',90),('art','Aktion',140),('ziel','Ziel',220),('status','Status',110),('faellig','Geplant für',140)])
        self.protokoll=log(self)
        self._probe=False
        for button in (self.start_knopf,self.probe_knopf):self.app.register_busy(button)

    def planen(self):
        from .workflow_components import busy
        if busy(self.app):return
        from .widgets import ask_form
        from .. import guicore
        result=ask_form(self,'Aktion planen',[
            {'key':'platform','label':'Plattform','kind':'choice','value':self.plattform.get(),'choices':[(p,guicore.PLATFORMS[p]['name']) for p in repo.PLATFORMS]},
            {'key':'art','label':'Was möchtest du tun?','kind':'choice','value':'like','choices':[('like','Gefällt mir setzen'),('report','Kommentar melden'),('kommentar','Kommentar schreiben')]},
            {'key':'count','label':'Höchstens so viele Kommentare','value':'20'},
            {'key':'filter','label':'Welche Kommentare?','kind':'choice','value':'all','choices':[('all','Alle gespeicherten'),('negative','Nur als negativ eingestufte')]},
        ],intro='Die Aktion wird zunächst vorbereitet. Danach kannst du einen Probelauf ansehen.',ok_text='Vorbereiten')
        if not result:return
        try:
            count=int(result['count'])
            if not 1<=count<=5000:raise ValueError('Bitte eine Anzahl zwischen 1 und 5000 eingeben.')
            self.plattform.set(result['platform']);self.art.set(result['art']);self.anzahl.set(count)
            self.nur_negativ.set(result['filter']=='negative')
            self._text=None
            if result['art']=='kommentar':
                content=ask_form(self,'Kommentar schreiben',[{'key':'text','label':'Kommentartext','value':''}],ok_text='Vorbereiten')
                if not content:return
                self._text=content['text'].strip()
                if not self._text:raise ValueError('Bitte einen Kommentartext eingeben.')
            self.einreihen()
        except (ValueError,tk.TclError) as error:messagebox.showerror('Aktion planen',str(error))

    def _cfg(self):
        return cfg_of(self.app)

    def einreihen(self):
        from .workflow_components import busy
        if busy(self.app):return
        try:
            with Store(self._cfg().db_path) as store:
                db = store.db
                spalten = {z[1] for z in db.execute('PRAGMA table_info(comments)').fetchall()}
                bedingungen = ['platform=?']
                if self.nur_negativ.get():
                    from .. import sentiment_repo
                    sentiment_repo.prepare(db)
                    bedingungen.append("uid IN (SELECT comment_uid FROM comment_sentiment WHERE label='negativ' AND text_hash=ss_th(comments.text))")
                zeilen = db.execute(f"SELECT uid FROM comments WHERE {' AND '.join(bedingungen)} "
                                    f"ORDER BY rowid DESC LIMIT ?",
                                    (self.plattform.get(), self.anzahl.get())).fetchall()
                neu = aktionen.enqueue_comments(db, self.plattform.get(), [z[0] for z in zeilen],
                                                art=self.art.get(), text=getattr(self,'_text',None))
            self.status.configure(text=f'{neu} Aktionen vorbereitet. Als Nächstes: Probelauf ansehen.' if neu else 'Keine neuen passenden Kommentare gefunden. Wähle eine andere Plattform oder rufe zuerst Kommentare ab.')
            self.protokoll.anhaengen(f'{neu} Aktionen vorbereitet.')
            self.aktualisieren()
        except Exception as fehler:
            messagebox.showerror('Einreihen', str(fehler))

    def starten(self, probe=False):
        if self._auftrag and self._auftrag.laeuft:
            return
        cfg = self._cfg()
        self._probe=probe
        self.status.configure(text='Probelauf läuft …' if probe else 'Aktionen werden ausgeführt …')
        self.protokoll.anhaengen('Probe …' if probe else 'Start …')
        self._auftrag = Auftrag(self, lambda melden, stop: aktionen.run(
            cfg.db_path, limit=200, dry=probe, cfg=cfg, melden=melden, stop=stop),
            bei_zeile=self.protokoll.anhaengen, bei_ende=self._fertig)
        self.start_knopf.configure(state='disabled')
        self.abbrechen_knopf.configure(state='normal')
        if not self._auftrag.starten():
            self.start_knopf.configure(state='normal')
            self.abbrechen_knopf.configure(state='disabled')

    def abbrechen(self):
        if self._auftrag:
            self._auftrag.abbrechen()
            self.protokoll.anhaengen('Abbruch angefordert …')

    def _fertig(self, ergebnis, fehler):
        self.start_knopf.configure(state='normal')
        self.abbrechen_knopf.configure(state='disabled')
        from .workflow_components import summary
        self.status.configure(text=f'Vorgang nicht abgeschlossen: {fehler}' if fehler else summary(ergebnis,probe=self._probe))
        self.protokoll.anhaengen(self.status.cget('text'))
        self.aktualisieren()

    def aktualisieren(self):
        self.liste.delete(*self.liste.get_children())
        try:
            with Store(self._cfg().db_path) as store:
                db = store.db
                zahlen = aktionen.counts(db)
                cur=db.execute('SELECT * FROM aktionen ORDER BY id DESC LIMIT 300')
                names=[c[0] for c in cur.description]
                for row in cur.fetchall():
                    a=dict(zip(names,row))
                    state = 'Ergebnis prüfen' if a['status']=='laeuft' and not (self._auftrag and self._auftrag.laeuft) else {'offen':'Vorbereitet','laeuft':'Läuft','erledigt':'Abgeschlossen','fehler':'Fehlgeschlagen','uebersprungen':'Übersprungen'}.get(a['status'],a['status'])
                    self.liste.insert('', 'end', values=(a['id'], a['platform'].title(), {'like':'Gefällt mir','report':'Meldung','kommentar':'Kommentar','bewertung':'Bewertung'}.get(a['art'],a['art']), a['ziel_uid'],
                                                         state, (a['faellig_am'] or '').replace('T',' ')[:16]))
            from .workflow_components import busy
            ready=bool(zahlen['offen'] or zahlen['fehler']) and not busy(self.app)
            self.start_knopf.configure(state='normal' if ready else 'disabled')
            self.probe_knopf.configure(state='normal' if ready else 'disabled')
            from ..net import proxymode
            if proxymode.is_anonymous(self._cfg()):
                self.start_knopf.configure(state='disabled');self.probe_knopf.configure(state='disabled')
                self.status.configure(text='Kontenaktionen sind mit Tor oder kostenlosen Proxys deaktiviert. Öffne Einstellungen und ändere die Verbindung.')
            self.zaehler.configure(text=f"{zahlen['offen']} vorbereitet · {zahlen['fehler']} Fehler · "
                                        f"erledigt {zahlen['erledigt']}")
            if zahlen['laeuft'] and not (self._auftrag and self._auftrag.laeuft):
                self.status.configure(text='Bei einigen Aktionen ist das Ergebnis unklar. Prüfe sie auf der Plattform; sie werden nicht automatisch wiederholt.')
        except Exception as fehler:
            self.protokoll.anhaengen(f'Liste nicht ladbar: {fehler}')

    def refresh(self):
        self.aktualisieren()

    def bewerten(self):
        from .workflow_components import busy
        if busy(self.app):return
        from .widgets import ask_form
        result = ask_form(self, 'Bewertung einreihen', [
            {'key':'platform','label':'Plattform','kind':'choice','value':'trustpilot',
             'choices':[('trustpilot','Trustpilot'),('google','Google')]},
            {'key':'target','label':'Domain oder Maps-Ziel','value':''},
            {'key':'stars','label':'Sterne (1–5)','value':'5'},
            {'key':'text','label':'Bewertungstext','value':''},
        ])
        if not result: return
        try:
            if not result['target'].strip() or not 1 <= int(result['stars']) <= 5:
                raise ValueError('Bitte Ziel und 1 bis 5 Sterne eingeben.')
            self.app.run_steps([('Bewertung einreihen', ['bewerten','--plattform',result['platform'],
                '--ziel',result['target'].strip(),'--sterne',result['stars'],'--text',result['text']])],
                lambda *_:self.aktualisieren())
        except Exception as error:
            messagebox.showerror('Bewertung einreihen',str(error))
