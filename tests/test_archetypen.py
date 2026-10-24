"""Tests für die Archetypen-Ebene: Determinismus, Tendenz mit Auslauf, Mischung."""
import os
import random
import tempfile
import unittest

from socialscraper import archetypen, repo
from socialscraper.store import Store

ANZAHL = 400


class ArchetypenTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.ordner.name, 'test.db')
        self.identities = []
        with Store(self.db) as store:
            archetypen.ensure_schema(store.db)
            for i in range(ANZAHL):
                konto = repo.add_account(store.db, 'tiktok', f'Konto {i:03d}')
                self.identities.append(konto['identity'])
            # Tier setzen, wie personas.zuweisen es täte (hier ohne Personas, deterministisch).
            for konto in repo.list_accounts(store.db, 'tiktok'):
                rng = random.Random(f"{konto['identity']}|tier")
                store.db.execute('UPDATE accounts SET tier=? WHERE id=?',
                                 (rng.choices(('kern', 'sekundaer', 'rand'), weights=(0.20, 0.40, 0.40))[0],
                                  konto['id']))
            store.db.commit()

    def tearDown(self):
        self.ordner.cleanup()

    def test_schema_ist_wiederholbar(self):
        with Store(self.db) as store:
            archetypen.ensure_schema(store.db)
            archetypen.ensure_schema(store.db)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM archetypen').fetchone()[0],
                             len(archetypen.ARCHETYPEN))

    def test_zuweisung_ist_deterministisch(self):
        archetypen.zuweisen(self.db)
        spalten = '"alter", geschlecht, geraet, verhalten_typ, stil'
        with Store(self.db) as store:
            vorher = store.db.execute(f'SELECT identity, {spalten} FROM accounts ORDER BY id').fetchall()
        archetypen.zuweisen(self.db, nur_ohne=False)
        with Store(self.db) as store:
            nachher = store.db.execute(f'SELECT identity, {spalten} FROM accounts ORDER BY id').fetchall()
        self.assertEqual(vorher, nachher)

    def test_nur_ohne_ueberschreibt_nicht(self):
        self.assertEqual(archetypen.zuweisen(self.db), ANZAHL)
        self.assertEqual(archetypen.zuweisen(self.db), 0)

    def test_kurve_hat_kern_und_auslauf(self):
        """Die Ziehung selbst: dichter Kern mit dünnem Auslauf — keine Klammer.

        Der Auslauf beginnt an der Kernkante und reicht darüber hinaus. Deshalb liegt nur
        etwa die Hälfte aller Werte innerhalb von ±Kern; das ist gewollt, denn die
        Auslaufwerte werden bewusst außerhalb gezogen. Gemessen wird deshalb zweierlei:
        die Verdichtung um die Mitte (muss deutlich sein) und der Anteil außerhalb der
        Kernkante (muss wirklich vorhanden sein)."""
        rng = random.Random(1)
        werte = [archetypen._kurve(rng, 20, 3.0, 5.0) for _ in range(20000)]
        dicht = sum(1 for w in werte if abs(w - 20) <= 4.5) / len(werte)
        draussen = sum(1 for w in werte if abs(w - 20) > 3) / len(werte)
        self.assertGreater(dicht, 0.60)        # der Schwerpunkt ist deutlich
        self.assertGreater(draussen, 0.40)     # der Auslauf ist wirklich da
        self.assertGreater(len(set(werte)), 15)
        self.assertGreater(max(werte) - min(werte), 15)

    def test_gezogene_alter_liegen_in_den_weltgrenzen(self):
        archetypen.zuweisen(self.db)
        with Store(self.db) as store:
            werte = [z[0] for z in store.db.execute('SELECT "alter" FROM accounts')]
        self.assertTrue(all(archetypen.ALTER_MIN <= a <= archetypen.ALTER_MAX for a in werte))
        self.assertGreater(len(set(werte)), 20)

    def test_kein_archetyp_ist_nur_auf_einem_geraet(self):
        for daten in archetypen.ARCHETYPEN.values():
            for geraet in ('web', 'app'):
                self.assertGreater(daten['geraet'][geraet], 0.0)

    def test_beide_geraete_kommen_vor(self):
        """Konten dürfen laut Dauerauftrag auch beides besitzen — aber nur als Minderheit."""
        archetypen.zuweisen(self.db)
        with Store(self.db) as store:
            zaehler = dict(store.db.execute('SELECT geraet, COUNT(*) FROM accounts GROUP BY geraet'))
        for wert in archetypen.GERAETE:
            self.assertGreater(zaehler.get(wert, 0), 5, f'kein Konto mit Gerät {wert}')
        self.assertLess(zaehler.get('beide', 0) / ANZAHL, 0.30)

    def test_nebenarchetypen_haupt_ist_eindeutig(self):
        archetypen.zuweisen(self.db)
        with Store(self.db) as store:
            haupt = store.db.execute("SELECT COUNT(*) FROM account_archetypen WHERE rolle='haupt'").fetchone()[0]
            neben = store.db.execute("SELECT COUNT(*) FROM account_archetypen WHERE rolle='neben'").fetchone()[0]
        self.assertEqual(haupt, ANZAHL)
        self.assertLessEqual(neben, ANZAHL * 2)
        self.assertGreater(neben, 0)

    def test_mischung_bleibt_unter_dem_zielwert(self):
        archetypen.zuweisen(self.db)
        for befund in archetypen.pruefen(self.db):
            if befund['urteil'] == 'nur ein Wert':
                continue
            self.assertLessEqual(befund['wert'], archetypen.ZIELWERT + 0.03,
                                 f"{befund['links']} → {befund['rechts']} zu vorhersagbar")

    def test_kreuztabelle_zaehlt_alles(self):
        archetypen.zuweisen(self.db)
        tabelle = archetypen.kreuztabelle(self.db, 'archetyp', 'geraet')
        self.assertEqual(sum(sum(z.values()) for z in tabelle.values()), ANZAHL)

    def test_archetyp_von_kennt_namen(self):
        archetypen.zuweisen(self.db)
        daten = archetypen.archetyp_von(self.db, self.identities[0])
        self.assertIn(daten['haupt'], archetypen.ARCHETYPEN)
        self.assertIn(daten['verhalten_typ'], archetypen.VERHALTEN)


if __name__ == '__main__':
    unittest.main()