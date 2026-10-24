@echo off
cd /d "%~dp0"
rem Startet das Programmfenster ohne Konsole. Fehler beim Start zeigt ein Meldungsfenster.
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  powershell.exe -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('Die lokale Umgebung fehlt. Bitte zuerst Einrichten.cmd ausfuehren.','Profilwerk')"
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m socialscraper.gui
