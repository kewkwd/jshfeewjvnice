"""Use only an explicitly identified TikTok account; every Android view is fake."""
import contextlib
import os
import socket
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import xml.etree.ElementTree as ET

from socialscraper import operation
from socialscraper.android import tiktok_app
from socialscraper.net.errors import FinalError


def tree(*, handle='fixture.valid', kind='username', edit=True, package=tiktok_app.PAKET,
         extra_handle=None, logged_out=False, code=False):
    root = ET.Element('hierarchy')
    frame = ET.SubElement(root, 'node', {'package': package, 'class': 'android.widget.FrameLayout',
                                       'bounds': '[0,0][1080,1920]'})
    def node(text, name, bounds, **attrs):
        return ET.SubElement(frame, 'node', {'package': package, 'text': text,
            'class': 'android.widget.TextView', 'resource-id': package + ':id/' + name,
            'bounds': bounds, 'visible-to-user': 'true', 'enabled': 'true', **attrs})
    if handle is not None:
        names = {'username': 'profile_username', 'bio': 'bio', 'display': 'display_name', 'label': 'obfuscated'}
        text = '@' + handle if kind != 'label' else ''
        attrs = {'content-desc': 'Username: @' + handle} if kind == 'label' else {}
        node(text, names[kind], '[100,150][800,210]' if kind != 'bio' else '[100,650][800,710]', **attrs)
    if extra_handle:
        node('@' + extra_handle, 'unique_id', '[100,230][800,280]')
    if edit:
        node('Edit profile', 'edit_profile', '[100,450][600,530]',
             **{'clickable': 'true', 'class': 'android.widget.Button'})
    if logged_out:
        node('Log in', 'login', '[100,450][600,530]',
             **{'clickable': 'true', 'class': 'android.widget.Button'})
    if code:
        node('Code', 'verification', '[100,300][700,400]')
    node('Profile', 'profile_tab', '[750,1800][1000,1900]', clickable='true', selected='true')
    return root


class AvdAccountSessionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network forbidden')))
        for method in ('connect', 'connect_ex', 'bind', 'sendto'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('Socket forbidden')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process forbidden')))
        self.stack.enter_context(patch.object(os, 'system', side_effect=AssertionError('Shell forbidden')))
        self.cancelled = threading.Event()
        self.now = 10.0
        self.stack.enter_context(patch.object(tiktok_app.time, 'monotonic', side_effect=lambda: self.now))
        self.stack.enter_context(patch.object(tiktok_app.time, 'sleep', side_effect=lambda seconds: self.advance(seconds)))
        self.stack.enter_context(patch.object(operation, 'wait', side_effect=self.wait))
        self.ctx = SimpleNamespace(email='fixture@example.test', password='offline-only',
            username='local_fixture_identity', identity='local_fixture_identity', tiktok_username='fixture.valid',
            timeout=5, stop=self.cancelled.is_set, box=None, melden=MagicMock(), code_holen=MagicMock())
        self.view = tree()
        self.adb = MagicMock()
        self.adb.baum.side_effect = lambda **kwargs: self.view
        self.adb.finde.side_effect = self.find
        self.adb.tippe_auf.side_effect = self.tap_text
        self.adb.daten_loeschen.side_effect = AssertionError('App data must be preserved')
        self.adb.app_stoppen.side_effect = AssertionError('No account logout/reset')
        self.dismiss = self.stack.enter_context(patch.object(tiktok_app, '_wegklicken'))
        self.navigate = self.stack.enter_context(patch.object(tiktok_app, '_tippe', return_value=True))
        self.fill = self.stack.enter_context(patch.object(tiktok_app, '_feld_fuellen'))
        self.next = self.stack.enter_context(patch.object(tiktok_app, '_weiter'))

    def advance(self, seconds):
        self.now += seconds

    def wait(self, seconds, stop=None):
        self.advance(seconds)
        return not (stop and stop())

    def find(self, *, teil=None, **kwargs):
        if teil and any(teil.casefold() in (node.get('text') or '').casefold()
                        for node in self.view.iter('node')):
            return (100, 100)
        return None

    def tap_text(self, *, teil=None, **kwargs):
        return bool(self.find(teil=teil))

    def login(self):
        return tiktok_app.anmelden(self.adb, self.ctx)

    def no_credentials(self):
        self.fill.assert_not_called()
        self.ctx.code_holen.assert_not_called()
        self.adb.daten_loeschen.assert_not_called()
        self.adb.app_stoppen.assert_not_called()

    def fresh_login(self, final_view):
        self.view = tree(handle=None, edit=False, logged_out=True)
        def fill(adb, value):
            if value == self.ctx.password:
                self.view = final_view
        self.fill.side_effect = fill

    def test_matching_existing_profile_is_reused_without_credential_input(self):
        self.assertTrue(self.login())
        self.no_credentials()
        self.adb.tippe_auf.assert_not_called()
        self.navigate.assert_not_called()

    def test_feed_uses_exact_profile_tab_without_clicking_edit_profile_by_substring(self):
        self.view = tree(handle=None, edit=False)
        def tap(**kwargs):
            self.assertNotIn('teil', kwargs)
            if kwargs.get('text') == 'Profile':
                self.view = tree()
                return True
            return False
        self.adb.tippe_auf.side_effect = tap
        self.assertTrue(self.login())
        self.no_credentials()
        self.assertEqual([call.kwargs['text'] for call in self.adb.tippe_auf.call_args_list],
                         ['Profil', 'Profile'])

    def test_cancellation_during_profile_navigation_prevents_second_action(self):
        self.view = tree(handle=None, edit=False)
        def tap(**kwargs):
            self.cancelled.set()
            return False
        self.adb.tippe_auf.side_effect = tap
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.login()
        self.adb.tippe_auf.assert_called_once()
        self.no_credentials()

    def test_explicit_handle_is_normalized_and_accessibility_label_is_supported(self):
        self.ctx.tiktok_username = ' @Fixture.Valid '
        self.view = tree(kind='label')
        self.assertTrue(self.login())
        self.no_credentials()

    def test_missing_handle_stops_before_start_even_when_local_identity_matches(self):
        self.ctx.tiktok_username = ''
        self.ctx.username = self.ctx.identity = 'fixture.valid'
        with self.assertRaisesRegex(FinalError, 'TikTok-Benutzername'):
            self.login()
        self.adb.app_starten.assert_not_called()
        self.no_credentials()

    def test_foreign_existing_account_is_not_accepted_or_modified(self):
        self.view = tree(handle='unrelated.fixture')
        with self.assertRaises(FinalError) as raised:
            self.login()
        self.assertNotIn('unrelated.fixture', str(raised.exception))
        self.no_credentials()
        self.adb.tippe_auf.assert_not_called()

    def test_bio_and_display_name_mentions_are_not_username_proof(self):
        for kind in ('bio', 'display'):
            with self.subTest(kind=kind):
                self.view = tree(kind=kind)
                with self.assertRaises(FinalError):
                    self.login()
                self.no_credentials()

    def test_another_package_or_public_profile_is_not_own_account_proof(self):
        for view in (tree(package='other.fixture.app'), tree(edit=False)):
            with self.subTest(view=ET.tostring(view)):
                self.view = view
                with self.assertRaises(FinalError):
                    self.login()
                self.no_credentials()

    def test_display_name_cannot_impersonate_an_explicit_username_label(self):
        for resource, attribute in (('display_name', 'text'), ('display_name', 'content-desc'),
                                    ('obfuscated', 'text')):
            with self.subTest(resource=resource, attribute=attribute):
                self.view = tree(kind='display')
                node = next(node for node in self.view.iter('node')
                            if node.get('resource-id', '').endswith('/display_name'))
                node.set('resource-id', tiktok_app.PAKET + ':id/' + resource)
                node.set(attribute, 'Username: @fixture.valid')
                with self.assertRaises(FinalError):
                    self.login()
                self.no_credentials()

    def test_multiple_profile_handle_candidates_are_ambiguous(self):
        self.view = tree(extra_handle='unrelated.fixture')
        with self.assertRaises(FinalError):
            self.login()
        self.no_credentials()

    def test_hidden_or_disabled_edit_profile_action_is_not_authentication(self):
        for attribute in ('enabled', 'visible-to-user'):
            with self.subTest(attribute=attribute):
                self.view = tree()
                edit = next(node for node in self.view.iter('node') if node.get('text') == 'Edit profile')
                edit.set(attribute, 'false')
                with self.assertRaises(FinalError):
                    self.login()
                self.no_credentials()

    def test_username_in_hidden_parent_is_not_account_proof(self):
        self.view = tree()
        frame = self.view.find('node')
        username = next(node for node in frame if node.get('resource-id', '').endswith('/profile_username'))
        frame.remove(username)
        parent = ET.SubElement(frame, 'node', {'package': tiktok_app.PAKET, 'visible-to-user': 'false'})
        parent.append(username)
        with self.assertRaises(FinalError):
            self.login()
        self.no_credentials()

    def test_invalid_android_tree_fails_without_any_credentials(self):
        self.adb.baum.return_value = None
        self.adb.baum.side_effect = lambda **kwargs: None
        with self.assertRaises(FinalError):
            self.login()
        self.no_credentials()

    def test_existing_cancellation_prevents_app_start(self):
        self.cancelled.set()
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.login()
        self.adb.app_starten.assert_not_called()
        self.no_credentials()

    def test_cancellation_during_profile_read_cannot_report_success(self):
        def read(**kwargs):
            self.cancelled.set()
            return self.view
        self.adb.baum.side_effect = read
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.login()
        self.no_credentials()

    def test_fresh_login_requires_matching_profile_before_success(self):
        self.fresh_login(tree())
        self.assertTrue(self.login())
        self.assertEqual([call.args[1] for call in self.fill.call_args_list],
                         [self.ctx.email, self.ctx.password])

    def test_fresh_login_cannot_confirm_a_different_account(self):
        self.fresh_login(tree(handle='unrelated.fixture'))
        with self.assertRaises(FinalError):
            self.login()
        self.assertEqual(self.fill.call_count, 2)
        self.adb.daten_loeschen.assert_not_called()
        self.adb.app_stoppen.assert_not_called()

    def test_cancellation_during_email_fill_prevents_password_and_submit(self):
        self.view = tree(handle=None, edit=False, logged_out=True)
        self.fill.side_effect = lambda *args: self.cancelled.set()
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.login()
        self.assertEqual([call.args[1] for call in self.fill.call_args_list], [self.ctx.email])
        self.next.assert_not_called()

    def test_cancellation_during_code_lookup_prevents_code_input(self):
        self.fresh_login(tree(handle=None, edit=False, code=True))
        self.ctx.box = object()
        def code(**kwargs):
            self.cancelled.set()
            return '123456'
        self.ctx.code_holen.side_effect = code
        with self.assertRaisesRegex(FinalError, 'Abgebrochen'):
            self.login()
        self.assertEqual([call.args[1] for call in self.fill.call_args_list],
                         [self.ctx.email, self.ctx.password])
        self.ctx.code_holen.assert_called_once()

    def test_unconfirmed_profile_after_login_is_an_explicit_bounded_error(self):
        self.fresh_login(tree(handle=None, edit=False))
        with self.assertRaises(FinalError):
            self.login()
        self.assertLessEqual(self.now, 30)


if __name__ == '__main__':
    unittest.main()
