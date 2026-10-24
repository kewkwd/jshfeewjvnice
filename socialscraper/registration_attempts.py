"""Persistent mailbox drafts, protected like the account vault, never pool entries."""
import hashlib
import json
import re
import secrets
from pathlib import Path
from . import accountvault
from .operation import exclusive

FILE_NAME = 'registration_attempts.json'


def _path(db_path):
    return Path(db_path).parent / FILE_NAME


def _load(db_path):
    try:
        data = json.loads(_path(db_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'version': 1, 'drafts': {}}
    except (OSError, ValueError):
        raise ValueError('Gespeicherte Registrierungsversuche sind nicht lesbar; bitte die Sicherung prüfen.') from None
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('drafts'), dict)):
        raise ValueError('Gespeicherte Registrierungsversuche haben ein unbekanntes Format.')
    for key, item in data['drafts'].items():
        if (not re.fullmatch(r'[a-f0-9]{64}', key) or not isinstance(item, dict)
                or not all(isinstance(item.get(k), str) and item[k] for k in ('provider', 'address', 'password', 'identity'))
                or not re.fullmatch(r'[a-f0-9]{6}', item['identity'])
                or item['address'].count('@') != 1 or item['address'].rpartition('@')[2].casefold() != item['provider']
                or not item['address'].partition('@')[0]
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in item['address'])
                or not isinstance(item.get('options'), dict)
                or item.get('status', 'prepared') not in ('prepared', 'running', 'failed', 'awaiting_mail', 'needs_review')):
            raise ValueError('Ein Registrierungsversuch ist beschädigt; kein neues Profil angelegt.')
        if item.get('status') == 'awaiting_mail' and not _valid_completion(item,item.get('completion')):
            raise ValueError('Die gespeicherte Abschlussprüfung ist beschädigt; keine neue Registrierung gestartet.')
    return data


def _valid_completion(draft, completion):
    if not isinstance(completion,dict) or not isinstance(completion.get('entry'),dict):return False
    entry=completion['entry'];address=entry.get('adresse');imap=entry.get('imap')
    if (not isinstance(address,str) or address.casefold()!=draft['address'].casefold()
            or entry.get('anbieter')!=draft['provider'] or
            not isinstance(entry.get('passwort'),str) or not entry['passwort'] or
            not isinstance(completion.get('proxy'),str) or not completion['proxy'] or
            not isinstance(entry.get('notizen'),list) or any(not isinstance(x,str) for x in entry['notizen']) or
            not isinstance(entry.get('erstellt_am'),str) or not isinstance(imap,dict)):
        return False
    if 'host' in imap and (not isinstance(imap['host'],str) or not imap['host'].strip()):return False
    if 'port' in imap and (type(imap['port']) is not int or not 1<=imap['port']<=65535):return False
    return True


def pending(db_path, provider, address=None):
    """Find a confirmed account without requiring its former browser options."""
    if address is not None and (not isinstance(address,str) or (address and not address.strip())):
        raise ValueError('Die Zieladresse muss gültiger Text sein.')
    wanted=address.strip() if isinstance(address,str) else None
    if wanted and '@' not in wanted:wanted+='@'+provider
    matches=[(key,item) for key,item in _load(db_path)['drafts'].items()
             if item['provider']==provider and item.get('status')=='awaiting_mail'
             and (not wanted or item['address'].casefold()==wanted.casefold())]
    if len(matches)>1:
        raise ValueError('Mehrere bestätigte Postfächer warten auf Mailprüfung. Bitte die Zieladresse ausdrücklich auswählen.')
    return matches[0] if matches else None


