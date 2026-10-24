"""Dedicated Nox test VM. Native traffic stays blocked except host loopback.

An Android HTTP proxy alone is not a network boundary. This backend requires
an independently verified Windows firewall lease before enabling VM networking.
"""
import json, os, re, socket, subprocess, time, uuid, tempfile
from pathlib import Path
import xml.etree.ElementTree as ET
from .adb import Adb
from ..net.errors import FinalError

NAME = 'Socialscraper_ProxyTest'
BIN = Path(r'C:\Program Files (x86)\Nox\bin')
RT = Path(r'C:\Program Files (x86)\Bignox\BigNoxVM\RT')
MANAGER = RT/'BigNoxVMMgr.exe'
ADB = BIN/'adb.exe'

def google_accounts(raw):
    # dumpsys also lists AuthenticatorDescription {type=com.google} when
    # there are no accounts. Only real Account records are registrations.
    return re.findall(r'^\s*Account\s*\{name=([^,{}]+),\s*type=com\.google\}',raw,re.M)

def command(*args, timeout=60):
    result=subprocess.run([str(MANAGER),*map(str,args)],capture_output=True,timeout=timeout,
                          creationflags=0x08000000 if os.name=='nt' else 0)
    if result.returncode:
        # Management commands contain no proxy/account credentials.
        raise FinalError('Nox: '+result.stderr.decode('utf8','replace').strip()[:500])
    return result.stdout.decode('utf8','replace')

def info(timeout=60):
    if not MANAGER.is_file() or not ADB.is_file():
        raise FinalError('Die vorhandene Nox-Installation mit VM-Steuerung und ADB wurde nicht gefunden.')
    return {'nox_present':True,'running_vms':command('list','runningvms',timeout=timeout).strip(),
            'firewall_required':True,'native_google_available':None}

def prepare(directory):
    """Clone packaged base disks only; never clone an existing user's VM."""
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    marker=directory/'owned.json'
    if marker.is_file():
        owned=json.loads(marker.read_text(encoding='utf8'))
        if owned.get('name')!=NAME or owned.get('directory')!=str(directory):
            raise FinalError('Das Android-Testprofil hat eine ungültige Eigentümermarkierung.')
        actual=command('showvminfo',NAME,'--machinereadable')
        if str(directory).replace('\\','\\\\') not in actual:
            raise FinalError('Der registrierte Emulator gehört nicht zum Testverzeichnis.')
        return directory
    if re.search(r'^"'+re.escape(NAME)+r'"',command('list','vms'),re.M):
        raise FinalError('Ein gleichnamiger Emulator existiert bereits; er wird nicht übernommen.')
    base=BIN/'data/base7'
    tree=ET.parse(base/'base7.vbox');root=tree.getroot()
    namespace=root.tag.partition('}')[0]+'}'
    machine=root.find(namespace+'Machine')
    machine.set('name',NAME);machine.set('uuid','{'+str(uuid.uuid4())+'}')
    machine.attrib.pop('currentSnapshot',None);machine.attrib.pop('stateFile',None)
    machine.set('snapshotFolder','Snapshots')
    for child in list(machine):
        if child.tag==namespace+'Snapshot':machine.remove(child)
    # Discard the template's snapshot registry and all old disk references.
    media=machine.find(namespace+'MediaRegistry')
    if media is None:media=ET.SubElement(machine,namespace+'MediaRegistry')
    media.clear();hard_disks=ET.SubElement(media,namespace+'HardDisks')
    new_ids=[]
    for number in (1,2):
        source=base/f'base7-disk{number}.vmdk';target=directory/f'disk{number}.vmdk'
        if target.exists():raise FinalError('Unvollständige Android-Vorbereitung vorhanden; keine Datei überschrieben.')
        command('clonemedium','disk',source,target,'--format','VMDK',timeout=300)
        details=command('showmediuminfo','disk',target)
        found=re.search(r'^UUID:\s*([0-9a-f-]{36})',details,re.M|re.I)
        if not found:raise FinalError('Neue Android-Testplatte konnte nicht geprüft werden.')
        identifier='{'+found[1]+'}';new_ids.append(identifier)
        ET.SubElement(hard_disks,namespace+'HardDisk',uuid=identifier,location=str(target),format='VMDK',type='Normal')
    controllers=machine.find(namespace+'StorageControllers')
    if controllers is None:raise FinalError('Android-Systemvorlage hat keine Plattensteuerung.')
    controller=next(iter(controllers))
    for child in list(controller):controller.remove(child)
    for number,identifier in enumerate(new_ids):
        device=ET.SubElement(controller,namespace+'AttachedDevice',type='HardDisk',port=str(number),device='0')
        ET.SubElement(device,namespace+'Image',uuid=identifier)
    network=machine.find('.//'+namespace+'Network')
    for adapter in network:
        adapter.set('enabled','false')
        for forwarding in list(adapter.iter(namespace+'Forwarding')):
            for parent in adapter.iter():
                if forwarding in list(parent):parent.remove(forwarding);break
    config=directory/(NAME+'.vbox');tree.write(config,encoding='utf-8',xml_declaration=True)
    command('registervm',config)
    marker.write_text(json.dumps({'name':NAME,'directory':str(directory),'fresh_base_disks':True}),encoding='utf8')
    return directory

