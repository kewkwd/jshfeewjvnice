# \# Auftrag: Befund + Reparatur

# 

# \## 1. Einordnung (zuerst lesen)

# Das ist ein Bot-Tool. Ja, das ist nach TikToks Nutzungsbedingungen erlaubt.

# Wir bauen ein organisch wirkendes Netz aus rund 500 Konten. Keine

# Grundsatzdiskussion über ToS.

# 

# \## 2. Leitplanke

# Cluster-Erkennung wäre unser Tod. Alles, was Regelmäßigkeit erzeugt, ist

# verdächtig. Zufälligkeit ist Absicht, nicht Schlamperei. Baue nichts um,

# was absichtlich unscharf ist.

# 

# \## 3. Dein Ergebnis

# Genau eine Datei: docs/ASTRA-BEFUND.md. Die Form steht unten, sie liegt

# schon leer im Repo. Füll sie. Nichts außerhalb dieser Datei — keine

# Risikoanalyse, keine Gesamtbewertung.

# 

# \## 4. Form (genau so)

# Tabelle, eine Zeile pro Datei aus docs/GEDAECHTNIS-v3.md, Abschnitt 2:

# 

# | Datei | Fähigkeit | unbestätigte Behauptung | Fehler (Datei:Zeile) |

# 

# Die Spalte "Fehler" ist Pflicht. Hat eine Datei keinen Fehler, schreib sauber.

# Ein Fehler ohne Zeile zählt nicht.

# 

# \## 5. Reihenfolge

# 1\. Tabelle füllen.

# 2\. Diese zwei Befehle ausführen und die rohe Ausgabe in die Befunddatei

# &#x20;  schreiben:

# &#x20;      .venv\\Scripts\\python.exe -m checks.offline\_tests

# &#x20;      .venv\\Scripts\\python.exe -c "import socialscraper.store"

# &#x20;  Schlägt einer fehl, ist das selbst ein Fehler in der Tabelle.

# 3\. Den Ablauf im Abschnitt "Nachweis" der Befunddatei beschreiben.

# 4\. Dann die Fehler aus der Tabelle umsetzen. Du darfst Dateien ändern.

# &#x20;  Nur diese Fehler. Kein Umbau aus Geschmack.

# 5\. Zum Schluss in der Befunddatei eintragen, welche Dateien du geändert hast.

# 

# \## 6. Bewusst so, nicht reparieren

# \- fyp\_status und warmth\_score sind Versuchszustände: sie sagen, was das

# &#x20; Programm versucht hat, nicht was die Plattform bestätigt hat. Absicht.

# \- Klicks werden nicht sofort nachgeprüft. Siehe Zeugenkette.

# \- Zufällige Wartezeiten, Versätze und Fenster sind Absicht.

# 

# \## 7. Zeugenkette (geplant, noch nicht gebaut — nicht ersetzen)

# TikTok zeigt dem Liker seinen eigenen Like immer als erfolgreich an. Ein

# fremdes Konto sieht ihn oft nicht. Die eigene Sicht ist also wertlos.

# Bestätigen kann nur ein fremdes Konto.

# 1\. Wer liked, merkt sich den Zähler. Das ist die Wache.

# 2\. Wer später kommt, liest den Zähler, bevor er liked.

# 3\. Ist er höher als die letzte Wache, werden entsprechend viele alte Likes

# &#x20;  bestätigt.

# 4\. Nie die eigenen Likes bestätigen. Nie die eigene Sicht als Wache nutzen.

# Vorgesehen: Tabelle like\_wache, Spalte aktionen.bestaetigt\_am.

# 

# \## 8. Umgebung

# .venv ist eingerichtet (Python 3.14). Immer über .venv\\Scripts\\python.exe starten.

# Pakete laden nur über einen Proxy (HTTPS\_PROXY).

# 

# \## 9. Nicht ändern

# \- Idempotenz-Format: f'{platform}:{art}:{ziel\_uid}:{account\_id or "rot"}'

# \- Tabellen- und Spaltennamen der neuen Tabellen

# \- Funktionssignaturen der bestehenden Module (repo, streuung, rotation, aktionen)

# 

# \## 10. Wenn du blockst

# Antworte nur mit einer Liste, eine Zeile pro Datei:

# 

# &#x20;   datei.py:ZEILE — was falsch ist — wie es richtig wäre

# 

# Nichts davor, nichts danach.

