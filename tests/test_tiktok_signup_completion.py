"""Signup proof and persisted provider handle; no external services or devices."""
import contextlib
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import accountvault
from socialscraper.android import tiktok_app
from socialscraper.net.errors import FinalError
from test_avd_account_session_r50 import tree
import test_signup_mail_access_r61 as mail_fixtures


class AndroidSignupProof(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stopped=False
        self.ctx=SimpleNamespace(email='fixture@example.invalid',password='FIXTURE_ONLY',
            stop=lambda:self.stopped,timeout=5,melden=MagicMock(),tiktok_username='')
        self.adb=MagicMock()
        self.adb.baum.return_value=tree()
        # The old substring check accepts even a foreign app or a bio mention.
        self.adb.finde.return_value=(100,100)
        for name in ('_wegklicken','_feld_fuellen','_weiter','_geburtsdatum','_pause'):
            self.stack.enter_context(patch.object(tiktok_app,name))
        self.stack.enter_context(patch.object(tiktok_app.time,'sleep'))
        self.stack.enter_context(patch.object(tiktok_app,'_tippe',return_value=True))
        self.stack.enter_context(patch.object(tiktok_app,'_raetsel',return_value=False))
        self.stack.enter_context(patch.object(tiktok_app,'_code_schritt',return_value=False))

    def run_signup(self):return tiktok_app.registrieren(self.adb,self.ctx)

    def test_edit_profile_text_without_handle_is_not_success(self):
        self.adb.baum.return_value=tree(handle=None)
        self.assertFalse(self.run_signup())
        self.assertEqual(self.ctx.tiktok_username,'')

    def test_verified_own_profile_records_provider_handle(self):
        self.adb.baum.return_value=tree(handle='Provider.Created')
        self.assertTrue(self.run_signup())
        self.assertEqual(self.ctx.tiktok_username,'provider.created')
        self.assertNotIn('teil',self.adb.tippe_auf.call_args.kwargs)

    def test_foreign_app_cannot_confirm_signup(self):
        self.adb.baum.return_value=tree(package='foreign.example')
        self.assertFalse(self.run_signup())

    def test_ambiguous_profile_cannot_confirm_signup(self):
        self.adb.baum.return_value=tree(extra_handle='other.provider')
        self.assertFalse(self.run_signup())

    def test_bio_cannot_supply_provider_handle(self):
        self.adb.baum.return_value=tree(kind='bio')
        self.assertFalse(self.run_signup())

    def test_cancel_after_back_prevents_navigation_and_snapshot(self):
        self.adb.taste.side_effect=lambda *_:setattr(self,'stopped',True)
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):self.run_signup()
        self.adb.tippe_auf.assert_not_called();self.adb.baum.assert_not_called()

    def test_cancel_after_profile_click_prevents_snapshot(self):
        def click(**kwargs):self.stopped=True;return True
        self.adb.tippe_auf.side_effect=click
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):self.run_signup()
        self.adb.baum.assert_not_called()

    def test_cancel_during_snapshot_does_not_record_success(self):
        def read():self.stopped=True;return tree()
        self.adb.baum.side_effect=read
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):self.run_signup()
        self.assertEqual(self.ctx.tiktok_username,'')


class SignupHandlePersistence(unittest.TestCase):
    def setUp(self):
        self.fixture=mail_fixtures.SignupMailAccess('runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def provider(self,handle):
        def complete(cfg,ctx,**kwargs):
            ctx.tiktok_username=handle
            return True,'fixture-avd'
        self.fixture.app.side_effect=complete

    def test_created_provider_handle_survives_for_later_app_login(self):
        self.provider(' @Provider.Created ')
        result=self.fixture.create()
        saved=accountvault.get(self.fixture.cfg.db_path,result['identity'])
        self.assertEqual(saved['tiktok_username'],'provider.created')
        self.assertNotEqual(saved['tiktok_username'],result['identity'])
        self.assertTrue(saved['passwort'])

    def test_app_true_without_provider_identity_cannot_save_success(self):
        self.provider('')
        with self.assertRaisesRegex(FinalError,'TikTok-Benutzername') as error:self.fixture.create()
        self.assertIn('Stand im Emulator',str(error.exception))
        self.assertNotIn('Konten →',str(error.exception))
        self.fixture.unchanged()

    def test_invalid_provider_identity_cannot_save_account_or_echo_input(self):
        self.provider('PRIVATE_INVALID@example.invalid')
        with self.assertRaises(FinalError) as error:self.fixture.create()
        self.assertNotIn('PRIVATE_INVALID',str(error.exception))
        self.fixture.unchanged()
