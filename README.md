# Socialscraper – startbereite Windows-Fassung

**Arbeitskopie vom 11.10.2026:** klare Abrufresultate, erhaltene Zwischenstände, sichere Wiederholung, ein einfaches Protokoll und eine geführte Kontenseite mit klarem nächsten Schritt. Gestartet wird wie unten beschrieben über **Socialscraper.exe** oder **Socialscraper.cmd**. Änderungen und Prüfumfang: [docs/ABRUF-AENDERUNGEN.md](docs/ABRUF-AENDERUNGEN.md).

Socialscraper holt öffentliche Videos und Kommentare von TikTok, Instagram und YouTube sowie Beiträge und Kommentare von Reddit und Antworten auf Beiträge von X, speichert sie in einer lokalen Datenbank und kann sie regelmäßig automatisch nachladen. Reddit und X haben eigene Grenzen und Risiken – lies dazu den Abschnitt „Reddit und X“. Die Daten lassen sich für PULSE bereitstellen (lokale Schnittstelle oder Cloud-Datenbank).

Der ursprüngliche Venice-Code ist korrigiert und für Windows eingerichtet. Die ausführlichen Codeänderungen stehen in BUGFIXES.md, die überprüften Ergebnisse in TESTRESULTS.json und READINESS.json. Ein öffentlicher YouTube-Abruf lieferte ein Video und fünf Kommentare. TikTok-Videodaten wurden ebenfalls ohne Konto abgerufen; die Kommentaransicht zeigte eine CAPTCHA-Abfrage. Deine Konten-Anmeldungen sind bewusst noch offen.

## Das Programm (Desktop-Fenster)

Socialscraper hat ein eigenes Programmfenster mit eigenem Icon – kein Browser, keine Konsole.

- **Start:** Doppelklick auf **Socialscraper.exe** (liegt im Programmordner; **Einrichten.cmd** baut sie) oder auf die Verknüpfung **Socialscraper** auf dem Desktop, die darauf zeigt (nachträglich anlegen: **Desktop-Verknuepfung.cmd**; entfernen: `.\Desktop-Verknuepfung.ps1 -Entfernen`; eine vorhandene Verknüpfung wird ersetzt, nicht verdoppelt). Rückfall, falls die .exe fehlt: **Socialscraper.cmd** doppelklicken.
- Die .exe ist ein kleines Startprogramm (nicht das ganze Programm, nicht signiert, lokal gebaut mit `build\Build-Exe.ps1`). Sie öffnet das Fenster ohne Konsole, holt bei einem zweiten Start das offene Fenster nach vorn, zeigt bei fehlender Umgebung eine Meldung mit „Einrichten starten“ und darf auch an anderer Stelle liegen (sie merkt sich den Programmordner). `Socialscraper.exe --dienst` startet den Hintergrunddienst unsichtbar (zum Beispiel für den Autostart). Details und Befund zur Einzel-.exe: `docs\EXE.md`.
- Jeder Lauf startet das Werkzeug als eigenen Prozess; ein Absturz oder Browserfehler reißt das Fenster nicht mit. Von Hand gestartet läuft immer nur ein Auftrag, und das Programm lässt sich nur einmal gleichzeitig öffnen. Ein Protokoll steht unten im Fenster und in `data\gui.log`.

### Übersicht, Hilfe und Probleme (Auftrag von Marc: „Verbessere die Bedienung“)

Das Fenster öffnet auf der **Übersicht** (Strg+1). Sie zeigt auf einen Blick, was los ist und was du tun musst:

- **Erste Schritte** (oben, solange nicht alles erledigt ist; mit „Ausblenden“ wegklickbar, in der Hilfe wieder einschaltbar): 1 Systemprüfung, 2 Konto anmelden (oder „Brauche ich nicht“, wenn du nur YouTube/Reddit nutzt), 3 Verbindung wählen („So lassen“ oder „Ändern“), 4 ersten Link abrufen, 5 Ergebnis ansehen. Die Haken kommen aus dem echten Zustand (gespeicherte Anmeldung, vorhandene Videos …), nicht aus einem Merkzettel; nur „Brauche ich nicht“, „So lassen“, „Daten ansehen“ und „Ausblenden“ werden gemerkt.
- **Vier große Knöpfe:** Video-Link abrufen, Kanal hinzufügen, Stimmung bestimmen, Daten ansehen.
- **Das braucht deine Aufmerksamkeit:** je Eintrag ein Satz mit Ursache und ein **Lösungsknopf**, zum Beispiel „Proxy-Modus ‚Tor‘ sperrt Konten → Auf ‚Aus‘ stellen“ (fragt nach und schreibt dieselbe Einstellung wie das Formular unter Einstellungen → Proxys), „TikTok-Konto nicht angemeldet → Jetzt anmelden“, „Kanal … pausiert bis 14:30 wegen CAPTCHA → Details“, „Hintergrunddienst läuft nicht, aber 3 Ziele sind fällig → Dienst starten“, „KI-Schlüssel fehlt → Zu den Einstellungen“, „Daten seit 14 Tagen nicht gesichert → Sicherungskopie anlegen“ (legt eine Kopie in `data\backups` an). Ist nichts zu tun, steht dort „Alles in Ordnung“.
- **Karten:** Hintergrunddienst (an/aus, Starten/Stoppen), Verbindung (Proxy-Modus), Konten je Plattform (bereit / in Pause / nicht angemeldet), letzter Lauf mit Ergebnis, Daten (Videos, Kommentare, Personen), neue Kommentare/Autoren/Videos der letzten 24 Stunden und **Kanäle in Arbeit** mit Fortschrittsbalken und nächstem Schritt.

Die Übersicht liest nur (Datenbank schreibgeschützt) und aktualisiert sich alle paar Sekunden, solange sie sichtbar ist; alles Langsame läuft im Hintergrund. Die Berechnung steht in `socialscraper\homelogic.py` (ohne Fenster, getestet).

**Blockaden mit Lösungsknopf.** Wo ein gelbes Banner „Proxy-Modus ‚Tor‘ aktiv: Konten sind gesperrt“ steht (Abrufen, Konten, Automatik), steht daneben „Auf ‚Aus‘ stellen“. Scheitert ein Abruf an dieser Sperre, am belegten Browser (Hintergrunddienst) oder an einer fehlenden Anmeldung, erscheinen unter dem Ergebnis Knöpfe wie „Auf ‚Aus‘ stellen“, „Dienst stoppen“ oder „Zu den Konten“. Von der Übersicht springen die Knöpfe zum richtigen Tab und markieren das Konto, den Kanal oder das Feld.

**Hilfe (Tab „Hilfe“, F1).** Erste Schritte, häufige Probleme mit Lösung (Anmeldung klappt nicht, Abruf liefert nichts, CAPTCHA, Proxy …), Begriffe in einfachem Deutsch und Tastenkürzel. Kleine **?**-Punkte neben Fachwörtern (Rotation, Abkühlung, Tempo-Profil, Proxy-Modus, Verlauf verfolgen, Ersteller/Kommentarschreiber, KI-Einschätzung, Gruppen, Hintergrunddienst …) zeigen beim Darüberfahren und bei Klick/Enter eine Erklärung. Alle Texte stehen in `socialscraper\helptexts.py`. Warum ein Tab und kein Fenster: er sieht aus wie der Rest, scrollt, ist mit F1 und Strg+7 erreichbar, und Marc verliert beim Zurückspringen nichts.

