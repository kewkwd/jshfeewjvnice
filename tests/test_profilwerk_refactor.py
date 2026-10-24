"""Behaviour checks for shared helpers without browsers, accounts or real Tk windows."""
import json
import runpy
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
import tkinter as tk
import unittest
import zipfile
from unittest.mock import Mock, patch

from socialscraper.dom import first_selector
from socialscraper.jsonfiles import write_report
from socialscraper.ui.forms import run_async
from socialscraper.ui.widgets import run_in_thread


class ReportWrites(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'nested' / 'report.json'

    def test_complete_utf8_report_replaces_previous_report(self):
        write_report(self.path, {'value': 'Größe'}, prefix='test-')
        write_report(self.path, {'value': 2}, prefix='test-')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf8')), {'value': 2})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_serialization_failure_preserves_old_report_and_cleans_partial_file(self):
        for value in ({'bad': object()}, {'bad': float('nan')}):
            with self.subTest(value_type=type(value['bad']).__name__):
                write_report(self.path, {'old': True}, prefix='test-')
                with self.assertRaises((ValueError, TypeError)):
                    write_report(self.path, value, prefix='test-')
                self.assertEqual(json.loads(self.path.read_text()), {'old': True})
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_replace_failure_keeps_old_file_and_cleans_temporary(self):
        write_report(self.path, {'old': True}, prefix='test-')
        with patch('socialscraper.jsonfiles.os.replace', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                write_report(self.path, {'new': True}, prefix='test-')
        self.assertEqual(json.loads(self.path.read_text()), {'old': True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_stream_creation_failure_closes_descriptor_and_cleans_temporary(self):
        with patch('socialscraper.jsonfiles.os.fdopen', side_effect=OSError), \
                patch('socialscraper.jsonfiles.os.close', wraps=__import__('os').close) as close:
            with self.assertRaises(OSError):
                write_report(self.path, {}, prefix='test-')
        close.assert_called_once()
        self.assertEqual(list(self.path.parent.iterdir()), [])


class SourceArchives(unittest.TestCase):
    def setUp(self):
        script = Path(__file__).resolve().parents[1] / 'build' / 'archive_source.py'
        self.archive = runpy.run_path(str(script))['create_archive']

    def test_unfinished_sources_and_tests_are_kept_but_runtime_data_is_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); root = base / 'app'; root.mkdir()
            examples = {'socialscraper/future.py': 'raise NotImplementedError',
                        'tests/test_future.py': '# planned tests', 'unused/planned.py': '# planned feature',
                        'socialscraper/__pycache__/future.pyc': 'cache', 'data/account_secrets.json': 'PRIVATE_FIXTURE',
                        'settings.toml': 'PRIVATE_FIXTURE', 'tools/sdk/executable': 'runtime',
                        '.venv/package.py': 'runtime', 'registration_attempts.json': 'PRIVATE_FIXTURE'}
            for name, value in examples.items():
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)
            destination = base / 'sources.zip'
            count = self.archive(root, destination)
            with zipfile.ZipFile(destination) as bundle:
                self.assertEqual(set(bundle.namelist()), {'socialscraper/future.py', 'tests/test_future.py',
                    'unused/planned.py', 'QUELLEN-MANIFEST.json'})
                self.assertEqual(count, 3)
                self.assertEqual(bundle.read('socialscraper/future.py').decode(), 'raise NotImplementedError')
            for name, value in examples.items():
                self.assertEqual((root / name).read_text(), value)

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'app'; root.mkdir()
            destination = Path(folder) / 'sources.zip'; destination.write_bytes(b'keep')
            with self.assertRaises(FileExistsError):self.archive(root, destination)
            self.assertEqual(destination.read_bytes(), b'keep')

    def test_archive_inside_program_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):self.archive(root, root / 'sources.zip')
            self.assertEqual(list(root.iterdir()), [])


class SelectorContracts(unittest.TestCase):
    def test_invisible_field_is_accepted_only_by_existence_contract(self):
        hidden = SimpleNamespace(is_visible=lambda: False)
        shown = SimpleNamespace(is_visible=lambda: True)
        page = SimpleNamespace(query_selector=lambda selector: {'hidden': hidden, 'shown': shown}.get(selector))
        from socialscraper.mailanbieter.gmail import _erstes as gmail
        from socialscraper.mailanbieter.unitedinternet import _erstes as united
        from socialscraper.mailanbieter.outlook import _erstes as outlook
        from socialscraper.warmup.sitzung import _erstes as session
        for find in (gmail, united):
            self.assertEqual(find(page, ['missing', 'hidden', 'shown']), 'shown')
        for find in (outlook, session):
            self.assertEqual(find(page, ['missing', 'hidden', 'shown']), 'hidden')

    def test_replaced_field_does_not_prevent_trying_next_selector(self):
        page = SimpleNamespace(query_selector=Mock(side_effect=[RuntimeError(), None, object()]))
        self.assertEqual(first_selector(page, ['old', 'missing', 'current']), 'current')
        self.assertEqual(page.query_selector.call_count, 3)

    def test_all_missing_fields_return_none(self):
        page = SimpleNamespace(query_selector=lambda selector: None)
        self.assertIsNone(first_selector(page, ['missing'], visible=True))


class IdentityBinding(unittest.TestCase):
    def test_both_entry_points_keep_pool_profile_root_and_account_binding(self):
        from socialscraper.mailanbieter import _identity_manager as mailbox
        from socialscraper.signup import _identity_manager as signup
        from socialscraper.net.accountproxy import Resolver
        from socialscraper.net.proxy import ProxyPool
        from socialscraper.net import proxymode
        with tempfile.TemporaryDirectory() as folder:
            cfg = SimpleNamespace(profiles_dir=Path(folder) / 'profiles', db_path=Path(folder) / 'account.db')
            for anonymous in (False, True):
                for create in (mailbox, signup):
                    with self.subTest(anonymous=anonymous, entry=create.__module__):
                        pool = ProxyPool([])
                        with patch.object(proxymode, 'make_pool', return_value=pool), \
                                patch.object(proxymode, 'is_anonymous', return_value=anonymous):
                            manager = create(cfg)
                        self.assertIs(manager.pool, pool)
                        self.assertEqual(manager.base, Path(folder) / 'identities')
                        if anonymous:
                            self.assertIsNone(manager.account_proxies)
                        else:
                            self.assertIsInstance(manager.account_proxies, Resolver)
                            self.assertEqual(manager.account_proxies.db_path, str(cfg.db_path))
            self.assertEqual(list(Path(folder).iterdir()), [])


class Value:
    def __init__(self, value):self.value = value
    def get(self):return self.value
    def set(self, value):self.value = value


class GroupChoices(unittest.TestCase):
    def actions(self):
        from socialscraper.ui.channel_dialog import ChannelDialog
        from socialscraper.ui.target_dialog import TargetDialog
        return (ChannelDialog._group_changed, TargetDialog._group_changed)

    def dialog(self):
        return SimpleNamespace(group_var=Value('Neue Gruppe …'), groups=[], group_cb=Mock(), db_path='unused')

    def test_existing_selection_does_not_open_a_dialog(self):
        for action in self.actions():
            dialog = self.dialog(); dialog.group_var.set('Vorhanden')
            with patch('tkinter.simpledialog.askstring') as ask:
                action(dialog)
            ask.assert_not_called()
            self.assertEqual(dialog.group_var.get(), 'Vorhanden')

    def test_cancelled_and_empty_names_keep_groups_unchanged(self):
        for action in self.actions():
            for value in (None, '', '   '):
                dialog = self.dialog()
                with patch('tkinter.simpledialog.askstring', return_value=value), \
                        patch('socialscraper.autologic.create_group') as create:
                    action(dialog)
                create.assert_not_called()
                self.assertEqual(dialog.groups, [])
                self.assertEqual(dialog.group_var.get(), '(keine Gruppe)')

    def test_failed_create_resets_selection_without_adding_a_group(self):
        for action in self.actions():
            dialog = self.dialog()
            with patch('tkinter.simpledialog.askstring', return_value='Belegt'), \
                    patch('socialscraper.autologic.create_group', side_effect=ValueError('Belegt')), \
                    patch('tkinter.messagebox.showwarning') as warn:
                action(dialog)
            warn.assert_called_once()
            self.assertEqual(dialog.groups, [])
            self.assertEqual(dialog.group_var.get(), '(keine Gruppe)')

    def test_success_updates_both_selection_and_menu_once(self):
        for action in self.actions():
            dialog = self.dialog(); group = {'id': 7, 'name': 'Neu'}
            with patch('tkinter.simpledialog.askstring', return_value='Neu'), \
                    patch('socialscraper.autologic.create_group', return_value=group):
                action(dialog)
            self.assertEqual(dialog.groups, [group])
            self.assertEqual(dialog.group_var.get(), 'Neu')
            dialog.group_cb.configure.assert_called_once_with(values=['(keine Gruppe)', 'Neu', 'Neue Gruppe …'])


class WorkerContracts(unittest.TestCase):
    def launch(self, api, work, *, exists=True, daemon=True):
        callbacks = queue.Queue()
        widget = SimpleNamespace(winfo_exists=lambda: exists)
        results = []
        schedule = lambda widget, interval, callback: callbacks.put(callback)
        module = 'socialscraper.ui.forms' if api is run_async else 'socialscraper.ui.widgets'
        with patch(module + '.track_after', side_effect=schedule):
            api(widget, work, lambda result, error: results.append((result, error, threading.get_ident())), daemon=daemon)
        return callbacks, results

    def pump(self, callbacks, results):
        end = time.monotonic() + 2
        while not results and time.monotonic() < end:
            callbacks.get(timeout=2)()
            time.sleep(0.001)
        self.assertTrue(results)

    def test_results_return_on_owner_thread_and_daemon_false_is_preserved(self):
        owner = threading.get_ident()
        for api in (run_async, run_in_thread):
            callbacks, results = self.launch(api, lambda: (threading.get_ident(), threading.current_thread().daemon), daemon=False)
            self.pump(callbacks, results)
            (worker, daemon), error, returned_on = results[0]
            self.assertNotEqual(worker, owner)
            self.assertFalse(daemon)
            self.assertIsNone(error)
            self.assertEqual(returned_on, owner)
            self.assertTrue(callbacks.empty())

    def test_worker_errors_including_system_exit_do_not_leave_an_endless_poller(self):
        for api in (run_async, run_in_thread):
            for error in (ValueError('fixture'), SystemExit(2)):
                def fail():raise error
                callbacks, results = self.launch(api, fail)
                self.pump(callbacks, results)
                self.assertIs(results[0][1], error)
                self.assertTrue(callbacks.empty())

    def test_closed_widget_receives_no_callback_but_file_worker_finishes(self):
        for api in (run_async, run_in_thread):
            completed = threading.Event()
            callbacks, results = self.launch(api, completed.set, exists=False, daemon=False)
            callbacks.get(timeout=2)()
            self.assertTrue(completed.wait(2))
            self.assertEqual(results, [])
            self.assertTrue(callbacks.empty())

    def test_already_destroyed_interpreter_does_not_abort_pending_file_work(self):
        for api in (run_async, run_in_thread):
            completed = threading.Event()
            module = 'socialscraper.ui.forms' if api is run_async else 'socialscraper.ui.widgets'
            with patch(module + '.track_after', side_effect=tk.TclError('fixture')):
                api(SimpleNamespace(), completed.set, Mock(), daemon=False)
            self.assertTrue(completed.wait(2))
