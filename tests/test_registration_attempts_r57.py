import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from socialscraper import registration_attempts as drafts


class RegistrationAttempts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'data/test.db'

    def draft(self, **options):
        args = dict(address='fixture@web.de', password='FAKE_TEST_ONLY', identity='abcdef',
                    explicit_address=False, explicit_password=False, details={'vorname':'Fixture'},
                    route='browser', country='de', mobile=False)
        args.update(options)
        return drafts.get_or_create(self.db, 'web.de', **args)

    def test_retry_reuses_profile_address_password_despite_new_candidates(self):
        key, first, reused = self.draft(); self.assertFalse(reused)
        key2, again, reused = self.draft(address='different@web.de', password='OTHER', identity='123456')
        self.assertTrue(reused); self.assertEqual(key, key2); self.assertEqual(first, again)

    def test_explicit_different_addresses_do_not_share_profiles(self):
        one = self.draft(explicit_address=True)
        two = self.draft(explicit_address=True, address='other@web.de', identity='123456')
        self.assertNotEqual(one[0], two[0]); self.assertNotEqual(one[1]['identity'], two[1]['identity'])

    def test_explicit_retry_of_generated_address_reuses_original_draft(self):
        first = self.draft()
        again = self.draft(explicit_address=True, address='FIXTURE@WEB.DE', identity='123456')
        self.assertEqual(first[:2], again[:2]); self.assertTrue(again[2])

    def test_corrected_personal_fields_keep_attempt_and_changed_options_require_reset(self):
        first = self.draft()
        again = self.draft(details={'vorname':'Corrected', 'plz':'Fixture'}, identity='123456')
        self.assertEqual(first[:2], again[:2]); self.assertTrue(again[2])
        with self.assertRaisesRegex(ValueError, 'Profiloptionen'):
            self.draft(country='us')

    def test_changed_explicit_password_is_rejected_without_overwrite(self):
        self.draft(explicit_address=True)
        before = drafts._path(self.db).read_bytes()
        with self.assertRaisesRegex(ValueError, 'anderes Kennwort') as caught:
            self.draft(explicit_address=True, explicit_password=True, password='PRIVATE_CHANGED')
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(drafts._path(self.db).read_bytes(), before)

    def test_corrupt_version_and_profile_path_fail_closed(self):
        key, _, _ = self.draft()
        original = json.loads(drafts._path(self.db).read_text())
        for corrupt in ({'version':2, 'drafts':{}}, {'version':1, 'drafts':{key:{**original['drafts'][key], 'identity':'../../escape'}}}):
            with self.subTest(kind=corrupt['version']):
                drafts._path(self.db).write_text(json.dumps(corrupt))
                with self.assertRaises(ValueError): self.draft()
                self.assertEqual(json.loads(drafts._path(self.db).read_text()), corrupt)

    def test_success_removes_only_matching_draft(self):
        key, _, _ = self.draft()
        other, _, _ = self.draft(explicit_address=True, address='other@web.de')
        drafts.completed(self.db, key)
        self.assertEqual(set(drafts._load(self.db)['drafts']), {other})

    def test_explicit_reset_preserves_browser_files_and_other_providers(self):
        self.draft()
        profile = self.db.parent / 'profiles/mail_webde_abcdef/mail/fixture'
        profile.parent.mkdir(parents=True); profile.write_text('cookies fixture')
        drafts.reset(self.db, 'gmx.net'); self.assertEqual(len(drafts._load(self.db)['drafts']), 1)
        drafts.reset(self.db, 'web.de'); self.assertFalse(drafts._load(self.db)['drafts'])
        self.assertEqual(profile.read_text(), 'cookies fixture')

    def test_failed_protected_save_prevents_a_new_attempt(self):
        with patch.object(drafts.accountvault, '_write', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError): self.draft()
        self.assertFalse(drafts._path(self.db).exists())
