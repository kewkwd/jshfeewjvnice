import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET

from socialscraper import totp, mailbox, accountvault
from socialscraper.android.adb import Adb, AdbError
from socialscraper.net.cookies import CookieJar


class UpdateTests(unittest.TestCase):
    def test_rfc6238_vectors(self):
        secrets = {'sha1': b'12345678901234567890',
                   'sha256': b'12345678901234567890123456789012',
                   'sha512': b'1234567890123456789012345678901234567890123456789012345678901234'}
        vectors = [(59, ('94287082', '46119246', '90693936')),
                   (1111111109, ('07081804', '68084774', '25091201')),
                   (20000000000, ('65353130', '77737706', '47863826'))]
        for at, codes in vectors:
            for algorithm, expected in zip(secrets, codes):
                secret = base64.b32encode(secrets[algorithm]).decode()
                self.assertEqual(totp.code(secret, at=at, digits=8, algo=algorithm), expected)

    def test_totp_uri_and_freshness(self):
        secret = 'JBSWY3DPEHPK3PXP'
        self.assertEqual(totp.normalize_secret('otpauth://totp/Test?secret='+secret), secret)
        fresh, remaining = totp.code_with_freshness(secret, at=59)
        self.assertEqual(fresh, totp.code(secret, at=60))
        self.assertEqual(remaining, 1)
        self.assertTrue(totp.verify(secret, totp.code(secret, at=0), at=0))

    def test_bad_totp_parameters(self):
        for period in (0, -1, True, 1.5):
            with self.assertRaises(totp.TotpError):
                totp.code('JBSWY3DPEHPK3PXP', period=period)

    def test_mailbox_missing_settings_and_imap_inference(self):
        with self.assertRaises(mailbox.MailboxError):
            mailbox.build(None)
        box = mailbox.build({'benutzer': 'test@gmx.net', 'passwort': 'test-only'})
        self.assertEqual(box.host, 'imap.gmx.net')
        self.assertEqual(box.address(), 'test@gmx.net')

    def test_mail_code_uses_sender_and_timestamp(self):
        box = mailbox.Mailbox()
        messages = [
            {'from': 'other@example.test', 'stamp': 20, 'subject': 'Code 111111', 'text': ''},
            {'from': 'service@tiktok.com', 'stamp': 5, 'subject': 'Code 222222', 'text': ''},
            {'from': 'service@tiktok.com', 'stamp': 20, 'subject': 'TikTok Code 333333', 'text': ''},
        ]
        with patch.object(box, 'fetch', return_value=messages):
            code, _ = box.wait_for_code(platform='tiktok', newer_than=10, timeout=0)
        self.assertEqual(code, '333333')

    def test_rejected_mail_login_is_reported_without_waiting_for_a_code(self):
        box = mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only')
        connection = MagicMock()
        connection.login.side_effect = mailbox.imaplib.IMAP4.error('AUTHENTICATIONFAILED')
        with patch.dict('os.environ', {'SS_REQUIRE_PROXY': '0'}), \
                patch.object(mailbox.imaplib, 'IMAP4_SSL', return_value=connection), \
                patch('socialscraper.operation.wait', side_effect=AssertionError('Rejected login was retried')) as wait:
            with self.assertRaisesRegex(mailbox.MailboxError, 'Anmeldung'):
                box.wait_for_code(platform='tiktok', timeout=240)
        connection.login.assert_called_once()
        connection.logout.assert_called_once()
        connection.select.assert_not_called()
        wait.assert_not_called()

    def test_temporary_mail_failure_can_recover_on_next_poll(self):
        box = mailbox.Mailbox()
        message = {'from': 'service@tiktok.com', 'stamp': 20, 'subject': 'Code 333333', 'text': ''}
        with patch.object(box, 'fetch', side_effect=[mailbox.MailboxError('Connection interrupted'), [message]]) as fetch, \
                patch('socialscraper.operation.wait', return_value=True) as wait:
            code, _ = box.wait_for_code(platform='tiktok', timeout=240)
        self.assertEqual(code, '333333')
        self.assertEqual(fetch.call_count, 2)
        wait.assert_called_once()

    def test_disconnected_imap_login_is_still_retryable(self):
        box = mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only')
        connection = MagicMock()
        connection.login.side_effect = mailbox.imaplib.IMAP4.abort('Connection lost')
        with patch.dict('os.environ', {'SS_REQUIRE_PROXY': '0'}), \
                patch.object(mailbox.imaplib, 'IMAP4_SSL', return_value=connection):
            with self.assertRaises(mailbox.MailboxError) as raised:
                box.fetch()
        self.assertNotIsInstance(raised.exception, mailbox.MailboxLoginError)
        connection.logout.assert_called_once()

    def test_vault_roundtrip_redaction_and_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'data' / 'test.sqlite'
            accountvault.put(db, 'test', passwort='secret-only', totp_secret='SECRETTEST')
            accountvault.add_postfach(db, {'adresse': 'test@example.test', 'passwort': 'mail-secret'})
            accountvault.belege(db, 'test@example.test', 'test')
            with self.assertRaises(ValueError):
                accountvault.belege(db, 'test@example.test', 'other')
            masked = json.dumps(accountvault.redacted(accountvault.load(db)))
            for secret in ('secret-only', 'SECRETTEST', 'mail-secret'):
                self.assertNotIn(secret, masked)
            self.assertFalse(db.exists())
            self.assertEqual(accountvault.get(db, 'test')['passwort'], 'secret-only')
            accountvault.freigeben(db, 'test')
            self.assertIsNotNone(accountvault.nimm_freies(db))

    def test_mail_sender_and_link_domain_validation(self):
        self.assertFalse(mailbox._sender_matches('TikTok <bad@evil.test>', 'tiktok'))
        self.assertFalse(mailbox._sender_matches('bad@tiktok.com.evil.test', 'tiktok'))
        box = mailbox.Mailbox()
        mail = {'from': 'service@trustpilot.com', 'subject': '',
                'text': 'https://evil.test/path/trustpilot.com/verify', 'stamp': 20}
        with patch.object(box, 'fetch', return_value=[mail]), self.assertRaises(mailbox.MailboxError):
            box.wait_for_link(platform='trustpilot', timeout=0)

    def test_corrupt_vault_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'test.sqlite'
            file = accountvault.path_for(db)
            file.write_text('broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                accountvault.put(db, 'test', passwort='secret-only')
            self.assertEqual(file.read_text(), 'broken')

    def test_google_youtube_trustpilot_cookies(self):
        for platform, domain, name in [('google', '.google.com', 'SID'),
                                       ('youtube', '.google.com', 'SID'),
                                       ('trustpilot', '.trustpilot.com', '__Host-jwt')]:
            cookies = [{'name': name, 'value': 'test', 'domain': domain}]
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                CookieJar.save('test', platform, cookies, base_dir=tmp)
                self.assertTrue(CookieJar.is_authenticated('test', platform, base_dir=tmp))
        self.assertFalse(CookieJar.authenticated([
            {'name': 'tracking-preferences', 'value': 'yes', 'domain': '.trustpilot.com'}], 'trustpilot'))
        self.assertFalse(CookieJar.authenticated([
            {'name': 'SID', 'value': 'test', 'domain': '.google.com.evil.test'}], 'google'))

    def test_android_description_and_text_quoting(self):
        adb = Adb('test')
        tree = ET.fromstring('<hierarchy><node text="" content-desc="Gefällt mir" bounds="[10,20][30,40]"/></hierarchy>')
        with patch.object(adb, 'baum', return_value=tree):
            self.assertEqual(adb.finde(teil='gefällt'), (20, 30))
        with patch.object(adb, 'sh') as shell:
            adb.tippen('a b&%s')
            self.assertEqual([c.args[-1] for c in shell.call_args_list], ['a', '%s', 'b', "'&'", '%', 's'])
        with self.assertRaises(AdbError):
            adb.tippen('ä')
