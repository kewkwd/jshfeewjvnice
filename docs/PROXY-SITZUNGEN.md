# Evomi-Sitzungen beim Profilstart

Die Startregel liegt geschützt in `data/proxy_session_policy.json` neben der konfigurierten Datenbank. Sie ist aktuell für 300 Sekunden aktiviert und verlangt einen bestätigten IP-Wechsel. Die Laufzeit des lokalen Proxy-Pools ist davon unabhängig und wird durch diese Regel nicht verändert.

Bei regulären Browserkontexten aus der Backend-Fabrik sowie zugeordneten AVD-Profilen wird unmittelbar vor dem Start eine neue profilbezogene Evomi-Sitzung vorbereitet. Die bereits gespeicherten Grund-Zugangsdaten werden dafür nicht überschrieben. Unterstützt ist derzeit der verwendete Mobile-Endpunkt `mp.evomi.com`.

Der Ablauf:

1. Die bisherige Ausgangs-IP wird über den Profilproxy abgefragt.
2. Eine neue alphanumerische Session-Kennung wird angefordert. Evomi erhält `_session-<Kennung>_lifetime-5`; die Einheit von `lifetime` ist Minuten.
3. Öffentliche IP und ungefährer Standort werden über die neue Sitzung abgefragt. Eine zweite IP-Abfrage prüft, ob die neue Zuordnung zu diesem Zeitpunkt gleich geblieben ist.
4. Bei fehlendem IP-Wechsel, widersprüchlichen Antworten oder einem Verbindungsfehler bleibt der Profilstart angehalten. Es gibt keinen automatischen Versuch mit immer weiteren Session-Kennungen und keine direkte Ersatzverbindung.
5. Die bestätigte Sitzung wird profilbezogen gespeichert. Proxy-Prüfungen für dieses Profil verwenden anschließend dieselbe Session-Kennung. Unter Profile erscheint die geschätzte Restzeit der letzten Startprüfung.

Die 300 Sekunden beginnen mit der ersten Nutzung der neuen Anbietersitzung. AVD-Start, App-Start und manuelle CAPTCHA-Schritte verbrauchen dieses Zeitfenster mit. Nach Ablauf ist eine gleichbleibende IP nicht zugesichert. Evomi kann eine normale Sitzung außerdem bei Verbindungsproblemen vorzeitig auf eine andere IP legen. Die Anzeige ist daher eine Schätzung und kein Nachweis einer durchgehend unveränderten IP.

Quelle: [Evomi Mobile Proxy Sessions](https://docs.evomi.com/proxy-instructions/mobile-proxies/proxy-sessions/).

Die Startprüfung erstellt kein Konto und bestätigt keinen Ablehnungsgrund eines Anbieters. Die neue Startregel wurde noch nicht in einem weiteren Live-Durchlauf ausgeführt.

## Datengenerator

Die bestehende lokale Personen-Schnittstelle ist in `PROFIL-EINSTELLUNGEN.md` beschrieben. Das vom Nutzer genannte eigenständige Programm „Datengenerator“ wurde bei der Suche noch nicht gefunden. Eine Anbindung oder automatische Adresszuordnung ist deshalb noch nicht eingerichtet. Dafür wird dessen konkrete Datei, Verknüpfung oder API benötigt.
