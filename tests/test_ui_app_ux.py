"""Hauptfenster: Tab-Reihenfolge, Tastenkürzel, gemerkte Fenstergröße und Tabs, Protokoll ein-/ausklappen, Probleme-Zähler,
Meldung bei fertigem Auftrag. Echtes (verborgenes) Hauptfenster, alles in einem Wegwerf-Ordner."""
import datetime
import gc
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import guicore as core, repo
from socialscraper.store import Store

PROJECT = pathlib.Path(__file__).resolve().parent.parent

try:
    import tkinter
except Exception:  # pragma: no cover
    tkinter = None


@unittest.skipUnless(os.name == 'nt' and tkinter is not None, 'Tk-Fenster nur unter Windows')
class AppUxTests(unittest.TestCase):
    def setUp(self):
        from socialscraper.ui import common
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'data').mkdir()
        shutil.copy(PROJECT / 'settings.toml', self.root / 'settings.toml')
        os.environ.pop('SS_PROXY_MODE', None)
        for p in (patch.dict(os.environ, {'SS_DB_PATH': str(self.root / 'data' / 'scrape.db')}), patch.object(core, 'ROOT', self.root),
                  patch.object(common, 'ROOT', self.root), patch.object(common, 'GUI_LOG', self.root / 'data' / 'gui.log')):
            p.start()
            self.addCleanup(p.stop)
        self.state_path = self.root / 'data' / 'ui_state.json'
        self.apps = []
        self.addCleanup(self._close_all)
        from socialscraper.ui import app as appmod
        self.appmod = appmod

    def _close_all(self):
        for app in self.apps:
            try:
                app.destroy()
            except tkinter.TclError:
                pass
        self.apps = []
        gc.collect()

    def make(self):
        try:
            app = self.appmod.App()
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        app.withdraw()
        self.apps.append(app)
        return app

    def make_recording(self):
        """App starten und festhalten, welche Fenstergröße beim Start gesetzt wurde (ein verborgenes Fenster nennt sie nicht)."""
        calls = []
        real = tkinter.Tk.geometry

        def spy(widget, value=None):
            if value is not None:
                calls.append(value)
            return real(widget, value)
        with patch.object(self.appmod.App, 'geometry', spy):
            app = self.make()
        return app, calls

    def pump(self, app, until, timeout=10):
        import time
        end = time.time() + timeout
        while not until() and time.time() < end:
            app.update()
            time.sleep(0.02)
        return until()

    def test_tab_order_and_start_page(self):
        app = self.make()
        self.assertEqual(list(app.tabs), ['Übersicht', 'Abrufen', 'Konten', 'Gespeicherte Daten', 'Automatik', 'Einstellungen', 'Aktionen', 'Aufwärmen', 'Postfächer', 'Streuung', 'Hilfe', 'Arbeitsregeln', 'Profile'])
        self.assertEqual(app._current_tab_title(), 'Übersicht')
        self.assertEqual([t for t, _c in self.appmod.TABS], list(app.tabs))

    def test_shortcuts_are_bound(self):
        app = self.make()
        for number in range(1, 8):
            self.assertTrue(app.tk.eval(f'bind {app} <Control-Key-{number}>'), number)
        self.assertTrue(app.tk.eval(f'bind {app} <F1>'))
        for shortcut in ('<Control-Key-8>', '<Control-Key-9>', '<Control-F2>', '<Control-F3>'):
            self.assertTrue(app.tk.eval(f'bind {app} {shortcut}'), shortcut)
        app.select_tab('Hilfe')
        self.assertEqual(app._current_tab_title(), 'Hilfe')

    def test_window_size_position_and_tab_are_remembered(self):
        app = self.make()
        app.deiconify()
        app.geometry('1100x760+40+30')
        app.update()
        app.select_tab('Konten')
        app.update()
        app._close()
        self.apps.remove(app)
        data = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual((data['geometry']['w'], data['geometry']['h']), (1100, 760))
        self.assertEqual(data['tab'], 'Konten')
        again, calls = self.make_recording()
        self.assertEqual(calls[0], '1100x760+40+30')
        self.assertEqual(again._current_tab_title(), 'Konten')

    def test_destroy_waits_for_background_helper_to_exit(self):
        from socialscraper.ui.forms import run_async
        app = self.make()
        started, release = threading.Event(), threading.Event()
        run_async(app, lambda: (started.set(), release.wait(2)), lambda _value, _error: None)
        deadline = time.monotonic() + 2
        while not started.is_set() and time.monotonic() < deadline:
            app.update(); time.sleep(.01)
        self.assertTrue(started.is_set())
        app.destroy()
        self.assertFalse(app._destroyed)
        release.set()
        deadline = time.monotonic() + 3
        while not app._destroyed and time.monotonic() < deadline:
            app.update(); time.sleep(.01)
        self.assertTrue(app._destroyed)
        self.apps.remove(app)

    def test_help_tab_is_not_restored_as_start_page(self):
        self.state_path.write_text(json.dumps({'tab': 'Hilfe'}), encoding='utf-8')
        self.assertEqual(self.make()._current_tab_title(), 'Übersicht')

    def test_broken_state_file_means_default_start(self):
        for content in ('{kaputt', '[]', '', '{"geometry": {"w": "x"}, "tab": 5}'):
            self.state_path.write_text(content, encoding='utf-8')
            app, calls = self.make_recording()
            self.assertEqual(app._current_tab_title(), 'Übersicht', content)
            self.assertTrue(calls[0].startswith('1240x'), calls)
            app.destroy()
            self.apps.remove(app)

    def test_off_screen_position_is_corrected(self):
        self.state_path.write_text(json.dumps({'geometry': {'w': 1000, 'h': 700, 'x': 90000, 'y': 90000}}), encoding='utf-8')
        app, calls = self.make_recording()
        self.assertEqual(calls[0], '1000x700')               # ohne Ort: Windows entscheidet, das Fenster bleibt auf dem Bildschirm

    def test_too_small_remembered_size_is_raised_to_the_minimum(self):
        self.state_path.write_text(json.dumps({'geometry': {'w': 200, 'h': 100, 'x': 10, 'y': 10}}), encoding='utf-8')
        app, calls = self.make_recording()
        self.assertEqual(calls[0], '860x620+10+10')

    def test_state_is_not_written_just_by_opening(self):
        app = self.make()
        app.update()
        self.assertFalse(self.state_path.exists())

    def test_log_follows_window_height_until_the_user_decides(self):
        app = self.make()
        with patch.object(app, 'winfo_height', return_value=900):
            app._auto_log()
        self.assertFalse(app._log_open)
        self.assertFalse(app.logf.winfo_manager())
        with patch.object(app, 'winfo_height', return_value=620):
            app._auto_log()
        self.assertFalse(app._log_open)
        self.assertFalse(app.logf.winfo_manager())
        self.assertEqual(app.log_btn['text'], 'Details ▾')
        app.toggle_log()                                    # Marc klappt es selbst auf
        self.assertTrue(app._log_open)
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['log_open'], True)
        with patch.object(app, 'winfo_height', return_value=620):
            app._auto_log()
        self.assertTrue(app._log_open)                      # seine Wahl bleibt

    def test_log_choice_is_remembered_and_text_still_collects_when_hidden(self):
        app = self.make()
        app.toggle_log()
        app.toggle_log()
        self.assertFalse(app._log_open)
        app.log('Eine Zeile', 'OK')
        self.assertIn('Eine Zeile', app.log_text.get('1.0', 'end'))
        app.destroy()
        self.apps.remove(app)
        again = self.make()
        self.assertFalse(again._log_open)
        self.assertEqual(again.log_btn['text'], 'Details ▾')

    def test_show_log_opens_it(self):
        app = self.make()
        self.assertFalse(app._log_open)
        app.show_log()
        self.assertTrue(app._log_open)

    def test_problem_button_text(self):
        app = self.make()
        self.assertEqual(app.problems_btn['text'], 'Probleme')
        app.set_problem_count(3)
        self.assertEqual(app.problems_btn['text'], '⚠ Probleme (3)')
        app.set_problem_count(0)
        self.assertEqual(app.problems_btn['text'], 'Probleme')

    def seed_failed_run(self):
        store = Store(str(self.root / 'data' / 'scrape.db'))
        t = repo.add_target(store.db, 'profile', 'reddit', 'abc', interval_min=60, max_comments=0)
        rid = repo.start_run(store.db, t['id'], 'profile reddit abc', 'daemon')
        repo.finish_run(store.db, rid, 'failed', error='Menschentest (CAPTCHA) erkannt')
        store.db.close()

    def test_problem_count_comes_from_the_database_and_resets_when_opened(self):
        self.seed_failed_run()
        app = self.make()
        app.update_problem_count()
        self.assertTrue(self.pump(app, lambda: app.problem_count == 1), app.problem_count)
        self.assertEqual(app.problems_btn['text'], '⚠ Probleme (1)')
        window = app.show_problems()
        self.assertTrue(self.pump(app, lambda: 'Menschentest' in ' '.join(
            str(w.cget('text')) for w in self._walk(window) if hasattr(w, 'cget') and 'text' in w.keys())))
        self.assertTrue(self.pump(app, lambda: app.problem_count == 0))
        self.assertEqual(app.problems_btn['text'], 'Probleme')
        self.assertIs(app.show_problems(), window)         # kein zweites Fenster
        window.close()
        self.assertIsNone(app._problems_window)

    @staticmethod
    def _walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from AppUxTests._walk(child)

    def test_finished_job_in_background_flashes_and_says_so(self):
        app = self.make()
        from socialscraper.ui import common
        with patch.object(common, 'window_in_front', return_value=False), patch.object(common, 'flash_window') as flash:
            app._notify_finished([('YouTube: 1 Link abrufen', 0, [])], False)
            self.assertIn('Fertig: YouTube: 1 Link abrufen.', app.status['text'])
            flash.assert_called_once_with(app)
            flash.reset_mock()
            app._notify_finished([('A', 0, []), ('B', 1, [])], False)
            self.assertIn('Nicht alles hat geklappt: B', app.status['text'])
            self.assertIn('Probleme', app.status['text'])
            flash.assert_called_once()

    def test_finished_job_in_front_or_cancelled_or_empty_stays_quiet(self):
        app = self.make()
        from socialscraper.ui import common
        app.set_status('vorher')
        with patch.object(common, 'flash_window') as flash:
            with patch.object(common, 'window_in_front', return_value=True):
                app._notify_finished([('A', 0, [])], False)
            with patch.object(common, 'window_in_front', return_value=False):
                app._notify_finished([('A', 0, [])], True)
                app._notify_finished([], False)
        flash.assert_not_called()
        self.assertEqual(app.status['text'], 'vorher')

    def test_flash_window_never_raises(self):
        from socialscraper.ui import common
        app = self.make()
        self.assertIn(common.flash_window(app), (True, False))
        with patch('ctypes.windll.user32.FlashWindowEx', side_effect=OSError('x')):
            self.assertFalse(common.flash_window(app))

    def test_window_in_front_for_a_withdrawn_window(self):
        from socialscraper.ui import common
        app = self.make()
        self.assertFalse(common.window_in_front(app))

    def test_run_action_goto_and_unknown(self):
        app = self.make()
        self.assertTrue(app.run_action({'id': 'goto', 'tab': 'Konten'}))
        self.assertEqual(app._current_tab_title(), 'Konten')
        self.assertTrue(app.run_action({'id': 'goto_help'}))
        self.assertEqual(app._current_tab_title(), 'Hilfe')
        self.assertFalse(app.run_action({'id': 'quatsch'}))

    def test_tab_change_is_noted_for_next_start(self):
        app = self.make()
        app.select_tab('Automatik')
        app.update()
        self.assertEqual(app.ui_state.get('tab'), 'Automatik')

    def test_goto_data_tab_marks_checklist_step(self):
        app = self.make()
        app.run_action({'id': 'goto_data'})
        self.assertTrue(app.ui_state.get('home', 'viewed_data'))

    def test_real_settings_and_credentials_are_never_touched(self):
        real = PROJECT / 'settings.toml'
        before = real.read_bytes()
        app = self.make()
        app.run_action({'id': 'skip_account'})
        app._close()
        self.apps.remove(app)
        self.assertEqual(real.read_bytes(), before)
        self.assertFalse((self.root / 'data' / 'credentials.json').exists())


if __name__ == '__main__':
    unittest.main()
