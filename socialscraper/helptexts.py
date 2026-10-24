"""Hilfetexte in einfachem Deutsch (ohne Tk): Begriffe, Erste Schritte, häufige Probleme, Tastenkürzel.

Die Begriffe stehen an einer Stelle; die „?“-Punkte im Fenster und der Hilfe-Tab benutzen dieselben Sätze.
"""

# Schlüssel -> (Name, Erklärung in ein bis zwei Sätzen)
GLOSSARY = {
    'proxy_mode': ('Proxy-Modus',
                   'Hier legst du fest, über welche Internetverbindung die Abrufe laufen. „Aus“ heißt: über deine eigene Verbindung. '
                   'Bei „Tor“ und „Kostenlose Proxys“ sind Konten gesperrt.'),
    'rotation': ('Rotation',
                 'Hast du mehrere Konten bei einer Plattform, benutzt das Programm sie abwechselnd. '
                 'So wird ein einzelnes Konto weniger belastet.'),
    'cooldown': ('Abkühlung',
                 'Ein Konto macht eine Pause, wenn die Plattform Ärger gemacht hat (zum Beispiel ein CAPTCHA). '
                 'Während der Pause wird es nicht benutzt – das schützt das Konto.'),
    'pace': ('Tempo-Profil',
             'Wie vorsichtig ein ganzer Kanal abgeholt wird: „Vorsichtig“ ist langsam und unauffällig, „Schnell“ holt viel auf einmal '
             'und fällt eher auf. Im Zweifel „Normal“.'),
    'follow': ('Verlauf verfolgen',
               'Das Programm ruft das Video regelmäßig neu ab. So siehst du neue Kommentare und wie sich Aufrufe und Likes entwickeln.'),
    'creator': ('Ersteller',
                'Die Person, die das Video veröffentlicht hat.'),
    'commenter': ('Kommentarschreiber',
                  'Eine Person, die unter einem Video etwas geschrieben hat.'),
    'ai_rating': ('KI-Einschätzung',
                  'Eine KI schätzt, ob ein Kommentar positiv, neutral oder negativ klingt. Das ist eine Schätzung, keine Messung, '
                  'und sie kostet etwas Geld.'),
    'groups': ('Gruppen',
               'Damit sortierst du Videos und Personen in Ordner, zum Beispiel nach Thema. Eine Gruppe zu löschen löscht nie Videos.'),
    'daemon': ('Hintergrunddienst',
               'Ein kleines Programm ohne Fenster, das Ziele und Kanäle von allein abruft. Nur solange es läuft, passiert etwas automatisch.'),
    'channel': ('Kanal',
                'Ein ganzes Profil oder ein ganzer Kanal. Das Programm holt alle Videos in kleinen Paketen mit Pausen und beobachtet danach neue.'),
    'target': ('Ziel',
               'Etwas, das der Hintergrunddienst regelmäßig abruft: ein Hashtag, ein Suchbegriff, ein Profil oder ein einzelnes Video.'),
    'login': ('Anmeldung',
              'Für manche Plattformen musst du dich einmal im Browserfenster anmelden. Das Programm merkt sich die Anmeldung, '
              'die Anmeldung bleibt auf deinem Rechner. Zugangsdaten neuer, im Programm registrierter Konten werden ebenfalls lokal geschützt gespeichert.'),
    'comments_per_video': ('Kommentare pro Video',
                           'So viele Kommentare holt das Programm höchstens zu jedem Video. 0 heißt: nur die Videodaten.'),
    'quiet_hours': ('Ruhezeit',
                    'In dieser Uhrzeit wird nichts abgerufen, zum Beispiel nachts. Danach geht es von allein weiter.'),
    'daily_limit': ('Tageslimit',
                    'So viele Videos holt das Programm pro Tag höchstens mit einem Konto. Ist es erreicht, ist am nächsten Tag wieder Platz.'),
    'new_author': ('Neue Autoren',
                   '„Neu“ ist, wer in deiner Datenbank noch nie gesehen wurde.'),
    'headless': ('Browser im Hintergrund',
                 'Abrufe öffnen kein Browserfenster, du wirst bei der Arbeit nicht gestört. Nur die Anmeldung zeigt ein Fenster.'),
}

# (Titel, Schritte)
FIRST_STEPS = [
    ('1. Prüfen, ob alles da ist',
     'Die Übersicht zeigt oben, ob Chrome und die nötigen Teile vorhanden sind. Fehlt etwas, startest du „Einrichten.cmd“ im Programmordner.'),
    ('2. Konto anmelden (nur wenn nötig)',
     'YouTube geht ohne Konto, Reddit meist auch. TikTok-Kommentare, Instagram und X brauchen eine Anmeldung: '
     'Tab „Konten“, Konto wählen, „Im Browser anmelden“, im Fenster selbst anmelden.'),
    ('3. Verbindung wählen',
     'Im Zweifel „Aus (meine eigene Verbindung)“ unter Einstellungen → Proxys. Nur dann funktionieren Konten.'),
    ('4. Ersten Link abrufen',
     'Tab „Abrufen“: Link einfügen (Strg+V), „Abrufen starten“ oder Strg+Enter. Das Programm holt das Video und die Kommentare.'),
    ('5. Ergebnis ansehen',
     'Tab „Gespeicherte Daten“: Videos und Kommentare ansehen, sortieren, in Gruppen ordnen und mit „Export …“ als Excel-Datei speichern.'),
]