def configure_network(port,name=NAME):
    command('modifyvm',name,'--nic1','nat','--natnet1','172.17.99.0/24',
            '--natdnshostresolver1','off','--natdnsproxy1','off','--natdnspassdomain1','off',
            # The shipped Android image uses NIC1 for ADB/rmnet_ipa0 and
            # NIC2 for wlan0. Without NIC2 Android has no default network.
            '--nic2','nat','--natnet2','172.17.100.0/24',
            '--natdnshostresolver2','off','--natdnsproxy2','off','--natdnspassdomain2','off',
            *[arg for n in range(3,10) for arg in (f'--nic{n}','none')])
    # Only a loopback ADB forwarding rule is allowed on the owned VM.
    current=command('showvminfo',name,'--machinereadable')
    if re.search(r'^Forwarding\(\d+\)="ss_adb,',current,re.M):
        command('modifyvm',name,'--natpf1','delete','ss_adb')
    command('modifyvm',name,'--natpf1',f'ss_adb,tcp,127.0.0.1,{port},,5555')

def configure_language():
    # Keep the installed Nox renderer's bundled host settings. The Android
    # screen language is applied inside the guest after it has booted.
    template=ET.parse(BIN/'data/base7/base7.vbox')
    properties={n.get('name'):n.get('value') for n in template.iter() if n.tag.endswith('}GuestProperty')}
    for key in ('nox_host_language','nox_host_country'):
        value=properties.get(key)
        if not value or not re.fullmatch(r'[a-zA-Z_-]{2,12}',value):
            raise FinalError('Sprachvorgabe der installierten Nox-Vorlage ist nicht lesbar.')
        command('guestproperty','set',NAME,key,value)
        if command('guestproperty','get',NAME,key).strip()!=f'Value: {value}':
            raise FinalError('Sprachvorgabe des Nox-Grafiksystems konnte nicht bestätigt werden.')

def verify_network(directory,name=NAME):
    root=ET.parse(Path(directory)/(name+'.vbox')).getroot()
    adapters=[node for node in root.iter() if node.tag.endswith('}Adapter') and node.get('enabled')=='true']
    if len(adapters)!=2 or {node.get('slot') for node in adapters}!={'0','1'}:
        raise FinalError('Android-Testgerät hat eine unerwartete Netzwerkverbindung.')
    networks={'0':'172.17.99.0/24','1':'172.17.100.0/24'}
    for adapter in adapters:
        nat=next((n for n in adapter if n.tag.endswith('}NAT')),None)
        if nat is None or nat.get('network')!=networks[adapter.get('slot')] or any(n.tag.endswith(('}BridgedInterface','}NATNetwork')) for n in adapter):
            raise FinalError('Android-Testgerät hat kein geprüftes NAT-Netz.')
        dns=next((n for n in nat if n.tag.endswith('}DNS')),None)
        # Nox omits false DNS attributes; effective ON/OFF was checked locally.
        if dns is None or dns.get('use-host-resolver','false')!='false' or dns.get('use-proxy','false')!='false':
            raise FinalError('DNS des Android-Testgeräts könnte den Host verwenden; Start gesperrt.')
        forwards=[n for n in nat if n.tag.endswith('}Forwarding')]
        if adapter.get('slot')=='1':
            if forwards:raise FinalError('Android-WLAN hat eine unerwartete Portweiterleitung.')
        elif len(forwards)!=1 or forwards[0].get('name')!='ss_adb' or forwards[0].get('hostip')!='127.0.0.1' or forwards[0].get('guestport')!='5555':
            raise FinalError('Android-ADB ist nicht ausschließlich lokal freigegeben.')

def stop(name=NAME,timeout=30):
    deadline=time.monotonic()+timeout
    raw=command('showvminfo',name,'--machinereadable',timeout=min(10,timeout))
    if re.search(r'^VMState="(?:running|paused|stuck)"\r?$',raw,re.M):
        seconds=deadline-time.monotonic()
        if seconds<=0:raise FinalError('Zeitlimit beim Abschalten des eigenen Nox-Testgeräts erreicht.')
        command('controlvm',name,'poweroff',timeout=seconds)

