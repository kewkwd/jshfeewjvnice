"""Archetypen: die oberste Ebene — wer ist das (Milieu, Alter, Geschlecht, Gerät).

Gegen Cluster-Bildung. Kernregeln aus dem Dauerauftrag §7:
- Jede Eigenschaft ist eine Tendenz mit dünnem Auslauf (ca. 70 % Kern, 30 % Auslauf), nie ein Bereich.
- Konten liegen zwischen Archetypen: ein Hauptarchetyp (60-80 %) und null bis zwei Nebenarchetypen.
- Alles hängt an der Person, nichts wird unabhängig gewürfelt — und nichts ist vorhersagbar.
- Keine Ebene darf Stellvertreter für eine andere sein.

Die Zuweisung ist deterministisch je Identity: dieselbe Identity bekommt immer denselben
Archetyp, auch nach Neustart und Neuberechnung. Keine Neuverteilung bei jedem Lauf.

ARCHETYPEN ist der veränderliche Teil. Sie steht absichtlich im Code und nicht in der
Datenbank, damit Änderungen im Git-Diff sichtbar sind.

Achtung: „alter" ist in SQLite ein Schlüsselwort. Der Spaltenname steht in jedem SQL in
Anführungszeichen, in Python bleibt der Schlüssel normal `alter`.
"""
import logging
import random

from . import repo
from .store import Store, _atomic_schema

log = logging.getLogger(__name__)

KERN = 0.70                       # Anteil im Schwerpunkt; der Rest ist Auslauf
ALTER_MIN, ALTER_MAX = 13, 62     # äußere Weltgrenzen, NICHT die Bandgrenze des Archetyps
HAUPT_MIN, HAUPT_MAX = 0.60, 0.80
NEBEN_VERTEILUNG = ((0, 0.55), (1, 0.35), (2, 0.10))
VERHALTEN = ('still', 'schauer', 'liker', 'mischer', 'kommentierer')
GERAETE = ('web', 'app', 'beide')
ROLLE_HAUPT, ROLLE_NEBEN = 'haupt', 'neben'

