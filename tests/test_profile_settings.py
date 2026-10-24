# tests/test_profile_settings.py
import random
import unittest

from socialscraper import profile_settings as ps


class BandTest(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(4242)

    def test_mitte_liegt_immer_im_band(self):
        for _ in range(2000):
            baender = ps.tempo_profil(self.rng)
            for wert, band in baender.items():
                self.assertLess(band["unten"], band["mitte"], wert)
                self.assertLess(band["mitte"], band["oben"], wert)

    def test_band_haelt_mindestbreite(self):
        for _ in range(2000):
            for band in ps.tempo_profil(self.rng).values():
                self.assertGreaterEqual(band["oben"] - band["unten"],
                                        band["mitte"] * ps.MINDESTBREITE * 0.99)

    def test_band_bleibt_in_der_werkzeugspanne(self):
        for _ in range(2000):
            for wert, band in ps.tempo_profil(self.rng).items():
                unten, oben = ps.LIMITS[wert]
                self.assertGreaterEqual(band["unten"], unten, wert)
                self.assertLessEqual(band["oben"], oben, wert)

    def test_baender_streuen_zwischen_profilen(self):
        breiten = set()
        for _ in range(300):
            band = ps.tempo_profil(self.rng)["click_ms"]
            breiten.add(round(band["oben"] - band["unten"], 3))
        self.assertGreater(len(breiten), 60)

    def test_lage_streut(self):
        lagen = set()
        for _ in range(300):
            band = ps.tempo_profil(self.rng)["typing_cps"]
            lagen.add(round((band["mitte"] - band["unten"])
                            / (band["oben"] - band["unten"]), 2))
        self.assertGreater(len(lagen), 20)

    def test_kleine_mitte_bleibt_breit_genug(self):
        """Das war der Befund bei drei Konten: kleiner click_ms-Wert."""
        unten, oben = ps.LIMITS["click_ms"]
        for mitte in (38, 40, 45, 50, 60, 80, 90, 100):
            for _ in range(200):
                band = ps.band_bauen(self.rng, mitte, unten, oben)
                self.assertLess(band["unten"], band["mitte"])
                self.assertLess(band["mitte"], band["oben"])
                self.assertGreaterEqual(band["oben"] - band["unten"],
                                        band["mitte"] * ps.MINDESTBREITE * 0.99)
                self.assertGreaterEqual(band["unten"], unten)

    def test_mitte_direkt_an_der_grenze(self):
        for wert, (unten, oben) in ps.LIMITS.items():
            for mitte in (unten + 0.5, unten + 2, oben - 2, oben - 0.5):
                band = ps.band_bauen(self.rng, mitte, unten, oben)
                self.assertLess(band["unten"], band["mitte"])
                self.assertLess(band["mitte"], band["oben"])
                self.assertGreaterEqual(band["unten"], unten)
                self.assertLessEqual(band["oben"], oben)

    def test_pruefen_ist_still(self):
        for _ in range(500):
            self.assertEqual(ps.pruefen(ps.tempo_profil(self.rng)), [])

    def test_mitte_klebt_nicht_an_der_spanne(self):
        for _ in range(500):
            for wert, band in ps.tempo_profil(self.rng).items():
                unten, oben = ps.LIMITS[wert]
                self.assertGreater(band["mitte"], unten)
                self.assertLess(band["mitte"], oben)


class ZiehenTest(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(1717)

    def test_ziehung_bleibt_im_band(self):
        for _ in range(2000):
            band = ps.tempo_profil(self.rng)["mouse_px_s"]
            for _ in range(5):
                wert = ps.tempo_ziehen(self.rng, band)
                self.assertGreaterEqual(wert, band["unten"])
                self.assertLessEqual(wert, band["oben"])

    def test_mitte_wird_haeufiger_gezogen(self):
        band = {"mitte": 50.0, "unten": 0.0, "oben": 100.0}
        werte = [ps.tempo_ziehen(self.rng, band) for _ in range(6000)]
        mitte_naehe = sum(1 for w in werte if 25 <= w <= 75)
        self.assertGreater(mitte_naehe / len(werte), 0.60)

    def test_grenzen_werden_selten_erreicht(self):
        band = {"mitte": 50.0, "unten": 0.0, "oben": 100.0}
        werte = [ps.tempo_ziehen(self.rng, band) for _ in range(6000)]
        aussen = sum(1 for w in werte if w < 5 or w > 95)
        self.assertLess(aussen / len(werte), 0.06)

    def test_tempo_ziehen_profil(self):
        baender = ps.tempo_profil(self.rng)
        gezogen = ps.tempo_ziehen_profil(self.rng, baender)
        self.assertEqual(set(gezogen), set(baender))
        for wert, zahl in gezogen.items():
            self.assertGreaterEqual(zahl, baender[wert]["unten"])
            self.assertLessEqual(zahl, baender[wert]["oben"])


class FlachTest(unittest.TestCase):
    def test_als_flach_macht_drei_spalten(self):
        flach = ps.als_flach(ps.tempo_profil(random.Random(1)))
        self.assertEqual(len(flach), 9)
        for wert in ps.LIMITS:
            for teil in ("mitte", "unten", "oben"):
                self.assertIn(f"{wert}_{teil}", flach)


class PrueferTest(unittest.TestCase):
    def test_zu_schmales_band_wird_gemeldet(self):
        meldungen = ps.pruefen({"click_ms": {"mitte": 100.0, "unten": 99.0, "oben": 100.5}})
        self.assertTrue(any("zu schmal" in m for m in meldungen))

    def test_mitte_ausserhalb_wird_gemeldet(self):
        meldungen = ps.pruefen({"click_ms": {"mitte": 100.0, "unten": 120.0, "oben": 200.0}})
        self.assertTrue(any("Mitte" in m for m in meldungen))

    def test_ausserhalb_der_spanne_wird_gemeldet(self):
        meldungen = ps.pruefen({"click_ms": {"mitte": 50.0, "unten": 10.0, "oben": 50.0}})
        self.assertTrue(any("Werkzeugspanne" in m for m in meldungen))


if __name__ == "__main__":
    unittest.main()