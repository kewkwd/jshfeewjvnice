import copy, json, os, pathlib, re, subprocess, tempfile, threading
SCHEMA_VERSION, FILE_NAME, MASKE = 1, 'account_secrets.json', '*****'
FELDER = ('email', 'email_passwort', 'passwort', 'totp_secret', 'telefon', 'notiz', 'tiktok_username')
ANBIETER_HOSTS = {'web.de': 'imap.web.de', 'gmx.net': 'imap.gmx.net', 'gmx.de': 'imap.gmx.net',
                  'gmail.com': 'imap.gmail.com', 'outlook.com': 'outlook.office365.com',
                  'outlook.de': 'outlook.office365.com'}
_LOCK = threading.RLock()

def path_for(db_path):
    return pathlib.Path(db_path).parent / FILE_NAME

def _leer():
    return {'schema_version': SCHEMA_VERSION, 'postfach': {}, 'postfaecher': [], 'konten': {}}

def _write(path, daten):
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix=FILE_NAME+'.', suffix='.tmp', dir=path.parent)
        tmp = pathlib.Path(filename)
        try:
            if os.name == 'nt':
                # chmod(0600) alone does not restrict NTFS access. Protect the
                # fresh temporary file before writing any secret into it.
                user = os.environ.get('USERDOMAIN', '') + '\\' + os.getlogin()
                protected = subprocess.run(['icacls', str(tmp), '/inheritance:r', '/grant:r', user+':(F)'],
                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                if protected.returncode:
                    raise OSError('Die Geheimnisdatei konnte nicht auf deinen Benutzer beschränkt werden.')
            strom = os.fdopen(fd, 'w', encoding='utf-8')
            fd = None
            with strom:
                json.dump(daten, strom, ensure_ascii=False, indent=2)
                strom.flush()
                os.fsync(strom.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
        finally:
            if fd is not None:
                os.close(fd)
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

def load(db_path):
    try:
        roh = json.loads(path_for(db_path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return _leer()
    except (OSError, ValueError):
        raise ValueError('Die Geheimnisdatei lässt sich nicht lesen. Bitte zuerst eine Sicherung prüfen.') from None
    if not isinstance(roh, dict):
        raise ValueError('Die Geheimnisdatei enthält kein gültiges Objekt.')
    if roh.get('schema_version', SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError('Diese Version der Geheimnisdatei wird nicht unterstützt. Die Datei wurde nicht verändert.')
    for key, expected in (('postfach', dict), ('postfaecher', list), ('konten', dict)):
        if key in roh and not isinstance(roh[key], expected):
            raise ValueError(f'Die Geheimnisdatei enthält ein ungültiges Feld „{key}“. Die Datei wurde nicht verändert.')
    if any(not isinstance(e, dict) or not isinstance(e.get('adresse'), str) or not e['adresse'].strip()
           for e in roh.get('postfaecher', [])) or any(not isinstance(e, dict) for e in roh.get('konten', {}).values()):
        raise ValueError('Die Geheimnisdatei enthält beschädigte Einträge. Die Datei wurde nicht verändert.')
    daten = _leer()
    daten['postfach'] = roh.get('postfach') if isinstance(roh.get('postfach'), dict) else {}
    if isinstance(roh.get('postfaecher'), list):
        daten['postfaecher'] = [e for e in roh['postfaecher'] if isinstance(e, dict)]
    if isinstance(roh.get('konten'), dict):
        daten['konten'] = {str(k): v for k, v in roh['konten'].items() if isinstance(v, dict)}
    return daten

def save(db_path, daten):
    daten = {**_leer(), **daten, 'schema_version': SCHEMA_VERSION}
    _write(path_for(db_path), daten)
    return daten

def get(db_path, identity):
    return copy.deepcopy(load(db_path)['konten'].get(identity) or {})

def normalize_tiktok_username(value, *, required=False):
    """Normalize an explicitly supplied provider username, never an account label."""
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ValueError('Der TikTok-Benutzername muss Text sein.')
    value = value.strip().removeprefix('@')
    if not value:
        if required:
            raise ValueError('Bitte unter Konten → Weitere Möglichkeiten → TikTok-Benutzername '
                             'den tatsächlichen Benutzernamen dieses Kontos hinterlegen.')
        return ''
    if not re.fullmatch(r'[A-Za-z0-9_.]{1,128}', value) or value.endswith('.'):
        raise ValueError('TikTok-Benutzername ungültig: den @Namen aus dem Profil eintragen, '
                         'keinen Anzeigenamen, Link oder eine E-Mail-Adresse.')
    return value.casefold()

def put(db_path, identity, **felder):
    unbekannt = set(felder) - set(FELDER)
    if unbekannt:
        raise ValueError(f'Unbekanntes Feld: {", ".join(sorted(unbekannt))}')
    if felder.get('tiktok_username') is not None:
        felder['tiktok_username'] = normalize_tiktok_username(felder['tiktok_username'])
    with _LOCK:
        daten = load(db_path)
        daten['konten'].setdefault(identity, {}).update({k: v for k, v in felder.items() if v is not None})
        return save(db_path, daten)['konten'][identity]

def remove(db_path, identity):
    with _LOCK:
        daten = load(db_path)
        if identity not in daten['konten'] and not any(e.get('belegt_von')==identity for e in daten['postfaecher']):
            return daten
        daten['konten'].pop(identity, None)
        for eintrag in daten['postfaecher']:
            if eintrag.get('belegt_von') == identity:
                eintrag['belegt_von'] = None
        return save(db_path, daten)

def postfach(db_path):
    return copy.deepcopy(load(db_path)['postfach'])

def set_postfach(db_path, cfg):
    with _LOCK:
        daten = load(db_path)
        daten['postfach'] = dict(cfg or {})
        return save(db_path, daten)['postfach']

def postfaecher(db_path):
    return copy.deepcopy(load(db_path)['postfaecher'])

def add_postfach(db_path, eintrag):
    if not eintrag.get('adresse'):
        raise ValueError('Ein Postfach braucht eine Adresse.')
    with _LOCK:
        daten = load(db_path)
        address = eintrag['adresse'].strip()
        if address.casefold() in {e.get('adresse', '').strip().casefold() for e in daten['postfaecher']}:
            raise ValueError(f"Postfach {eintrag['adresse']} ist schon im Pool.")
        daten['postfaecher'].append({**eintrag, 'adresse': address, 'belegt_von': eintrag.get('belegt_von')})
        save(db_path, daten)
    return copy.deepcopy(eintrag)

def nimm_freies(db_path):
    for eintrag in load(db_path)['postfaecher']:
        if not eintrag.get('belegt_von'):
            return copy.deepcopy(eintrag)
    return None

def belege(db_path, adresse, identity):
    with _LOCK:
        daten = load(db_path)
        for eintrag in daten['postfaecher']:
            if eintrag.get('adresse') == adresse:
                if eintrag.get('belegt_von') not in (None, '', identity):
                    raise ValueError('Dieses Postfach ist bereits einem anderen Konto zugeordnet.')
                eintrag['belegt_von'] = identity
                break
        else:
            raise ValueError(f'Postfach {adresse} ist nicht im Pool.')
        save(db_path, daten)

def freigeben(db_path, identity):
    with _LOCK:
        daten = load(db_path)
        for eintrag in daten['postfaecher']:
            if eintrag.get('belegt_von') == identity:
                eintrag['belegt_von'] = None
        save(db_path, daten)

def als_postfach(eintrag):
    adresse, imap = eintrag.get('adresse') or '', eintrag.get('imap') or {}
    return {'art': 'imap', 'benutzer': adresse, 'passwort': eintrag.get('passwort') or '',
            'host': imap.get('host') or ANBIETER_HOSTS.get(adresse.rpartition('@')[2].lower()),
            'port': imap.get('port', 993), 'ssl': True, 'ordner': imap.get('ordner', 'INBOX')}

def redacted(daten):
    aus = copy.deepcopy(daten)
    for eintrag in (aus.get('konten') or {}).values():
        for feld in ('passwort', 'email_passwort', 'totp_secret'):
            if eintrag.get(feld):
                eintrag[feld] = MASKE
    for eintrag in (aus.get('postfaecher') or []):
        if eintrag.get('passwort'):
            eintrag['passwort'] = MASKE
    if (aus.get('postfach') or {}).get('passwort'):
        aus['postfach']['passwort'] = MASKE
    return aus

def scrub(text, daten=None):
    text = str(text)
    quellen = list(((daten or {}).get('konten') or {}).values()) + list((daten or {}).get('postfaecher') or [])
    quellen.append((daten or {}).get('postfach') or {})
    for eintrag in quellen:
        for feld in ('passwort', 'email_passwort', 'totp_secret'):
            wert = (eintrag or {}).get(feld)
            if isinstance(wert, str) and len(wert) >= 6:
                text = text.replace(wert, MASKE)
    return text
