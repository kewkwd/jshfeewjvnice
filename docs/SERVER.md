# Socialscraper auf einem Server betreiben

Ziel: Der Hintergrunddienst läuft rund um die Uhr auf einem gemieteten Linux-Server, ohne Fenster. Deine Daten landen in einer Cloud-Datenbank, aus der PULSE und dein PC lesen.

> **Wichtig, ehrlich vorab:** Alles in diesem Dokument – `Dockerfile`, `deploy\docker-compose.yml`, `deploy\socialscraper.service` – ist **nicht ausgeführt und ungeprüft**. Auf dem Entwicklungsrechner war kein Docker vorhanden, ein Linux-Server stand nicht zur Verfügung. Die Abläufe sind sorgfältig geschrieben und die Dateien auf Syntax geprüft (die Compose-Datei ist gültiges YAML), aber ein erster echter Lauf kann Anpassungen brauchen. Plane dafür Zeit ein.

## Was du brauchst

- Einen Linux-Server (Ubuntu 22.04/24.04 oder Debian 12) mit **64-Bit Intel/AMD** (nicht ARM – Google Chrome gibt es für Linux nur dafür), mindestens **2 CPU-Kerne, 4 GB Arbeitsspeicher, 20 GB Platz**.
- Auf deinem PC: dieses Programm, eingerichtet und mit **angemeldeten Konten** (die Anmeldung geht nur dort, siehe Schritt 3).
- Optional, aber empfohlen: eine Cloud-Datenbank (Supabase), damit PULSE die Daten sieht (Schritt 6).

## Schritt 1: Server mieten

Ein kleiner „VPS“ oder „Root-Server“ genügt (zum Beispiel bei Hetzner, netcup, Contabo, DigitalOcean – keine Empfehlung, nur Beispiele). Beim Bestellen: Ubuntu 24.04, 4 GB RAM. Du bekommst eine IP-Adresse und ein Passwort oder einen SSH-Schlüssel per E-Mail.

Verbinden (Windows: Eingabeaufforderung oder PowerShell):

```
ssh root@DEINE-SERVER-IP
```

Dann einen eigenen Nutzer anlegen und mit ihm weiterarbeiten (nicht dauerhaft als root):

```
adduser betrieb
usermod -aG sudo betrieb
su - betrieb
```

## Schritt 2: Docker installieren und Programm hochladen

Docker installieren (offizielle Anleitung: docs.docker.com/engine/install/ubuntu). Danach prüfen:

```
docker --version
docker compose version
```

Programm hochladen. Auf deinem PC den Programmordner **ohne** die Ordner `.venv` und `data` und ohne `credentials.json` packen und per `scp` kopieren (Beispiel PowerShell; `.venv`/`data` sind schon in `.dockerignore`, hier nur zum Verkleinern):

```
scp -r C:\Pfad\zu\socialscraper-startbereit betrieb@DEINE-SERVER-IP:~/socialscraper
```

(Alternative: ein Programm wie WinSCP oder FileZilla zum Hochladen.) Danach auf dem Server:

```
cd ~/socialscraper
docker build -t socialscraper .
```

Das dauert einige Minuten (lädt Python, Chrome und Node.js).

## Schritt 3: Anmeldung lokal durchführen und Cookie-Dateien hochladen

TikTok, Instagram und X verlangen eine Anmeldung, oft mit Bestätigungen oder CAPTCHA (bei Reddit ist sie freiwillig). Das geht nur mit einem sichtbaren Fenster – **also an deinem PC**, nicht auf dem Server.

1. Auf dem PC im Programmfenster unter **Konten** anmelden (wie bisher). Die Anmeldung wird in `data\cookies\<Konto>.<Plattform>.json` gespeichert, zum Beispiel `data\cookies\tiktok_main.tiktok.json`.
2. Diese Datei(en) auf den Server hochladen, in den Ordner `~/socialscraper/deploy/daten/hochgeladen/` (anlegen: `mkdir -p ~/socialscraper/deploy/daten/hochgeladen`):

```
scp data\cookies\tiktok_main.tiktok.json betrieb@DEINE-SERVER-IP:~/socialscraper/deploy/daten/hochgeladen/
```

