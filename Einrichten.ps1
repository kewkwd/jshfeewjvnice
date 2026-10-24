[CmdletBinding()]
param(
    [switch]$NurPruefen,
    [switch]$OhnePause
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
# UTF-8 gilt ausschließlich für diesen gestarteten Prozess.
$taskUtf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $taskUtf8
$OutputEncoding = $taskUtf8
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $PSScriptRoot

function Invoke-Python {
    param([string]$Exe, [string[]]$Prefix = @(), [string[]]$Arguments)
    & $Exe @Prefix @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Der Python-Schritt konnte nicht abgeschlossen werden.' }
}

function Find-Chrome {
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    $command = Get-Command chrome.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

$success = $false
try {
    Write-Host ''
    Write-Host 'Profilwerk einrichten' -ForegroundColor Cyan
    Write-Host 'Die Einrichtung öffnet keine Accounts und benötigt keine Zugangsdaten.'

    if (-not (Find-Chrome)) { throw 'Google Chrome fehlt. Bitte Chrome installieren und die Einrichtung erneut starten.' }
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $node) { throw 'Node.js fehlt. Bitte Node.js ab Version 22 installieren und die Einrichtung erneut starten.' }
    $nodeMajor = & $node.Source -p "Number(process.versions.node.split('.')[0])"
    if ($LASTEXITCODE -ne 0 -or [int]$nodeMajor -lt 22) { throw 'Für YouTube wird Node.js ab Version 22 benötigt.' }

    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        if ($NurPruefen) { throw 'Die lokale Umgebung fehlt. Bitte Einrichten.cmd zuerst ohne Prüfoption starten.' }
        $bootstrap = Get-Command py.exe -ErrorAction SilentlyContinue
        $prefix = @('-3')
        if (-not $bootstrap) {
            $bootstrap = Get-Command python.exe -ErrorAction SilentlyContinue
            $prefix = @()
        }
        if (-not $bootstrap) { throw 'Python fehlt. Bitte Python ab Version 3.11 installieren und die Einrichtung erneut starten.' }
        Invoke-Python -Exe $bootstrap.Source -Prefix $prefix -Arguments @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)')
        Write-Host 'Lokale Umgebung wird angelegt ...'
        Invoke-Python -Exe $bootstrap.Source -Prefix $prefix -Arguments @('-m', 'venv', '.venv')
    }
    Invoke-Python -Exe $python -Arguments @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)')
    if (-not $NurPruefen) {
        $requirements = Join-Path $PSScriptRoot 'requirements-lock.txt'
        if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) { $requirements = Join-Path $PSScriptRoot 'requirements.txt' }
        Write-Host 'Benötigte Pakete werden geprüft und eingerichtet ...'
        $packageProxy = $env:HTTPS_PROXY
        if (-not $packageProxy) { $packageProxy = $env:ALL_PROXY }
        if (-not $packageProxy) {
            throw 'Paketabrufe benötigen einen ausdrücklich gesetzten HTTPS_PROXY oder ALL_PROXY. Kein Direktabruf gestartet. Für die lokale Prüfung -NurPruefen verwenden.'
        }
        $proxyUri = $null
        if (-not [Uri]::TryCreate($packageProxy, [UriKind]::Absolute, [ref]$proxyUri) -or
            $proxyUri.Scheme -notin @('http', 'https', 'socks5h') -or -not $proxyUri.Host) {
            throw 'Paketproxy ungültig. Kein Direktabruf gestartet.'
        }
        $previousNoProxy = $env:NO_PROXY
        try {
            # Explicit pip proxy wins over config; empty bypass and wheels only
            # prevent direct index bypass and network-capable source builds.
            $env:NO_PROXY = ''
            Invoke-Python -Exe $python -Arguments @('-m', 'pip', '--isolated', 'install', '--proxy', $packageProxy,
                '--only-binary=:all:', '--disable-pip-version-check', '-r', $requirements)
        }
        finally { $env:NO_PROXY = $previousNoProxy }
    }
    Invoke-Python -Exe $python -Arguments @('build/repair_environment.py')
    Invoke-Python -Exe $python -Arguments @('-m', 'pip', 'check')
    Invoke-Python -Exe $python -Arguments @('-c', "import yt_dlp, curl_cffi, instaloader, patchright, rebrowser_playwright, nodriver, requests; from socialscraper.config import load; from socialscraper.adapters.youtube import youtube_runtime_options; load(); youtube_runtime_options(); print('Pakete und Einstellungen sind verfügbar.')")

    Invoke-Python -Exe $python -Arguments @('-c', "import tkinter; print('Fenster-Bibliothek (Tkinter) ist verfügbar.')")

    if (-not $NurPruefen) {
        $data = Join-Path $PSScriptRoot 'data'
        if (-not (Test-Path -LiteralPath $data -PathType Container)) { New-Item -ItemType Directory -Path $data | Out-Null }
        $credentials = Join-Path $data 'credentials.json'
        if (-not (Test-Path -LiteralPath $credentials)) {
            # Vorlage kopieren; fehlt sie, eine leere Vorlage mit denselben Schlüsseln schreiben. Nie abbrechen.
            try {
                $vorlage = Join-Path $PSScriptRoot 'credentials.example.json'
                if (Test-Path -LiteralPath $vorlage -PathType Leaf) {
                    [System.IO.File]::Copy($vorlage, $credentials, $false)
                }
                else {
                    Write-Host 'Hinweis: credentials.example.json fehlt, schreibe eine leere Vorlage nach data\credentials.json.' -ForegroundColor Yellow
                    $leer = '{' + "`n" + '  "tiktok": { "tiktok_main": { "user": "", "password": "" } },' + "`n" + '  "instagram": { "instagram_main": { "user": "", "password": "" } }' + "`n" + '}' + "`n"
                    [System.IO.File]::WriteAllText($credentials, $leer, (New-Object System.Text.UTF8Encoding $false))
                }
            }
            catch {
                Write-Host ('Hinweis: data\credentials.json konnte nicht angelegt werden (' + $_.Exception.Message + '). Das ist nicht schlimm, die Einrichtung läuft weiter.') -ForegroundColor Yellow
            }
        }
    }
    Write-Host ''
    # Startprogramm Profilwerk.exe: bauen (oder bei -NurPruefen nur prüfen). Fehler hier sind nicht fatal.
    $buildExe = Join-Path $PSScriptRoot 'build\Build-Exe.ps1'
    $exePfad = Join-Path $PSScriptRoot 'Profilwerk.exe'
    $exeOk = $false
    try {
        if (-not (Test-Path -LiteralPath $buildExe)) { throw 'build\Build-Exe.ps1 fehlt.' }
        if ($NurPruefen) {
            if (Test-Path -LiteralPath $exePfad -PathType Leaf) { & $buildExe -NurPruefen; $exeOk = $true }
            else { Write-Host 'Profilwerk.exe ist noch nicht gebaut (Einrichten.cmd ohne Prüfoption baut sie).' -ForegroundColor Yellow }
        }
        else { & $buildExe; $exeOk = $true }
    }
    catch {
        Write-Host ('Profilwerk.exe nicht verfügbar: ' + $_.Exception.Message) -ForegroundColor Yellow
        Write-Host 'Das Programm startet trotzdem über die Verknüpfung (Rückfall) oder Profilwerk.cmd.' -ForegroundColor Yellow
    }
    if (-not $NurPruefen) {
        try { & (Join-Path $PSScriptRoot 'Desktop-Verknuepfung.ps1') }
        catch { Write-Host ('Desktop-Verknüpfung nicht angelegt: ' + $_.Exception.Message) -ForegroundColor Yellow }
    }
    if ($exeOk -and -not $NurPruefen) {
        Write-Host 'Einrichtung bereit. Programm: Verknüpfung „Profilwerk“ auf dem Desktop (startet Profilwerk.exe).' -ForegroundColor Green
    }
    else {
        Write-Host 'Einrichtung bereit. Programm: Verknüpfung „Profilwerk“ auf dem Desktop (oder Profilwerk.exe / Profilwerk.cmd).' -ForegroundColor Green
    }
    Write-Host 'Das Textmenü gibt es weiterhin über Starten.cmd.'
    Write-Host 'Zugangsdaten können später lokal in data\credentials.json eingetragen werden.'
    $success = $true
}
catch {
    Write-Host ''
    Write-Host ('Einrichtung angehalten: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Nach der Korrektur Einrichten.cmd erneut starten. Vorhandene Daten bleiben erhalten.'
}
finally {
    if (-not $OhnePause) { [void](Read-Host 'Zum Schließen Enter drücken') }
}
if ($success) { exit 0 }
exit 1



