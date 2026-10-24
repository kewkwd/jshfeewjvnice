"""AVD safety regressions with fake SDK files; never boots a device."""
import hashlib
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.android import avd, geraet, avd_session
from socialscraper.android.adb import Adb
from socialscraper.net.errors import FinalError


class SdkFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.sdk = self.base / 'sdk'
        self.home = self.base / 'avds'
        files = []
        for name in ('emulator/emulator.exe', 'emulator/qemu/windows-x86_64/qemu-system-x86_64.exe',
                     'platform-tools/adb.exe', *(avd.IMAGE+'/'+name for name in
                        ('system.img', 'vendor.img', 'ramdisk.img', 'kernel-ranchu', 'source.properties', 'userdata.img'))):
            target = self.sdk/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'offline fixture')
            files.append({'path': name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
        self.marker = {'schema': 1, 'complete': True, 'image': avd.IMAGE, 'files': files}
        self.write_marker()

    def write_marker(self):
        (self.sdk/'socialscraper-install.json').write_text(json.dumps(self.marker), encoding='utf8')

    def test_profile_preparation_is_idempotent_and_preserves_user_data(self):
        with patch('subprocess.Popen', side_effect=AssertionError('native start')):
            folder = avd.prepare_profile(sdk=self.sdk, home=self.home)
            payload = folder/'userdata-qemu.img'
            payload.write_bytes(b'preserve session')
            self.assertEqual(avd.prepare_profile(sdk=self.sdk, home=self.home), folder)
            self.assertEqual(payload.read_bytes(), b'preserve session')

    def test_incomplete_sdk_is_rejected(self):
        self.marker['complete'] = False
        self.write_marker()
        with self.assertRaises(FinalError):
            avd.prepare_profile(sdk=self.sdk, home=self.home)
        self.assertFalse(self.home.exists())

    def test_official_directory_data_layout_is_accepted_without_userdata_image(self):
        (self.sdk/avd.IMAGE/'userdata.img').unlink()
        marker = self.sdk/avd.IMAGE/'data/empty_data_disk'
        marker.parent.mkdir()
        marker.write_bytes(b'Official image creates an empty data partition at boot.')
        with patch('subprocess.Popen', side_effect=AssertionError('native start')):
            self.assertEqual(avd.sdk_info(self.sdk)[0], self.sdk)
            avd.prepare_profile(sdk=self.sdk, home=self.home)

    def test_missing_or_empty_data_template_is_rejected(self):
        (self.sdk/avd.IMAGE/'userdata.img').unlink()
        with self.assertRaisesRegex(FinalError, 'Benutzerdaten'):
            avd.sdk_info(self.sdk)
        marker = self.sdk/avd.IMAGE/'data/empty_data_disk'
        marker.parent.mkdir()
        marker.write_bytes(b'')
        with self.assertRaisesRegex(FinalError, 'Benutzerdaten'):
            avd.sdk_info(self.sdk)

    def test_missing_boot_component_is_rejected_even_with_userdata(self):
        (self.sdk/avd.IMAGE/'kernel-ranchu').unlink()
        with self.assertRaisesRegex(FinalError, 'Datei fehlt'):
            avd.sdk_info(self.sdk)

    def test_changed_sdk_program_is_rejected(self):
        (self.sdk/'platform-tools/adb.exe').write_bytes(b'changed')
        with self.assertRaises(FinalError):
            avd.sdk_info(self.sdk)

    def test_new_untracked_native_program_is_rejected(self):
        (self.sdk/'emulator/new.exe').write_bytes(b'unknown')
        with self.assertRaises(FinalError):
            avd.sdk_info(self.sdk)

    def test_foreign_profile_is_never_adopted_or_reset(self):
        folder = self.home/(avd.TEST_NAME+'.avd')
        folder.mkdir(parents=True)
        payload = folder/'userdata-qemu.img'
        payload.write_bytes(b'foreign')
        with self.assertRaises(FinalError):
            avd.prepare_profile(sdk=self.sdk, home=self.home)
        self.assertEqual(payload.read_bytes(), b'foreign')

    def test_profile_image_and_index_cannot_escape(self):
        folder = avd.prepare_profile(sdk=self.sdk, home=self.home)
        index = self.home/(avd.TEST_NAME+'.ini')
        original = index.read_text(encoding='utf8')
        index.write_text('path='+str(self.base/'foreign'), encoding='utf8')
        with self.assertRaises(FinalError):
            avd.verify_profile(avd.TEST_NAME, sdk=self.sdk, home=self.home)
        index.write_text(original, encoding='utf8')
        config = folder/'config.ini'
        config.write_text(config.read_text(encoding='utf8').replace(str(self.sdk).replace('\\','/'), str(self.base/'foreign').replace('\\','/')), encoding='utf8')
        with self.assertRaises(FinalError):
            avd.verify_profile(avd.TEST_NAME, sdk=self.sdk, home=self.home)

    def test_guard_failure_precedes_any_native_start_or_bridge(self):
        avd.prepare_profile(sdk=self.sdk, home=self.home)
        with patch.object(avd, 'verify_guard', side_effect=FinalError('guard missing')), \
             patch('subprocess.Popen') as native, \
             patch('socialscraper.net.browserproxy.BrowserProxy') as bridge:
            with self.assertRaisesRegex(FinalError, 'guard missing'):
                with avd_session.Session(avd.TEST_NAME, 'http://proxy.example.test:80', sdk=self.sdk, home=self.home):
                    self.fail('must not enter')
        native.assert_not_called()
        bridge.assert_not_called()

    def test_guard_requires_actual_powershell_success_not_marker(self):
        with patch.object(avd.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as call:
            with self.assertRaises(FinalError):
                avd.verify_guard(self.sdk)
        self.assertIn('-VerifyOnly', call.call_args.args[0])

    def test_guard_child_does_not_inherit_incompatible_powershell_module_path(self):
        inherited = {'PSModulePath': 'C:/PowerShell7/Modules', 'KEEP_FIXTURE': 'unchanged'}
        with patch.dict(os.environ, inherited, clear=True), \
                patch.object(avd.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as call:
            self.assertTrue(avd.verify_guard(self.sdk))
            child = call.call_args.kwargs.get('env')
            self.assertIsNotNone(child)
            self.assertFalse(any(key.casefold() == 'psmodulepath' for key in child))
            self.assertEqual(child['KEEP_FIXTURE'], 'unchanged')
            self.assertEqual(os.environ['PSModulePath'], inherited['PSModulePath'])

    def test_guard_child_timeout_keeps_parent_environment_and_start_blocked(self):
        with patch.dict(os.environ, {'PSModulePath': 'fixture-path'}), \
                patch.object(avd.subprocess, 'run', side_effect=avd.subprocess.TimeoutExpired('powershell', 45)):
            with self.assertRaisesRegex(FinalError, 'Start gesperrt'):
                avd.verify_guard(self.sdk)
            self.assertEqual(os.environ['PSModulePath'], 'fixture-path')

    def test_complete_session_routes_proxy_and_dns_locally_and_cleans_up(self):
        avd.prepare_profile(sdk=self.sdk, home=self.home)
        with ExitStack() as stack:
            guard = stack.enter_context(patch.object(avd, 'verify_guard', return_value=True))
            bridge = stack.enter_context(patch('socialscraper.net.browserproxy.BrowserProxy')).return_value
            bridge.playwright = {'server': 'http://127.0.0.1:23456'}
            dns = stack.enter_context(patch('socialscraper.android.avd_dns.ProxyDns')).return_value
            dns.start.return_value = dns
            job = stack.enter_context(patch('socialscraper.android.avd_process.WindowsOwnedJob')).return_value
            job.run.return_value = SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
            process = job.start.return_value
            process.poll.return_value = None
            stack.enter_context(patch.object(avd_session, 'emulator_port', return_value=5560))
            stack.enter_context(patch.object(avd_session.socket, 'create_connection'))
            device = stack.enter_context(patch.object(avd_session, 'Adb')).return_value
            device.sh.side_effect = lambda *args, **kwargs: (
                'config: mcc310-mnc260-de-rDE-ldltr-v35' if args == ('am', 'get-config') else '1')
            with avd_session.Session(avd.TEST_NAME, 'http://fixture:PRIVATE@proxy.example.test:80',
                                     sdk=self.sdk, home=self.home) as actual:
                self.assertIs(actual, device)
                actual._socialscraper_avd_session.check()
            guard.assert_called_with(self.sdk)
            self.assertEqual(guard.call_count, 2)
            self.assertEqual(job.start.call_count, 2)
            server_call = job.start.call_args_list[0]
            server_args = server_call.args[0]
            listener = server_args[server_args.index('-L') + 1]
            # Windows ADB rejects a hostname in its listener specification.
            # Without -a, tcp:PORT binds loopback; clients still use an explicit host.
            self.assertRegex(listener, r'^tcp:[0-9]+$')
            self.assertNotIn('-a', server_args)
            self.assertEqual(server_call.kwargs['env']['ADB_SERVER_SOCKET'],
                             'tcp:127.0.0.1:' + listener.split(':')[1])
            self.assertEqual(job.run.call_args.args[0][-1], '-accel-check')
            args = job.start.call_args.args[0]
            self.assertEqual(args[args.index('-http-proxy')+1], 'http://127.0.0.1:23456')
            self.assertEqual(args[args.index('-dns-server')+1], '127.0.0.1')
            self.assertNotIn('PRIVATE', ' '.join(args))
            self.assertIn('-no-window', args)
            self.assertIn('-no-snapshot', args)
            self.assertNotIn('-wipe-data', args)
            device._cmd.assert_called_once_with('emu', 'kill', timeout=5, _check_cancel=False)
            job.close.assert_called_once()
            dns.close.assert_called_once()
            bridge.close.assert_called_once()

    def test_missing_acceleration_stops_before_adb_or_emulator_start(self):
        avd.prepare_profile(sdk=self.sdk, home=self.home)
        with ExitStack() as stack:
            stack.enter_context(patch.object(avd, 'verify_guard', return_value=True))
            bridge = stack.enter_context(patch('socialscraper.net.browserproxy.BrowserProxy')).return_value
            bridge.playwright = {'server': 'http://127.0.0.1:23456'}
            dns = stack.enter_context(patch('socialscraper.android.avd_dns.ProxyDns')).return_value
            dns.start.return_value = dns
            job = stack.enter_context(patch('socialscraper.android.avd_process.WindowsOwnedJob')).return_value
            job.run.return_value = SimpleNamespace(returncode=1, stdout=b'PRIVATE diagnostic', stderr=b'')
            with self.assertRaisesRegex(FinalError, 'Hypervisor') as raised:
                with avd_session.Session(avd.TEST_NAME, 'http://proxy.example.test:80',
                                         sdk=self.sdk, home=self.home):
                    self.fail('Acceleration unavailable')
            self.assertNotIn('PRIVATE', str(raised.exception))
            job.start.assert_not_called()
            job.close.assert_called_once()
            dns.close.assert_called_once()
            bridge.close.assert_called_once()


class RuntimeBoundary(unittest.TestCase):
    def test_missing_or_unsupported_proxy_is_always_rejected(self):
        for proxy in (None, '', 'bad://private:secret@host:3', 'https://host:4', 'socks4://host:5'):
            with self.subTest(proxy=bool(proxy)), patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(FinalError) as raised:
                    avd_session.Session(avd.TEST_NAME, proxy)
                self.assertNotIn('secret', str(raised.exception))

    def test_legacy_entrypoints_do_not_start_or_adopt_devices(self):
        with patch('subprocess.Popen') as child:
            for entry in (geraet.starten, geraet.verbinden, geraet.stoppen):
                with self.assertRaises(FinalError):
                    entry('emulator-5554')
            with self.assertRaises(FinalError):
                with geraet.mit_geraet(serial='foreign', proxy='http://example.test:80'):
                    self.fail('foreign')
        child.assert_not_called()

    def test_prepare_preserves_app_session_and_proxy(self):
        device = MagicMock()
        device.sh.return_value = 'package:/data/app/com.example/base.apk'
        device.modell.return_value = 'Test AVD'
        self.assertEqual(geraet.vorbereiten(device), 'Test AVD')
        device.daten_loeschen.assert_not_called()
        device.proxy_setzen.assert_not_called()
        device.app_stoppen.assert_not_called()

    def test_prepare_requires_ownership_and_installed_app(self):
        with self.assertRaises(FinalError):
            geraet.vorbereiten(SimpleNamespace())
        device = MagicMock()
        device.sh.return_value = ''
        with self.assertRaisesRegex(FinalError, 'nicht installiert'):
            geraet.vorbereiten(device)
        device.daten_loeschen.assert_not_called()

    def test_adb_environment_is_copied_and_given_only_to_child(self):
        env = {'ADB_SERVER_SOCKET': 'tcp:127.0.0.1:17777'}
        adb = Adb('emulator-5560', env=env)
        env.clear()
        with patch('socialscraper.android.adb.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=b'ok')) as run:
            self.assertEqual(adb.sh('getprop', 'sys.boot_completed'), 'ok')
        self.assertEqual(run.call_args.kwargs['env'], {'ADB_SERVER_SOCKET': 'tcp:127.0.0.1:17777'})

    def test_avd_adb_command_cannot_escape_its_owned_runner(self):
        session = avd_session.Session(avd.TEST_NAME, 'http://proxy.example.test:80')
        session.job, session.server = MagicMock(), MagicMock()
        session.server.poll.return_value = None
        session.sdk = Path('C:/owned-sdk')
        session.job.run.return_value = SimpleNamespace(returncode=0, stdout=b'ok', stderr=b'')
        env = {'ADB_SERVER_SOCKET': 'tcp:127.0.0.1:17777'}
        device = Adb('emulator-5560', adb='C:/owned-sdk/platform-tools/adb.exe',
                     env=env, runner=session._run_adb)
        with patch('subprocess.run', side_effect=AssertionError('Unowned ADB forbidden')):
            self.assertEqual(device.sh('getprop', 'sys.boot_completed', timeout=3), 'ok')
            session.job.run.assert_called_once_with(
                ['C:/owned-sdk/platform-tools/adb.exe', '-s', 'emulator-5560', 'shell', 'getprop', 'sys.boot_completed'],
                env=env, cwd=session.sdk, timeout=3)
            session.server.poll.return_value = 1
            with self.assertRaisesRegex(avd_session.AdbError, 'Server'):
                device.sh('getprop', 'sys.boot_completed')
            self.assertEqual(session.job.run.call_count, 1)

    def test_child_environment_cannot_inherit_foreign_proxy_or_server(self):
        from pathlib import Path
        with patch.dict(os.environ, {'http_proxy':'http://secret@host', 'ANDROID_SERIAL':'foreign',
                                     'ADB_SERVER_SOCKET':'tcp:external.example:5037'}, clear=True):
            env = avd_session.environment(Path('sdk'), Path('home'), 17777)
            self.assertEqual(os.environ['ANDROID_SERIAL'], 'foreign')
        self.assertNotIn('http_proxy', env)
        self.assertNotIn('ANDROID_SERIAL', env)
        self.assertEqual(env['ADB_SERVER_SOCKET'], 'tcp:127.0.0.1:17777')

    def test_tiktok_decorator_does_not_create_browser_runtime(self):
        from socialscraper.operation import browser_owned
        def create(cfg, platform):
            return platform
        create.__module__ = 'socialscraper.signup'
        with patch('socialscraper.net.proxymode.require_no_anon'), patch('socialscraper.cli.make_runtime') as make:
            self.assertEqual(browser_owned(create)(SimpleNamespace(), 'tiktok'), 'tiktok')
        make.assert_not_called()

    def test_native_cleanup_continues_after_resource_failure(self):
        session = avd_session.Session(avd.TEST_NAME, 'http://proxy.example.test:80')
        session.job, session.dns, session.bridge, session.lock = [MagicMock() for _ in range(4)]
        session.job.close.side_effect = RuntimeError('failed')
        with self.assertRaises(FinalError):
            session.__exit__(None, None, None)
        session.dns.close.assert_called_once()
        session.bridge.close.assert_called_once()
        session.lock.release.assert_not_called()
        self.assertIn(session, avd_session._RETAINED)
        avd_session._RETAINED.remove(session)