3. Auf dem Server übernehmen (der Befehl heißt `import-cookies`; `--identity` ist der Kontoname, `--platform` tiktok, instagram, reddit oder x). Einmalig vorher, sonst scheitert der Befehl: `server.env` anlegen (Docker Compose verlangt die Datei, siehe Schritt 4) und den Datenordner dem Dienst-Nutzer geben (im Container läuft der Dienst als Nutzer 10001):

```
cd ~/socialscraper/deploy
cp -n server.env.example server.env
sudo chown -R 10001:10001 daten
docker compose run --rm socialscraper import-cookies --platform tiktok --identity tiktok_main --file /data/hochgeladen/tiktok_main.tiktok.json
```

4. Danach die hochgeladene Datei vom Server löschen (sie ist wie ein Passwort): `sudo rm daten/hochgeladen/*.json`. Für ein späteres erneutes Hochladen gehört der Ordner dem Dienst-Nutzer; vorher `sudo chmod 1777 daten/hochgeladen` ausführen, dann klappt `scp` wieder.

Cookie-Dateien sind **wie Passwörter**. Nie weitergeben, nie in einen Chat oder ins Internet stellen. Sie laufen nach einiger Zeit ab; dann die Anmeldung am PC erneuern und die Datei neu hochladen.

## Schritt 4: Einstellungen und Konten auf den Server bringen

Die Ziele der Automatik (Hashtags, Suchbegriffe, Profile, bekannte Videos), Konten und Gruppen stehen in der Datenbank `data\scrape.db`. Zwei Wege:

- **Einfach:** Nimm die Datenbank vom PC mit: `scrape.db` nach `~/socialscraper/deploy/daten/` kopieren (Dienst vorher stoppen, siehe Schritt 5). Dann hat der Server dieselben Konten, Gruppen und Ziele.
- **Sauber getrennt:** Auf dem Server mit leerer Datenbank starten und Ziele über den PC-Ablauf definieren – das Fenster gibt es auf dem Server nicht; deshalb ist der einfache Weg meist besser.

### Geheimnis-Dateien beim Umzug mitnehmen

Neben der Datenbank liegen im Ordner `data` Dateien mit **Geheimnissen**. Sie gehören zu deinem Umzug, wenn du die entsprechende Funktion auch auf dem Server nutzen willst – und sie sind wie Passwörter zu behandeln (nur per `scp` über deine eigene Verbindung, nie in Chats, nie ins Internet, nach dem Kopieren keine Reste im Hochlade-Ordner lassen):

- `data\cookies\*.json` – die Anmeldungen (Schritt 3, besser über `import-cookies`).
- `data\account_proxies.json` – **feste Proxys je Konto, mit Benutzername und Passwort, unverschlüsselt**. Ohne diese Datei ruft der Server für Konten, die einen festen Proxy haben, absichtlich nicht ab (er weicht nie auf den Direktweg aus).
- `data\ai.json` – Schlüssel und Einstellungen der KI-Stimmung (alternativ die Umgebungsvariable `ANTHROPIC_API_KEY`).
- `data\integration.json` – Token der lokalen Schnittstelle und Zugang zur Cloud-Datenbank (Schritt 6).
- `deploy/server.env` – Proxys und weitere Einstellungen für den Server.
- `settings.toml` (im Programmordner) – enthält im Modus „Eigene Proxy-Liste“ die Proxys **mit Benutzername und Passwort**. Sie wird deshalb nicht ins Docker-Image kopiert (`.dockerignore`); auf dem Server gelten die Werte aus `deploy/server.env` (`SS_…`-Variablen).

Mitnehmen heißt: nur das, was du dort wirklich brauchst. Lass auf dem Server alles weg, was er nicht benutzt.

### Rotation, Kanäle und Tempo-Profile auf dem Server