**Probleme (n).** In der Statusleiste unten rechts zählt der Knopf, wie viele Fehler seit dem letzten Öffnen dazugekommen sind. Er öffnet ein kleines Fenster mit den Fehlern der letzten sieben Tage in einfacher Sprache: Zeit, was passiert ist, warum, was zu tun ist und „Dorthin“. Quellen: fehlgeschlagene und teilweise geklappte Läufe (`runs.error`), Kanäle (Pausen, fehlgeschlagene Videos), Konten in Pause und Fehlerzeilen des Hintergrunddienstes (`data\daemon.log`). Passwörter und Schlüssel werden herausgefiltert. Pausen von Konten und Kanälen sind Zustände und zählen nicht in der Zahl.

**Schneller arbeiten.** *Zwischenablage-Hinweis* im Tab „Abrufen“: kommt das Fenster wieder nach vorn und steht in der Zwischenablage *nur* ein erkannter Link (oder mehrere, oder ein Kanal-Link), erscheint „In der Zwischenablage ist ein YouTube-Link. Abrufen?“ mit „Einfügen und abrufen“, „Nur einfügen“ und „Nein danke“. Gelesen wird nur beim Fensterfokus; anderer Text wird ignoriert; nichts davon wird gespeichert (nur ein Prüfwert für „Nein danke“ bleibt bis zum Beenden im Arbeitsspeicher). Strg+V im Linkfeld legt jeden Link in eine eigene Zeile, schon vorhandene kommen nicht doppelt hinein. Enter startet den Abruf, wenn dort nur gültige Links stehen (Umschalt+Enter = neue Zeile; Strg+Enter geht weiter). Das Programm merkt sich in `data\ui_state.json` (klein, atomar geschrieben, bei Fehlern ignoriert, nie Geheimnisse): Fenstergröße und -ort, den zuletzt geöffneten Tab, Konto und Gruppe im Tab „Abrufen“, ob das Protokoll offen ist, den Zustand der Erste-Schritte-Liste und den Zeitpunkt der letzten Sicherung. Die Zahl der Kommentare gilt weiter nur für den einzelnen Abruf und wird nicht gemerkt; in `settings.toml` wird dafür nichts geschrieben. Das **Protokoll** unten lässt sich mit „Protokoll ▾/▴“ ein- und ausklappen (solange Marc nichts gewählt hat, richtet es sich nach der Fensterhöhe: in kleinen Fenstern eingeklappt, damit die Tabs mehr Platz haben). Ist ein langer Auftrag fertig oder gescheitert und das Fenster nicht vorn, blinkt die Taskleiste, und die Statuszeile sagt es. Tastenkürzel: F1 Hilfe, Strg+1 bis Strg+7 Tabs, Enter und Strg+Enter im Linkfeld, Strg+S speichern, Doppelklick auf ein Konto = nächster Schritt.

### Die Tabs

Reihenfolge: **Übersicht, Abrufen, Konten, Gespeicherte Daten, Automatik, Einstellungen, Hilfe**.

- **Übersicht, Hilfe:** siehe oben.

- **Abrufen:** Links einfügen (mehrere, gemischt aus TikTok, Instagram, YouTube, Reddit, X), Konto und Gruppe wählen, starten. Dasselbe Video in mehreren Schreibweisen (`youtube.com/watch?v=…`, `youtu.be/…`, Shorts, mit Zusätzen wie `&t=30s`) wird nur einmal abgerufen und einmal gezählt. TikTok-Profile lassen sich über den Benutzernamen abrufen. Die Zahl der Kommentare pro Video gilt nur für diesen Abruf; den Standard stellst du unter **Einstellungen** ein. Der Block „Einstellungen für diesen Abruf“ zeigt je Reiter nur, was dort gilt: bei „Video-Links“ alles, bei „TikTok-Profil“ nur das TikTok-Konto, bei „Kanal“ gar nichts (Kommentare, Konto, Gruppe, Tempo und Ruhezeit stellst du im Kanal-Dialog ein, sie gehören zum Kanal). Ein Abbruch-Knopf beendet den Lauf samt Browser. Optional werden die abgerufenen Videos gleich einer Gruppe zugeordnet und für den Verlauf der Zahlen vorgemerkt.
- **Konten:** siehe „Mehrere Konten“.
- **Gespeicherte Daten:** links die Gruppen, rechts oben die Ansicht **Videos | Ersteller | Kommentarschreiber** mit Suche (Groß- und Kleinschreibung sowie Umlaute spielen keine Rolle: „MÜLLER“ findet „müller“), darunter Kommentare (auch „nur neue seit …“, mit Stimmung), der Verlauf der Zahlen (und der Stimmung) als Grafik und eine Gruppenübersicht. Der Knopf **Stimmung bestimmen …** steht oben rechts. Video im Browser öffnen, Export.
- **Automatik:** Hintergrunddienst, Ziele, Laufprotokoll, neue Kommentare – siehe „Automatik“.
- **Tabellen im engen Fenster (ab 860 × 620):** Zahlen und Zustände behalten ihre Breite, lange Texte (Kontoname, Proxy, Kanalname) dürfen schmal werden; wer mit der Maus darüberfährt, sieht den vollen Text als Hinweis. Reicht der Platz trotzdem nicht, erscheint unten eine Bildlaufleiste. Die Kanalliste hat getrennte Spalten „Videos“ (z. B. „412 von 1.380 · 3 fehlgeschlagen“), „Zustand“ und „Nächster Schritt“; im niedrigen Fenster bekommen Ziele und Kanäle je einen Teil der Höhe.
- **Einstellungen:** echte Formulare statt einer Textdatei: Abruf, Proxys, Benachrichtigungen, Automatik-Dienst, PULSE-Anbindung, Cloud-Datenbank, System (Prüfung, Orte der Dateien, Sicherungskopie, Version). Ungespeicherte Änderungen sind markiert; die vorige Fassung von `settings.toml` bleibt als Sicherung erhalten.

## Mehrere Konten

Für TikTok, Instagram, Reddit und X kannst du beliebig viele Konten anlegen, frei benennen und eines davon zum Standard machen. Bei **Reddit ist die Anmeldung freiwillig**, bei **X braucht fast alles ein Konto**. Jedes Konto hat seine eigene gespeicherte Anmeldung und sein eigenes Browserprofil (unter `data`). Im Tab **Konten**: anlegen, umbenennen, Standard setzen, anmelden, Status prüfen, abmelden, entfernen. Beim Abrufen und bei jedem automatischen Ziel wählst du, mit welchem Konto es läuft – oder ob die Konten abwechselnd benutzt werden (siehe „Konten rotieren und feste Proxys“). YouTube braucht kein Konto.

- **Anmelden** öffnet einmalig ein sichtbares Browserfenster; dort meldest du dich selbst an (TikTok bietet auch den QR-Code). Der Status zeigt ohne Browserstart, ob eine Anmeldung gespeichert ist; „Status prüfen“ bestätigt sie am Konto.
- **Anmeldung aus Datei übernehmen** liest eine Cookie-Datei (JSON oder Text) aus einem angemeldeten Browser – gedacht für Server ohne Bildschirm.
- Die vom Menü genutzten Zugangsdaten stehen (leer vorbereitet) in `data\credentials.json`, getrennt nach Plattform und Konto. Bei Reddit und X meldest du dich immer von Hand im Browserfenster an (kein Passwort-Eintrag durch das Programm).

## Konten rotieren und feste Proxys

Hast du **mehrere Konten auf derselben Plattform** (TikTok, Instagram, Reddit, X), kann das Programm sie **abwechselnd benutzen**, und jedem Konto lässt sich ein **fester Proxy** zuweisen.

