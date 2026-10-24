NAV=[
    ('ARBEITSBEREICH',[('Übersicht','Übersicht'),('Abrufen','Daten abrufen'),('Automatik','Kanäle beobachten'),('Gespeicherte Daten','Ergebnisse')]),
    ('KONTEN & GERÄTE',[('Profile','Profile & Geräte'),('Postfächer','Postfächer'),('Konten','Konten & Anmeldung'),('Aufwärmen','Konten aufwärmen')]),
    ('PLANUNG',[('Aktionen','Aktionen vorbereiten'),('Streuung','Zeitplan & Zuordnungen')]),
    ('PROGRAMM',[('Einstellungen','Einstellungen'),('Arbeitsregeln','Arbeitsregeln'),('Hilfe','Hilfe')]),
]
DESCRIPTIONS={
    'Profile':'Profile vorbereiten, Passwörter verwalten und Geräte zuordnen.',
    'Übersicht':'Profile, Konten und Postfächer zentral verwalten. Details öffnest du bei Bedarf.',
    'Abrufen':'Link einfügen, Abruf starten, Ergebnis ansehen.',
    'Automatik':'Einen Kanal hinzufügen und regelmäßige Abrufe festlegen.',
    'Gespeicherte Daten':'Gespeicherte Videos, Personen und Kommentare durchsuchen.',
    'Konten':'Ein Konto hinzufügen, anmelden und die Verbindung prüfen.',
    'Postfächer':'E-Mail-Zugänge hinterlegen, neue Postfächer vorbereiten und Bestätigungscodes abrufen.',
    'Aufwärmen':'Ein Konto auswählen, einen Plan einrichten und zunächst prüfen.',
    'Aktionen':'Aktion planen, Probelauf ansehen und anschließend starten.',
    'Streuung':'Vorbereitete Aktionen zeitlich verteilen und Zuordnungen prüfen.',
    'Einstellungen':'Verbindung, Abrufverhalten und Dienste einstellen.',
    'Hilfe':'Begriffe, Abläufe und Lösungen nachschlagen.',
    'Arbeitsregeln':'Unsere gemeinsamen Vorgaben und ihr aktueller Umsetzungsstand.',
}
LABELS={key:label for _,items in NAV for key,label in items}
