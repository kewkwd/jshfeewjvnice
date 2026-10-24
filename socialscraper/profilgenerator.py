"""Datengenerator: erzeugt vollständige Bot-Profile — Person, Adresse, E-Mail, Passwort, Tempo.

Kein eigenes Werkzeug mehr. Alles landet direkt in den echten Dateien des Tools:
- profile_settings  Person und Maus-/Klick-/Tipptempo
- account_secrets   E-Mail, Mailpasswort, Profilpasswort
- optional der Postfach-Pool und die Registrierungsdaten

Herkunft der Daten:
- Anschrift (PLZ, Ort, Straße) aus der Datengenerator-HTML.
- Alter und Geschlecht aus der Archetypen-Ebene, daraus das Geburtsdatum.
- Vorname aus eigener Liste, damit er zum Geschlecht passt (die HTML-Liste ist
  geschlechtslos). Nachname weiter aus der HTML-Liste.
- Hausnummer und Tempo sind nicht in der Quelle belegt und werden gestreut.

Alles ist deterministisch je Identity: zweimal laufen ergibt dasselbe Profil.
"""
import base64
import gzip
import hashlib
import io
import json
import logging
import random
import re
from datetime import date, timedelta
from pathlib import Path

from . import accountvault, archetypen, password_supply, profile_settings, registration_data, repo
from .local_generator import DataBlocks
from .store import Store

log = logging.getLogger(__name__)

MAX_HTML = 16 * 1024 * 1024
MAX_ENTPACKT = 96 * 1024 * 1024
STADT_UEBERSETZUNG = {'munich': 'münchen', 'cologne': 'köln', 'nuremberg': 'nürnberg'}

VORNAMEN = {
    'weiblich': ('Anna', 'Lena', 'Marie', 'Sophie', 'Lea', 'Mia', 'Emma', 'Hannah', 'Laura', 'Julia',
                 'Sarah', 'Lisa', 'Nina', 'Jana', 'Leonie', 'Amelie', 'Clara', 'Ida', 'Frieda', 'Greta',
                 'Nele', 'Merle', 'Finja', 'Romy', 'Pia', 'Alina', 'Vanessa', 'Melina', 'Yasmin', 'Selin',
                 'Elif', 'Aylin', 'Melisa', 'Zeynep', 'Esma', 'Hatice', 'Milena', 'Antonia', 'Theresa',
                 'Katharina', 'Franziska', 'Sabrina', 'Michelle', 'Angelina', 'Elena'),
    'männlich': ('Lukas', 'Jonas', 'Finn', 'Leon', 'Paul', 'Ben', 'Elias', 'Noah', 'Max', 'Felix',
                 'Jan', 'Tim', 'Nico', 'Tom', 'Luis', 'Moritz', 'Julian', 'Fabian', 'Simon', 'David',
                 'Philipp', 'Tobias', 'Sebastian', 'Florian', 'Dominik', 'Marcel', 'Kevin', 'Patrick',
                 'Emre', 'Yusuf', 'Ali', 'Mert', 'Kaan', 'Hamza', 'Kerem', 'Baran', 'Eren', 'Arda',
                 'Stefan', 'Andreas', 'Michael', 'Thomas', 'Christian', 'Matthias', 'Daniel'),
    'divers': ('Alex', 'Kim', 'Robin', 'Luca', 'Sascha', 'Mika', 'Noel', 'Jona', 'Toni', 'Charlie',
               'Deniz', 'Ege', 'Sam', 'Lior'),
}
GESCHLECHTER = {'w': ('weiblich', 'Frau'), 'm': ('männlich', 'Herr'), 'd': ('divers', 'Divers')}
ANBIETER = (('web.de', 0.34), ('gmx.net', 0.30), ('gmail.com', 0.26), ('outlook.com', 0.10))