**Rotation.** Beim Abrufen (Tab „Abrufen“, Auswahl „Konto“ je Plattform: **Standardkonto**, ein bestimmtes Konto oder **🔄 Automatisch wechseln (Rotation)**) und bei jedem Ziel der Automatik (Dialog „Konto“: Standardkonto der Plattform / bestimmtes Konto / Rotation) wählst du, wie Konten gewählt werden. Bei der Rotation nimmt das Programm unter den Konten mit gesetztem Häkchen **„In Rotation“** (Tab „Konten“) das, das **am längsten nicht benutzt** wurde (bei Gleichstand das mit weniger Abrufen, dann das ältere), und wechselt
- **nach einer festen Zahl Videos** (Einstellungen → Abruf → „Konto wechseln nach … Videos“, 1 bis 100, Standard 5, Einstellung `rotation_every`),
- **sofort bei einem Kontofehler** (Anmeldung abgelaufen oder Anmeldeseite statt Video, CAPTCHA/Menschentest, Konto gesperrt, „zu viele Anfragen“ (429), Proxy nicht erreichbar). Ein einzelnes **403** bei einem Video (regionale Sperre, privates oder entferntes Video) ist dagegen ein **Problem des Videos**: Es wird als nicht abrufbar übersprungen, das Konto bleibt im Einsatz und kommt nicht in die Abkühlung. Scheitern aber **3 verschiedene Videos hintereinander** mit 403 (ohne Erfolg dazwischen), zählt das als Kontosperre (`rotation.FORBIDDEN_LIMIT`), Das fehlgeschlagene Video wird mit dem nächsten Konto **noch einmal** versucht, jedes Video aber **höchstens einmal je Konto**. Sind alle Konten durch, endet der Lauf mit einer klaren Meldung, die sagt, **wann wieder ein Konto frei wird**.

**Abkühlzeit.** Ein Konto nach einem Fehler macht eine Pause, die bei jeder Wiederholung in Folge länger wird: CAPTCHA, Sperre oder abgelaufene Anmeldung 6 → 12 → 24 → 48 Stunden (dann bleibt es bei 48), „zu viele Anfragen“ 1 → 2 → 4 Stunden, Proxy nicht erreichbar 30 Minuten. Ein gelungener Abruf setzt die Zähler zurück; „Abkühlung aufheben“ (Tab „Konten“) holt ein Konto sofort zurück, eine erfolgreiche neue Anmeldung hebt die Pause wegen abgelaufener Anmeldung auf. Außerdem gibt es ein **Tageslimit** je Konto („Tageslimit …“, Abrufe = Videos pro Tag, leer = unbegrenzt) und eine Zahl „Heute benutzt“. Ein Konto ohne gespeicherte Anmeldung wird nicht benutzt (Reddit geht auch ohne). Ein bestimmtes Konto oder das Standardkonto wird weiter benutzt wie bisher, auch in der Abkühlzeit – der Fehler wird nur vermerkt.

**Anzeige.** Im Tab „Konten“ zeigt eine Kopfzeile je Plattform („3 Konten · 2 bereit · 1 in Abkühlung bis 14:30“), und jedes Konto hat die Spalten „In Rotation“ (Häkchen, wird sofort gespeichert), „Proxy“, „Rotation“ (bereit / Abkühlung bis … wegen … / Tageslimit erreicht / keine Anmeldung) und „Heute“. Das Laufprotokoll nennt je Block „Konto: <Name>“; das Ergebnis eines Abrufs und das Laufprotokoll der Automatik nennen die benutzten Konten (`runs.accounts_used`).

**Fester Proxy je Konto.** Tab „Konten“ → „Proxy festlegen …“: Adresse eintragen (`http://benutzer:passwort@1.2.3.4:8080`; erlaubt sind http, https, socks5, socks4; ein SOCKS-Proxy **mit** Passwort geht im Browser nicht), **Testen** zeigt die Ausgangs-IP (ein HTTPS-Aufruf mit Zertifikatsprüfung durch den Proxy; dabei meldet sich kein Konto an), **Speichern**, **Entfernen**. Danach läuft **alles dieses Kontos** (Anmelden, Status prüfen, Abrufen, Hintergrunddienst) **nur über diesen Proxy** – nie direkt, nie über einen anderen. Ist er nicht erreichbar (schneller Verbindungsversuch vor dem Start), bricht der Abruf mit klarer Meldung ab, das Konto macht 30 Minuten Pause, und die Rotation nimmt das nächste Konto. Der feste Proxy hat Vorrang vor der Proxy-Liste und gilt auch im Modus „Aus“; in den Modi „Kostenlose Proxys“ und „Tor“ bleiben Konten wie bisher gesperrt (und damit auch die Rotation und feste Proxys). Passwörter erscheinen nirgends im Klartext: in der Anzeige als `*****`, in Meldungen und Protokollen werden sie herausgefiltert.

**Wo das Proxy-Passwort liegt.** Nicht in der Datenbank, sondern in `data\account_proxies.json` neben der Datenbank (mit eingeschränkten Rechten angelegt, soweit Windows das zulässt – wie `integration.json`; sie liegt im Ordner deines Programms und ist nicht verschlüsselt). Die Datenbank trägt je Konto nur die **maskierte** Anzeige (`http://benutzer:*****@1.2.3.4:8080`). Deshalb enthalten Datensicherung, Export, Cloud-Abgleich und die Schnittstelle für PULSE den Proxy **nie** (die Schnittstelle zeigt nur `has_proxy`). Die Datei gehört nicht ins Quellpaket und nicht in Sicherungen, die du weitergibst; beim Umzug auf einen Server musst du sie wie `data\cookies` selbst mitnehmen. Fehlt sie, obwohl ein Proxy vermerkt ist, wird **nicht** direkt abgerufen, sondern mit Meldung abgebrochen.

**Befehlszeile.** `scrape` und `profile` kennen `--rotate`, `--rotation-every N`, `--account-id ID` (ein bestimmtes Konto der Datenbank) und `--accounts ID,ID` (Rotation nur unter diesen Konten); `--identity` bleibt für den Einzelfall wie bisher. Am Ende steht „Benutzte Konten: …“.

**Grenzen, ehrlich.** Die Rotation macht Abrufe nicht unsichtbar: Plattformen erkennen auch an der **IP-Adresse** und am **Gerät** (derselbe Rechner, dieselbe Browser-Umgebung), dass mehrere Konten zusammengehören. Teilen sich Konten denselben Proxy oder dieselbe Verbindung, sieht das für die Plattform nach derselben IP aus; das Programm weist im Tab „Konten“ darauf hin (nur ein Hinweis, keine Sperre). Ein eigener Proxy je Konto hilft dagegen nur, wenn er wirklich eine andere Ausgangs-IP hat. Konten, die miteinander zu tun haben (gleiche Telefonnummer, gleiche E-Mail, gleiche Gewohnheiten), bleiben verknüpfbar. Mehr Konten heißen mehr Risiko für **jedes** davon; bei X verstößt das Auslesen mit Konto gegen die Regeln. Nicht gegen echte Plattformen geprüft (siehe „Grenzen“): die Rotation und der feste Proxy wurden mit simulierten Plattformen, lokalen Fake-Proxys und einem echten unsichtbaren Chrome gegen einen lokalen Fake-Proxy geprüft, nicht mit echten Konten oder bezahlten Proxys.

## Gruppen

Videos **und Personen** lassen sich in Gruppen sortieren (Name und Farbe). Ein Video kann in mehreren Gruppen sein; Gruppen löschen löscht nie Videos oder Personen. Die Gruppenübersicht zeigt je Gruppe Videos, Ersteller, Kommentarschreiber, Kommentare, die Summen der Aufrufe und Likes und die Stimmung. Beim Export lässt sich auf eine Gruppe oder eine Auswahl einschränken.

### Personen in Gruppen

