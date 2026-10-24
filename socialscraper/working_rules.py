"""Shared, user-visible agreements. Coverage is explicit, not a success claim."""
VERSION = '24.10.2026 · Webseiten-Formulare vor dem Absenden prüfen'
RULES = (
    ('Besonders die Webseiten-Anmeldungen in Profilwerk absichern',
     'Webseiten-Anmeldungen vor vermeidbaren eigenen Fehlern schützen: eindeutige Felder, vollständige Eingaben, '
     'richtige Auswahlwerte und kontrolliertes Absenden. Gemeint sind die von Profilwerk bedienten Anbieterseiten.',
     'Eingebaut: gemeinsame Browser-Feldsuche verweigert mehrdeutige sichtbare Ziele und berücksichtigt verdeckte Duplikate. '
     'Abweichende vorhandene Eingaben werden nicht überschrieben. Auswahllisten werden anhand angebotener Beschriftungen '
     'zugeordnet und nach der Auswahl zurückgelesen; Monatsnamen statt Annahmen über Nummerierung ab null oder eins. '
     'Gemeinsame Weiter-/Absendeaktionen prüfen den aktuellen Formularbereich auf bekannte unveränderte Eingaben, '
     'native Ungültigkeit, sichtbare Fehler und unbekannte Felder; dieselbe Formularstufe wird nicht blind erneut abgesendet. '
     'Bekannte Navigation zum Formular ist gesondert im Quellcode gekennzeichnet. Zugangseingaben sind auf hinterlegte HTTPS-Anbieterhosts beschränkt. '
     'Google/Gmail, Outlook, Instagram, X, TikTok-Web und Trustpilot kontrollieren zusätzliche Pflicht-Eingabeergebnisse. '
     'WEB.DE/GMX lesen bereits bearbeitete Felder erneut und prüfen gespeicherte Anrede und Maildomain. '
     'Der dauerhafte Plattform-Versuchsstand umfasst nun auch die Browserregistrierungen. Vorhandene angemeldete Sitzungen '
     'gelten nicht als neu erstellte Konten; eine erkannte neue Sitzung wird vor dem Schließen festgehalten. '
     'Manuell veränderte Browser-Zugangsdaten verhindern die Speicherung eines unbestätigten generierten Kennworts. '
     'Postfach-Browserversuche werden ab dem Formularlauf als prüfpflichtig behandelt statt automatisch neu begonnen. '
     'Offen: praktische Fehler-/Gesamtläufe, neue Hosts/Formularvarianten, nicht unterstützte Widgets und Wiederaufnahmeassistent. '
     'Passwortlose Browserwege benötigen die Übernahme eines vorhandenen Zugangs; keine fertigen Passwörter behaupten. '
     'Dies ist keine fehlerfreie Live-Abnahme. Keine Tests oder echten Registrierungen in dieser Quellenrunde ausgeführt.'),
    ('Vermeidbare eigene Fehler vor einer Registrierung abfangen',
     'Registrierungen gegen falsche oder unvollständige Eingaben, doppelte Aktionen und versehentliche Neustarts härten. '
     'Vor dem Absenden den aktuellen Formularstand prüfen. Bei unklarem Abschluss den vorhandenen Versuch erhalten. '
     'Eine hundertprozentige Erfolgsgarantie lässt sich daraus nicht ableiten; Anbieterantworten bleiben extern.',
     'Eingebaut: TikTok prüft gespeicherten Postfachzugang und an Android übergebbare Zugangsdaten vor der Anmeldung. '
     'Ein dauerhafter Versuchsstand sperrt einen erneuten Start mit demselben Profil oder derselben E-Mail-Adresse auch nach Absturz. '
     'Ein erkannter Benutzername wird vor dem Schließen des Geräts festgehalten; erst nach den lokalen Speicherschritten wird der Versuch als gespeichert markiert. '
     'TikTok-App-Daten werden beim Registrierungsstart nicht mehr gelöscht. Eine bestehende Anmeldung wird nicht als neue Registrierung übernommen. '
     'Die Anfangsnavigation verwendet eindeutige vollständige Beschriftungen und verlangt eine erkannte abgemeldete Ausgangsansicht. '
     'Vor dem Weiter-Klick werden sämtliche sichtbaren Zugangsfelder erneut zugeordnet und geprüft; unbekannte Felder verhindern das automatische Absenden. '
     'Soweit die App Zeichenanzahl oder Text offenlegt, werden unvollständige Kennworteingaben abgefangen. Eine Passwortmaske ist kein Nachweis des vollständigen Geheimnisses. '
     'Unklare native Browser-Eingaben/Klicks werden als Stopp weitergegeben statt an einem anderen Ziel erneut versucht. '
     'Offen: praktische Abnahme, weitergehende Wiederaufnahme/Übernahme eines unterbrochenen TikTok-Zugangs, unbekannte App-Ansichten '
     'und verbleibende anbieterspezifische Browserwege. Alte Versuche vor diesem Schutz haben keinen nachträglich erzeugten Datensatz. '
     'Keine echten Registrierungen oder Tests durch diese Quellenänderung ausgeführt.'),
    ('Bedienbarkeit und Ergebnisse zusätzlich zum Eingabetempo prüfen',
     'Mausgeschwindigkeit, Klickpause und Tipptempo reichen für zuverlässige Abläufe nicht aus. '
     'Vor Aktionen Sichtbarkeit, Bedienbarkeit und aktuelles Ziel prüfen; Eingaben und Folgezustand kontrollieren. '
     'Gespeicherte Angaben in eindeutig beschrifteten Auswahlfeldern selbst übernehmen und bekannte Formulare begrenzt scrollen. '
     'Wartezeiten und Versuche begrenzen; Abbruch beachten. Keine zusätzlichen Tempo-Regler dafür verlangen.',
     'Eingebaut: gemeinsame Android-Prüfung aus zwei aktuellen Ansichten vor bekannten Feld-/Weiter-/Ablehnen-Aktionen. '
     'Verdeckte Ziele, fremde Berechtigungsdialoge und verdeckte Wischstrecken halten die Bedienung an. '
     'Eine beobachtete Oberflächenänderung bestätigt nur eine Reaktion, noch keinen Kontoabschluss. '
     'WEB.DE kann eindeutig beschriftete native Listen für gespeichertes Land, Anrede/Geschlecht und Datum bedienen, '
     'den übernommenen Wert erneut lesen und innerhalb erkannter Formularbereiche höchstens viermal je Stufe scrollen. '
     'Listenwahl und Absenden werden nach unklarem Abschluss nicht automatisch wiederholt. '
     'TikTok prüft vor dem Fokussieren bekannter numerischer Datumsfelder ebenfalls die Bedienbarkeit. '
     'Gemeinsame Browser-Eingaben lesen den eingetragenen Wert zurück; beim Tippen wird der Feldfokus kontrolliert. '
     'Browserklicks prüfen freie, stabile Ziele mit begrenzter Wartezeit. '
     'Offen: unbekannte Datumsräder, andere Listenoberflächen, Unicode-Android-Eingabe und Live-Abnahme. '
     'Verdeckte Android-Kennwörter sind nicht als Klartext zurücklesbar; der tatsächliche Login/Mailabruf bleibt Abschlussnachweis. '
     'Keine neu gestartete Registrierung oder Behauptung eines vollständig erfolgreichen Gesamtlaufs aus diesen Quellenänderungen.'),
    ('Kostenpflichtige und ähnliche Zusatzangebote automatisch ablehnen',
     'Alle kostenpflichtigen Angebote, Abos, Testabos, Upgrades und vergleichbaren optionalen Zusatzangebote '
     'ohne Rückfrage ablehnen. Den kostenlosen Registrierungsweg fortsetzen. Keine Testphase oder Buchung starten.',
     'Eingebaut: gemeinsame Ablehnungsregel für WEB.DE-/TikTok-Android und die gemeinsamen Browser-Registrierungsroutinen. '
     'Eindeutige Ablehnen-, Nein-danke- und Kostenlos-fortfahren-Schaltflächen werden innerhalb erkannter Angebote verwendet. '
     'Bekannte Kauf-/Testabo-Aktionen blockieren allgemeines Weiter-/Absenden. Ein fehlender oder unklarer Ablehnen-Knopf '
     'wird als technische Grenze gemeldet; daraus entsteht keine neue Kaufentscheidung für den Nutzer. '
     'Keine wiederholten Ablehnungsversuche nach unklarem Klickabschluss. '
     'Offen: weitere Anbietervarianten und echte Live-Abnahme; keine universelle Erkennung unbekannter Angebotsoberflächen bestätigt.'),
    ('Protokoll für normale Nutzer verständlich machen',
     'Im sichtbaren Protokoll in einfachem Deutsch sagen, was gerade geschieht, was bestätigt ist und welcher nächste Schritt nötig ist. '
     'Technische Kennungen und Rohdaten gehören in die technischen Details. Kontoerstellung und erfolgreiche Abschlussprüfung unterscheiden.',
     'Eingebaut: lesbare Registrierungsschritte im Hauptprotokoll statt reiner Statusleistenanzeige; klare Meldungen bei nötiger Hilfe, '
     'offenem E-Mail-Zugriff und tatsächlich bestätigtem Abschluss. Android-Vorbereitung beschreibt das virtuelle Handy. '
     'Hauptprotokoll und weitere Auftragsprotokolle verwenden dieselbe sprachliche Aufbereitung, Zeitangaben und Hinweise. '
     'Direkt wiederholte gleiche Meldungen erscheinen nur einmal; technische Fehlerausgaben bleiben für die Diagnose zugänglich. '
     'Neue gewöhnliche WEB.DE-Aktionen und Angebotsablehnungen melden den ausgeführten Schritt, ohne allein daraus Erfolg abzuleiten. '
     'Offen: visuelle und praktische Abnahme im laufenden Programm.'),
    ('Routineaufgaben ohne unnötige Rückfragen erledigen',
     'Alle eindeutig bekannten normalen Registrierungsschritte selbstständig ausführen: gespeicherte Angaben, '
     'Weiter, kostenlose Kontoerstellung, Mailcode, gewöhnliche Abschlussnavigation und Speicherung. '
     'Nur für erforderliche menschliche Bestätigungen, fehlende Angaben oder neue Entscheidungen unterbrechen. '
     'Eine noch fehlende Formularerkennung ist eine offene technische Aufgabe, keine grundsätzlich menschliche Pflicht.',
     'Eingebaut: WEB.DE bedient eindeutig erkannte normale Weiter- und kostenlose Registrierungsaktionen nach Feldprüfung. '
     'Bekannte erforderliche AGB-/Datenschutzkästchen werden ausgewählt, erkannte optionale Werbeauswahl wird abgewählt. '
     'Cookie-Dialoge folgen der vorhandenen gespeicherten Einstellung. Bekannte Postfach-/Menüknöpfe öffnen die Abschlussansicht. '
     'Feldstufen und Aktionen haben begrenzte Einmalzuordnungen; keine blinde Registrierungs- oder Klickschleife. '
     'TikTok versucht beschriftete numerische Geburtstagsfelder und identifizierte native Zahlenwähler mit dem gespeicherten Datum. '
     'Bekannte optionale Einführungsschritte werden übersprungen; das eigene Profil bleibt der Kontonachweis. '
     'Nach erkennbar erledigten manuellen Schritten entfällt ein zusätzlicher Weiter-prüfen-Klick. '
     'TikTok kann im gleichen Versuch bei bekannten Folgefeldern fortsetzen, ohne App-Daten erneut zu löschen. '
     'Offen: unbekannte Drehräder/Formularvarianten, Browser-Vertragsformulare, andere Anbieter und echte Live-Abnahme. '
     'CAPTCHA und Telefonzugang bleiben beim Nutzer. Kostenpflichtige und ähnliche Zusatzangebote werden abgelehnt.'),
    ('E-Mail und Passwort automatisch übernehmen',
     'Gespeicherte E-Mail-Adressen und Kennwörter selbstständig in eindeutig erkannte Felder eintragen. '
     'Dafür keinen zusätzlichen manuellen Füllschritt verlangen. Nur echte Rückfragen oder unklare Felder anhalten.',
     'Eingebaut: WEB.DE füllt eindeutig erkannte leere Eingaben beim ersten Erkennen einer bekannten Formularstufe. '
     'Die Stufe wird über die sichtbare Fortschrittsnummer erkannt; Tastatur und Fokus lösen keine neue automatische Eingabe aus. '
     'Bei Fehlern kein automatischer zweiter Füllversuch. Die zusätzliche Füllschaltfläche bleibt als freiwillige Hilfe. '
     'TikTok liest E-Mail-, Passwort- und Codefelder anhand des aktuellen Android-Feldbaums, prüft Feldfokus und lesbare Werte. '
     'Gemeinsam sichtbare Zugangsfelder werden vor dem Weitergehen gefüllt; vorbereitete Werte bleiben erhalten. '
     'SMS- und unbekannte Codeabfragen erhalten keinen E-Mail-Code. Nach Mailabruf wird die Feldart erneut geprüft. '
     'Ein eindeutig erkannter Weiter-Klick allein gilt nicht als Kennwortänderung. '
     'CAPTCHA, Telefonprüfung, unbekannte Formulare und unbestätigte manuelle Kennwörter bleiben offen. '
     'Offen: Funktionstest mit echten App-Ansichten und vollständiger erfolgreicher Gesamtlauf.'),
    ('WEB.DE-App als regulärer begleiteter Weg',
     'Die WEB.DE-APK mit dem vorbereiteten Profil, gespeichertem Postfachkennwort und eigenem Proxy verwenden. '
     'Kein fertiges Postfach allein wegen eines sichtbaren Formulars oder gespeicherter Zugangsdaten melden.',
     'Eingebaut: Postfächer → WEB.DE → Android verwendet den eigenen AVD im Hintergrund und die Geräteansicht bei Rückfragen. '
     'Ein vorbereitetes Profil ist erforderlich; Personendaten müssen zum Anbieterformular passen. '
     'Eindeutig beschriftete leere Eingaben werden in bekannten Formularstufen automatisch gefüllt; die Füllschaltfläche ist zusätzlich verfügbar. '
     'Vorhandene Eingaben bleiben erhalten. Normale Weiter-Schritte und bekannte erforderliche Bedingungen sind automatisiert; '
     'CAPTCHA, Telefonprüfung und nicht erkannte Entscheidungen brauchen Hilfe. '
     'Die genaue Zieladresse muss im nativen WEB.DE-Kontomenü eindeutig bestätigt sein. '
     'Ein unbestätigtes oder manuell geändertes Kennwort wird ausdrücklich nachgefragt. '
     'Danach folgen die gemeinsame Mailprüfung und Speicherung mit Geräte-/Profilzuordnung. '
     'Ein unterbrochener App-Versuch braucht Prüfung; ein bestätigtes Konto mit offenem Mailabruf wird nicht erneut registriert. '
     'Vorhandene Postfächer im Gerät werden nicht gelöscht. '
     'Offen: Live-Abnahme und weitere Formularvarianten. Der bekannte Android-Weg ist um normale Folgeaktionen erweitert.'),
    ('Android im Hintergrund, Rückfragen bei Bedarf',
     'Emulatoraufträge sollen ohne dauerhaft sichtbares Gerätefenster arbeiten. '
     'Bei CAPTCHA, Telefonprüfung oder anderen notwendigen Eingaben die Geräteansicht öffnen; '
     'automatische Eingaben währenddessen pausieren und erst nach aktueller Zustandsprüfung fortsetzen.',
     'Eingebaut für neue begleitete TikTok-Registrierungen aus Profilwerk: unsichtbarer AVD-Start und '
     'eigene temporäre Geräteansicht bei nicht bedienbarem Geburtsdatum, erkanntem CAPTCHA oder unbestätigtem Formularabschluss. '
     'Tippen, Wischen und begrenzte Texteingabe stammen ausschließlich aus deinen Eingaben. '
     'Weiter prüfen bestätigt keinen Erfolg von sich aus. Die Ansicht lässt sich ausblenden und über die Statusleiste öffnen. '
     'Veraltete Eingaben werden verworfen; Abbruch, Zeitlimit, Gerätebesitz, Proxy und deutsche Systemsprache bleiben erforderlich. '
     'Beobachtete manuelle Kennwortänderungen verhindern eine unbestätigte Speicherung. '
     'Kein automatisches CAPTCHA-Lösen. WEB.DE nutzt die Geräteansicht inzwischen ebenfalls; Browserwege sind noch nicht umgestellt. '
     'Offen: Live-Abnahme des Hintergrundbetriebs und der Rückfrageansicht; kein neuer Kontoabschluss nachgewiesen.'),
    ('Aus einer kompakten Übersicht arbeiten',
     'Die Hauptarbeit soll aus einer kompakten zentralen Übersicht erfolgen. '
     'Nicht sämtliche Formulare und Werkzeuge gleichzeitig in ein Fenster stecken; Details erst bei Bedarf öffnen.',
     'Umgesetzt: gemeinsame Bestandsliste mit Umschaltung zwischen Profilen, Konten und Postfächern. '
     'Zur Auswahl passende Aktionen stehen daneben; Profilbearbeitung, Proxyzuordnung und Hinzufügen öffnen vorhandene Dialoge. '
     'Bestandszahlen, Suche, Passwortvorrat und Abrufvorbereitung sind direkt erreichbar. '
     'Zusätzliche Navigationsbereiche sind aufklappbar; Anleitungen öffnen in einem eigenen Fenster. '
     'Die Übersicht ist der Startbereich. Gespeicherte Stände sind keine automatische Liveprüfung.'),
    ('Oberfläche klar und einheitlich gestalten',
     'Die gesamte Oberfläche soll hell, modern und übersichtlich sein, mit einer dunklen Seitenleiste. '
     'Häufige Aufgaben hervorheben, zusätzliche Werkzeuge geordnet anbieten und verständliches Deutsch verwenden.',
     'Umgesetzt: gemeinsame Farben, Schriftgrößen, Eingabefelder, Tabellen und Schaltflächen. '
     'Navigation mit Bereichssuche und Strg+K; überarbeitete Startseite mit gespeicherten Bestandszahlen. '
     'Profile und Postfächer mit Suchfeldern, eindeutigen Hauptaktionen und verständlichen Leerzuständen. '
     'Zusätzliche Werkzeuge bleiben über aufklappbare Bereiche erreichbar. '
     'Eine optische Überarbeitung bestätigt weder Anmeldungen noch Verbindungen oder Registrierungsabschlüsse.'),
    ('Passwörter vor der Registrierung speichern',
     'Passwörter lokal erzeugen oder eine eigene Liste einmalig einfügen/importieren. '
     'Neue Profile erhalten das nächste freie Listenpasswort in Eingabereihenfolge. '
     'Ein zugeordnetes Passwort bleibt auch nach Abbruch bei seinem Profil; vorhandene Passwörter nicht überschreiben.',
     'Umgesetzt: Profile und Postfächer → Passwortvorrat bietet automatische Erzeugung und Listenmodus. '
     'Neue Profile speichern ihr Plattformpasswort vor der Registrierung; Postfachentwürfe erhalten ein separates Passwort. '
     'Eine leere Liste hält an, doppelte Vorratseinträge werden übersprungen. '
     'Erzeugte Passwörter haben zufällige Zeichen und Längen ohne festes Wort oder Suffix. '
     'Keine Bestätigung eines Kontoabschlusses allein durch gespeicherte Zugangsdaten. '
     'Manuell direkt im Emulator eingegebene Ersatzpasswörter müssen weiterhin ausdrücklich nachgetragen werden. '
     'Eingebaut: WEB.DE hat einen regulären begleiteten AVD-Weg mit Formularhilfe und expliziter Kennwortbestätigung bei Bedarf. '
     'Nicht übernommen: menschenähnliche Passwortmuster zur Tarnung automatisierter Konten oder Umgehung von Clustererkennung. '
     'Die neuen Abläufe sind noch nicht live abgenommen.'),
    ('Android grundsätzlich auf Deutsch',
     'Alle von Profilwerk verwalteten AVD-Emulatoren müssen Deutsch (Deutschland) als erste aktive Systemsprache verwenden. '
     'Die Vorgabe gilt für bestehende und neue Profile, auch bei WEB.DE und TikTok.',
     'Umgesetzt: Der gemeinsame AVD-Start prüft die aktive Sprache vor dem Öffnen der Apps. '
     'Die englische SDK-Standardsprache wird nötigenfalls über die Android-Spracheinstellungen durch Deutsch ersetzt. '
     'Unbekannte abweichende Sprachlisten benötigen eine manuelle Auswahl. Kontodaten bleiben erhalten. '
     'Kann die aktive deutsche Sprache nicht bestätigt werden, wird der Auftrag mit einer Fehlermeldung beendet. '
     'Eine eigene abweichende Spracheinstellung innerhalb einer App ist gesondert zu prüfen.'),
    ('Dauerhafte Anforderungen pflegen',
     'Wichtige neue Projektvorgaben hier eintragen und vor weiterer Projektarbeit lesen. '
     'Wünsche, umgesetzte Funktionen, offene Nachweise und Grenzen ausdrücklich unterscheiden.',
     'Ergänzt am 19.10.2026: feste Personendaten und getrennte Maus-, Klick- und Tippwerte je Profil; Datenimport für ein späteres Tool vorbereitet. '
     'Ein Eintrag ist kein Funktionsnachweis und hebt keine bestehenden Grenzen auf.'),
    ('Registrierungsdaten und Geschlechtsauswahl',
     'Für reale Registrierungen lokal hinterlegte, berechtigt nutzbare Namen und zusammengehörige Anschriften übernehmen. '
     'Name und ausdrücklich gewähltes Geschlecht vor dem Registrierungsstart festlegen. Anrede und Geschlecht getrennt speichern. '
     'Die gespeicherte Auswahl automatisch wählen, wenn sie angeboten wird. '
     'Nicht angebotene Optionen melden, manuelle Auswahl erkennen und bereits erledigte Felder erhalten.',
     'Umgesetzt: Profile → Person und Eingabetempo verwaltet Name, Wohnort und Geschlecht. '
     'Ein späteres Datentool kann geprüfte Angaben importieren; bestehende abweichende Angaben werden dabei nicht überschrieben. '
     'Normale Radio-Beschriftungen werden unabhängig vom internen Feldnamen erkannt. '
     'Keine Angabe wird nicht stillschweigend durch weiblich, männlich oder divers ersetzt. '
     'Offen: passende berechtigt nutzbare lokale Anschrift und angebotene Geschlechtsauswahl für den nächsten Liveversuch. '
     'Gewünscht, nicht übernommen: realistisch erfundene Identitäten mit echten Anschriften zur Verdeckung automatischer Konten. '
     'Der vorhandene Beispielgenerator bleibt als klar gekennzeichnete Offline-Testhilfe erhalten.'),
    ('Nachvollziehbares Ausfülltempo je Profil',
     'Mausgeschwindigkeit, Klickpause und Tippgeschwindigkeit für jedes Profil getrennt speichern. '
     'Die gewählten Werte für neue Aufträge benutzen; Abbruch muss auch während Pausen reagieren.',
     'Umgesetzt: Profile → Person und Eingabetempo. Die gemeinsamen Chrome-/Firefox-Formularaktionen verwenden die Profilwerte. '
     'Bei nativen Android-Registrierungen gelten Tippgeschwindigkeit und Pause vor Berührungen; Android hat hier keinen Mauszeiger. '
     'Neue Profile beginnen mit festen bearbeitbaren Standardwerten. Keine künstlichen Tippfehler in Namen oder Zugangsdaten.'),
    ('Erster Website-Besuch',
     'Über google.de suchen, den geprüften Treffer zur offiziellen Homepage öffnen und dort den Registrierungslink verwenden. '
     'Keine erfundenen Verweise und kein direkter Ersatzaufruf, wenn dieser Weg fehlt.',
     'Browserregistrierungen: gemeinsamer Einstieg aktiv. WEB.DE hat zusätzlich die Tarifübersicht. '
     'Der WEB.DE-Ablauf ist als vollständige Offline-Probe mit vereinfachten Formularseiten und simuliertem IMAP-Transport hinterlegt. '
     'Ablaufvorlage: docs/WEBDE-Blueprint.txt; ein echter Kontoabschluss bleibt gesondert live nachzuweisen. '
     'Weitere Anbieter benötigen bei unbekannten Zwischenschritten deine Hilfe. '
     'Datenabrufe und bestehende Anmeldungen sind noch auf diese Einstiegsregel umzustellen.'),
    ('Profile und Versuche',
     'Nach fehlgeschlagener Registrierung oder Browserfehler beim nächsten Versuch ein neues leeres Browserprofil verwenden. '
     'Verschiedene Konten getrennt halten; keine automatische Versuchsschleife.',
     'Lokale Chrome- und Firefox-Profile werden nach Fehlern getrennt neu angelegt. Alte Dateien bleiben erhalten. '
     'Fehlgeschlagene Formularversuche behalten Zieladresse und Kennwort und erhalten eine neue Profilkennung. '
     'Nach bestätigtem Kontoabschluss wird nur der Mailabruf über den gespeicherten Proxy fortgesetzt. '
     'Ein unklar abgebrochener Kontoabschluss muss vor einer neuen Registrierung geprüft werden. '
     'Ein neues Profil hebt keine Kontosperre auf. Android verwendet keine Browserprofile.'),
    ('Verbindung',
     'Externe Tests ausschließlich über den eingerichteten Proxy oder Tor. Kein Direkt-Fallback und kein Vergleich mit deiner normalen IP.',
     'Proxy-Pflicht in der Testumgebung aktiv. Der Einstieg verwendet dieselbe Browsersitzung und startet keinen eigenen Netzwerkclient.'),
    ('Bestätigungen',
     'Du erlaubst dem Assistenten persönliche Einwilligungen und Windows-Administratorabfragen, soweit das jeweilige Werkzeug sie zulässt. '
     'CAPTCHA und Telefonprüfung bleiben bei dir.',
     'Auf deinen Wunsch akzeptiert die App eindeutig erkannte Cookie- und Tracking-Dialoge bei Browserregistrierungen automatisch, '
     'wenn der Schalter oben aktiv ist. Die Auswahl ist lokal gespeichert und gilt für neue Aufträge. '
     'Ein Zeitlimit beim Cookie-Klick löst keinen zweiten automatischen Klick aus. Die App prüft den weiteren Seitenzustand; '
     'bei weiterhin sichtbarem Dialog bleibt die manuelle Bestätigung offen. '
     'WEB.DE-Android übernimmt bekannte notwendige Bedingungen des kostenlosen Postfachs im autorisierten Registrierungsauftrag. '
     'Kostenpflichtige Angebote und ähnliche Extras werden automatisch abgelehnt; unbekannte Dialoge und technische Grenzen werden als offene Schritte genannt. '
     'Browser-Vertragsformulare sind noch nicht entsprechend erweitert.'),
    ('Daten und Nachweise',
     'Gespeicherte Angaben verwenden; fiktive Testdaten klar kennzeichnen. Du erlaubst Kennwörter und persönliche Daten in Prüfberichten. '
     'Standardmäßig bleiben die Berichte auf notwendige Diagnoseangaben beschränkt.',
     'Ein Postfach gilt erst nach bestätigtem Kontoabschluss und echtem Mailabruf als erfolgreich. '
     'Die gemeinsame Postfachroutine prüft Kontoabschluss, bekanntes Kennwort und tatsächlichen IMAP-Zugriff. '
     'Eine offene Mailprüfung gilt noch nicht als fertig und löst keine zweite Registrierung aus. '
     'Eine Fehlerseite allein beweist weder eine Proxy-Sperre noch einen Erkennungsgrund.'),
    ('Fehler gemeinsam klären',
     'Bei einer fehlgeschlagenen Registrierung oder gemeldeten Kontosperre den letzten erreichten Schritt, die Fehlermeldung '
     'und das offene Hindernis nennen. Gemeinsam das weitere Vorgehen klären.',
     'Begleitete Browseraufträge aus Postfächer halten das eigene Fenster bei Fehlern bis zu fünf Minuten zur Diagnose offen. '
     'Währenddessen keine automatischen Eingaben oder neue Registrierung; Abbruch und Zeitlimit bleiben aktiv. '
     'Unklar manuell weitergeführte Versuche benötigen vor Wiederholung eine Prüfung. '
     'Postfachfehler sind unter Postfächer → Letzten Fehler anzeigen abrufbar. '
     'Ein allgemeiner technischer Fehler wird nicht als bestätigte Kontosperre ausgegeben.'),
    ('Änderungen prüfen',
     'Fehler nachvollziehen, korrigieren, angemessen testen und den gesamten Änderungsdiff prüfen. '
     'Erst dann mit Sicherung in die Desktop-Version übernehmen.',
     'Sichtbare Tests erst nach den Hintergrundprüfungen. Nur eigene Testfenster bedienen. '
     'Offene oder ungeprüfte Funktionen ausdrücklich benennen.'),
    ('Erweiterungsauftrag: Status und Vorrang',
     'Das Auftragsdokument vom 18.10.2026 ist mit deinen bisherigen Vorgaben abgeglichen. '
     'Die folgenden Punkte sind ein Anforderungsregister, keine aktivierten Funktionen. '
     'Bestehende Vorgaben bleiben bei Widersprüchen erhalten.',
     'Status: vorgemerkt = noch umzusetzen oder nachzuweisen; Klärung = widersprüchlich; '
     'nicht übernommen = kein Umsetzungsauftrag. Vollständiger Abschnittsabgleich: Auftragsabgleich-2026-10-18.txt im Prüfbericht-Ordner.'),
    ('Priorität und echte Abnahme · Dokument 7, 9, 12',
     'Neue Priorität: tatsächliche Browsermerkmale und ihre Konsistenz diagnostizieren, zusammen mit WEB.DE und TikTok prüfen. '
     'WEB.DE benötigt bestätigten Kontoabschluss und echten Mailabruf, TikTok die Registrierung über AVD. '
     'Anschließend GMX/Instagram, YouTube/Gmail, Outlook/Reddit. '
     'Eine technische Diagnose ersetzt den Funktionsnachweis nicht.',
     'Vorgemerkt: erfolgreiche und fehlgeschlagene Schritte samt Zeitpunkt und Ursache erfassen; '
     'Erfolgsquote mit Versuchszahl, Zeitraum und unbekannten Ergebnissen ausweisen. '
     '7-/30-Tage-Nachprüfung eigener Konten als separaten Langzeittest führen, nicht als bereits belegten Erfolg.'),
    ('Profile und Identitätsdaten · Dokument 1, 3, 7.5–7.9',
     'Konten, Cookies, Postfachzugänge und Profile getrennt zuordnen. Erfolgreiche Konten behalten ihre Zuordnung. '
     'Nach einem fehlgeschlagenen Browser-/Registrierungsversuch bleibt deine Regel eines neuen leeren Profils bestehen; '
     'Zieladresse und Kennwort des offenen Versuchs bleiben erhalten.',
     'Geklärt: Neuanlage und spätere Vorbereitung sind verschiedene Schritte. '
     'Gewünscht, nicht übernommen: fremde Cookies und künstliche Historie sammeln, um automatisierte Registrierungen als normale Nutzer erscheinen zu lassen. '
     'Vorgemerkt: transparente Vorbereitung mit Prüfungen von Speicherung, Trennung und Verbindung. '
     'Umgesetzt: Profile & Geräte zeigt Browserordner, gespeicherte Kontozuordnungen und vorhandene AVD-Ordner. '
     'Abgeleitete Android-Zuordnungen sind gekennzeichnet; Postfachentwürfe sind noch keine bestätigten Konten.'),
    ('Browser- und Gerätebestand · Dokument 0, 2, 3, 7.3–7.5',
     'AVD bleibt der Schwerpunkt für TikTok-Registrierung; Nox benötigt einen belegten Sondernutzen. '
     'Chrome und der von dir gewünschte Firefox bleiben erhalten. Browserbetrieb nach der Android-Registrierung bleibt vorgesehen. '
     'Ein TikTok-Registrierungsabschluss verlangt eine eindeutige eigene Profilansicht im TikTok-Paket. '
     'Der dabei gelesene tatsächliche Benutzername wird für spätere App-Anmeldungen im Tresor gespeichert.',
     'Geklärt: Chrome und Firefox bleiben erhalten; kein pauschaler Android-Auftritt aller Browser. '
     'Vorhanden sind net/identity.py, net/fingerprint.py und net/humanize.py; „Geräteachse hat null Code“ trifft nicht zu. '
     'Entscheidung: vorerst beim vorhandenen Google-Play-Image bleiben. Ein Wechsel zu Google APIs mit root benötigt einen belegten Funktionsbedarf. '
     'Die Eignung für den vollständigen TikTok-Ablauf ist noch zu prüfen.'),
    ('Diagnose ohne Erkennungsversprechen · Dokument 2, 3, 7.1–7.2, 7.10',
     'Umgesetzt für neue lokale Chrome-/Firefox-Sitzungen: Sprache, Zeitzone, Fenstergröße und Browserkennung lesen '
     'und Soll-/Ist-Abweichungen unter Profile & Geräte anzeigen. Der Messzeitpunkt und der zugehörige Profilordner sind entscheidend. '
     'Umgesetzt für reguläre, geschützte AVD-Starts: Android-Version, Modell, Sprache, Zeitzone, Bootzustand und Paketstatus lesen. '
     'Unter Profile & Geräte → Diagnose anzeigen stehen Browser- und Android-Berichte mit Messzeitpunkt. '
     'Externe Diagnoseziele nur über Proxy/Tor; keine automatische Standortfreigabe.',
     'Kein Nachweis: gleiche Merkmale, ein Ähnlichkeitsschwellwert oder acht unterschiedliche Signale erklären keine Sperre. '
     'Canvas-/WebGL-Verfälschung und Entfernen von Erkennungsspuren zum verdeckten Massenbetrieb sind nicht als Auftrag übernommen. '
     'Ein Emulator wird nicht als ununterscheidbares echtes Telefon bezeichnet. '
     'Die Diagnose ruft keine zusätzliche Website auf und misst weder externe IP noch Anmeldeerfolg. '
     'Ein gesetzter Emulator-Proxy beweist noch keine funktionierende externe Verbindung; Android-Systemproxy ist davon getrennt. '
     'Vorhandene Paketnamen bestätigen noch nicht die Herkunft der installierten App. '
     'Ältere Messungen sind keine Prüfung eines neuen Profilordners; Darstellungsänderungen im vergrößerbaren Fenster werden erklärt.'),
    ('Verbindung und Ressourcen · Dokument 5, 6, 7.4, 9, 12',
     'Vorgemerkt: Kontoproxy-Zuordnung für alle Wege prüfen, DNS-/WebRTC-/IPv6-Lecks testen, '
     'Verbindungsfehler klar anzeigen, konkurrierende Aufträge begrenzen und Verbrauch/Kosten sichtbar machen. '
     'Getrennte Datenbankverbindungen und zuverlässige Auftrags- und Ressourcensperren bei Parallelisierung prüfen.',
     'Kein Direkt-Fallback, kein stiller Wechsel auf einen billigeren Weg, keine kostenpflichtige Buchung. '
     'Reservekonten starten ohne freigegebene Verbindung keinen Netzwerkauftrag. '
     'Zwei Anbieter, ASN-Quoten und mobile/Wohn-IP-Pflichten sind neue Beschaffungswünsche, nicht beschlossen oder als Erfolgsgarantie belegt.'),
    ('Registrierungsfenster und Formulare · Dokument 7.7–7.8, 11',
     'Vorgemerkt: vollständig sichtbares, vergrößerbares Bestätigungsfenster mit Benachrichtigung; '
     'nach CAPTCHA und manueller Navigation frisch prüfen und fortsetzen. '
     'Felder anhand ihres Zustands erkennen; Abbruch, Zeitlimit und doppelte Übermittlung zuverlässig behandeln.',
     'Vorhandene Eingabetempo- und Bereitschaftsprüfungen weiter testen. Testdaten klar kennzeichnen; '
     'keine zufälligen Tippfehler in Zugangsdaten. CAPTCHA und Telefonprüfung bleiben bei dir. '
     'Telefonnummernrotation zur Umgehung von Kontoprüfungen wird nicht übernommen.'),
    ('Planung und Grenzen · Dokument 4, 5, 8, 10, 13',
     'Vorgemerkt für autorisierte eigene Abläufe: reproduzierbare Testpläne, Aktivfenster, Pausen, '
     'Abbruchmöglichkeiten, begrenzte Parallelität und Schutz vor doppelten Aktionen. '
     'Planung, Ausführung und Proxywahl getrennt halten; feste Sicherheitsgrenzen bleiben zentral.',
     'Die Dokumentzahlen sind keine geprüften sicheren Grenzwerte. '
     'Nicht übernommen: simuliertes Publikum, verschleierte Kontencluster, künstliche Likes/Follows/Kommentare/Bewertungen '
     'und deren Lieferkurven oder Mengenpläne. Daraus werden keine automatisch ausführbaren Kampagnen.'),
    ('Nachweise, Langzeittests und offene Annahmen · Dokument 7.10, 9–10, 14',
     'Vorgemerkt: Fehlerarten getrennt zählen, Testdaten von echten Ergebnissen trennen, '
     'beobachtete Kontoeinschränkungen mit Zeitpunkt und Beleg dokumentieren und Statusprüfungen in der Oberfläche zeigen. '
     'Unbekannte Ergebnisse bleiben unbekannt; keine pauschale Bugfreiheit behaupten.',
     'Unbelegt bleiben Registrierungsdauer, Tagesdurchsatz, Überlebensraten, Marktanteile und angeblich sichere Aktionszahlen. '
     'Beobachtbare Erreichbarkeit ist kein Beweis gegen einen „Schattenbann“. '
     'Die genannten alten Bugs werden anhand des aktuellen Codes geprüft, nicht ungeprüft als noch offen übernommen.'),
    ('Technik und Dokumentwidersprüche · Dokument 0, 4, 7.4, 10, 13',
     'Deutsche Oberfläche in Du-Form beibehalten. Abhängigkeiten und Windows-Kompatibilität vor Änderungen prüfen. '
     'Die Einschränkung „ausschließlich Python“ ist auf deinen Wunsch aufgehoben. '
     'Browser, Emulatoren und weitere geeignete Komponenten dürfen verwendet werden; Auswahl nach Nutzen und Kompatibilität. '
     'Architektur- und Android-Imagewechsel weiterhin begründen und testen.',
     'Geklärt am 18.10.2026: kein Umbau auf reine Python-Komponenten erforderlich. '
     'Im Dokument stehen Kampagnenmodul und Ausschluss von Kampagnenlogik nebeneinander; '
     'Pausentage für alle und Ausnahme für Like-Konten widersprechen sich. '
     '„Alles zufällig“ passt weder zu stabilen Identitäten noch zu festen Sicherheitsregeln.'),
)