# Tempo nach Altersband: (bis Alter, Maus px/s, Klickpause ms, Tippen Zeichen/s)
TEMPO_BAND = ((18, 1400, 85, 7.5), (25, 1250, 95, 6.8), (35, 1050, 110, 6.0), (99, 850, 135, 5.0))
ZUSCHLAG = {'gaming': 1.12, 'teenie': 1.08, 'stiller': 0.92}
UMLAUTE = {'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss', 'é': 'e', 'è': 'e', 'ç': 'c', 'ı': 'i'}


class ProfilError(RuntimeError):
    pass


class Quelle:
    """Liest die Datengenerator-HTML einmal: Nachnamen und alle Orte mit ihren Straßen."""

    def __init__(self, pfad):
        pfad = Path(pfad).expanduser().absolute()
        if not pfad.is_file():
            raise ProfilError(f'Datengenerator-Datei nicht gefunden: {pfad}')
        raw = pfad.read_bytes()
        if len(raw) > MAX_HTML:
            raise ProfilError('Die Datengenerator-Datei ist zu groß (maximal 16 MB).')
        parser = DataBlocks()
        parser.feed(raw.decode('utf-8-sig'))
        try:
            namen = json.loads(''.join(parser.blocks['daten-namen']))
            verdichtet = base64.b64decode(''.join(parser.blocks['daten-strassen']).strip(), validate=True)
        except (KeyError, ValueError):
            raise ProfilError('Die Datenblöcke der Datei haben nicht das erwartete Format.') from None
        self.nachnamen = [n for n in namen['n'] if isinstance(n, str) and n.strip()]
        self.pfad = str(pfad)
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.orte = {}
        gesamt = 0
        with gzip.GzipFile(fileobj=io.BytesIO(verdichtet)) as strom:
            for zeile in strom:
                gesamt += len(zeile)
                if gesamt > MAX_ENTPACKT:
                    raise ProfilError('Die entpackten Straßendaten sind zu groß.')
                felder = zeile.decode('utf-8').rstrip('\r\n').split('\t', 2)
                if len(felder) != 3 or not re.fullmatch('[0-9]{5}', felder[0]):
                    continue
                schluessel = felder[1].strip().casefold()
                self.orte.setdefault(schluessel, {'plz': felder[0], 'ort': felder[1].strip(),
                                                  'strassen': felder[2].split('\t')})

    def stadt(self, rng, wunsch=None):
        if wunsch:
            schluessel = STADT_UEBERSETZUNG.get(wunsch.strip().casefold(), wunsch.strip().casefold())
            if schluessel not in self.orte:
                raise ProfilError(f'Der Ort „{wunsch}“ kommt im Datenbestand nicht vor.')
            return self.orte[schluessel]
        return self.orte[rng.choice(sorted(self.orte))]


def _ascii(text):
    return ''.join(UMLAUTE.get(z, z) for z in str(text).casefold() if z.isalpha() or z in UMLAUTE)


def _klemme(wert, low, high, *, ganz=False):
    wert = max(low, min(high, wert))
    return int(round(wert)) if ganz else round(wert, 1)


def _anschrift(ort, rng):
    """Hausnummer ist nicht durch die Quelle belegt und wird gestreut."""
    return {'land': 'DE', 'plz': ort['plz'], 'ort': ort['ort'],
            'strasse': rng.choice(ort['strassen']), 'hausnummer': str(rng.randint(1, 187))}


def _lokalteil(vorname, nachname, rng):
    v, n = _ascii(vorname), _ascii(nachname)
    if not n:
        n = 'mail'
    trenner = rng.choices(['.', '_', ''], weights=[0.45, 0.20, 0.35])[0]
    ziffern = rng.choices(['', '99', '999', '9999'], weights=[0.30, 0.20, 0.30, 0.20])[0]
    roh = f'{v}{trenner}{n}{ziffern}'
    return ''.join(str(rng.randint(0, 9)) if z == '9' else z for z in roh)[:28]


def vorhandene_adressen(db_path):
    daten = accountvault.load(db_path)
    werte = {e.get('email', '') for e in daten['konten'].values()}
    werte |= {e.get('adresse', '') for e in daten['postfaecher']}
    return {w.strip().casefold() for w in werte if w and w.strip()}


