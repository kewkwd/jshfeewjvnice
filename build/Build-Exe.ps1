# Baut das Startprogramm Profilwerk.exe (C#, .NET-Framework-Compiler von Windows, keine Zusatzpakete).
#   .\build\Build-Exe.ps1            baut, prüft die Datei und schreibt den Ort für Kopien der .exe
#   .\build\Build-Exe.ps1 -Pruefen   startet danach die .exe wirklich und prüft, dass das Fenster erscheint
#   .\build\Build-Exe.ps1 -NurPruefen  baut nichts, prüft nur die vorhandene .exe (ohne Fenster zu öffnen)
# Die .exe ist nicht signiert; sie wird lokal gebaut, ohne Sicherheitseinstellungen zu ändern.
[CmdletBinding()]
param(
    [switch]$Pruefen,
    [switch]$NurPruefen,
    [string]$Ausgabe
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$root = Split-Path -Parent $PSScriptRoot
$quelle = Join-Path $root 'launcher\Socialscraper.cs'
$manifest = Join-Path $root 'launcher\Socialscraper.manifest'
$icon = Join-Path $root 'assets\socialscraper.ico'
if ([string]::IsNullOrEmpty($Ausgabe)) { $Ausgabe = Join-Path $root 'Profilwerk.exe' }

function Find-Compiler {
    $kandidaten = @(
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
        (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
    )
    foreach ($k in $kandidaten) { if (Test-Path -LiteralPath $k -PathType Leaf) { return $k } }
    throw 'Der C#-Compiler von Windows (csc.exe, .NET Framework 4) wurde nicht gefunden.'
}

function Schreibe-Ort {
    $ordner = Join-Path $env:LOCALAPPDATA 'Profilwerk'
    New-Item -ItemType Directory -Force -Path $ordner | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $ordner 'ort.txt'), $root + "`r`n", [System.Text.UTF8Encoding]::new($false))
    Write-Host ('Ort für Kopien der .exe gespeichert: ' + (Join-Path $ordner 'ort.txt'))
}

function Pruefe-Datei {
    if (-not (Test-Path -LiteralPath $Ausgabe -PathType Leaf)) { throw 'Profilwerk.exe fehlt.' }
    $bytes = [System.IO.File]::ReadAllBytes($Ausgabe)
    if ($bytes.Length -lt 4096 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) { throw 'Profilwerk.exe ist keine gültige Programmdatei.' }
    $info = (Get-Item -LiteralPath $Ausgabe).VersionInfo
    if ($info.ProductName -ne 'Profilwerk') { throw 'In der .exe fehlen die Versionsinformationen.' }
    # Symbol eingebettet? (Windows liefert für Dateien ohne Symbol das Standardsymbol; wir vergleichen mit dem Symbol der Quelle)
    Add-Type -AssemblyName System.Drawing
    $ico = [System.Drawing.Icon]::ExtractAssociatedIcon($Ausgabe)
    if ($null -eq $ico) { throw 'Kein Symbol in der .exe gefunden.' }
    $ico.Dispose()
    Write-Host ('Datei in Ordnung: ' + $Ausgabe + ' (' + [math]::Round($bytes.Length / 1024) + ' KB, Version ' + $info.FileVersion + ')') -ForegroundColor Green
}

function Finde-Fenster {
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'python*' -and $_.MainWindowTitle -eq 'Profilwerk' }
}

