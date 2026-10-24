# IPQS-Prüfung

Unter **Profile → IPQS-Bewertung …** lässt sich der API-Schlüssel lokal eingeben,
speichern oder löschen. Ist ein Profil ausgewählt, kann dessen fest zugeordneter
Proxy geprüft werden; alternativ der eindeutige Proxy aus den Einstellungen.

Der Schlüssel wird mit Windows DPAPI für den aktuellen Windows-Benutzer
verschlüsselt und zusätzlich in einer auf diesen Benutzer beschränkten Datei
gespeichert (`data/ipqs_key.json`, neben der Datenbank). Keine Speicherung im
Klartext und kein unverschlüsselter Ersatz bei einem Fehler. Programme, die
bereits unter demselben Windows-Benutzer laufen, können diesen Schutz unter
Umständen überwinden; er ersetzt keinen Schutz vor Schadsoftware.

Die Funktion startet keinen lokalen Server, öffnet keinen eingehenden Port und
ändert weder Firewall noch Router. Alle Abrufe erfolgen per HTTPS mit
Zertifikatsprüfung über den gewählten Proxy. Keine direkte Ersatzverbindung.
IPQS erhält den API-Schlüssel und die gemessene öffentliche Proxy-IP. Namen,
Anschriften und Proxy-Zugangsdaten werden nicht an IPQS übermittelt. Für die
IP-Messung wird zusätzlich api.ipify.org über denselben Proxy aufgerufen.

Jeder Klick führt höchstens eine IPQS-Abfrage mit `strictness=1` aus. Es gibt keine
automatischen Wiederholungen, periodischen Prüfungen, Käufe oder Tarifwechsel.
Eine Prüfung kann einen Credit des IPQS-Kontos verbrauchen. Kontingent- und
Zugriffsfehler sowie unvollständige Antworten bedeuten **nicht bewertet**.
Die API-Adresse ist fest vorgegeben; Weiterleitungen werden nicht verfolgt.
Antwortgröße und Wartezeiten sind begrenzt. Schlüssel, vollständige API-URLs,
Rohantworten und Netzwerkfehlermeldungen werden nicht protokolliert oder angezeigt.

Angezeigt werden IP, Prüfzeit, Score 0–100 und verfügbare Hinweise auf Proxy,
VPN, Tor und Missbrauch. Nach der Abfrage wird die Ausgangs-IP nochmals gemessen.
Bei einem Wechsel wird gewarnt: Die Bewertung gilt nur für die angezeigte IP,
nicht pauschal für einen Proxy-Anbieter oder eine laufende Sitzung. Auch zwei
gleiche IP-Messungen garantieren keine dauerhafte Verbindungskonstanz.
Das letzte Ergebnis wird ohne Schlüssel unter `data/ipqs_diagnostics` gespeichert.

Dies ist eine manuelle Diagnose. Sie wählt keine anderen Proxys aus, ändert keine
Profilbindungen und löst keine Registrierungen aus. Ein niedriger Score ist keine
Sicherheitsgarantie und garantiert keine Annahme bei einem Dienst.

Implementiert, aber noch nicht mit einem echten API-Schlüssel live geprüft.
Es wurden für diese Änderung keine Tests hinzugefügt oder ausgeführt.

Offizielle Dokumentation:
- https://www.ipqualityscore.com/documentation/proxy-detection-api/overview
- https://www.ipqualityscore.com/documentation/proxy-detection-api/response-parameters
