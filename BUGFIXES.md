# Behobene Fehler

- Syntaxfehler in `models.py`, der jeden normalen Import verhinderte; zusätzliche Felder und ungültige Zeitstempel werden korrekt verarbeitet.
- Fehlende eindeutige Datenbank-IDs: wiederholte Videos und Kommentare aktualisieren jetzt vorhandene Zeilen. Ein Video mit Kommentaren und Verlaufssnapshot wird als eine Transaktion gespeichert; fehlerhafte Ergebnisse hinterlassen keine Teilstände.
- Fehlende Exportordner, ungültige Tabellenwahl, Datenbank-Verbindungsfehler und offene Verbindungen bei Schemafehlern.
- Falsch einsortierte TOML-Einstellungen hinter `[sphere_presets]`; Kommentargrenze 0 und Konfiguration für Login, Anmeldedauer, Meldungen und Zugangsdaten greifen zuverlässig.
- Abstürzender Profilbefehl, fehlende Optionen, falsche Login-Parameter und nur teilweise konfigurierte Meldungen.
- Falsche Plattform bei Anmeldeprüfungen, unzuverlässige Erfolgsmeldungen, abgelaufene Cookies, vermischte Cookie-Domains, ignorierter Zugangsdatenpfad und Stundenumrechnung.
- Instagram-Browseranmeldung wird über die öffentliche `Instaloader.load_session`-Schnittstelle auch vom Instagram-Adapter verwendet.
- Kommentar-Dubletten beim Scrollen, ungenaue Kommentargrenzen, falsche Antwortzuordnung/Zeitstempel und unerwünschte Wiederholungen nach Sessionverlust.
- URL-Zuordnung nach echtem Host statt Textfundstelle; optionale Pakete werden erst bei ihrer Verwendung geladen.
- Proxy-Zugangsdaten werden nicht ungefragt umgeschrieben; URL-Zeichen, IPv6, Identitätspfade und Länderzuordnung werden korrekt behandelt. YouTube erhält einen konfigurierten Proxy.
- Browser-Sessions werden nach Plattform getrennt und berücksichtigen Ablaufzeit, maximale Anzahl und Headless-Einstellung. Wiederverwendung, Startfehler und Bereinigung sind abgesichert.
- Nodriver-Schnittstellen: Auswertung von Funktionen/Argumenten, falsche Rückgabewerte, fehlende Klickmethode, Cookie-Schnittstellen und nicht abgewartete Hintergrundoperationen.
- Mehrere lokale Browser-Sessions teilen ihre Playwright-Laufzeit. Zugangsdaten, Cookie-Dateien und Datenbanken sind im lokalen `.gitignore` ausgeschlossen.

## Nachweise und Grenzen

TESTRESULTS.json dokumentiert 149 bestandene Tests, einschließlich echter lokaler Chrome-Prüfungen. READINESS.json hält die installierten Versionen, beide bestandenen Browserwege sowie die öffentlichen Plattformabrufe fest: YouTube mit fünf Kommentaren und TikTok-Videodaten. Die TikTok-Kommentaransicht wurde wegen CAPTCHA nicht geprüft. Echte Account-Anmeldungen bleiben für den Nutzer offen.

API-Abgleich anhand der Originalquellen:

