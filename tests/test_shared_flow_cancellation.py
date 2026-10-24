"""Provider-independent cancellation boundaries for signup and mailbox flows."""
import contextlib
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from socialscraper import mailanbieter, signup
from socialscraper.net.cookies import CookieJar
from socialscraper.net.errors import FinalError


class SharedFlowCancellationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))
        self.stack.enter_context(patch.object(signup.time, 'sleep', side_effect=AssertionError('Real sleep blocked')))
        self.cancelled = threading.Event()
        self.backend = MagicMock()
        self.handle = object()
        self.page = self.backend.page.return_value
        self.notify = MagicMock()
        self.box = MagicMock()
        self.box.wait_for_code.return_value = ('123456', {})
        self.box.wait_for_link.return_value = ('https://example.test/confirm', {})
        self.signup = signup.Ctx(platform='tiktok', email='fixture@example.test', password='offline-password',
                                 username='offline-fixture', mode='assisted', country='de', mobile=False,
                                 timeout=1, backend=self.backend, handle=self.handle, box=self.box,
                                 melden=self.notify, stop=self.cancelled.is_set)
        self.mail = mailanbieter.MailCtx(anbieter='gmail.com', vorname='Offline', nachname='Fixture',
                                         adresse='fixture@example.test', passwort='offline-password',
                                         mode='assisted', land='de', mobile=False, timeout=1,
                                         backend=self.backend, handle=self.handle, melden=self.notify,
                                         stop=self.cancelled.is_set)
        self.fill = {}
        self.click = {}
        self.dismiss = {}
        self.original_fill = signup._fill
        self.original_click = signup._click
        for module in (signup, mailanbieter):
            self.fill[module] = self.stack.enter_context(patch.object(module, '_fill', return_value='#email'))
            self.click[module] = self.stack.enter_context(patch.object(module, '_click', return_value='#next'))
            self.dismiss[module] = self.stack.enter_context(patch.object(module, '_dismiss'))

    def call(self, ctx, action):
        if action == 'fill':
            return ctx.fill(['#email'], 'fixture@example.test')
        if action == 'click':
            return ctx.click(['#next'])
        if action == 'warte':
            return ctx.warte(100)
        if action in ('code_holen', 'link_holen'):
            return getattr(ctx, action)(timeout=1, ab=100)
        return getattr(ctx, action)()

    def test_signup_existing_stop_prevents_ui_and_mailbox_operations(self):
        self.cancelled.set()
        for action in ('fill', 'click', 'dismiss', 'warte', 'code_holen', 'link_holen'):
            with self.subTest(action=action), self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                self.call(self.signup, action)
        self.backend.page.assert_not_called()
        self.fill[signup].assert_not_called()
        self.click[signup].assert_not_called()
        self.dismiss[signup].assert_not_called()
        self.box.wait_for_code.assert_not_called()
        self.box.wait_for_link.assert_not_called()

    def test_mail_existing_stop_prevents_ui_operations(self):
        self.cancelled.set()
        for action in ('fill', 'click', 'dismiss', 'warte'):
            with self.subTest(action=action), self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                self.call(self.mail, action)
        self.backend.page.assert_not_called()
        self.fill[mailanbieter].assert_not_called()
        self.click[mailanbieter].assert_not_called()
        self.dismiss[mailanbieter].assert_not_called()

    def test_ui_operations_recheck_stop_after_blocking_helpers_return(self):
        for module, ctx in ((signup, self.signup), (mailanbieter, self.mail)):
            helpers = {'fill': self.fill[module], 'click': self.click[module],
                       'dismiss': self.dismiss[module], 'warte': self.page.wait_for_timeout}
            for action, helper in helpers.items():
                with self.subTest(context=module.__name__, action=action):
                    self.cancelled.clear()
                    helper.side_effect = lambda *args, **kwargs: self.cancelled.set()
                    try:
                        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                            self.call(ctx, action)
                    finally:
                        helper.side_effect = None

    def test_signup_rejects_mailbox_code_and_link_that_arrive_after_stop(self):
        for action, method, result in (
                ('code_holen', self.box.wait_for_code, ('123456', {})),
                ('link_holen', self.box.wait_for_link, ('https://example.test/confirm', {}))):
            with self.subTest(action=action):
                self.cancelled.clear()

                def late_result(*args, **kwargs):
                    self.cancelled.set()
                    return result

                method.side_effect = late_result
                with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                    self.call(self.signup, action)
                method.assert_called_once_with(platform='tiktok', timeout=1, newer_than=100,
                                               stop=self.cancelled.is_set)

    def test_cancelled_native_fill_does_not_try_a_second_selector(self):
        cancelled = self.cancelled

        class Field:
            @property
            def first(self):
                return self

            def count(self):
                return 1

            def is_visible(self):
                return True

            def fill(self, value, **kwargs):
                cancelled.set()
                raise RuntimeError('Offline native fill failure')

        class Page:
            def __init__(self):
                self.selectors = []

            def locator(self, selector):
                self.selectors.append(selector)
                return Field()

        for module, ctx in ((signup, self.signup), (mailanbieter, self.mail)):
            with self.subTest(context=module.__name__):
                self.cancelled.clear()
                page = Page()
                self.backend.page.return_value = page
                with patch.object(module, '_fill', self.original_fill):
                    with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                        ctx.fill(['#first', '#second'], 'fixture@example.test')
                self.assertEqual(page.selectors, ['#first'])

    def test_cancelled_native_click_does_not_try_a_second_selector(self):
        cancelled = self.cancelled

        class Page:
            def __init__(self):
                self.selectors = []

            def click(self, selector, **kwargs):
                self.selectors.append(selector)
                cancelled.set()
                raise RuntimeError('Offline native click failure')

        for module, ctx in ((signup, self.signup), (mailanbieter, self.mail)):
            with self.subTest(context=module.__name__):
                self.cancelled.clear()
                page = Page()
                self.backend.page.return_value = page
                with patch.object(module, '_click', self.original_click):
                    with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                        ctx.click(['#first', '#second'])
                self.assertEqual(page.selectors, ['#first'])

    def test_signup_cookie_read_cannot_return_success_or_continue_after_stop(self):
        tick = MagicMock()
        for outcome in ('authenticated', 'empty', 'error'):
            with self.subTest(outcome=outcome):
                self.cancelled.clear()
                self.page.wait_for_timeout.reset_mock()
                self.dismiss[signup].reset_mock()
                tick.reset_mock()

                def read(*args, **kwargs):
                    self.cancelled.set()
                    if outcome == 'error':
                        raise RuntimeError('Offline cookie-read failure')
                    return ([{'name': 'sessionid', 'value': 'fixture', 'domain': '.tiktok.com', 'path': '/'}]
                            if outcome == 'authenticated' else [])

                self.backend.read_cookies.side_effect = read
                with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                    self.signup.warte_auf_cookies(tick=tick)
                tick.assert_not_called()
                self.dismiss[signup].assert_not_called()
                self.page.wait_for_timeout.assert_not_called()

    def test_signup_tick_cancellation_prevents_a_second_cookie_read(self):
        self.backend.read_cookies.return_value = []

        def tick(remaining):
            self.cancelled.set()
            return True

        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.signup.warte_auf_cookies(tick=tick)
        self.backend.read_cookies.assert_called_once_with(self.handle)
        self.page.wait_for_timeout.assert_not_called()

    def test_normal_shared_ui_and_mailbox_operations_keep_their_results(self):
        for module, ctx in ((signup, self.signup), (mailanbieter, self.mail)):
            with self.subTest(context=module.__name__):
                self.assertEqual(self.call(ctx, 'fill'), '#email')
                self.assertEqual(self.call(ctx, 'click'), '#next')
                self.call(ctx, 'dismiss')
                self.call(ctx, 'warte')
                self.fill[module].assert_called_once_with(self.page, ['#email'], 'fixture@example.test',
                                                          check=ctx.check_cancelled)
                self.click[module].assert_called_once_with(self.page, ['#next'], check=ctx.check_cancelled)
                self.dismiss[module].assert_called_once_with(self.page, check=ctx.check_cancelled)
        self.assertEqual(self.call(self.signup, 'code_holen'), '123456')
        self.assertEqual(self.call(self.signup, 'link_holen'), 'https://example.test/confirm')

    def test_normal_signup_cookie_result_is_filtered_to_its_platform(self):
        cookies = [{'name': 'sessionid', 'value': 'fixture', 'domain': '.tiktok.com', 'path': '/'},
                   {'name': 'unrelated', 'value': 'fixture', 'domain': '.example.test', 'path': '/'}]
        self.backend.read_cookies.return_value = cookies
        result = self.signup.warte_auf_cookies()
        self.assertEqual(result, CookieJar.for_platform(cookies, 'tiktok'))
        self.assertEqual([cookie['name'] for cookie in result], ['sessionid'])

    def test_cancelled_password_fill_does_not_record_a_confirmed_password(self):
        def late_fill(*args, **kwargs):
            self.cancelled.set()
            return '#password'

        self.fill[mailanbieter].side_effect = late_fill
        with patch('socialscraper.mailanbieter.credentials.PasswordChangeGuard') as guard:
            with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                self.mail.fill(['#password'], self.mail.passwort)
            self.assertFalse(self.mail.password_filled)
            guard.assert_not_called()

    def confirmation(self, platform):
        self.page.url = ('https://myaccount.google.com/' if platform == 'google'
                         else 'https://outlook.live.com/mail/')
        self.backend.read_cookies.return_value = [
            {'name': 'SID', 'value': 'fixture', 'domain': '.google.com', 'path': '/'}]

    def test_existing_stop_prevents_mail_confirmation_work(self):
        self.cancelled.set()
        for platform in ('google', 'outlook'):
            with self.subTest(platform=platform), self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                self.mail.bestaetigen(platform)
        self.notify.assert_not_called()
        self.backend.page.assert_not_called()
        self.backend.read_cookies.assert_not_called()

    def test_mail_confirmation_stops_after_tick_before_read_or_navigation(self):
        for platform in ('google', 'outlook'):
            with self.subTest(platform=platform):
                self.cancelled.clear()
                self.confirmation(platform)
                self.backend.read_cookies.reset_mock()
                self.page.goto.reset_mock()
                with patch.object(mailanbieter, '_confirmed_address', return_value=True) as confirmed, \
                        patch.object(self.mail, 'require_known_password'):
                    with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                        self.mail.bestaetigen(platform, tick=self.cancelled.set)
                    self.backend.read_cookies.assert_not_called()
                    self.page.goto.assert_not_called()
                    confirmed.assert_not_called()

    def test_mail_confirmation_rejects_authenticated_cookies_returned_after_stop(self):
        self.confirmation('google')

        def read(*args, **kwargs):
            self.cancelled.set()
            return self.backend.read_cookies.return_value

        self.backend.read_cookies.side_effect = read
        with patch.object(mailanbieter, '_confirmed_address', return_value=True) as confirmed, \
                patch.object(self.mail, 'require_known_password'):
            with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                self.mail.bestaetigen('google')
            self.page.goto.assert_not_called()
            confirmed.assert_not_called()

    def test_mail_confirmation_stops_after_navigation_before_address_verification(self):
        for platform in ('google', 'outlook'):
            with self.subTest(platform=platform):
                self.cancelled.clear()
                self.confirmation(platform)
                self.page.goto.side_effect = lambda *args, **kwargs: self.cancelled.set()
                with patch.object(mailanbieter, '_confirmed_address', return_value=True) as confirmed, \
                        patch.object(self.mail, 'require_known_password'):
                    with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                        self.mail.bestaetigen(platform)
                    confirmed.assert_not_called()

    def test_mail_address_verification_cannot_return_success_after_stop(self):
        for platform in ('google', 'outlook'):
            with self.subTest(platform=platform):
                self.cancelled.clear()
                self.confirmation(platform)

                def confirmed(*args, **kwargs):
                    self.cancelled.set()
                    return True

                with patch.object(mailanbieter, '_confirmed_address', side_effect=confirmed), \
                        patch.object(self.mail, 'require_known_password') as password:
                    with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                        self.mail.bestaetigen(platform)
                    password.assert_not_called()

    def test_normal_mail_confirmation_verifies_address_and_known_password(self):
        for platform in ('google', 'outlook'):
            with self.subTest(platform=platform):
                self.confirmation(platform)
                self.page.goto.reset_mock()
                with patch.object(mailanbieter, '_confirmed_address', return_value=True) as confirmed, \
                        patch.object(self.mail, 'require_known_password') as password:
                    self.assertIsNone(self.mail.bestaetigen(platform))
                    expected_url = ('https://myaccount.google.com/' if platform == 'google'
                                    else 'https://outlook.live.com/mail/')
                    self.page.goto.assert_called_once_with(expected_url, wait_until='domcontentloaded')
                    confirmed.assert_called_once_with(self.page, platform, self.mail.adresse)
                    password.assert_called_once_with()

    def test_provider_return_and_session_close_cancellation_prevent_mailbox_persistence(self):
        from socialscraper import profile_settings
        from profile_fixture import PERSON
        cfg = SimpleNamespace(db_path='unused-test-db.sqlite', profiles_dir='unused-profiles', headless=True)
        identities = MagicMock()
        from socialscraper.net.proxy import Proxy
        identities.proxy_for.return_value = Proxy.parse('http://127.0.0.1:9')
        provider = SimpleNamespace(SIGNUP_URL='https://registration.example.test/', open_signup=MagicMock(), run=MagicMock())
        session = self.backend.session.return_value
        with patch.object(profile_settings, 'update', return_value={**profile_settings.defaults('fixture'), 'person':PERSON}), \
                patch.object(mailanbieter, 'ANBIETER', {'web.de': provider}), \
                patch.object(mailanbieter.proxymode, 'require_no_anon'), \
                patch.object(mailanbieter.proxymode, 'is_anonymous', return_value=False), \
                patch('socialscraper.mailanbieter.unitedinternet.raise_if_rejected'), \
                patch.object(mailanbieter.MailCtx,'require_known_password'), \
                patch('socialscraper.mailbox.build') as mailbox_build, \
                patch.object(mailanbieter.accountvault, 'add_postfach') as save:
            mailbox_build.return_value.check_access.return_value=0
            for phase in ('provider_return', 'session_close', 'normal'):
                with self.subTest(phase=phase):
                    self.cancelled.clear()
                    save.reset_mock()
                    provider.run.reset_mock()
                    session.reset_mock()

                    def run(*args, **kwargs):
                        args[1].account_confirmed=True;args[1].password_filled=True
                        if phase == 'provider_return':
                            self.cancelled.set()
                        return {}

                    def close(*args, **kwargs):
                        if phase == 'session_close':
                            self.cancelled.set()
                        return False

                    provider.run.side_effect = run
                    session.__exit__.side_effect = close

                    def create():
                        return mailanbieter._create_validated(
                            cfg, 'web.de', kennung='fixture', adresse='fixture@web.de',
                            vorname='Offline', nachname='Fixture', passwort='offline-password',
                            mode='assisted', land='de', mobile=False, timeout=1,
                            backend=self.backend, im=identities, melden=self.notify,
                            stop=self.cancelled.is_set)

                    if phase == 'normal':
                        self.assertEqual(create()['adresse'], 'fixture@web.de')
                        save.assert_called_once()
                    else:
                        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
                            create()
                        save.assert_not_called()
                    provider.run.assert_called_once()
                    session.__exit__.assert_called_once()
                    self.assertIsNone(provider.run.call_args.args[1].handle)


if __name__ == '__main__':
    unittest.main()
