# tests/test_kandidaten.py
import os
import random
import sqlite3
import tempfile
import unittest

from socialscraper import freigabe as fg
from socialscraper import profilgenerator as pg
from socialscraper.mail_pruefen import AMPEL_BELEGT, AMPEL_FREI, Befund


class Kette:
    """Sagt bei den ersten N geprueften Adressen 'belegt', danach 'frei'."""

    def __init__(self, belegte=0, standard=AMPEL_FREI, grund="550 frei"):
        self.belegte = belegte
        self.standard = standard
        self.grund = grund
        self.stufe = "smtp"
        self.gesehen = []

    def befund(self, adresse):
        self.gesehen.append(adresse)
        if len(self.gesehen) <= self.belegte:
            return Befund(adresse, "smtp", AMPEL_BELEGT, "250 belegt")
        return Befund(adresse, "smtp", self.standard, self.grund)


class Basis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.db = os.path.join(self.ordner, "test.db")
        self.rng = random.Random(7)
        self.quellen = {"namen": ["Hans-Dieter;Müller;m"],
                        "strassen": ["Hauptstrasse;10115;Berlin"]}

    def kandidaten(self, identity="tiktok_konto_0001"):
        with sqlite3.connect(self.db) as conn:
            return [z[0] for z in conn.execute(
                'SELECT adresse FROM "adresskandidaten" WHERE identity = ? ORDER BY rang',
                (identity,))]

    def benutzte_adresse(self, identity="tiktok_konto_0001"):
        with sqlite3.connect(self.db) as conn:
            return conn.execute('SELECT adresse FROM "postfaecher" WHERE identity = ?',
                                (identity,)).fetchone()[0]


