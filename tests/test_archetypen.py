import os, random, tempfile, unittest
from socialscraper import archetypen, repo
from socialscraper.store import Store


class ArchetypenTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.ordner.name, 'test.db')
        with Store(self.db) as store:
            archetypen.ensure_schema(store.db)
            for i in range(400):
                repo.add_account(store.db, 'tiktok', f'Konto {i}', identity=f'tiktok_{i:04d}')

    def tearDown(self):
        self.ordner.cleanup()

    def test_schema_ist_wiederholbar(self):
        with Store(self.db) as store:
            archetypen.ensure_schema(store.db)
            archetypen.ensure_schema(store.db)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM archetypen').fetchone()[0], len(archetypen.ARCHETYPEN))

    def test_zuweisung_ist_deterministisch(self):
        archetypen.zuweisen(self.db)
        with Store(self.db) as store:
            vorher = store.db.execute('SELECT identity, alter, geschlecht, geraet FROM accounts ORDER BY id').fetchall()
        archetypen.zuweisen(self.db, nur_ohne=False)
        with Store(self.db) as store:
            nachher = store.db.execute('SELECT identity, alter, geschlecht, geraet FROM accounts ORDER BY id').fetchall()
        self.assertEqual(vorher, nachher)

    def test_nur_ohne_ueberschreibt_nicht(self):
        self.assertEqual(archetypen.zuweisen(self.db), 400)
        self.assertEqual(archetypen.zuweisen(self.db), 0)

    def test_alter_hat_kern_und_auslauf(self):
        archetypen.zuweisen(self.db)
        with Store(self.db) as store:
            zeilen = store.db.execute('SELECT alter FROM accounts').fetchall()
        kerne = auslaeufe = 0
        for (alter,) in zeilen:
            passend = [d for d in archetypen.ARCHETYPEN.values()
                       if abs(alter - d['alter'][0]) <= d['alter'][1]]
            if passend:
                kerne += 1
            elif any(abs(alter - d['alter'][0]) <= d['alter'][1] * 3 for d in archetypen.ARCHETYPEN.values()):
                auslaeufe += 1
        self.assertGreater(kerne / len(zeilen), 0.55)
        self.assertGreater(auslaeufe / len(zeilen), 0.15)

    def test_kein_archetyp_ist_nur_auf_einem_geraet(self):
        for daten in archetypen.ARCHETYPEN.values():
            for geraet in ('web', 'app'):
                self.assertGreater(daten['geraet'][geraet], 0.0)

    def test_nebenarchetypen_haupt_ist_eindeutig(self):
        archetypen.zuweisen(self.db)
        with Store(self.db) as store:
            haupt = store.db.execute("SELECT COUNT(*) FROM account_archetypen WHERE rolle='haupt'").fetchone()[0]
            neben = store.db.execute("SELECT COUNT(*) FROM account_archetypen WHERE rolle='neben'").fetchone()[0]
        self.assertEqual(haupt, 400)
        self.assertLessEqual(neben, 800)

    def test_mischung_bleibt_unter_dem_zielwert(self):
        archetypen.zuweisen(self.db)
        for befund in archetypen.pruefen(self.db):
            self.assertLessEqual(befund['wert'], archetypen.ZIELWERT,
                                 f"{befund['links']} → {befund['rechts']} zu vorhersagbar")

    def test_kreuztabelle_zaehlt_alles(self):
        archetypen.zuweisen(self.db)
        tabelle = archetypen.kreuztabelle(self.db, 'archetyp', 'geraet')
        self.assertEqual(sum(sum(z.values()) for z in tabelle.values()), 400)

    def test_archetyp_von_kennt_namen(self):
        archetypen.zuweisen(self.db)
        daten = archetypen.archetyp_von(self.db, 'tiktok_0000')
        self.assertIn(daten['haupt'], archetypen.ARCHETYPEN)
        self.assertIn(daten['verhalten_typ'], archetypen.VERHALTEN)


if __name__ == '__main__':
    unittest.main()