"""Tests für die Proxy-Oberfläche: Logik ohne Tk (proxylogic, Prüfen, Speichern) und Fenstertests mit Mocks.

Kein echtes Netz, kein echtes Tor, keine echten Nutzerdaten: alles läuft in Wegwerf-Ordnern, describe/ensure_ready/
freeproxy.refresh/tor.* sind ersetzt.
"""
import gc
import os
import pathlib
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import formlogic as fl
from socialscraper import guicore as core
from socialscraper import proxylogic
from socialscraper.net import proxymode


class LogicTests(unittest.TestCase):
    def test_modes_and_texts(self):
        self.assertEqual([c[0] for c in proxylogic.MODE_CARDS], ['aus', 'liste', 'frei', 'tor'])
        self.assertEqual({c[0] for c in proxylogic.MODE_CARDS if c[3]}, {'frei', 'tor'})
        self.assertIn('Konten, Anmeldung, Instagram, X und TikTok-Kommentare sind in diesem Modus gesperrt', proxylogic.WARN_TEXT)
        self.assertIn('aus Sicherheitsgründen', proxylogic.WARN_TEXT)
        self.assertIn('sie sehen, welche Seiten du aufrufst', proxylogic.FREE_HINT)

    def test_validate_mode(self):
        for mode in ('aus', 'liste', 'frei', 'tor'):
            self.assertEqual(proxylogic.validate_mode(mode), (mode, None))
        for bad in ('', 'TOR', 'vpn', None, 3):
            value, error = proxylogic.validate_mode(bad)
            self.assertIsNone(value)
            self.assertTrue(error)

    def test_banner_only_in_anonymous_modes(self):
        self.assertEqual(proxylogic.banner_text('aus'), '')
        self.assertEqual(proxylogic.banner_text('liste'), '')
        self.assertEqual(proxylogic.banner_text('tor'), 'Proxy-Modus ‚Tor‘ aktiv: Konten sind gesperrt.')
        self.assertIn('Kostenlose Proxys', proxylogic.banner_text('frei'))

    def test_fetch_problem_uses_the_rule_from_proxymode(self):
        self.assertIsNone(proxylogic.fetch_problem('aus', 'instagram', 50))
        self.assertIsNone(proxylogic.fetch_problem('liste', 'tiktok', 50))
        self.assertIsNone(proxylogic.fetch_problem('tor', 'youtube', 500))
        self.assertIsNone(proxylogic.fetch_problem('frei', 'tiktok', 0))
        self.assertIn(proxymode.NO_ACCOUNTS, proxylogic.fetch_problem('tor', 'instagram', 0))
        text = proxylogic.fetch_problem('frei', 'tiktok', 20)
        self.assertIn('TikTok-Kommentare', text)
        self.assertNotIn('--max-comments', text)
        self.assertIn('Hashtags', proxylogic.fetch_problem('tor', 'tiktok', 0, 'hashtag'))
        self.assertIn('Kommentare', proxylogic.fetch_problem('tor', 'tiktok', 5, 'profile'))

    def test_target_problem(self):
        self.assertIsNone(proxylogic.target_problem('tor', {'kind': 'video', 'platform': 'youtube', 'max_comments': 100}))
        self.assertTrue(proxylogic.target_problem('tor', {'kind': 'hashtag', 'platform': 'tiktok', 'max_comments': 0}))
        self.assertTrue(proxylogic.target_problem('frei', {'kind': 'video', 'platform': 'instagram', 'max_comments': 0}))
        self.assertTrue(proxylogic.target_problem('frei', {'kind': 'video', 'platform': 'tiktok', 'max_comments': 10}))
        self.assertIsNone(proxylogic.target_problem('aus', {'kind': 'hashtag', 'platform': 'tiktok', 'max_comments': 10}))

    def test_status_colors(self):
        self.assertEqual(proxylogic.status_kind('tor', True), 'ok')
        self.assertEqual(proxylogic.status_kind('tor', False), 'err')
        self.assertEqual(proxylogic.status_kind('frei', False), 'warn')
        self.assertEqual(proxylogic.status_kind('liste', False), 'warn')

    def test_free_summary(self):
        self.assertIn('noch nie', proxylogic.free_summary({'alter_s': None}))
        text = proxylogic.free_summary({'alter_s': 125, 'vorhanden': 3, 'gueltig': True,
                                        'proxys': [{'ms': 900}, {'ms': 310}, {'ms': None}]})
        self.assertIn('3 funktionierende Proxys', text)
        self.assertIn('310 ms', text)
        self.assertIn('2 Minuten', text)
        self.assertIn('abgelaufen', proxylogic.free_summary({'alter_s': 4000, 'vorhanden': 1, 'gueltig': False, 'proxys': []}))

    def test_refresh_summary(self):
        ok, text = proxylogic.refresh_summary({'kandidaten': 120, 'funktionierend': 4, 'zertifikatsfehler': 2,
                                               'schnellste': [{'proxy': 'x', 'ms': 250}]})
        self.assertTrue(ok)
        self.assertIn('120 gefunden, 4 funktionieren, 2 wegen Zertifikatsfehler verworfen', text)
        ok, text = proxylogic.refresh_summary({'kandidaten': 10, 'funktionierend': 0, 'zertifikatsfehler': 0, 'schnellste': []})
        self.assertFalse(ok)
        self.assertIn('nicht abgerufen', text)

    def test_stop_tor_is_honest_about_foreign_tor(self):
        ok, text = proxylogic.stop_tor_message(False, 'Es gibt kein von Socialscraper gestartetes Tor (keine tor.pid). Ein anderes Tor wird nicht beendet.')
        self.assertFalse(ok)
        self.assertEqual(text, proxylogic.TOR_NOT_OURS)
        self.assertIn('extern', text)
        self.assertEqual(proxylogic.stop_tor_message(True, 'Tor beendet.'), (True, 'Tor beendet.'))

    def test_tor_connection_text(self):
        self.assertEqual(proxylogic.tor_connection_text({'ok': True, 'port': 9050, 'message': ''})[1],
                         'Du bist über Tor verbunden (Anschluss 9050).')
        ok, text = proxylogic.tor_connection_text({'ok': False, 'port': None, 'message': 'x'})
        self.assertFalse(ok)
        self.assertIn('Nicht verbunden', text)

    def test_current_mode_reads_settings_and_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'SS_DB_PATH': str(pathlib.Path(tmp) / 'data' / 'scrape.db')}):
                os.environ.pop('SS_PROXY_MODE', None)
                self.assertEqual(proxylogic.current_mode(tmp), 'aus')          # keine Datei
                (pathlib.Path(tmp) / 'settings.toml').write_text('proxy_mode = "tor"\n', encoding='utf-8')
                self.assertEqual(proxylogic.current_mode(tmp), 'tor')
                (pathlib.Path(tmp) / 'settings.toml').write_text('proxy_mode = [[[\n', encoding='utf-8')
                self.assertEqual(proxylogic.current_mode(tmp), 'aus')          # kaputt -> sicher 'aus'


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        env = patch.dict(os.environ, {'SS_DB_PATH': str(self.root / 'data' / 'scrape.db')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SS_PROXY_MODE', None)
        (self.root / 'settings.toml').write_text(fl.DEFAULT_SETTINGS_TEXT, encoding='utf-8', newline='')

    def test_default_settings_have_mode_aus(self):
        self.assertEqual(core.read_settings(self.root).proxy_mode, 'aus')
        self.assertEqual(fl.default_raw()['proxy_mode'], 'aus')

    def test_write_each_mode_and_roundtrip(self):
        for mode in ('tor', 'frei', 'liste', 'aus'):
            core.write_settings({'proxy_mode': mode}, self.root)
            self.assertEqual(tomllib.loads((self.root / 'settings.toml').read_text(encoding='utf-8'))['proxy_mode'], mode)
            self.assertEqual(core.read_settings(self.root).proxy_mode, mode)

    def test_write_invalid_mode_writes_nothing(self):
        before = (self.root / 'settings.toml').read_bytes()
        for bad in ('vpn', '', None, 1, True, ['tor']):
            with self.assertRaises(ValueError):
                core.write_settings({'proxy_mode': bad}, self.root)
        self.assertEqual((self.root / 'settings.toml').read_bytes(), before)

    def test_missing_key_is_added_and_comments_stay(self):
        text = '# oben\nheadless = true  # still\n\n[sphere_presets]\ntiktok = ""\n'
        (self.root / 'settings.toml').write_text(text, encoding='utf-8', newline='')
        core.write_settings({'proxy_mode': 'frei'}, self.root)
        out = (self.root / 'settings.toml').read_text(encoding='utf-8')
        self.assertIn('# oben\n', out)
        self.assertIn('headless = true  # still\n', out)
        self.assertIn('proxy_mode = "frei"\n', out)
        self.assertIn('[sphere_presets]\ntiktok = ""\n', out)

    def test_form_validation_and_change_tracking(self):
        raw = fl.default_raw()
        raw['proxy_mode'] = 'tor'
        settings, _integration, errors = fl.validate_form(raw)
        self.assertFalse(errors)
        self.assertEqual(settings['proxy_mode'], 'tor')
        raw['proxy_mode'] = 'vpn'
        self.assertIn('proxy_mode', fl.validate_form(raw)[2])
        a, b = fl.default_raw(), fl.default_raw()
        b['proxy_mode'] = 'frei'
        self.assertEqual(fl.changed_keys(a, b), ['proxy_mode'])
        self.assertEqual(fl.changed_sections(a, b), {'proxys'})
        self.assertEqual(fl.describe_changes(['proxy_mode']), 'Proxys')

    def test_settings_to_raw_reads_mode(self):
        core.write_settings({'proxy_mode': 'frei'}, self.root)
        self.assertEqual(fl.settings_to_raw(core.read_settings(self.root))['proxy_mode'], 'frei')


# ---------- Fenstertests ----------

try:
    import tkinter
    from tkinter import messagebox
    from ui_fake import FakeApp
    from socialscraper.ui.tab_settings import SettingsTab
except Exception:  # pragma: no cover
    FakeApp = None


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class ProxyTabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        env = patch.dict(os.environ, {'SS_DB_PATH': str(self.root / 'data' / 'scrape.db')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SS_PROXY_MODE', None)
        # Keine echten Aufrufe: alles, was Netz, Tor oder Dateien der Proxy-Listen anfasst, ist ersetzt.
        self.calls = []

        def fake_describe(cfg, root=None):
            self.calls.append(('describe', cfg.proxy_mode))
            texts = {'aus': ('Kein Proxy.', True), 'liste': ('Liste.', bool(cfg.proxies)), 'frei': ('Frei.', False), 'tor': ('Tor.', True)}
            text, ok = texts[cfg.proxy_mode]
            return cfg.proxy_mode, text, ok
        self.patches = {
            'describe': patch.object(proxymode, 'describe', side_effect=fake_describe),
            'ready': patch.object(proxymode, 'ensure_ready', side_effect=lambda cfg, root=None: (self.calls.append(('ready', cfg.proxy_mode)) or (True, 'Bereit.'))),
            'refresh': patch('socialscraper.net.freeproxy.refresh', side_effect=self._fake_refresh),
            'status': patch('socialscraper.net.freeproxy.status', return_value={'vorhanden': 2, 'alter_s': 60, 'gueltig': True, 'gesperrt': 0,
                                                                                   'proxys': [{'proxy': 'a', 'ms': 400, 'ok': 0, 'fail': 0}]}),
            'tor_start': patch('socialscraper.net.tor.ensure_running', side_effect=lambda root, **k: (self.calls.append(('tor_start',)) or {'ok': True, 'port': 9050, 'started': True, 'message': 'Tor gestartet.'})),
            'tor_stop': patch('socialscraper.net.tor.stop', side_effect=lambda root, **k: (self.calls.append(('tor_stop',)) or (False, 'Es gibt kein von Socialscraper gestartetes Tor (keine tor.pid). Ein anderes Tor wird nicht beendet.'))),
            'tor_find': patch('socialscraper.net.tor.find', side_effect=lambda **k: (self.calls.append(('tor_find',)) or {'ok': True, 'port': 9050, 'message': 'x'})),
        }
        self.mocks = {}
        for name, p in self.patches.items():
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self._close)
        for name in ('showinfo', 'showwarning', 'showerror', 'askyesno'):
            p = patch(f'tkinter.messagebox.{name}', **({'return_value': True} if name == 'askyesno' else {}))
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        self.tab = self.app.add('Einstellungen', SettingsTab)
        self.app.update()
        self.settle()

    def _fake_refresh(self, root=None, **kwargs):
        self.calls.append(('refresh',))
        if kwargs.get('progress'):
            kwargs['progress']('teste 20 Kandidaten')
        return {'kandidaten': 80, 'getestet': 80, 'funktionierend': 5, 'zertifikatsfehler': 3,
                'schnellste': [{'proxy': 'http://1.2.3.4:80', 'ms': 210}]}

    def _close(self):
        self.settle()
        self.app.destroy()
        self.app = self.tab = None
        gc.collect()

    def settle(self):
        t = self.tab
        self.app.pump(lambda: not t._proxy_busy, 5)
        self.app.pump(lambda: False, 0.3)
        self.app.update()

    def choose(self, mode):
        self.tab.vars['proxy_mode'].set(mode)
        self.app.update()
        self.settle()

    def test_mode_choice_shows_matching_parts(self):
        t = self.tab
        self.tab.show_section('proxys')
        self.app.update()
        view = lambda: tuple(bool(w.winfo_manager()) for w in (t.warn_box, t.list_panel, t.tor_panel, t.free_panel))
        self.assertEqual(view(), (False, False, False, False))                # aus
        self.choose('liste')
        self.assertEqual(view(), (False, True, False, False))
        self.choose('frei')
        self.assertEqual(view(), (True, False, False, True))
        self.choose('tor')
        self.assertEqual(view(), (True, False, True, False))
        self.assertIn('Konten, Anmeldung, Instagram, X und TikTok-Kommentare sind in diesem Modus gesperrt', t.warn_label['text'])

    def test_status_comes_from_describe_in_background_with_color(self):
        self.choose('tor')
        self.assertIn(('describe', 'tor'), self.calls)
        self.assertIn('Tor.', self.tab.status_label['text'])
        self.assertEqual(str(self.tab.status_label['foreground']), proxylogic_color('ok'))
        self.choose('frei')
        self.assertEqual(str(self.tab.status_label['foreground']), proxylogic_color('warn'))
        self.assertIn('2 funktionierende Proxys', self.tab.free_info['text'])
        self.assertIn('400 ms', self.tab.free_info['text'])
        self.assertIn('Noch nicht gespeichert', self.tab.status_label['text'])
        self.choose('aus')
        self.assertNotIn('Noch nicht gespeichert', self.tab.status_label['text'])

    def test_describe_is_not_called_in_the_window_thread(self):
        import threading
        main = threading.get_ident()
        seen = []
        self.mocks['describe'].side_effect = lambda cfg, root=None: (seen.append(threading.get_ident()) or (cfg.proxy_mode, 'x', True))
        self.choose('tor')
        self.assertTrue(seen)
        self.assertNotIn(main, seen)

    def test_save_and_reset_with_mode(self):
        t = self.tab
        self.choose('tor')
        self.assertTrue(t.dirty)
        self.assertEqual(t.state_label['text'], '● Ungespeicherte Änderungen')
        self.assertIn('●', t.nav.get(1))                                        # Bereich „Proxys“ markiert
        t.reset()
        self.assertFalse(t.dirty)
        self.assertEqual(t.vars['proxy_mode'].get(), 'aus')
        self.choose('frei')
        self.assertTrue(t.save())
        self.assertEqual(core.read_settings(self.root).proxy_mode, 'frei')
        self.assertFalse(t.dirty)
        self.assertEqual(t.baseline['proxy_mode'], 'frei')
        self.settle()
        self.assertNotIn('Noch nicht gespeichert', t.status_label['text'])

    def test_check_button_runs_ensure_ready_in_background(self):
        t = self.tab
        self.choose('tor')
        t.proxy_check()
        self.assertTrue(t._proxy_busy)
        self.assertEqual(str(t.check_btn['state']), 'disabled')
        self.settle()
        self.assertIn(('ready', 'tor'), self.calls)
        self.assertIn('Bereit.', t.check_result['text'])
        self.assertEqual(str(t.check_btn['state']), 'normal')

    def test_check_failure_is_red(self):
        self.mocks['ready'].side_effect = lambda cfg, root=None: (False, 'Kein Tor.')
        self.choose('tor')
        self.tab.proxy_check()
        self.settle()
        self.assertIn('✖', self.tab.check_result['text'])
        self.assertEqual(str(self.tab.check_result['foreground']), proxylogic_color('err'))

    def test_tor_buttons(self):
        t = self.tab
        self.choose('tor')
        t.tor_start(); self.settle()
        self.assertIn(('tor_start',), self.calls)
        self.assertIn('Tor gestartet.', t.tor_result['text'])
        t.tor_stop(); self.settle()
        self.assertIn(('tor_stop',), self.calls)
        self.assertIn('extern', t.tor_result['text'])
        self.assertIn('nicht von diesem Programm', t.tor_result['text'])
        t.tor_test(); self.settle()
        self.assertIn('Du bist über Tor verbunden', t.tor_result['text'])
        self.mocks['tor_find'].side_effect = lambda **k: {'ok': False, 'port': None, 'message': 'x'}
        t.tor_test(); self.settle()
        self.assertIn('Nicht verbunden', t.tor_result['text'])

    def test_free_refresh_shows_progress_and_result(self):
        t = self.tab
        self.choose('frei')
        t.free_refresh()
        self.assertTrue(t._proxy_busy)
        self.assertIn('bis zu einer Minute', t.proxy_note['text'])
        self.assertTrue(t.proxy_progress.winfo_manager())
        self.settle()
        self.assertIn(('refresh',), self.calls)
        self.assertIn('80 gefunden, 5 funktionieren, 3 wegen Zertifikatsfehler verworfen', t.free_result['text'])
        self.assertFalse(t.proxy_progress.winfo_manager())
        self.assertEqual(t.proxy_note['text'], '')

    def test_free_refresh_error_is_friendly(self):
        self.mocks['refresh'].side_effect = RuntimeError('Netz weg')
        self.choose('frei')
        self.tab.free_refresh(); self.settle()
        self.assertIn('Netz weg', self.tab.free_result['text'])
        self.assertIn('✖', self.tab.free_result['text'])

    def test_second_action_is_ignored_while_busy(self):
        t = self.tab
        self.choose('frei')
        t.free_refresh()
        self.assertFalse(t._proxy_run(lambda: None, lambda r, e: None, '', t.free_result))
        self.settle()
        self.assertEqual(sum(1 for c in self.calls if c == ('refresh',)), 1)

    def test_list_with_entries_in_mode_aus_shows_note(self):
        t = self.tab
        t.proxy_editor.entry_var.set('http://h.de:80')
        t.proxy_editor.add_from_entry()
        self.app.update()
        self.assertTrue(t.aus_note.winfo_manager())
        self.choose('liste')
        self.assertFalse(t.aus_note.winfo_manager())
        self.assertTrue(t.list_panel.winfo_manager())

    def test_logs_and_status_do_not_contain_secrets(self):
        t = self.tab
        self.choose('liste')
        t.proxy_editor.entry_var.set('http://kunde:GeheimPw@gw.de:8000')
        t.proxy_editor.add_from_entry()
        t.proxy_check(); self.settle()
        t.save()
        for text in [t.status_label['text'], t.check_result['text']] + [l for l, _ in self.app.logs]:
            self.assertNotIn('GeheimPw', text)


def proxylogic_color(kind):
    from socialscraper.ui.common import COLORS
    return COLORS[kind]


# ---------- Hinweise in den anderen Tabs ----------

try:
    from socialscraper.ui.tab_accounts import AccountsTab
    from socialscraper.ui.tab_fetch import FetchTab
    from socialscraper.ui.tab_auto import AutoTab
except Exception:  # pragma: no cover
    AccountsTab = FetchTab = AutoTab = None


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None and FetchTab is not None, 'Tk-Fenster nur unter Windows')
class OtherTabsTests(unittest.TestCase):
    """Konten-, Abrufen- und Automatik-Tab zeigen den Proxy-Modus und melden gesperrte Abrufe vor dem Start."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        env = patch.dict(os.environ, {'SS_DB_PATH': str(self.root / 'data' / 'scrape.db')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SS_PROXY_MODE', None)
        p = patch.object(core, 'ROOT', self.root)
        p.start()
        self.addCleanup(p.stop)
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self._close)
        self.msgs = {}
        for name in ('showinfo', 'showwarning', 'showerror', 'askyesno'):
            p = patch(f'tkinter.messagebox.{name}')
            self.msgs[name] = p.start()
            self.addCleanup(p.stop)
        self.app.job = None

    def _close(self):
        for tab in self.app.tabs.values():
            if hasattr(tab, 'loading'):
                self.app.pump(lambda t=tab: not t.loading, 5)
        self.app.destroy()
        self.app = None
        gc.collect()

    def mode(self, mode):
        core.write_settings({'proxy_mode': mode}, self.root)

    def test_fetch_tab_banner_and_refusals(self):
        fetch = self.app.add('Abrufen', FetchTab)
        self.app.update()
        self.assertFalse(fetch.mode_banner.winfo_manager())
        self.mode('tor')
        fetch.refresh()
        self.app.update()
        self.assertTrue(fetch.mode_banner.winfo_manager())
        self.assertIn('Proxy-Modus ‚Tor‘ aktiv: Konten sind gesperrt.', fetch.mode_banner.label['text'])
        fetch.max_comments.set(20)
        fetch.links.insert('1.0', 'https://www.tiktok.com/@abc/video/7000000000000000001')
        fetch.start_fetch()
        self.msgs['showwarning'].assert_called_once()
        self.assertIn('TikTok-Kommentare', self.msgs['showwarning'].call_args[0][1])
        self.assertEqual(self.app.steps, [])                                   # nichts gestartet
        self.assertIn('⚠', fetch.result['text'])
        fetch.links.delete('1.0', 'end')
        fetch.links.insert('1.0', 'https://www.instagram.com/reel/ABCdef12345/')
        self.msgs['showwarning'].reset_mock()
        fetch.start_fetch()
        self.assertIn('Instagram', self.msgs['showwarning'].call_args[0][1])
        self.assertEqual(self.app.steps, [])
        # YouTube und TikTok ohne Kommentare gehen
        fetch.links.delete('1.0', 'end')
        fetch.links.insert('1.0', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        fetch.max_comments.set(30)
        with patch('socialscraper.ui.logic.describe_busy', return_value=''):
            fetch.start_fetch()
        self.assertEqual(len(self.app.steps), 1)

    def test_fetch_profile_refused_with_comments_in_anonymous_mode(self):
        fetch = self.app.add('Abrufen', FetchTab)
        self.mode('frei')
        fetch.refresh()
        fetch.profile_user.set('@abc')
        fetch.max_comments.set(5)
        fetch.start_profile()
        self.msgs['showwarning'].assert_called_once()
        self.assertEqual(self.app.steps, [])

    def test_fetch_unchanged_in_mode_aus(self):
        fetch = self.app.add('Abrufen', FetchTab)
        fetch.refresh()
        fetch.links.insert('1.0', 'https://www.tiktok.com/@abc/video/7000000000000000001')
        fetch.max_comments.set(20)
        with patch('socialscraper.ui.logic.describe_busy', return_value=''):
            fetch.start_fetch()
        self.msgs['showwarning'].assert_not_called()
        self.assertEqual(len(self.app.steps), 1)

    def test_accounts_tab_banner_and_blocked_actions(self):
        acc = self.app.add('Konten', AccountsTab)
        self.app.update()
        self.assertFalse(acc.mode_banner.winfo_manager())
        self.mode('frei')
        acc.refresh()
        self.app.update()
        self.assertTrue(acc.mode_banner.winfo_manager())
        self.assertIn('Konten sind gesperrt', acc.mode_banner.label['text'])
        first = acc.tree.get_children()[0]
        acc.tree.selection_set(first)
        acc.login('qr')
        acc.check()
        self.assertEqual(self.msgs['showinfo'].call_count, 2)
        self.assertEqual(self.app.steps, [])
        self.assertIn('gesperrt', self.msgs['showinfo'].call_args[0][1])
        self.mode('liste')
        acc.refresh()
        self.assertFalse(acc.mode_banner.winfo_manager())

    def test_auto_tab_marks_blocked_targets(self):
        from socialscraper import repo
        from socialscraper.store import Store
        with Store(str(self.root / 'data' / 'scrape.db')) as store:
            repo.add_target(store.db, 'hashtag', 'tiktok', 'katzen', interval_min=60, max_comments=0)
            repo.add_target(store.db, 'video', 'youtube', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', interval_min=60, max_comments=10)
        self.mode('tor')
        auto = self.app.add('Automatik', AutoTab)
        self.app.update()
        auto.reload()
        self.app.pump(lambda: not auto.loading and auto.snapshot['targets'], 10)
        self.app.update()
        rows = [auto.tree.item(i, 'values') for i in auto.tree.get_children()]
        texts = [r[7] for r in rows]
        self.assertEqual(sorted(texts).count(proxylogic.BLOCKED_TEXT), 1)
        blocked = next(r for r in rows if r[7] == proxylogic.BLOCKED_TEXT)
        self.assertEqual(blocked[2], '#katzen')
        self.assertTrue(auto.mode_banner.winfo_manager())


if __name__ == '__main__':
    unittest.main()
