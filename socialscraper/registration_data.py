"""Local registration details and explicitly requested fictional form fixtures."""
import datetime as dt
import json
import secrets
from pathlib import Path
import threading

from . import accountvault

PROVIDERS = ('web.de', 'gmx.net', 'gmail.com', 'outlook.com')
FIELDS = ('vorname', 'nachname', 'geburtsdatum', 'geschlecht', 'anrede', 'land', 'plz', 'ort', 'strasse', 'hausnummer')
LABELS = ('Vorname', 'Nachname', 'Geburtsdatum', 'Geschlecht', 'Anrede', 'Land', 'Postleitzahl', 'Ort', 'Straße', 'Hausnummer')
COUNTRIES = {'DE': 'Deutschland', 'AT': 'Österreich', 'CH': 'Schweiz'}
SALUTATIONS = ('Herr', 'Frau', 'Divers', 'Keine Angabe')
_LOCK = threading.RLock()


def generate_test_data(*, country='DE'):
    """Create fictional examples locally; the sample address is not deliverable."""
    if country not in COUNTRIES:
        raise ValueError('Bitte ein angebotenes Land auswählen.')
    today = dt.date.today()
    first = dt.date(today.year - 60, 1, 1)
    last = dt.date(today.year - 25, 1, 1)
    birthday = first + dt.timedelta(days=secrets.randbelow((last - first).days + 1))
    return normalize({
        'vorname': secrets.choice(('Alex', 'Kim', 'Robin', 'Sascha')),
        'nachname': 'Musterperson', 'geburtsdatum': birthday.isoformat(),
        'anrede': 'Keine Angabe', 'geschlecht': 'keine Angabe', 'land': country,
        'plz': '00000' if country == 'DE' else '0000',
        'ort': 'Musterstadt', 'strasse': 'Musterstraße',
        'hausnummer': str(1 + secrets.randbelow(99)),
    }, required=True)


def suggest_test_data(current):
    """Suggest only empty fields; retain even incomplete user edits verbatim."""
    if not isinstance(current, dict) or set(current) - set(FIELDS):
        raise ValueError('Registrierungsdaten haben ein unbekanntes Format.')
    if any(not isinstance(value, str) for value in current.values()):
        raise ValueError('Registrierungsdaten müssen Text enthalten.')
    missing = [key for key in FIELDS if not current.get(key, '').strip()]
    if not missing:
        return {}
    generated = generate_test_data(country=current.get('land', '').strip() or 'DE')
    generated['geburtsdatum'] = dt.date.fromisoformat(generated['geburtsdatum']).strftime('%d.%m.%Y')
    return {key: generated[key] for key in missing}


def normalize(data, *, required=False):
    if not isinstance(data, dict) or set(data) - set(FIELDS):
        raise ValueError('Registrierungsdaten haben ein unbekanntes Format.')
    result = {}
    for key, label in zip(FIELDS, LABELS):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f'{label}: Bitte gültigen Text eintragen.')
        result[key] = value.strip()
    raw = result['geburtsdatum']
    if raw:
        try:
            birthday = dt.datetime.strptime(raw, '%d.%m.%Y').date() if '.' in raw else dt.date.fromisoformat(raw)
            if birthday > dt.date.today():
                raise ValueError('future')
        except ValueError:
            raise ValueError('Geburtsdatum: Bitte ein gültiges Datum im Format TT.MM.JJJJ eintragen.') from None
        result['geburtsdatum'] = birthday.isoformat()
    if result['anrede'] and result['anrede'] not in SALUTATIONS:
        raise ValueError('Bitte eine angebotene Anrede auswählen.')
    from .profile_settings import GENDERS
    if result['geschlecht'] and result['geschlecht'] not in GENDERS:
        raise ValueError('Bitte ein angebotenes Geschlecht auswählen.')
    if result['land'] and result['land'] not in COUNTRIES:
        raise ValueError('Bitte ein angebotenes Land auswählen.')
    if required and any(not result[key] for key in ('vorname', 'nachname', 'geburtsdatum', 'geschlecht')):
        raise ValueError('Bitte unter Postfächer → Registrierungsdaten Vorname, Nachname, Geschlecht und Geburtsdatum hinterlegen.')
    return result


def _path(db_path):
    return Path(db_path).parent / 'registration_details.json'


def _read(db_path):
    try:
        raw = json.loads(_path(db_path).read_text(encoding='utf8'))
    except FileNotFoundError:
        return {'schema_version': 1, 'providers': {}}
    except (OSError, ValueError):
        raise ValueError('Die lokalen Registrierungsdaten lassen sich nicht lesen; bitte die Sicherung prüfen.') from None
    if (not isinstance(raw, dict) or raw.get('schema_version') != 1 or
            not isinstance(raw.get('providers'), dict) or set(raw['providers']) - set(PROVIDERS)):
        raise ValueError('Die lokalen Registrierungsdaten haben ein unbekanntes Format.')
    return {'schema_version': 1, 'providers': {key: normalize(value) for key, value in raw['providers'].items()}}


def load(db_path, provider):
    if provider not in PROVIDERS:
        raise ValueError('Unbekannter Postfachanbieter für Registrierungsdaten.')
    with _LOCK:
        return dict(_read(db_path)['providers'].get(provider, {}))


def save(db_path, provider, values):
    if provider not in PROVIDERS:
        raise ValueError('Unbekannter Postfachanbieter für Registrierungsdaten.')
    validated = normalize(values, required=True)
    with _LOCK:
        data = _read(db_path)
        data['providers'][provider] = validated
        # Same atomic replacement and Windows user-only ACL as stored passwords.
        accountvault._write(_path(db_path), data)
    return dict(validated)
