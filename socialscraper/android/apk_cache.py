"""Install pinned local application packages; never download during AVD boot."""
import hashlib
import json
import logging
from pathlib import Path
import re
from ..net.errors import FinalError

APPS = {
    'webde': ('de.web.mobile.android.mail', 'b46cbc7870b38ff8ee5d068413812808a7408f7114b97cec73e3e5c7e39c916a'),
    'tiktok': ('com.zhiliaoapp.musically', '9041803e91bcb814b4b4399fb5c85a91640b755e5e8ba76813814bf4cf2ab5ba'),
}
log = logging.getLogger(__name__)

def files(root, app):
    """Validate the locally reviewed package index and every installation file."""
    package, signer = APPS[app]
    folder = Path(root) / app
    manifest = folder / 'manifest.json'
    if not manifest.exists():return None
    try:
        data = json.loads(manifest.read_text(encoding='utf-8-sig'))
        if (data.get('version') != 1 or data.get('package') != package or
                data.get('signer_sha256') != signer):raise ValueError()
        records = data['apks']
        if not isinstance(records, list) or not 1 <= len(records) <= 32:raise ValueError()
        paths = []
        versions = set()
        for item in records:
            relative = Path(item['file'])
            if relative.is_absolute() or '..' in relative.parts:raise ValueError()
            path = folder / relative
            if (path.suffix != '.apk' or not path.resolve().is_relative_to(folder.resolve()) or
                    any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))):raise ValueError()
            if item.get('signer_sha256') != signer or not re.fullmatch('[0-9a-f]{64}',item['sha256']):raise ValueError()
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:raise ValueError()
            versions.add(str(item['version_code']))
            paths.append(path.resolve())
        if len(versions) != 1 or len(set(paths)) != len(paths):raise ValueError()
        version = versions.pop()
        if not version.isdecimal():raise ValueError()
        return package, version, paths
    except (ValueError, KeyError, TypeError, OSError, AttributeError):
        raise FinalError('Der lokale APK-Vorrat für '+app+' ist unvollständig oder verändert. Keine Installation gestartet.') from None

def ensure(adb, root, app):
    cached = files(root, app)
    if cached is None:return None
    package, version, paths = cached
    installed = adb.sh('pm', 'list', 'packages', package, timeout=20).splitlines()
    if 'package:'+package in installed:
        # Preserve the app and all profile data. Updates are a separate action.
        return {'app':app, 'package':package, 'installed_now':False}
    args = ('install', '-r', str(paths[0])) if len(paths) == 1 else ('install-multiple', '-r', *(str(p) for p in paths))
    log.info('Installiere %s aus dem lokalen APK-Vorrat.',app)
    result = adb._cmd(*args, timeout=300)
    if 'Success' not in result.splitlines():
        raise FinalError('Android hat die lokale APK-Installation für '+app+' nicht bestätigt.')
    installed = adb.sh('pm', 'list', 'packages', package, timeout=20).splitlines()
    if 'package:'+package not in installed:
        raise FinalError('Das installierte Android-Paket für '+app+' konnte nicht bestätigt werden.')
    details = adb.sh('dumpsys', 'package', package, timeout=20)
    if not re.search(r'\bversionCode='+re.escape(version)+r'\b',details):
        raise FinalError('Die installierte Version von '+app+' passt nicht zum lokalen APK-Vorrat.')
    return {'app':app, 'package':package, 'installed_now':True}

def ensure_all(adb, root):
    return [result for app in APPS if (result := ensure(adb, root, app)) is not None]
