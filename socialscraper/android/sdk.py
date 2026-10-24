"""Official Nox frontend for the explicitly created and recorded test profile."""
import json,os,re,subprocess,time
from pathlib import Path
from . import nox
from .adb import Adb,AdbError
from .sdk_network import read as network
from ..net.errors import FinalError
from ..problemlogic import scrub
from .progress import emit

CONSOLE=nox.BIN/'NoxConsole.exe'
CLEANUP_SECONDS=60

def remaining(deadline,maximum=None):
    seconds=deadline-time.monotonic()
    if seconds<=0:raise FinalError('Zeitlimit der Nox-Steuerung erreicht; Verbindungssperre bleibt bei ungewissem Ende erhalten.')
    return min(seconds,maximum) if maximum is not None else seconds

def console(*args,timeout=10):
    result=subprocess.run([str(CONSOLE),*args],cwd=CONSOLE.parent,capture_output=True,timeout=timeout,creationflags=0x08000000)
    if result.returncode:
        raise FinalError('Nox-Konsole: '+scrub(result.stdout.decode('utf8','replace')+' '+result.stderr.decode('utf8','replace')))
    return result.stdout.decode('utf8','replace')

class Profile:
    def __init__(self,directory):
        self.directory=Path(directory).resolve();self.marker=self.directory/'owned.json'
        if not self.marker.is_file():raise FinalError('Eigenes offizielles Nox-Testprofil ist noch nicht vorbereitet. Kein vorhandenes Profil übernommen.')
        self.record=json.loads(self.marker.read_text(encoding='utf8'));self.name=self.record.get('name')
        if not isinstance(self.name,str) or not re.fullmatch(r'Nox_[0-9]{1,4}',self.name) or self.record.get('sdk_title')!='Socialscraper_ProxyConsole' or self.record.get('fresh_sdk_base') is not True or self.record.get('phase')!='prepared':
            raise FinalError('Nox-Testprofil hat keine bestätigte eigene Zuordnung.')
        self.config=Path(self.record['config']).resolve()
        if self.config.name!=self.name+'.vbox' or self.record.get('uuid') in self.record.get('prior_vm_uuids',[]):
            raise FinalError('Nox-Testprofil verweist auf ein anderes Gerät.')
        self.verify()

    def row(self,timeout=10):
        rows=[r.split(',') for r in console('list',timeout=timeout).splitlines()]
        own=[r for r in rows if len(r)>=7 and r[1:3]==[self.name,'Socialscraper_ProxyConsole']]
        if len(own)!=1 or not own[0][0].isdecimal():raise FinalError('Nox-Testprofil ist nicht eindeutig im offiziellen Starter vorhanden.')
        return own[0]

    def verify(self,timeout=20):
        deadline=time.monotonic()+timeout
        self.row(timeout=remaining(deadline,10))
        raw=nox.command('showvminfo',self.name,'--machinereadable',timeout=remaining(deadline,10))
        if ('UUID="'+str(self.record.get('uuid'))+'"') not in raw or ('CfgFile="'+str(self.config).replace('\\','\\\\')+'"') not in raw:
            raise FinalError('Registriertes Nox-Gerät stimmt nicht mit dem eigenen Testprofil überein.')

    def action(self,verb,timeout=30):
        if verb not in ('launch','quit'):raise ValueError('Unbekannte Nox-Steuerung.')
        deadline=time.monotonic()+timeout
        self.verify(timeout=remaining(deadline,20));row=self.row(timeout=remaining(deadline,5))
        return console(verb,'-index:'+row[0],timeout=remaining(deadline,10))

