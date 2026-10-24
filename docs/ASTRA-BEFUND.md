# Astra-Befund

Stand: 24.10.2026 · geprüfter Ausgangsstand: `4869993` (`main`)

## Umfang und Ergebnis

Geprüft wurden die Dateien aus `docs/GEDAECHTNIS-v3.md`, Abschnitt 2: `personas.py`, das Paket `fyp`, die beiden zugehörigen CLI-Befehle, `checks/watchtime_check.py` und `store.py`. Für die Watch-Time-Prüfung war zusätzlich eine kleine Korrektur in `backends/nodriver_shim.py` nötig. Die festgestellten Datenbank-, Buchungs- und Aufruffehler sind im lokalen Checkout behoben. Die öffentlichen Signaturen aus Abschnitt 4 sind unverändert.

## Befunde und Korrekturen

| Bereich | Reproduzierter Befund | Korrektur |
| --- | --- | --- |
| `personas.ensure_schema`, `fyp.ensure_schema`, `Store._migrate` | `executescript()` schloss eine bereits offene Transaktion. Ein erzwungener Fehler ließ Tabellen oder Account-Spalten nur teilweise angelegt zurück. Zwei parallele Persona-Migrationen konnten mit `duplicate column name: persona_id` scheitern. | DDL wird einzeln in einer Transaktion ausgeführt. Bei einer äußeren Transaktion werden Savepoints verwendet; `Store` migriert Persona- und FYP-Schema gemeinsam. Die Spaltenprüfung erfolgt nach Erwerb der Schreibsperre. |
| `personas.anlegen`, `commands/personas.py` | Ein Fehler beim Anlegen eines Kanals konnte eine Persona ohne ihre Kanäle hinterlassen. `personas vorschlag` verwendete bei jedem Lauf dieselben Namen; ein späterer Namenskonflikt ließ bereits angelegte Vorschläge bestehen. | Persona und Kanäle werden atomar angelegt. Der CLI-Befehl vergibt freie Namen und speichert den gesamten Vorschlagslauf in einer Transaktion über `personas.anlegen`. |
| `fyp.verbuche` | Eine unbekannte Identity erzeugte einen Eintrag in `konsum_log`, obwohl kein Account aktualisiert wurde. | Die Kontoaktualisierung wird geprüft; Log und Account werden gemeinsam geschrieben oder vollständig zurückgerollt. |
| `fyp/konsum.py` | Relative Video-Links wurden nicht als Treffer erkannt. Treffer hatten immer `video_uid=NULL`; SQLite ließ dadurch identische Treffer mehrfach zu. Drei Wiederholungen konnten `fyp_status='im_fyp'` auslösen. | Autor und echte Video-ID werden aus relativen und absoluten TikTok-Links gelesen. Neue Treffer werden per ID dedupliziert; alte NULL-Treffer zählen nicht mehr für den Schwellwert. Treffer, Status und Sitzungslog werden gemeinsam gebucht. Bei null Schritten wird keine Sitzung gebucht. |
| `fyp/seed.py` | Der Influencer wurde in der Kanalliste doppelt besucht. Auch ein gestoppter oder leerer Lauf setzte `fyp_status='gesaet'`. | Der Influencer erscheint einmal. Der Status wird nur nach einem vollständig beendeten Lauf mit mindestens einem Video gesetzt. |
| `fyp/monitor.py` | Nach einem konkurrierenden `INSERT OR IGNORE` konnte eine nicht eingefügte Video-ID trotzdem als neu gemeldet werden. | Nur ein tatsächlich eingefügter Datensatz wird gemeldet. |
| FYP-CLI und Browser-Ressourcen | `--konto` akzeptierte fehlende oder plattformfremde IDs; `seed` ohne Auswahl betraf alle TikTok-Konten. Nichtpositive Konsumdauer wurde akzeptiert. Fehler beim Laden einer Identity ließen bereits erzeugte Browser-Ressourcen offen. | Konto und Plattform werden geprüft; `seed` verlangt eine konkrete Konto-ID; die Dauer muss positiv sein. Die Laufzeitumgebung wird auch bei Identity-Fehlern geschlossen. |
| `watchtime_check.py`, `nodriver_shim.py` | Der Watch-Time-Check nutzte `page.on('request')`, das im Nodriver-Shim fehlte. Eine unbekannte Konto-ID führte still zu einer anderen Identity; gespeicherte mobile Profile wurden als Desktop geöffnet. Seitenlade-Telemetrie konnte als Wiedergabe-Telemetrie zählen. | Der Shim bietet das Request-Ereignis über CDP an. Konto und gespeicherter Gerätetyp werden geprüft; die Laufzeitumgebung wird auch bei Fehlern geschlossen. Nur Requests während des Messfensters werden gezählt. Die Ausgabe behauptet keine serverseitig nachgewiesene Watch-Time. |

