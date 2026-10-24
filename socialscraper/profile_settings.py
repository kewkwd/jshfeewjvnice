"""Persistent profile input settings and explicit personal data.

External data sources can supply the documented import_person payload later;
this module never contacts a service or infers gender from a name.
"""
import copy
import datetime as dt
import json
from pathlib import Path

from . import accountvault
from .runlock import OsLock

GENDERS = ('männlich', 'weiblich', 'divers', 'keine Angabe')
PERSON_FIELDS = ('vorname', 'nachname', 'wohnort', 'geschlecht', 'geburtsdatum')
LIMITS = {'mouse_px_s': (100, 5000), 'click_ms': (20, 1000), 'typing_cps': (1, 30)}


def _path(profiles_dir, identity):
    from .net.identity import validate_name
    validate_name(identity)
    return Path(profiles_dir).parent / 'profile_settings' / (identity + '.json')


def defaults(identity):
    return {'version': 1, 'identity': identity, 'person': dict.fromkeys(PERSON_FIELDS, ''),
            'tempo': {'mouse_px_s': 900, 'click_ms': 120, 'typing_cps': 5},
            'source': ''}


def normalize_person(values, *, required=False):
    if not isinstance(values, dict) or set(values) - set(PERSON_FIELDS):
        raise ValueError('Profildaten haben unbekannte Felder.')
    person = {}
    for key in PERSON_FIELDS:
        value = values.get(key, '')
        if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Profildaten müssen gültigen Text enthalten.')
        person[key] = value.strip()
    if person['geschlecht'] and person['geschlecht'] not in GENDERS:
        raise ValueError('Bitte ein angebotenes Geschlecht auswählen.')
    if person['geburtsdatum']:
        try:
            raw = person['geburtsdatum']
            date = dt.datetime.strptime(raw, '%d.%m.%Y').date() if '.' in raw else dt.date.fromisoformat(raw)
            if date > dt.date.today():raise ValueError()
            person['geburtsdatum'] = date.isoformat()
        except ValueError:
            raise ValueError('Geburtsdatum: Bitte TT.MM.JJJJ oder JJJJ-MM-TT angeben.') from None
    if required and any(not person[k] for k in ('vorname', 'nachname', 'geschlecht')):
        raise ValueError('Vor der Registrierung unter Profile Name und Geschlecht festlegen.')
    return person


def validate(data, identity):
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('identity') != identity:
        raise ValueError('Gespeicherte Profileinstellungen haben ein unbekanntes Format.')
    tempo = data.get('tempo')
    if not isinstance(tempo, dict) or set(tempo) != set(LIMITS):
        raise ValueError('Das Profiltempo ist unvollständig.')
    for key, (low, high) in LIMITS.items():
        value = tempo[key]
        if type(value) not in (int, float) or not low <= value <= high:
            raise ValueError(f'{key}: Bitte einen Wert zwischen {low} und {high} angeben.')
    source = data.get('source', '')
    if not isinstance(source, str) or len(source) > 100 or any(ord(c) < 32 for c in source):
        raise ValueError('Die Datenquelle ist ungültig.')
    return {'version': 1, 'identity': identity, 'person': normalize_person(data.get('person')),
            'tempo': dict(tempo), 'source': source}


def load(profiles_dir, identity):
    path = _path(profiles_dir, identity)
    try:raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return defaults(identity)
    except (OSError, ValueError):
        raise ValueError('Die Profileinstellungen sind nicht lesbar; keine Daten überschrieben.') from None
    return validate(raw, identity)


def update(profiles_dir, identity, *, person=None, tempo=None, source=None, fill_empty=False):
    path = _path(profiles_dir, identity)
    lock = OsLock(path.parent.with_name('profile_settings.locks') / (identity + '.lock'))
    if not lock.try_acquire({}, tries=101):
        raise RuntimeError('Das Profil wird gerade bearbeitet. Bitte erneut versuchen.')
    try:
        data = load(profiles_dir, identity)
        if person is not None:
            incoming = normalize_person(person)
            if fill_empty:
                for key, value in incoming.items():
                    if value and data['person'][key] and value != data['person'][key]:
                        raise ValueError('Die angegebenen Personendaten weichen vom gespeicherten Profil ab.')
                    if value:data['person'][key] = value
            else:data['person'] = incoming
        if tempo is not None:data['tempo'] = dict(tempo)
        if source is not None:data['source'] = source
        data = validate(data, identity)
        if not path.exists() or data != load(profiles_dir, identity):
            if any(data['person'].values()):
                accountvault._write(path, data)
            else:
                # Tempo-only defaults contain no personal data. Basic identity
                # creation stays local and does not launch an ACL subprocess.
                from .net.identity import _write
                path.parent.mkdir(parents=True, exist_ok=True)
                _write(path, data)
        return copy.deepcopy(data)
    finally:
        lock.release()


def import_person(profiles_dir, identity, payload, *, source):
    """Future tool contract: explicit person fields, merge without overwriting."""
    normalize_person(payload, required=True)
    return update(profiles_dir, identity, person=payload, source=source, fill_empty=True)


def from_registration(details):
    return normalize_person({'vorname': details.get('vorname', ''), 'nachname': details.get('nachname', ''),
                             'wohnort': details.get('ort', ''), 'geschlecht': details.get('geschlecht', ''),
                             'geburtsdatum': details.get('geburtsdatum', '')})


def birthdate(person):
    data = normalize_person(person, required=True)
    if not data['geburtsdatum']:
        raise ValueError('Für diesen Registrierungsweg bitte das Geburtsdatum im Profil hinterlegen.')
    return dt.date.fromisoformat(data['geburtsdatum'])


def full_name(person):
    data = normalize_person(person, required=True)
    return data['vorname'] + ' ' + data['nachname']


def select_gender(page, selectors, person, check=None):
    from .signup import _select
    from .net.errors import FinalError
    gender = normalize_person(person, required=True)['geschlecht']
    labels = {'männlich': ('Männlich', 'Male'), 'weiblich': ('Weiblich', 'Female'),
              'divers': ('Divers', 'Nichtbinär', 'Non-binary'),
              'keine Angabe': ('Keine Angabe', 'Möchte ich nicht sagen', 'Rather not say')}[gender]
    for label in labels:
        if _select(page, selectors, label, check=check):return
    raise FinalError('Die gespeicherte Geschlechtsauswahl wird hier nicht angeboten. Bitte im Fenster prüfen; keine Ersatzwahl.')