def _adresse(vorname, nachname, rng, vorhanden, *, anbieter=None):
    for _ in range(80):
        host = anbieter or rng.choices([a for a, _ in ANBIETER], weights=[p for _, p in ANBIETER])[0]
        adresse = f'{_lokalteil(vorname, nachname, rng)}@{host}'
        if adresse.casefold() not in vorhanden:
            return adresse
    raise ProfilError('Keine freie E-Mail-Adresse gefunden.')


def tempo_fuer(alter, archetyp, rng):
    """Maus-, Klick- und Tipptempo aus dem Alter, gestreut und innerhalb der Grenzen des Tools."""
    band = next(b for b in TEMPO_BAND if alter < b[0])
    zu = ZUSCHLAG.get(archetyp, 1.0)
    maus = band[1] * zu * max(0.6, 1 + rng.gauss(0, 0.15))
    klick = band[2] / max(zu, 0.95) * max(0.6, 1 + rng.gauss(0, 0.18))
    tippen = band[3] * zu * max(0.6, 1 + rng.gauss(0, 0.15))
    grenzen = profile_settings.LIMITS
    return {'mouse_px_s': _klemme(maus, *grenzen['mouse_px_s'], ganz=True),
            'click_ms': _klemme(klick, *grenzen['click_ms'], ganz=True),
            'typing_cps': _klemme(tippen, *grenzen['typing_cps'])}


def rechnen(cfg, identity, *, quelle, stadt=None, anbieter=None):
    """Profil nur berechnen, nichts schreiben. Ergebnis ist bei gleicher Identity immer gleich."""
    archetypen.zuweisen(cfg.db_path, nur_ohne=True)
    daten = archetypen.archetyp_von(cfg.db_path, identity)
    if daten is None:
        raise ProfilError(f'Kein Konto mit der Kennung {identity}.')
    if not daten.get('alter') or not daten.get('geschlecht'):
        raise ProfilError('Für dieses Konto fehlen Alter oder Geschlecht.')
    rng = random.Random(f'{identity}|profil')
    geschlecht, anrede = GESCHLECHTER[daten['geschlecht']]
    vorname = rng.choice(VORNAMEN[geschlecht])
    nachname = rng.choice(quelle.nachnamen)
    alter = int(daten['alter'])
    geburt = date.today() - timedelta(days=int(alter * 365.25) + rng.randint(0, 364))
    anschrift = _anschrift(quelle.stadt(rng, stadt), rng)
    adresse = _adresse(vorname, nachname, rng, vorhandene_adressen(cfg.db_path), anbieter=anbieter)
    return {'identity': identity, 'archetyp': daten.get('haupt'), 'alter': alter,
            'verhalten': daten.get('verhalten_typ'), 'geschlecht': geschlecht, 'anrede': anrede,
            'vorname': vorname, 'nachname': nachname, 'geburt': geburt.isoformat(),
            'adresse': adresse, 'anschrift': anschrift,
            'tempo': tempo_fuer(alter, daten.get('haupt'), rng),
            'quelle': quelle.sha256[:12]}


def anlegen(cfg, identity, *, quelle, stadt=None, anbieter=None, mit_postfach=False, trocken=False):
    """Profil berechnen und in die echten Dateien schreiben."""
    profil = rechnen(cfg, identity, quelle=quelle, stadt=stadt, anbieter=anbieter)
    if trocken:
        profil['schreibweise'] = 'nur gerechnet'
        return profil
    anschrift = profil['anschrift']
    passwort = password_supply.profile_password(cfg.db_path, identity)
    mail_passwort = password_supply.reserve(cfg.db_path, 'postfach:' + profil['adresse'])
    accountvault.put(cfg.db_path, identity, email=profil['adresse'], email_passwort=mail_passwort)
    if mit_postfach:
        accountvault.add_postfach(cfg.db_path, {'adresse': profil['adresse'], 'passwort': mail_passwort,
                                                'belegt_von': identity})
    profile_settings.update(
        cfg.profiles_dir, identity,
        person={'vorname': profil['vorname'], 'nachname': profil['nachname'],
                'wohnort': anschrift['ort'], 'geschlecht': profil['geschlecht'],
                'geburtsdatum': profil['geburt']},
        tempo=profil['tempo'], source=f'profilgenerator:{profil["quelle"]}', fill_empty=True)
    if anbieter:
        registration_data.save(cfg.db_path, anbieter, {
            'vorname': profil['vorname'], 'nachname': profil['nachname'], 'geburtsdatum': profil['geburt'],
            'geschlecht': profil['geschlecht'], 'anrede': profil['anrede'], 'land': anschrift['land'],
            'plz': anschrift['plz'], 'ort': anschrift['ort'], 'strasse': anschrift['strasse'],
            'hausnummer': anschrift['hausnummer']})
    profil['schreibweise'] = 'geschrieben'
    profil['passwort_gesetzt'] = bool(passwort)
    return profil


