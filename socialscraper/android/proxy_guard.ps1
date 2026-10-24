param([switch]$VerifyOnly,[string]$Group,[int]$WorkerPid,[string]$StateFile,[string]$VmName='Socialscraper_ProxyTest',[string]$OwnershipFile)
$ErrorActionPreference='Stop'
$blocked4=@('0.0.0.0/2','64.0.0.0/3','96.0.0.0/4','112.0.0.0/5','120.0.0.0/6','124.0.0.0/7','126.0.0.0/8','128.0.0.0/1')
$blocked6=@('::/1','8000::/1')
$remoteBlocked=$blocked4+$blocked6
$engineFolders=@('C:\Program Files (x86)\Nox\bin','C:\Program Files (x86)\Bignox\BigNoxVM\RT')
$programs=@($engineFolders | ForEach-Object { Get-ChildItem -LiteralPath $_ -File -Filter '*.exe' } | ForEach-Object FullName)
function Normalize-Address([string]$address) {
    $parts=$address.Split('/')
    if ($parts.Count -eq 2 -and $parts[1] -match '^\d+\.\d+\.\d+\.\d+$') {
        $bits=([Net.IPAddress]::Parse($parts[1]).GetAddressBytes() | ForEach-Object { [Convert]::ToString($_,2).PadLeft(8,'0') }) -join ''
        if ($bits -notmatch '^1*0*$') { throw 'Ungültige Firewall-Netzmaske' }
        return $parts[0]+'/'+($bits.TrimEnd('0').Length)
    }
    if ($parts.Count -eq 2) { return [Net.IPAddress]::Parse($parts[0]).ToString().ToLowerInvariant()+'/'+$parts[1] }
    return $address.ToLowerInvariant()
}
function Assert-Guard {
    if ($Group -notmatch '^SocialscraperProxy_[0-9a-f]{32}$') { throw 'Ungültige Regelgruppe' }
    if (@(Get-NetFirewallProfile -PolicyStore ActiveStore | Where-Object Enabled -ne 1).Count) { throw 'Eine Windows-Firewall ist ausgeschaltet' }
    $verified=@{}
    foreach($rule in @(Get-NetFirewallRule -PolicyStore ActiveStore -Group $Group | Where-Object { $_.Enabled -eq 1 -and $_.Direction -eq 2 -and $_.Action -eq 4 })) {
            $app=$rule | Get-NetFirewallApplicationFilter
            $address=$rule | Get-NetFirewallAddressFilter
            $port=$rule | Get-NetFirewallPortFilter
            if ($port.Protocol -eq 'Any' -and ([int]$rule.Profile -eq 0 -or ([int]$rule.Profile -band 7) -eq 7)) {
                $verified[$app.Program]=@($verified[$app.Program])+@($address.RemoteAddress | ForEach-Object { Normalize-Address $_ })
            }
    }
    foreach($program in $programs) {
        $actualCoverage=($verified[$program] | Sort-Object -Unique) -join ','
        $expectedCoverage=($remoteBlocked | Sort-Object -Unique) -join ','
        if ($actualCoverage -ne $expectedCoverage) { throw ('Netzwerksperre fehlt: '+[IO.Path]::GetFileName($program)) }
    }
}
function Assert-EnginesStopped {
    if(@(Get-Process -Name Nox,NoxVMHandle,BigNoxVM,NoxConsole -ErrorAction SilentlyContinue).Count) {
        throw 'Nox-Startprozess laeuft noch: Netzwerksperre bleibt bestehen.'
    }
}
function Remove-OrphanedGuards([string]$folder) {
    $newGroup=$Group
    foreach($previousFile in @(Get-ChildItem -LiteralPath $folder -File -Filter 'guard-*.json')) {
        if($previousFile.FullName -eq [IO.Path]::GetFullPath($StateFile) -or (Test-Path -LiteralPath ($previousFile.FullName+'.closed'))) { continue }
        $previous=Get-Content -LiteralPath $previousFile.FullName -Raw | ConvertFrom-Json
        if($previous.proxy_only -ne $true -or $previous.group -notmatch '^SocialscraperProxy_[0-9a-f]{32}$' -or
            $previous.release_file -ne ($previousFile.FullName+'.release') -or $previous.worker_pid -le 0) { continue }
        if(Get-Process -Id $previous.worker_pid -ErrorAction SilentlyContinue) { continue }
        if(Test-Path -LiteralPath ($previousFile.FullName+'.hold')) { continue }
        # Never remove a rule while any emulator is running, including an
        # unrelated user's VM. Only independently verified own rules qualify.
        $remaining=(& $manager list runningvms) -join "`n"
        if($LASTEXITCODE -ne 0 -or $remaining.Trim()) { throw 'Nox läuft noch: verwaiste Netzwerksperre bleibt bestehen.' }
        Assert-EnginesStopped
        $oldRules=@(Get-NetFirewallRule -Group $previous.group -ErrorAction SilentlyContinue)
        if($oldRules.Count) {
            foreach($rule in $oldRules) {
                $app=$rule | Get-NetFirewallApplicationFilter
                # The COM API's Name becomes DisplayName in NetSecurity;
                # NetSecurity assigns its own GUID to the internal Name.
                if($app.Program -notin $programs -or $rule.DisplayName -notmatch ('^'+[regex]::Escape($previous.group)+'_\d+$')) {
                    throw 'Unbekannte Regel in alter Testgruppe; keine automatische Entfernung.'
                }
            }
            try { $Group=$previous.group; Assert-Guard } finally { $Group=$newGroup }
            $oldRules | Remove-NetFirewallRule
        }
        @{group=$previous.group;released=$true;reason='worker_ended'} | ConvertTo-Json | Set-Content -LiteralPath ($previousFile.FullName+'.closed') -Encoding UTF8
    }
}
if($VerifyOnly) { Assert-Guard; exit 0 }
if (![Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Windows-Administratorbestätigung fehlt' }
if (!$StateFile -or $WorkerPid -le 0) { throw 'Testauftrag fehlt' }
$Group='SocialscraperProxy_'+[guid]::NewGuid().ToString('N')
$release=$StateFile+'.release'
$retain=$StateFile+'.hold'
$manager='C:\Program Files (x86)\Bignox\BigNoxVM\RT\BigNoxVMMgr.exe'
$created=$false
$phase='Vorprüfung'
try {
    $phase='Eigenes SDK-Testprofil pruefen'
    if($VmName -notin @('Socialscraper_ProxyTest','Socialscraper_ProxyConsole')) {
        $expectedOwner=Join-Path ([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($StateFile)))) 'nox-console-device\owned.json'
        if($VmName -notmatch '^Nox_[0-9]{1,4}$' -or !$OwnershipFile -or [IO.Path]::GetFullPath($OwnershipFile) -ne $expectedOwner) { throw 'Unbekanntes Testgeraet' }
        $owner=Get-Content -LiteralPath $OwnershipFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if($owner.name -ne $VmName -or $owner.sdk_title -ne 'Socialscraper_ProxyConsole' -or $owner.fresh_sdk_base -ne $true) { throw 'Testprofil-Zuordnung fehlt' }
        $rows=& 'C:\Program Files (x86)\Nox\bin\NoxConsole.exe' list
        $matches=@($rows | Where-Object { $columns=$_ -split ','; $columns.Count -ge 3 -and $columns[1] -ceq $VmName -and $columns[2] -ceq 'Socialscraper_ProxyConsole' })
        if($LASTEXITCODE -ne 0 -or $matches.Count -ne 1) { throw 'SDK-Testprofil nicht eindeutig bestaetigt' }
        $registered=(& $manager list vms) -join "`n"
        if($LASTEXITCODE -ne 0) { throw 'VM-Verzeichnis nicht lesbar' }
        if($owner.phase -eq 'created') {
            if($registered -match ('(?m)^"'+[regex]::Escape($VmName)+'"')) { throw 'Neue SDK-VM unerwartet bereits registriert' }
        } elseif($owner.phase -eq 'prepared') {
            $actual=(& $manager showvminfo $VmName --machinereadable) -join "`n"
            $escapedConfig=([string]$owner.config).Replace('\','\\')
            if($LASTEXITCODE -ne 0 -or !$owner.uuid -or !$owner.config -or !$actual.Contains('UUID="'+$owner.uuid+'"') -or !$actual.Contains('CfgFile="'+$escapedConfig+'"')) { throw 'SDK-VM hat eine abweichende Zuordnung' }
            if($owner.uuid -in @($owner.prior_vm_uuids)) { throw 'SDK-VM verweist auf ein vorheriges Geraet' }
        } else { throw 'Unbekannter SDK-Vorbereitungsstand' }
    }
    if (@(Get-NetFirewallProfile -PolicyStore ActiveStore | Where-Object Enabled -ne 1).Count) { throw 'Windows-Firewall ist ausgeschaltet' }
    $phase='Vorige Testaufträge aufräumen'
    Remove-OrphanedGuards ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($StateFile)))
    $index=0
    $created=$true
    $policy=New-Object -ComObject HNetCfg.FwPolicy2
    foreach($program in $programs) {
        # The installed native firewall API accepts address ranges which the
        # NetSecurity CIM creation provider rejects on this Windows version.
      foreach($family in @(@{name='IPv4';addresses=$blocked4},@{name='IPv6';addresses=$blocked6})) {
        $phase='Regelerstellung '+$family.name+' '+[IO.Path]::GetFileName($program)
        $nativeRule=New-Object -ComObject HNetCfg.FWRule
        $nativeRule.Name=$Group+'_'+$index
        $nativeRule.Grouping=$Group
        $nativeRule.Description='Temporäre Nox-Testregel: nur die lokale Proxy-Brücke ist erreichbar.'
        $nativeRule.ApplicationName=$program
        $nativeRule.Direction=2
        $nativeRule.Action=0
        $nativeRule.Protocol=256
        $nativeRule.Profiles=7
        $nativeRule.LocalAddresses='*'
        $nativeRule.InterfaceTypes='All'
        $nativeRule.RemoteAddresses=$family.addresses -join ','
        $nativeRule.Enabled=$true
        $policy.Rules.Add($nativeRule)
        $index++
      }
    }
    $phase='Prüfung der wirksamen Regeln'
    Assert-Guard
    @{group=$Group;worker_pid=$WorkerPid;release_file=$release;retain_file=$retain;vm_name=$VmName;proxy_only=$true} | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
    $deadline=(Get-Date).AddMinutes(25)
    while ((Get-Process -Id $WorkerPid -ErrorAction SilentlyContinue) -and !(Test-Path -LiteralPath $release) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    $vms=(& $manager list runningvms) -join "`n"
    if($vms -match ('^"'+[regex]::Escape($VmName)+'"')) { & $manager controlvm $VmName poweroff | Out-Null }
    $remaining=(& $manager list runningvms) -join "`n"
    if($LASTEXITCODE -ne 0 -or $remaining.Trim()) { throw 'Nox läuft noch: Netzwerksperre bleibt bestehen.' }
    if(Test-Path -LiteralPath $retain) { throw 'SDK-Start nicht sicher abgeschlossen: Netzwerksperre bleibt bestehen.' }
    Assert-EnginesStopped
    Get-NetFirewallRule -Group $Group | Remove-NetFirewallRule
    @{group=$Group;released=$true} | ConvertTo-Json | Set-Content -LiteralPath ($StateFile+'.closed') -Encoding UTF8
} catch {
    if ($created) {
        try {
            $remaining=(& $manager list runningvms) -join "`n"
            if($LASTEXITCODE -eq 0 -and !$remaining.Trim() -and !(Test-Path -LiteralPath $retain)) {
                Assert-EnginesStopped
                Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
                $created=$false
            }
        } catch { }
    }
    @{error=$_.Exception.Message;phase=$phase;group=$Group;rules_retained=$created} | ConvertTo-Json | Set-Content -LiteralPath ($StateFile+'.error') -Encoding UTF8
    exit 1
}
