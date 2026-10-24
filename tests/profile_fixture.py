"""Explicit fictional person metadata for isolated registration fixtures."""
import json
from socialscraper import profile_settings

PERSON = {'vorname':'Offline', 'nachname':'Fixture', 'wohnort':'Fixturestadt',
          'geschlecht':'keine Angabe', 'geburtsdatum':'1995-01-01'}
REGISTRATION = {'vorname':'Offline', 'nachname':'Fixture', 'ort':'Fixturestadt',
                'geschlecht':'keine Angabe', 'geburtsdatum':'1995-01-01'}


def prepare(cfg, *identities):
    for identity in identities:
        path = profile_settings._path(cfg.profiles_dir, identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = profile_settings.defaults(identity)
        data.update(person=dict(PERSON), source='offline_fixture')
        path.write_text(json.dumps(data), encoding='utf8')
