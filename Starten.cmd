@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Starten.ps1" %*
exit /b %errorlevel%
