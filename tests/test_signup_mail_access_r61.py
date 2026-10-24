"""Mailbox transport must succeed before a dependent provider registration."""
import contextlib
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
from socialscraper.net.proxy import Proxy
from socialscraper.store import Store


class ReachedProvider(FinalError):
    pass


class SignupMailAccess(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'SS_REQUIRE_PROXY':'0'}))
        for method in ('create_connection','getaddrinfo'):
            self.stack.enter_context(patch.object(socket,method,side_effect=AssertionError('External network forbidden')))
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('External network forbidden')))
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.cfg = Settings(db_path=str(root/'data/test.db'), profiles_dir=str(root/'profiles'))
        from profile_fixture import prepare
        prepare(self.cfg, 'mail-access-fixture')
        with Store(self.cfg.db_path):pass
        accountvault.add_postfach(self.cfg.db_path, {'adresse':'fixture@web.de','passwort':'FIXTURE_ONLY'})
        self.before = accountvault.path_for(self.cfg.db_path).read_bytes()
        self.proxy = Proxy.parse('socks5://127.0.0.1:9')
        self.manager = MagicMock(); self.manager.proxy_for.return_value = self.proxy
        self.box = MagicMock(); self.box.check_access.return_value = 0
        self.build = self.stack.enter_context(patch.object(mailbox,'build',return_value=self.box))
        self.stack.enter_context(patch.object(signup,'precheck_local_proxy'))
        self.stack.enter_context(patch.object(streuung,'pruefe_neues_konto',return_value=[]))
        self.stack.enter_context(patch.object(streuung,'wartezeit_bis_registrierung',return_value=0))
        self.app = MagicMock(side_effect=ReachedProvider('provider reached'))
        self.browser = self.stack.enter_context(patch.object(signup,'_browser_lauf',side_effect=ReachedProvider('provider reached')))
        self.stack.enter_context(patch.object(signup,'MODULE',{
            'tiktok':SimpleNamespace(registrieren_app=self.app), 'reddit':SimpleNamespace()}))
        self.log = MagicMock()

    def create(self, platform='tiktok', **kwargs):
        return signup.create(self.cfg,platform,name='mail-access-fixture',backend=MagicMock(),
                             im=self.manager,warmup='aus',melden=self.log,**kwargs)

    def unchanged(self):
        self.assertEqual(accountvault.path_for(self.cfg.db_path).read_bytes(),self.before)
        with Store(self.cfg.db_path) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],0)

    def test_failed_mail_read_stops_android_and_browser_before_provider(self):
        for platform in ('tiktok','reddit'):
            with self.subTest(platform=platform):
                self.box.check_access.side_effect = mailbox.MailboxLoginError('FIXTURE_SECRET_REJECTED')
                with self.assertRaisesRegex(FinalError,'Mailabruf') as error:self.create(platform)
                self.assertNotIn('FIXTURE_SECRET',str(error.exception))
                self.assertIs(self.build.call_args.kwargs['proxy'],self.proxy)
                self.app.assert_not_called(); self.browser.assert_not_called(); self.unchanged()

    def test_empty_accessible_inbox_is_checked_before_provider_with_same_proxy(self):
        order=[]
        self.box.check_access.side_effect=lambda:order.append('mail') or 0
        def provider(*args,**kwargs):
            order.append('provider'); raise ReachedProvider('provider reached')
        self.app.side_effect=provider
        with self.assertRaises(ReachedProvider):self.create()
        self.assertEqual(order,['mail','provider'])
        self.assertIs(self.build.call_args.kwargs['proxy'],self.proxy)
        self.assertEqual(self.app.call_args.kwargs['proxy'],self.proxy.url)
        self.box.check_access.assert_called_once(); self.unchanged()

    def test_stop_during_mail_read_prevents_provider_and_success_notice(self):
        stopped=[False]
        def read():stopped[0]=True; return 0
        self.box.check_access.side_effect=read
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):self.create(stop=lambda:stopped[0])
        self.app.assert_not_called(); self.browser.assert_not_called(); self.unchanged()
        self.assertFalse(any('Posteingang erreichbar' in str(c.args) for c in self.log.call_args_list))

    def test_network_error_is_not_retried_or_reported_as_success(self):
        self.box.check_access.side_effect=OSError('FIXTURE_PRIVATE_ENDPOINT')
        with self.assertRaisesRegex(FinalError,'Mailabruf') as error:self.create()
        self.assertNotIn('FIXTURE_PRIVATE',str(error.exception))
        self.box.check_access.assert_called_once(); self.app.assert_not_called(); self.unchanged()


class ImapAccess(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ,{'SS_REQUIRE_PROXY':'0'}))
        self.connection=MagicMock()
        self.connection.select.return_value=('OK',[])
        self.connection.search.return_value=('OK',[b'1'])
        self.connection.fetch.return_value=('OK',[(b'1 (RFC822 {7}',b'Subject: fixture\r\n\r\nbody'),b')'])
        self.stack.enter_context(patch.object(mailbox.imaplib,'IMAP4_SSL',return_value=self.connection))
        self.box=mailbox.ImapBox('imap.example.invalid','fixture@example.invalid','FIXTURE_ONLY')

    def test_message_read_and_empty_inbox_are_distinguished(self):
        self.assertEqual(self.box.check_access(),1)
        self.connection.select.assert_called_with('INBOX',readonly=True)
        self.connection.logout.assert_called_once()
        self.connection.reset_mock()
        self.connection.search.return_value=('OK',[b''])
        self.assertEqual(self.box.check_access(),0)
        self.connection.fetch.assert_not_called(); self.connection.logout.assert_called_once()

    def test_failed_or_unreadable_fetch_cannot_validate_mail_access(self):
        for response in (('NO',[b'failure']),('OK',[]),('OK',[b')'])):
            with self.subTest(response=response):
                self.connection.fetch.return_value=response
                with self.assertRaises(mailbox.MailboxError):self.box.check_access()
        self.assertEqual(self.connection.logout.call_count,3)

    def test_ordinary_fetch_preserves_best_effort_behavior(self):
        self.connection.fetch.return_value=('NO',[b'failure'])
        self.assertEqual(self.box.fetch(limit=1),[])

    def test_generic_mail_access_rejects_malformed_collection(self):
        box=mailbox.Mailbox(); box.fetch=MagicMock()
        for data in (None,{},['invalid']):
            box.fetch.return_value=data
            with self.assertRaises(mailbox.MailboxError):box.check_access()
        box.fetch.return_value=[]
        self.assertEqual(box.check_access(),0)