class Session:
    def __init__(self,profile,proxy,stop_requested=None,melden=lambda _:None):
        self.profile=profile;self.proxy=proxy;self.stop_requested=stop_requested;self.melden=melden
        self.bridge=None;self.adb=None;self.lease=None;self.launched=False;self.observed=False

    def cancelled(self):
        if self.stop_requested and self.stop_requested():raise FinalError('Abgebrochen.')

    def __enter__(self):
        if self.proxy is None:raise FinalError('Android benötigt einen ausdrücklich konfigurierten Proxy oder Tor.')
        self.cancelled()
        path=os.environ.get('SS_ANDROID_GUARD')
        if not path:raise FinalError('Windows-Netzwerksperre fehlt; kein Android-Gerät gestartet.')
        self.lease=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if self.lease.get('worker_pid')!=os.getpid() or self.lease.get('vm_name')!=self.profile.name or not self.lease.get('retain_file'):
            raise FinalError('Windows-Netzwerksperre gehört nicht zum eigenen SDK-Testauftrag.')
        nox.verify_guard(self.lease)
        try:
            if nox.info()['running_vms']:raise FinalError('Ein Nox-Gerät läuft bereits; kein weiterer Test gestartet.')
            self.profile.verify();network(self.profile.config)
            Path(self.lease['retain_file']).write_text('pending SDK start',encoding='ascii')
            self.launched=True
            emit('starting',self.melden)
            self.melden('Eigenes Nox-Testgerät über den offiziellen Starter öffnen …')
            self.profile.action('launch')
            controller=Adb(adb=str(nox.ADB),stop_requested=self.stop_requested);until=time.monotonic()+180
            while time.monotonic()<until:
                self.cancelled()
                serial,gateway=network(self.profile.config)
                running='VMState="running"' in nox.command('showvminfo',self.profile.name,'--machinereadable',timeout=10)
                if running:
                    if not self.observed:emit('booting',self.melden)
                    self.observed=True
                    try:
                        controller._cmd('connect',serial,timeout=5);adb=Adb(serial,adb=str(nox.ADB),stop_requested=self.stop_requested)
                        if adb.sh('getprop','sys.boot_completed',timeout=5).strip()=='1' and adb.sh('am','get-config',timeout=5).startswith('config:'):break
                    except AdbError:pass  # Device transport may appear after the native VM.
                time.sleep(.5)
            else:raise FinalError('Eigenes Nox-Testgerät ist nicht rechtzeitig hochgefahren.')
            self.observed=True;nox.verify_guard(self.lease);self.profile.verify();network(self.profile.config)
            if nox.google_accounts(adb.sh('dumpsys','account')):raise FinalError('Eigenes Gerät enthält bereits ein Google-Konto; keine Anmeldung übernommen.')
            from ..net.browserproxy import BrowserProxy
            emit('proxy_check',self.melden)
            self.bridge=BrowserProxy(self.proxy,allow_no_auth=True)
            expected=gateway+':'+str(self.bridge.server.server_address[1]);adb.proxy_setzen(expected)
            if adb.sh('settings','get','global','http_proxy').strip()!=expected:raise FinalError('Android-Proxy ist nicht bestätigt.')
            nox.verify_guard(self.lease);self.adb=adb
            emit('device_ready',self.melden)
            self.melden('Android gestartet und Proxy bestätigt. Google-Kontodialog wird vorbereitet …')
            return self
        except BaseException as original:
            try:self.__exit__(None,None,None)
            except BaseException as cleanup:
                if isinstance(original,(KeyboardInterrupt,SystemExit)):
                    original.add_note('Aufräumen: '+scrub(str(cleanup)))
                    raise original
                raise FinalError(scrub(str(original))+' Aufräumen: '+scrub(str(cleanup))) from original
            raise

    def __exit__(self,*unused):
        errors=[];stopped=False;cleanup_deadline=time.monotonic()+CLEANUP_SECONDS
        def attempt(label,function):
            try:function();return True
            except BaseException as problem:errors.append((label,problem));return False
        if self.launched:
            attempt('Abschlussmeldung',lambda:emit('stopping',self.melden))
            if attempt('Eigene Gerätezuordnung prüfen',lambda:self.profile.verify(timeout=remaining(cleanup_deadline,10))):
                def observe():
                    if 'VMState="running"' in nox.command('showvminfo',self.profile.name,'--machinereadable',timeout=remaining(cleanup_deadline,5)):self.observed=True
                attempt('Gerätezustand lesen',observe)
                attempt('Nox-Fenster schließen',lambda:self.profile.action('quit',timeout=remaining(cleanup_deadline,15)))
                # A failed frontend quit must not skip the hypervisor fallback.
                # Recheck ownership immediately before that fallback.
                if attempt('Gerätezuordnung erneut prüfen',lambda:self.profile.verify(timeout=remaining(cleanup_deadline,10))):
                    attempt('Eigenes Gerät abschalten',lambda:nox.stop(name=self.profile.name,timeout=remaining(cleanup_deadline,20)))
            def wait_stopped():
                nonlocal stopped
                until=min(cleanup_deadline,time.monotonic()+15);stopped=False
                while time.monotonic()<until:
                    check=subprocess.run(['powershell.exe','-NoProfile','-Command',
                        'if(@(Get-Process -Name Nox,NoxVMHandle,BigNoxVM,NoxConsole -ErrorAction SilentlyContinue).Count){exit 1}'],capture_output=True,timeout=remaining(until,5),creationflags=0x08000000)
                    if check.returncode==0 and not nox.info(timeout=remaining(until,3))['running_vms']:stopped=True;break
                    time.sleep(.5)
            attempt('Ende der nativen Prozesse bestätigen',wait_stopped)
            if not self.observed or not stopped:
                errors.append(('Netzwerksperre',FinalError('SDK-Start nicht sicher beendet; Direktverbindungssperre bleibt erhalten.')))
        if self.bridge:attempt('Proxy-Brücke schließen',self.bridge.close)
        if self.lease:
            if self.launched and not errors and not self.lease.get('reused_retained_guard'):
                attempt('Sperrhaltung abschließen',lambda:Path(self.lease['retain_file']).unlink(missing_ok=True))
            # Signal even when another cleanup operation failed. The hold still
            # prevents removal in every uncertain shutdown case.
            attempt('Freigabesignal schreiben',lambda:Path(self.lease['release_file']).write_text('release',encoding='ascii'))
        if errors:
            description='; '.join(label+': '+scrub(str(problem)) for label,problem in errors)
            original=unused[1] if len(unused)>1 else None
            if isinstance(original,(KeyboardInterrupt,SystemExit)):
                original.add_note(description);raise original
            for _,problem in errors:
                if isinstance(problem,(KeyboardInterrupt,SystemExit)):
                    if original is not None:problem.add_note('Ursprünglicher Fehler: '+scrub(str(original)))
                    problem.add_note(description);raise problem
            if original is not None:raise FinalError(scrub(str(original))+' Aufräumen: '+description) from original
            raise FinalError(description) from errors[0][1]
