"""R47: Firefox configuration and UI readiness checks without native processes."""
import contextlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from socialscraper import config, formlogic, guicore


class FirefoxConfig(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='firefox-config-')))
        self.stack.enter_context(patch.dict(os.environ))
        for key in config._ENV:
            os.environ.pop(key, None)
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        for method in ('connect', 'connect_ex', 'sendto', 'bind'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))

    def test_firefox_is_accepted_without_changing_default_engine(self):
        self.assertEqual(config.validate(config.Settings(engine='firefox')).engine, 'firefox')
        self.assertEqual(config.Settings().engine, 'patchright')
        self.assertEqual(formlogic.default_raw()['engine'], 'patchright')
        path = self.root / 'settings.toml'
        path.write_text(formlogic.DEFAULT_SETTINGS_TEXT, encoding='utf-8')
        self.assertEqual(config.load(path).engine, 'patchright')

    def test_selecting_firefox_preserves_unrelated_settings_and_credentials(self):
        path = self.root / 'settings.toml'
        source = ('# Keep this comment\nengine = "patchright"\nheadless = false\n'
                  'proxy_mode = "liste"\nproxies = ["http://fake:fixture@proxy.invalid:1234"]\n'
                  'credentials_file = "credentials-fixture.json"\nmax_comments = 17\n'
                  '[sphere_presets]\ntiktok = "fixture-preset"\n')
        path.write_text(source, encoding='utf-8')
        credentials = self.root / 'credentials-fixture.json'
        credentials.write_bytes(b'{"fixture": "never-real-credentials"}')
        before = credentials.read_bytes()
        guicore.write_settings({'engine': 'firefox'}, self.root)
        loaded = config.load(path)
        self.assertEqual(loaded.engine, 'firefox')
        self.assertEqual(loaded.max_comments, 17)
        self.assertFalse(loaded.headless)
        self.assertEqual(loaded.proxy_mode, 'liste')
        self.assertEqual(loaded.proxies, ['http://fake:fixture@proxy.invalid:1234'])
        self.assertEqual(loaded.sphere_presets, {'tiktok': 'fixture-preset'})
        self.assertEqual(path.read_text(encoding='utf-8'), source.replace('engine = "patchright"', 'engine = "firefox"'))
        self.assertEqual(credentials.read_bytes(), before)

    def test_environment_selects_firefox_without_rewriting_settings(self):
        path = self.root / 'settings.toml'
        path.write_text('engine = "nodriver"\n', encoding='utf-8')
        before = path.read_bytes()
        os.environ['SS_ENGINE'] = 'firefox'
        self.assertEqual(config.load(path).engine, 'firefox')
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_engine_is_rejected_by_configuration_and_form(self):
        with self.assertRaisesRegex(ValueError, 'Browser-Engine'):
            config.validate(config.Settings(engine='unsupported-browser'))
        raw = formlogic.default_raw()
        raw['engine'] = 'unsupported-browser'
        settings, _, errors = formlogic.validate_form(raw)
        self.assertIn('engine', errors)
        self.assertNotIn('engine', settings)

    def test_firefox_form_roundtrip_keeps_existing_proxy_selection(self):
        cfg = config.Settings(engine='firefox', proxy_mode='liste', proxies=['http://proxy.invalid:1234'])
        raw = formlogic.default_raw()
        raw.update(formlogic.settings_to_raw(cfg))
        settings, _, errors = formlogic.validate_form(raw)
        self.assertEqual(errors, {})
        self.assertEqual(settings['engine'], 'firefox')
        self.assertEqual(settings['proxy_mode'], 'liste')
        self.assertEqual(settings['proxies'], cfg.proxies)

    def test_ui_choices_distinguish_browsers_controllers_and_desktop_limit(self):
        from socialscraper.ui.tab_settings import CHOICES, SettingsTab
        self.assertIs(CHOICES['engine'], formlogic.ENGINES)
        choices = {value: label for label, value in CHOICES['engine']}
        self.assertEqual(set(choices), {'patchright', 'nodriver', 'firefox'})
        self.assertIn('Chrome / Patchright', choices['patchright'])
        self.assertIn('Chrome / Nodriver', choices['nodriver'])
        self.assertIn('Firefox / Playwright', choices['firefox'])
        self.assertIn('Desktop', choices['firefox'])
        # Call only the field-building description on an inert fake, without Tk.
        tab = Mock()
        SettingsTab._build_abruf(tab, object())
        engine_call = next(call for call in tab._choice_field.call_args_list if call.args[1] == 'engine')
        hint = engine_call.args[3]
        self.assertIn('Desktop', hint)
        self.assertIn('mobile', hint)
        self.assertIn('Proxy', hint)

    def test_cli_accepts_firefox_for_each_existing_browser_command(self):
        from socialscraper.cli import parser
        arguments = (['scrape', 'https://fixture.invalid/video'], ['profile', 'fixture'],
                     ['login'], ['check'], ['logout'], ['import-cookies', '--file', 'fixture.json'])
        for args in arguments:
            self.assertEqual(parser().parse_args([*args, '--engine', 'firefox']).engine, 'firefox')
        self.assertIsNone(parser().parse_args(['check']).engine)

    def test_firefox_system_check_uses_runtime_status_without_chrome_requirement(self):
        from socialscraper.ui import common
        runtime = types.ModuleType('socialscraper.net.firefox_runtime')
        runtime.installation_status = Mock(return_value=(True, 'Firefox / Playwright bereit'))
        with patch.dict(sys.modules, {'socialscraper.net.firefox_runtime': runtime}), \
                patch.object(common.importlib.util, 'find_spec', return_value=object()) as find_spec, \
                patch.object(common.shutil, 'which', side_effect=lambda name: 'node-fixture' if name == 'node' else None) as which:
            rows, ok = common.system_check(config.Settings(engine='firefox'))
            self.assertTrue(ok)
            self.assertIn('✔ Firefox / Playwright bereit', rows)
            self.assertFalse(any('Chrome' in row for row in rows))
            self.assertNotIn('patchright', [call.args[0] for call in find_spec.call_args_list])
            self.assertNotIn('chrome', [call.args[0] for call in which.call_args_list])
            runtime.installation_status.assert_called_once_with()
            runtime.installation_status.return_value = (False, 'Firefox-Browser fehlt')
            rows, ok = common.system_check(config.Settings(engine='firefox'))
            self.assertFalse(ok)
            self.assertIn('✖ Firefox-Browser fehlt', rows)

    def test_system_check_without_configuration_keeps_chrome_defaults(self):
        from socialscraper.ui import common
        with patch.object(common.importlib.util, 'find_spec', return_value=object()) as find_spec, \
                patch.object(common.pathlib.Path, 'is_file', return_value=False), \
                patch.object(common.shutil, 'which', side_effect=lambda name: 'node-fixture' if name == 'node' else None):
            rows, ok = common.system_check()
        self.assertFalse(ok)
        self.assertIn('✖ Google Chrome', rows)
        self.assertIn('patchright', [call.args[0] for call in find_spec.call_args_list])


if __name__ == '__main__':
    unittest.main()
