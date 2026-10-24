import tkinter as tk
from tkinter import ttk
from .hintergrund import Protokoll

def busy(app):
    return getattr(app,'job',None) is not None or any(getattr(getattr(page,'_auftrag',None),'laeuft',False) for page in getattr(app,'tabs',{}).values())

def steps(parent, text):
    frame=ttk.Frame(parent,padding=(12,10),style='Hint.TFrame');frame.pack(fill='x',pady=(0,12))
    label=ttk.Label(frame,text=text,font=('Segoe UI',10),style='Hint.TLabel',justify='left')
    label.pack(anchor='w',fill='x')
    frame.bind('<Configure>',lambda e:label.configure(wraplength=max(180,e.width-24)))
    return frame

def table(parent, columns):
    frame=ttk.Frame(parent);frame.pack(fill='both',expand=True,pady=(10,0))
    frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
    tree=ttk.Treeview(frame,columns=[c[0] for c in columns],show='headings',height=9)
    for key,label,width in columns:
        tree.heading(key,text=label);tree.column(key,width=width,minwidth=min(width,80),stretch=True)
    tree.grid(row=0,column=0,sticky='nsew')
    y=ttk.Scrollbar(frame,command=tree.yview);y.grid(row=0,column=1,sticky='ns')
    x=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview);x.grid(row=1,column=0,sticky='ew')
    tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
    return tree

def log(parent):
    body=ttk.Frame(parent);body.pack(fill='x',pady=(10,0))
    pane=ttk.Frame(body)
    text=tk.Text(pane,height=5,relief='flat',borderwidth=0)
    text.pack(fill='both',expand=True)
    shown=[False]
    def toggle():
        shown[0]=not shown[0]
        if shown[0]:pane.pack(fill='x',pady=(6,0))
        else:pane.pack_forget()
        button.configure(text='Details ausblenden' if shown[0] else 'Details anzeigen')
    button=ttk.Button(body,text='Details anzeigen',command=toggle);button.pack(anchor='w')
    return Protokoll(text,hoehe=5)

def summary(result, *, probe=False):
    if result.get('abgebrochen'):return 'Abgebrochen. Bereits abgeschlossene Schritte bleiben gespeichert.'
    if result.get('gruende'):
        reasons=', '.join(result['gruende'])
        return f'Der Plan ist noch nicht vollständig ausführbar: {reasons}. Prüfe die Kontenanmeldung und den Zeitplan.'
    if probe and 'konten' in result and not result['konten']:
        return 'Aktuell ist keine Sitzung fällig. Prüfe Plan, Anmeldung und Aktivitätszeiten des Kontos.'
    if probe:return 'Probelauf abgeschlossen. Es wurden keine Aktionen auf einer Plattform ausgeführt.'
    return f"Abgeschlossen: {result.get('erledigt',result.get('sitzungen',0))} erfolgreich · {result.get('fehler',0)} fehlgeschlagen."
