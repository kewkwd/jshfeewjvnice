"""Round 30 regressions: delivery timestamps and registration mailbox identity."""
import contextlib
import datetime
from email.message import EmailMessage
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import accountvault, mailbox, signup, streuung
from socialscraper.config import Settings
from socialscraper.net.errors import FinalError
from socialscraper.store import Store


class _ReachedBrowser(RuntimeError):
    pass


class MailboxRegistrationReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'SS_REQUIRE_PROXY': '0'}))
        for target in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, target, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.db = self.root / 'data/test.sqlite'
        self.cfg = Settings(db_path=str(self.db), profiles_dir=str(self.root / 'data/profiles'))
        from profile_fixture import prepare
        prepare(self.cfg, 'mail-mismatch', 'mail-unknown', 'mail-matching')
        with Store(self.db):
            pass
        self.delivered = datetime.datetime(2026, 10, 15, 10, 30, tzinfo=datetime.timezone.utc).timestamp()
        self.message_date = 'Thu, 15 Oct 2026 10:30:00 +0000'
        self.internal_date = b'15-Oct-2026 12:30:00 +0200'

    @contextlib.contextmanager
    def imap_message(self, *, date=None, internal=None, trailer=None, body='Google code 123456'):
        message = EmailMessage()
        message['From'] = 'noreply@google.com'
        message['Subject'] = 'Google confirmation'
        if date is not None:
            message['Date'] = date
        message.set_content(body)
        payload = message.as_bytes()
        metadata = b'1 ('
        if internal is not None:
            metadata += b'INTERNALDATE "' + internal + b'" '
        metadata += b'RFC822 {' + str(len(payload)).encode() + b'}'
        connection = MagicMock()
        connection.select.return_value = ('OK', [])
        connection.search.return_value = ('OK', [b'1'])
        connection.fetch.return_value = ('OK', [(metadata, payload), trailer or b')'])
        box = mailbox.ImapBox('imap.example.test', 'test@example.test', 'test-only')
        with patch.object(mailbox.imaplib, 'IMAP4_SSL', return_value=connection), \
                patch('socialscraper.operation.wait', side_effect=AssertionError('Unexpected wait')):
            yield box, connection

    def test_internal_delivery_date_overrides_forged_message_date(self):
        cases = [('new_delivery', self.internal_date, 'Sat, 01 Jan 2000 00:00:00 +0000', True),
                 ('old_delivery', b'01-Jan-2000 00:00:00 +0000', self.message_date, False)]
        for name, internal, date, accepted in cases:
            with self.subTest(case=name), self.imap_message(date=date, internal=internal) as (box, connection):
                if accepted:
                    value, message = box.wait_for_code(platform='google', timeout=0, newer_than=self.delivered - 60)
                    self.assertEqual(value, '123456')
                    self.assertEqual(message['stamp'], self.delivered)
                else:
                    with self.assertRaises(mailbox.MailboxError):
                        box.wait_for_code(platform='google', timeout=0, newer_than=self.delivered - 60)
                self.assertIn('INTERNALDATE', connection.fetch.call_args.args[1])

    def test_missing_message_date_uses_internal_delivery_date(self):
        with self.imap_message(internal=self.internal_date) as (box, connection):
            value, message = box.wait_for_code(platform='google', timeout=0, newer_than=self.delivered - 60)
        self.assertEqual(value, '123456')
        self.assertEqual(message['stamp'], self.delivered)
        self.assertIn('INTERNALDATE', connection.fetch.call_args.args[1])

    def test_message_date_remains_fallback_without_usable_internal_date(self):
        for internal in (None, b'invalid', b'15-Xyz-2026 12:30:00 +0200'):
            with self.subTest(internal=internal), self.imap_message(date=self.message_date, internal=internal) as (box, _):
                value, message = box.wait_for_code(platform='google', timeout=0, newer_than=self.delivered - 60)
                self.assertEqual(value, '123456')
                self.assertEqual(message['stamp'], self.delivered)

    def test_internal_date_after_rfc822_literal_is_used(self):
        trailer = b' INTERNALDATE "' + self.internal_date + b'")'
        with self.imap_message(trailer=trailer) as (box, connection):
            value, message = box.wait_for_code(platform='google', timeout=0, newer_than=self.delivered - 60)
        self.assertEqual(value, '123456')
        self.assertEqual(message['stamp'], self.delivered)
        self.assertIn('INTERNALDATE', connection.fetch.call_args.args[1])

    def test_message_body_cannot_forge_internal_date_metadata(self):
        body = 'Google code 123456\nINTERNALDATE "01-Jan-2000 00:00:00 +0000"'
        with self.imap_message(date=self.message_date, body=body) as (box, _):
            value, message = box.wait_for_code(platform='google', timeout=0, newer_than=self.delivered - 60)
        self.assertEqual(value, '123456')
        self.assertEqual(message['stamp'], self.delivered)

    def test_explicit_email_mismatch_is_rejected_before_mailbox_identity_or_browser(self):
        configurations = [
            {'art': 'imap', 'benutzer': 'other@web.de', 'passwort': 'test-only'},
            {'art': 'wegwerf', 'adresse': 'other@example.test', 'passwort': 'test-only'},
        ]
        for config in configurations:
            with self.subTest(kind=config['art']):
                accountvault.set_postfach(self.db, config)
                manager = MagicMock()
                manager.proxy_for.return_value = None
                with patch.object(signup, 'MODULE', {'reddit': SimpleNamespace()}), \
                        patch.object(mailbox, 'build', side_effect=AssertionError('Mailbox must not be constructed')) as build, \
                        patch.object(signup, '_browser_lauf') as browser:
                    with self.assertRaises(FinalError):
                        signup.create(self.cfg, 'reddit', email='target@web.de', name='mail-mismatch',
                                      im=manager, backend=MagicMock(), warmup='aus')
                build.assert_not_called()
                manager.get.assert_not_called()
                browser.assert_not_called()

    def test_unknown_disposable_address_is_not_created_for_an_explicit_email(self):
        for config in ({'art': 'wegwerf'}, {'art': 'wegwerf', 'adresse': 'target@web.de'}):
            with self.subTest(config=config):
                accountvault.set_postfach(self.db, config)
                manager = MagicMock()
                manager.proxy_for.return_value = None
                with patch.object(signup, 'MODULE', {'reddit': SimpleNamespace()}), \
                        patch.object(mailbox, 'build', side_effect=AssertionError('Mailbox must not be constructed')) as build, \
                        patch.object(mailbox.TempMailBox, 'create') as create, \
                        patch.object(signup, '_browser_lauf') as browser:
                    with self.assertRaises(FinalError):
                        signup.create(self.cfg, 'reddit', email='target@web.de', name='mail-unknown',
                                      im=manager, backend=MagicMock(), warmup='aus')
                build.assert_not_called()
                create.assert_not_called()
                manager.get.assert_not_called()
                browser.assert_not_called()

    def test_matching_legacy_mailbox_accepts_case_and_whitespace_differences(self):
        configurations = [
            {'art': 'imap', 'benutzer': 'target@web.de', 'passwort': 'test-only'},
            {'art': 'imap', 'host': 'imap.example.test', 'benutzer': 'custom-login',
             'adresse': 'target@web.de', 'passwort': 'test-only'},
            {'art': 'imap', 'host': 'imap.example.test', 'benutzer': 'custom-login', 'passwort': 'test-only'},
            {'art': 'imap', 'host': 'imap.example.test', 'benutzer': 'login@example.test',
             'adresse': 'target@web.de', 'passwort': 'test-only'},
        ]
        for config in configurations:
            with self.subTest(config=config):
                accountvault.set_postfach(self.db, config)
                manager = MagicMock()
                manager.proxy_for.return_value = None

                def inspect_context(_module, ctx, *_args):
                    self.assertEqual(ctx.email.casefold(), 'target@web.de')
                    self.assertIsInstance(ctx.box, mailbox.ImapBox)
                    self.assertEqual(ctx.box.address(), config['benutzer'])
                    raise _ReachedBrowser()

                with patch.object(signup, 'MODULE', {'reddit': SimpleNamespace()}), \
                        patch.object(mailbox.ImapBox, 'check_access', return_value=0) as access, \
                        patch.object(streuung, 'wartezeit_bis_registrierung', return_value=0), \
                        patch.object(signup, '_browser_lauf', side_effect=inspect_context):
                    with self.assertRaises(_ReachedBrowser):
                        signup.create(self.cfg, 'reddit', email='  TARGET@WEB.DE  ', name='mail-matching',
                                      im=manager, backend=MagicMock(), warmup='aus')
                manager.get.assert_called_once()
                access.assert_called_once()


if __name__ == '__main__':
    unittest.main()