# anteil = Tendenz über die ganze Flotte. Kein Archetyp ist nur auf einem Gerät.
# alter = (Mitte, Kern-Streuung, Auslauf-Streuung). Die Kurven sind bewusst breit:
#         ein Gaming-Nerd ist nicht 22 plus/minus 3, sondern 14 bis 35.
# verhalten = Tendenz je Archetyp, damit ein Teenie erkennbar anders tickt als ein stiller
#         Beobachter — aber ohne Klammer. Zielwert bleibt unter 0,70.
ARCHETYPEN = {
    'teenie': {
        'name': 'Teenie-Fan', 'anteil': 0.16, 'milieu': 'Schule, Memes, Clips',
        'alter': (16, 2.5, 4.0),
        'geschlecht': {'w': 0.52, 'm': 0.46, 'd': 0.02},
        'geraet': {'app': 0.74, 'web': 0.14, 'beide': 0.12},
        'verhalten': {'still': 0.12, 'schauer': 0.22, 'liker': 0.45, 'mischer': 0.13, 'kommentierer': 0.08},
        'stil': ('kurz', 'emojis', 'kleinschreibung', 'slang'),
    },
    'girlpower': {
        'name': 'Girl-Power-Fan', 'anteil': 0.14, 'milieu': 'Fandoms, Dance, Pop',
        'alter': (20, 3.5, 5.0),
        'geschlecht': {'w': 0.88, 'm': 0.08, 'd': 0.04},
        'geraet': {'app': 0.78, 'web': 0.10, 'beide': 0.12},
        'verhalten': {'still': 0.07, 'schauer': 0.19, 'liker': 0.40, 'mischer': 0.22, 'kommentierer': 0.12},
        'stil': ('begeistert', 'emojis', 'herz', 'ausrufezeichen'),
    },
    'links': {
        'name': 'Linke Zecke', 'anteil': 0.12, 'milieu': 'Politik, Satire, Podcasts',
        'alter': (26, 5.0, 7.0),
        'geschlecht': {'w': 0.48, 'm': 0.48, 'd': 0.04},
        'geraet': {'app': 0.56, 'web': 0.32, 'beide': 0.12},
        'verhalten': {'still': 0.16, 'schauer': 0.35, 'liker': 0.11, 'mischer': 0.18, 'kommentierer': 0.20},
        'stil': ('polemisch', 'kleinschreibung', 'ironie', 'slang'),
    },
    'gaming': {
        'name': 'Gaming-Nerd', 'anteil': 0.13, 'milieu': 'Games, Streams, Tech',
        'alter': (23, 5.5, 7.0),
        'geschlecht': {'w': 0.22, 'm': 0.75, 'd': 0.03},
        'geraet': {'app': 0.30, 'web': 0.58, 'beide': 0.12},
        'verhalten': {'still': 0.17, 'schauer': 0.40, 'liker': 0.20, 'mischer': 0.17, 'kommentierer': 0.06},
        'stil': ('fachbegriffe', 'ironie', 'kurz'),
    },
    'migra': {
        'name': 'Migra-Bubble', 'anteil': 0.14, 'milieu': 'Familie, Musik, Comedy',
        'alter': (23, 5.0, 7.0),
        'geschlecht': {'w': 0.50, 'm': 0.48, 'd': 0.02},
        'geraet': {'app': 0.78, 'web': 0.10, 'beide': 0.12},
        'verhalten': {'still': 0.11, 'schauer': 0.19, 'liker': 0.40, 'mischer': 0.22, 'kommentierer': 0.08},
        'stil': ('slang', 'emojis', 'kurz'),
    },
    'student': {
        'name': 'Studentin/Student', 'anteil': 0.12, 'milieu': 'Uni, Kultur, Nachrichten',
        'alter': (23, 3.5, 5.0),
        'geschlecht': {'w': 0.52, 'm': 0.46, 'd': 0.02},
        'geraet': {'app': 0.42, 'web': 0.45, 'beide': 0.13},
        'verhalten': {'still': 0.16, 'schauer': 0.32, 'liker': 0.12, 'mischer': 0.22, 'kommentierer': 0.18},
        'stil': ('sachlich', 'ironie', 'kleinschreibung'),
    },
    'normalo': {
        'name': 'Normalo berufstätig', 'anteil': 0.12, 'milieu': 'Alltag, Sport, Regionales',
        'alter': (32, 6.0, 8.0),
        'geschlecht': {'w': 0.50, 'm': 0.48, 'd': 0.02},
        'geraet': {'app': 0.36, 'web': 0.52, 'beide': 0.12},
        'verhalten': {'still': 0.26, 'schauer': 0.42, 'liker': 0.12, 'mischer': 0.16, 'kommentierer': 0.04},
        'stil': ('normal', 'kurz'),
    },
    'stiller': {
        'name': 'Stiller Beobachter', 'anteil': 0.07, 'milieu': 'Nachrichten, Doku, Ruhiges',
        'alter': (38, 7.0, 9.0),
        'geschlecht': {'w': 0.50, 'm': 0.48, 'd': 0.02},
        'geraet': {'app': 0.40, 'web': 0.46, 'beide': 0.14},
        'verhalten': {'still': 0.62, 'schauer': 0.30, 'liker': 0.05, 'mischer': 0.02, 'kommentierer': 0.01},
        'stil': ('kurz', 'selten'),
    },
}

SPALTEN = {'alter': 'INTEGER', 'geschlecht': 'TEXT', 'geraet': 'TEXT',
           'verhalten_typ': 'TEXT', 'stil': 'TEXT'}

