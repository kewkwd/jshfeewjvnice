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
$script:Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

function Get-Platform {
    Write-Host ''
    Write-Host '1  TikTok'
    Write-Host '2  Instagram'
    Write-Host '3  Reddit (Anmeldung freiwillig)'
    Write-Host '4  X (braucht ein Konto)'
    $choice = Read-Host 'Plattform wählen (Enter = zurück)'
    switch ($choice.Trim()) {
        '1' { return 'tiktok' }
        '2' { return 'instagram' }
        '3' { return 'reddit' }
        '4' { return 'x' }
        default { return $null }
    }
}

function Get-Identity {
    param([string]$Platform)
    switch ($Platform) {
        'tiktok' { return 'tiktok_main' }
        'instagram' { return 'instagram_main' }
        'reddit' { return 'reddit_main' }
        'x' { return 'x_main' }
        'youtube' { return 'youtube_main' }
        default { throw 'Die Plattform wird nicht unterstützt.' }
    }
}

function Get-LoginArguments {
    param([string]$Platform, [string]$Mode = 'qr')
    if ($Platform -notin @('tiktok', 'instagram', 'reddit', 'x')) { throw 'Anmeldung wird für diese Plattform nicht unterstützt.' }
    if ($Mode -notin @('qr', 'password')) { throw 'Die Anmeldemethode wird nicht unterstützt.' }
    if ($Mode -eq 'password' -and $Platform -in @('reddit', 'x')) { throw 'Bei Reddit und X meldest du dich im Browserfenster selbst an.' }
    return @('login', '--backend', 'local', '--platform', $Platform,
        '--identity', (Get-Identity $Platform), '--country', 'de', '--mode', $Mode, '--timeout', '600')
}

function Get-CheckArguments {
    param([string]$Platform)
    if ($Platform -notin @('tiktok', 'instagram', 'reddit', 'x')) { throw 'Anmeldestatus wird für diese Plattform nicht unterstützt.' }
    return @('check', '--backend', 'local', '--platform', $Platform,
        '--identity', (Get-Identity $Platform), '--country', 'de')
}

function Get-LogoutArguments {
    param([string]$Platform)
    if ($Platform -notin @('tiktok', 'instagram', 'reddit', 'x')) { throw 'Abmelden wird für diese Plattform nicht unterstützt.' }
    return @('logout', '--platform', $Platform, '--identity', (Get-Identity $Platform))
}

