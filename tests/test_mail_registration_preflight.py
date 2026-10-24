"""Round 31: reject invalid mailbox registrations before runtime or browser I/O."""
import contextlib
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import accountvault, mailanbieter, signup
from socialscraper.config import Settings
from socialscraper.net.errors import FinalError
from socialscraper.store import Store


class MailRegistrationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'SS_REQUIRE_PROXY': '0'}))
        for target in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, target, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.db = self.root / 'data/test.sqlite'
        self.cfg = Settings(db_path=str(self.db), profiles_dir=str(self.root / 'data/profiles'))
        with Store(self.db):
            pass
        self.provider = SimpleNamespace(SIGNUP_URL='https://registration.example.test/',
                                        open_signup=MagicMock(),
                                        wunsch_adresse=MagicMock(return_value='test@web.de'),
                                        run=MagicMock(return_value={}))
        self.backend, self.im = MagicMock(), MagicMock()
        from socialscraper.net.proxy import Proxy
        self.im.proxy_for.return_value = Proxy.parse('http://127.0.0.1:9')
        self.stack.enter_context(patch.object(mailanbieter, 'ANBIETER', {'web.de': self.provider}))
        self.stack.enter_context(patch.object(mailanbieter, '_dismiss'))
        self.stack.enter_context(patch('socialscraper.mailanbieter.unitedinternet.raise_if_rejected'))
        def confirmed(_page,ctx):
            ctx.account_confirmed=True;ctx.password_filled=True
            return {'adresse':ctx.adresse}
        self.provider.run.side_effect=confirmed
        self.confirmed=confirmed
        self.stack.enter_context(patch.object(mailanbieter.MailCtx,'require_known_password'))
        self.access=MagicMock();self.access.check_access.return_value=0
        self.stack.enter_context(patch('socialscraper.mailbox.build',return_value=self.access))
        self.add_real_mailbox = accountvault.add_postfach
        self.save = self.stack.enter_context(patch.object(accountvault, 'add_postfach'))
        self.runtime = self.stack.enter_context(patch('socialscraper.cli.make_runtime',
                                                     return_value=(self.im, self.backend, [])))
        self.stack.enter_context(patch('socialscraper.cli.shutdown_runtime'))

    def create(self, *, supplied_backend=True, **kwargs):
        return mailanbieter.create(self.cfg, 'web.de', im=self.im,
                                   backend=self.backend if supplied_backend else None,
                                   registrierungsdaten=kwargs.pop('registrierungsdaten', {'vorname':'Test','nachname':'Person','geburtsdatum':'1995-01-01','geschlecht':'keine Angabe'}),
                                   adresse=kwargs.pop('adresse', 'test@web.de'), **kwargs)

    def assert_no_start(self):
        self.im.get.assert_not_called()
        self.backend.session.assert_not_called()
        self.provider.run.assert_not_called()
        self.save.assert_not_called()
        self.runtime.assert_not_called()

    def reset_calls(self):
        for mock in (self.im, self.backend, self.provider.run, self.save, self.runtime):
            mock.reset_mock()

    def test_cancellation_with_existing_backend_prevents_external_start(self):
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.create(stop=lambda: True)
        self.assert_no_start()

    def test_cancellation_without_backend_prevents_runtime_creation(self):
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.create(supplied_backend=False, stop=lambda: True)
        self.assert_no_start()

    def test_invalid_timeouts_are_rejected_before_runtime_identity_session_or_save(self):
        invalid = [('nan', float('nan')), ('infinity', float('inf')), ('negative_infinity', float('-inf')),
                   ('zero', 0), ('negative', -1), ('boolean', True), ('text', 'abc'), ('huge_integer', 10 ** 1000)]
        for supplied in (True, False):
            for label, value in invalid:
                with self.subTest(supplied_backend=supplied, timeout=label):
                    self.reset_calls()
                    with self.assertRaises(ValueError):
                        self.create(supplied_backend=supplied, timeout=value)
                    self.assert_no_start()

    def test_positive_float_timeout_is_preserved_and_registration_still_runs(self):
        result = self.create(timeout=2.5)
        self.assertEqual(result['adresse'], 'test@web.de')
        self.assertEqual(self.provider.run.call_args.args[1].timeout, 2.5)
        self.backend.session.assert_called_once()
        self.provider.run.assert_called_once()
        self.save.assert_called_once()

    def test_saved_privacy_choice_reaches_mail_provider_context(self):
        from socialscraper.privacy_consent import save
        save(self.db, True)
        self.create()
        self.assertIs(self.provider.run.call_args.args[1].accept_privacy, True)

    def test_provider_entry_failure_prevents_form_run_and_pool_storage(self):
        self.provider.open_signup = MagicMock(side_effect=FinalError('Entry unavailable'))
        with self.assertRaisesRegex(FinalError, 'Entry unavailable'):
            self.create()
        self.provider.open_signup.assert_called_once()
        self.backend.page.return_value.goto.assert_not_called()
        self.provider.run.assert_not_called(); self.save.assert_not_called()

    def test_failed_registration_keeps_credentials_but_uses_new_identity_on_retry(self):
        self.provider.run.side_effect = FinalError('Fixture interrupted')
        with self.assertRaises(FinalError):self.create(adresse=None)
        first = self.provider.run.call_args.args[1]
        first_identity = self.im.get.call_args.args[0]
        self.provider.run.side_effect = self.confirmed
        self.create(adresse=None, registrierungsdaten={'vorname':'Corrected','nachname':'Person','geburtsdatum':'1995-01-01','geschlecht':'keine Angabe'})
        again = self.provider.run.call_args.args[1]
        self.assertEqual((first.adresse, first.passwort), (again.adresse, again.passwort))
        self.assertNotEqual(first_identity, self.im.get.call_args.args[0])
        self.assertEqual(again.vorname, 'Corrected')
        from socialscraper.registration_attempts import _load
        self.assertFalse(_load(self.db)['drafts'])

    def test_failure_report_records_actual_form_step_and_preserves_error(self):
        error = FinalError('Fixture provider rejected')
        def failed(page, ctx):
            ctx.progress('E-Mail-Adresse')
            ctx.hinweis('Fixture validation pending')
            raise error
        self.provider.run.side_effect = failed
        with self.assertRaises(FinalError) as caught:self.create()
        self.assertIs(caught.exception, error)
        from socialscraper.failure_reports import latest
        item = latest(self.db, 'web.de')
        self.assertEqual(item['stage'], 'E-Mail-Adresse')
        self.assertEqual(item['notice'], 'Fixture validation pending')
        self.provider.run.assert_called_once(); self.save.assert_not_called()

    def test_other_provider_uses_shared_entry_without_direct_goto(self):
        del self.provider.open_signup
        with patch('socialscraper.entry_rules.open_registration') as entry:
            self.create()
        self.assertEqual(entry.call_args.args[1:3], ('web.de', self.provider.SIGNUP_URL))
        self.backend.page.return_value.goto.assert_not_called()

    def test_missing_or_invalid_personal_details_prevent_external_start(self):
        for values in ({}, {'vorname':'Offline','nachname':'Fixture','geburtsdatum':'31.02.1995'}, 'PRIVATE_INVALID'):
            with self.subTest(kind=type(values).__name__), self.assertRaises(ValueError) as error:
                self.create(supplied_backend=False,registrierungsdaten=values)
            self.assertNotIn('PRIVATE_INVALID',str(error.exception))
            self.assert_no_start()

    def test_explicit_personal_details_reach_the_provider_without_random_defaults(self):
        self.create(registrierungsdaten={'vorname':'Offline','nachname':'Fixture','geburtsdatum':'29.02.1996','ort':'Fixturestadt','geschlecht':'keine Angabe'})
        ctx=self.provider.run.call_args.args[1]
        self.assertEqual((ctx.vorname,ctx.nachname),('Offline','Fixture'))
        self.assertEqual(ctx.registrierungsdaten['geburtsdatum'],'1996-02-29')
        self.assertEqual(ctx.registrierungsdaten['ort'],'Fixturestadt')

    def test_explicit_address_is_trimmed_before_provider_and_storage(self):
        result = self.create(adresse='  test@web.de  ')
        self.assertEqual(result['adresse'], 'test@web.de')
        self.assertEqual(self.provider.run.call_args.args[1].adresse, 'test@web.de')
        self.assertEqual(self.save.call_args.args[1]['adresse'], 'test@web.de')

    def test_known_pool_address_is_rejected_before_external_start(self):
        self.add_real_mailbox(self.db, {'adresse': 'TEST@WEB.DE', 'passwort': 'test-only'})
        with self.assertRaises((ValueError, FinalError)):
            self.create(adresse='test@web.de')
        self.assert_no_start()

    def test_known_pool_address_is_rejected_before_runtime_creation(self):
        self.add_real_mailbox(self.db, {'adresse': 'TEST@WEB.DE', 'passwort': 'test-only'})
        with self.assertRaises((ValueError, FinalError)):
            self.create(supplied_backend=False, adresse='test@web.de')
        self.assert_no_start()

    def test_invalid_address_structure_is_rejected_but_provider_case_is_accepted(self):
        invalid = ['x@y@web.de', 'test name@web.de', 'test\tname@web.de',
                   'test\x00name@web.de', 'test\nname@web.de']
        for address in invalid:
            with self.subTest(address=address):
                self.reset_calls()
                with self.assertRaises(ValueError):
                    self.create(adresse=address)
                self.assert_no_start()
        self.reset_calls()
        result = self.create(adresse='test@WEB.DE')
        self.assertEqual(result['adresse'].casefold(), 'test@web.de')
        self.assertEqual(self.provider.run.call_args.args[1].adresse.casefold(), 'test@web.de')
        self.provider.run.assert_called_once()
        self.save.assert_called_once()

    def test_cancellation_during_navigation_stops_before_wait_provider_and_save(self):
        cancelled = [False]
        page = self.backend.page.return_value

        def navigating(*_args, **_kwargs):
            cancelled[0] = True

        self.provider.open_signup.side_effect = navigating
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.create(stop=lambda: cancelled[0])
        self.backend.session.assert_called_once()
        self.provider.open_signup.assert_called_once()
        page.goto.assert_not_called()
        page.wait_for_timeout.assert_not_called()
        self.provider.run.assert_not_called()
        self.save.assert_not_called()

    def test_signup_shared_guard_rejects_cancel_and_nonfinite_timeout_before_runtime(self):
        cases = [('cancelled', True, 600, FinalError),
                 ('nan', False, float('nan'), ValueError),
                 ('infinity', False, float('inf'), ValueError)]
        for label, cancelled, timeout, expected in cases:
            with self.subTest(case=label), \
                    patch.object(signup, 'MODULE', {'reddit': SimpleNamespace()}), \
                    patch.object(signup, '_browser_lauf') as browser:
                self.reset_calls()
                with self.assertRaises(expected) as error:
                    signup.create(self.cfg, 'reddit', backend=None, im=self.im,
                                  email='test@web.de', name='preflight-signup', warmup='aus',
                                  stop=lambda: cancelled, timeout=timeout)
                if cancelled:
                    self.assertIn('Abgebrochen', str(error.exception))
                self.assert_no_start()
                browser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