Die Ansicht **Ersteller** zeigt die Ersteller der gespeicherten Videos (Videos, Aufrufe, Likes), **Kommentarschreiber** alle Kommentarautoren (Kommentare, unter wie vielen Videos, Likes, erste und letzte Aktivität, bei vorhandener Stimmung der Anteil positiv und negativ). Suche, Plattform- und Gruppenfilter, Mehrfachauswahl (Strg+A) und die Knöpfe **Zu Gruppe hinzufügen …**, **Aus Gruppe entfernen** (auch Entf) und **Neue Gruppe mit Auswahl …** ordnen Personen ein. Auch aus der Videoauswahl („Ersteller zur Gruppe …“) und aus der Kommentarliste („Schreiber zur Gruppe …“) geht das. Eine Person ist ein Name oder eine Kennung auf einer Plattform (Kennung, sonst der Name kleingeschrieben ohne @); gleiche Namen auf zwei Plattformen bleiben zwei Personen. Wählst du links eine Gruppe, zeigt die Videoansicht die Videos der Gruppe **oder** von Erstellern der Gruppe (steht oben dabei), die Kommentarliste kann auf Kommentarschreiber der Gruppe eingeschränkt werden, die Personenansichten zeigen die Mitglieder. Automatik-Ziele sortieren weiterhin nur Videos ein – Personen ordnet nur du selbst ein.

**Datenschutz:** Es werden nur öffentlich sichtbare Namen und Kennungen verwendet, die ohnehin in der Datenbank stehen. Das Gruppieren von Kommentarschreibern ist eine Auswertung von Personen; du entscheidest, wofür du sie nutzt, und beachtest dabei den Datenschutz (PULSE-Regel: personenbezogene Auswertungen nur bewusst).

### Neue Autoren

Für jeden Autor merkt sich die Datenbank, wann er zum ersten Mal gesehen wurde (kleinster „zuerst gesehen“-Zeitpunkt seiner Kommentare bzw. Videos; Plattform + Kennung/Name wie bei den Gruppen). In den Personenansichten gibt es die Spalte **Zuerst gesehen** (neue Autoren sind blau und mit „★ neu“ markiert), den Filter **Zeige: Alle | Nur neue Autoren | Nur bekannte | Ohne Gruppe**, die Einstellung **Neu heißt:** (seit heute, 24 Stunden, 7 oder 30 Tage, seit dem letzten Lauf oder ein eingetipptes Datum) und einen Zähler („12 neue Kommentarschreiber in den letzten 24 Stunden“). In der Kommentarliste und im Feed „Neue Kommentare“ trägt ein Kommentar eines neuen Autors den Vermerk „★ neu“. Das Laufprotokoll zählt je Lauf die neuen Autoren. Neue Autoren lassen sich markieren (Strg+A) und direkt in eine Gruppe einordnen. „Neu“ heißt: noch nie in **dieser** Datenbank gesehen – eine Liste bekannter Personen aus anderer Quelle gibt es nicht.

## Stimmung (KI-Einschätzung)

Der Knopf **Stimmung bestimmen …** (Tab „Gespeicherte Daten“) lässt die Claude-KI (Anthropic API) für gespeicherte Kommentare schätzen, ob sie **positiv, neutral oder negativ** sind (mit Wert von -1 bis 1). Das Ergebnis heißt überall **KI-Einschätzung**: eine Schätzung, **nicht gemessen** und keine bewiesene Ursache. Umfang: gewählte Videos, aktuelle Gruppe oder alle Kommentare. Ablauf: Das Fenster zeigt zuerst die **Schätzung** (offene Kommentare, Anfragen, grob geschätzte Tokens – einen Geldbetrag nur, wenn du unter Einstellungen → KI / Stimmung selbst Preise einträgst; das Programm kennt keine Preise), fragt „Jetzt bestimmen?“ und arbeitet dann im Hintergrund mit Fortschrittsbalken und **Abbrechen**. Bereits fertige Stapel bleiben gespeichert; ein späterer Lauf schickt nur noch, was offen ist („alles einmal einstufen, danach nur neue Kommentare“). Ändert sich der Text eines Kommentars, wird er neu eingestuft. Der Auftrag an die KI kennt alle fünf Plattformen: TikTok, Instagram und YouTube (kurze Kommentare mit Emojis), Reddit (Kommentare unter Beiträgen, oft länger und sarkastisch, auch mit „/s“) und X (kurze Antworten mit Hashtags und Erwähnungen); die Fassung des Auftrags steht als `PROMPT_VERSION` (jetzt 2) bei jeder Einstufung. Ältere Einstufungen bleiben gültig (maßgeblich ist der Textabgleich, nicht die Fassung); wer sie mit der neuen Fassung wiederholen will, stuft die Kommentare neu ein. Gleiche Texte werden nur einmal gesendet; eine Obergrenze pro Lauf (Standard 5000) und eine Pause zwischen den Anfragen schützen vor Überraschungen.

Anzeige: Verteilungsbalken Positiv/Neutral/Negativ (grün/grau/rot, Zahlen und Prozent), Spalte „Stimmung (KI)“ und Filter in der Kommentarliste, Summen in der Gruppenübersicht, Verlauf je Tag („Stimmung (KI)“ im Verlauf-Reiter). Auf der Kommandozeile: `python -m socialscraper sentiment status`, `sentiment estimate [--video UID] [--group ID]`, `sentiment run [--video UID] [--group ID] [--max N] [--yes]` (ohne `--yes` nur die Schätzung).

Einrichten: Unter **Einstellungen → KI / Stimmung** trägst du den API-Schlüssel selbst ein (Anzeigen/Kopieren, **Verbindung testen** sendet nur auf deinen Klick eine winzige echte Anfrage). Er steht in `data\ai.json` (nicht in `settings.toml`, Rechte eingeschränkt, nie im Protokoll); alternativ gilt die Umgebungsvariable `ANTHROPIC_API_KEY`. Modell (Standard `claude-haiku-5-5`), Obergrenze pro Lauf und freiwillige Preise stehen dort auch. Der Schalter **Neue Kommentare nach jedem Lauf automatisch einstufen** (Standard **aus**, weil es Geld kostet; auch im Tab „Automatik“) lässt den Hintergrunddienst nach einem Lauf nur die neuen Kommentare einstufen – ein Fehler dabei lässt den Lauf nie scheitern.

**Datenschutz und Kosten:** Beim Bestimmen werden Kommentartexte an die Anthropic-API gesendet. Gesendet wird nur der Text (Leerraum vereinheitlicht, auf 600 Zeichen gekürzt, Links durch „[Link]“ und @Namen durch „@nutzer“ ersetzt) – keine Autorennamen, keine Nutzerkennungen, keine Video-Links. Der Aufruf geht direkt, nicht über die Proxys. Anthropic verarbeitet die Texte nach eigenen Bedingungen, und jede Anfrage kostet Geld (dein Konto).

**Ehrlich:** Mit der echten Anthropic-API wurde das **nicht geprüft** (nur gegen einen selbstgebauten lokalen Testserver, auch die Modellnamen und das Antwortformat der echten API nicht); die **Qualität der Einstufung** ist ungeprüft (Ironie, Slang und Mischsprache sind schwierig). Die Kostenschätzung ist grob (Zeichen geteilt durch 3,2 plus Prompt-Gerüst); der echte Verbrauch steht nach dem Lauf im Ergebnis.

### Export

**Gespeicherte Daten → Export …** schreibt einen neuen Ordner mit mehreren Dateien: Videos, Kommentare, Gruppen, die Zuordnung Video ↔ Gruppe und den Verlauf der Zahlen.

