"""No network: final mailbox proof, recovery and Android cancellation boundaries."""
import contextlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from socialscraper import accountvault, mailanbieter, mailbox, registration_attempts as drafts
from socialscraper.config import Settings
from socialscraper.net.proxy import Proxy
from socialscraper.net.errors import FinalError


class MailCompletion(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.cfg = Settings(db_path=str(root/'data/test.db'), profiles_dir=str(root/'data/profiles'))
        self.proxy = Proxy.parse('http://127.0.0.1:9')
        self.im, self.backend = MagicMock(), MagicMock()
        self.im.proxy_for.return_value = self.proxy
        self.provider = SimpleNamespace(DOMAENE='web.de', SIGNUP_URL='https://example.invalid',
                                        open_signup=MagicMock(), run=MagicMock(side_effect=self.registered))
        self.stack.enter_context(patch.object(mailanbieter, 'ANBIETER', {'web.de': self.provider}))
        self.stack.enter_context(patch.object(mailanbieter, '_dismiss'))
        self.stack.enter_context(patch('socialscraper.mailanbieter.unitedinternet.raise_if_rejected'))
        self.stack.enter_context(patch.object(mailanbieter.MailCtx, 'require_known_password'))
        self.box = MagicMock(); self.box.check_access.return_value=0
        self.build = self.stack.enter_context(patch.object(mailbox, 'build', return_value=self.box))
        self.messages = []

    def registered(self, page, ctx):
        ctx.account_confirmed=True; ctx.password_filled=True
        return {'adresse':ctx.adresse}

    def create(self, **options):
        return mailanbieter.create(self.cfg, 'web.de', adresse='fixture@web.de', passwort='FIXTURE_SECRET',
            registrierungsdaten={'vorname':'Fixture','nachname':'Person','geburtsdatum':'1995-01-01','geschlecht':'keine Angabe'},
            backend=self.backend, im=self.im, melden=self.messages.append, **options)

    def test_provider_return_without_completion_evidence_cannot_enter_pool(self):
        self.provider.run.side_effect=None; self.provider.run.return_value={}
        with self.assertRaises(FinalError): self.create()
        self.assertFalse(accountvault.postfaecher(self.cfg.db_path))
        self.box.check_access.assert_not_called()

    def test_success_requires_real_mail_check_through_identical_proxy(self):
        result=self.create()
        self.box.check_access.assert_called_once()
        self.assertEqual(self.build.call_args.kwargs['proxy'].url, self.proxy.url)
        self.assertEqual(result['adresse'], 'fixture@web.de')
        self.assertFalse(drafts._load(self.cfg.db_path)['drafts'])
        self.assertEqual(len(accountvault.postfaecher(self.cfg.db_path)),1)

    def test_failed_mail_check_preserves_completion_and_only_rechecks_on_retry(self):
        self.box.check_access.side_effect=mailbox.MailboxError('FIXTURE_SECRET arbitrary transport dump')
        with self.assertRaises(FinalError) as caught:self.create()
        self.assertNotIn('FIXTURE_SECRET',str(caught.exception))
        pending=next(iter(drafts._load(self.cfg.db_path)['drafts'].values()))
        self.assertEqual(pending['status'],'awaiting_mail')
        self.assertFalse(accountvault.postfaecher(self.cfg.db_path))
        first_identity=pending['identity']
        self.box.check_access.side_effect=None
        self.im.proxy_for.return_value=Proxy.parse('http://127.0.0.1:10')
        self.create()
        self.provider.run.assert_called_once(); self.backend.session.assert_called_once()
        self.im.get.assert_called_once()
        self.assertEqual(self.build.call_args.kwargs['proxy'].url,self.proxy.url)
        self.assertEqual(self.box.check_access.call_count,2)
        self.assertNotIn('FIXTURE_SECRET','\n'.join(self.messages))

    def test_wrong_address_does_not_confirm_or_store_mailbox(self):
        def wrong(page,ctx):
            ctx.account_confirmed=True;ctx.password_filled=True
            return {'adresse':'other@web.de'}
        self.provider.run.side_effect=wrong
        with self.assertRaises(FinalError):self.create()
        self.assertFalse(accountvault.postfaecher(self.cfg.db_path))
        self.box.check_access.assert_not_called()

    def test_cancellation_after_mail_read_prevents_pool_save_but_keeps_checkpoint(self):
        stopped=[False]
        self.box.check_access.side_effect=lambda:stopped.__setitem__(0,True) or 0
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):self.create(stop=lambda:stopped[0])
        self.assertFalse(accountvault.postfaecher(self.cfg.db_path))
        self.assertEqual(next(iter(drafts._load(self.cfg.db_path)['drafts'].values()))['status'],'awaiting_mail')

    def test_browser_close_error_after_confirmation_does_not_repeat_registration(self):
        self.backend.session.return_value.__exit__.side_effect=RuntimeError('fixture-close')
        with self.assertRaises(RuntimeError):self.create()
        self.backend.session.return_value.__exit__.side_effect=None
        self.create()
        self.provider.run.assert_called_once()

    def test_missing_proxy_stops_before_browser_open(self):
        self.im.proxy_for.return_value=None
        with self.assertRaisesRegex(FinalError,'Proxy'):self.create()
        self.backend.session.assert_not_called()

    def test_unclean_running_attempt_requires_review_instead_of_new_registration(self):
        key,_,_=drafts.get_or_create(self.cfg.db_path,'web.de',address='fixture@web.de',password='FIXTURE_SECRET',
            identity='abcdef',explicit_address=True,explicit_password=True,details={},route='browser',country='de',mobile=False)
        drafts.status(self.cfg.db_path,key,'running')
        with self.assertRaisesRegex(ValueError,'prüfen'):self.create()
        self.backend.session.assert_not_called()


    def test_recovery_needs_no_personal_details_or_matching_browser_engine(self):
        self.box.check_access.side_effect=mailbox.MailboxError('fixture')
        with self.assertRaises(FinalError):self.create()
        self.box.check_access.side_effect=None
        self.cfg.engine='firefox'
        result=mailanbieter.create(self.cfg,'web.de',adresse='fixture@web.de',registrierungsdaten={},
                                   backend=None,im=None,melden=self.messages.append)
        self.assertEqual(result['adresse'],'fixture@web.de')
        self.provider.run.assert_called_once();self.backend.session.assert_called_once()

    def test_observed_account_with_unknown_password_requires_review(self):
        def reached(page,ctx):
            ctx.account_confirmed=True
            raise FinalError('Das verwendete Kennwort ist unklar.')
        self.provider.run.side_effect=reached
        with self.assertRaises(FinalError):self.create()
        pending=next(iter(drafts._load(self.cfg.db_path)['drafts'].values()))
        self.assertEqual(pending['status'],'needs_review')
        with self.assertRaisesRegex(ValueError,'prüfen'):self.create()
        self.provider.run.assert_called_once()

    def test_corrupt_completion_cannot_reopen_a_browser_or_read_mail(self):
        self.box.check_access.side_effect=mailbox.MailboxError('fixture')
        with self.assertRaises(FinalError):self.create()
        path=drafts._path(self.cfg.db_path);original=json.loads(path.read_text())
        for field,value in (('adresse',42),('notizen',{}),('imap',[]),('passwort',None)):
            data=json.loads(json.dumps(original));key=next(iter(data['drafts']))
            data['drafts'][key]['completion']['entry'][field]=value
            path.write_text(json.dumps(data))
            with self.subTest(field=field),self.assertRaises(ValueError):self.create()
        self.provider.run.assert_called_once();self.box.check_access.assert_called_once()


    def test_crash_after_pool_commit_resumes_without_duplicate_storage(self):
        with patch.object(drafts,'completed',side_effect=OSError('fixture-exit')):
            with self.assertRaises(OSError):self.create()
        self.assertEqual(len(accountvault.postfaecher(self.cfg.db_path)),1)
        self.create()
        self.provider.run.assert_called_once();self.backend.session.assert_called_once()
        self.assertEqual(len(accountvault.postfaecher(self.cfg.db_path)),1)
        self.assertFalse(drafts._load(self.cfg.db_path)['drafts'])
        self.assertEqual(self.box.check_access.call_count,2)


    def test_changed_saved_imap_settings_are_not_overwritten_or_claimed_verified(self):
        with patch.object(drafts,'completed',side_effect=OSError('fixture-exit')):
            with self.assertRaises(OSError):self.create()
        data=accountvault.load(self.cfg.db_path)
        data['postfaecher'][0]['imap']={'host':'changed.example.invalid'}
        accountvault.save(self.cfg.db_path,data)
        with self.assertRaisesRegex(FinalError,'abweichender'):self.create()
        self.assertEqual(accountvault.postfaecher(self.cfg.db_path)[0]['imap']['host'],'changed.example.invalid')
        self.box.check_access.assert_called_once();self.provider.run.assert_called_once()