def get_or_create(db_path, provider, *, address, password, identity, explicit_address,
                  explicit_password, details, route, country, mobile, engine='patchright', profile_identity=None,
                  password_factory=None):
    """Caller holds the operation lock. Keep credentials; retire failed identities."""
    # Editing a postcode/name must not silently change the account or profile.
    selector = {'provider': provider, 'address': address.casefold() if explicit_address else None}
    options = {'route': route, 'country': country.lower(), 'mobile': bool(mobile), 'engine': engine}
    if profile_identity is not None:
        from .net.identity import validate_name
        options['profile_identity'] = validate_name(profile_identity)
    key = hashlib.sha256(json.dumps(selector, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    data = _load(db_path)
    draft = data['drafts'].get(key)
    if draft is None and explicit_address:
        matches = [(saved_key, item) for saved_key, item in data['drafts'].items()
                   if item['provider'] == provider and item['address'].casefold() == address.casefold()]
        if len(matches) > 1:
            raise ValueError('Mehrere offene Versuche für diese Adresse gefunden. Bitte die gespeicherten Versuche prüfen.')
        if matches:
            key, draft = matches[0]
    if draft:
        if draft['options'] != options:
            raise ValueError('Der offene Versuch verwendet andere Profiloptionen. '
                             'Mit den bisherigen Optionen fortsetzen oder den Versuch bewusst zurücksetzen.')
        if explicit_password and password != draft['password']:
            raise ValueError('Für diesen Versuch ist bereits ein anderes Kennwort hinterlegt. '
                             'Den bestehenden Versuch fortsetzen oder unter Postfächer bewusst zurücksetzen.')
        if draft.get('status') in ('running','needs_review'):
            raise ValueError('Der Kontoabschluss eines unterbrochenen Versuchs ist unklar. '
                             'Bitte den letzten Stand prüfen und ein bereits erstelltes Postfach übernehmen '
                             'oder den Versuch bewusst zurücksetzen; keine automatische neue Registrierung.')
        # Older drafts have no completion evidence; do not resume their browser storage.
        if draft.get('status', 'failed') == 'failed':
            if profile_identity is not None:
                raise ValueError('Der vorherige Versuch mit diesem Profil ist fehlgeschlagen. Bitte zuerst den gespeicherten Stand prüfen; keine automatische Wiederholung.')
            if route == 'browser':
                previous = draft['identity']
                while identity == previous:
                    identity = secrets.token_hex(3)
                draft['identity'] = identity
            draft['status'] = 'prepared'
            accountvault._write(_path(db_path), data)
        return key, dict(draft), True
    if not password and password_factory is not None:
        password = password_factory(key + ':' + identity)
    if not isinstance(password, str) or not password:
        raise ValueError('Für den neuen Versuch fehlt ein gespeichertes Passwort.')
    draft = {'provider': provider, 'address': address, 'password': password, 'identity': identity, 'options': options,
             'status': 'prepared'}
    data['drafts'][key] = draft
    accountvault._write(_path(db_path), data)
    return key, dict(draft), False


def status(db_path, key, value):
    if value not in ('prepared', 'running', 'failed', 'needs_review'):raise ValueError('Ungültiger Versuchsstatus.')
    data = _load(db_path)
    data['drafts'][key]['status'] = value
    accountvault._write(_path(db_path), data)


def confirmed(db_path, key, entry, proxy):
    """Durably checkpoint account evidence before closing its browser/device."""
    data = _load(db_path)
    draft = data['drafts'][key]
    completion={'entry':entry,'proxy':proxy.url if proxy is not None else None}
    if not _valid_completion(draft,completion):
        raise ValueError('Der bestätigte Kontoabschluss passt nicht zum gespeicherten Versuch.')
    draft.update(status='awaiting_mail', completion=completion)
    accountvault._write(_path(db_path), data)


def completed(db_path, key):
    data = _load(db_path)
    if key in data['drafts']:
        del data['drafts'][key]
        accountvault._write(_path(db_path), data)


@exclusive
def reset(db_path, provider):
    """Explicit user action; old browser files remain available for recovery."""
    data = _load(db_path)
    data['drafts'] = {key: item for key, item in data['drafts'].items() if item['provider'] != provider}
    accountvault._write(_path(db_path), data)
