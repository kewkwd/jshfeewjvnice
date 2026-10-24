@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Einrichten.ps1" %*
exit /b %errorlevel%
