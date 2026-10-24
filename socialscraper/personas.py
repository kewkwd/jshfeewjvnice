"""Personas: Interessensgruppen mit eigenem FYP-Korridor. Gegen Cluster-Bildung."""
import json, logging, random

from . import repo
from .store import Store

log = logging.getLogger(__name__)
TIERS = (('kern', 0.20), ('sekundaer', 0.40), ('rand', 0.40))
FOLGT_P = 0.6
VERHALTEN = {'schauen_sekunden': (4.0, 11.0), 'kurz_sekunden': 2.5, 'like_p': 0.35,
             'save_p': 0.10, 'follow_p': 0.06, 'sitzung_sekunden': (240, 600)}
SPALTEN = {'persona_id': 'INTEGER', 'tier': 'TEXT', 'folgt_influencer': 'INTEGER DEFAULT 0',
           'fyp_status': "TEXT DEFAULT 'drift'", 'warmth_score': 'REAL DEFAULT 0',
           'letzter_konsum': 'TEXT', 'konsum_sitzungen': 'INTEGER DEFAULT 0'}
DDL = """
CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, beschreibung TEXT,
    influencer TEXT NOT NULL, influencer_gewicht REAL NOT NULL DEFAULT 1.0,
    verhalten TEXT, aktiv INTEGER NOT NULL DEFAULT 1, erstellt_am TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS persona_kanaele (
    id INTEGER PRIMARY KEY AUTOINCREMENT, persona_id INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    platform TEXT NOT NULL DEFAULT 'tiktok', handle TEXT NOT NULL, thema TEXT, gewicht REAL NOT NULL DEFAULT 1.0,
    UNIQUE(persona_id, platform, handle));
CREATE INDEX IF NOT EXISTS idx_persona_kanaele ON persona_kanaele (persona_id);
"""


def ensure_schema(db):
    db.executescript(DDL)
    vorhanden = {z[1] for z in db.execute('PRAGMA table_info(accounts)').fetchall()}
    for name, typ in SPALTEN.items():
        if name not in vorhanden:
            db.execute(f'ALTER TABLE accounts ADD COLUMN {name} {typ}')
    db.commit()


def _verhalten(text):
    daten = dict(VERHALTEN)
    try:
        daten.update(json.loads(text) if text else {})
    except (ValueError, TypeError):
        pass
    return daten


def anlegen(db, name, *, influencer, kanaele, beschreibung=None, influencer_gewicht=1.0, verhalten=None, now=None):
    if not name or not influencer:
        raise ValueError('Persona braucht Namen und Influencer.')
    ensure_schema(db)
    cur = db.execute('INSERT INTO personas (name, beschreibung, influencer, influencer_gewicht, verhalten, '
                     'erstellt_am) VALUES (?,?,?,?,?,?)',
                     (name, beschreibung, influencer, float(influencer_gewicht),
                      json.dumps(verhalten, ensure_ascii=False) if verhalten else None, repo.iso(now or repo.now())))
    persona_id = cur.lastrowid
    for handle in dict.fromkeys(kanaele):
        db.execute('INSERT OR IGNORE INTO persona_kanaele (persona_id, handle) VALUES (?,?)', (persona_id, handle))
    db.commit()
    return persona_id


def vorschlag(kanaele, *, anzahl, kanaele_je, influencer, rng=None):
    """Personas mit niedriger paarweiser Kanal-Überlappung (kleiner Jaccard, unscharf)."""
    rng = rng or random.Random()
    kanaele = list(dict.fromkeys(kanaele))
    if kanaele_je < 2 or len(kanaele) < kanaele_je - 1:
        raise ValueError('Zu wenige Kanäle für die gewünschte Persona-Größe.')
    personae = []
    for _ in range(anzahl):
        if not personae:
            gewaehlt = set(rng.sample(kanaele, kanaele_je - 1))
        else:
            pool = rng.sample(kanaele, min(len(kanaele), kanaele_je * 4))
            best = None
            for _ in range(60):
                kand = set(rng.sample(pool, min(len(pool), kanaele_je - 1)))
                if any(kand == p['kanaele'] for p in personae):
                    continue
                jac = sum(len(kand & p['kanaele']) / len(kand | p['kanaele']) for p in personae)
                if best is None or jac < best[0]:
                    best = (jac, kand)
            gewaehlt = best[1] if best else set(rng.sample(kanaele, kanaele_je - 1))
        personae.append({'name': f'persona_{len(personae) + 1:02d}', 'influencer': influencer, 'kanaele': gewaehlt})
    return personae


def aktive(db, platform='tiktok'):
    cur = db.execute('SELECT * FROM personas WHERE aktiv=1 ORDER BY id')
    namen = [c[0] for c in cur.description]
    ausgabe = []
    for zeile in cur.fetchall():
        persona = dict(zip(namen, zeile))
        persona['kanaele'] = [z[0] for z in db.execute(
            'SELECT handle FROM persona_kanaele WHERE persona_id=? AND platform=?', (persona['id'], platform))]
        persona['verhalten'] = _verhalten(persona.get('verhalten'))
        ausgabe.append(persona)
    return ausgabe


def persona_von(db_path, identity, platform='tiktok'):
    with Store(db_path) as store:
        ensure_schema(store.db)
        row = store.db.execute('SELECT persona_id FROM accounts WHERE identity=?', (identity,)).fetchone()
        if not row or not row[0]:
            return None
        persona = next((p for p in aktive(store.db, platform) if p['id'] == row[0]), None)
        if persona is None:
            return None
        persona['kanaele'] = persona['kanaele'] + [persona['influencer']]
        return persona


def zuweisen(db_path, platform='tiktok', *, nur_ohne=True):
    """Konten ohne Persona gewichtet zuweisen (deterministisch je Identity), inkl. Tier und Follow-Münze."""
    with Store(db_path) as store:
        db = store.db
        ensure_schema(db)
        personae = aktive(db, platform)
        if not personae:
            raise ValueError('Keine Persona vorhanden.')
        gewichte = [max(0.2, random.Random(p['name']).gauss(1.0, 0.4)) for p in personae]
        zugewiesen = 0
        for konto in repo.list_accounts(db, platform):
            if nur_ohne and konto.get('persona_id'):
                continue
            rng = random.Random(f"{konto['identity']}|persona")
            persona = rng.choices(personae, weights=gewichte, k=1)[0]
            tier = rng.choices([t for t, _ in TIERS], weights=[w for _, w in TIERS], k=1)[0]
            db.execute('UPDATE accounts SET persona_id=?, tier=?, folgt_influencer=? WHERE id=?',
                       (persona['id'], tier, 1 if rng.random() < FOLGT_P else 0, konto['id']))
            zugewiesen += 1
        db.commit()
        return zugewiesen