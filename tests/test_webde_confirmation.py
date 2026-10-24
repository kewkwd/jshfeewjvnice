"""Mailbox completion requires affirmative evidence on an account page."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from socialscraper.mailanbieter import unitedinternet


class WebdeConfirmationTests(unittest.TestCase):
    def confirmed(self, body, url='https://register.web.de/success', *, domain='web.de'):
        page = SimpleNamespace(url=url, inner_text=Mock(return_value=body))
        ctx = SimpleNamespace(adresse='test123@' + domain, domaene=domain, anbieter=domain)
        return unitedinternet._confirmed_mailbox(page, ctx)

    def test_mailbox_not_yet_ready_is_not_confirmation(self):
        self.assertFalse(self.confirmed('Dein Postfach ist noch nicht bereit: test123@web.de'))

    def test_unsuccessful_registration_is_not_confirmation(self):
        self.assertFalse(self.confirmed('Du bist nicht erfolgreich registriert: test123@web.de'))

    def test_future_registration_completion_is_not_confirmation(self):
        self.assertFalse(self.confirmed(
            'Sobald die Registrierung abgeschlossen ist, kannst du test123@web.de verwenden.'
        ))

    def test_support_page_quoting_success_does_not_confirm_an_account(self):
        self.assertFalse(self.confirmed(
            'Beispiel einer Meldung: „Dein Postfach ist bereit: test123@web.de“.',
            'https://hilfe.web.de/kundenservice/registrierung.html',
        ))

    def test_ready_mailbox_confirms_case_insensitive_exact_address(self):
        self.assertTrue(self.confirmed('Dein Postfach ist bereit: TEST123@WEB.DE'))

    def test_successfully_completed_registration_confirms_exact_address(self):
        self.assertTrue(self.confirmed('Registrierung erfolgreich abgeschlossen: test123@web.de'))

    def test_authenticated_inbox_confirms_exact_address(self):
        self.assertTrue(self.confirmed(
            'Posteingang | test123@web.de | Abmelden', 'https://navigator.web.de/mail',
        ))

    def test_wrong_or_similar_addresses_and_insecure_pages_do_not_confirm(self):
        for address in ('other@web.de', 'test1234@web.de', 'xtest123@web.de',
                        'test123+other@web.de', 'test123@web.de.evil.invalid'):
            self.assertFalse(self.confirmed('Dein Postfach ist bereit: ' + address), address)
        for url, body in (
            ('http://register.web.de/success', 'Dein Postfach ist bereit: test123@web.de'),
            ('http://navigator.web.de/mail', 'Posteingang | test123@web.de | Abmelden'),
        ):
            self.assertFalse(self.confirmed(body, url), url)

    def test_negated_completion_is_not_confirmation(self):
        self.assertFalse(self.confirmed(
            'Registrierung ist nicht erfolgreich abgeschlossen: test123@web.de'
        ))

    def test_conditional_readiness_is_not_confirmation(self):
        self.assertFalse(self.confirmed(
            'Wenn dein Postfach bereit ist, kannst du test123@web.de verwenden.'
        ))

    def test_formal_positive_registration_declarations_confirm(self):
        for declaration in ('Ihre Registrierung ist abgeschlossen.',
                            'Sie haben sich erfolgreich registriert.'):
            self.assertTrue(self.confirmed(declaration + '\ntest123@web.de'), declaration)

    def test_gmx_requires_affirmative_evidence_on_an_account_host(self):
        cases = (
            ('https://register.gmx.net/success', 'Dein Postfach ist bereit: test123@gmx.net', True),
            ('https://navigator.gmx.net/mail', 'Posteingang | test123@gmx.net | Abmelden', True),
            ('https://register.gmx.net/success', 'Dein Postfach ist noch nicht bereit: test123@gmx.net', False),
            ('https://hilfe.gmx.net/registrierung', 'Dein Postfach ist bereit: test123@gmx.net', False),
        )
        for url, body, expected in cases:
            self.assertEqual(self.confirmed(body, url, domain='gmx.net'), expected, (url, body))

    def test_mail_prefix_on_an_unapproved_nested_host_is_not_an_inbox(self):
        for body in ('Posteingang | test123@web.de | Abmelden',
                     'Dein Postfach ist bereit: test123@web.de'):
            self.assertFalse(self.confirmed(body, 'https://mail.attacker.web.de/mail'), body)

    def test_navigation_and_body_timeouts_remain_unconfirmed(self):
        ctx = SimpleNamespace(adresse='test123@web.de', domaene='web.de')
        for error in (TimeoutError('body is loading'),
                      RuntimeError('Execution context was destroyed during navigation')):
            page = SimpleNamespace(url='https://register.web.de/success',
                                   inner_text=Mock(side_effect=error))
            self.assertFalse(unitedinternet._confirmed_mailbox(page, ctx), str(error))
        self.assertFalse(self.confirmed('Dein Postfach ist bereit: test123@web.de', 'about:blank'))

    def test_wait_with_known_password_does_not_accept_negated_success(self):
        page = SimpleNamespace(url='https://register.web.de/success',
            inner_text=Mock(return_value='Dein Postfach ist noch nicht bereit: test123@web.de'),
            wait_for_timeout=Mock())
        ctx = SimpleNamespace(adresse='test123@web.de', domaene='web.de',
                              password_filled=True, stop=None, timeout=45)
        with patch.object(unitedinternet.time, 'monotonic', side_effect=[0.0, 1.0, 46.0]):
            with self.assertRaisesRegex(RuntimeError, 'nicht bestätigt'):
                unitedinternet._warte_bis_fertig(page, ctx)
        page.wait_for_timeout.assert_called_once()


if __name__ == '__main__':
    unittest.main()