- **Rotation:** Mehrere Konten je Plattform werden abwechselnd benutzt (Einstellung „Konto wechseln nach … Videos“, `SS_ROTATION_EVERY`). Bei CAPTCHA, Sperre oder abgelaufener Anmeldung kommt ein Konto in eine **Abkühlung** (Stunden); der Dienst nimmt dann das nächste. Sind alle Konten in Abkühlung, wartet das Ziel bis zum frühesten Zeitpunkt – am Server bedeutet das: Anmeldung am PC erneuern und die Cookie-Datei neu hochladen.
- **Kanäle:** Ein ganzer Kanal wird nicht auf einmal abgerufen. Der Dienst sammelt zuerst die Liste der Videos und arbeitet sie danach in **Paketen** ab (neueste zuerst), mit Pausen, einer Tagesgrenze je Konto und Plattform und optionalen Ruhezeiten (zum Beispiel 23–7 Uhr). Zwischen zwei Paketen kommen andere Ziele dran. Danach beobachtet er den Kanal auf neue Videos. Wie weit ein Kanal ist, siehst du im Fenster (Automatik → Kanäle) oder mit `python -m socialscraper channel list`.
- **Tempo-Profile:** *Vorsichtig* (8 Videos pro Lauf, 60 pro Tag und Konto), *Normal* (20 / 200), *Schnell* (50 / 600, auffälliger). Das ist Rücksicht, kein Schutz: Auf einer Server-Adresse bleibt *Vorsichtig* die bessere Wahl.

### Reddit und X auf dem Server

- **Reddit** geht ohne Anmeldung, mit Anmeldung (Cookies) seltener in Sperren. **X braucht ein Konto** (nur ein einzelner Beitrag ohne Antworten geht ohne). Die Anmeldung für X (und Reddit) machst du **am PC** (sichtbares Fenster) und lädst die Cookie-Datei hoch, wie in Schritt 3.
- `import-cookies` kennt beide Plattformen: `--platform reddit` bzw. `--platform x` (Schlüssel-Cookies: `reddit_session` oder `token_v2` bei Reddit, `auth_token` und `ct0` bei X; Domain x.com oder twitter.com). Beispiel: `docker compose run --rm socialscraper import-cookies --platform x --identity x_main --file /data/hochgeladen/x_main.x.json`.
- X wird im Proxy-Modus `frei` und `tor` immer abgelehnt.

Weitere Einstellungen (Proxy, Kommentarzahl) in `deploy/server.env` (Vorlage `deploy/server.env.example`):

```
cd ~/socialscraper/deploy
cp server.env.example server.env
nano server.env
```

## Schritt 5: Hintergrunddienst starten

```
cd ~/socialscraper/deploy
mkdir -p daten
sudo chown -R 10001:10001 daten
docker compose up -d --build
```

Prüfen:

```
docker compose ps                  # Zustand "running" bzw. "healthy"
docker compose logs -f             # Protokoll (Strg+C beendet nur die Anzeige)
```

Der Dienst startet nach einem Neustart des Servers von selbst (`restart: unless-stopped`). Anhalten: `docker compose stop`. Neue Programmversion: Ordner neu hochladen, `docker compose up -d --build`.

Der Befehl hinter dem Dienst ist `python -m socialscraper daemon`. Es läuft immer nur ein Browserauftrag zugleich (eine Sperrdatei in `data\run.lock`).

**Ohne Docker (systemd):** Beispiel in `deploy/socialscraper.service`; Voraussetzungen dort im Kopf der Datei (Python 3.11+, Google Chrome, Node.js 22+, eigener Nutzer). Ebenfalls ungeprüft.

## Schritt 6: Cloud-Datenbank als Treffpunkt

Damit PULSE und dein PC die Daten sehen, ohne dass sie bei dir zu Hause liegen müssen, überträgt der Dienst nach jedem Lauf die Daten in eine Cloud-Datenbank (Supabase). Wie das eingerichtet wird, beschreibt **`docs/PULSE-Anbindung.md`** (kommt von der Schnittstellen-Arbeit; die Tabellen legt `deploy/supabase_schema.sql` an). Kurzfassung:

1. Supabase-Konto anlegen, Projekt erstellen, `deploy/supabase_schema.sql` im SQL-Editor ausführen.
2. Adresse und Schlüssel des Projekts in `data/integration.json` eintragen (Abschnitt `sync`: `kind` = `supabase`, `url`, `key`). **Diese Datei enthält ein Geheimnis**; sie gehört nur auf deinen PC und den Server.
3. Prüfen: `docker compose run --rm socialscraper sync test`. Manuell senden: `… sync push`, holen: `… sync pull`.

Auf dem PC kannst du dieselbe Datenbank im Fenster per „Aus Cloud aktualisieren“ einmischen. Der Server-Dienst stößt den Abgleich nach jedem Lauf selbst an, wenn „auto“ in `integration.json` gesetzt ist.

Ungeprüft: Die Cloud-Datenbank konnte ohne Konto nicht live getestet werden.

## Grenzen – bitte lesen