- **CSV für Excel (Semikolon):** öffnet sich im deutschen Excel sofort richtig (UTF-8 mit BOM, Semikolon, Formelschutz). Dateien: `videos.csv`, `kommentare.csv` (mit den Spalten „Stimmung (KI-Einschätzung)“ und „Stimmungswert (KI-Einschätzung)“), `gruppen.csv`, `gruppen_zuordnung.csv`, `verlauf.csv`, `personen.csv` (Ersteller und Kommentarschreiber mit Kennzahlen, „Zuerst gesehen“ und Gruppen) und `gruppen_personen.csv` – mit lesbaren deutschen Spaltennamen (Plattform, Autor, Beschreibung, Link, Aufrufe, Likes, Kommentare, Veröffentlicht, Zuerst gesehen, Zuletzt abgerufen, Gruppen …).
- **CSV (Komma):** dasselbe mit Komma als Trenner.
- **JSONL (für Programme):** alle Rohspalten (`videos`, `comments`, `groups`, `group_videos`, `group_people`, `comment_sentiment`, `video_snapshots`).

Bei „Nur diese Gruppe“ gilt der Filter für alle Dateien: auch Videos von Erstellern der Gruppe, die Mitglieder und die Kommentare der Kommentarschreiber der Gruppe.

## Automatik und Hintergrunddienst

Im Tab **Automatik** legst du Ziele an, die der Hintergrunddienst in Abständen selbst abruft (Voreinstellungen von 15 Minuten bis 1 Woche, eine eigene Zeit geht ab 5 Minuten):

- **Hashtag, Suchbegriff, Profil** finden neue Videos oder Beiträge und rufen sie ab (optional landen sie in einer Gruppe). Hashtags gibt es für TikTok, YouTube und X; Suche für TikTok, YouTube, Reddit und X; Profile für TikTok, Reddit und X. Bei **Reddit** holt die Suche `r/name` die neuen Beiträge eines Subreddits (jeder andere Text ist ein Suchbegriff), ein Profil heißt `u/name`. Bei **X** brauchen alle Ziele ein bestätigt angemeldetes Konto. Instagram unterstützt das Programm hier nicht.
- **Video-Link** holt immer wieder neue Kommentare unter einem bekannten Video und einen neuen Punkt für den Verlauf der Zahlen (Aufrufe, Likes, Kommentare).

Das Laufprotokoll zeigt jeden Lauf mit Zielname, Ergebnis, Zahl neuer Autoren und Fehler; der Reiter „Neue Kommentare“ zeigt, was seit einem Zeitpunkt dazugekommen ist. Den Dienst startest und stoppst du im selben Tab; auf Wunsch startet er automatisch mit Windows (nur eine Verknüpfung im Autostart-Ordner deines Benutzers, keine Systemeinstellung). Ohne Fenster: `python -m socialscraper daemon`. Es gibt genau eine Dienst-Instanz; Dienst und Fenster teilen sich eine Sperre (`data\run.lock`), damit nie zwei Browseraufträge gleichzeitig laufen.

## Ganze Kanäle (Auftrag von Marc, 12.10.2026)

Ein ganzer Kanal oder ein ganzes Profil wird eingefügt, das Programm holt danach **alle Videos** in kleinen Paketen und beobachtet neue.

- **Hinzufügen:** Tab **Automatik → Kanäle → Kanal hinzufügen …** oder Tab **Abrufen → Kanal**. Erkannt werden YouTube (`/@name`, `/channel/UC…`, `/c/…`, `/user/…`), TikTok (`/@name`), Instagram (`/name/`), Reddit (`/user/name`, `/u/name`, `/r/name`) und X (`/name`). Der Host wird echt geprüft: ähnlich aussehende Adressen (`youtube.com.evil.com`), Video-/Beitrags-Links, Kurzlinks und Links mit Benutzername/Passwort werden abgelehnt. „Prüfen“ zeigt bei YouTube Name und, falls YouTube sie nennt, die Zahl der Videos (kleine Netzabfrage im Hintergrund); die anderen Plattformen nennen sie erst nach dem ersten Lauf.
- **Optionen:** Umfang (alle Videos / nur die neuesten N / ab Datum), Kommentare pro Video, Gruppe (auch „Neue Gruppe …“), Konto (Standard, bestimmtes Konto oder Rotation), Tempo-Profil, Ruhezeit (Standard aus), Intervall für neue Videos nach dem Backfill (Standard 6 Stunden), „Neue Videos danach automatisch beobachten“ (Standard an). YouTube: Shorts mitnehmen (Standard an). Reddit und X: „Nur Beiträge mit Video“. Subreddit: neueste oder beste zuerst. „Kanal-Inhaber auch als Ersteller in die Gruppe“ steht **aus**; Personen kommen nur durch deinen Haken in eine Gruppe.
- **Ablauf in Stufen:** (1) Listen sammeln – billig, ohne Download (YouTube: yt-dlp flach; TikTok/X: Profil im Browser mit Konto scrollen; Instagram: instaloader; Reddit: JSON-Liste) → (2) Warteschlange füllen (Tabelle `channel_items`, neueste zuerst) → (3) Videos abrufen – teuer, in kleinen Paketen. Schon gespeicherte Videos (jünger als 7 Tage abgerufen) werden übersprungen und kommen trotzdem in die Gruppe.
- **Tempo-Profile** (die einzigen Stellschrauben; Zahlen in `socialscraper/channels.py`):

  | Profil | Videos pro Lauf | pro Tag und Konto | pro Tag und Plattform | Pausen zwischen Videos | Abstand der Läufe |
  |---|---|---|---|---|---|
  | Vorsichtig | 8 | 60 | 150 | 8–25 s, oft Lesepausen (20–60 s) | 45–90 Minuten |
  | Normal | 20 | 200 | 500 | 2–9 s, selten Lesepausen | 20–45 Minuten |
  | Schnell | 50 | 600 | 1.500 | 1–4 s | 8–15 Minuten |

  Ohne Konto (YouTube, Reddit ohne Anmeldung) zählt eine Verbindung: das Tageslimit „je Konto“ gilt dann für die ganze Plattform. Bei Rotation zählt jedes Konto für sich (zusätzlich die Konten-Tageslimits unter „Konten“; das strengere gilt). Die Läufe selbst haben Zufall (Abstand ±, Intervall ±15 %).
