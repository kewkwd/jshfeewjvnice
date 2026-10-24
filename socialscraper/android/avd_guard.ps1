param([Parameter(Mandatory=$true)][string]$SdkRoot,[switch]$VerifyOnly)
$ErrorActionPreference='Stop'
$appRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$expected=[IO.Path]::GetFullPath((Join-Path $appRoot 'tools\android-sdk'))
if ([IO.Path]::GetFullPath($SdkRoot) -ne $expected) { throw 'Nur das eigene Android-SDK ist erlaubt.' }
$SdkRoot=$expected
$cursor=Get-Item -LiteralPath $SdkRoot
while ($null -ne $cursor) {
    if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Umgeleiteter SDK-Pfad.' }
    $cursor=$cursor.Parent
}
$manifest=Get-Content -LiteralPath (Join-Path $SdkRoot 'socialscraper-install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema -ne 1 -or $manifest.complete -ne $true) { throw 'SDK-Installation ist unvollständig.' }
$items=@(Get-ChildItem -LiteralPath $SdkRoot -Recurse -Force)
if (@($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'SDK enthält Dateiumleitungen.' }
$programs=@($items | Where-Object { !$_.PSIsContainer -and $_.Extension -ieq '.exe' } | Sort-Object FullName)
if (!$programs.Count) { throw 'SDK-Programme fehlen.' }
foreach ($program in $programs) {
    $relative=$program.FullName.Substring($SdkRoot.Length+1).Replace('\','/')
    $entries=@($manifest.files | Where-Object { $_.path -ceq $relative })
    if ($entries.Count -ne 1 -or (Get-FileHash -LiteralPath $program.FullName -Algorithm SHA256).Hash -ine $entries[0].sha256) { throw 'SDK-Programm stimmt nicht mit Installation überein.' }
}
$hasher=[Security.Cryptography.SHA256]::Create()
try { $id=([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($SdkRoot.ToLowerInvariant())))).Replace('-','').Substring(0,24) } finally { $hasher.Dispose() }
$group='SocialscraperAVD_'+$id
# IPv4 loopback is the sole allowed route to the application-owned proxy.
# IPv6 is blocked entirely, including mapped/external IPv6 destinations.
$blocked4=@('0.0.0.0/2','64.0.0.0/3','96.0.0.0/4','112.0.0.0/5','120.0.0.0/6','124.0.0.0/7','126.0.0.0/8','128.0.0.0/1')
$blocked6=@('::/1','8000::/1')
function Normalize-Address([string]$address) {
    $parts=$address.Split('/')
    if ($parts.Count -eq 2 -and $parts[1] -match '^\d+\.\d+\.\d+\.\d+$') {
        $bits=([Net.IPAddress]::Parse($parts[1]).GetAddressBytes() | ForEach-Object { [Convert]::ToString($_,2).PadLeft(8,'0') }) -join ''
        if ($bits -notmatch '^1*0*$') { throw 'Ungültige Firewall-Netzmaske.' }
        return $parts[0]+'/'+$bits.TrimEnd('0').Length
    }
    if ($parts.Count -eq 2) { return [Net.IPAddress]::Parse($parts[0]).ToString().ToLowerInvariant()+'/'+$parts[1] }
    return $address.ToLowerInvariant()
}
function Assert-Profiles {
    $profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore)
    if ($profiles.Count -ne 3 -or @($profiles | Where-Object Enabled -ne 1).Count -or
        @($profiles | Where-Object AllowLocalFirewallRules -eq 0).Count) { throw 'Windows-Firewall oder lokale Regeln sind nicht für alle Profile aktiv.' }
}
function Assert-Guard {
    Assert-Profiles
    $coverage=@{}
    foreach ($rule in @(Get-NetFirewallRule -PolicyStore ActiveStore -Group $group)) {
        if ($rule.Enabled -ne 1 -or $rule.Direction -ne 2 -or $rule.Action -ne 4) { continue }
        # ActiveStore can still contain configured rules that Windows does not
        # enforce (policy merge disabled, unresolved application, etc.).
        $enforcement=@($rule.CimInstanceProperties['EnforcementStatus'].Value)
        if ([int]$rule.PrimaryStatus -ne 1 -or $enforcement -notcontains 1 -or
            @($enforcement | Where-Object { $_ -notin @(1,5) }).Count) { continue }
        $app=$rule | Get-NetFirewallApplicationFilter
        $address=$rule | Get-NetFirewallAddressFilter
        $port=$rule | Get-NetFirewallPortFilter
        $interface=$rule | Get-NetFirewallInterfaceTypeFilter
        $alias=$rule | Get-NetFirewallInterfaceFilter
        $service=$rule | Get-NetFirewallServiceFilter
        if ($port.Protocol -eq 'Any' -and $port.LocalPort -eq 'Any' -and $port.RemotePort -eq 'Any' -and
            $address.LocalAddress -eq 'Any' -and $interface.InterfaceType -eq 'Any' -and
            $alias.InterfaceAlias -eq 'Any' -and $service.Service -eq 'Any' -and
            ([int]$rule.Profile -eq 0 -or ([int]$rule.Profile -band 7) -eq 7)) {
            $coverage[$app.Program]=@($coverage[$app.Program])+@($address.RemoteAddress | ForEach-Object { Normalize-Address $_ })
        }
    }
    foreach ($program in $programs) {
        if ((($coverage[$program.FullName] | Sort-Object -Unique) -join ',') -ne ((($blocked4+$blocked6) | Sort-Object -Unique) -join ',')) { throw 'AVD-Netzwerksperre ist unvollständig.' }
    }
}
if ($VerifyOnly) { Assert-Guard; Write-Output 'AVD_GUARD_VERIFIED'; exit 0 }
if (![Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Windows-Administratorbestätigung fehlt.' }
Assert-Profiles
# Persistent rules protect only this application-owned SDK. They are never
# removed on a crash or timeout, and do not alter other emulator installations.
$policy=New-Object -ComObject HNetCfg.FwPolicy2
$index=0
foreach ($program in $programs) {
    foreach ($family in @(@{addresses=$blocked4}, @{addresses=$blocked6})) {
        $name=$group+'_'+$index
        $existing=@($policy.Rules | Where-Object { $_.Name -ceq $name })
        if ($existing.Count) {
            if ($existing.Count -ne 1 -or $existing[0].Grouping -cne $group -or $existing[0].ApplicationName -ine $program.FullName) { throw 'Unbekannte gleichnamige Firewallregel.' }
            # Existing rules are independently checked below; never silently weaken them.
        } else {
            $rule=New-Object -ComObject HNetCfg.FWRule
            $rule.Name=$name; $rule.Grouping=$group
            $rule.Description='Socialscraper-AVD: externe Direktverbindungen gesperrt; nur lokale Proxy-Brücke.'
            $rule.ApplicationName=$program.FullName; $rule.Direction=2; $rule.Action=0
            $rule.Protocol=256; $rule.Profiles=7; $rule.LocalAddresses='*'
            $rule.InterfaceTypes='All'; $rule.RemoteAddresses=($family.addresses -join ','); $rule.Enabled=$true
            $policy.Rules.Add($rule)
        }
        $index++
    }
}
Assert-Guard
Write-Output 'AVD_GUARD_INSTALLED_AND_VERIFIED'