def stapel(cfg, anzahl, *, quelle, prefix='konto', ab=1, stadt=None, anbieter=None,
           mit_postfach=False, trocken=False):
    """Für mehrere Konten nacheinander. Identitäten wie bei `konten anlegen`."""
    ergebnis = {'angelegt': 0, 'uebersprungen': 0, 'profile': []}
    with Store(cfg.db_path) as store:
        vorhanden = {k['identity'] for k in repo.list_accounts(store.db, 'tiktok')}
    for nummer in range(ab, ab + anzahl):
        identity = f'tiktok_{repo.slug(f"{prefix} {nummer:04d}")}'
        if identity not in vorhanden:
            ergebnis['uebersprungen'] += 1
            continue
        ergebnis['profile'].append(anlegen(cfg, identity, quelle=quelle, stadt=stadt,
                                           anbieter=anbieter, mit_postfach=mit_postfach, trocken=trocken))
        ergebnis['angelegt'] += 1
    return ergebnis


def pruefen(cfg):
    """Gegenprobe: passen Alter, Tempo, Adresse und Zugangsdaten zusammen?"""
    befunde = []
    with Store(cfg.db_path) as store:
        konten = repo.list_accounts(store.db, 'tiktok')
        personen = {}
        for konto in konten:
            try:
                personen[konto['identity']] = profile_settings.load(cfg.profiles_dir, konto['identity'])
            except ValueError as fehler:
                befunde.append({'identity': konto['identity'], 'art': 'datei', 'text': str(fehler)})
    geheimnisse = accountvault.load(cfg.db_path)['konten']
    adressen = {}
    for konto in konten:
        identity = konto['identity']
        daten = personen.get(identity)
        if daten is None:
            continue
        if not daten['person']['vorname']:
            befunde.append({'identity': identity, 'art': 'person', 'text': 'Kein Profil angelegt.'})
            continue
        alter = konto.get('alter')
        if alter and abs(alter - konto['alter']) > 2:
            befunde.append({'identity': identity, 'art': 'alter', 'text': 'Alter weicht vom Archetyp ab.'})
        tempo = daten['tempo']
        for name, (low, high) in profile_settings.LIMITS.items():
            if not low <= tempo[name] <= high:
                befunde.append({'identity': identity, 'art': 'tempo', 'text': f'{name} außerhalb der Grenzen.'})
        geheim = geheimnisse.get(identity) or {}
        if not geheim.get('email') or not geheim.get('passwort'):
            befunde.append({'identity': identity, 'art': 'zugang', 'text': 'E-Mail oder Passwort fehlt.'})
        adresse = (geheim.get('email') or '').casefold()
        if adresse:
            adressen.setdefault(adresse, []).append(identity)
    for adresse, konten_liste in adressen.items():
        if len(konten_liste) > 1:
            befunde.append({'identity': konten_liste[0], 'art': 'adresse',
                            'text': f'{adresse} ist {len(konten_liste)}-mal vergeben.'})
    return {'konten': len(konten), 'mit_profil': sum(1 for d in personen.values() if d['person']['vorname']),
            'befunde': befunde}