- **Ruhezeiten:** z. B. `23-7` – in dieser Zeit (Ortszeit) wird nichts abgerufen; der nächste Lauf liegt kurz nach dem Ende. „Jetzt weiter“ überspringt Pause und Ruhezeit einmal, **nie** die Tagesgrenze.
- **Bei CAPTCHA, Sperre, „zu viele Anfragen“ oder abgelaufener Anmeldung** hört der Lauf sofort auf: das Konto kühlt ab (wie in der Rotation), bei Rotation macht das nächste freie Konto weiter. Ist keins frei, steht der Kanal auf „pausiert bis HH:MM wegen …“ und wird bis dahin nicht angefasst. Fehler eines einzelnen Videos zählen mit (nach 3 Versuchen „fehlgeschlagen“, später „Fehlgeschlagene erneut“); Fehler des Kontos zählen nicht gegen das Video. Ein 403 oder „nicht verfügbar in deinem Land“ bei einem einzelnen Video ist ein Fehler des Videos (siehe „Konten rotieren“).
- **Befehlszeile für Kanäle:** `python -m socialscraper channel add <link> [--scope all|latest|since] [--latest N] [--since JJJJ-MM-TT] [--max-comments N] [--group NAME] [--account-mode default|fixed|rotate] [--account-id ID] [--pace vorsichtig|normal|schnell] [--quiet 23-7] [--watch|--no-watch] [--interval MIN]`, `channel list` (mit Fortschritt), `channel status ID`, `channel pause|resume|retry-failed|remove ID` und `channel estimate <link> --count N [--pace …] [--accounts N] [--comments N] [--quiet …]` (Zeitschätzung ohne Netz). Dieselben Prüfungen und dieselbe Logik wie der Dialog (`channels.py`, `chanlogic.py`, `repo.py`); Ausgaben deutsch; Exit-Code 0 = erledigt, 1 = Eingabe- oder Datenbankfehler, 2 = Kanal mit dieser Nummer gibt es nicht.
- **Absturz oder Neustart:** Videos, die gerade abgerufen wurden, kommen beim nächsten Lauf (und beim Start des Dienstes) zurück in die Warteschlange; die Liste macht dort weiter, wo sie war.
- **Zeitschätzung** (im Dialog): `Tage = Videos ÷ pro Tag`, mit `pro Tag = min(Tageslimit je Konto × Konten, Tageslimit der Plattform, Läufe pro Tag × Paketgröße)`. Beispiel: 1.380 Videos bei Tempo „Vorsichtig“ und einer Verbindung = 1.380 ÷ 60 = rund 23 Tage. Das sind die Grenzen, die sich das Programm selbst setzt, keine Messung. Kommentare machen jedes Video langsamer; die KI-Stimmung kostet extra (Kostenschätzung unter Einstellungen → KI / Stimmung).
- **Anzeige:** Seite „Kanäle“ im Tab Automatik – je Kanal ein Balken und „412 von 1.380 · 3 fehlgeschlagen · nächster Schritt 14:30 · pausiert bis … wegen CAPTCHA“; Knöpfe: Einzelheiten (Warteschlange mit Status und Fehlern), Pausieren/Fortsetzen, Jetzt weiter, Fehlgeschlagene erneut, Tempo ändern, Entfernen (gespeicherte Videos bleiben). Ist alles abgerufen, wechselt der Kanal auf „beobachtet neue Videos“. Export: `kanaele.csv` / `channels.jsonl`; Schnittstelle: `GET /v1/channels`.
- **Grenzen der Plattformen** (werden ehrlich gemeldet, nie als „alle“): Reddit zeigt je Liste höchstens etwa 1.000 Beiträge (ein Subreddit holt standardmäßig die neuesten 500); X zeigt eine Zeitleiste nur etwa 3.200 Beiträge zurück; TikTok und Instagram zeigen nur, was beim Scrollen bzw. Blättern kommt – bei einer Lücke steht „230 von 1.380 bekannten Videos gefunden; die Plattform zeigt nicht mehr“. YouTube: das Datum in der Liste ist ungefähr (aus „vor 2 Jahren“), das echte Datum steht nach dem Abruf; Livestreams, die gerade laufen oder erst kommen, werden beim Beobachten später mitgenommen.
- **Anti-Detect ist kein Versprechen.** Kleine Pakete, Pausen, Zufall, Tagesgrenzen, Ruhezeiten und der sofortige Stopp machen das Muster unauffälliger, verhindern aber keine Sperre. Bei Konten (vor allem X) bleibt das Risiko bei dir.
- **Nicht gegen echte Plattformen geprüft:** TikTok, Instagram, Reddit und X (nur mit Attrappen). YouTube wurde live mit einem kleinen und einem großen öffentlichen Kanal geprüft (nur Listen und wenige Videos).

## Videolänge

Die Länge jedes Videos steht in Sekunden in der Datenbank (`duration_s`, bei jedem Abruf; ein später fehlender Wert überschreibt eine bekannte Länge nie; Millisekunden-Werte werden erkannt). **Gespeicherte Daten:** Spalte „Länge“ (m:ss oder h:mm:ss, sortierbar, leere Werte immer am Ende), Filter „Länge: alle | kurz | mittel | lang“ (Grenzen unter Einstellungen → Abruf, Standard 60 s und 10 min), die Länge im Detail unter der Liste, in der Gruppenübersicht Ø/kürzestes/längstes und der Reiter „Länge im Vergleich“ (je Klasse Zahl der Videos, Ø Aufrufe, Ø Likes, Ø Kommentare – nur aus gemessenen Werten; Videos ohne Länge stehen in einer eigenen Zeile). Export: `Länge (m:ss)` und `Länge (Sekunden)`; Schnittstelle: `duration_s`, `duration_class`, Ø/min/max in `/v1/groups` und `/v1/stats`. Bei Reddit-Videobeiträgen kommt die Länge aus dem Reddit-Video (`secure_media`/`media`), bei X aus dem Video der Antwort; Beiträge ohne Video haben keine Länge.

## Unsichtbare Abrufe

Abrufe und der Hintergrunddienst öffnen **kein** Browserfenster (Standard „Browser im Hintergrund“ unter Einstellungen → Abruf). Sichtbar wird nur die einmalige Anmeldung unter „Konten“. Das Abschalten der Unsichtbarkeit ist nur zur Fehlersuche gedacht.

## PULSE-Anbindung

Socialscraper stellt die Daten für PULSE bereit, ohne PULSE anzufassen: eine **lokale, nur lesende Schnittstelle** (`http://127.0.0.1:<Port>/v1/…`, Anmeldung per Token, einschaltbar unter Einstellungen → PULSE-Anbindung) und einen Beispiel-Client. Format, Einrichtung, Endpunkte und Beispiele: **`docs\PULSE-Anbindung.md`**. PULSE selbst hat aktuell nur die Oberfläche; das Einbauen dort ist ein späterer Auftrag.

## Cloud-Datenbank

Alternativ oder zusätzlich kann Socialscraper Kopien der Daten in eine Cloud-Datenbank (Supabase) hochladen und von dort zusammenführen (Einstellungen → Cloud-Datenbank: Adresse, Schlüssel, „Verbindung testen“, „Jetzt hochladen“, „Aus Cloud aktualisieren“; Tabellen aus `deploy\supabase_schema.sql`). Die Adresse wird beim Speichern bereinigt (nur `https`, ohne Schrägstrich und ohne `/rest/v1` am Ende). **Nicht geprüft:** Gegen eine echte Cloud-Datenbank wurde das nicht getestet, nur gegen einen selbstgebauten Testserver.

## Server-Betrieb

Der Hintergrunddienst kann auf einem Linux-Server ohne Fenster laufen (Dockerfile, `deploy\docker-compose.yml`, Anleitung `docs\SERVER.md`). Dieser Teil ist **nicht ausgeführt und ungeprüft**; Anmeldungen gehen nur am PC (Cookies hochladen), TikTok und Instagram sperren Server-Adressen häufiger.

## Proxys und Tor

Unter Einstellungen → Proxys wählst du, über welche Verbindung die Abrufe laufen (Einstellung `proxy_mode` in `settings.toml`):

- **Aus (meine eigene Verbindung):** Standard. Kein Proxy-Modus – ist unter „Eigene Proxy-Liste“ aber eine Liste eingetragen, wird sie weiterhin benutzt (das Formular zeigt dann einen Hinweis); für Abrufe ganz ohne Proxy die Liste leeren.
- **Eigene Proxy-Liste:** Proxys, die du selbst einträgst (z. B. gekauft). Darf mit Konten benutzt werden. Passwörter werden in der Liste als `*****` angezeigt und nie ins Protokoll geschrieben.
- **Kostenlose Proxys:** das Programm lädt öffentliche Listen und testet die Proxys. Mit „Liste jetzt aktualisieren“ geht das von Hand (dauert bis zu einer Minute). Sie sind langsam und unzuverlässig, und sie sehen, welche Seiten du aufrufst.
- **Tor:** das mitgelieferte Tor (Ordner `tools\tor`) wird beim ersten Abruf automatisch gestartet; „Tor starten“, „Tor beenden“ und „Verbindung testen“ gibt es auch von Hand. „Tor beenden“ beendet nur ein Tor, das dieses Programm selbst gestartet hat; ein extern laufendes Tor (z. B. der Tor Browser) bleibt unberührt.

