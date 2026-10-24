"""Live Android view and bounded input for the current owned registration job."""
import json, time, uuid
from pathlib import Path
import tkinter as tk
from tkinter import ttk, simpledialog
from .hintergrund import cfg_of

def validate(app,event):
    process=getattr(getattr(app,'job',None),'proc',None)
    if process is None:return False
    from ..net.deadline import descendants
    allowed={process.pid,*[pid for pid,_name in descendants(process.pid)]}
    base=Path(cfg_of(app).profiles_dir).resolve().parent/'android-sessions'
    folder=Path(event['directory']).resolve()
    return event['pid'] in allowed and folder.parent==base and folder.name.startswith('session-') and folder.is_dir() and event['proxy'] is True

class AndroidDialog(tk.Toplevel):
    def __init__(self,app,event):
        if not validate(app,event):raise ValueError('Android-Fenster gehört nicht zum laufenden Testauftrag.')
        super().__init__(app)
        self.app=app;self.directory=Path(event['directory']).resolve();self.factor=1;self.photo=None;self.timer=None;self.stamp=None
        self.source_size=None;self.seen_at=None;self.display_ready=False;self.autofill_available=False;self.input_buttons=[]
        self.credentials_required=False
        self.title('Socialscraper – Android-Registrierung');self.transient(app)
        ttk.Label(self,text='Android-Testgerät · Internet ausschließlich über Proxy/Tor',padding=10).pack()
        ttk.Label(self,text='Google-Konto erstellen und Bestätigungen selbst übernehmen.\nGoogle kann auch im Emulator Telefon, CAPTCHA oder QR verlangen.',padding=6).pack()
        instructions=''
        try:
            data=json.loads((self.directory/'instructions.json').read_text(encoding='utf8'))
            if data.get('version')==1 and all(isinstance(data.get(k),str) and len(data[k])<=254 for k in ('address','first_name','last_name')):
                instructions='Zieladresse: '+data['address']+'\nName: '+data['first_name']+' '+data['last_name']
        except (OSError,ValueError,TypeError,AttributeError):pass
        if instructions:ttk.Label(self,text=instructions,wraplength=500,padding=6).pack()
        self.stage=tk.StringVar(value='Aktueller Schritt wird gelesen …')
        ttk.Label(self,textvariable=self.stage,wraplength=500,padding=6).pack()
        self.screen=tk.Label(self,borderwidth=0,anchor='nw');self.screen.pack()
        self.screen.bind('<Button-1>',self.tap)
        bar=ttk.Frame(self,padding=8);bar.pack(fill='x')
        for label,action in (('Zurück',lambda:self.send('key',key='4')),('Weiter/Enter',lambda:self.send('key',key='66')),
                            ('Text eingeben',self.text),('Kontodaten eintragen',lambda:self.send('fill')),
                            ('↓',lambda:self.send('scroll',direction='down')),('↑',lambda:self.send('scroll',direction='up'))):
            button=ttk.Button(bar,text=label,command=action);button.pack(side='left',padx=2)
            self.input_buttons.append(button)
            if label=='Kontodaten eintragen':self.fill_button=button
        ttk.Button(self,text='Auftrag abbrechen',command=app.cancel).pack(pady=8)
        self.confirm_button=ttk.Button(self,text='Verwendetes Kennwort bestätigen',command=self.confirm_password)
        self.confirm_button.pack(pady=4)
        self.protocol('WM_DELETE_WINDOW',app.cancel)
        self.refresh();self.lift()
        from ..window_notification import window_opened
        window_opened(self)

    def send(self,action,**values):
        if not self.directory.is_dir() or not self.display_ready or self.source_size is None:return
        if action=='fill' and not self.autofill_available:return
        if action=='confirm_password' and not self.credentials_required:return
        filename=self.directory/'inputs'/f'{time.time_ns():020d}-{uuid.uuid4().hex}.json'
        temporary=filename.with_suffix('.tmp')
        temporary.write_text(json.dumps({'action':action,'size':list(self.source_size),'seen_at':self.seen_at,**values}),encoding='utf8');temporary.replace(filename)

    def tap(self,event):
        if self.photo and 0<=event.x<self.photo.width() and 0<=event.y<self.photo.height():
            self.send('tap',x=event.x*self.factor,y=event.y*self.factor)

    def text(self):
        value=simpledialog.askstring('Android-Eingabe','Text für das gerade ausgewählte Android-Feld:',show='*',parent=self)
        if value is not None:self.send('text',value=value)

    def confirm_password(self):
        if not self.credentials_required or not self.display_ready:return
        value=simpledialog.askstring('Testpostfach übernehmen',
            'Die Zieladresse ist bestätigt. Trage das tatsächlich bei Google verwendete Kennwort ein, damit dieses Postfach lokal gespeichert werden kann:',
            show='*',parent=self)
        if value is not None:self.send('confirm_password',value=value)

    def refresh(self):
        try:
            from ..android.google_mail import STAGES
            status=json.loads((self.directory/'status.json').read_text(encoding='utf8'))
            if status.get('version')==1 and status.get('stage') in STAGES:
                self.stage.set(STAGES[status['stage']])
                self.autofill_available=status.get('autofill') is True
                self.credentials_required=status['stage']=='credentials_required'
        except (OSError,ValueError,TypeError,AttributeError):pass
        path=self.directory/'frame.png'
        try:
            stat=path.stat();stamp=stat.st_mtime_ns
            self.display_ready=0<=time.time()-stat.st_mtime<=10
            if stamp!=self.stamp:
                original=tk.PhotoImage(file=str(path))
                max_width=max(300,min(1000,self.winfo_screenwidth()-120))
                max_height=max(200,min(650,self.winfo_screenheight()-366))
                self.factor=max(1,(original.height()+max_height-1)//max_height,(original.width()+max_width-1)//max_width)
                self.photo=original.subsample(self.factor);self.screen.configure(image=self.photo);self.stamp=stamp
                self.source_size=original.width(),original.height();self.seen_at=time.monotonic()
        except (OSError,tk.TclError):self.display_ready=False
        if not self.display_ready:self.stage.set('Warte auf ein aktuelles Android-Bild. Eingaben sind vorübergehend gesperrt.')
        for button in self.input_buttons:button.state(['!disabled'] if self.display_ready else ['disabled'])
        if not self.autofill_available:self.fill_button.state(['disabled'])
        self.confirm_button.state(['!disabled'] if self.display_ready and self.credentials_required else ['disabled'])
        if self.directory.is_dir():self.timer=self.after(300,self.refresh)

    def destroy(self):
        if self.timer:
            try:self.after_cancel(self.timer)
            except tk.TclError:pass
        super().destroy()
