"""AVD-only entry point; no arbitrary emulator or inherited ADB connection."""
from contextlib import contextmanager
from . import avd as owned
from ..net.errors import FinalError
TIKTOK_PAKET = 'com.zhiliaoapp.musically'

def emulator_pfad():
    sdk, _, _ = owned.sdk_info()
    return str(sdk / 'emulator/emulator.exe')

def liste():
    if not owned.HOME.is_dir():
        return []
    result = []
    for index in owned.HOME.glob('ss_*.ini'):
        try:
            owned.verify_profile(index.stem)
        except FinalError:
            continue
        result.append(index.stem)
    return sorted(result)

def starten(*args, **kwargs):
    raise FinalError('AVD nur innerhalb einer geprüften eigenen Sitzung mit mit_geraet starten.')

def verbinden(*args, **kwargs):
    raise FinalError('Fremde oder bereits laufende Android-Geräte werden nicht übernommen.')

def stoppen(*args, **kwargs):
    raise FinalError('AVD wird ausschließlich vom Besitzer seiner Sitzung beendet.')

def vorbereiten(adb, *, proxy=None):
    session = getattr(adb, '_socialscraper_avd_session', None)
    if session is None:
        raise FinalError('Keine geprüfte eigene AVD-Sitzung vorhanden.')
    session.check()
    # Session configures the transport below Android. Keep existing app data.
    if not adb.sh('pm', 'path', TIKTOK_PAKET, timeout=5).strip().startswith('package:'):
        raise FinalError('TikTok ist auf diesem eigenen AVD noch nicht installiert.')
    session.check()
    return adb.modell()

@contextmanager
def mit_geraet(avd=None, *, proxy=None, serial=None, headless=True, stop_requested=None):
    if serial is not None:
        raise FinalError('Eine fremde ADB-Verbindung wird nicht übernommen.')
    from .avd_session import Session
    with Session(avd, proxy, headless=headless, stop_requested=stop_requested) as adb:
        yield adb