**Grenzen:** Kostenlose Proxys und Tor gelten nur für öffentliche Abrufe ohne Konto (YouTube, Reddit, öffentliche TikTok-Videodaten ohne Kommentare). Konten, Anmeldung, Instagram, X, TikTok-Kommentare, -Hashtags und -Suche sind in diesen Modi aus Sicherheitsgründen gesperrt (Reddit sperrt kostenlose Proxys und Tor aber oft selbst); Konten- und Abrufen-Tab zeigen dazu einen Hinweis, gesperrte Abrufe werden vor dem Start gemeldet, die Automatik kennzeichnet betroffene Ziele als „blockiert durch Proxy-Modus“. Gibt es keinen funktionierenden Proxy, wird nie stillschweigend ohne Proxy abgerufen. Ob die Zielseiten kostenlose Proxys oder Tor-Ausgänge durchlassen, hängt vom Tag ab und ist nicht zugesichert. Die Statuszeile prüft nur lokal; „Prüfen“ testet wirklich (kann bis zu 90 Sekunden dauern).

## Reddit und X

Auftrag von Marc, 11.10.2026: „Wir müssen auch Reddit und X abdecken.“ Beide laufen ausschließlich über den Browser (unsichtbar, wie TikTok); die Daten landen in denselben Tabellen wie alles andere (`reddit:<Beitrags-ID>`, `x:<Beitrags-Nummer>`).

### Reddit – nur Browser, Anmeldung freiwillig

- Kein API-Schlüssel. Das Programm öffnet den Beitrag unsichtbar im Browser und liest die Daten über den JSON-Weg derselben Seite (`/r/<name>/comments/<id>/.json`), die Anfrage kommt also von einem echten Browser. Weitere Kommentare („more“) werden nachgeladen (sortiert nach „top“, mit den Tempo-Pausen des Programms). Zeigt die Seite etwas anderes, wird die sichtbare Seite gelesen (nicht gegen echtes Reddit geprüft).
- **Links:** `reddit.com/r/<name>/comments/<id>/…` (auch `old.`, `np.`, `m.`, mit `?utm_…`), `reddit.com/comments/<id>`, `redd.it/<id>`; der Link zu einem einzelnen Kommentar holt den ganzen Beitrag. Neue Kurzlinks `reddit.com/r/<name>/s/<Code>` löst der Browser auf.
- **Abbildung:** Beitrag: Beschreibung = Titel, dann Text; Likes = Punkte; Kommentare = Zahl der Kommentare; Aufrufe zeigt Reddit nicht (leer); Unterreddit, Verhältnis hoch/runter, Kennzeichnung, Crosspost-Angaben stehen unter `raw`. Kommentare behalten ihre Verschachtelung (`parent_id`); Antworten des Beitragsautors sind markiert. **Gelöschte oder entfernte Kommentare** („[deleted]“, „[removed]“) haben keinen Text und werden übersprungen (die Meldung nennt die Zahl); Konten, die es nicht mehr gibt, zählen nie als Autor „[deleted]“.
- **Ziele der Automatik:** Video-Link, Suchbegriff (auch `r/name` für neue Beiträge eines Subreddits) und Profil (`u/name`); Hashtags gibt es bei Reddit nicht. Ohne Konto erlaubt, auch mit Tor oder kostenlosen Proxys (ohne Konto).
- **Grenzen, live geprüft am 11.10.2026 von diesem Rechner:** Reddit sperrt manche Verbindungen ganz („blocked by network security“) und zeigt Besuchern ohne Anmeldung bei Beiträgen mit Kommentaren oft einen Menschen-Test („Prove your humanity“). Beides erkennt das Programm und meldet es auf Deutsch; es versucht nicht, die Sperre zu umgehen. Bei diesem Rechner lieferte die Liste neuer Beiträge eines Subreddits (`r/python`) drei Links, ein einzelner Beitrag mit Kommentaren aber den Menschen-Test; mit einem Reddit-Konto wurde **nicht** geprüft (kein Konto vorhanden).

### X – Browser mit Konto

- **Mit Konto** (Tab „Konten“ → X → „Im Browser anmelden“, im sichtbaren Fenster, wie bei TikTok): Der Beitrag wird unsichtbar geöffnet, die internen Antworten der Seite (`TweetDetail`, `TweetResultByRestId`) werden abgefangen und tolerant ausgewertet (Langtexte, Medien, Zitate, Antworten mit Verschachtelung), weitere Antworten laden durch Scrollen. Findet sich so nichts, werden die sichtbaren Beiträge gelesen. Nur mit selbstgebauten Beispieldaten geprüft, **nicht gegen echtes X**, weil kein Konto vorhanden war.
- **Ohne Konto** geht nur ein einzelner Beitrag **ohne Antworten**, über den öffentlichen Einbettungsdienst von X. Das Ergebnis sagt dann klar „Antworten brauchen eine Anmeldung“. Live geprüft (siehe unten).
- **Links:** `x.com/<name>/status/<Nummer>`, `twitter.com/…`, `mobile.twitter.com/…`, `x.com/i/web/status/<Nummer>`, auch mit `?s=…&t=…`. Dienste wie `fxtwitter.com` zählen nicht. `t.co`-Kurzlinks werden nur erkannt, aber nie verfolgt (sie können überallhin führen): Öffne sie im Browser und kopiere die Adresse des Beitrags.
- **Abbildung:** Beschreibung = Text; Aufrufe = Ansichten (nur mit Konto); Likes; Kommentare = Zahl der Antworten; `share_count` = Weiterleitungen + Zitate (die Aufteilung steht unter `raw` als `retweet_count` und `quote_count`); gespeichert = Lesezeichen; Hashtags und Erwähnungen aus dem Text. Antworten sind die Kommentare; `parent_id` ist leer bei direkten Antworten. X zeigt nicht alle Antworten (versteckte, mögliche Spam-Antworten): „weniger als gemeldet“ ist normal.
- **Ziele der Automatik:** Video-Link, Hashtag, Suchbegriff und Profil – alle nur mit bestätigter Anmeldung („Anmeldung fehlt für Konto …“). Mit Tor oder kostenlosen Proxys wird X immer abgelehnt.
- **Risiko, ehrlich:** Das automatische Auslesen mit einem Konto verstößt gegen die Regeln von X. Das Konto kann eingeschränkt oder gesperrt werden, und die Auswertung kann jederzeit brechen, wenn X seine Seite ändert. **Nimm ein Zweitkonto**, kein wichtiges. Marc kennt und trägt dieses Risiko.

## Grenzen (ehrlich)

- **Reddit:** nur im Browser, Anmeldung freiwillig. Reddit sperrt manche Netze und zeigt ohne Anmeldung oft einen Menschen-Test; am 11.10.2026 ging von diesem Rechner nur die Liste neuer Beiträge, ein Beitrag mit Kommentaren nicht (siehe „Reddit und X“). Mit Konto nicht geprüft.
- **X:** Antworten, Suche, Hashtags und Profile nur mit Konto im Browser, gegen die Regeln von X und deshalb mit Sperrrisiko (Zweitkonto!); die Auswertung mit Konto ist **nicht gegen echtes X geprüft**. Ohne Konto geht nur ein einzelner Beitrag ohne Antworten (live geprüft).
- **TikTok:** Videodaten ohne Konto gehen; Kommentare brauchen oft eine Anmeldung und stoßen auf eine CAPTCHA-Abfrage, die du im Browserfenster selbst löst. TikTok-Hashtags und -Suche brauchen ein angemeldetes Konto.
- **Instagram:** geht nur angemeldet; Hashtags, Suche und Profile werden nicht unterstützt.
- **YouTube:** ohne Konto möglich.
- **Ganze Kanäle:** siehe „Ganze Kanäle“ – Plattform-Listen sind begrenzt, TikTok/Instagram/Reddit/X nur mit Attrappen geprüft, Anti-Detect ist kein Versprechen.
- **Kostenlose Proxys und Tor** (nur für Abrufe ohne Konto): siehe Abschnitt „Proxys und Tor“. Langsam, unzuverlässig, nicht für Konten, Instagram und TikTok-Kommentare.
- Das Programm läuft aus der installierten Umgebung (Ordner `.venv`); die .exe startet es nur und ist keine einzelne Datei zum Weitergeben. Eine Einzel-.exe mit allem drin wurde geprüft, aber nicht ausgeliefert (`docs\EXE.md`).
- Nicht live geprüft: Instagram und angemeldete TikTok-Wege (deine Anmeldung fehlt noch), Reddit-Beiträge mit Kommentaren (am Testrechner blockiert), X mit Konto (kein Konto), Cloud-Datenbank, Server/Docker.

