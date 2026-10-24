"""User-facing paths through the existing features, without executing them."""
def steps(task, state):
    connected=state.get('connected',0)>0
    connection=bool(state.get('proxy_configured')) and not state.get('anonymous',False)
    data=state.get('comments',0)>0
    paths={
        'collect':[
            ('Link abrufen','Füge einen Video- oder Beitragslink ein. Plattform und passende Einstellungen werden erkannt.','Abrufen',False),
            ('Ergebnisse ansehen','Gespeicherte Videos und Kommentare findest du hier. Du kannst sie filtern und exportieren.','Gespeicherte Daten',False)],
        'account':[
            ('Verbindung vorbereiten','Wähle deine eigene Proxy-Liste. Die aktive Verbindung wird beim Auftrag geprüft.','Einstellungen',connection),
            ('Konto hinzufügen und anmelden','Füge ein vorhandenes Konto hinzu und schließe die Anmeldung im Browser ab.','Konten',connected)],
        'register':[
            ('Verbindung vorbereiten','Hinterlege deine eigene Proxy-Liste. Tor und kostenlose öffentliche Proxys sind für Konten nicht vorgesehen.','Einstellungen',connection),
            ('Profil vorbereiten','Lege Personendaten und Passwort fest und ordne dem Profil seine Verbindung zu.','Profile',False),
            ('Postfach bereitstellen','Füge ein vorhandenes Postfach hinzu. Es wird für Bestätigungscodes benötigt.','Postfächer',state.get('mailboxes',0)>0),
            ('Registrierung öffnen','Wähle unter Konten „Neues Konto registrieren“. Schließe Rückfragen im sichtbaren Fenster ab.','Konten',False)],
        'act':[
            ('Kommentare speichern','Aktionen benötigen ein gespeichertes Ziel. Rufe zuerst die passenden Kommentare ab.','Abrufen',data),
            ('Konto verbinden','Eine gespeicherte Anmeldung wird für die gewählte Plattform benötigt.','Konten',connected and connection),
            ('Aktion planen und prüfen','Stelle die Aktion zusammen. Ein Probelauf zeigt den Ablauf, ohne etwas auf der Plattform auszuführen.','Aktionen',False)],
        'results':[
            ('Ergebnisse öffnen','Suche nach Videos, Personen oder Kommentaren. Filter und Export liegen in derselben Ansicht.','Gespeicherte Daten',False)],
    }
    return [{'title':title,'body':body,'target':target,'done':done} for title,body,target,done in paths[task]]

def next_step(items):
    return next((i for i,item in enumerate(items) if not item['done']),None)
