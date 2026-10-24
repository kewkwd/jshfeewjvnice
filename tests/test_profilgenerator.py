# tests/test_profilgenerator.py
import os
import random
import sqlite3
import tempfile
import unittest
from datetime import date

from socialscraper import profilgenerator as pg
from socialscraper.mail_pruefen import AMPEL_BELEGT, AMPEL_UNKLAR, Befund


class PrueferFake:
    def __init__(self, belegte=(), unklare=()):
        self.belegte = set(belegte)
        self.unklare = set(unklare)
        self.gefragt = []
        self.stufe = "smtp"

    def befund(self, adresse):
        self.gefragt.append(adresse)
        if adresse in self.belegte:
            return Befund(adresse, "smtp", AMPEL_BELEGT, "250 belegt")
        if adresse in self.unklare:
            return Befund(adresse, "smtp", AMPEL_UNKLAR, "catch-all")
        return Befund(adresse, "smtp", "frei", "550 frei")


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.db = os.path.join(self.ordner, "test.db")
        self.rng = random.Random(4711)
        self.quellen = {"namen": ["Anna;Schmidt;w", "Lukas;Weber;m"],
                        "strassen": ["Hauptstrasse;10115;Berlin"]}


class ProfilTest(Basis):
    def test_profil_hat_alle_felder(self):
        daten = pg.profil_bauen(self.rng, quellen=self.quellen)
        for feld in ("vorname", "nachname", "geschlecht", "geburtsdatum", "alter",
                     "strasse", "hausnummer", "plz", "ort", "adresse", "passwort",
                     "benutzer", "geraet", "heimatweg", "tempo", "kandidaten"):
            self.assertIn(feld, daten)
        self.assertTrue(daten["adresse"].endswith(tuple(f"@{a}" for a in pg.ANBIETER)))

    def test_alter_bleibt_in_der_spanne(self):
        for _ in range(400):
            alter = pg.alter_ziehen(self.rng)
            self.assertGreaterEqual(alter, pg.ALTER_SPANNE[0])
            self.assertLessEqual(alter, pg.ALTER_SPANNE[1])

    def test_alter_verdichtet_um_den_kern(self):
        werte = [pg.alter_ziehen(self.rng) for _ in range(4000)]
        nah = sum(1 for a in werte if abs(a - pg.ALTER_KERN) <= 6)
        self.assertGreater(nah / len(werte), 0.45)

    def test_geburtsdatum_passt_zum_alter(self):
        stichtag = date(2026, 10, 24)
        for _ in range(50):
            geburt = pg.geburtsdatum_ziehen(self.rng, stichtag)
            alter = pg._alter_am(geburt, stichtag)
            self.assertGreaterEqual(alter, pg.ALTER_SPANNE[0] - 1)
            self.assertLessEqual(alter, pg.ALTER_SPANNE[1] + 1)

    def test_vorname_passt_zum_geschlecht(self):
        vornamen, _ = pg.namen_lesen(self.quellen)
        for _ in range(50):
            geschlecht = pg.geschlecht_ziehen(self.rng)
            vorname = pg.vorname_ziehen(self.rng, vornamen, geschlecht)
            passende = [v for v, g in vornamen if g == geschlecht]
            self.assertIn(vorname, passende)

    def test_passwort_ist_brauchbar(self):
        for _ in range(50):
            passwort = pg.passwort_bauen(self.rng)
            self.assertGreaterEqual(len(passwort), 11)
            self.assertTrue(any(z.isdigit() for z in passwort))
            self.assertTrue(any(z.isupper() for z in passwort))
            self.assertTrue(any(z.islower() for z in passwort))

    def test_tempo_ist_ein_band_kein_wert(self):
        for band in pg.profil_bauen(self.rng, quellen=self.quellen)["tempo"].values():
            self.assertLess(band["unten"], band["mitte"])
            self.assertLess(band["mitte"], band["oben"])

    def test_tempo_band_haelt_mindestbreite(self):
        from socialscraper import profile_settings as ps
        for _ in range(200):
            self.assertEqual(ps.pruefen(ps.tempo_profil(self.rng)), [])

    def test_geraet_und_heimatweg_passen_zusammen(self):
        for _ in range(100):
            daten = pg.profil_bauen(self.rng, quellen=self.quellen)
            self.assertIn(daten["heimatweg"], pg.HEIMATWEGE[daten["geraet"]])