function Get-UrlPlatform {
    param([string]$Link)
    $uri = $null
    if (-not [Uri]::TryCreate($Link, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -notin @('https', 'http') -or $uri.UserInfo) {
        throw 'Bitte einen vollständigen öffentlichen Video-Link mit https:// eingeben.'
    }
    switch ($uri.DnsSafeHost.ToLowerInvariant()) {
        { $_ -in @('tiktok.com', 'www.tiktok.com', 'm.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com') } { return 'tiktok' }
        { $_ -in @('instagram.com', 'www.instagram.com') } { return 'instagram' }
        { $_ -in @('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be') } { return 'youtube' }
        { $_ -in @('reddit.com', 'www.reddit.com', 'old.reddit.com', 'np.reddit.com', 'm.reddit.com', 'redd.it', 'www.redd.it') } { return 'reddit' }
        { $_ -in @('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com') } { return 'x' }
        default { throw 'Dieser Link gehört nicht zu TikTok, Instagram, YouTube, Reddit oder X.' }
    }
}

function Get-ScrapeArguments {
    param([string]$Link, [string]$Export)
    $platform = Get-UrlPlatform $Link
    $arguments = @('scrape', $Link, '--backend', 'local',
        '--identity', (Get-Identity $platform), '--country', 'de', '--max-comments', '20', '--export', $Export)
    if ($platform -in @('tiktok', 'instagram', 'x')) { $arguments += '--require-auth' }
    return $arguments
}

function Invoke-Scraper {
    param([string[]]$Arguments)
    & $script:Python '-m' 'socialscraper' @Arguments | Out-Host
    return $LASTEXITCODE
}

function Show-Login {
    $platform = Get-Platform
    if (-not $platform) { return }
    Write-Host ''
    Write-Host '1  Im eigenen Browser anmelden (TikTok auch mit QR-Code)'
    Write-Host '2  Zugangsdaten aus data\credentials.json verwenden (nur TikTok und Instagram)'
    $method = Read-Host 'Methode wählen (Enter = 1)'
    $mode = 'qr'
    if ($method.Trim() -eq '2') { $mode = 'password' }
    elseif ($method.Trim() -notin @('', '1')) { return }
    if ($mode -eq 'password' -and $platform -in @('reddit', 'x')) {
        Write-Host 'Bei Reddit und X meldest du dich im Browserfenster selbst an.'
        $mode = 'qr'
    }
    Write-Host 'Das separate Anmeldefenster wird geöffnet. Bestätigung oder CAPTCHA dort abschließen.'
    $code = Invoke-Scraper (Get-LoginArguments $platform $mode)
    if ($code -eq 0) { Write-Host 'Anmeldung bestätigt und lokal gespeichert.' -ForegroundColor Green }
    else { Write-Host 'Anmeldung nicht bestätigt. Bitte die Meldung oben beachten und erneut versuchen.' -ForegroundColor Yellow }
}

function Show-Check {
    $platform = Get-Platform
    if (-not $platform) { return }
    $code = Invoke-Scraper (Get-CheckArguments $platform)
    if ($code -eq 0) { Write-Host 'Anmeldung ist gültig.' -ForegroundColor Green }
    elseif ($code -eq 4) { Write-Host 'Der Browser ist gerade belegt (Hintergrunddienst oder Programmfenster). Bitte später noch einmal prüfen.' -ForegroundColor Yellow }
    elseif ($code -eq 2 -or $code -eq 3) { Write-Host 'Anmeldung ist derzeit nicht bestätigt. Über Menüpunkt 1 anmelden.' -ForegroundColor Yellow }
    else { Write-Host 'Die Prüfung war nicht möglich (zum Beispiel kein Internet). Bitte die Meldung oben beachten.' -ForegroundColor Yellow }
}

function Show-Logout {
    $platform = Get-Platform
    if (-not $platform) { return }
    $confirm = Read-Host 'Gespeicherte Anmeldung und Browserprofil löschen? (j/n)'
    if ($confirm.Trim().ToLowerInvariant() -ne 'j') { return }
    $code = Invoke-Scraper (Get-LogoutArguments $platform)
    if ($code -eq 0) { Write-Host 'Abgemeldet. Für einen neuen Abruf über Menüpunkt 1 anmelden.' -ForegroundColor Green }
    else { Write-Host 'Abmelden nicht abgeschlossen. Bitte offene Browserfenster schließen und erneut versuchen.' -ForegroundColor Yellow }
}

function Show-Scrape {
    $link = (Read-Host 'Öffentlichen Video-Link eingeben (Enter = zurück)').Trim()
    if (-not $link) { return }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $suffix = [Guid]::NewGuid().ToString('N').Substring(0, 6)
    $export = Join-Path 'data\export' ($stamp + '-' + $suffix)
    $arguments = Get-ScrapeArguments $link $export
    Write-Host 'Videodaten und bis zu 20 Kommentare werden abgerufen ...'
    $code = Invoke-Scraper $arguments
    if ($code -eq 0) {
        Write-Host 'Abruf abgeschlossen.' -ForegroundColor Green
        Write-Host ('Gesamtexport der gespeicherten Daten: ' + (Join-Path $PSScriptRoot $export))
    }
    elseif ($code -eq 3) { Write-Host 'Ein bestätigter Login fehlt oder ist abgelaufen. Zuerst über Menüpunkt 1 anmelden.' -ForegroundColor Yellow }
    else { Write-Host 'Abruf nicht vollständig. Bitte die Meldung oben beachten.' -ForegroundColor Yellow }
}

function Start-Menu {
    if (-not (Test-Path -LiteralPath $script:Python -PathType Leaf)) {
        throw 'Die lokale Umgebung fehlt. Bitte zuerst Einrichten.cmd starten.'
    }
    & $script:Python '-m' 'socialscraper' '--help' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Das Programm ist noch nicht startbereit. Bitte Einrichten.cmd erneut starten.' }
    if ($NurPruefen) { Write-Host 'Startumgebung und Programm sind verfügbar.'; return }
    do {
        Write-Host ''
        Write-Host 'Socialscraper' -ForegroundColor Cyan
        Write-Host '1  Anmelden'
        Write-Host '2  Anmeldestatus prüfen'
        Write-Host '3  Video-Link auslesen'
        Write-Host '4  Abmelden'
        Write-Host '5  Beenden'
        $choice = Read-Host 'Bitte wählen'
        try {
            switch ($choice.Trim()) {
                '1' { Show-Login }
                '2' { Show-Check }
                '3' { Show-Scrape }
                '4' { Show-Logout }
                '5' { return }
                default { Write-Host 'Bitte 1, 2, 3, 4 oder 5 wählen.' }
            }
        }
        catch { Write-Host ('Vorgang angehalten: ' + $_.Exception.Message) -ForegroundColor Red }
    } while ($true)
}

# Funktionen dürfen für lokale Parameterprüfungen geladen werden, ohne das Menü zu starten.
if ($MyInvocation.InvocationName -ne '.') {
    try { Start-Menu; exit 0 }
    catch {
        Write-Host ('Start angehalten: ' + $_.Exception.Message) -ForegroundColor Red
        if (-not $OhnePause) { [void](Read-Host 'Zum Schließen Enter drücken') }
        exit 1
    }
}