class AndroidCancellation(unittest.TestCase):
    def test_cancel_before_registration_cannot_clear_existing_app_data(self):
        from socialscraper.android import tiktok_app
        adb=MagicMock();ctx=SimpleNamespace(stop=lambda:True,melden=MagicMock())
        with patch.object(tiktok_app.time,'sleep'):
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):tiktok_app.registrieren(adb,ctx)
        adb.daten_loeschen.assert_not_called()

    def test_cancel_during_mail_read_never_types_code(self):
        from socialscraper.android import tiktok_app
        stopped=[False];adb=MagicMock();adb.warte_auf.return_value=True
        ctx=SimpleNamespace(stop=lambda:stopped[0],melden=MagicMock())
        ctx.code_holen=lambda **kw:stopped.__setitem__(0,True) or '123456'
        with patch.object(tiktok_app,'_feld_fuellen') as fill, patch.object(tiktok_app.time,'sleep'):
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):tiktok_app._code_schritt(adb,ctx)
        fill.assert_not_called()


    def test_cancel_after_field_tap_never_types_any_value(self):
        from socialscraper.android import tiktok_app
        stopped=[False];adb=MagicMock();adb.groesse.return_value=(1080,1920)
        adb.tap.side_effect=lambda *args:stopped.__setitem__(0,True)
        with patch.object(tiktok_app.time,'sleep'):
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):
                tiktok_app._feld_fuellen(adb,'FIXTURE_SECRET',stop=lambda:stopped[0])
        adb.tippen.assert_not_called()

    def test_cancel_during_long_pause_is_seen_after_one_short_sleep(self):
        from socialscraper.android import tiktok_app
        stopped=[False]
        with patch.object(tiktok_app.time,'sleep',side_effect=lambda _:stopped.__setitem__(0,True)) as sleep:
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):
                tiktok_app._pause(12,stop=lambda:stopped[0])
        sleep.assert_called_once_with(0.2)


