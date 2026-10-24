"""Read the dashboard's local inventory without opening devices or connections."""
import sqlite3

from .. import accountvault, guicore, profile_settings, repo
from ..net import proxymode
from ..profile_inventory import inventory
from ..runlock import root_for_db
from ..store import Store


def snapshot(cfg, account_state=None):
    result = {'profiles': [], 'accounts': [], 'mailboxes': [], 'warnings': [],
              'counts': {'profiles': None, 'accounts': None, 'mailboxes': None, 'videos': None},
              'state': {'anonymous': proxymode.is_anonymous(cfg),
                        'proxy_configured': bool(getattr(cfg, 'proxies', []))}}
    try:
        rows, warnings = inventory(cfg.profiles_dir, cfg.db_path)
        result['warnings'].extend(warnings)
        groups = {}
        for row in rows:
            key = 'device:' + row.android if row.identity == 'Nicht zugeordnet' else 'profile:' + row.identity
            groups.setdefault(key, []).append(row)
        for key, entries in groups.items():
            row = sorted(entries, key=lambda r: ('Älterer Versuch' in r.status, r.path))[0]
            valid = row.identity != 'Nicht zugeordnet'
            name = row.identity if valid else 'Android-Gerät ohne Profil'
            if valid:
                try:
                    person = profile_settings.load(cfg.profiles_dir, row.identity)['person']
                    name = ' '.join(filter(None, (person['vorname'], person['nachname']))) or name
                except (OSError, ValueError):
                    result['warnings'].append('Personendaten eines Profils konnten nicht gelesen werden.')
            android = list(dict.fromkeys(r.android for r in entries if r.android != 'Kein AVD vorhanden/zugeordnet'))
            states = list(dict.fromkeys(r.status for r in entries))
            stored_state = states[0] if len(states) == 1 else 'Mehrere gespeicherte Bereiche'
            relation = row.accounts if row.accounts != 'Kein gespeichertes Konto' else 'Keine Kontozuordnung'
            result['profiles'].append({
                'key': key, 'view': 'profiles', 'identity': row.identity, 'row': row,
                'title': name, 'editable': valid,
                'values': (name, relation, 'Zuordnung vorhanden' if android else 'Keine Zuordnung', stored_state),
                'body': f'Profilkennung: {row.identity}\n\nKonten: {row.accounts}\n\nAndroid: '
                        + ('; '.join(android) if android else 'Kein Gerät zugeordnet')
                        + '\n\nGespeicherter Stand: ' + '; '.join(states)})
        result['profiles'].sort(key=lambda item: (not item['editable'], item['title'].casefold(), item['key']))
        result['counts']['profiles'] = sum(item['editable'] for item in result['profiles'])
    except (OSError, ValueError, sqlite3.Error):
        result['warnings'].append('Profile und Geräte konnten nicht vollständig geladen werden.')
    try:
        with Store(cfg.db_path) as store:
            accounts = repo.list_accounts(store.db)
            videos = store.db.execute('SELECT COUNT(*) FROM videos').fetchone()[0]
            comments = store.db.execute('SELECT COUNT(*) FROM comments').fetchone()[0]
        saved = 0
        for account in accounts:
            state, text = (account_state(account) if account_state is not None else
                           guicore.account_status(account['platform'], root_for_db(cfg.db_path), account['identity']))
            saved += state in ('saved', 'verified')
            platform = guicore.PLATFORMS.get(account['platform'], {}).get('name', account['platform'])
            if state == 'saved':
                text = 'Anmeldung gespeichert · ungeprüft'
            result['accounts'].append({
                'key': 'account:' + str(account['id']), 'view': 'accounts', 'identity': account['identity'],
                'account_id': str(account['id']), 'title': account['label'], 'editable': True,
                'values': (account['label'], platform, account['identity'], text),
                'body': f'Plattform: {platform}\n\nProfil: {account["identity"]}\n\nAnmeldung: {text}'})
        result['counts'].update(accounts=len(accounts), videos=videos)
        result['state'].update(videos=videos, comments=comments, connected=saved)
    except (OSError, ValueError, sqlite3.Error):
        result['warnings'].append('Konten und Ergebniszahlen konnten nicht vollständig geladen werden.')
    try:
        boxes = accountvault.postfaecher(cfg.db_path)
        for box in boxes:
            assigned = box.get('belegt_von')
            address = box['adresse']
            # Keep only display metadata, never the mailbox's password or IMAP secret.
            result['mailboxes'].append({
                'key': 'mail:' + address.casefold(), 'view': 'mailboxes', 'identity': assigned,
                'address': address, 'title': address, 'editable': True,
                'values': (address, box.get('anbieter') or address.rpartition('@')[2], assigned or 'Nicht zugeordnet',
                           'Zugeordnet' if assigned else 'Frei'),
                'body': f'E-Mail-Adresse: {address}\n\nProfil: {assigned or "Noch nicht zugeordnet"}'
                        '\n\nDer gespeicherte Eintrag bestätigt keinen aktuellen Mailabruf.'})
        result['counts']['mailboxes'] = len(boxes)
        result['state']['mailboxes'] = sum(not box.get('belegt_von') for box in boxes)
    except (OSError, ValueError):
        result['warnings'].append('Die Postfachliste konnte nicht geladen werden.')
    result['warnings'] = list(dict.fromkeys(result['warnings']))
    return result
