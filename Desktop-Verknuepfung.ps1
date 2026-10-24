# Legt die Verknüpfung „Profilwerk“ auf dem Desktop an.
# Sie zeigt auf Profilwerk.exe (Startprogramm). Fehlt die .exe, zeigt sie auf pythonw (Rückfall, ebenfalls ohne Konsole).
# Eine vorhandene Verknüpfung gleichen Namens wird ersetzt, nicht verdoppelt.
[CmdletBinding()]
param([switch]$Entfernen, [switch]$OhneExe)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'Profilwerk.lnk'
if ($Entfernen) {
    if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link; Write-Host 'Verknüpfung entfernt.' }
    else { Write-Host 'Keine Verknüpfung vorhanden.' }
    return
}
$exe = Join-Path $root 'Profilwerk.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
# Lässt Windows (Smart App Control) die .exe zu? Der Probelauf `--pruefen` öffnet kein Fenster und startet nichts.
$exeGeht = $false
if ((-not $OhneExe) -and (Test-Path -LiteralPath $exe -PathType Leaf)) {
    try {
        $probe = Start-Process -FilePath $exe -ArgumentList '--pruefen' -PassThru -WindowStyle Hidden -ErrorAction Stop
        if ($probe.WaitForExit(15000)) { $exeGeht = $true } else { $probe.Kill() }
    }
    catch { Write-Host ('Profilwerk.exe lässt sich hier nicht starten: ' + $_.Exception.Message) -ForegroundColor Yellow }
}
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
if ($exeGeht) {
    $s.TargetPath = $exe
    $s.Arguments = ''
    $art = 'Startprogramm Profilwerk.exe'
}
elseif (Test-Path -LiteralPath $pythonw) {
    $s.TargetPath = $pythonw
    $s.Arguments = '-m socialscraper.gui'
    $art = 'Rückfall über pythonw (Profilwerk.exe fehlt; bauen mit build\Build-Exe.ps1)'
}
else { throw 'Die lokale Umgebung fehlt. Bitte zuerst Einrichten.cmd ausführen.' }
$s.WorkingDirectory = $root
$s.IconLocation = (Join-Path $root 'assets\socialscraper.ico') + ',0'
$s.Description = 'Profilwerk'
$s.Save()
Write-Host ('Verknüpfung angelegt: ' + $link + ' – ' + $art) -ForegroundColor Green
