# Lokaler Datengenerator

Unter **Profile → Datengenerator – lokale Tests …** wird standardmäßig
`Desktop/Datengenerator.html` angeboten. Ein anderer lokaler HTML-Pfad kann gewählt
werden. Die Originaldatei wird weder verändert noch ausgeführt. Es werden nur die
eingebetteten JSON-Namenslisten und gzip/base64-Straßendaten gelesen. HTML-Größe,
entpackte Gesamtgröße und Zeilenlänge sind begrenzt; es gibt keine Downloads.

Der vorhandene Generator enthält deutsche Straßen, PLZ, Orte und Namenslisten.
Geschlecht und Geburtsdatum fehlen. Hausnummern werden im Original zufällig
erzeugt, sind aber nicht durch die Straßenquelle belegt. Diese drei Felder bleiben
in der Anbindung leer. Namenskombinationen sind synthetisch; Straßen können real
sein. Daraus folgt keine tatsächliche Identität oder ein bestätigter Wohnort.

## Ortsreferenz

Bei zuvor ausgewähltem Profil kann dessen gespeicherte Sticky-Messung gelesen
werden. Der gespeicherte Herkunftshash muss zum derzeit fest zugeordneten Proxy
passen. Ohne passende Messung, bei anderem Land als DE oder fehlender Stadt wird
kein Ersatzort gewählt. Explizit übersetzt werden nur Munich/München,
Cologne/Köln und Nuremberg/Nürnberg. Ansonsten muss die Stadt im Bestand exakt
übereinstimmen (ohne Beachtung der Großschreibung und mit Unicode-Normalisierung).
PLZ, Ort und Straße stammen aus derselben Datenzeile. Land und Stadt werden
verglichen. Die Quelle enthält keine unabhängige Regionszuordnung; Region und
tatsächlicher Wohnort können damit nicht bestätigt werden.

Die Referenz wird vor und nach der Erstellung gelesen. Ändern sich gespeicherte
Sitzung oder Proxy-Zuordnung zwischenzeitlich, wird die Erstellung abgebrochen.
Eine abgelaufene 300-Sekunden-Messung wird nur als historischer Vergleich
gekennzeichnet. Auch innerhalb der Laufzeit findet keine aktuelle Netzwerkprüfung
statt. Es wird keine neue Proxy-Sitzung gestartet und kein aktueller Standort
behauptet.

Alternativ kann eine deutsche Stadt als **lokale Simulation ohne Proxy** angegeben
werden. Diese Datensätze werden ausdrücklich als Simulation gekennzeichnet.

## Trennung von echten Profilen

Es werden ausschließlich neue Dateien unter `data/local_generator_tests`
(neben der Datenbank) gespeichert. Kennzeichnungen: `test_only: true`,
`purpose: local_fixture`, `live_verified: false`. Keine Änderung an
`profile_settings`, Konten, Registrierungsdaten oder Proxy-Konfigurationen.
Browser und Android werden nicht gestartet. Die Registrierungsabläufe lesen
diesen separaten Testordner nicht. Es gibt keinen automatischen Übernahmeweg.

Die Datenblöcke stammen aus der vom Benutzer bereitgestellten HTML-Datei; deren
SHA-256 wird im Testdatensatz festgehalten. Quellen- und Lizenzhinweise für
OpenPLZ/OpenStreetMap und die Faker-Namenslisten stehen in der Originaldatei.
Diese Originaldatei wird nicht kopiert oder mit dem Programm weiterverteilt.

Für diese Änderung wurden keine Tests hinzugefügt oder ausgeführt.
