"""Explicit offline relocation of an exact copied AVD, with both emulators stopped.

This changes only owned path metadata. It never boots Android or changes the
Windows network boundary. The original profile is read only.
"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build.repair_environment import plain, replace_files
from socialscraper.android import avd
from socialscraper.net.errors import FinalError
from socialscraper.runlock import OsLock


def ensure_stopped():
    if os.name != 'nt':
        raise ValueError('Diese lokale AVD-Umzugshilfe benötigt Windows.')
    result = subprocess.run(['tasklist.exe', '/FO', 'CSV', '/NH'], capture_output=True,
                            timeout=15, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    # Executable names are ASCII even on a localized Windows installation.
    rows = csv.reader(io.StringIO(result.stdout.decode('utf8', errors='replace')))
    if any(row and (row[0].lower() == 'emulator.exe' or row[0].lower().startswith('qemu-system-')) for row in rows):
        raise ValueError('Vor dem Umzug alle Android-Emulatoren beenden.')


def rebased(data, source, destination):
    """Rebase complete root path prefixes only, preserving every other value."""
    text = data.decode('utf8')
    # Windows INI paths may use either separator. Do not rewrite prefix siblings.
    pattern = r'[\\/]'.join(re.escape(part) for part in str(source).replace('\\', '/').split('/'))
    text = re.sub(pattern + r'(?=[\\/]|$)', lambda _: str(destination).replace('\\', '/'),
                  text, flags=re.IGNORECASE | re.MULTILINE)
    if str(source).replace('\\', '/').casefold() in text.replace('\\', '/').casefold():
        raise ValueError('Unbekannter alter Pfad in AVD-Metadaten.')
    return text.encode('utf8')


def relocate(source, destination, name=avd.TEST_NAME):
    source, destination = plain(source), plain(destination)
    avd.safe_name(name)
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('Quell- und Zielinstallation müssen getrennte Ordner sein.')
    ensure_stopped()
    source_sdk, target_sdk = source / 'tools/android-sdk', destination / 'tools/android-sdk'
    source_home, target_home = source / 'data/android-avd', plain(destination / 'data/android-avd')
    source_folder = avd.verify_profile(name, sdk=source_sdk, home=source_home)
    target_sdk, _, _ = avd.sdk_info(target_sdk)
    try:
        avd.verify_profile(name, sdk=target_sdk, home=target_home)
        return 0
    except FinalError:
        pass
    lock = OsLock(target_home / '.session.lock')
    preparation = OsLock(target_home / '.prepare.lock')
    if not lock.try_acquire({'owner': 'offline-relocation'}):
        raise ValueError('Im Ziel läuft bereits ein Android-Auftrag.')
    try:
        if not preparation.try_acquire({'owner': 'offline-relocation'}):
            raise ValueError('Im Ziel wird bereits ein Android-Profil vorbereitet.')
        target_folder = plain(target_home / (name + '.avd'))
        relatives = [Path(name + '.ini'), Path(name + '.avd/config.ini'),
                     Path(name + '.avd/socialscraper-owned.json')]
        if (source_folder / 'hardware-qemu.ini').exists() or (target_folder / 'hardware-qemu.ini').exists():
            relatives.append(Path(name + '.avd/hardware-qemu.ini'))
        changes = {}
        for relative in relatives:
            old = plain(source_home / relative).read_bytes()
            target = plain(target_home / relative)
            if target.read_bytes() != old:
                raise ValueError('AVD-Metadaten sind keine unveränderte eigene Kopie.')
            if relative.name == 'socialscraper-owned.json':
                owner = json.loads(old)
                owner['sdk'], owner['directory'] = str(target_sdk), str(target_folder)
                changes[target] = json.dumps(owner, indent=2).encode('utf8')
            else:
                changes[target] = rebased(old, source, destination)
        return replace_files(changes, lambda: avd.verify_profile(name, sdk=target_sdk, home=target_home))
    finally:
        preparation.release()
        lock.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--name', default=avd.TEST_NAME)
    args = parser.parse_args()
    count = relocate(args.source, ROOT, args.name)
    print(f'AVD-Metadaten lokal geprüft; {count} Dateien umgezogen. Kein Emulatorstart.')
