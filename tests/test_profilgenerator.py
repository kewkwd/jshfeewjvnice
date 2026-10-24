"""Tests für den Datengenerator: Determinismus, Alter, Tempo, Eindeutigkeit."""
import base64
import gzip
import json
import os
import tempfile
import types
import unittest

from socialscraper import archetypen, profilgenerator, repo
from socialscraper.store import Store


def _html(pfad):
    namen = json.dumps({'v': ['Anna', 'Max'], 'n': ['Muster', 'Beispiel', 'Mustermann']})
    zeilen = ('10115\tBerlin\tHauptstrasse\tNebenweg\n'
              '80331\tMünchen\tSendlinger Strasse\tLeopoldstrasse\n')
    verdichtet = base64.b64encode(gzip.compress(zeilen.encode('utf-8'))).decode()
    with open(pfad, 'w', encoding='utf-8') as strom:
        strom.write(f'<html><script id="daten-namen">{namen}</script>'
                    f'<script id="daten-strassen">{verdichtet}</script></html>')


class ProfilTest(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.cfg = types.SimpleNamespace(db_path=os.path.join(self.ordner.name, 'test.db'),
                                         profiles_dir=os.path.join(self.ordner.name, 'profiles'))
        self.html = os.path.join(self.ordner.name, 'generator.html')
        _html(self.html)
        with Store(self.cfg.db_path) as store:
            archetypen.ensure_schema(store.db)
            for i in range(1, 31):
                repo.add_account(store.db, 'tiktok', f'konto {i:04d}')
        archetypen.zuweisen(self.cfg.db_path)
        self.quelle = profilgenerator.Quelle(self.html)

    def tearDown(self):
        self.ordner.cleanup()

    def test_rechnen_ist_deterministisch(self):
        erst = profilgenerator.rechnen(self.cfg, 'tiktok_konto_0001', quelle=self.quelle)
        zweit = profilgenerator.rechnen(self.cfg, 'tiktok_konto_0001', quelle=self.quelle)
        self.assertEqual(erst, zweit)

    def test_alter_passt_zum_archetyp(self):
        for i in range(1, 21):
            identity = f'tiktok_konto_{i:04d}'
            profil = profilgenerator.rechnen(self.cfg, identity, quelle=self.quelle)
            self.assertGreaterEqual(profil['alter'], archetypen.ALTER_MIN)
            self.assertLessEqual(profil['alter'], archetypen.ALTER_MAX)
            jahr = int(profil['geburt'][:4])
            self.assertTrue(2020 > jahr > 1950)

    def test_geschlecht_passt_zum_vornamen(self):
        for i in range(1, 21):
            profil = profilgenerator.rechnen(self.cfg, f'tiktok_konto_{i:04d}', quelle=self.quelle)
            self.assertIn(profil['vorname'], profilgenerator.VORNAMEN[profil['geschlecht']])

    def test_tempo_liegt_in_den_grenzen(self):
        for i in range(1, 21):
            profil = profilgenerator.rechnen(self.cfg, f'tiktok_konto_{i:04d}', quelle=self.quelle)
            for name, (low, high) in profilgenerator.profile_settings.LIMITS.items():
                self.assertTrue(low <= profil['tempo'][name] <= high)

    def test_anlegen_schreibt_profil_und_zugang(self):
        from socialscraper import accountvault, profile_settings
        profil = profilgenerator.anlegen(self.cfg, 'tiktok_konto_0001', quelle=self.quelle)
        daten = profile_settings.load(self.cfg.profiles_dir, 'tiktok_konto_0001')
        self.assertEqual(daten['person']['vorname'], profil['vorname'])
        self.assertEqual(daten['person']['geburtsdatum'], profil['geburt'])
        geheim = accountvault.get(self.cfg.db_path, 'tiktok_konto_0001')
        self.assertEqual(geheim['email'], profil['adresse'])
        self.assertTrue(geheim['passwort'])
        self.assertTrue(geheim['email_passwort'])

    def test_adressen_sind_eindeutig(self):
        adressen = set()
        for i in range(1, 31):
            profil = profilgenerator.rechnen(self.cfg, f'tiktok_konto_{i:04d}', quelle=self.quelle)
            adressen.add(profil['adresse'].casefold())
        self.assertEqual(len(adressen), 30)

    def test_stadt_auswahl_und_fehler(self):
        profil = profilgenerator.rechnen(self.cfg, 'tiktok_konto_0001', quelle=self.quelle, stadt='Berlin')
        self.assertEqual(profil['anschrift']['ort'], 'Berlin')
        with self.assertRaises(profilgenerator.ProfilError):
            profilgenerator.rechnen(self.cfg, 'tiktok_konto_0001', quelle=self.quelle, stadt='Hintertupfingen')

    def test_stapel_und_pruefen(self):
        bericht = profilgenerator.stapel(self.cfg, 10, quelle=self.quelle)
        self.assertEqual(bericht['angelegt'], 10)
        ergebnis = profilgenerator.pruefen(self.cfg)
        self.assertEqual(ergebnis['mit_profil'], 10)
        self.assertEqual([b for b in ergebnis['befunde'] if b['art'] == 'zugang'], [])


if __name__ == '__main__':
    unittest.main()