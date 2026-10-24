# tests/test_mail_pruefen.py
import os
import tempfile
import unittest

from socialscraper import mail_pruefen as mp


def _mx(hosts):
    return lambda dom, timeout: list(hosts)


def _kein_a(dom, timeout):
    return False


def _smtp(antwort):
    return lambda host, adresse, timeout: antwort


class SyntaxTest(unittest.TestCase):
    def test_gueltig(self):
        for adresse in ("anna.mueller@gmail.com", "a_b-c@web.de", "x1@t-online.de"):
            self.assertTrue(mp.syntax_ok(adresse), adresse)

    def test_ungueltig(self):
        for adresse in ("", "ohne_at", "@web.de", "a@b", "a@@b.de", "a..b@web.de"):
            self.assertFalse(mp.syntax_ok(adresse), adresse)


class StufenTest(unittest.TestCase):
    def test_keine_stufe(self):
        self.assertEqual(mp.pruefen("voellig@kaputt", "keine").ampel, mp.AMPEL_FREI)

    def test_syntax_wird_erkannt(self):
        self.assertEqual(mp.pruefen("kaputt@", "mx").ampel, mp.AMPEL_UNGUELTIG)

    def test_wegwerf_ist_unklar(self):
        self.assertEqual(mp.pruefen("test@mailinator.com", "mx").ampel, mp.AMPEL_UNKLAR)

    def test_domain_ohne_post_ist_ungueltig(self):
        befund = mp.pruefen("a@nixda.de", "mx", mx_fn=_mx([]), a_fn=_kein_a)
        self.assertEqual(befund.ampel, mp.AMPEL_UNGUELTIG)

    def test_mx_stufe_gibt_frei(self):
        befund = mp.pruefen("a@beispiel.de", "mx", mx_fn=_mx(["mx.beispiel.de"]))
        self.assertEqual(befund.ampel, mp.AMPEL_FREI)
        self.assertIn("MX", befund.grund)

    def test_a_record_reicht_als_mailserver(self):
        befund = mp.pruefen("a@beispiel.de", "mx", mx_fn=_mx([]),
                            a_fn=lambda dom, timeout: True)
        self.assertEqual(befund.ampel, mp.AMPEL_FREI)
        self.assertIn("A-Record", befund.grund)

    def test_smtp_550_heisst_frei(self):
        befund = mp.pruefen("a@beispiel.de", "smtp", mx_fn=_mx(["mx.beispiel.de"]),
                            smtp_fn=_smtp((mp.AMPEL_FREI, "550 unbekannt")))
        self.assertEqual(befund.ampel, mp.AMPEL_FREI)

    def test_smtp_250_heisst_belegt(self):
        befund = mp.pruefen("a@beispiel.de", "smtp", mx_fn=_mx(["mx.beispiel.de"]),
                            smtp_fn=_smtp((mp.AMPEL_BELEGT, "250 ok")),
                            merker={}, ca_fn=lambda h, d, t: False)
        self.assertEqual(befund.ampel, mp.AMPEL_BELEGT)

    def test_catch_all_wird_unklar(self):
        befund = mp.pruefen("a@beispiel.de", "smtp", mx_fn=_mx(["mx.beispiel.de"]),
                            smtp_fn=_smtp((mp.AMPEL_BELEGT, "250 ok")),
                            merker={}, ca_fn=lambda h, d, t: True)
        self.assertEqual(befund.ampel, mp.AMPEL_UNKLAR)
        self.assertIn("catch-all", befund.grund)

    def test_unbekannte_stufe(self):
        with self.assertRaises(ValueError):
            mp.pruefen("a@b.de", "irgendwas")


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.mkdtemp()
        self.cache = mp.Cache(os.path.join(self.ordner, "test.db"))

    def test_merken_und_holen(self):
        self.cache.merken(mp.Befund("a@b.de", "mx", mp.AMPEL_BELEGT, "250 ok"))
        wieder = self.cache.holen("a@b.de", "mx")
        self.assertIsNotNone(wieder)
        self.assertEqual(wieder.ampel, mp.AMPEL_BELEGT)
        self.assertTrue(wieder.aus_cache)

    def test_stufe_muss_reichen(self):
        self.cache.merken(mp.Befund("a@b.de", "formal", mp.AMPEL_FREI))
        self.assertIsNone(self.cache.holen("a@b.de", "smtp"))
        self.assertIsNotNone(self.cache.holen("a@b.de", "formal"))

    def test_keine_stufe_wird_nicht_gemerkt(self):
        self.cache.merken(mp.Befund("a@b.de", "keine", mp.AMPEL_FREI))
        self.assertIsNone(self.cache.holen("a@b.de", "keine"))


class PrueferTest(unittest.TestCase):
    def test_zaehlt_mit(self):
        pruefer = mp.Adresspruefer("keine", None)
        for adresse in ("a@b.de", "c@d.de"):
            befund = pruefer.befund(adresse)
            pruefer.zaehlerstand[befund.ampel] += 1
        self.assertEqual(pruefer.zaehlerstand[mp.AMPEL_FREI], 2)
        self.assertIn("frei=2", pruefer.bericht())

    def test_befund_viele(self):
        pruefer = mp.Adresspruefer("keine", None, threads=4)
        befunde = pruefer.befund_viele([f"a{i}@b.de" for i in range(6)])
        self.assertEqual(len(befunde), 6)
        self.assertTrue(all(b.ampel == mp.AMPEL_FREI for b in befunde))

    def test_brauchbar_und_belegt(self):
        self.assertTrue(mp.Befund("a@b.de", "smtp", mp.AMPEL_FREI).brauchbar)
        self.assertTrue(mp.Befund("a@b.de", "smtp", mp.AMPEL_UNKLAR).brauchbar)
        self.assertFalse(mp.Befund("a@b.de", "smtp", mp.AMPEL_BELEGT).brauchbar)
        self.assertTrue(mp.Befund("a@b.de", "smtp", mp.AMPEL_BELEGT).belegt)


class DomainTest(unittest.TestCase):
    def test_domain_von(self):
        self.assertEqual(mp.domain_von("a@Web.DE"), "web.de")
        self.assertEqual(mp.domain_von("kaputt"), "")


if __name__ == "__main__":
    unittest.main()