DDL = """
CREATE TABLE IF NOT EXISTS archetypen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, schluessel TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    beschreibung TEXT, anteil REAL NOT NULL DEFAULT 0, aktiv INTEGER NOT NULL DEFAULT 1,
    erstellt_am TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS account_archetypen (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    archetyp_id INTEGER NOT NULL REFERENCES archetypen(id) ON DELETE CASCADE,
    rolle TEXT NOT NULL DEFAULT 'neben', gewicht REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (account_id, archetyp_id));
CREATE INDEX IF NOT EXISTS idx_account_archetypen ON account_archetypen (archetyp_id);
"""


class ArchetypError(RuntimeError):
    pass


def ensure_schema(db):
    with _atomic_schema(db, 'archetypen_schema'):
        for statement in DDL.split(';'):
            if statement.strip():
                db.execute(statement)
        vorhanden = {z[1] for z in db.execute('PRAGMA table_info(accounts)')}
        for name, typ in SPALTEN.items():
            if name not in vorhanden:
                # „alter“ ist in SQLite ein Schlüsselwort: Spaltennamen deshalb immer in Anführungszeichen.
                db.execute(f'ALTER TABLE accounts ADD COLUMN "{name}" {typ}')
        _saat(db)


def _saat(db):
    """Archetypen aus dem Code in die Tabelle spiegeln. `aktiv` bleibt eine Entscheidung des Nutzers."""
    jetzt = repo.iso(repo.now())
    for schluessel, daten in ARCHETYPEN.items():
        db.execute(
            'INSERT INTO archetypen (schluessel, name, beschreibung, anteil, erstellt_am) VALUES (?,?,?,?,?) '
            'ON CONFLICT(schluessel) DO UPDATE SET name=excluded.name, beschreibung=excluded.beschreibung, '
            'anteil=excluded.anteil',
            (schluessel, daten['name'], daten.get('milieu'), float(daten['anteil']), jetzt))


def _ids(db):
    return {z[0]: z[1] for z in db.execute('SELECT schluessel, id FROM archetypen WHERE aktiv=1')}


def _kurve(rng, mitte, kern, auslauf):
    """70 % im Schwerpunkt, 30 % im Auslauf — gezogen, nicht behauptet.

    Der Auslauf geht in eine zufällige Richtung und wird erst an der äußeren Weltgrenze
    beschnitten, nicht am Band des Archetyps. Deshalb gibt es kein Altersband ohne Auslauf."""
    if rng.random() < KERN:
        wert = rng.gauss(mitte, kern)
    else:
        richtung = rng.choice((-1, 1))
        wert = mitte + richtung * (kern + abs(rng.gauss(0, auslauf)))
    return int(max(ALTER_MIN, min(ALTER_MAX, round(wert))))


def _tendenz(rng, gewichte):
    werte = [w for w, anteil in gewichte.items() if anteil > 0]
    return rng.choices(werte, weights=[gewichte[w] for w in werte], k=1)[0]


def _rng(identity):
    return random.Random(f'{identity}|archetyp')


def _neben(rng, haupt, alle):
    anzahl = rng.choices([n for n, _ in NEBEN_VERTEILUNG],
                         weights=[p for _, p in NEBEN_VERTEILUNG], k=1)[0]
    rest = [s for s in alle if s != haupt]
    rng.shuffle(rest)
    return rest[:anzahl]


def _ziehen(rng, schluessel):
    """Alle Eigenschaften des Kontos aus dem Archetyp — mit eigener Ziehung je Eigenschaft."""
    daten = ARCHETYPEN[schluessel]
    mitte, kern, auslauf = daten['alter']
    return {
        'alter': _kurve(rng, mitte, kern, auslauf),
        'geschlecht': _tendenz(rng, daten['geschlecht']),
        'geraet': _tendenz(rng, daten['geraet']),
        'verhalten_typ': _tendenz(rng, daten['verhalten']),
        'stil': rng.choice(daten['stil']),
    }