class BauTest(Basis):
    def test_kette_wird_angelegt(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        self.assertEqual(len(self.kandidaten()), pg.KANDIDATEN)

    def test_erste_wahl_ist_die_hauptadresse(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        self.assertEqual(self.kandidaten()[0], self.benutzte_adresse())

    def test_kette_ist_in_sich_eindeutig(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        kette = self.kandidaten()
        self.assertEqual(len(kette), len(set(kette)))

    def test_ketten_verschiedener_konten_kollidieren_nicht(self):
        for i in range(1, 13):
            pg.anlegen(self.db, f"tiktok_konto_{i:04d}", self.rng, quellen=self.quellen)
        alle = []
        for i in range(1, 13):
            alle += self.kandidaten(f"tiktok_konto_{i:04d}")
        self.assertEqual(len(alle), len(set(alle)))

    def test_umlaute_werden_uebersetzt(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        for adresse in self.kandidaten():
            self.assertNotIn("ü", adresse)
            self.assertNotIn("ä", adresse)

    def test_bindestrich_im_vornamen_bleibt(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        self.assertTrue(any("hans-dieter" in a for a in self.kandidaten()))

    def test_kandidatenzahl_einstellbar(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen,
                   kandidaten=4)
        self.assertEqual(len(self.kandidaten()), 4)


class AnbieterTendenzTest(unittest.TestCase):
    """Anbieter ist eine Tendenz, keine Grenze. Nie 0, nie 1."""

    def setUp(self):
        self.rng = random.Random(99)

    def _anteil(self, alter, anbieter, male=4000):
        treffer = sum(1 for _ in range(male)
                      if pg.anbieter_ziehen(self.rng, alter) == anbieter)
        return treffer / male

    def test_jung_hat_viel_gmail_aber_nicht_nur(self):
        anteil = self._anteil(18, "gmail.com")
        self.assertGreater(anteil, 0.30)
        self.assertLess(anteil, 0.75)

    def test_alt_hat_wenig_gmail_aber_immer_noch_etwas(self):
        anteil = self._anteil(50, "gmail.com")
        self.assertGreater(anteil, 0.04)
        self.assertLess(anteil, 0.30)

    def test_gmx_steigt_mit_dem_alter(self):
        self.assertGreater(self._anteil(50, "gmx.de"), self._anteil(18, "gmx.de") * 1.4)

    def test_gmail_faellt_mit_dem_alter(self):
        self.assertGreater(self._anteil(18, "gmail.com"), self._anteil(50, "gmail.com") * 1.8)

    def test_t_online_ist_eher_alt(self):
        self.assertGreater(self._anteil(50, "t-online.de"),
                           self._anteil(18, "t-online.de"))

    def test_junger_kann_gmx_bekommen(self):
        self.assertGreater(self._anteil(19, "gmx.de"), 0.03)

    def test_alter_kann_gmail_bekommen(self):
        self.assertGreater(self._anteil(48, "gmail.com"), 0.03)

    def test_jeder_anbieter_in_jedem_alter_moeglich(self):
        for alter in (16, 25, 35, 45, 52):
            for anbieter in pg.ANBIETER:
                self.assertGreater(pg.anbieter_gewichte(alter, self.rng)[anbieter],
                                   0.0, f"{anbieter}@{alter}")

    def test_gleiches_alter_gibt_verschiedene_anbieter(self):
        gesehen = {pg.anbieter_ziehen(self.rng, 25) for _ in range(60)}
        self.assertGreaterEqual(len(gesehen), 4)

    def test_gewichte_kleben_nicht_an_null_oder_eins(self):
        for alter in (16, 52):
            for gewicht in pg.anbieter_gewichte(alter, self.rng).values():
                self.assertTrue(0.0 < gewicht < 1.0)


class TorTest(Basis):
    def setUp(self):
        super().setUp()
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)

    def test_erste_freie_gewinnt(self):
        ergebnis = fg.freigeben(self.db, "tiktok_konto_0001", Kette(), rng=self.rng)
        self.assertEqual(ergebnis["rang"], 0)
        self.assertEqual(ergebnis["zustand"], fg.ZUSTAND_BEREIT)

    def test_belegte_erste_greift_zweite(self):
        ergebnis = fg.freigeben(self.db, "tiktok_konto_0001", Kette(belegte=1),
                                rng=self.rng)
        self.assertEqual(ergebnis["rang"], 1)
        self.assertEqual(ergebnis["adresse"], self.kandidaten()[1])
        self.assertEqual(ergebnis["verworfen"], 1)
        self.assertEqual(self.benutzte_adresse(), self.kandidaten()[1])

    def test_drei_belegte_greift_vierte(self):
        ergebnis = fg.freigeben(self.db, "tiktok_konto_0001", Kette(belegte=3),
                                rng=self.rng)
        self.assertEqual(ergebnis["rang"], 3)
        self.assertEqual(self.benutzte_adresse(), self.kandidaten()[3])

    def test_person_bleibt_bei_ausweichadresse(self):
        ergebnis = fg.freigeben(self.db, "tiktok_konto_0001", Kette(belegte=2),
                                rng=self.rng)
        with sqlite3.connect(self.db) as conn:
            vorname, nachname = conn.execute(
                'SELECT vorname, nachname FROM "profile_settings" WHERE identity = ?',
                ("tiktok_konto_0001",)).fetchone()
        self.assertEqual(vorname, "Hans-Dieter")
        self.assertEqual(nachname, "Müller")
        self.assertIn("hans", ergebnis["adresse"].lower())

    def test_kette_leer_wird_nachgelegt(self):
        ergebnis = fg.freigeben(self.db, "tiktok_konto_0001",
                                Kette(belegte=pg.KANDIDATEN), rng=self.rng)
        self.assertEqual(ergebnis["zustand"], fg.ZUSTAND_BEREIT)
        self.assertGreater(len(self.kandidaten()), pg.KANDIDATEN)
        self.assertGreaterEqual(ergebnis["rang"], pg.KANDIDATEN)

    def test_ganze_kette_belegt_landet_in_nacharbeit(self):
        ergebnis = fg.freigeben(self.db, "tiktok_konto_0001",
                                Kette(belegte=999), rng=self.rng)
        self.assertEqual(ergebnis["zustand"], fg.ZUSTAND_NACHARBEIT)

    def test_benutzte_adresse_ist_markiert(self):
        fg.freigeben(self.db, "tiktok_konto_0001", Kette(belegte=2), rng=self.rng)
        with sqlite3.connect(self.db) as conn:
            markiert = conn.execute(
                'SELECT rang FROM "adresskandidaten" WHERE identity = ?'
                ' AND in_benutzung = 1', ("tiktok_konto_0001",)).fetchone()[0]
        self.assertEqual(markiert, 2)

    def test_belegte_glieder_sind_vermerkt(self):
        fg.freigeben(self.db, "tiktok_konto_0001", Kette(belegte=2), rng=self.rng)
        with sqlite3.connect(self.db) as conn:
            belegte = conn.execute(
                'SELECT COUNT(*) FROM "adresskandidaten" WHERE identity = ?'
                ' AND ampel = ?', ("tiktok_konto_0001", AMPEL_BELEGT)).fetchone()[0]
        self.assertEqual(belegte, 2)


class EntnehmenTest(Basis):
    def setUp(self):
        super().setUp()
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        fg.freigeben(self.db, "tiktok_konto_0001", Kette(), rng=self.rng)

    def test_entnehmen_nutzt_naechstes_glied_wenn_weg(self):
        alt = self.benutzte_adresse()
        daten = fg.entnehmen(self.db, rng=self.rng)
        self.assertIsNotNone(daten)
        self.assertNotEqual(daten["adresse"], alt)
        self.assertEqual(daten["adresse"], self.kandidaten()[1])

    def test_entnehmen_bleibt_bei_freier_adresse(self):
        self.assertEqual(fg.entnehmen(self.db, rng=self.rng)["adresse"],
                         self.benutzte_adresse())


class StandTest(Basis):
    def test_stand_zeigt_ketten(self):
        pg.anlegen(self.db, "tiktok_konto_0001", self.rng, quellen=self.quellen)
        fg.freigeben(self.db, "tiktok_konto_0001", Kette(belegte=2), rng=self.rng)
        ergebnis = fg.stand(self.db)
        self.assertEqual(ergebnis["bereit"], 1)
        self.assertEqual(ergebnis["davon_verworfen"], 2)


if __name__ == "__main__":
    unittest.main()