- **TikTok, Instagram und Reddit sperren Server-Adressen häufiger** als private Anschlüsse (X ist dort nicht geprüft). Rechenzentrums-Adressen werden schneller verlangsamt, mit CAPTCHA belegt oder blockiert. Erwarte mehr Fehlversuche als am PC.
- **Tor:** Das mitgelieferte Tor (`tor.exe` im Programmordner) ist ein **Windows-Programm** und läuft auf dem Linux-Server **nicht**. Wer auf Linux den Modus `tor` nutzen will, installiert ein eigenes Tor (zum Beispiel `sudo apt install tor`, lauscht normalerweise auf `127.0.0.1:9050`) und stellt `proxy_mode` (`SS_PROXY_MODE=tor`) darauf ein; im Docker-Container muss der Tor-Dienst vom Container aus erreichbar sein. Ungeprüft. Konten, Rotation und feste Proxys sind im Modus `tor`/`frei` ohnehin gesperrt, X ebenfalls.
- **Proxy:** Hilft ein sogenannter Wohn-Proxy („residential“) eines Anbieters. In `deploy/server.env`: `SS_PROXIES=http://BENUTZER:PASSWORT@HOST:PORT` (nur mit deinen echten Zugangsdaten füllen, nie ins Protokoll kopieren). Ob ein bestimmter Anbieter bei TikTok/Instagram funktioniert, ist ungeprüft.
- **CAPTCHA** lassen sich auf dem Server nicht lösen. Läuft ein Konto ins CAPTCHA, am PC anmelden/lösen und die Cookie-Datei neu hochladen.
- **Instagram:** Anmeldungen von einer neuen Adresse lösen oft Sicherheitsabfragen aus. Konto am besten zuerst vom PC aus nutzen, dann erst hochladen.
- **YouTube** geht ohne Anmeldung und ist am wenigsten betroffen.
- Mehrere Konten gleichzeitig vom Server aus anzumelden fällt Plattformen eher auf als gelegentliche Abrufe. Lieber wenige Ziele, längere Intervalle.
- **Datensicherung:** Alles Wichtige liegt in `deploy/daten/` (Datenbank, Cookies, Browserprofile, Protokolle). Dieser Ordner gehört in deine Sicherung. Die Cloud-Datenbank ist eine zweite Kopie der Ergebnisse, nicht der Anmeldungen.
- **Sicherheit:** Die lokale Schnittstelle für PULSE (`127.0.0.1:8765`) wird vom Container **nicht** nach außen geöffnet. Nicht ohne Not ändern. Bleib bei der Cloud-Datenbank.

## Wo im Programm Windows angenommen wird (Stand der Prüfung des Codes)

Für den Hintergrunddienst auf Linux sind das **keine Hindernisse** (alle mit Betriebssystem-Weiche `os.name`, ungeprüft auf echtem Linux):

- Sperrdatei: Windows `msvcrt`, sonst `fcntl` (`socialscraper/runlock.py`).
- Dienst starten/beenden: Windows-Flags und `pythonw` nur unter Windows (`socialscraper/daemon.py`); unter Linux `start_new_session`.
- Fenster-Teile (`gui.py`, `ui/`, `guicore.py` mit `taskkill`) werden vom Dienst nicht benutzt.

Echte Einschränkungen für den Server:

- `login` öffnet absichtlich ein **sichtbares** Browserfenster (`headless=False` in `socialscraper/login.py`). Auf dem Server ohne Bildschirm geht das nicht; deshalb Anmeldung am PC und `import-cookies` (Schritt 3). Nicht auf dem Server `login` ausführen.
- Die Browser-Tarnung gibt sich als **Windows-Chrome** aus (`net/fingerprint.py`: Windows-User-Agent, `Win32`). Auf Linux stimmt das nicht mit dem echten System überein (Schriften, Grafik); das kann Erkennung begünstigen. Ungeprüft, ob es in der Praxis stört.
- Chrome wird über `channel="chrome"` gestartet (Patchright). Das braucht auf Linux das Google-Chrome-Paket (das macht das Dockerfile) und läuft nur auf amd64.
- Nodriver-Engine (`--engine nodriver`) wurde nur unter Windows geprüft; der Standard ist Patchright.
- Die Systemprüfung im Fenster sucht Chrome nur an Windows-Pfaden – betrifft nur das Fenster.
