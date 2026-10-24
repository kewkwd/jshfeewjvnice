"""Bounded, read-only checks inside an already owned and guarded AVD session."""
from datetime import datetime, timezone
import hashlib
import json
import re
from ..jsonfiles import write_report
import time

from . import avd
from ..operation import check_cancelled, positive_timeout

PACKAGES = {'tiktok':'com.zhiliaoapp.musically', 'webde':'de.web.mobile.android.mail', 'play_services':'com.google.android.gms',
            'play_store':'com.android.vending'}
PROPERTIES = {'boot':'sys.boot_completed', 'sdk':'ro.build.version.sdk',
              'android':'ro.build.version.release', 'model':'ro.product.model',
              'locale':'persist.sys.locale', 'timezone':'persist.sys.timezone'}


def _text(value):
    if not isinstance(value, str):raise ValueError('Ungültige Android-Diagnose.')
    value = value.strip()
    if len(value) > 120 or any(ord(c) < 32 for c in value):
        raise ValueError('Ungültige Android-Diagnose.')
    return value or None


def capture(session, *, budget=25):
    deadline = time.monotonic() + positive_timeout(budget)
    session.check()
    report = {'version':1, 'captured_at':datetime.now(timezone.utc).isoformat(),
              'values':{}, 'packages':{}, 'unavailable':[], 'unavailable_reason':{},
              'transport_proxy_configured':session.bridge is not None,
              'external_connection':'not_tested'}

    def read(label, *command):
        session.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            report['unavailable'].append(label)
            report['unavailable_reason'][label] = 'budget'
            return None
        try:
            # Package manager may still be settling just after boot completed.
            maximum = 5 if command[0] == 'pm' else 2
            result = session.adb.sh(*command, timeout=min(maximum, remaining))
            if not isinstance(result, str):raise ValueError()
            session.check()
            return result.strip()
        except Exception as error:
            check_cancelled(session.stop)
            report['unavailable'].append(label)
            report['unavailable_reason'][label] = ('timeout' if 'hat zu lange gebraucht' in str(error)
                                                     else 'read_error')
            return None

    for label, prop in PROPERTIES.items():
        raw = read(label, 'getprop', prop)
        try:report['values'][label] = _text(raw) if raw is not None else None
        except ValueError:
            report['values'][label] = None
            report['unavailable'].append(label)
            report['unavailable_reason'][label] = 'invalid_value'
    raw = read('system_proxy', 'settings', 'get', 'global', 'http_proxy')
    report['system_proxy'] = ('unknown' if raw is None else 'unset' if raw in ('', 'null', ':0', '0') else 'configured')
    for label, package in PACKAGES.items():
        # pm path can fail for an absent app. A successfully read filtered
        # package list can be empty, and never exposes the private APK path.
        raw = read(label, 'pm', 'list', 'packages', package)
        lines = raw.splitlines() if raw is not None else None
        if lines is None or any(not re.fullmatch(r'package:[A-Za-z0-9_.]+', line) for line in lines):
            report['packages'][label] = None
        else:
            report['packages'][label] = ('package:' + package) in lines
    session.check()
    return report


def _paths(home, name):
    avd.safe_name(name)
    home = avd._plain(home)
    folder = avd._plain(home / (name + '.avd'))
    config = avd._plain(folder / 'config.ini')
    path = avd._plain(home / '_diagnostics' / (name + '.json'))
    return folder, config, path


def observe(session):
    folder, config, path = _paths(session.home, session.name)
    report = capture(session)
    report.update(profile_path=str(folder), config_sha256=hashlib.sha256(config.read_bytes()).hexdigest())
    write_report(path, report, prefix='avd-diagnostic-')
    return report


def summary(home, name):
    try:
        folder, config, path = _paths(home, name)
        data = json.loads(path.read_text(encoding='utf8'))
        if not isinstance(data, dict) or data.get('version') != 1:raise ValueError()
        if (data.get('profile_path') != str(folder)
                or data.get('config_sha256') != hashlib.sha256(config.read_bytes()).hexdigest()):
            return 'Android-Diagnose gehört zu einer älteren Profilkonfiguration.'
        stamp = datetime.fromisoformat(data['captured_at']).astimezone().strftime('%d.%m.%Y %H:%M:%S')
        values, packages = data['values'], data['packages']
        lines = ['Android gemessen: ' + stamp + ' · gespeicherter Stand, kein Live-Status']
        for label, key in (('Android','android'), ('SDK','sdk'), ('Modell','model'), ('Sprache','locale'), ('Zeitzone','timezone')):
            value = values.get(key)
            text = _text(value) if value is not None else None
            lines.append(label + ': ' + (text or 'Unbekannt'))
        boot = values.get('boot')
        lines.append('Start abgeschlossen: ' + ('Ja' if boot == '1' else 'Unbekannt' if boot is None else 'Nein'))
        transport = data.get('transport_proxy_configured')
        if type(transport) is not bool:raise ValueError()
        lines.append('Emulator-Proxy konfiguriert: ' + ('Ja' if transport else 'Nein'))
        system = {'unset':'Nicht gesetzt; vom Emulatortransport getrennt',
                  'configured':'Gesetzt (Adresse ausgeblendet)', 'unknown':'Unbekannt'}[data['system_proxy']]
        lines.append('Android-Systemproxy: ' + system)
        for label, key in (('TikTok-Paket','tiktok'), ('WEB.DE-Paket','webde'), ('Google Play-Dienste','play_services'), ('Google Play Store','play_store')):
            value = packages.get(key)
            if value is not None and type(value) is not bool:raise ValueError()
            state = 'Vorhanden' if value else 'Nicht vorhanden' if value is False else 'Unbekannt'
            reason = data.get('unavailable_reason', {}).get(key)
            if value is None and reason in ('budget', 'timeout', 'read_error'):
                state += ' · ' + {'budget':'Gesamtzeitlimit erreicht', 'timeout':'Abfragezeitlimit erreicht',
                                 'read_error':'Abfrage fehlgeschlagen'}[reason]
            lines.append(label + ': ' + state)
        lines.append('App-Herkunft, externe Verbindung und Kontoanmeldung nicht geprüft.')
        return '\n'.join(lines)
    except FileNotFoundError:
        return 'Noch keine Android-Laufzeitmessung gespeichert.'
    except Exception:
        return 'Android-Diagnose nicht lesbar oder Profilpfad umgeleitet.'
