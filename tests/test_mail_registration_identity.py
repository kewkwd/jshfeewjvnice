import unittest
from unittest.mock import MagicMock
from socialscraper.mailanbieter import _confirmed_address, MailCtx
from socialscraper.net.errors import FinalError, explain, is_hopeless, wrap

class ConfirmedMailbox(unittest.TestCase):
    def context(self, **kwargs):
        return MailCtx('gmail.com','Test','Person','expected@gmail.com','FAKE',
                       'assisted','de',False,kwargs.pop('timeout',0),MagicMock(),**kwargs)

    def test_unconfirmed_mailbox_and_cancellation_keep_intended_error(self):
        with self.assertRaisesRegex(FinalError, 'nicht bestätigt'):
            self.context().bestaetigen('google')
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.context(timeout=10,stop=lambda:True).bestaetigen('google')

    def test_final_error_is_not_retranslated_or_retried(self):
        error=FinalError('CAPTCHA nicht bestätigt; kein Postfach gespeichert.')
        self.assertEqual(explain(error),str(error))
        self.assertTrue(is_hopeless(error))
        self.assertIs(wrap(error),error)

    def page(self,url,label):
        page=MagicMock();page.url=url
        page.locator.return_value.evaluate_all.return_value=[label]
        return page
    def test_microsoft_account_does_not_prove_outlook_mailbox(self):
        page=self.page('https://account.microsoft.com/','expected@outlook.com')
        self.assertFalse(_confirmed_address(page,'outlook','expected@outlook.com'))
    def test_wrong_or_similar_address_is_not_saved(self):
        for label in ('other@outlook.com','xexpected@outlook.com','expected@outlook.com.evil.test'):
            page=self.page('https://outlook.live.com/mail/',label)
            self.assertFalse(_confirmed_address(page,'outlook','expected@outlook.com'))
    def test_actual_mailbox_address_is_confirmed(self):
        page=self.page('https://outlook.live.com/mail/','Konto: expected@outlook.com')
        self.assertTrue(_confirmed_address(page,'outlook','expected@outlook.com'))
    def test_unencrypted_pages_cannot_confirm_mailboxes(self):
        for platform,host,path,address in (
            ('google','myaccount.google.com','/','expected@gmail.com'),
            ('outlook','outlook.live.com','/mail/','expected@outlook.com'),
        ):
            with self.subTest(platform=platform):
                page=self.page('http://'+host+path,address)
                self.assertFalse(_confirmed_address(page,platform,address))
                page.locator.assert_not_called()
    def test_google_signup_form_is_not_a_mailbox_confirmation(self):
        page=self.page('https://accounts.google.com/signup','expected@gmail.com')
        self.assertFalse(_confirmed_address(page,'google','expected@gmail.com'))
        page.url='https://myaccount.google.com/'
        self.assertTrue(_confirmed_address(page,'google','expected@gmail.com'))
