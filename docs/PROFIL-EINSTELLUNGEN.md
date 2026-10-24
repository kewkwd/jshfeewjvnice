# Person und Eingabetempo je Profil

Unter **Profile → Profil vorbereiten** legst du zuerst eine Profilkennung, Vorname, Nachname und Geschlecht fest. Wohnort und Geburtsdatum gehören zum selben Datensatz. Ein Geburtsdatum wird vor Registrierungen verlangt, deren Formular es benötigt. Unter **Person und Eingabetempo** kannst du vorhandene Einträge bearbeiten. Bei der Kontenregistrierung wählst du die vorbereitete Kennung.

Jedes Profil speichert seine Werte unabhängig: Mausgeschwindigkeit in Pixeln pro Sekunde, Pause vor Klicks in Millisekunden und Tippgeschwindigkeit in Zeichen pro Sekunde. Die Anfangswerte sind 900 px/s, 120 ms und 5 Zeichen/s. Änderungen gelten ab dem nächsten Auftrag. Profile mit unterschiedlichen Anforderungen kannst du getrennt einstellen; es gibt keine wechselnden Zufallswerte. Die Werte werden in `data/profile_settings/<Profilkennung>.json` gespeichert, abhängig vom konfigurierten Profilverzeichnis. Personendaten erhalten denselben lokalen Dateischutz wie Zugangsdaten.

Die gemeinsamen nativen Formularaktionen von Chrome und Firefox verwenden diese Werte. Eingaben bleiben zeichengetreu und abbrechbar. Strukturierte Datumsfelder bekommen einen vollständigen nativen Wert. Android-Registrierungen verwenden die Tippgeschwindigkeit und Pause vor Berührungen; einen Mauszeiger gibt es bei diesen ADB-Berührungen nicht. Die Pause verlängert keinen Tastendruck und erzeugt keinen langen Fingerdruck. Browser- und ADB-Aufwand kann Eingaben zusätzlich verlangsamen. Ein Klick oder eine teilweise Texteingabe mit unklarem Ausgang wird nicht automatisch erneut ausgelöst.

Postfachregistrierungen übernehmen vor dem Browserstart die ausdrücklich gespeicherten Angaben des gewählten Anbieters in ihr eigenes Profil. **Postfächer → Registrierungsdaten** gilt jetzt für WEB.DE, GMX, Gmail und Outlook. Anrede und Geschlecht sind getrennte Angaben. Die frühere globale Zusatzpause ist für neue Registrierungen durch das Profiltempo ersetzt. Bestätigte Postfächer mit noch offener Mailprüfung setzen weiterhin ausschließlich die Mailprüfung fort.

## Proxy und Standort prüfen

**Proxy aus Einstellungen prüfen** ist auch ohne Profilauswahl verfügbar. Beide Tests lassen sich nach jedem Abschluss oder Fehler erneut starten, auch während anderer Aufträge. Während einer laufenden Standortabfrage sind nur die beiden Testknöpfe kurz gesperrt. Ohne eingestellten Proxy erscheint eine verständliche Fehlermeldung. Bei mehreren Proxys bitte das gewünschte Profil mit fester Zuordnung auswählen.

Unter **Profile & Geräte** ein Profil auswählen und **Proxy und Standort prüfen** anklicken. Angezeigt werden öffentliche Ausgangs-IP, ungefährer Standort (Stadt, Region, Land), Internetanbieter und Abfragedauer. Das Ergebnis bleibt mit Zeitstempel am Profil gespeichert; ein IP-Wechsel gegenüber der letzten erfolgreichen Messung sowie ein abweichendes Profilland werden angezeigt.

Die HTTPS-Abfrage an `ipwho.is` läuft ausschließlich über den zugeordneten Profilproxy. Es gibt keinen Rückfall auf die direkte Verbindung. Bei mehreren konfigurierten Proxys ohne feste Zuordnung wird zuerst eine eindeutige Zuordnung verlangt. Es werden keine Profil-Personendaten oder Zugangsdaten an den Standortdienst gesendet. Fehler werden als Fehler angezeigt und ersetzen keinen fehlgeschlagenen Test durch einen alten Standort.

Die Stadt ist eine ungefähre IP-Ortung. Der Test misst seine eigene Verbindung; bei rotierenden Proxys kann eine bereits offene Browsersitzung eine andere Ausgangs-IP haben. Die Anzeige bestätigt weder eine genaue Geräteposition noch einen Ablehnungsgrund eines Anbieters.

## Vorbereitung für das spätere Datentool

Das Tool ist noch nicht verbunden. Der lokale Einstieg ist `socialscraper.profile_settings.import_person(profiles_dir, identity, payload, source=...)`. Ein Adapter übergibt ausschließlich die folgenden Felder als Text:

```json
{
  "vorname": "Test",
  "nachname": "Person",
  "wohnort": "Teststadt",
  "geschlecht": "keine Angabe",
  "geburtsdatum": "1996-02-29"
}
```

Dies sind ausdrücklich fiktive Beispieldaten. Zulässige Geschlechtswerte: `männlich`, `weiblich`, `divers`, `keine Angabe`. Es wird nichts aus Namen oder Anreden abgeleitet. Die Quelle wird am Profil vermerkt. Ungültige oder widersprüchliche Angaben werden vor dem Speichern abgelehnt; vorhandene Daten bleiben erhalten. Der Import löst weder einen Browserstart noch eine Registrierung aus.
