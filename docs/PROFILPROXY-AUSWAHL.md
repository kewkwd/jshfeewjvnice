# Pro Profil einen Proxy auswählen

1. Profilwerk neu starten und **Profile** öffnen.
2. Ein Profil auswählen, dann **Profilproxy auswählen …** anklicken.
3. Einen Eintrag aus der gespeicherten Proxy-Liste auswählen oder eine vollständige
   eigene Proxy-Adresse eingeben und **Diesem Profil zuordnen** anklicken.

Die Zuordnung bleibt beim Profil gespeichert. Verschiedene Profile dürfen
verschiedene Proxys verwenden; derselbe Proxy kann auch mehreren Profilen
zugeordnet werden. Eine manuell eingegebene Adresse gilt nur für das gewählte
Profil und ändert nicht die allgemeine Proxy-Liste. Weitere Listeneinträge lassen
sich in den Einstellungen hinterlegen.

Die Profilansicht zeigt den festen Proxy mit maskiertem Passwort. Die Speicherung
verwendet die bestehende Profilauflösung in `account_proxies.json` und beschränkt
die Datei auf den Windows-Benutzer. Alle Konten mit derselben Profilkennung
erhalten dieselbe maskierte Referenz in der Datenbank. Zugangsdaten kommen nicht
in diese Referenz. Vorbereitete Profile ohne Konto werden ebenfalls unterstützt.
Beschädigte vorhandene Zuordnungen werden nicht überschrieben.

Änderungen gelten beim nächsten Start. Bereits offene Browser-/Android-Sitzungen
müssen zuvor beendet werden. Während eines verwalteten Auftrags verhindert die
Auftragssperre das Speichern einer neuen Zuordnung. Es wird kein automatischer
Proxy-Wechsel ausgeführt und die Person/Anschrift des Profils wird nicht geändert.
Der bestehende Proxy-Standorttest und die manuelle IPQS-Prüfung verwenden die feste
Zuordnung. Gespeicherte Prüfergebnisse bleiben historische Momentaufnahmen.

Für diese Änderung wurden keine Tests hinzugefügt oder ausgeführt.
