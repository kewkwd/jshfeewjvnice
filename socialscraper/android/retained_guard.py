"""Transfer an effective retained test lease after both old processes exited.

The original hold stays set throughout transfer. No firewall rule is removed or
created here; independent ActiveStore verification remains mandatory.
"""
import json,os,re,subprocess,uuid
from pathlib import Path
from ..net.errors import FinalError

def inactive(worker,guardian):
    if any(type(pid) is not int or pid<=0 for pid in (worker,guardian)):return False
    script=("$ErrorActionPreference='Stop'; if(@(Get-Process -Id "+str(worker)+','+str(guardian)+
        " -ErrorAction SilentlyContinue).Count){exit 1}; "+
        "if(@(Get-Process -Name Nox,NoxVMHandle,BigNoxVM,NoxConsole -ErrorAction SilentlyContinue).Count){exit 1}")
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],
        capture_output=True,timeout=15,creationflags=0x08000000)
    return result.returncode==0

def reuse(directory,vm_name,ownership_file):
    if not re.fullmatch(r'Nox_[0-9]{1,4}',vm_name) or ownership_file is None:return None
    from . import nox
    from .sdk import Profile
    directory=Path(directory).resolve()
    expected=directory.parent/'nox-console-device/owned.json'
    if Path(ownership_file).resolve()!=expected:return None
    for file in sorted(directory.glob('guard-*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
        if not re.fullmatch(r'guard-[0-9a-f]{32}\.json',file.name) or file.resolve().parent!=directory:continue
        hold=Path(str(file)+'.hold');release=Path(str(file)+'.release')
        if Path(str(file)+'.closed').exists() or not hold.is_file() or not release.is_file():continue
        from ..runlock import OsLock
        lock=OsLock(Path(str(file)+'.transfer.lock'))
        if not lock.try_acquire({'pid':os.getpid(),'owner':'android_guard_transfer'}):
            raise FinalError('Die Android-Netzwerksperre wird bereits von einem anderen Auftrag geprüft.')
        try:
            result=_candidate(file,expected,vm_name)
            if result:return result
        finally:lock.release()
    return None

def _candidate(file,expected,vm_name):
    from . import nox
    from .sdk import Profile
    hold=Path(str(file)+'.hold');release=Path(str(file)+'.release')
    try:
        state=json.loads(file.read_text(encoding='utf-8-sig'))
        error=json.loads(Path(str(file)+'.error').read_text(encoding='utf-8-sig'))
        launcher=json.loads(Path(str(file)+'.launch.json').read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return None
    if not all(isinstance(x,dict) for x in (state,error,launcher)):return None
    if (state.get('vm_name')!=vm_name or state.get('proxy_only') is not True or
        state.get('retain_file')!=str(hold) or state.get('release_file')!=str(release) or
        error.get('rules_retained') is not True or error.get('group')!=state.get('group') or
        launcher.get('started') is not True):return None
    if not inactive(state.get('worker_pid'),launcher.get('pid')):return None
    profile=Profile(expected.parent)
    if profile.name!=vm_name:raise FinalError('Gehaltene Sperre verweist auf ein anderes Testprofil.')
    if nox.info()['running_vms']:raise FinalError('Ein Android-Gerät läuft; Sperre wird nicht übertragen.')
    nox.verify_guard(state)
    # Keep canonical paths and the existing hold so orphan cleanup cannot
    # remove the effective group between verification and the next launch.
    state['previous_worker_pid']=state['worker_pid']
    state['worker_pid']=os.getpid();state['reused_retained_guard']=True
    temporary=file.with_suffix('.transfer-'+uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
        temporary.replace(file)
    finally:temporary.unlink(missing_ok=True)
    return file,state