def zuweisen(db_path, platform='tiktok', *, nur_ohne=True, jetzt=None):
    """Konten ohne Archetyp zuweisen. Deterministisch je Identity, also stabil über Neustarts."""
    with Store(db_path) as store:
        db = store.db
        ensure_schema(db)
        ids = _ids(db)
        if not ids:
            raise ArchetypError('Keine aktiven Archetypen.')
        anteile = [ARCHETYPEN[s]['anteil'] for s in ids]
        fertig = {z[0] for z in db.execute('SELECT DISTINCT account_id FROM account_archetypen')}
        zugewiesen = 0
        for konto in repo.list_accounts(db, platform):
            if nur_ohne and konto['id'] in fertig:
                continue
            rng = _rng(konto['identity'])
            haupt = rng.choices(list(ids), weights=anteile, k=1)[0]
            eigenschaften = _ziehen(rng, haupt)
            neben = _neben(rng, haupt, list(ids))
            haupt_gewicht = rng.uniform(HAUPT_MIN, HAUPT_MAX)
            db.execute('UPDATE accounts SET "alter"=?, geschlecht=?, geraet=?, verhalten_typ=?, stil=? WHERE id=?',
                       (eigenschaften['alter'], eigenschaften['geschlecht'], eigenschaften['geraet'],
                        eigenschaften['verhalten_typ'], eigenschaften['stil'], konto['id']))
            db.execute('DELETE FROM account_archetypen WHERE account_id=?', (konto['id'],))
            db.execute('INSERT INTO account_archetypen (account_id, archetyp_id, rolle, gewicht) VALUES (?,?,?,?)',
                       (konto['id'], ids[haupt], ROLLE_HAUPT, haupt_gewicht))
            rest = (1.0 - haupt_gewicht) / len(neben) if neben else 0.0
            for schluessel in neben:
                db.execute('INSERT INTO account_archetypen (account_id, archetyp_id, rolle, gewicht) VALUES (?,?,?,?)',
                           (konto['id'], ids[schluessel], ROLLE_NEBEN, rest))
            zugewiesen += 1
        db.commit()
        return zugewiesen


def haupt_archetyp(db, account_id):
    zeile = db.execute('SELECT t.schluessel FROM account_archetypen a JOIN archetypen t ON t.id=a.archetyp_id '
                       "WHERE a.account_id=? AND a.rolle='haupt'", (account_id,)).fetchone()
    return zeile[0] if zeile else None


def archetyp_von(db_path, identity):
    """Alles, was dieses Konto als Person ausmacht. Für Anzeige und Kommentar-Stil."""
    with Store(db_path) as store:
        db = store.db
        ensure_schema(db)
        konto = db.execute('SELECT * FROM accounts WHERE identity=?', (identity,)).fetchone()
        if konto is None:
            return None
        namen = [c[0] for c in db.execute('SELECT * FROM accounts WHERE identity=?', (identity,)).description]
        daten = dict(zip(namen, konto))
        rollen = {}
        for schluessel, rolle, gewicht in db.execute(
                'SELECT t.schluessel, a.rolle, a.gewicht FROM account_archetypen a '
                'JOIN archetypen t ON t.id=a.archetyp_id WHERE a.account_id=? ORDER BY a.gewicht DESC',
                (daten['id'],)):
            rollen.setdefault(rolle, []).append({'schluessel': schluessel, 'gewicht': round(gewicht, 3)})
        daten['archetypen'] = rollen.get(ROLLE_HAUPT, []) + rollen.get(ROLLE_NEBEN, [])
        daten['haupt'] = (rollen.get(ROLLE_HAUPT) or [{}])[0].get('schluessel')
        return daten


def alter_korb(alter):
    if alter is None:
        return 'unbekannt'
    if alter < 18:
        return 'unter 18'
    if alter < 25:
        return '18-24'
    if alter < 35:
        return '25-34'
    return '35+'


