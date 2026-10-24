"""Manual fixed proxy assignment shared by every account of one profile."""
import json
from pathlib import Path
import sqlite3

from . import accountproxy
from .identity import validate_name
from .proxy import Proxy


def _entries(cfg):
    try:
        data = json.loads(accountproxy.path_for(cfg.db_path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError('Die vorhandenen Proxy-Zuordnungen sind nicht lesbar. Es wurde nichts überschrieben.') from None
    if (not isinstance(data, dict) or data.get('schema_version') != 1
            or not isinstance(data.get('proxies'), dict)
            or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                   for k, v in data['proxies'].items())):
        raise ValueError('Die gespeicherten Proxy-Zuordnungen sind beschädigt. Es wurde nichts überschrieben.')
    return data['proxies']


def options(cfg, identity):
    """Raw values stay internal; the UI must use only the masked labels."""
    validate_name(identity)
    current = _entries(cfg).get(identity)
    choices = []
    seen = set()
    for raw, title in ([(current, 'Aktuell')] if current else []) + [
            (raw, f'Liste {index}') for index, raw in enumerate(getattr(cfg, 'proxies', []), 1)]:
        if raw in seen:
            continue
        seen.add(raw)
        choices.append((raw, title + ': ' + accountproxy.mask(raw)))
    return choices


def display(cfg, identity):
    raw = _entries(cfg).get(validate_name(identity))
    return accountproxy.mask(raw) if raw else 'Kein fester Profilproxy zugeordnet'


def assign(cfg, identity, raw):
    """Persist credentials with a Windows user ACL, database gets a masked ref.

    No networking, rotation, profile/person edits or changes to open sessions.
    The global operation lock prevents writes during managed browser jobs.
    """
    from ..accountvault import _write
    from .. import runlock
    validate_name(identity)
    raw = accountproxy.normalize(raw)
    parsed = Proxy.parse(raw)
    if parsed.password == accountproxy.MASK:
        raise ValueError('Eine maskierte Anzeige enthält kein gültiges Passwort. Bitte die vollständige Proxy-Adresse verwenden.')
    shown = accountproxy.mask(parsed)
    path = accountproxy.path_for(cfg.db_path)
    with runlock.acquire(runlock.root_for_db(cfg.db_path), 'gui'), accountproxy._LOCK:
        old = _entries(cfg)
        updated = {**old, identity: raw}
        db = None
        written = False
        try:
            # Do not create an empty database for a prepared profile.
            db_path = Path(cfg.db_path).resolve()
            if db_path.is_file():
                db = sqlite3.connect(db_path.as_uri() + '?mode=rw', uri=True, timeout=2)
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE accounts SET proxy=? WHERE identity=?', (shown, identity))
            _write(path, {'schema_version': 1, 'proxies': updated})
            written = True
            if db is not None:
                db.commit()
        except (OSError, sqlite3.Error):
            if db is not None:
                db.rollback()
            if written:
                try:
                    _write(path, {'schema_version': 1, 'proxies': old})
                except OSError:
                    raise ValueError('Die Zuordnung konnte nicht vollständig gespeichert oder zurückgesetzt werden. '
                                     'Bitte vor dem nächsten Start den festen Profilproxy erneut festlegen.') from None
            raise ValueError('Der Profilproxy konnte nicht gespeichert werden. Bitte Dateizugriff und Kontendatenbank prüfen.') from None
        finally:
            if db is not None:
                db.close()
        accountproxy._remember(parsed)
    return shown
