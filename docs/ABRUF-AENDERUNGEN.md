# Abrufresultate und sichere Wiederholung

Auftrag vom 11.10.2026: verständliche Abrufresultate sowie Anmeldung, Abbruch und erneuten Versuch absichern.

## Ausprobieren

Dieser Stand ist in das Programm übernommen: **Socialscraper.exe** (oder Socialscraper.cmd) starten. Nach einem Umzug oder auf einem anderen Rechner zuerst **Einrichten.cmd** ausführen.

## Sichtbare Änderungen

- Nur Videodaten gewählt: „Nur Videodaten gespeichert — so gewählt.“
- Plattform meldet null Kommentare: eigener Hinweis; kein vermuteter Nullwert nach einem Fehler.
- Kommentarabruf fehlgeschlagen: gespeicherte Videodaten und Kommentare stehen separat vom fehlenden Teil samt nächstem Schritt.
- Nach einem Abbruch bleibt sichtbar, wie viele Videos und Kommentare durch bestätigte Speicherpunkte erhalten sind. Die Knöpfe werden erst wieder freigegeben, wenn der laufende Prozess beendet wurde.
- **Gespeicherte Ergebnisse öffnen** führt zum erhaltenen Bestand. **Erneut versuchen** behält Konto und Kommentarzahl des ursprünglichen Auftrags und lässt abgeschlossene Video-Links aus. Eine abgebrochene Profilsuche wird erneut ausgeführt; vorhandene Einträge bleiben dabei erhalten und werden über ihre Kennungen aktualisiert.
- Eine fehlende Anmeldung auf einer Plattform blockiert die anderen Plattformen eines gemischten Abrufs nicht.

Videodaten werden bei TikTok und Instagram vor dem Kommentarabruf gespeichert; hinzugekommene Kommentare werden während des Abrufs gesichert. Wiederholungen löschen keine früher gespeicherten Kommentare. Die geplante maximale Kommentarzahl ist eine Abrufgrenze und kein Beweis für einen vollständigen Kommentarbestand.

## Einfaches Protokoll

Das Protokoll zeigt die Uhrzeit und kurze Sätze: Was passiert gerade, was ist gespeichert und was ist als Nächstes zu tun? Beispiele:

- „Die Angaben zum Video sind gespeichert. Kommentare waren nicht ausgewählt.“
- „Die Anmeldung konnte nicht bestätigt werden. Prüfe sie unter ‚Konten‘.“
- „Es hat noch nicht geklappt (Versuch 1 von 3). In 2 Sekunden versuchen wir es erneut.“

Interne Programmkennungen, Fehlerklassen und lange Video-Kennungen erscheinen nicht im Hauptprotokoll. Der Knopf **Technische Details** öffnet die ursprünglichen Meldungen separat. **Protokolldatei öffnen** zeigt dasselbe einfache Protokoll wie das Fenster.

„Keine Kommentare geladen“ wird nicht als „Das Video hat keine Kommentare“ ausgegeben. Die Sammlung enthält Angaben zu Videos; es werden dabei keine Videodateien heruntergeladen. „In deiner Sammlung“ bezeichnet den gesamten gespeicherten Bestand und nicht nur den letzten Abruf.

Ein bereits geöffnetes Fenster einmal schließen und das Programm erneut starten, damit die neuen Texte geladen werden. Gespeicherte Daten bleiben erhalten.

## Konten: klarer nächster Schritt

Wähle ein Konto in der Liste. Die Karte darunter zeigt dessen Anmeldung und genau einen nächsten Schritt:

- Ohne Anmeldung: **Im Browser anmelden**.
- Mit gespeicherter, ungeprüfter Anmeldung: **Anmeldung prüfen**.
- Nach erfolgreicher Prüfung: **Zum Abrufen**.

**Weitere Möglichkeiten** enthält weiterhin Umbenennen, Standardkonto, Abmelden, Entfernen, Passwort-Anmeldung und Übernahme aus einer Datei. Diese Wege wurden erhalten und mitgeprüft.

Eine bloß vorhandene Anmeldedatei wird neutral als „gespeichert · noch nicht geprüft“ angezeigt. Ein Fehler beim Prüfen fordert einen neuen Prüfversuch an; eine nachweislich ungültige Anmeldung fordert eine neue Anmeldung an. Ein Austausch der Anmeldedatei verwirft auch veraltete Bestätigungen und Ergebnistexte. Bestehende Anmeldedateien werden durch einen fehlgeschlagenen Prüfversuch nicht gelöscht.

Die Kontenseite wurde mit 177 Tests ohne Fehler und einem übersprungenen Browserfall geprüft. Der unabhängige Prüfer hat zusätzlich 28 Klick-/Statusfälle, neun Fälle zum Dateiaustausch und beide Desktopgrößen (860 × 620 und 1060 × 790 Pixel) geprüft. Lange Kontonamen und aufgeklappte Zusatzoptionen passen ins Fenster.

## Prüfumfang

Die Regressionen prüfen reale, verborgene Fensterwidgets mit Wegwerf-Datenbanken, simulierte Plattformantworten und einen echten lokalen Prozessabbruch. Angemeldete Aufrufe auf TikTok/Instagram bleiben bis zu einer Anmeldung und Prüfung mit passenden Links offen. KI-Einstufung, Cloud und die PULSE-Anbindung gehören nicht zu diesem Paket.

Die Nachprüfung der Protokolländerung umfasst 123 Tests ohne Fehler und einen bewusst übersprungenen Browserfall. Zusätzlich hat ein unabhängiger Prüfer 13 Fälle an echten Fensterwidgets geprüft. Die vorherige Gesamtprüfung des Abrufstands umfasst 928 Tests ohne Fehler und zehn übersprungene Fälle. Das aktuelle Prüfergebnis steht in **VALIDIERUNG.json**.