AUSDRUECKE = {
    'archetyp': "COALESCE(t.schluessel, 'kein')",
    'alter': "CASE WHEN a.\"alter\" IS NULL THEN 'unbekannt' WHEN a.\"alter\"<18 THEN 'unter 18' "
             "WHEN a.\"alter\"<25 THEN '18-24' WHEN a.\"alter\"<35 THEN '25-34' ELSE '35+' END",
    'geschlecht': "COALESCE(a.geschlecht, 'unbekannt')",
    'geraet': "COALESCE(a.geraet, 'unbekannt')",
    'verhalten': "COALESCE(a.verhalten_typ, 'unbekannt')",
    'tier': "COALESCE(a.tier, 'unbekannt')",
    'stil': "COALESCE(a.stil, 'unbekannt')",
}
FELDER = tuple(AUSDRUECKE)


def _grundgeruest():
    return ('FROM accounts a LEFT JOIN account_archetypen aa ON aa.account_id=a.id AND aa.rolle=? '
            'LEFT JOIN archetypen t ON t.id=aa.archetyp_id')


def kreuztabelle(db_path, links, rechts):
    """Zählwerk: wie oft kommt links=Wert und rechts=Wert zusammen vor."""
    if links not in AUSDRUECKE or rechts not in AUSDRUECKE:
        raise ArchetypError(f'Unbekanntes Feld. Erlaubt: {", ".join(FELDER)}')
    with Store(db_path) as store:
        db = store.db
        ensure_schema(db)
        sql = (f'SELECT {AUSDRUECKE[links]}, {AUSDRUECKE[rechts]}, COUNT(*) {_grundgeruest()} GROUP BY 1, 2')
        tabelle = {}
        for links_wert, rechts_wert, anzahl in db.execute(sql, (ROLLE_HAUPT,)):
            tabelle.setdefault(links_wert, {})[rechts_wert] = anzahl
        return tabelle


def _vorhersage(tabelle):
    gesamt = sum(sum(zeile.values()) for zeile in tabelle.values())
    if not gesamt:
        return 0.0
    return sum(max(zeile.values()) for zeile in tabelle.values()) / gesamt


def vorhersagbarkeit(db_path, links, rechts):
    """Wie gut sagt links rechts voraus? 1.0 = vollständig, 0.0 = zufällig."""
    return _vorhersage(kreuztabelle(db_path, links, rechts))


PAARE = (('archetyp', 'alter'), ('archetyp', 'geschlecht'), ('archetyp', 'geraet'),
         ('archetyp', 'verhalten'), ('archetyp', 'tier'), ('archetyp', 'stil'),
         ('geraet', 'verhalten'), ('alter', 'geschlecht'), ('tier', 'verhalten'))
ZIELWERT = 0.70


def pruefen(db_path, *, zielwert=ZIELWERT):
    """Misch-Prüfer: keine Paarung darf über dem Zielwert liegen. Unter 0.40 ist fast zu zufällig.

    Paare, bei denen eine Seite nur einen einzigen Wert hat (z. B. `tier`, solange keine
    Personas zugewiesen sind), werden als „nur ein Wert“ gekennzeichnet und nicht gewertet."""
    befunde = []
    for links, rechts in PAARE:
        tabelle = kreuztabelle(db_path, links, rechts)
        werte_rechts = {w for zeile in tabelle.values() for w in zeile}
        if len(tabelle) < 2 or len(werte_rechts) < 2:
            befunde.append({'links': links, 'rechts': rechts, 'wert': 1.0, 'urteil': 'nur ein Wert'})
            continue
        wert = _vorhersage(tabelle)
        befunde.append({'links': links, 'rechts': rechts, 'wert': round(wert, 3),
                        'urteil': 'zu gleichförmig' if wert > zielwert else
                                  ('fast zufällig' if wert < 0.40 else 'gut')})
    return sorted(befunde, key=lambda b: -b['wert'])