## Einrichten und Textmenü

1. Den Ordner vollständig entpacken; die Dateien müssen zusammenbleiben.
2. **Einrichten.cmd** doppelklicken. Es prüft Python, Chrome und Node.js, richtet die benötigten Pakete ein und legt eine leere Zugangsdaten-Datei an. Eine vorhandene `.venv` und vorhandene Daten werden weiterverwendet. Auf diesem Rechner ist die mitgelieferte lokale Umgebung bereits vorbereitet; eine ZIP enthält diese große Umgebung nicht.
3. Wenn du später deine Konten verwenden möchtest, **Starten.cmd** doppelklicken. Das Textmenü bietet Anmeldung, Anmeldestatus, Video-Abruf, Abmelden und Beenden (Plattformen: TikTok, Instagram, Reddit, X; der Video-Abruf erkennt auch YouTube-Links).

Die Einrichtung benötigt eine Internetverbindung und Python ab 3.11, Google Chrome sowie Node.js ab Version 22. Die Startdateien funktionieren auch in Ordnern mit Leerzeichen. Eine Änderung der globalen PowerShell-Einstellungen ist nicht nötig.

## Zugangsdaten später einfügen

Öffne **data/credentials.json** lokal in einem Texteditor und fülle die leeren `user`- und `password`-Felder der gewünschten Plattform aus (oder im Tab „Konten“ auf „Zugangsdaten-Datei öffnen“ klicken). Die Struktur entspricht credentials.example.json. Vorhandene Zugangsdaten werden beim erneuten Einrichten nicht überschrieben.

Im Menü **Anmelden** kannst du wählen:

- **Im Browser anmelden:** Du meldest dich im separaten Fenster selbst an; TikTok bietet dort auch den QR-Code. Die Zugangsdaten-Datei darf dabei leer bleiben.
- **Gespeicherte Zugangsdaten:** Das Programm verwendet die lokal eingetragenen Werte. Zusätzliche Bestätigungen und CAPTCHA erledigst du im Browser.

Passwörter werden vom Menü weder abgefragt noch ausgegeben. Die JSON-Datei bleibt lokal und sollte nicht weitergegeben werden. Bei Anführungszeichen oder Rückwärtsschrägstrichen im Passwort muss die Datei gültiges JSON bleiben; alternativ im Browser anmelden.

Die gespeicherten Sitzungen liegen ausschließlich im eigenen `data`-Ordner. **Abmelden** löscht die gespeicherten Cookies und das Browserprofil der Plattform; dafür müssen ihre Browserfenster geschlossen sein. Beim Login lehnt das Programm optionale Cookies ab und klickt nie „Alle akzeptieren“. Das Programm übernimmt keine Anmeldung aus deinem normalen Chrome-Profil. Das erste Konto je Plattform heißt „Hauptkonto“ und nutzt die Identität `tiktok_main` beziehungsweise `instagram_main` (YouTube: `youtube_main`), mit Land `de` und Desktop-Profil; weitere Konten bekommen ihren eigenen Schlüssel.

## Erster echter Abruf

Für TikTok oder Instagram zuerst anmelden und den Anmeldestatus prüfen. Instagram benötigt eine gültige eigene Anmeldung. YouTube kann ohne Konto abgerufen werden.

Danach im Tab **Abrufen** (oder im Textmenü unter **Video-Link auslesen**) einen öffentlichen TikTok-, Instagram-, YouTube-, Reddit- oder X-Link einfügen. Daten werden in `data/scrape.db` gespeichert; im Fenster siehst du sie unter „Gespeicherte Daten“, der Export legt einen neuen Ordner an. Einige Videos haben keine öffentlich verfügbaren Kommentare; ein erfolgreicher Abruf muss daher nicht die gewünschte Zahl Kommentare ergeben.

Der öffentliche Testlauf mit YouTube war einschließlich Speicherung und Export erfolgreich. TikTok-Videodaten wurden gespeichert, die Kommentare am Testlink waren wegen einer CAPTCHA-Abfrage nicht prüfbar. Instagram und die angemeldeten TikTok-Wege benötigen noch deine Anmeldung und einen anschließenden Test mit deinem Link. CAPTCHA und zusätzliche Kontobestätigungen erledigst du manuell im separaten Browserfenster.

## Einstellungen und lokale Prüfung

Einstellungen stehen in `settings.toml` und werden über den Tab „Einstellungen“ bearbeitet (Kommentare in der Datei bleiben erhalten). `max_comments = 0` lädt nur Videodaten; `rotation_every` (1 bis 100, Standard 5) sagt, nach wie vielen Videos die Konten-Rotation zum nächsten Konto wechselt; `session_max_age` ist in Stunden angegeben und kein hartes Ablaufdatum: Ist die gespeicherte Anmeldung älter, wird sie im Browser neu geprüft; bestätigt die Prüfung sie, werden die Cookies aufgefrischt und die Zeit beginnt von vorn, nur bei einer abgemeldeten Sitzung ist sie abgelaufen. Das Menü und die Abrufe verwenden den lokalen Chrome-Browser und je Konto ein eigenes Land; neue Identitäten über die Kommandozeile starten mit `us` und Desktop, vorhandene Identitäten behalten Land und Gerät. Mobile Identitäten funktionieren nur mit der Engine `patchright`. Proxys sind optional und zunächst leer. Gewöhnliche Proxy-Benutzernamen bleiben unverändert; Sessionvorlagen können `{identity}` und `{country}` im Benutzernamen verwenden.

Die Einrichtung bevorzugt requirements-lock.txt mit den geprüften Paketversionen. Fehlt diese Datei, verwendet sie requirements.txt. Eine erneute reine Prüfung ohne Installation oder Konto-Aufruf ist möglich:

```powershell
.\Einrichten.cmd -NurPruefen -OhnePause
.\Starten.cmd -NurPruefen -OhnePause
```

Die automatischen Tests laufen mit der installierten Umgebung und prüfen lokale Beispiele, simulierte Antworten und (nur auf Wunsch, unsichtbar) echte lokale Chrome-Prüfungen. Die Zahl der Tests wächst mit dem Programm; maßgeblich ist das Ergebnis des Laufs, nicht eine Zahl in dieser Datei. Die öffentlichen Plattformabrufe sind separat in READINESS.json dokumentiert. Tests berühren nie die echten Daten (Datenbank, Zugangsdaten, `settings.toml`): sie arbeiten in Wegwerf-Ordnern.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q socialscraper tests
```

Ohne echte Kommentar-ID kann der Autor-/Text-Fallback identische Kommentare desselben Autors zusammenfassen. Alte Datenbankzeilen ohne IDs bleiben erhalten; neue Speichervorgänge verwenden eindeutige IDs. Laufzeitdaten, Zugangsdaten und Browserprofile gehören nicht ins Quellpaket.

Der zusätzliche Nodriver-Browsercheck ist bewusst optional (er startet ein unsichtbares Chrome). Für den vollständigen lokalen Prüflauf:

```powershell
$env:SS_RUN_LOCAL_BROWSER_TESTS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
