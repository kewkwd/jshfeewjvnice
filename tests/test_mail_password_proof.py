"""Credential proof gates mailbox completion without collecting manual passwords."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from socialscraper import mailanbieter
from socialscraper.config import Settings
from socialscraper.mailanbieter import MailCtx, unitedinternet
from socialscraper.net.errors import FinalError


GENERATED = 'FAKE_GENERATED_PASSWORD_FOR_TEST'
ERROR_SECRET = 'FakeSecretInBrowserException'


class MailPasswordProofTests(unittest.TestCase):
    def page(self):
        def evaluate(_expression, arg=None):
            if arg is None:
                return 'https://register.web.de'
            return 'armed' if isinstance(arg, dict) else True
        return SimpleNamespace(url='https://register.web.de/success', main_frame=object(),
            expose_binding=Mock(), add_init_script=Mock(), evaluate=Mock(side_effect=evaluate),
            wait_for_timeout=Mock())

    def context(self, page):
        backend = SimpleNamespace(page=Mock(return_value=page))
        return MailCtx('web.de', 'Test', 'Person', 'test123@web.de', GENERATED,
                       'assisted', 'de', False, 30, backend, domaene='web.de', melden=Mock())

    def fill_password(self, ctx):
        with patch.object(mailanbieter, '_fill', return_value='#password'):
            self.assertEqual(ctx.fill(['#password'], GENERATED), '#password')

    def assert_proof_refused_without_secrets(self, ctx):
        with self.assertRaises(FinalError) as caught:
            ctx.require_known_password()
        exposed = ' '.join((str(caught.exception), repr(caught.exception),
                            repr(ctx.notizen), repr(ctx.melden.call_args_list)))
        for secret in (GENERATED, ERROR_SECRET):
            self.assertNotIn(secret, exposed)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_generated_password_with_active_guard_can_be_confirmed(self):
        ctx = self.context(self.page())
        self.fill_password(ctx)
        self.assertTrue(ctx.password_filled)
        ctx.require_known_password()

    def test_password_filled_flag_alone_is_not_credential_proof(self):
        ctx = self.context(self.page())
        ctx.password_filled = True
        self.assert_proof_refused_without_secrets(ctx)

    def test_javascript_only_backend_cannot_prove_the_password(self):
        page = SimpleNamespace(evaluate=Mock(return_value='armed'))
        ctx = self.context(page)
        self.fill_password(ctx)
        self.assertTrue(ctx.password_filled)
        self.assert_proof_refused_without_secrets(ctx)
        page.evaluate.assert_not_called()

    def test_guard_installation_exceptions_do_not_disclose_secrets(self):
        for method in ('expose_binding', 'add_init_script'):
            with self.subTest(method=method):
                page = self.page()
                getattr(page, method).side_effect = RuntimeError(ERROR_SECRET)
                ctx = self.context(page)
                self.fill_password(ctx)
                self.assert_proof_refused_without_secrets(ctx)

    def test_guard_evaluation_exceptions_do_not_disclose_secrets(self):
        for stage in ('origin', 'watch'):
            with self.subTest(stage=stage):
                page = self.page()
                error = RuntimeError(ERROR_SECRET)
                page.evaluate.side_effect = (error if stage == 'origin' else
                                             ['https://register.web.de', error])
                ctx = self.context(page)
                self.fill_password(ctx)
                self.assert_proof_refused_without_secrets(ctx)

    def test_final_guard_flush_exception_refuses_proof_without_disclosing_secrets(self):
        page = self.page()
        ctx = self.context(page)
        self.fill_password(ctx)
        page.evaluate.side_effect = RuntimeError(ERROR_SECRET)
        self.assert_proof_refused_without_secrets(ctx)

    def test_webde_wait_requires_current_proof_after_a_manual_edit(self):
        page = self.page()
        ctx = self.context(page)
        self.fill_password(ctx)
        ctx.require_known_password()
        changed = page.expose_binding.call_args.args[1]

        def manual_edit_signal():
            changed({'page': page, 'frame': page.main_frame})

        with patch.object(unitedinternet, '_confirmed_mailbox', return_value=True), \
                patch.object(time, 'monotonic', side_effect=[0.0, 1.0]), \
                patch.object(ctx, 'require_known_password', wraps=ctx.require_known_password) as verify:
            with self.assertRaises(FinalError):
                unitedinternet._warte_bis_fertig(page, ctx, tick=manual_edit_signal)
        verify.assert_called_once_with()
        self.assertFalse(ctx.password_filled)
        page.wait_for_timeout.assert_not_called()

    def assert_provider_confirmation_cannot_save(self, platform, domain, authenticated_url):
        page = SimpleNamespace(url=authenticated_url, goto=Mock(), wait_for_timeout=Mock())
        page.goto.side_effect = lambda url, **_kwargs: setattr(page, 'url', url)
        backend, identity_manager = MagicMock(), MagicMock()
        backend.page.return_value = page
        from socialscraper.net.proxy import Proxy
        identity_manager.proxy_for.return_value = Proxy.parse('http://127.0.0.1:9')
        address = 'test123@' + domain

        def confirm(_page, ctx):
            page.url = authenticated_url
            ctx.password_filled = True  # Legacy flag, without an installed proof guard.
            ctx.bestaetigen(platform)
            return {'adresse': address}

        provider = SimpleNamespace(SIGNUP_URL='https://signup.example.test/',
                                   open_signup=Mock(),
                                   DOMAENE=domain, run=Mock(side_effect=confirm))
        from socialscraper import profile_settings
        from profile_fixture import PERSON
        with patch.object(profile_settings, 'update', return_value={**profile_settings.defaults('proof-test'), 'person':PERSON}), \
                patch.object(mailanbieter, 'ANBIETER', {domain: provider}), \
                patch.object(mailanbieter, '_dismiss'), \
                patch.object(mailanbieter, '_confirmed_address', return_value=True) as confirmed, \
                patch('socialscraper.net.cookies.CookieJar.authenticated', return_value=True), \
                patch.object(mailanbieter.accountvault, 'add_postfach') as save, \
                patch.object(time, 'monotonic', side_effect=[0.0, 1.0]):
            with self.assertRaises(FinalError):
                mailanbieter._create_validated(
                    Settings(db_path='unused-proof-test.sqlite'), domain,
                    kennung='proof-test', adresse=address, vorname='Test', nachname='Person',
                    passwort=GENERATED, mode='assisted', land='de', mobile=False, timeout=30,
                    backend=backend, im=identity_manager, melden=Mock(),
                )
            confirmed.assert_called_once_with(page, platform, address)
            save.assert_not_called()
        target = ('https://myaccount.google.com/' if platform == 'google'
                  else 'https://outlook.live.com/mail/')
        self.assertEqual(page.goto.call_args.args[0], target)

    def test_google_account_confirmation_without_password_proof_does_not_save(self):
        self.assert_provider_confirmation_cannot_save(
            'google', 'gmail.com', 'https://myaccount.google.com/'
        )

    def test_outlook_account_confirmation_without_password_proof_does_not_save(self):
        self.assert_provider_confirmation_cannot_save(
            'outlook', 'outlook.com', 'https://account.microsoft.com/'
        )


if __name__ == '__main__':
    unittest.main()