- [Instaloader – öffentliche Session-Schnittstelle](https://instaloader.github.io/module/instaloader.html)
- [Playwright – BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext)
- [Nodriver – Tab](https://ultrafunkamsterdam.github.io/nodriver/_modules/nodriver/core/tab.html)
- [Nodriver – Element-Klick](https://ultrafunkamsterdam.github.io/nodriver/_modules/nodriver/core/element.html)
- [yt-dlp – Python-Optionen](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py)

Die im Chat genannten Linken-Sphere-Endpunkte und aktuelle Plattform-Selektoren bleiben ohne Live-Prüfung unbestätigt. Die PULSE-Anwendung erhält durch diese separate Scraper-Fassung keine neue Datenquelle.

## Einrichtung und Live-Prüfung

- Aktuelles yt-dlp mit passenden EJS-Komponenten, geprüfter lokaler Node-Laufzeit und klaren Einrichtungsfehlern. Die Komponenten werden bei der Einrichtung installiert.
- TikTok liest das Hydration-JSON auch aus dem Script-Element; zuvor war die gleichnamige window-Eigenschaft ein HTMLScriptElement statt der erwarteten Daten.
- Nodriver verwendet einen absoluten Chrome-Profilpfad; relative Pfade beendeten den echten Chrome-Start. Eingabe-Methode und Windows-Prozessbereinigung sind ergänzt.
- Windows-Einrichtung und Startmenü verwenden eigene Umgebung, feste Plattform-Identitäten und UTF-8. Vorhandene Zugangsdaten werden erhalten.
- Zugangsdaten-Dateien mit UTF-8-Kennzeichnung aus Windows-Editoren werden korrekt gelesen.
- requirements-lock.txt enthält die tatsächlich installierten und geprüften Versionen. Die Quelldateien und das ZIP enthalten keine Account-Daten oder Browserprofile.

## Code-Prüfung vom 11.10.2026

- TikTok-Kommentare: Das Skript suchte den Kommentartext innerhalb des Elements, das selbst schon der Text ist, und fand dadurch nie Kommentare. Es liest jetzt das innerste Textelement und holt Name und Likes aus dem umgebenden Kommentarblock. Getestet im echten Chrome mit beiden bekannten Layouts. Die DOM-`id` dient nicht mehr als Kommentar-ID.
- Login: Lädt die Seite während der Anmeldung neu, etwa nach dem Absenden, beim QR-Wechsel oder bei der Weiterleitung, bricht die Anmeldung nicht mehr ab.
- Login: Beim Anmelden werden nur noch ablehnende Antworten angeklickt (Deutsch und Englisch); optionale Cookies werden abgelehnt, „Alle akzeptieren“ nie automatisch geklickt. Ein fehlender Anmelde-Button kostet höchstens 5 Sekunden statt 30.
- Anmeldestatus: Mehrere Versuche über bis zu 10 Sekunden statt einer einzelnen Prüfung nach 4 Sekunden; zusätzliche sprachunabhängige und deutsche Merkmale. Ein angemeldeter Account wird dadurch nicht mehr so leicht als „nicht bestätigt“ gemeldet.
- Abmelden löscht neben der Cookie-Datei auch das Browserprofil der Plattform. Ist der Browser noch offen, bleibt die Anmeldung vollständig erhalten und eine klare Meldung erscheint. Im Startmenü gibt es dafür den Punkt **Abmelden**.
- Kommandozeile: Ohne `--country` und `--mobile` gelten die Werte der gespeicherten Identität; vorher scheiterte z. B. `check` für `tiktok_main` am Standardland `us`.
- Patchright sendet bei Desktop-Identitäten den User-Agent des installierten Chrome statt des veralteten festen `Chrome/133`. Mobile Identitäten verwenden die installierte Chrome-Hauptversion.
- Nodriver übernimmt Zeitzone, Sprache und Standort der Identität wie Patchright und lehnt mobile Identitäten mit klarer Meldung ab.
- Private, gelöschte oder nicht unterstützte YouTube-Videos und nicht gefundene Instagram-Beiträge werden nicht mehr mehrfach abgerufen; vorübergehende Fehler werden weiterhin wiederholt.
- TikTok speichert die endgültige Video-URL ohne Tracking-Parameter; Kurzlinks werden aufgelöst.
- JSONL-Export: `hashtags`, `mentions` und `raw` sind echte JSON-Werte statt JSON-Text in JSON.

## Desktop-Programm und Prüfung vom 11.10.2026

- Neu: Programmfenster (`socialscraper/gui.py`, Logik in `guicore.py`), Desktop-Verknüpfung mit Icon, `Socialscraper.cmd`; Einrichten.cmd prüft Tkinter und legt die Verknüpfung an.
- YouTube: „This video is unavailable“ wurde als vorübergehender Fehler dreimal wiederholt (rund 10 Sekunden Wartezeit); es zählt jetzt als dauerhaft und scheitert sofort mit klarer Meldung.
- Oberfläche: Beim Lesen der Datenbank blieben Verbindungen offen und sperrten die Datei unter Windows; sie werden jetzt geschlossen. Einstellungen werden CRLF-erhaltend geschrieben.
- Neue Tests: `tests/test_gui.py` (24 Fälle: Links, Befehle, Status, Einstellungen, Datenbank, Export, Prozessabbruch, Fenster mit echten Läufen). Sie berühren nicht die echten Nutzerdaten.
- Windows-Fehlfenster „Ungültiges Bild“ beim YouTube-Abruf: Smart App Control blockierte die unsignierten Programmdateien von pycryptodomex (Fehler 4551), die yt-dlp über den Zusatz „default“ mitinstalliert hatte. Das Paket wird nicht mehr installiert (YouTube braucht es nicht), und das Programm unterdrückt Windows-Fehlfenster für blockierte Dateien, damit ein Lauf nie hängen bleibt.

## Fehlersuche vom 13.10.2026

Suchlauf über den Abruf-, Daten-, Automatik-, Schnittstellen- und Fensterteil (96 Funde, davon 3 schwer, 33 mittel, 60 leicht); korrigiert wurde, was sich am Code belegen ließ. Jede Korrektur hat einen Rückfalltest (`tests/test_fehlerkorrekturen.py`, `tests/test_korrekturen_netz.py`, `tests/test_korrekturen_planer.py`, `tests/test_korrekturen_fenster.py` und Ergänzungen in bestehenden Testdateien). Nicht Teil des Auftrags waren Registrierung, Postfächer, Aufwärmen, Streuung und automatische Aktionen.

Schwer:
- YouTube: Die Kommentarzahl eines Videos war nur die Zahl der geholten Kommentare (yt-dlp überschreibt das Feld nach dem Kommentarabruf). Jetzt wird die Zahl der Seite vor der Verarbeitung gerettet; Teilergebnisse heißen wieder „teilweise“.
- Automatik: Instagram-Ziele scheiterten immer mit „Anmeldung fehlt“, weil die Anmeldeprüfung des Planers Adapter ohne Browser nicht prüfen kann. Der Instagram-Adapter prüft seine Anmeldung jetzt wie in der Befehlszeile selbst.
- Tab „Gespeicherte Daten“ stürzte ohne Datenbankdatei beim Längenvergleich ab.

Mittel (Auswahl):
- Schnittstelle: Einzelabruf eines Videos ohne `duration_class`; `/v1/people` ohne Rolle sortierte nicht nach erster Sichtung; Gruppenangaben wie „²“ oder 20 Ziffern gaben 500; Gruppen mit Ziffernnamen sind erreichbar.
- Cloud-Abgleich löschte eine bekannte Videolänge mit leer; Verlaufspunkte der Befehlszeile hatten ein anderes Zeitformat; die Migration schreibt `first_seen_at` im vereinbarten Format, Personenlisten vergleichen beide Schreibweisen.
- `integration.json`/`ai.json` in falscher Kodierung oder mit Text statt Zahl ließen den Einstellungen-Tab (und damit den Programmstart) abstürzen.
- Fehler des Hintergrunddienstes erschienen nie im Problemfenster (Datum und Millisekunden im `daemon.log`); der Passwort-Filter fehlte im `daemon.log`.
- Rotation: Der 403-Zähler überlebte den Kontowechsel; bei Standard-/festem Konto lief ein Lauf nach CAPTCHA/Sperre/429 mit demselben Konto weiter; Mitternacht/Ruhezeiten am Tag der Zeitumstellung.
- Ausführungssperre: Dienst, `run-target` und Fenster nehmen bei eigenem `db_path` dieselbe Sperrdatei; `Dienst stoppen` räumt hart beendete Läufe auf.
- Abbrechen von „Jetzt ausführen“ setzt eine Abbruchdatei (`SS_CANCEL_FILE`), der Lauf endet selbst als „abgebrochen“; erst nach 10 s wird hart beendet, danach werden hängengebliebene Läufe als unterbrochen vermerkt.
- `account_status` meldete „Anmeldung gespeichert“ bei abgelaufenen Cookies (neu: „abgelaufen“); `login()` nennt den echten Grund (toter Proxy, Netz); beschädigte Identitätsdateien heilen sich; toter Playwright-Treiber nach Zeitlimit wird neu gestartet; X mit Nodriver endet mit klarer Meldung.
- Reddit-Kanalliste: Zwischenstand-Cursor zeigte schon auf die nächste Seite; TikTok: Wörter wie „puzzle“ im Seitentext galten als CAPTCHA.
- Fenster: Fehler beim Neuladen eines Tabs legten die Auftragsschleife still; Schließen während Hintergrundarbeit; Export/Sicherung laufen nach dem Schließen zu Ende; Knopf „Prüfen“ im Kanal-Dialog und „Schließen“ im Stimmungsdialog blieben gesperrt; Kontoauswahl sprang bei Lesefehler auf „Rotation“; Fenstergröße/Tab beim Schließen während eines Auftrags; Zweitbildschirm.
- Docker: `settings.toml` (Proxy-Liste mit Passwort) und `*.bak` kommen nicht mehr ins Image; `stop_grace_period`; Reihenfolge in `docs/SERVER.md` Schritt 3.

Nachtrag vom 14.10.2026:
- Befehlszeile `scrape` mit festem Konto hält nach CAPTCHA, Sperre, abgelaufener Anmeldung oder „zu viele Anfragen“ an, statt mit demselben Konto weiterzumachen (wie der Planer).
- Eine belegte oder nicht lesbare Datenbank heißt in `channel status/list` und im Einzelheiten-Fenster nicht mehr „diesen Kanal gibt es nicht“, sondern wird als solche gemeldet; der Automatik-Tab bleibt tolerant.
- „Heute“ bei „neu seit“ und die Zeiten aus `daemon.log` werden über die Ortszeit des jeweiligen Tages gerechnet (Zeitumstellung).
- Lesende Verbindungen des Automatik-Tabs gehen ebenfalls über `util.ro_connect` (Netzwerkpfade).
- `TESTRESULTS.json` und `READINESS.json` tragen den Stand nach der Fehlersuche.

Bewusst nicht geändert (Entscheidung nötig): Instagram-Sitzungsdatei je Konto; `integration.json` wird von Dienst und Fenster ohne gemeinsame Sperre geschrieben; `created_at` weiter mit `+00:00`; Herzschlag des Dienstes läuft im eigenen Thread; „Konto hinzufügen“ bietet YouTube/Google/Trustpilot an; „Standardkonto“ beim Verfolgen wird als festes Konto gespeichert.

Nachtrag vom 19.10.2026:
- Das Hauptfenster wartet beim Schließen auf laufende Hintergrundhelfer. Ihre Fertigmeldung erfolgt erst nach dem echten Threadende; so wird der letzte Tk-Verweis nicht aus einem Neben-Thread freigegeben.
- Geprüft: vollständige Offline-Suite (2.693 Fälle, 84 ausgelassen) vor diesem Nachtrag; danach drei gezielte Oberflächenfälle mit Tk-Laufzeitwarnungen als Fehler (3/3 bestanden).

Profilangaben und Eingabetempo, 19.10.2026:
- Google-Suche: Ein Browser-Zeitlimit nach Enter verwirft den bereits gestarteten Suchschritt nicht mehr. Der Auftrag beobachtet die nächste Seite innerhalb seines Gesamtzeitlimits, ohne Enter erneut zu senden. Mit einmaliger Suche bei verzögerter Navigation geprüft; 32 gezielte Navigationsfälle bestanden.
- WEB.DE-Seitenvarianten: `/premium/tarifevergleich-mobile/` wird wie die Desktop-Tarifübersicht anhand der tatsächlichen Zielseite eingeordnet, auch mit Tracking-Query und Fragment. Unbekannte Zwischenseiten auf den zugelassenen Anbieterhosts bleiben ohne automatische Aktionen zur manuellen Fortsetzung offen. 25 gezielte Fälle einschließlich lokalem Chrome mit der beobachteten mobilen Route bestanden; kein Nachweis eines echten Kontoabschlusses.
- Live-Einstieg WEB.DE: mobile Cookie-Weiterleitung unter `/magazine/consent-management/` ergänzt. Der sichtbare „Registrieren“-Kopfbereich kann als Link oder Knopf dienen; unbekannte Knopfziele werden erst nach der tatsächlichen Navigation geprüft. Manuelle Tarifnavigation wird erkannt; fehlende Links führen zu einem Hinweis und weiterem Warten innerhalb des Gesamtzeitlimits. Geprüft: 36 gezielte Fälle sowie zwei lokale Chrome-Abläufe einschließlich des mobilen Knopfs und der vollständigen nachgebildeten Postfachregistrierung. Ein echter Kontoabschluss ist damit noch nicht nachgewiesen.
- Unter Profile können Personendaten und Mausgeschwindigkeit, Klickpause sowie Tippgeschwindigkeit pro Kennung gespeichert werden. Neue Profile beginnen mit bearbeitbaren Standardwerten; Name und ausdrücklich gewähltes Geschlecht werden vor neuen Registrierungen geprüft.
- Gemeinsame Browser-Formularaktionen verwenden das gespeicherte Tempo; native Android-Registrierungen verwenden Tippgeschwindigkeit und Pause vor Berührungen. Teilweise Eingaben und unklare Klicks werden nicht automatisch wiederholt.
- Ein lokaler Importvertrag für das spätere Datentool ist vorbereitet. Bestehende widersprüchliche Personendaten werden nicht überschrieben; kein externer Dienst ist angebunden.
- Arbeitsregeln: Textumbruch richtet sich nach der Kartenbreite. Die vorherige Rückkopplung zwischen Beschriftungsbreite und Umbruch verlangsamte den Fensteraufbau.
- Nachprüfung: 255 Regressionstests ohne Fehler (8 ausgelassen); 85 gezielte Fälle ohne Fehler (10 ausgelassen). Nach der Umbruchkorrektur nochmals 16 Fälle einschließlich lokalem Chrome, Profileditor und Registrierungsdialog vollständig bestanden. Keine Live-Registrierungen.

