# Die .exe – was es gibt und was geprüft wurde

Stand 11.10.2026. Geprüft auf dem Entwicklungsrechner (Windows 11, Smart App Control im Zustand „erzwingen“).

## Hauptlösung: Startprogramm `Socialscraper.exe`

Eine kleine echte .exe (C#, nur .NET Framework 4, gebaut mit dem Compiler, der in Windows steckt). Sie ist **kein** Bündel des ganzen Programms, sondern der Starter:

- Quelle `launcher\Socialscraper.cs`, Manifest `launcher\Socialscraper.manifest` (DPI-scharf, kein Administrator), Symbol `assets\socialscraper.ico`, Versionsinformationen eingebaut.
- Bauen: `build\Build-Exe.ps1` (macht auch `Einrichten.cmd`). Mit `-Pruefen` wird die .exe danach wirklich gestartet und geprüft, dass das Fenster „Socialscraper“ erscheint.
- Start: Ordner der .exe, sonst der Pfad aus `%LOCALAPPDATA%\Socialscraper\ort.txt` (schreibt der Build) – so darf die .exe auch auf den Desktop kopiert werden. Findet sie nichts, fragt sie nach dem Ordner und merkt ihn sich.
- Verhalten: `.venv\Scripts\pythonw.exe -m socialscraper.gui`, Arbeitsordner = Programmordner, ohne Konsolenfenster, wartet nicht. Fehlt die Umgebung: Meldungsfenster mit „Einrichten starten“ (startet `Einrichten.cmd`). Zweiter Start: holt das vorhandene Fenster nach vorn.
- `Socialscraper.exe --dienst` startet den Hintergrunddienst (`python -m socialscraper daemon`) unsichtbar, ohne Meldungsfenster (für Autostart).
- Sie ist nicht signiert. Smart App Control hat sie auf diesem Rechner nicht blockiert (lokal gebaut). Auf einem anderen Rechner kann das anders sein; dann Einrichten.cmd dort ausführen (baut die .exe lokal) oder `Socialscraper.cmd` nehmen.

## Zusatzfrage: eine einzelne .exe mit allem (PyInstaller) – Befund

Versuch in einer Wegwerf-Umgebung außerhalb des Projekts (PyInstaller 6.22.3, Python 3.14.7, Pakete aus `requirements-lock.txt`), Ordner-Bündel (`--onedir`):

| Prüfung | Ergebnis |
|---|---|
| Bündel bauen | gelang, 157 MB, ca. 80 s |
| Bündel starten (`--help`) | läuft, Beendigungscode 0 |
| `cffi`, `greenlet`, `curl_cffi` (Sitzung angelegt), `yt_dlp`, `websockets` im Bündel | laufen; keine Blockade durch Smart App Control |
| Patchright im Bündel: Treiber starten, echtes Chrome unsichtbar öffnen, Seite setzen, schließen | läuft (Chrome 154) |
| Fenster-Programm (Tkinter) im Bündel | nicht geprüft |
| Einzeldatei (`--onefile`) | nicht geprüft |

Warum es trotzdem **nicht** als Option ausgeliefert wird (`build\Build-Bundle.ps1` gibt es deshalb nicht):

1. Das Fenster startet seine Abrufe als eigenen Prozess mit `python -m socialscraper …` (`socialscraper/guicore.py`). Eine gebündelte .exe versteht `-m` nicht; das Fenster würde im Bündel keine Abrufe starten können. Der Hintergrunddienst (`daemon.py`) kennt den Fall „gepackt“, das Fenster nicht. Dafür müsste die Python-Seite geändert werden – nicht Teil dieses Auftrags.
2. Smart App Control entscheidet pro Datei nach Ruf. Auf diesem Rechner wurde bereits eine einzelne Paketdatei blockiert (pycryptodomex, siehe BUGFIXES.md), andere gleich unsignierte liefen. Dass das Bündel hier läuft, sagt nichts über andere Rechner. Ungeprüft und nicht zu prüfen.
3. 157 MB mit jeder Paketänderung neu, Chrome und Node.js müssen trotzdem separat installiert sein.

Wer ein Bündel nur für Kommandozeile/Dienst braucht: `pyinstaller --onedir --collect-all patchright --collect-all curl_cffi --collect-all yt_dlp --collect-all certifi --paths . Einstieg.py` mit einem Einstieg `from socialscraper.cli import main; raise SystemExit(main())`. Das ist nur der gelaufene Versuch, kein unterstützter Weg.