class SchreibenTest(Basis):
    def test_anlegen_schreibt_zeilen(self):
        daten = pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        with sqlite3.connect(self.db) as conn:
            for tabelle in ("profile_settings", "postfaecher", "account_secrets"):
                anzahl = conn.execute(f'SELECT COUNT(*) FROM "{tabelle}"').fetchone()[0]
                self.assertEqual(anzahl, 1, tabelle)
        self.assertEqual(daten["identity"], "tiktok_konto_0001")

    def test_anlegen_ist_wiederholbar(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        with sqlite3.connect(self.db) as conn:
            anzahl = conn.execute('SELECT COUNT(*) FROM "profile_settings"').fetchone()[0]
            adressen = conn.execute('SELECT adresse FROM "postfaecher"').fetchall()
            kandidaten = conn.execute('SELECT COUNT(*) FROM "adresskandidaten"').fetchone()[0]
        self.assertEqual(anzahl, 1)
        self.assertEqual(len(adressen), 1)
        self.assertEqual(kandidaten, pg.KANDIDATEN)

    def test_vorhandene_adressen_schliesst_eigene_aus(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        pg.anlegen(self.db, "tiktok_konto_0002", self.rng, quellen=self.quellen)
        ohne_eigene = pg.vorhandene_adressen(self.db, ohne="tiktok_konto_0001")
        with sqlite3.connect(self.db) as conn:
            eigene = {z[0] for z in conn.execute(
                'SELECT adresse FROM "adresskandidaten" WHERE identity = ?',
                ("tiktok_konto_0001",))}
            fremd = conn.execute('SELECT adresse FROM "postfaecher" WHERE identity = ?',
                                 ("tiktok_konto_0002",)).fetchone()[0]
        self.assertFalse(eigene & ohne_eigene)
        self.assertIn(fremd, ohne_eigene)

    def test_stapel_zaehlt_hoch(self):
        ergebnisse = pg.stapel(self.db, 5, praefix="tiktok_konto_", stellen=4, saat=99)
        kennungen = [e["identity"] for e in ergebnisse]
        self.assertEqual(kennungen[0], "tiktok_konto_0001")
        self.assertEqual(len(set(kennungen)), 5)

    def test_stapel_setzt_fort(self):
        pg.stapel(self.db, 3, saat=1)
        self.assertEqual(pg.stapel(self.db, 2, saat=2)[0]["identity"], "tiktok_konto_0004")

    def test_erneuern_haelt_kennungen(self):
        pg.stapel(self.db, 4, saat=1)
        ergebnisse = pg.erneuern(self.db, saat=2)
        self.assertEqual([e["identity"] for e in ergebnisse],
                         ["tiktok_konto_0001", "tiktok_konto_0002",
                          "tiktok_konto_0003", "tiktok_konto_0004"])

    def test_erneuern_mit_bereich(self):
        pg.stapel(self.db, 5, saat=1)
        ergebnisse = pg.erneuern(self.db, von=2, bis=3, saat=2)
        self.assertEqual(len(ergebnisse), 2)

    def test_erneuern_ersetzt_kette_nicht_verdoppelt(self):
        pg.stapel(self.db, 2, saat=1)
        pg.erneuern(self.db, saat=2)
        with sqlite3.connect(self.db) as conn:
            anzahl = conn.execute('SELECT COUNT(*) FROM "adresskandidaten"').fetchone()[0]
        self.assertEqual(anzahl, 2 * pg.KANDIDATEN)

    def test_trocken_schreibt_nichts(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng,
                   quellen=self.quellen, schreiben=False)
        with sqlite3.connect(self.db) as conn:
            try:
                anzahl = conn.execute('SELECT COUNT(*) FROM "profile_settings"').fetchone()[0]
            except sqlite3.OperationalError:
                anzahl = 0
        self.assertEqual(anzahl, 0)

    def test_quellen_lesen_toleriert_fehlende_tabelle(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE datenblock (block TEXT, wert TEXT)')
            conn.execute("INSERT INTO datenblock VALUES ('daten-namen', 'Lena;w')")
        self.assertEqual(pg.quellen_laden(self.db)["namen"], ["Lena;w"])

    def test_namen_lesen_mit_drei_feldern(self):
        vornamen, nachnamen = pg.namen_lesen({"namen": ["Lena;Mueller;w"]})
        self.assertEqual(vornamen, [("Lena", "w")])
        self.assertEqual(nachnamen, ["Mueller"])


if __name__ == "__main__":
    unittest.main()