def verify_guard(state):
    """Independent effective-rule verification; a marker file alone is insufficient."""
    group=state.get('group','')
    if not re.fullmatch(r'SocialscraperProxy_[0-9a-f]{32}',group):
        raise FinalError('Android-Netzwerksperre ist nicht gültig.')
    script=Path(__file__).with_name('proxy_guard.ps1')
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
        '-VerifyOnly','-Group',group],capture_output=True,timeout=30,creationflags=0x08000000)
    if result.returncode:
        raise FinalError('Die Nox-Direktverbindungssperre wurde nicht bestätigt; Emulator bleibt aus.')

def request_guard(directory, melden, stop_requested=None,vm_name=NAME,ownership_file=None):
    if vm_name not in (NAME,'Socialscraper_ProxyConsole'):
        expected=Path(directory).resolve().parent/'nox-console-device/owned.json'
        if not re.fullmatch(r'Nox_[0-9]{1,4}',vm_name) or ownership_file is None or Path(ownership_file).resolve()!=expected:
            raise FinalError('Unbekanntes Android-Testgerät; keine Windows-Freigabe angefordert.')
        record=json.loads(expected.read_text(encoding='utf8'))
        if record.get('name')!=vm_name or record.get('sdk_title')!='Socialscraper_ProxyConsole' or record.get('fresh_sdk_base') is not True:
            raise FinalError('SDK-Testprofil ist nicht dem eigenen Versuch zugeordnet.')
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    state_file=directory/('guard-'+uuid.uuid4().hex+'.json')
    script=Path(__file__).with_name('proxy_guard.ps1').resolve()
    if stop_requested and stop_requested():raise FinalError('Abgebrochen.')
    if info()['running_vms']:raise FinalError('Andere Nox-Emulatoren laufen; kein Android-Test gestartet.')
    from .retained_guard import reuse
    retained=reuse(directory,vm_name,ownership_file)
    if retained:
        state_file,state=retained
        if stop_requested and stop_requested():raise FinalError('Abgebrochen.')
        os.environ['SS_ANDROID_GUARD']=str(state_file)
        from .progress import emit
        emit('guard_verified',melden)
        melden('Vorhandene Android-Netzwerksperre wirksam geprüft; keine erneute Windows-Abfrage nötig.')
        return state
    melden('Windows-Administratorbestätigung erforderlich: temporäre Direktverbindungssperre für Nox. Ohne Bestätigung bleibt Android aus.')
    from .progress import emit
    emit('waiting_guard',melden)
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    arguments=['-NoProfile','-ExecutionPolicy','Bypass','-File','"'+str(script)+'"',
               '-WorkerPid',str(os.getpid()),'-StateFile','"'+str(state_file)+'"','-VmName',vm_name]
    if ownership_file is not None:arguments+=['-OwnershipFile','"'+str(Path(ownership_file).resolve())+'"']
    launch_result=Path(str(state_file)+'.launch.json')
    statement=("$ErrorActionPreference='Stop'; try { $p=Start-Process -FilePath powershell.exe -Verb RunAs -WindowStyle Hidden -PassThru -ArgumentList @("+
        ','.join(quote(a) for a in arguments)+"); @{started=$true;pid=$p.Id}|ConvertTo-Json|Set-Content -LiteralPath "+quote(launch_result)+
        " -Encoding UTF8 } catch { $e=$_.Exception; $code=0; while($e){if($e -is [ComponentModel.Win32Exception]){$code=$e.NativeErrorCode};$e=$e.InnerException}; "+
        "@{started=$false;native_code=$code;error=$_.Exception.Message}|ConvertTo-Json|Set-Content -LiteralPath "+quote(launch_result)+
        " -Encoding UTF8; exit 1 }")
    launcher=subprocess.Popen(['powershell.exe','-NoProfile','-Command',statement],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        if stop_requested and stop_requested():break
        if state_file.is_file():
            state=json.loads(state_file.read_text(encoding='utf-8-sig'))
            if state.get('worker_pid')!=os.getpid():raise FinalError('Falscher Android-Testauftrag.')
            try:verify_guard(state)
            except BaseException:
                Path(str(state_file)+'.release').write_text('release',encoding='ascii');raise
            os.environ['SS_ANDROID_GUARD']=str(state_file);return state
        error=Path(str(state_file)+'.error')
        if error.is_file():
            raise FinalError('Android-Netzwerksperre fehlgeschlagen: '+json.loads(error.read_text(encoding='utf-8-sig')).get('error','Windows-Freigabe fehlt.'))
        if launcher.poll() not in (None,0):
            Path(str(state_file)+'.release').write_text('release',encoding='ascii')
            if launch_result.is_file():
                result=json.loads(launch_result.read_text(encoding='utf-8-sig'))
                if result.get('native_code')==1223:
                    raise FinalError('Die Windows-Administratorabfrage wurde geschlossen oder abgelehnt; kein Emulator gestartet.')
                from ..problemlogic import scrub
                raise FinalError('Windows konnte die Administratorabfrage nicht starten: '+scrub(str(result.get('error','Unbekannter Startfehler.')))[:500])
            raise FinalError('Windows konnte die Administratorabfrage nicht starten; kein Emulator gestartet.')
        time.sleep(.5)
    Path(str(state_file)+'.release').write_text('release',encoding='ascii')
    if launcher.poll() is None:launcher.terminate()
    raise FinalError('Windows-Freigabe für Android nicht bestätigt; kein Emulator gestartet.')

class Session:
    def __init__(self, directory, proxy, stop_requested=None):
        self.directory=Path(directory).resolve();self.proxy=proxy;self.stop_requested=stop_requested
        self.adb=None;self.bridge=None;self.started=False;self.lease=None
        self.renderer=None;self.frontend=None;self.frontend_log=None

    def __enter__(self):
        if self.proxy is None:raise FinalError('Android-Tests benötigen einen ausdrücklich konfigurierten Proxy oder Tor.')
        if info()['running_vms']:raise FinalError('Bitte andere Nox-Emulatoren zuerst beenden; sie werden nicht verändert.')
        from ..net.browserproxy import BrowserProxy
        # A caller prepares and grants the OS lease explicitly; never self-elevate.
        path=os.environ.get('SS_ANDROID_GUARD')
        if not path:raise FinalError('Für Android fehlt die Windows-Netzwerksperre. „Android vorbereiten“ ausführen; kein Emulator gestartet.')
        self.lease=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        verify_guard(self.lease)
        if self.lease.get('worker_pid')!=os.getpid():
            raise FinalError('Die Android-Netzwerksperre gehört nicht zu diesem Testauftrag.')
        try:
            prepare(self.directory)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            configure_language();configure_network(port);verify_network(self.directory);verify_guard(self.lease)
            from .render_host import RenderHost
            self.renderer=RenderHost(self.directory.parent/'android-sessions',self.stop_requested)
            hwnd=self.renderer.start()
            verify_guard(self.lease)
            self.frontend_log=tempfile.TemporaryFile(dir=self.directory)
            self.started=True  # A failed launch can still leave a VM running.
            self.frontend=subprocess.Popen([str(RT/'NoxVMHandle.exe'),'--startvm',NAME,
                '--renderpath',str(RT),'--hwnd',str(hwnd)],cwd=RT,
                stdout=self.frontend_log,stderr=self.frontend_log,creationflags=0x08000000)
            serial=f'127.0.0.1:{port}'
            controller=Adb(adb=str(ADB))
            until=time.monotonic()+120
            while time.monotonic()<until:
                if self.stop_requested and self.stop_requested():raise FinalError('Abgebrochen.')
                if self.frontend.poll() is not None:
                    raise FinalError('Der Android-Testemulator wurde beim Start beendet. Details im eigenen Nox-Testprotokoll.')
                controller._cmd('connect',serial,timeout=10)
                adb=Adb(serial,adb=str(ADB))
                try:
                    if adb.sh('getprop','sys.boot_completed',timeout=10).strip()=='1':break
                except Exception:pass
                time.sleep(2)
            else:raise FinalError('Android-Testgerät ist nicht hochgefahren.')
            # No inherited Google account may make network requests via our proxy.
            if google_accounts(adb.sh('dumpsys','account')):
                raise FinalError('Die Android-Vorlage enthält ein Google-Konto; es wird nicht für Tests verwendet.')
            self.bridge=BrowserProxy(self.proxy,allow_no_auth=True)
            bridge_port=self.bridge.server.server_address[1]
            # Network-bound Android clients must reach the host gateway of wlan0.
            adb.proxy_setzen(f'172.17.100.2:{bridge_port}')
            if adb.sh('settings','get','global','http_proxy').strip()!=f'172.17.100.2:{bridge_port}':
                raise FinalError('Android-Proxy konnte nicht bestätigt werden.')
            verify_guard(self.lease);self.adb=adb
            return self
        except BaseException:
            self.__exit__(None,None,None);raise

    def __exit__(self,*unused):
        try:
            if self.started:stop()
        finally:
            try:
                if self.frontend:
                    try:self.frontend.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.frontend.terminate();self.frontend.wait(timeout=10)
            finally:
                if self.frontend_log:self.frontend_log.close()
                try:
                    if self.renderer:self.renderer.close()
                finally:
                    try:
                        if self.bridge:self.bridge.close()
                    finally:
                        if self.lease:
                            # The helper retains rules unless all VMs are off.
                            Path(self.lease['release_file']).write_text('release',encoding='ascii')