## Verträge und Schema

Die Signaturen von `personas.ensure_schema(db)`, `personas.anlegen(db, name, *, influencer, kanaele, beschreibung=None, influencer_gewicht=1.0, verhalten=None, now=None)`, `personas.aktive(db, platform='tiktok')`, `personas.persona_von(db_path, identity, platform='tiktok')`, `personas.zuweisen(db_path, platform='tiktok', *, nur_ohne=True)`, `fyp.ensure_schema(db)` und `fyp.verbuche(db_path, identity, *, schritte, treffer, now=None)` wurden geprüft und beibehalten. Die Signaturen der bestehenden Module `repo`, `streuung`, `rotation` und `aktionen` sowie das vorgegebene Idempotenzformat wurden nicht geändert.

Nach einer erneuten Migration sind genau die vorgesehenen neuen Tabellen (`personas`, `persona_kanaele`, `fyp_videos`, `fyp_treffer`, `konsum_log`) und Account-Spalten (`persona_id`, `tier`, `folgt_influencer`, `fyp_status`, `warmth_score`, `letzter_konsum`, `konsum_sitzungen`) vorhanden. Ein zweiter `_migrate()`-Aufruf sowie parallele Starts gegen eine frische und eine minimale ältere SQLite-Datenbank gelangen im lokalen Test.

## Prüfungen und Grenzen

- Mit `.venv\Scripts\python.exe` bestanden 29 gezielte und bestehende Tests (`test_astra_schema`, `test_astra_cli`, `test_astra_fyp`, `test_repo`). Die betroffenen Python-Dateien ließen sich kompilieren; `git diff --check` meldete keine Formatfehler.
- Die hier eingerichtete `.venv` verwendet Python **3.12.14**. Die im Auftrag genannte Python-Version 3.14 stand in diesem Checkout nicht bereit. `requests` und `nodriver` waren nicht installiert; deshalb wurden der bestehende umfassendere Audit-Test und ein echter Nodriver-Lauf nicht ausgeführt.
- Ein zusätzlicher Backend-Testlauf hatte einen Fehler in `test_local_initialization_accepts_script_and_applies_cookies`: Der unveränderte `LocalBackend` fügt ein Fingerprint-Skript hinzu, während der Test eine leere Liste erwartet. Dieser Befund wurde nicht im Rahmen des Astra-Auftrags geändert.
- Es gab keinen Live-Test gegen TikTok. Steigende Videozeit und begleitende Browser-Telemetrie beweisen keine serverseitig verbuchte Watch-Time. Browser-Aktionen und SQLite lassen sich außerdem nicht gemeinsam transaktional zurückrollen: Bei einem Prozessabbruch nach einer Browser-Aktion, aber vor dem Datenbank-Commit, kann eine externe Aktion ohne lokalen Eintrag bestehen bleiben.
- `folgt_influencer` wird bei der Persona-Zuweisung gesetzt, im FYP-Ablauf aber nicht ausgewertet. Die genaue Bedeutung dieses Flags für Follow-Aktionen ist nicht spezifiziert; eine Verhaltensänderung ohne diesen Vertrag wurde nicht vorgenommen.
