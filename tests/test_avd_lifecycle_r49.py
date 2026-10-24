"""Offline account lifecycle checks: Android registration, warmup, then web login."""
import contextlib
import datetime
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from socialscraper import accountvault, repo, signup, streuung, warmup
from socialscraper.config import Settings
from socialscraper.net.errors import FinalError
from socialscraper.net.proxy import Proxy
from socialscraper.store import Store


class AvdLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.db = self.root / 'data' / 'fixture.sqlite'
        self.cfg = Settings(db_path=str(self.db), profiles_dir=str(self.root / 'profiles'))
        from profile_fixture import prepare
        prepare(self.cfg, 'fixture')
        self.stack.enter_context(patch.dict(os.environ, {'SS_REQUIRE_PROXY': '1'}))
        for name in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, name, side_effect=AssertionError('Network forbidden')))
        for name in ('connect', 'connect_ex', 'bind', 'sendto'):
            self.stack.enter_context(patch.object(socket.socket, name, side_effect=AssertionError('Socket forbidden')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process forbidden')))
        self.stack.enter_context(patch.object(os, 'system', side_effect=AssertionError('Shell forbidden')))
        self.stack.enter_context(patch.object(signup.time, 'sleep', side_effect=AssertionError('Real sleep forbidden')))
        self.stop = threading.Event()
        self.notify = MagicMock()
        self.vault = self.stack.enter_context(patch.object(accountvault, 'load', return_value={'postfaecher': []}))
        self.credentials = self.stack.enter_context(patch.object(accountvault, 'get', return_value={
            'email': 'fixture@example.test', 'passwort': 'offline-fixture', 'tiktok_username': 'provider.fixture'}))
        self.mailboxes = self.stack.enter_context(patch.object(accountvault, 'postfaecher', return_value=[]))
        self.mailbox_build = self.stack.enter_context(patch('socialscraper.mailbox.build'))
        self.save_credentials = self.stack.enter_context(patch.object(accountvault, 'put'))
        self.stack.enter_context(patch.object(signup, 'invalidate'))
        self.save_cookies = self.stack.enter_context(patch.object(signup.CookieJar, 'save'))
        self.stack.enter_context(patch.object(streuung, 'pruefe_neues_konto', return_value=[]))
        self.stack.enter_context(patch.object(streuung, 'wartezeit_bis_registrierung', return_value=0))
        self.stack.enter_context(patch.object(streuung, 'im_fenster', return_value=True))
        self.stack.enter_context(patch.object(warmup.rotation, 'has_login', return_value=True))
        self.browser = self.stack.enter_context(patch.object(signup, '_browser_lauf'))
        self.web_login = self.stack.enter_context(patch('socialscraper.signup.tiktok.web_anmelden', return_value=False))
        self.web_session = self.stack.enter_context(patch('socialscraper.warmup.sitzung.laufen', return_value=1))
        self.emulator = self.stack.enter_context(patch('socialscraper.android.geraet.mit_geraet'))
        self.adb = self.emulator.return_value.__enter__.return_value
        self.prepare = self.stack.enter_context(patch('socialscraper.android.geraet.vorbereiten'))
        self.app_login = self.stack.enter_context(patch('socialscraper.android.tiktok_app.anmelden', return_value=True))
        self.app_session = self.stack.enter_context(patch('socialscraper.android.warmup_app.sitzung', return_value=1))
        self.route = self.stack.enter_context(patch.object(streuung, 'proxy_url_fuer', return_value='http://proxy.example.test:18080'))
        self.im = MagicMock()
        self.im.proxy_for.return_value = None
        self.im.get.return_value = SimpleNamespace(name='fixture', country='de', mobile=True)
        self.backend = MagicMock()
        self.backend.session.side_effect = AssertionError('Native browser forbidden')
        self.now = datetime.datetime(2026, 10, 15, 8, tzinfo=datetime.timezone.utc)
        with Store(self.db):
            pass

    def account(self, *, phase='app', expired=True, label='Fixture'):
        with Store(self.db) as store:
            account = repo.add_account(store.db, 'tiktok', label)
        warmup.setzen(self.db, account['id'], phase=phase, app_avd='fixture-avd',
                      app_tage=3 if phase == 'app' else None, now=self.now)
        with Store(self.db) as store:
            if phase == 'app' and expired:
                store.db.execute('UPDATE accounts SET app_bis=? WHERE id=?',
                                 (repo.iso(self.now - datetime.timedelta(seconds=1)), account['id']))
                store.db.commit()
            return repo.get_account(store.db, account['id'])

    def stored(self, account):
        with Store(self.db) as store:
            return repo.get_account(store.db, account['id'])

    def create(self, module, platform='tiktok', **kwargs):
        with patch.object(signup, 'MODULE', {platform: module}):
            return signup.create(self.cfg, platform, name='fixture', email='fixture@example.test',
                                 password='offline-fixture', backend=self.backend, im=self.im,
                                 melden=self.notify, stop=self.stop.is_set, **kwargs)

    def run_warmup(self, **kwargs):
        with patch.object(repo, 'now', return_value=self.now):
            return warmup.run(self.db, cfg=self.cfg, now=self.now,
                              stop=self.stop.is_set, melden=self.notify, **kwargs)

    def assert_no_saved_signup(self):
        self.browser.assert_not_called()
        self.backend.session.assert_not_called()
        self.save_credentials.assert_not_called()
        self.save_cookies.assert_not_called()
        with Store(self.db) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0], 0)

    def test_app_failures_never_fall_back_or_save_an_account(self):
        for outcome in (RuntimeError('App unavailable'), FinalError('Human step pending'),
                        (False, 'fixture-avd'), (True, None), (True, '')):
            with self.subTest(outcome=type(outcome).__name__ + str(outcome)):
                self.browser.reset_mock()
                app = MagicMock()
                if isinstance(outcome, Exception):
                    app.side_effect = outcome
                else:
                    app.return_value = outcome
                with self.assertRaises(FinalError):
                    self.create(SimpleNamespace(WEG='app', registrieren_app=app))
                self.assert_no_saved_signup()

    def test_failed_app_cannot_report_success_using_incidental_browser_cookies(self):
        def failed(cfg, ctx, **kwargs):
            ctx.cookies = [{'name': 'sessionid', 'value': 'fixture', 'domain': '.tiktok.com'}]
            return False, 'fixture-avd'
        with self.assertRaises(FinalError):
            self.create(SimpleNamespace(WEG='app', registrieren_app=failed))
        self.assert_no_saved_signup()

    def test_tiktok_without_android_entry_cannot_use_browser_signup(self):
        with self.assertRaises(FinalError):
            self.create(SimpleNamespace(WEG='web'))
        self.assert_no_saved_signup()

    def test_confirmed_app_signup_persists_avd_and_app_phase(self):
        def completed(cfg, ctx, **kwargs):
            ctx.tiktok_username = 'provider.fixture'
            return True, 'fixture-avd'
        result = self.create(SimpleNamespace(WEG='app', registrieren_app=completed))
        with Store(self.db) as store:
            saved = repo.get_account(store.db, result['konto_id'])
        self.assertEqual((saved['phase'], saved['app_avd']), ('app', 'fixture-avd'))
        self.assertGreaterEqual(saved['app_tage'], 3)
        self.save_credentials.assert_called_once()
        self.browser.assert_not_called()
        self.save_cookies.assert_not_called()

    def test_stop_after_app_result_prevents_account_persistence(self):
        def completed(cfg, ctx, **kwargs):
            self.stop.set()
            return True, 'fixture-avd'
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.create(SimpleNamespace(WEG='app', registrieren_app=completed))
        self.assert_no_saved_signup()

    def test_stop_with_unconfirmed_app_result_is_reported_as_cancelled(self):
        def incomplete(cfg, ctx, **kwargs):
            self.stop.set()
            return False, 'fixture-avd'
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.create(SimpleNamespace(WEG='app', registrieren_app=incomplete))
        self.assert_no_saved_signup()

    def test_other_providers_keep_their_confirmed_browser_registration(self):
        def registered(module, ctx, *args):
            ctx.cookies = [{'name': 'reddit_session', 'value': 'fixture', 'domain': '.reddit.com'}]
        self.browser.side_effect = registered
        result = self.create(SimpleNamespace(WEG='web'), platform='reddit', warmup='aus')
        self.assertEqual(result['platform'], 'reddit')
        self.browser.assert_called_once()
        self.save_cookies.assert_called_once()
        self.save_credentials.assert_called_once()

    def test_default_app_duration_is_at_least_three_days(self):
        rng = SimpleNamespace(choice=min, uniform=lambda low, high: low)
        with Store(self.db) as store:
            account = repo.add_account(store.db, 'tiktok', 'Duration')
        result = warmup.setzen(self.db, account['id'], phase='app', app_avd='fixture-avd',
                               now=self.now, rng=rng)
        self.assertEqual(result['app_tage'], 3)
        self.assertGreaterEqual(repo.parse(result['app_bis']) - self.now, datetime.timedelta(days=3))

    def test_explicit_whole_day_duration_is_preserved(self):
        account = self.account()
        rng = SimpleNamespace(uniform=lambda low, high: low)
        result = warmup.setzen(self.db, account['id'], phase='app', app_avd='fixture-avd',
                               app_tage=5, now=self.now, rng=rng)
        self.assertEqual(result['app_tage'], 5)
        self.assertEqual(repo.parse(result['app_bis']), self.now + datetime.timedelta(days=5))

    def test_invalid_duration_is_rejected_without_changing_plan(self):
        account = self.account()
        before = self.stored(account)
        for key in ('tage', 'app_tage'):
            for invalid in (True, -1, 1.5, float('nan'), float('inf'), 'broken'):
                with self.subTest(key=key, value=repr(invalid)), self.assertRaises(ValueError):
                    warmup.setzen(self.db, account['id'], phase='app', app_avd='fixture-avd',
                                  **{key: invalid})
                self.assertEqual(self.stored(account), before)

    def test_expired_or_missing_date_does_not_turn_app_account_into_web(self):
        account = self.account()
        self.assertTrue(warmup._app_phase(account, self.now))
        account['app_bis'] = None
        self.assertTrue(warmup._app_phase(account, self.now))

    def test_expired_app_account_stays_due_without_browser_cookies(self):
        account = self.account()
        with patch.object(warmup.rotation, 'has_login', return_value=False):
            self.assertIn(account['id'], [row['id'] for row in warmup.faellig(self.db, now=self.now)])

    def test_failed_handoff_continues_in_same_app_instead_of_browser(self):
        account = self.account()
        result = self.run_warmup(konto_id=account['id'])
        self.assertEqual(result['sitzungen'], 1)
        self.assertEqual(self.stored(account)['phase'], 'app')
        self.emulator.assert_called_once_with('fixture-avd', proxy='http://proxy.example.test:18080',
                                              stop_requested=self.stop.is_set)
        self.app_session.assert_called_once()
        self.web_session.assert_not_called()
        self.adb.daten_loeschen.assert_not_called()

    def test_confirmed_handoff_switches_to_web_and_does_not_start_emulator(self):
        account = self.account()
        self.web_login.return_value = True
        result = self.run_warmup(konto_id=account['id'])
        self.assertEqual(result['sitzungen'], 1)
        self.assertEqual(self.stored(account)['phase'], 'web')
        self.web_session.assert_called_once()
        self.emulator.assert_not_called()

    def test_selected_account_does_not_handoff_other_due_accounts(self):
        first = self.account(label='First')
        other = self.account(label='Other')
        self.web_login.return_value = True
        self.run_warmup(konto_id=first['id'])
        self.web_login.assert_called_once()
        self.assertEqual(self.web_login.call_args.args[1]['id'], first['id'])
        self.assertEqual(self.stored(other)['phase'], 'app')

    def test_stop_during_handoff_does_not_commit_phase_or_start_session(self):
        account = self.account()
        def login(*args, **kwargs):
            self.stop.set()
            return True
        self.web_login.side_effect = login
        result = self.run_warmup(konto_id=account['id'])
        self.assertTrue(result['abgebrochen'])
        self.assertEqual(self.stored(account)['phase'], 'app')
        self.web_session.assert_not_called()
        self.emulator.assert_not_called()

    def test_stop_during_app_preparation_prevents_login_and_warmup(self):
        account = self.account(expired=False)
        self.prepare.side_effect = lambda *args, **kwargs: self.stop.set()
        result = self.run_warmup(konto_id=account['id'])
        self.assertTrue(result['abgebrochen'])
        self.app_login.assert_not_called()
        self.app_session.assert_not_called()
        self.emulator.return_value.__exit__.assert_called_once()

    def test_stop_during_app_login_prevents_warmup(self):
        account = self.account(expired=False)
        def login(*args):
            self.stop.set()
            return True
        self.app_login.side_effect = login
        result = self.run_warmup(konto_id=account['id'])
        self.assertTrue(result['abgebrochen'])
        self.app_session.assert_not_called()
        self.assertEqual(self.stored(account)['warmup_sitzungen'], 0)

    def test_missing_account_avd_cannot_start_a_default_emulator(self):
        account = self.account(expired=False)
        with Store(self.db) as store:
            store.db.execute('UPDATE accounts SET app_avd=NULL WHERE id=?', (account['id'],))
            store.db.commit()
        result = self.run_warmup(konto_id=account['id'])
        self.assertEqual(result['fehler'], 1)
        self.assertEqual(result['sitzungen'], 0)
        self.emulator.assert_not_called()
        self.app_login.assert_not_called()
        self.app_session.assert_not_called()

    def test_dry_run_does_not_handoff_or_start_app(self):
        account = self.account()
        result = self.run_warmup(konto_id=account['id'], trocken=True)
        self.assertEqual(result['konten'], 1)
        self.assertEqual(self.stored(account)['phase'], 'app')
        self.web_login.assert_not_called()
        self.emulator.assert_not_called()
        self.web_session.assert_not_called()

    def test_missing_or_malformed_proxy_stops_before_mailbox_or_emulator(self):
        self.credentials.return_value['email_passwort'] = 'offline-mail-fixture'
        for index, route in enumerate((None, '', 'ftp://proxy.example.test:80',
                                      'http://fixture-user:fixture-secret@proxy.example.test:bad-port')):
            with self.subTest(route_type=type(route).__name__):
                account = self.account(expired=False, label=f'Invalid route {index}')
                self.route.return_value = route
                self.notify.reset_mock()
                result = self.run_warmup(konto_id=account['id'])
                self.assertEqual(result['fehler'], 1)
                self.assertEqual(result['sitzungen'], 0)
                self.mailbox_build.assert_not_called()
                self.emulator.assert_not_called()
                self.assertNotIn('fixture-secret', str(self.notify.mock_calls))
                self.assertNotIn('bad-port', str(self.notify.mock_calls))

    def test_both_mailbox_sources_receive_the_selected_account_proxy(self):
        route = 'http://fixture-user:fixture-secret@proxy.example.test:18080'
        self.route.return_value = route
        for source in ('account', 'pool'):
            with self.subTest(source=source):
                account = self.account(expired=False, label=source)
                self.mailbox_build.reset_mock()
                self.emulator.reset_mock()
                self.credentials.return_value = {'email': 'fixture@example.test', 'passwort': 'offline-fixture',
                                                 'tiktok_username': 'provider.fixture'}
                mailbox_settings = {'benutzer': 'fixture@example.test', 'passwort': 'offline-mail-fixture'}
                if source == 'account':
                    self.credentials.return_value['email_passwort'] = 'offline-mail-fixture'
                    self.mailboxes.return_value = []
                else:
                    self.mailboxes.return_value = [{'adresse': 'fixture@example.test', 'passwort': 'offline-mail-fixture'}]
                with patch.object(accountvault, 'als_postfach', return_value=mailbox_settings):
                    result = self.run_warmup(konto_id=account['id'])
                self.assertEqual(result['sitzungen'], 1)
                self.route.assert_called_with(self.db, account['identity'])
                self.mailbox_build.assert_called_once_with(mailbox_settings, proxy=Proxy.parse(route))
                self.emulator.assert_called_once_with('fixture-avd', proxy=route, stop_requested=self.stop.is_set)

    def test_stop_during_proxy_selection_prevents_mailbox_and_emulator(self):
        account = self.account(expired=False)
        self.credentials.return_value['email_passwort'] = 'offline-mail-fixture'
        def selected(*args):
            self.stop.set()
            return 'http://proxy.example.test:18080'
        self.route.side_effect = selected
        result = self.run_warmup(konto_id=account['id'])
        self.assertTrue(result['abgebrochen'])
        self.mailbox_build.assert_not_called()
        self.emulator.assert_not_called()

    def test_stop_during_mailbox_creation_prevents_emulator(self):
        account = self.account(expired=False)
        self.credentials.return_value['email_passwort'] = 'offline-mail-fixture'
        def mailbox(*args, **kwargs):
            self.stop.set()
            return MagicMock()
        self.mailbox_build.side_effect = mailbox
        result = self.run_warmup(konto_id=account['id'])
        self.assertTrue(result['abgebrochen'])
        self.emulator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