class RegistrationEvents(unittest.TestCase):
    def test_event_payload_never_contains_credentials_and_deduplicates_phase(self):
        from socialscraper import registration_progress as progress
        messages=[];ctx=SimpleNamespace(anbieter='web.de',adresse='PRIVATE_ADDRESS',passwort='PRIVATE_PASSWORD',melden=messages.append)
        progress.update(ctx,'checking_mail');progress.update(ctx,'checking_mail')
        self.assertEqual(len(messages),1)
        self.assertNotIn('PRIVATE',messages[0])
        data=progress.parse(messages[0]);self.assertEqual(set(data),{'version','phase','provider','pid'})

    def test_malformed_or_unexpected_payloads_are_rejected(self):
        from socialscraper import registration_progress as progress
        good={'version':1,'phase':'form','provider':'web.de','pid':123}
        for changes in ({'pid':True},{'version':True},{'phase':[]},{'provider':{}},{'secret':'private'}, {'phase':'invented'}):
            with self.subTest(fields=list(changes)):
                self.assertIsNone(progress.parse(progress.PREFIX+json.dumps({**good,**changes})))

    def test_status_accepts_only_owned_worker_then_yields_to_android_preparation(self):
        from socialscraper.ui.app import App
        from socialscraper import registration_progress as progress
        from socialscraper.android.progress import PREFIX as ANDROID
        app=SimpleNamespace(job=SimpleNamespace(proc=SimpleNamespace(pid=123)),current_label='Postfach',set_status=MagicMock())
        def event(pid):return progress.PREFIX+json.dumps({'version':1,'provider':'web.de','phase':'pending_mail','pid':pid})
        with patch('socialscraper.net.deadline.descendants',return_value=[(456,'own-worker')]):
            App.log(app,event(999));app.set_status.assert_not_called()
            App.log(app,event(456));self.assertEqual(app.current_registration_phase,'pending_mail')
            self.assertIn('Mailabruf',app.set_status.call_args.args[0])
            App.log(app,ANDROID+json.dumps({'version':1,'phase':'booting','pid':456}))
            self.assertIsNone(app.current_registration_phase)
            self.assertEqual(app.current_phase,'booting')
            app.job.proc=None;App.log(app,event(456));self.assertIsNone(app.current_registration_phase)

    def test_next_job_clears_previous_registration_phase(self):
        from socialscraper.ui.app import App
        app=SimpleNamespace(steps=[('Mail',[])],cancelled=False,current_registration_phase='pending_mail',
                            set_status=MagicMock(),log=MagicMock())
        with patch('socialscraper.ui.app.core.Job'):
            App._next_step(app)
        self.assertIsNone(app.current_registration_phase)
