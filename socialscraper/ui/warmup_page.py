from tkinter import messagebox, ttk
from .. import repo, warmup
from ..store import Store
from .common import Tab
from .hintergrund import Auftrag, cfg_of
from .widgets import ask_form
from .workflow_components import steps, table, log, summary, busy as app_busy

MODE_NAMES={'aus':'Ausgeschaltet','schonend':'Schonend','normal':'Normal','zuegig':'Zügig'}
SCOPES={'nur_schauen':'Nur ansehen','liken':'Ansehen und liken','aktiv':'Zusätzlich kommentieren','voll':'Zusätzlich folgen'}

class AufwaermenTab(Tab):
    kuerzel='<Control-Key-9>'
    def build(self):
        self.configure(padding=16)
        self._auftrag=None;self._items={};self._probe=False
        steps(self,'1  Konto auswählen     →     2  Plan einrichten     →     3  Probelauf ansehen')
        self.status=ttk.Label(self,text='Aufwärmen ist optional. Richte zuerst eine erfolgreiche Kontenanmeldung ein.',foreground='#64748b',wraplength=720)
        self.status.pack(anchor='w',pady=(0,14))
        bar=ttk.Frame(self);bar.pack(fill='x')
        self.plan_button=ttk.Button(bar,text='Plan einrichten …',command=self.uebernehmen,style='Primary.TButton',state='disabled');self.plan_button.pack(side='left')
        self.probe_button=ttk.Button(bar,text='Probelauf',command=lambda:self.starten(probe=True));self.probe_button.pack(side='left',padx=8)
        self.start_knopf=ttk.Button(bar,text='Fällige Sitzungen starten',command=self.starten);self.start_knopf.pack(side='left')
        self.abbrechen_knopf=ttk.Button(bar,text='Abbrechen',command=self.abbrechen,state='disabled');self.abbrechen_knopf.pack(side='left',padx=8)
        self.liste=table(self,[('konto','Konto',190),('plattform','Plattform',100),('plan','Plan',170),('bis','Bis',130),('status','Status',150)])
        self.liste.bind('<<TreeviewSelect>>',self.selection_changed)
        self.liste.bind('<Double-1>',lambda e:self.uebernehmen())
        self.protokoll=log(self)
        for button in (self.plan_button,self.start_knopf,self.probe_button):self.app.register_busy(button)

    def _cfg(self):return cfg_of(self.app)
    def selection_changed(self,event=None):
        from ..warmup.sitzung import PLATTFORMEN
        selected=self._markiert()
        available=selected in self._items and self._items[selected]['platform'] in PLATTFORMEN and not app_busy(self.app)
        self.plan_button.configure(state='normal' if available else 'disabled')
    def _markiert(self):
        selected=self.liste.selection()
        return int(selected[0]) if selected else None

    def uebernehmen(self):
        if app_busy(self.app):return
        selected=self._markiert()
        if selected is None:self.status.configure(text='Markiere zuerst ein Konto in der Liste.');return
        account=self._items[selected]
        from ..warmup.sitzung import PLATTFORMEN
        if account['platform'] not in PLATTFORMEN:
            self.status.configure(text='Für diese Plattform ist kein automatisches Aufwärmen verfügbar.');return
        result=ask_form(self,'Aufwärmplan für '+account['label'],[
            {'key':'mode','label':'Intensität','kind':'choice','value':account.get('warmup_modus') or 'normal','choices':list(MODE_NAMES.items())},
            {'key':'scope','label':'Welche Handlungen?','kind':'choice','value':account.get('warmup_umfang') or 'nur_schauen','choices':list(SCOPES.items())},
            {'key':'days','label':'Dauer in Tagen (leer = Vorgabe)','value':''},
        ],intro='Für den Einstieg reicht „Nur ansehen“. Der Probelauf führt keine Handlungen aus.',ok_text='Plan speichern')
        if not result:return
        try:
            days=int(result['days']) if result['days'].strip() else None
            if days is not None and days<0:raise ValueError('Die Dauer darf nicht negativ sein.')
            warmup.setzen(self._cfg().db_path,selected,modus=result['mode'],umfang=result['scope'],tage=days,phase=account.get('phase') or 'web',app_avd=account.get('app_avd'),app_tage=account.get('app_tage'))
            if account.get('phase')=='app' and account.get('app_bis'):
                with Store(self._cfg().db_path) as store:
                    store.db.execute('UPDATE accounts SET app_bis=? WHERE id=?',(account['app_bis'],selected));store.db.commit()
            self.status.configure(text='Plan gespeichert. Als Nächstes kannst du den Probelauf ansehen.');self.refresh()
        except Exception as error:messagebox.showerror('Plan speichern',str(error))

    def starten(self,probe=False):
        if self._auftrag and self._auftrag.laeuft:return
        cfg=self._cfg();self._probe=probe
        self._auftrag=Auftrag(self,lambda melden,stop:warmup.run(cfg.db_path,cfg=cfg,trocken=probe,melden=melden,stop=stop),bei_zeile=self.protokoll.anhaengen,bei_ende=self._fertig)
        if self._auftrag.starten():
            self.start_knopf.configure(state='disabled');self.probe_button.configure(state='disabled');self.abbrechen_knopf.configure(state='normal')
            self.status.configure(text='Probelauf läuft …' if probe else 'Fällige Sitzungen werden ausgeführt …')

    def abbrechen(self):
        if self._auftrag:self._auftrag.abbrechen()
    def _fertig(self,result,error):
        self.abbrechen_knopf.configure(state='disabled')
        self.status.configure(text=f'Vorgang nicht abgeschlossen: {error}' if error else summary(result,probe=self._probe))
        self.protokoll.anhaengen(self.status.cget('text'));self.refresh()

    def refresh(self):
        keep=self._markiert();self.liste.delete(*self.liste.get_children())
        with Store(self._cfg().db_path) as store:accounts=repo.list_accounts(store.db)
        from ..warmup.sitzung import PLATTFORMEN
        self._items={a['id']:a for a in accounts};configured=0
        for a in accounts:
            mode=a.get('warmup_modus');active=mode and mode!='aus' and a.get('warmup_until')
            state='Noch kein Plan' if not mode else 'Ausgeschaltet' if mode=='aus' else 'Eingerichtet'
            try:
                if active and repo.parse(a['warmup_until'])<=repo.now():state='Abgeschlossen';active=False
            except (ValueError,TypeError):state='Zeitplan prüfen';active=False
            configured+=bool(active and a['platform'] in PLATTFORMEN)
            if a['platform'] not in PLATTFORMEN:state='Nicht unterstützt'
            self.liste.insert('', 'end',iid=str(a['id']),values=(a['label'],a['platform'].title(),MODE_NAMES.get(mode,'–'),(a.get('warmup_until') or '')[:10],state))
        if keep in self._items:self.liste.selection_set(str(keep))
        busy=app_busy(self.app)
        self.start_knopf.configure(state='normal' if configured and not busy else 'disabled')
        self.probe_button.configure(state='normal' if configured and not busy else 'disabled')
        from ..net import proxymode
        if proxymode.is_anonymous(self._cfg()):
            self.start_knopf.configure(state='disabled');self.probe_button.configure(state='disabled')
            self.status.configure(text='Aufwärmen ist mit Tor oder kostenlosen Proxys deaktiviert. Öffne Einstellungen und ändere die Verbindung.')
        self.selection_changed()
        if busy:self.plan_button.configure(state='disabled')
    def aktualisieren(self):self.refresh()