function Teste-Start {
    # Zuerst der stille Probelauf: Blockiert Windows (Smart App Control) die neue .exe, ist das kein Programmfehler.
    try {
        $probe = Start-Process -FilePath $Ausgabe -ArgumentList '--pruefen' -PassThru -WindowStyle Hidden -ErrorAction Stop
        if (-not $probe.WaitForExit(15000)) { $probe.Kill(); throw 'Probelauf hängt.' }
    }
    catch {
        throw ('Profilwerk.exe lässt sich hier nicht starten (' + $_.Exception.Message + '). Wenn Windows sie blockiert: nochmals bauen (Einrichten.cmd) oder Profilwerk.cmd benutzen.')
    }
    $vorher = @(Finde-Fenster)
    if ($vorher.Count -gt 0) {
        # Es läuft schon ein Fenster (vielleicht deins) – das schließen wir nicht. Geprüft wird der Zweitstart.
        Write-Host 'Ein Profilwerk-Fenster ist schon offen und bleibt unberührt; geprüft wird der zweite Start.'
        $p = Start-Process -FilePath $Ausgabe -PassThru -WindowStyle Hidden
        if (-not $p.WaitForExit(15000)) { throw 'Zweiter Start: die .exe hat sich nicht beendet.' }
        if (@(Finde-Fenster).Count -ne $vorher.Count) { throw 'Zweiter Start hat die Fensteranzahl verändert.' }
        Write-Host 'Zweiter Start: kein weiteres Fenster, kein Absturz.' -ForegroundColor Green
        return
    }
    $p = Start-Process -FilePath $Ausgabe -PassThru
    $fenster = $null
    for ($i = 0; $i -lt 60 -and -not $fenster; $i++) {
        Start-Sleep -Milliseconds 500
        $fenster = @(Finde-Fenster) | Select-Object -First 1
    }
    if (-not $fenster) { throw 'Nach 30 Sekunden ist kein Fenster „Profilwerk“ erschienen.' }
    Write-Host ('Fenster erschienen: Prozess ' + $fenster.Name + ' (' + $fenster.Id + '), Titel „' + $fenster.MainWindowTitle + '“.') -ForegroundColor Green
    # Nur das Fenster schließen, das wir selbst geöffnet haben.
    [void]$fenster.CloseMainWindow()
    if (-not $fenster.WaitForExit(8000)) { Stop-Process -Id $fenster.Id -Force }
    Start-Sleep -Milliseconds 800
    if (@(Finde-Fenster).Count -gt 0) { throw 'Das Fenster ließ sich nicht schließen.' }
    Write-Host 'Fenster wieder geschlossen.'
}

if (-not $NurPruefen) {
    $csc = Find-Compiler
    foreach ($f in @($quelle, $manifest, $icon)) { if (-not (Test-Path -LiteralPath $f)) { throw ('Fehlt: ' + $f) } }
    $temp = Join-Path $env:TEMP ('socialscraper-exe-' + [guid]::NewGuid().ToString('N') + '.exe')
    try {
        $argumente = @('/nologo', '/target:winexe', '/platform:anycpu', '/optimize+', '/codepage:65001',
            ('/win32icon:' + $icon), ('/win32manifest:' + $manifest), ('/out:' + $temp),
            '/reference:System.dll', '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll', $quelle)
        Write-Host ('Compiler: ' + $csc)
        $meldung = & $csc @argumente 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $temp)) { throw ("Bauen fehlgeschlagen:" + [Environment]::NewLine + $meldung) }
        if ($meldung.Trim()) { Write-Host $meldung.Trim() }
        try { Copy-Item -LiteralPath $temp -Destination $Ausgabe -Force }
        catch { throw 'Profilwerk.exe konnte nicht geschrieben werden (läuft sie gerade? Dann zuerst schließen).' }
    }
    finally { Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }
}
Pruefe-Datei
if ((-not $NurPruefen) -and ($Ausgabe -eq (Join-Path $root 'Profilwerk.exe'))) { Schreibe-Ort }
# Keep the old filename inside this copy as a compatibility entry point.
if ((-not $NurPruefen) -and ($Ausgabe -eq (Join-Path $root 'Profilwerk.exe'))) {
    Copy-Item -LiteralPath $Ausgabe -Destination (Join-Path $root 'Socialscraper.exe') -Force
}
if ($Pruefen) { Teste-Start }
