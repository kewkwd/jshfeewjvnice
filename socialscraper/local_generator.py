"""Offline synthetic fixtures. These files are never profile/registration inputs."""
import base64
from datetime import datetime, timezone
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import secrets
import time
import unicodedata

from . import accountvault
from .net import accountproxy, sticky_sessions
from .net.identity import validate_name
from .net.proxy import Proxy

MAX_HTML = 16 * 1024 * 1024
MAX_STREETS = 96 * 1024 * 1024


class DataBlocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = None
        self.blocks = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('id') in ('daten-namen', 'daten-strassen'):
            key = attrs['id']
            if key in self.blocks:
                raise ValueError('Die Datei enthält doppelte Datenblöcke.')
            self.active = key
            self.blocks[key] = []

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = None

    def handle_data(self, text):
        if self.active is not None:
            self.blocks[self.active].append(text)


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise ValueError('Fehlende oder ungültige Orts-/Namensangabe.')
    return value.strip()


def _city_key(value):
    value = unicodedata.normalize('NFC', _text(value)).casefold()
    # Only explicit translations, never an arbitrary nearby-city fallback.
    return {'munich': 'münchen', 'cologne': 'köln', 'nuremberg': 'nürnberg'}.get(value, value)


def snapshot(cfg, identity):
    """Read a recorded sticky session; no network or fresh session creation."""
    validate_name(identity)
    saved = sticky_sessions.load(cfg, identity)
    raw = accountproxy.load(cfg.db_path).get(identity)
    if saved is None or not raw:
        raise ValueError('Keine gespeicherte Sticky-Messung für dieses Profil. Bitte die lokale Simulation verwenden.')
    proxy = Proxy.parse(raw)
    if not sticky_sessions.supported(proxy) or saved['source_hash'] != sticky_sessions._hash(proxy):
        raise ValueError('Die gespeicherte Sticky-Messung gehört nicht zum zugeordneten Proxy.')
    country = _text(saved.get('country_code')).upper()
    if country != 'DE':
        raise ValueError('Der Datengenerator enthält nur deutsche Anschriften. Kein Ersatzort ausgewählt.')
    city = _text(saved.get('city'))
    age = time.time() - saved['started_at']
    if age < 0:
        raise ValueError('Die gespeicherte Messung liegt in der Zukunft und ist nicht verwendbar.')
    return {'kind': 'recorded_sticky', 'identity': identity, 'ip': saved['ip'],
            'session_id': saved['session_id'], 'source_hash': saved['source_hash'],
            'started_at': saved['started_at'], 'country_code': country, 'city': city,
            'region': saved.get('region', ''), 'recorded_at': saved.get('checked_at', ''),
            'within_recorded_lifetime': age < 300, 'live_verified': False}


def _sample(path, city):
    path = Path(path).expanduser().absolute()
    if str(path).startswith('\\\\') or path.suffix.lower() not in ('.html', '.htm'):
        raise ValueError('Bitte eine lokale HTML-Datei auswählen.')
    with path.open('rb') as stream:
        raw = stream.read(MAX_HTML + 1)
    if len(raw) > MAX_HTML:
        raise ValueError('Die Datengenerator-Datei ist zu groß (maximal 16 MB).')
    parser = DataBlocks()
    parser.feed(raw.decode('utf-8-sig'))
    try:
        names = json.loads(''.join(parser.blocks['daten-namen']))
        compressed = base64.b64decode(''.join(parser.blocks['daten-strassen']).strip(), validate=True)
        if not isinstance(names, dict):
            raise ValueError()
        first, last = names['v'], names['n']
        for items in (first, last):
            if not isinstance(items, list) or not items or len(items) > 100000:
                raise ValueError()
            for value in items:
                _text(value)
    except (ValueError, KeyError, TypeError):
        raise ValueError('Die eingebauten Datenblöcke haben nicht das erwartete Format.') from None
    chosen = None
    count = total = 0
    wanted = _city_key(city)
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            while True:
                line = stream.readline(2 * 1024 * 1024 + 1)
                if not line:
                    break
                total += len(line)
                if total > MAX_STREETS or len(line) > 2 * 1024 * 1024:
                    raise ValueError('Die entpackten Straßendaten überschreiten das Größenlimit.')
                fields = line.decode('utf-8').rstrip('\r\n').split('\t', 2)
                if len(fields) != 3 or not re.fullmatch('[0-9]{5}', fields[0]):
                    raise ValueError('Ungültiger Straßen-Datensatz.')
                if _city_key(fields[1]) != wanted:
                    continue
                streets = fields[2].split('\t')
                count += len(streets)
                # Same weighting as the HTML generator: uniform over street entries.
                if secrets.randbelow(count) < len(streets):
                    chosen = {'plz': fields[0], 'ort': _text(fields[1]),
                              'strasse': _text(secrets.choice(streets)), 'land': 'DE'}
    except (OSError, EOFError, UnicodeError):
        raise ValueError('Die Straßendaten lassen sich nicht sicher entpacken.') from None
    if chosen is None:
        raise ValueError('Der gemessene Ort fehlt im deutschen Datenbestand. Es wird keine andere Stadt verwendet.')
    chosen.update(vorname=secrets.choice(first), nachname=secrets.choice(last),
                  hausnummer='', geschlecht='', geburtsdatum='')
    return chosen, hashlib.sha256(raw).hexdigest(), str(path)


