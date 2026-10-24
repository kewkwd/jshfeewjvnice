"""FYP-Konsum: Konten halten ihre For-You-Seite lebendig und handeln bei Persona-Kanälen."""
import logging

from .. import repo
from ..store import Store

log = logging.getLogger(__name__)
DDL = """
CREATE TABLE IF NOT EXISTS fyp_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL DEFAULT 'tiktok', kanal TEXT NOT NULL,
    uid TEXT NOT NULL, autor TEXT, erstellt_am TEXT NOT NULL, UNIQUE(platform, uid));
CREATE TABLE IF NOT EXISTS fyp_treffer (
    id INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL, kanal TEXT NOT NULL, video_uid TEXT,
    persona_id INTEGER, art TEXT NOT NULL, organisch INTEGER NOT NULL DEFAULT 1, erstellt_am TEXT NOT NULL,
    UNIQUE(identity, kanal, video_uid));
CREATE TABLE IF NOT EXISTS konsum_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL, sitzung_am TEXT NOT NULL,
    schritte INTEGER NOT NULL DEFAULT 0, treffer INTEGER NOT NULL DEFAULT 0);
"""


def ensure_schema(db):
    db.executescript(DDL)
    db.commit()


def verbuche(db_path, identity, *, schritte, treffer, now=None):
    with Store(db_path) as store:
        ensure_schema(store.db)
        jetzt = repo.iso(now or repo.now())
        store.db.execute('INSERT INTO konsum_log (identity, sitzung_am, schritte, treffer) VALUES (?,?,?,?)',
                         (identity, jetzt, schritte, treffer))
        store.db.execute('UPDATE accounts SET letzter_konsum=?, konsum_sitzungen=COALESCE(konsum_sitzungen,0)+1,'
                         ' warmth_score=MIN(1.0, COALESCE(warmth_score,0)+?) WHERE identity=?',
                         (jetzt, 0.1 if treffer else 0.03, identity))
        store.db.commit()