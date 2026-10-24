"""Durable signup attempt guard; create() holds the shared operation lock."""
import hashlib
import json
from pathlib import Path
import re
import time

from . import accountvault
from .net.errors import FinalError
from .net.identity import validate_name

FILE_NAME = 'platform_signup_attempts.json'
STATES = {'running', 'profile_seen', 'session_seen', 'stored'}
PLATFORMS = {'tiktok', 'reddit', 'instagram', 'x', 'google', 'youtube', 'trustpilot'}


def _path(db_path):
    return Path(db_path).parent / FILE_NAME


def _email_hash(email):
    if (not isinstance(email, str) or not email or len(email) > 254
            or email != email.strip() or not email.isascii()
            or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)
            or any(ord(char) < 33 or ord(char) > 126 for char in email)):
        raise FinalError('Die gespeicherte E-Mail-Adresse ist unvollständig oder wird für diesen Registrierungsweg nicht unterstützt. Bitte den Postfachzugang prüfen.')
    return hashlib.sha256(email.casefold().encode('utf-8')).hexdigest()


def _key(identity, platform='tiktok'):
    if platform not in PLATFORMS:
        raise ValueError('Unbekannte Registrierungsplattform.')
    return platform + ':' + validate_name(identity)


def _load(db_path):
    try:
        data = json.loads(_path(db_path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {'version': 1, 'attempts': {}}
    except (OSError, ValueError):
        raise FinalError('Der gespeicherte Registrierungsstand ist nicht lesbar. Bitte die Sicherung prüfen; keine neue Anmeldung gestartet.') from None
    try:
        if (not isinstance(data, dict) or set(data) != {'version', 'attempts'}
                or type(data['version']) is not int or data['version'] != 1 or not isinstance(data['attempts'], dict)):
            raise ValueError
        for key, item in data['attempts'].items():
            platform = key.partition(':')[0]
            if (not isinstance(item, dict)
                    or set(item) != {'identity', 'email_hash', 'status', 'handle', 'created_at', 'updated_at'}
                    or not isinstance(item['identity'], str) or key != _key(item['identity'], platform)
                    or not isinstance(item['email_hash'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['email_hash'])
                    or not isinstance(item['status'], str) or item['status'] not in STATES
                    or not isinstance(item['handle'], str)
                    or any(type(item[k]) is not int or item[k] <= 0 for k in ('created_at', 'updated_at'))):
                raise ValueError
            if item['status'] == 'running' or platform != 'tiktok':
                if item['handle']:
                    raise ValueError
            elif accountvault.normalize_tiktok_username(item['handle'], required=True) != item['handle']:
                raise ValueError
            if (platform == 'tiktok' and item['status'] == 'session_seen'
                    or platform != 'tiktok' and item['status'] == 'profile_seen'):
                raise ValueError
    except (ValueError, TypeError, KeyError):
        raise FinalError('Der gespeicherte Registrierungsstand ist beschädigt. Bitte die Sicherung prüfen; keine neue Anmeldung gestartet.') from None
    return data


def ensure_available(db_path, identity, email=None, *, platform='tiktok'):
    key = _key(identity, platform)
    wanted = _email_hash(email) if email is not None else None
    for saved_key, item in _load(db_path)['attempts'].items():
        if not saved_key.startswith(platform + ':'):
            continue
        if saved_key == key or (wanted is not None and item['email_hash'] == wanted):
            raise FinalError('Für dieses Profil oder diese E-Mail-Adresse wurde bei dieser Plattform bereits eine Registrierung gestartet. '
                             'Bitte den bestehenden Stand prüfen und einen vorhandenen Zugang übernehmen. '
                             'Der bisherige Versuch bleibt erhalten; eine neue Registrierung wurde nicht gestartet.')


def begin(db_path, identity, email, *, platform='tiktok'):
    ensure_available(db_path, identity, email, platform=platform)
    data = _load(db_path)
    now = int(time.time())
    data['attempts'][_key(identity, platform)] = {'identity': identity, 'email_hash': _email_hash(email),
        'status': 'running', 'handle': '', 'created_at': now, 'updated_at': now}
    # This must reach disk before entering the provider form. No automatic reset
    # after an exception: a crash can hide a successful provider-side action.
    accountvault._write(_path(db_path), data)


def profile_seen(db_path, identity, email, handle):
    handle = accountvault.normalize_tiktok_username(handle, required=True)
    data = _load(db_path)
    item = data['attempts'].get(_key(identity))
    if item is None or item['email_hash'] != _email_hash(email) or item['status'] != 'running':
        raise FinalError('Das erkannte TikTok-Profil passt nicht zum gespeicherten Versuch. Bitte den vorhandenen Stand prüfen.')
    item.update(status='profile_seen', handle=handle, updated_at=int(time.time()))
    accountvault._write(_path(db_path), data)


def session_seen(db_path, identity, email, *, platform):
    if platform == 'tiktok':
        raise ValueError('TikTok benötigt den beobachteten Profilnamen.')
    data = _load(db_path)
    item = data['attempts'].get(_key(identity, platform))
    if item is None or item['email_hash'] != _email_hash(email) or item['status'] != 'running':
        raise FinalError('Die erkannte Browsersitzung passt nicht zum gespeicherten Versuch. Bitte den bestehenden Stand prüfen.')
    item.update(status='session_seen', updated_at=int(time.time()))
    accountvault._write(_path(db_path), data)


def stored(db_path, identity, email, handle='', *, platform='tiktok'):
    data = _load(db_path)
    item = data['attempts'].get(_key(identity, platform))
    expected_status = 'profile_seen' if platform == 'tiktok' else 'session_seen'
    expected_handle = accountvault.normalize_tiktok_username(handle, required=True) if platform == 'tiktok' else ''
    if (item is None or item['email_hash'] != _email_hash(email) or item['status'] != expected_status
            or item['handle'] != expected_handle):
        raise FinalError('Die Speicherung des Zugangs ist noch nicht vollständig bestätigt. Bitte den vorhandenen Stand prüfen.')
    item.update(status='stored', updated_at=int(time.time()))
    accountvault._write(_path(db_path), data)
