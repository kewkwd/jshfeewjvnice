"""Local password supply with durable, ordered, one-owner reservations."""
from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import string
import threading

from . import accountvault
from .runlock import OsLock

MODES = {'generated': 'Automatisch erzeugen', 'list': 'Nächstes Passwort aus der Liste'}
_LOCK = threading.RLock()


def _path(db_path):
    return Path(db_path).parent / 'password_supply.json'


@contextmanager
def _locked(db_path):
    with _LOCK:
        lock = OsLock(_path(db_path).with_suffix('.lock'))
        if not lock.try_acquire({}, tries=21):
            raise ValueError('Der Passwortvorrat wird gerade verwendet. Bitte kurz warten.')
        try:
            yield
        finally:
            lock.release()


def _valid_password(value, minimum=1):
    return (isinstance(value, str) and minimum <= len(value) <= 128
            and all(32 <= ord(c) <= 126 for c in value))


def _read(db_path):
    try:
        data = json.loads(_path(db_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'version': 1, 'mode': 'generated', 'entries': []}
    except (OSError, ValueError):
        raise ValueError('Der Passwortvorrat ist nicht lesbar; nichts überschrieben.') from None
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('mode'), str) or data['mode'] not in MODES
            or not isinstance(data.get('entries'), list)):
        raise ValueError('Der Passwortvorrat hat ein unbekanntes Format; nichts überschrieben.')
    values, owners = set(), set()
    for entry in data['entries']:
        if (not isinstance(entry, dict) or not _valid_password(entry.get('value'))
                or 'owner' not in entry or entry.get('source') not in ('list', 'generated')
                or (entry.get('owner') is not None and not _valid_owner(entry['owner']))):
            raise ValueError('Ein Eintrag im Passwortvorrat ist beschädigt; nichts überschrieben.')
        value, owner = entry['value'], entry['owner']
        if value in values or (owner is not None and owner in owners):
            raise ValueError('Der Passwortvorrat enthält doppelte Zuordnungen; nichts überschrieben.')
        if owner is None and entry['source'] != 'list':
            raise ValueError('Der Passwortvorrat enthält eine unvollständige Zuordnung.')
        values.add(value)
        if owner is not None:
            owners.add(owner)
    return data


def _valid_owner(owner):
    return (isinstance(owner, str) and 1 <= len(owner) <= 256
            and all(ord(c) >= 32 and ord(c) != 127 for c in owner))


def generate():
    """Independent random characters, without a fixed word or suffix."""
    groups = (string.ascii_lowercase, string.ascii_uppercase, string.digits, '!@#$%*-_+=?')
    length = 18 + secrets.randbelow(3)
    chars = [secrets.choice(group) for group in groups]
    alphabet = ''.join(groups)
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


def parse_lines(text):
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise ValueError('Bitte höchstens 10.000 Passwörter auf einmal einfügen.')
    # Only CR/LF are separators; never trim or silently alter a password.
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    values = []
    for number, value in enumerate(lines, 1):
        if not value:
            continue
        if not _valid_password(value, minimum=12) or value != value.strip():
            raise ValueError(f'Zeile {number}: 12 bis 128 druckbare ASCII-Zeichen verwenden, ohne Leerzeichen am Rand.')
        values.append(value)
    if len(values) > 10_000:
        raise ValueError('Bitte höchstens 10.000 Passwörter auf einmal einfügen.')
    return values


def _stored_passwords(db_path):
    vault = accountvault.load(db_path)
    records = [*vault['konten'].values(), *vault['postfaecher'], vault['postfach']]
    return {record[key] for record in records for key in ('passwort', 'email_passwort')
            if isinstance(record.get(key), str) and record[key]}


def configure(db_path, mode, text=''):
    """Append in pasted order, retaining every existing reservation."""
    if mode not in MODES:
        raise ValueError('Bitte eine angebotene Passwortquelle auswählen.')
    values = parse_lines(text)
    with _locked(db_path):
        data = _read(db_path)
        known = {entry['value'] for entry in data['entries']} | _stored_passwords(db_path)
        added = 0
        for value in values:
            if value not in known:
                data['entries'].append({'value': value, 'source': 'list', 'owner': None})
                known.add(value)
                added += 1
        data['mode'] = mode
        accountvault._write(_path(db_path), data)
        return {'added': added, 'duplicates': len(values) - added}


def summary(db_path):
    """Counts only; no password is returned to logs or list views."""
    with _locked(db_path):
        data = _read(db_path)
        stored = _stored_passwords(db_path)
        return {'mode': data['mode'],
                'available': sum(e['owner'] is None and e['value'] not in stored for e in data['entries']),
                'assigned': sum(e['owner'] is not None for e in data['entries'])}


def reserve(db_path, owner):
    if not _valid_owner(owner):
        raise ValueError('Ungültige Zuordnung für den Passwortvorrat.')
    with _locked(db_path):
        data = _read(db_path)
        existing = next((e for e in data['entries'] if e['owner'] == owner), None)
        if existing is not None:
            return existing['value']
        stored = _stored_passwords(db_path)
        if data['mode'] == 'list':
            entry = next((e for e in data['entries'] if e['owner'] is None and e['value'] not in stored), None)
            if entry is None:
                raise ValueError('Der Passwortvorrat ist leer. Unter „Passwortvorrat“ weitere Passwörter hinzufügen '
                                 'oder ausdrücklich „Automatisch erzeugen“ auswählen.')
            entry['owner'] = owner
        else:
            known = {e['value'] for e in data['entries']} | stored
            value = generate()
            while value in known:
                value = generate()
            entry = {'value': value, 'source': 'generated', 'owner': owner}
            data['entries'].append(entry)
        # Store before any form filling. Failed registrations never free a slot.
        accountvault._write(_path(db_path), data)
        return entry['value']


def profile_password(db_path, identity, explicit=None):
    """Caller holds the global operation lock when updating account secrets."""
    from .net.identity import validate_name
    validate_name(identity)
    old = accountvault.get(db_path, identity)
    saved = old.get('passwort')
    if saved:
        if not isinstance(saved, str):
            raise ValueError('Das gespeicherte Profilpasswort hat ein ungültiges Format; nichts überschrieben.')
        if explicit is not None and explicit != saved:
            raise ValueError('Für dieses Profil ist bereits ein anderes Passwort gespeichert; nichts überschrieben.')
        return saved
    if explicit is not None:
        if (not isinstance(explicit, str) or not 1 <= len(explicit) <= 256
                or any(ord(c) < 32 or ord(c) == 127 for c in explicit)):
            raise ValueError('Das Passwort muss 1 bis 256 Zeichen ohne Steuerzeichen enthalten.')
        value = explicit
    else:
        value = reserve(db_path, 'profile:' + identity)
    fields = {'passwort': value}
    if not old.get('notiz'):
        fields['notiz'] = 'Passwort vorbereitet; Kontoabschluss noch nicht bestätigt.'
    accountvault.put(db_path, identity, **fields)
    return value