def create(cfg, generator_path, *, identity=None, simulated_city=None):
    """Save only to local_generator_tests, never to profile_settings or providers."""
    if identity is not None and simulated_city is not None:
        raise ValueError('Bitte genau eine Ortsquelle wählen.')
    if identity is not None:
        reference = snapshot(cfg, identity)
    else:
        reference = {'kind': 'simulation', 'country_code': 'DE', 'city': _text(simulated_city),
                     'live_verified': False}
    person, source_hash, source_path = _sample(generator_path, reference['city'])
    if identity is not None:
        latest = snapshot(cfg, identity)
        for field in ('source_hash', 'session_id', 'ip', 'city', 'country_code', 'started_at'):
            if latest[field] != reference[field]:
                raise ValueError('Die gespeicherte Proxy-Sitzung hat sich während der Erstellung geändert. Bitte neu erstellen.')
        reference = latest
    result = {'version': 1, 'test_only': True, 'purpose': 'local_fixture',
              'created_at': datetime.now(timezone.utc).isoformat(),
              'generator': {'path': source_path, 'sha256': source_hash},
              'reference': reference, 'person': person,
              'validation': {'country_matches': person['land'] == reference['country_code'],
                             'city_matches': _city_key(person['ort']) == _city_key(reference['city']),
                             'region_independently_verified': False,
                             'residence_verified': False}}
    path = Path(cfg.db_path).parent / 'local_generator_tests' / ('test_' + secrets.token_hex(8) + '.json')
    accountvault._write(path, result)
    return result, path


def summary(result, path):
    person, reference = result['person'], result['reference']
    text = ('NUR LOKALE TESTDATEN – kein echtes Konto angelegt\n\n'
            + person['vorname'] + ' ' + person['nachname'] + '\n'
            + person['strasse'] + '\n' + person['plz'] + ' ' + person['ort'] + ', DE\n\n'
            + 'Hausnummer, Geschlecht und Geburtsdatum: nicht in der Datenquelle belegt, bleiben leer.\n\n')
    if reference['kind'] == 'simulation':
        text += 'Ortsquelle: lokale Simulation, keine gemessene Proxy-IP.\n'
    else:
        text += 'Ortsquelle: gespeicherte Sticky-Messung für ' + reference['identity'] + '\n'
        text += 'IP der Messung: ' + reference['ip'] + '\n'
        text += 'Messzeit: ' + str(reference['recorded_at']) + '\n'
        text += ('Aufgezeichnete 300-Sekunden-Laufzeit noch nicht abgelaufen.\n' if reference['within_recorded_lifetime']
                 else 'Aufgezeichnete Sitzung ist abgelaufen; nur historischer Vergleich.\n')
    text += ('Land und Stadt stimmen mit der gewählten Referenz überein.\n'
             'Region und tatsächlicher Wohnort sind nicht unabhängig geprüft.\n'
             'Keine aktuelle Netzwerkprüfung durchgeführt. Keine Änderung am echten Profil.\n\n'
             'Testdatei gespeichert: ' + str(path))
    return text
