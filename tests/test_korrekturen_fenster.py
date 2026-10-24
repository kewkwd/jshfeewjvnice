"""Korrekturen am Programmfenster (Fundliste „fenster“, 13.10.2026): jeder Test zeigt den Fehler vorher und besteht nachher.

Alles läuft in Wegwerf-Ordnern (SS_DB_PATH, ROOT), ohne Netz, ohne Browser, nur mit verborgenen Fenstern.
Fenstertests mit dem Mini-Hauptfenster `ui_fake.FakeApp`; einige brauchen das echte (verborgene) Hauptfenster.
"""
import datetime
import gc
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import warnings
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import config, guicore as core, repo, uistate
from socialscraper.models import Video
from socialscraper.store import Store
from socialscraper.ui import logic

PROJECT = pathlib.Path(__file__).resolve().parent.parent
UTC = datetime.timezone.utc

try:
    import tkinter
    from tkinter import ttk
    from ui_fake import FakeApp
except Exception:  # pragma: no cover
    tkinter = FakeApp = None


class Base(unittest.TestCase):
    """Wegwerf-Ordner mit Datenbankpfad; die echten Daten und die echte settings.toml bleiben unberührt."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'data').mkdir()
        self.db = self.root / 'data' / 'scrape.db'
        for p in (patch.dict(os.environ, {'SS_DB_PATH': str(self.db)}), patch.object(core, 'ROOT', self.root)):
            p.start()
            self.addCleanup(p.stop)
        for env in config._ENV:            # keine Umgebungsvariable des Rechners darf die Einstellungen der Tests überstimmen
            if env != 'SS_DB_PATH':
                os.environ.pop(env, None)


# ---------- Logik ohne Fenster ----------

class LogikTests(Base):
    def seed(self):
        with Store(self.db) as s:
            repo.ensure_default_accounts(s.db)
            for v in (Video('youtube', 'a1', 'https://youtu.be/a1', author='Anna'),
                      Video('tiktok', 'b2', 'https://www.tiktok.com/@u/video/b2', author='Bert'),
                      Video('instagram', 'c3', 'https://www.instagram.com/p/c3/', author='Cleo')):
                s.save_result(v, [], None)

    def test_fund4_cloud_zeilen_mit_t_format_zaehlen_nicht_als_gerade_abgerufen(self):
        # Aus der Cloud übernommene Zeilen haben fetched_at 'JJJJ-MM-TTTHH:MM:SSZ'; im rohen Textvergleich galt jede
        # Zeile desselben Tages als neuer als 'JJJJ-MM-TT HH:MM:SS' (weil 'T' > ' ').
        self.seed()
        with logic.writer(self.db) as db:
            db.execute("UPDATE videos SET fetched_at='2026-10-13T08:00:00Z' WHERE video_id='a1'")    # Cloud, Vormittag
            db.execute("UPDATE videos SET fetched_at='2026-10-13 14:05:00' WHERE video_id='b2'")     # lokal, nach dem Start
            db.execute("UPDATE videos SET fetched_at='2026-10-13T14:05:00Z' WHERE video_id='c3'")    # Cloud, nach dem Start
            db.commit()
        since = datetime.datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
        found = logic.find_fetched_uids(self.db, since, ['youtube', 'tiktok', 'instagram'])
        self.assertNotIn('youtube:a1', found)
        self.assertEqual(sorted(found), ['instagram:c3', 'tiktok:b2'])
        # ohne Linkliste (TikTok-Profil, Kurzlink) nimmt der Abruf alle Kandidaten der Plattform – auch da bleibt die alte Zeile draußen
        with logic.writer(self.db) as db:
            db.execute("UPDATE videos SET fetched_at='2026-10-13T08:00:00Z' WHERE video_id='b2'"); db.commit()
        self.assertEqual(logic.find_fetched_uids(self.db, since, ['tiktok'], {'tiktok': ['https://vm.tiktok.com/xyz/']}), [])

    def test_fund10_aus_gruppe_entfernen_zaehlt_nur_wirklich_entfernte(self):
        self.seed()
        g = logic.create_group(self.db, 'Reise')
        uids = ['youtube:a1', 'tiktok:b2', 'instagram:c3']
        logic.add_to_group(self.db, g['id'], uids[:1])
        # zwei der drei markierten Videos hängen nicht über group_videos in der Gruppe (z. B. nur über ihren Ersteller)
        self.assertEqual(logic.remove_from_group(self.db, g['id'], uids), 1)
        self.assertEqual(logic.remove_from_group(self.db, g['id'], uids), 0)

    def test_fund13_standard_kommentare_null_bleibt_null(self):
        from socialscraper.ui.tab_auto import AutoTab
        (self.root / 'settings.toml').write_text('max_comments = 0\n', encoding='utf-8')
        fake = types.SimpleNamespace(root=lambda: self.root)
        self.assertEqual(AutoTab._default_comments(fake), 0)                # 0 = bewusst „nur Videodaten“
        (self.root / 'settings.toml').write_text('max_comments = 7000\n', encoding='utf-8')
        self.assertEqual(AutoTab._default_comments(fake), 5000)
        (self.root / 'settings.toml').write_text('max_comments = [[[\n', encoding='utf-8')
        self.assertEqual(AutoTab._default_comments(fake), 20)              # nur bei Lesefehler der Notwert

    def test_fund23_fensterlage_auf_zweitem_bildschirm_bleibt(self):
        geo = {'w': 1000, 'h': 700, 'x': 2100, 'y': 10}
        self.assertEqual(uistate.clamp_geometry(geo, 1920, 1080), (1000, 700, None, None))             # nur Hauptbildschirm bekannt
        self.assertEqual(uistate.clamp_geometry(geo, 3840, 1080, screen_x=0, screen_y=0), (1000, 700, 2100, 10))
        links = dict(geo, x=-1000)                                                                      # Monitor links vom Hauptbildschirm
        self.assertEqual(uistate.clamp_geometry(links, 3840, 1080, screen_x=-1920, screen_y=0), (1000, 700, -1000, 10))
        self.assertEqual(uistate.clamp_geometry(dict(geo, x=5000), 3840, 1080, screen_x=0)[2:], (None, None))   # wirklich weg
        self.assertEqual(uistate.clamp_geometry(dict(geo, y=-500), 3840, 1080, screen_x=0, screen_y=-1080), (1000, 700, 2100, -500))


# ---------- Fenster mit dem Mini-Hauptfenster ----------

@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class FensterBase(Base):
    def setUp(self):
        super().setUp()
        for name in ('showinfo', 'showwarning', 'showerror'):
            p = patch(f'tkinter.messagebox.{name}'); setattr(self, name, p.start()); self.addCleanup(p.stop)
        p = patch('tkinter.messagebox.askyesno', return_value=True); self.ask = p.start(); self.addCleanup(p.stop)
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.tab = None
        self.addCleanup(self._close)

    def _close(self):
        tab = getattr(self, 'tab', None)
        if tab is not None and hasattr(tab, 'loading'):
            self.app.pump(lambda: not tab.loading, 5)
        self.app.destroy()
        self.app = self.tab = None
        gc.collect()

    @staticmethod
    def _walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from FensterBase._walk(child)

    def _texts(self, widget, kind):
        return [str(w.cget('text')) for w in self._walk(widget) if isinstance(w, kind)]


class FensterTests(FensterBase):
    def test_fund3_run_async_kann_ohne_daemon_laufen(self):
        from socialscraper.ui.forms import run_async
        seen, done = {}, []
        run_async(self.app, lambda: seen.setdefault('daemon', threading.current_thread().daemon), lambda r, e: done.append(r), daemon=False)
        self.assertTrue(self.app.pump(lambda: bool(done)))
        self.assertFalse(seen['daemon'])                                   # Sicherung/Export laufen nach dem Schließen zu Ende
        seen.clear(); done.clear()
        run_async(self.app, lambda: seen.setdefault('daemon', threading.current_thread().daemon), lambda r, e: done.append(r))
        self.assertTrue(self.app.pump(lambda: bool(done)))
        self.assertTrue(seen['daemon'])                                    # Standard bleibt wie bisher

    def test_fund7_kanal_dialog_sagt_wenn_gruppen_und_konten_fehlen(self):
        from socialscraper.ui import channel_dialog
        self.db.write_bytes(b'kein sqlite ' * 100)                         # beschädigte Datenbank
        parent = ttk.Frame(self.app)
        parent.app = self.app
        dialog = channel_dialog.open_dialog(parent, self.root, '', modal=False)
        self.addCleanup(dialog.destroy)
        self.showwarning.assert_called_once()
        self.assertIn('beschädigt', self.showwarning.call_args[0][1])
        self.assertTrue(any('Kanal-Dialog' in line and tag == 'ERROR' for line, tag in self.app.logs))

    def test_fund8_dienst_stoppt_bleibt_waehrend_refresh_stehen(self):
        from socialscraper.ui.forms import ServicePanel
        panel = ServicePanel(self.app, lambda: self.root)
        panel.pack()
        panel.refresh()
        self.assertNotIn('stoppt', panel.state_label['text'])
        panel._set_busy('Dienst stoppt … (kann bis zu einer halben Minute dauern)')
        panel.refresh()                                                     # kommt alle 5 s aus den Tabs
        self.assertTrue(panel.state_label['text'].startswith('Dienst stoppt'))
        self.assertEqual(str(panel.stop_btn['state']), 'disabled')
        self.assertIsNotNone(panel.last_status)                            # der Zustand wird trotzdem gemerkt
        panel.busy = False
        panel.refresh()
        self.assertNotIn('stoppt', panel.state_label['text'])              # danach wieder der echte Zustand

    def test_fund9_protokoll_der_hintergrund_auftraege_waechst_nicht_endlos(self):
        from socialscraper.ui.hintergrund import Protokoll
        text = tkinter.Text(self.app)
        protokoll = Protokoll(text)
        for i in range(3001):
            protokoll.anhaengen(f'Zeile {i}')
        lines = int(text.index('end-1c').split('.')[0])
        self.assertLess(lines, 3000)
        self.assertGreater(lines, 2000)
        self.assertIn('Zeile 3000', text.get('end-2l', 'end'))             # die neuesten Zeilen bleiben

    def test_fund11_ausgewaehltes_konto_folgt_der_auswahl_auch_waehrend_eines_auftrags(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        self.tab = self.app.add('Konten', AccountsTab)
        self.tab.refresh(); self.app.update()
        rows = list(self.tab.tree.get_children())
        first, second = self.tab.accounts[rows[0]], self.tab.accounts[rows[1]]
        self.tab.tree.selection_set(rows[0]); self.tab._update_buttons()
        self.assertIn(core.PLATFORMS[first['platform']]['name'], self.tab.account_name['text'])
        self.app.job = object()                                             # ein Auftrag läuft: das Hauptfenster hat die Knöpfe gesperrt
        self.tab.btn['remove'].configure(state='disabled')
        self.tab.tree.selection_set(rows[1]); self.tab._update_buttons()
        self.assertIn(core.PLATFORMS[second['platform']]['name'], self.tab.account_name['text'])
        self.assertEqual(str(self.tab.btn['remove']['state']), 'disabled')  # Knöpfe bleiben gesperrt
        self.app.job = None

    def test_fund15_abgelehnter_start_zeigt_nicht_abruf_laeuft(self):
        from socialscraper.ui.tab_fetch import FetchTab
        self.tab = self.app.add('Abrufen', FetchTab)
        self.tab.refresh(); self.app.update()
        self.tab._post_plan(['youtube'], {'youtube': ['https://youtu.be/abcdefghijk']})
        with patch.object(self.app, 'run_steps', return_value=False):      # „Es läuft bereits ein Auftrag.“
            self.tab._start([('YouTube: 1 Link abrufen', ['scrape', 'https://youtu.be/abcdefghijk'])])
        self.assertIsNone(self.tab._pending)
        self.assertIn('nicht gestartet', self.tab.result['text'])
        self.assertNotIn('Abruf läuft', self.tab.result['text'])
        # die Vorprüfung erkennt auch Hintergrund-Aufträge anderer Seiten (die run_steps ebenfalls ablehnt)
        self.app.tabs['Aufwärmen'] = types.SimpleNamespace(_auftrag=types.SimpleNamespace(laeuft=True))
        self.assertTrue(self.tab._already_running())
        self.showinfo.assert_called_once()
        del self.app.tabs['Aufwärmen']
        self.assertFalse(self.tab._already_running())

    def test_fund15_anmeldung_nicht_gestartet_meldet_sich_in_der_statuszeile(self):
        from socialscraper.ui.tab_accounts import AccountsTab
        self.tab = self.app.add('Konten', AccountsTab)
        self.tab.refresh(); self.app.update()
        rows = list(self.tab.tree.get_children())
        self.tab.tree.selection_set(rows[0])
        with patch.object(self.app, 'run_steps', return_value=False), patch.object(logic, 'describe_busy', return_value=None):
            self.tab.login('qr')
        self.assertIn('nicht gestartet', self.app.statuses[-1][0])


class EinstellungenTests(FensterBase):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_settings import SettingsTab
        self.tab = self.app.add('Einstellungen', SettingsTab)
        self.app.update()

    def test_fund16_hinweis_am_verlaufspunkt_nennt_die_automatik(self):
        hint = next(t for t in self._texts(self.tab, ttk.Label) if 'Verlaufspunkt' in t and 'entwickeln' in t)
        self.assertIn('Automatik speichert immer einen Verlaufspunkt', hint)

    def test_fund17_neu_erzeugen_zeigt_token_und_umschalter_sagt_verbergen(self):
        t = self.tab
        self.assertEqual((t.token_entry['show'], t.token_toggle['text']), ('•', 'Anzeigen'))
        t._new_token()
        self.assertEqual((t.token_entry['show'], t.token_toggle['text']), ('', 'Verbergen'))
        t.token_toggle.invoke()                                             # ein Klick verbirgt wieder
        self.assertEqual((t.token_entry['show'], t.token_toggle['text']), ('•', 'Anzeigen'))

    def test_fund18_systempruefung_und_proxy_pruefen_haben_eigene_knoepfe(self):
        t = self.tab
        self.assertIsNot(t.check_btn, t.syscheck_btn)
        self.assertIn('Systemprüfung', t.syscheck_btn['text'])
        self.assertEqual(t.check_btn['text'], 'Prüfen')
        t._proxy_buttons(False)                                             # Proxy-Arbeit läuft
        self.assertEqual((str(t.check_btn['state']), str(t.syscheck_btn['state'])), ('disabled', 'normal'))
        t._proxy_buttons(True)
        t.show_check()
        self.assertEqual((str(t.check_btn['state']), str(t.syscheck_btn['state'])), ('normal', 'disabled'))
        self.assertTrue(self.app.pump(lambda: str(t.syscheck_btn['state']) == 'normal'))

    def test_fund19_umgebungsvariable_ueberstimmt_speichern_wird_gemeldet(self):
        t = self.tab
        with patch.dict(os.environ, {'SS_HEADLESS': 'true'}):
            t.vars['headless'].set(False)
            self.assertTrue(t.dirty)
            self.assertTrue(t.save())
            self.assertTrue(t.vars['headless'].get())                       # die Variable gewinnt, das Feld springt zurück
        self.showwarning.assert_called_once()
        self.assertIn('SS_HEADLESS', self.showwarning.call_args[0][1])
        self.assertIn('headless', self.showwarning.call_args[0][1])
        self.assertEqual(self.app.statuses[-1][1], 'warn')
        self.assertTrue(any('SS_HEADLESS' in line for line, _tag in self.app.logs))
        self.showwarning.reset_mock()
        t.vars['max_comments'].set('40')                                    # ohne Variable: alles wie bisher
        self.assertTrue(t.save())
        self.showwarning.assert_not_called()
        self.assertEqual(self.app.statuses[-1], ('Einstellungen gespeichert.', 'ok'))

    def test_fund20_sprung_zum_ki_schluessel_haengt_den_pfeil_nur_einmal_an(self):
        t = self.tab
        t.vars['max_comments'].set('41')                                    # ungespeichert: refresh lädt nicht neu
        for _ in range(3):
            t.focus_item({'field': 'ai_key'})
        self.assertEqual(t.ai_key_state.cget('text').count('⬆'), 1)

    def test_fund21_nicht_lesbare_integration_bietet_kein_ersetzen_an(self):
        from socialscraper import integration
        t = self.tab
        with patch.object(integration, 'load_with_status',
                          return_value=(integration.default_config(), 'Die Datei integration.json lässt sich nicht lesen (gesperrt).')):
            t._load_from_disk()
        buttons = self._texts(t.banner, ttk.Button)
        self.assertIn('Erneut laden', buttons)
        self.assertFalse([b for b in buttons if 'ersetzen' in b])
        with patch.object(integration, 'load_with_status',
                          return_value=(integration.default_config(), 'Die Datei integration.json ist beschädigt und wird ignoriert.')):
            t._load_from_disk()
        self.assertTrue([b for b in self._texts(t.banner, ttk.Button) if 'ersetzen' in b])

    def test_fund22_kein_dienst_gestartet_wenn_der_alte_nicht_stoppt(self):
        from socialscraper import autologic, daemon
        t = self.tab
        running = {'running': True, 'pid': 4711, 'stale': False, 'current': None}
        with patch.object(daemon, 'stop', return_value=False), patch.object(daemon, 'start_detached') as start, \
                patch.object(autologic, 'service_status', return_value=running):
            t.restart_service()
            self.assertTrue(self.app.pump(lambda: any('nicht beenden' in s for s, _k in self.app.statuses), 5))
            self.app.pump(lambda: False, 0.5)                               # kurz weiterlaufen lassen: kein verspäteter Start
        start.assert_not_called()
        self.assertEqual(self.app.statuses[-1][1], 'err')
        self.assertNotIn('Dienst gestartet.', [s for s, _k in self.app.statuses])


# ---------- Echtes (verborgenes) Hauptfenster ----------

@unittest.skipUnless(os.name == 'nt' and tkinter is not None, 'Tk-Fenster nur unter Windows')
class HauptfensterTests(unittest.TestCase):
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
        for name in ('showinfo', 'showwarning', 'showerror'):
            p = patch(f'tkinter.messagebox.{name}'); setattr(self, name, p.start()); self.addCleanup(p.stop)
        p = patch('tkinter.messagebox.askyesno', return_value=True); self.ask = p.start(); self.addCleanup(p.stop)
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

    def pump(self, app, until, timeout=10):
        import time
        end = time.time() + timeout
        while not until() and time.time() < end:
            app.update()
            time.sleep(0.02)
        return until()

    @staticmethod
    def _fake_job(cancel_result=True):
        return types.SimpleNamespace(lines=[], cancelled=False, cancel=lambda: cancel_result)

    def _arm(self, app, steps=()):
        """So tun, als liefe gerade ein Schritt „A“ (ohne echten Prozess): der nächste 'done'-Eintrag schließt ihn ab."""
        app.job = self._fake_job()
        app.steps, app.results, app.current_label = list(steps), [], 'A'
        app.calls = []
        app.on_finish = lambda r, c: app.calls.append((r, c))
        app.cancelled = False
        app._busy(True)
        app.after_cancel(app._after_ids['poll'])                            # _poll rufen die Tests selbst auf

    def test_fund1_fehler_beim_neuladen_eines_tabs_legt_die_auftragsschleife_nicht_still(self):
        app = self.make()
        self._arm(app)

        def broken():
            raise sqlite3.OperationalError('database is locked')
        app.tabs['Konten'].refresh = broken
        app.events.put(('done', 0))
        app._poll()
        self.assertEqual(app.calls, [([('A', 0, [])], False)])              # on_finish kommt trotzdem
        shown = app.log_text.get('1.0', 'end')
        self.assertIn('Konten', shown)
        self.assertIn('database is locked', shown)
        self.assertTrue(app.tk.call('after', 'info', app._after_ids['poll']))   # die Schleife ist wieder eingeplant
        self.assertEqual(str(app.cancel_btn['state']), 'disabled')         # Knöpfe sind wieder frei
        self._arm(app)                                                      # der nächste Auftrag kommt noch an
        app.events.put(('done', 0))
        app._poll()
        self.assertEqual(app.calls, [([('A', 0, [])], False)])
        self.assertTrue(app.tk.call('after', 'info', app._after_ids['poll']))

    def test_fund1_fehler_im_rueckruf_stoppt_weder_meldung_noch_schleife(self):
        app = self.make()
        self._arm(app)

        def broken(_results, _cancelled):
            raise sqlite3.OperationalError('database is locked')
        app.on_finish = broken
        app.events.put(('done', 0))
        with patch.object(app, '_notify_finished') as notify:
            app._poll()
        notify.assert_called_once()
        self.assertIn('database is locked', app.log_text.get('1.0', 'end'))
        self.assertTrue(app.tk.call('after', 'info', app._after_ids['poll']))

    def test_fund1_schliessen_waehrend_eines_auftrags_merkt_fenster_und_tab(self):
        app = self.make()
        app.deiconify(); app.geometry('1100x760+40+30'); app.update()
        app.select_tab('Konten'); app.update()
        app._closing = True                                                 # Marc hat „Abbrechen und beenden“ bestätigt
        app._finish()                                                       # das Prozessende kommt an
        self.apps.remove(app)
        data = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual((data['geometry']['w'], data['geometry']['h'], data['tab']), (1100, 760, 'Konten'))

    def test_fund2_x_knopf_bei_hintergrund_auftrag_fragt_und_zeigt_den_abbruch(self):
        app = self.make()
        app.select_tab('Konten'); app.update()

        class Auftrag:
            laeuft = True

            def abbrechen(self):
                self.laeuft = False                                         # der Auftrag endet an seinem Prüfpunkt
        task = Auftrag()
        app.tabs['Aktionen']._auftrag = task
        self.ask.return_value = False                                       # „Nein“: nichts passiert
        app._close()
        self.assertTrue(task.laeuft)
        self.assertFalse(app._closing)
        self.ask.return_value = True
        app._close()
        self.assertTrue(app._closing)
        self.assertIn('Abbruch läuft', app.status['text'])
        self.assertTrue(self.pump(app, lambda: app._destroyed, 5))         # schließt, sobald der Auftrag zu Ende ist
        self.apps.remove(app)
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['tab'], 'Konten')

    def test_fund5_abbruch_im_moment_des_schrittendes_meldet_abgebrochen(self):
        app = self.make()
        self._arm(app, steps=[('B', ['scrape', 'https://example.org/b'])])
        app.cancel()                                                        # der Prozess von A endet gleichzeitig von selbst
        self.assertTrue(app.cancelled)
        app.events.put(('done', 0))
        with patch.object(core, 'Job') as job_cls:
            app._poll()
        job_cls.assert_not_called()                                         # B läuft nicht mehr …
        self.assertEqual(app.calls, [([('A', 0, [])], True)])              # … und der Lauf gilt als abgebrochen, nicht als „Fertig“
        self.assertEqual(app.steps, [])
        # war A der letzte Schritt, bleibt es ein regulärer Abschluss
        self._arm(app)
        app.cancel()
        app.events.put(('done', 0))
        app._poll()
        self.assertEqual(app.calls, [([('A', 0, [])], False)])

    def test_fund6_editor_oeffnen_ohne_resourcewarning(self):
        app = self.make()
        real = subprocess.Popen

        def harmless(args, *a, **kw):                                       # kein Notepad-Fenster im Test
            return real([sys.executable, '-c', 'pass'], *a, **kw)
        with patch('subprocess.Popen', side_effect=harmless) as popen, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            app.open_path(self.root / 'settings.toml', edit=True)
            gc.collect()
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0][0], 'notepad.exe')
        self.assertFalse([w for w in caught if issubclass(w.category, ResourceWarning)])

    def test_fund23_hauptfenster_nimmt_den_ganzen_desktop(self):
        app = self.make()
        x0, y0, w, h = app._screen_bounds()
        self.assertTrue(all(isinstance(v, int) for v in (x0, y0, w, h)))
        self.assertGreaterEqual(w, app.winfo_screenwidth())
        metrics = {76: -1920, 77: 0, 78: 3840, 79: 1080}                    # zwei Bildschirme, der zweite links
        with patch('ctypes.windll.user32.GetSystemMetrics', side_effect=lambda n: metrics[n]):
            self.assertEqual(app._screen_bounds(), (-1920, 0, 3840, 1080))
        app.destroy(); self.apps.remove(app)
        self.state_path.write_text(json.dumps({'geometry': {'w': 1000, 'h': 700, 'x': -1000, 'y': 10}}), encoding='utf-8')
        calls = []
        real = tkinter.Tk.geometry

        def spy(widget, value=None):
            if value is not None:
                calls.append(value)
            return real(widget, value)
        with patch('ctypes.windll.user32.GetSystemMetrics', side_effect=lambda n: metrics[n]), patch.object(self.appmod.App, 'geometry', spy):
            self.make()
        self.assertEqual(calls[0], '1000x700+-1000+10')                     # die Lage auf dem zweiten Bildschirm bleibt


if __name__ == '__main__':
    unittest.main()
