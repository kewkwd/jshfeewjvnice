"""Locate the pinned Playwright Firefox without starting a driver or downloading."""
import importlib.metadata
import json
import os
from pathlib import Path
import sys

PLAYWRIGHT_VERSION = '1.64.0'
FIREFOX_REVISION = '1555'
FIREFOX_VERSION = '157.0'


def installed_binary():
    try:
        dist = importlib.metadata.distribution('playwright')
    except importlib.metadata.PackageNotFoundError:
        raise FileNotFoundError('Firefox benötigt das Paket Playwright ' + PLAYWRIGHT_VERSION + '.') from None
    if dist.version != PLAYWRIGHT_VERSION:
        raise ValueError('Diese Firefox-Anbindung benötigt Playwright ' + PLAYWRIGHT_VERSION + '.')
    package = Path(dist.locate_file('playwright/driver/package'))
    manifest = json.loads((package / 'browsers.json').read_text(encoding='utf8'))
    if not isinstance(manifest, dict) or not isinstance(manifest.get('browsers'), list):
        raise ValueError('Das Firefox-Runtime-Manifest ist beschädigt.')
    entries = [item for item in manifest['browsers'] if isinstance(item, dict) and item.get('name') == 'firefox']
    if len(entries) != 1:
        raise ValueError('Das Firefox-Runtime-Manifest ist unvollständig.')
    entry = entries[0]
    if str(entry['revision']) != FIREFOX_REVISION or entry['browserVersion'] != FIREFOX_VERSION:
        raise ValueError('Die Firefox-Runtime stimmt nicht mit der geprüften Version überein.')
    custom = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    # Keep the bundle with this Python runtime. A Store/MSIX host may virtualize
    # LocalAppData, making its cache invisible to the user's normal desktop app.
    if not custom or custom == '0':
        cache = package / '.local-browsers'
    else:
        cache = Path(custom).expanduser().resolve()
    suffix = {'win32': 'firefox/firefox.exe', 'darwin': 'firefox/Nightly.app/Contents/MacOS/firefox'}.get(
        sys.platform, 'firefox/firefox')
    binary = cache / ('firefox-' + FIREFOX_REVISION) / suffix
    if not binary.is_file():
        raise FileNotFoundError('Firefox ' + FIREFOX_VERSION + ' für Playwright fehlt. '
                                'Einrichtung über den konfigurierten Proxy erforderlich.')
    return binary


def installation_status():
    try:
        installed_binary()
    except (OSError, ValueError, KeyError, StopIteration) as error:
        return False, str(error) or 'Firefox-Runtime unvollständig.'
    return True, 'Firefox ' + FIREFOX_VERSION + ' (Playwright ' + PLAYWRIGHT_VERSION + ')'