# (Frage, [Antwortsätze], Aktion oder None); Aktion = (Knopftext, Tab, Ziel)
PROBLEMS = [
    ('Die Anmeldung klappt nicht',
     ['Prüfe zuerst unter Einstellungen → Proxys, dass der Proxy-Modus auf „Aus“ steht. Bei „Tor“ und „Kostenlose Proxys“ sind Konten gesperrt.',
      'Ein Browserfenster muss sich öffnen, und du meldest dich dort selbst an. Schließe das Fenster erst, wenn das Programm „bestätigt“ meldet.',
      'Läuft der Hintergrunddienst gerade, kann es sein, dass der Browser belegt ist. Warte kurz oder stoppe den Dienst im Tab „Automatik“.'],
     ('Zu den Konten', 'Konten', None)),
    ('Der Abruf liefert nichts',
     ['Schau in „Probleme“ (unten rechts): Dort steht in einfachen Worten, was schiefging.',
      'Manche Videos haben keine öffentlichen Kommentare, oder die Plattform zeigt sie nur angemeldeten Konten.',
      'Steht „Kommentare pro Video“ auf 0, werden nur die Videodaten geholt.'],
     ('Probleme ansehen', 'Probleme', None)),
    ('Es kam ein CAPTCHA oder ein Menschentest',
     ['Das ist normal, wenn eine Plattform misstrauisch wird. Das Konto macht dann automatisch eine Pause („Abkühlung“) – '
      'bei mehreren Konten wechselt das Programm zum nächsten.',
      'Hilft nicht: ein paar Stunden warten, Tempo auf „Vorsichtig“ stellen, weniger Kommentare pro Video holen.',
      'Löse ein CAPTCHA nur selbst im Anmeldefenster, nie automatisch.'],
     ('Zu den Konten', 'Konten', None)),
    ('Der Proxy-Modus macht Ärger',
     ['„Tor“ und „Kostenlose Proxys“ gehen nur für öffentliche Abrufe ohne Konto. Für Konten, Instagram, X und TikTok-Kommentare brauchst du „Aus“.',
      'Auf der Übersicht steht dazu ein Hinweis mit einem Knopf, der den Modus für dich umstellt.'],
     ('Zu den Proxy-Einstellungen', 'Einstellungen', 'proxys')),
    ('Nichts passiert von allein',
     ['Automatische Abrufe laufen nur, wenn der Hintergrunddienst läuft. Auf der Übersicht siehst du das und kannst ihn starten.',
      'Der Dienst kann auch mit Windows starten (Tab „Automatik“ oder Einstellungen → Automatik-Dienst).'],
     ('Zur Automatik', 'Automatik', None)),
    ('Die Stimmung lässt sich nicht bestimmen',
     ['Dafür braucht das Programm einen Schlüssel von Anthropic (Claude). Trage ihn unter Einstellungen → KI / Stimmung ein.'],
     ('Zu den KI-Einstellungen', 'Einstellungen', 'ki')),
    ('Das Programm ist langsam oder hängt',
     ['Ein Kanal wird bewusst in kleinen Paketen mit Pausen geholt. Das ist gewollt, damit die Plattform nicht misstrauisch wird.',
      'Mit „Abbrechen“ unten rechts beendest du einen Abruf. Bereits gespeicherte Daten bleiben erhalten.'],
     None),
    ('Meine Daten sind weg oder ich will eine Sicherung',
     ['Deine Daten liegen in „data\\scrape.db“. Unter Einstellungen → System legst du eine Sicherungskopie an.'],
     ('Zur Sicherung', 'Einstellungen', 'system')),
]

# (Tasten, Wirkung)
SHORTCUTS = [
    ('F1', 'Diese Hilfe öffnen'),
    ('Strg+1 … Strg+6', 'Zu einem Tab springen (1 = Übersicht)'),
    ('Strg+V', 'Im Link-Feld: Link einfügen (mehrere Zeilen sind möglich)'),
    ('Strg+Enter', 'Im Tab „Abrufen“: Abruf starten'),
    ('Strg+S', 'Im Tab „Einstellungen“: speichern'),
    ('Strg+A', 'In Listen: alles markieren'),
    ('Entf', 'In Listen: Auswahl entfernen oder löschen (mit Rückfrage)'),
    ('Doppelklick', 'Öffnet das Gewählte (Video im Browser, Ziel bearbeiten, Kanal-Einzelheiten)'),
    ('Esc', 'Dialog schließen'),
    ('Enter', 'Dialog bestätigen'),
]

MODE_EXPLAIN = {
    'aus': 'Abrufe laufen über deine eigene Verbindung. Konten gehen.',
    'liste': 'Abrufe laufen über deine eigenen Proxys. Konten gehen.',
    'frei': 'Nur ohne Konto. Konten sind gesperrt.',
    'tor': 'Nur ohne Konto. Konten sind gesperrt.',
}


def term(key):
    """(Name, Erklärung) eines Begriffs."""
    return GLOSSARY[key]


def tip(key):
    """Text für einen „?“-Hinweis."""
    name, text = GLOSSARY[key]
    return f'{name}: {text}'
