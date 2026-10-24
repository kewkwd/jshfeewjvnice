"""Offline regression tests for the independent copy and transactional repair."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import uuid

from build import repair_environment as env
from build import relocate_avd as move
from socialscraper.android import avd

ROOT = Path(__file__).resolve().parents[1]


class FileTransactions(unittest.TestCase):
    def test_failed_postcheck_rolls_back_existing_and_new_file(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new = Path(folder) / 'old', Path(folder) / 'new'
            old.write_bytes(b'before')
            def fail(): raise ValueError('postcheck')
            with self.assertRaisesRegex(ValueError, 'postcheck'):
                env.replace_files({old: b'changed', new: b'new'}, fail)
            self.assertEqual(old.read_bytes(), b'before')
            self.assertFalse(new.exists())
            self.assertEqual(list(Path(folder).glob('.repair-*')), [])

    def test_failed_second_replace_rolls_back_first(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / 'a', Path(folder) / 'b'
            a.write_bytes(b'a'); b.write_bytes(b'b')
            replace = os.replace
            count = 0
            def fail_second(src, dst):
                nonlocal count
                count += 1
                if count == 2: raise OSError('write failed')
                return replace(src, dst)
            with patch.object(env.os, 'replace', side_effect=fail_second):
                with self.assertRaisesRegex(OSError, 'write failed'):
                    env.replace_files({a: b'A', b: b'B'})
            self.assertEqual((a.read_bytes(), b.read_bytes()), (b'a', b'b'))
            self.assertEqual(list(Path(folder).glob('.repair-*')), [])


class AvdRelocation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source, self.dest = self.base / 'source', self.base / 'destination'
        self.name = 'ss_fixture'
        self.home = self.source / 'data/android-avd'
        folder = self.home / (self.name + '.avd')
        folder.mkdir(parents=True)
        sdk = self.source / 'tools/android-sdk'
        owner = {'schema': 1, 'name': self.name, 'sdk': str(sdk), 'directory': str(folder),
                 'image': avd.IMAGE, 'fresh_profile': True, 'preserved': 'yes'}
        (folder / 'socialscraper-owned.json').write_text(json.dumps(owner), encoding='utf8')
        (self.home / (self.name + '.ini')).write_text('path=' + str(folder) + '\n', encoding='utf8')
        (folder / 'config.ini').write_text('AvdId=' + self.name + '\nabi.type=x86_64\nimage.sysdir.1=' + str(sdk / avd.IMAGE).replace('\\', '/') + '/\n', encoding='utf8')
        (folder / 'hardware-qemu.ini').write_text('disk=' + str(folder / 'disk.qcow2') + '\n', encoding='utf8')
        import shutil
        shutil.copytree(self.source, self.dest)
        self.guard = patch.object(move, 'ensure_stopped')
        self.guard.start(); self.addCleanup(self.guard.stop)
        self.sdk = patch.object(avd, 'sdk_info', side_effect=lambda sdk: (sdk.resolve(), {}, []))
        self.sdk.start(); self.addCleanup(self.sdk.stop)

    def test_exact_copy_is_rebased_without_changing_original(self):
        originals = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(move.relocate(self.source, self.dest, self.name), 4)
        target = avd.verify_profile(self.name, sdk=self.dest / 'tools/android-sdk', home=self.dest / 'data/android-avd')
        owner = json.loads((target / 'socialscraper-owned.json').read_text())
        self.assertEqual(owner['preserved'], 'yes')
        self.assertIn(str(self.dest).replace('\\', '/'), (target / 'hardware-qemu.ini').read_text())
        self.assertEqual(move.relocate(self.source, self.dest, self.name), 0)
        self.assertEqual(originals, {p: p.read_bytes() for p in originals})

    def test_different_copy_is_rejected_without_partial_updates(self):
        file = self.dest / 'data/android-avd' / (self.name + '.avd/config.ini')
        file.write_bytes(file.read_bytes() + b'foreign=yes\n')
        originals = {p: p.read_bytes() for p in file.parent.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'unveränderte'):
            move.relocate(self.source, self.dest, self.name)
        self.assertEqual(originals, {p: p.read_bytes() for p in originals})

    def test_source_ownership_must_be_valid(self):
        (self.home / (self.name + '.avd/socialscraper-owned.json')).write_text('{}')
        with self.assertRaises(move.FinalError):
            move.relocate(self.source, self.dest, self.name)

    def test_prefix_siblings_are_rejected_instead_of_rewritten(self):
        with self.assertRaisesRegex(ValueError, 'Unbekannter'):
            move.rebased((str(self.source) + '_foreign/file').encode(), self.source, self.dest)

    def test_both_windows_separator_styles_are_rebased(self):
        source, dest = Path(r'C:\test\source'), Path(r'C:\test\destination')
        data = b'a=C:\\test\\source\\file\nb=C:/test/source/other\n'
        result = move.rebased(data, source, dest)
        self.assertEqual(result, b'a=C:/test/destination\\file\nb=C:/test/destination/other\n')

    def test_emulator_must_be_stopped(self):
        self.guard.stop()
        with patch.object(move.subprocess, 'run') as run:
            run.return_value.stdout = b'"qemu-system-x86_64.exe","123"\r\n'
            with self.assertRaisesRegex(ValueError, 'Emulatoren'):
                move.ensure_stopped()


class Independence(unittest.TestCase):
    def test_same_identity_across_ui_and_launcher(self):
        from socialscraper import branding, daemon, gui
        from socialscraper.ui.common import TITLE
        self.assertEqual(TITLE, branding.NAME)
        self.assertEqual(gui.APP_ID, 'Profilwerk.Desktop')
        self.assertEqual(daemon.AUTOSTART_NAME, 'Profilwerk Hintergrunddienst.lnk')
        source = (ROOT / 'launcher/Socialscraper.cs').read_text(encoding='utf8')
        self.assertIn('"' + gui.APP_ID + '"', source)
        self.assertIn('"Profilwerk"', source)
        self.assertNotIn('"Socialscraper.Desktop"', source)

    @unittest.skipUnless(os.name == 'nt', 'Windows named mutex')
    def test_mutex_separates_products_and_closes_duplicate_handle(self):
        from socialscraper import gui
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handles = []
        try:
            for product in ('Profilwerk', 'Socialscraper'):
                with patch.object(gui, 'APP_ID', product + '.Test.' + str(uuid.uuid4())):
                    self.assertTrue(gui._single_instance())
                    handle = gui._single_instance.handle
                    handles.append(handle)
                    self.assertFalse(gui._single_instance())
                    self.assertEqual(gui._single_instance.handle, handle)
        finally:
            for handle in handles: self.assertTrue(kernel.CloseHandle(handle))
            if hasattr(gui._single_instance, 'handle'): del gui._single_instance.handle

    def test_repair_rejects_other_interpreter(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'eigenen'):
                env.repair(folder)

    def test_unknown_wrapper_aborts_without_replacing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); scripts = root / '.venv/Scripts'
            scripts.mkdir(parents=True)
            python = scripts / 'python.exe'; python.write_bytes(b'fixture')
            unknown = scripts / 'unknown.exe'; unknown.write_bytes(b'original')
            from types import SimpleNamespace
            entry = SimpleNamespace(name='pip', group='console_scripts', value='pip._internal.cli.main:main')
            with patch.object(env.sys, 'prefix', str(root / '.venv')), patch.object(env.sys, 'executable', str(python)), patch.object(env.metadata, 'distributions', return_value=[SimpleNamespace(entry_points=[entry])]):
                with self.assertRaisesRegex(ValueError, 'Unbekannter'):
                    env.repair(root)
            self.assertEqual(unknown.read_bytes(), b'original')


if __name__ == '__main__': unittest.main()
