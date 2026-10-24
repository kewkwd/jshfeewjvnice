"""Owned Android SDK profiles and an independently checked Windows boundary.

Preparing a profile never boots a device. Firewall approval is a separate,
explicit setup step; a JSON marker is never accepted as a network boundary.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from ..net.errors import FinalError
from ..runlock import OsLock

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / 'tools/android-sdk'
HOME = ROOT / 'data/android-avd'
IMAGE = 'system-images/android-35/google_apis_playstore/x86_64'
TEST_NAME = 'ss_avd_test_r49'


def name_for(identity):
    from ..net.identity import validate_name
    validate_name(identity)
    return 'ss_' + hashlib.sha256(identity.encode('utf8')).hexdigest()[:24]


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'ss_[a-z0-9_]{1,40}', name):
        raise FinalError('AVD ist kein eigenes Socialscraper-Gerät.')
    return name


def _plain(path):
    """Reject redirects anywhere in an owned path, including its parents."""
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise FinalError('AVD-Pfad enthält eine Dateiumleitung.')
    return path.resolve()


def sdk_info(sdk=None):
    sdk = _plain(sdk or SDK)
    try:
        info = json.loads((sdk / 'socialscraper-install.json').read_text(encoding='utf8'))
    except (OSError, ValueError):
        raise FinalError('Das eigene Android-SDK ist noch nicht vollständig installiert.') from None
    if info.get('schema') != 1 or info.get('complete') is not True or info.get('image') != IMAGE:
        raise FinalError('Das Android-SDK hat keinen bestätigten Installationsstand.')
    for relative in ('emulator/emulator.exe', 'platform-tools/adb.exe',
                     *(IMAGE + '/' + name for name in
                       ('system.img', 'vendor.img', 'ramdisk.img', 'kernel-ranchu', 'source.properties'))):
        path = _plain(sdk / relative)
        if not path.is_file() or not path.is_relative_to(sdk):
            raise FinalError('Eine benötigte Android-SDK-Datei fehlt.')
    # Current official images let the emulator create userdata from data/.
    # Accept that published layout as well as the older prebuilt partition.
    data_sources = [_plain(sdk / IMAGE / name) for name in ('userdata.img', 'data/empty_data_disk')]
    if not any(path.is_file() and path.stat().st_size > 0 for path in data_sources):
        raise FinalError('Die Android-Vorlage für Benutzerdaten fehlt.')
    files = info.get('files')
    if not isinstance(files, list) or not files:
        raise FinalError('Das SDK-Dateiverzeichnis fehlt.')
    expected = {}
    for entry in files:
        relative, digest = entry.get('path'), entry.get('sha256')
        if (not isinstance(relative, str) or '\\' in relative or
                any(x in ('', '.', '..') for x in relative.split('/')) or
                not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise FinalError('Das SDK-Dateiverzeichnis ist ungültig.')
        path = _plain(sdk / relative)
        if not path.is_relative_to(sdk) or path in expected:
            raise FinalError('Das SDK-Dateiverzeichnis hat einen fremden oder doppelten Pfad.')
        expected[path] = digest
    programs = sorted(p for p in sdk.rglob('*') if p.suffix.lower() == '.exe')
    if not programs or any(_plain(p) not in expected for p in programs):
        raise FinalError('SDK-Programme sind nicht vollständig zugeordnet.')
    for program in programs:
        with program.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != expected[program.resolve()]:
            raise FinalError('Ein SDK-Programm wurde seit der Installation verändert.')
    return sdk, info, programs


def prepare_profile(name=TEST_NAME, *, sdk=None, home=None):
    safe_name(name)
    sdk, info, _ = sdk_info(sdk)
    home = _plain(home or HOME)
    home.mkdir(parents=True, exist_ok=True)
    lock = OsLock(home / '.prepare.lock')
    if not lock.try_acquire({}, tries=21):
        raise FinalError('Ein AVD wird bereits vorbereitet.')
    try:
        folder = _plain(home / (name + '.avd'))
        index = home / (name + '.ini')
        if folder.exists() or index.exists():
            return verify_profile(name, sdk=sdk, home=home)
        folder.mkdir()
        config = ('AvdId=' + name + '\navd.ini.encoding=UTF-8\n'
                  'abi.type=x86_64\nhw.cpu.arch=x86_64\nhw.cpu.ncore=2\n'
                  'hw.ramSize=2048\nvm.heapSize=256\nhw.lcd.width=1080\n'
                  'hw.lcd.height=1920\nhw.lcd.density=420\nhw.keyboard=yes\n'
                  'hw.gpu.enabled=yes\nhw.gpu.mode=auto\nhw.mainKeys=no\n'
                  'hw.audioInput=no\nhw.audioOutput=no\n'
                  'hw.camera.back=none\nhw.camera.front=none\n'
                  'disk.dataPartition.size=6G\nPlayStore.enabled=true\n'
                  'tag.id=google_apis_playstore\ntag.display=Google Play\n'
                  'image.sysdir.1=' + str(sdk / IMAGE).replace('\\', '/') + '/\n'
                  'fastboot.forceColdBoot=yes\nfastboot.forceFastBoot=no\n'
                  'showDeviceFrame=no\n')
        (folder / 'config.ini').write_text(config, encoding='utf8')
        index.write_text('avd.ini.encoding=UTF-8\npath=' + str(folder) + '\ntarget=android-35\n', encoding='utf8')
        (folder / 'socialscraper-owned.json').write_text(json.dumps({
            'schema': 1, 'name': name, 'sdk': str(sdk), 'directory': str(folder),
            'image': IMAGE, 'fresh_profile': True,
        }, indent=2), encoding='utf8')
        return verify_profile(name, sdk=sdk, home=home)
    finally:
        lock.release()


def _ini(path):
    values = {}
    for line in path.read_text(encoding='utf8').splitlines():
        if line.strip() and not line.startswith(('#', ';')):
            key, separator, value = line.partition('=')
            if not separator or key in values:
                raise FinalError('AVD-Konfiguration ist nicht eindeutig.')
            values[key] = value
    return values


def verify_profile(name, *, sdk=None, home=None):
    safe_name(name)
    sdk = _plain(sdk or SDK)
    home = _plain(home or HOME)
    folder = _plain(home / (name + '.avd'))
    try:
        owner = json.loads(_plain(folder / 'socialscraper-owned.json').read_text(encoding='utf8'))
        index = _ini(_plain(home / (name + '.ini')))
        config = _ini(_plain(folder / 'config.ini'))
        if (owner.get('schema') != 1 or owner.get('name') != name or
                owner.get('sdk') != str(sdk) or owner.get('directory') != str(folder) or
                owner.get('image') != IMAGE or owner.get('fresh_profile') is not True or
                Path(index.get('path', '')).resolve() != folder or
                Path(config.get('image.sysdir.1', '')).resolve() != sdk / IMAGE or
                config.get('abi.type') != 'x86_64' or config.get('AvdId') != name):
            raise ValueError('ownership')
    except (OSError, ValueError):
        raise FinalError('Das AVD-Profil ist nicht als eigenes Gerät bestätigt.') from None
    return folder


def verify_guard(sdk=None):
    sdk, _, _ = sdk_info(sdk)
    if os.name != 'nt':
        raise FinalError('Die AVD-Netzwerksperre ist bisher nur für Windows vorbereitet.')
    # Windows PowerShell must rebuild its own built-in module paths. Inheriting
    # PowerShell 7's PSModulePath can hide Get-FileHash before verification starts.
    child_env = {key: value for key, value in os.environ.items() if key.casefold() != 'psmodulepath'}
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(Path(__file__).with_name('avd_guard.ps1')),
                                 '-SdkRoot', str(sdk), '-VerifyOnly'],
                                capture_output=True, timeout=45, creationflags=0x08000000,
                                env=child_env)
    except (OSError, subprocess.TimeoutExpired):
        raise FinalError('AVD-Netzwerksperre konnte nicht geprüft werden; Start gesperrt.') from None
    if result.returncode != 0:
        raise FinalError('Die Windows-Netzwerksperre für AVD fehlt oder ist unwirksam; Start gesperrt.')
    return True
