import csv
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest

from socialscraper import guicore as core
from socialscraper.models import Comment, Video
from socialscraper.store import Store


class LinkTests(unittest.TestCase):
    def test_detects_platform_by_real_host(self):
        self.assertEqual(core.detect_platform('https://www.tiktok.com/@a/video/1'), 'tiktok')
        self.assertEqual(core.detect_platform('https://vm.tiktok.com/abc/'), 'tiktok')
        self.assertEqual(core.detect_platform('https://youtu.be/abc'), 'youtube')
        self.assertEqual(core.detect_platform('https://m.youtube.com/watch?v=1'), 'youtube')
        self.assertEqual(core.detect_platform('https://www.instagram.com/reel/abc/'), 'instagram')

    def test_rejects_lookalikes_and_odd_schemes(self):
        for bad in ('https://evil.com/?u=https://tiktok.com/x', 'https://tiktok.com.evil.com/x',
                    'https://nottiktok.com/x', 'ftp://tiktok.com/x', 'javascript:alert(1)',
                    'https://user:pw@www.tiktok.com/x', 'not a link', ''):
            self.assertIsNone(core.detect_platform(bad), bad)

    def test_parse_links_groups_dedupes_and_reports_bad_lines(self):
        text = ('  https://youtu.be/a  \n\nwww.tiktok.com/@u/video/1\nhttps://youtu.be/a\n'
                '<https://www.instagram.com/p/xyz/>\nhallo\r\nhttps://example.org/x\n')
        groups, bad = core.parse_links(text)
        self.assertEqual(groups['youtube'], ['https://youtu.be/a'])
        self.assertEqual(groups['tiktok'], ['https://www.tiktok.com/@u/video/1'])
        self.assertEqual(groups['instagram'], ['https://www.instagram.com/p/xyz/'])
        self.assertEqual(bad, ['hallo', 'https://example.org/x'])


class CanonicalLinkTests(unittest.TestCase):
    ID = 'dQw4w9WgXcQ'

    def test_same_youtube_video_in_every_spelling_is_one_video(self):
        spellings = [f'https://www.youtube.com/watch?v={self.ID}', f'https://youtu.be/{self.ID}', f'https://m.youtube.com/watch?v={self.ID}',
                     f'https://youtube.com/watch?v={self.ID}&t=42s&feature=share', f'https://youtu.be/{self.ID}?si=abcDEF',
                     f'https://www.youtube.com/shorts/{self.ID}', f'https://music.youtube.com/watch?v={self.ID}&list=PLx',
                     f'www.youtube.com/watch?feature=share&v={self.ID}', f'<https://youtu.be/{self.ID}>']
        groups, bad, merged = core.parse_links_detailed('\n'.join(spellings))
        self.assertEqual(bad, [])
        self.assertEqual(groups, {'youtube': [f'https://www.youtube.com/watch?v={self.ID}']})
        self.assertEqual(merged, len(spellings) - 1)

    def test_different_videos_stay_different(self):
        groups, _bad, merged = core.parse_links_detailed(f'https://youtu.be/{self.ID}\nhttps://youtu.be/aBcDeFgHiJk\n'
                                                        'https://www.tiktok.com/@u/video/111\nhttps://www.tiktok.com/@u/video/222')
        self.assertEqual(len(groups['youtube']), 2); self.assertEqual(len(groups['tiktok']), 2); self.assertEqual(merged, 0)

    def test_tiktok_and_instagram_tracking_parameters_are_dropped(self):
        groups, _bad, merged = core.parse_links_detailed(
            'https://www.tiktok.com/@anna/video/7123456789012345678?is_from_webapp=1&sender_device=pc\n'
            'https://m.tiktok.com/@anna/video/7123456789012345678\n'
            'https://www.instagram.com/reel/CxYz123abc/?igsh=xyz\n'
            'https://www.instagram.com/anna/reel/CxYz123abc/\n'
            'https://www.instagram.com/p/AbCdE12345/')
        self.assertEqual(groups['tiktok'], ['https://www.tiktok.com/@anna/video/7123456789012345678'])
        self.assertEqual(groups['instagram'], ['https://www.instagram.com/reel/CxYz123abc/', 'https://www.instagram.com/p/AbCdE12345/'])
        self.assertEqual(merged, 2)

    def test_unrecognised_links_stay_as_they_are(self):
        self.assertEqual(core.canonical_video('https://vm.tiktok.com/ZMabc/'), ('https://vm.tiktok.com/ZMabc/', 'https://vm.tiktok.com/ZMabc/'))
        self.assertEqual(core.canonical_video('https://www.youtube.com/@kanal')[0], 'https://www.youtube.com/@kanal')
        self.assertEqual(core.canonical_video('https://youtu.be/a')[1], 'https://youtu.be/a')   # keine gültige Kennung


class DisplayLineTests(unittest.TestCase):
    def test_empty_and_raw_summary_lines_become_readable(self):
        self.assertIsNone(core.display_line('')); self.assertIsNone(core.display_line('   '))
        self.assertEqual(core.display_line("{'videos': {'youtube': 1}, 'comments': {'youtube': 5}}"),
                         'Gespeichert insgesamt: 1 Video, 5 Kommentare')
        self.assertEqual(core.display_line("{'videos': {'youtube': 2, 'tiktok': 1}, 'comments': {}}"),
                         'Gespeichert insgesamt: 3 Videos, 0 Kommentare')
        self.assertEqual(core.display_line('fertig: 2 ok, 0 fehler'), 'Fertig: 2 abgerufen')
        self.assertEqual(core.display_line('fertig: 1 ok, 1 fehler'), 'Fertig: 1 abgerufen, 1 fehlgeschlagen')
        self.assertEqual(core.display_line('export -> x/'), 'export -> x/')
        self.assertEqual(core.display_line('{kein wörterbuch'), '{kein wörterbuch')
        self.assertEqual(core.display_line('{}'), '{}')
        self.assertEqual(core.parse_summary(['fertig: 2 ok, 0 fehler']), (2, 0))          # die Auswertung liest weiter die Rohzeile

    def test_cookie_import_errors_are_translated(self):
        for raw in ('Ungültiger Cookie-Header', 'Keine Cookies in der Importdatei gefunden', 'Cookie-JSON ist ungültig'):
            shown = core.display_line(f'04:15:37 ERROR   cli | {raw}')
            self.assertTrue(shown.startswith('04:15:37 ERROR   cli | Die Datei enthält keine passenden Cookies'), shown)
            self.assertEqual(core.log_level(shown), 'ERROR')
        shown = core.display_line("04:15:37 ERROR   cli | [Errno 2] No such file or directory: 'C:\\\\x\\\\c.json'")
        self.assertIn('Die Datei wurde nicht gefunden', shown); self.assertNotIn('Errno', shown)
        self.assertIn('nicht zugegriffen', core.display_line('04:15:37 ERROR   cli | [Errno 13] Permission denied: x'))
        self.assertEqual(core.display_line('04:15:37 INFO    cli | youtube -> abc | 0 Kommentare'), '04:15:37 INFO    cli | youtube -> abc | 0 Kommentare')

    def test_target_log_lines_get_the_target_name(self):
        names = {3: 'Hashtag #lofi (YouTube)'}
        line = '04:15:37 INFO    socialscraper.scheduler | Ziel 3: ok (1 Videos, 1 neu, 1 neue Kommentare)'
        self.assertEqual(core.display_line(line, names.get),
                         '04:15:37 INFO    socialscraper.scheduler | Hashtag #lofi (YouTube): fertig - 1 Video, 1 neu, 1 neuer Kommentar')
        self.assertIn('Ziel 9: fertig - 2 Videos', core.display_line(line.replace('Ziel 3', 'Ziel 9').replace('1 Videos', '2 Videos'), names.get))
        self.assertIn('Ziel 3: fertig', core.display_line(line))                                  # ohne Namensauskunft bleibt die Nummer
        warn = '04:15:37 WARNING socialscraper.scheduler | Ziel 3 fehlgeschlagen: Netzwerkfehler'
        self.assertTrue(core.display_line(warn, names.get).endswith('Hashtag #lofi (YouTube) fehlgeschlagen: Netzwerkfehler'))
        self.assertIsNone(core.display_line(''))

    def test_plural_helper(self):
        self.assertEqual(core.plural(1, 'Video', 'Videos'), '1 Video'); self.assertEqual(core.plural(0, 'Video', 'Videos'), '0 Videos')
        self.assertEqual(core.plural(2, 'neuer Kommentar', 'neue Kommentare'), '2 neue Kommentare')


class CommandTests(unittest.TestCase):
    def test_scrape_command_uses_platform_identity_and_flags(self):
        cmd = core.scrape_command('tiktok', ['https://www.tiktok.com/@u/video/1'], 20, require_auth=True)
        self.assertEqual(cmd[0], 'scrape')
        self.assertEqual(cmd[cmd.index('--identity') + 1], 'tiktok_main')
        self.assertEqual(cmd[cmd.index('--max-comments') + 1], '20')
        self.assertIn('--require-auth', cmd)
        self.assertNotIn('--require-auth', core.scrape_command('youtube', ['https://youtu.be/a'], 0))

    def test_link_starting_with_dash_cannot_become_an_option(self):
        groups, bad = core.parse_links('--export C:/x')
        self.assertEqual(groups, {}); self.assertEqual(bad, ['--export C:/x'])

    def test_profile_username_is_validated(self):
        self.assertEqual(core.profile_command('@jane.doe_1', 5, 0)[1], 'jane.doe_1')
        for bad in ('', 'a b', '--limit', 'a/b', 'x' * 65, '&calc'):
            with self.assertRaises(ValueError): core.profile_command(bad, 5, 0)

    def test_youtube_account_commands_are_supported(self):
        for fn in (core.check_command, core.logout_command):
            self.assertIn('youtube', fn('youtube'))
        with self.assertRaises(ValueError): core.login_command('tiktok', 'evil')

    def test_commands_are_accepted_by_the_real_parser(self):
        from socialscraper import cli
        ap = cli.parser()
        for cmd in (core.scrape_command('instagram', ['https://www.instagram.com/p/a/'], 5, True, 'x'),
                    core.profile_command('user', 3, 5, True), core.login_command('tiktok', 'qr'),
                    core.login_command('instagram', 'password'), core.check_command('tiktok'),
                    core.logout_command('instagram')):
            ap.parse_args(cmd)

    def test_summary_and_levels(self):
        self.assertEqual(core.parse_summary(['x', 'fertig: 2 ok, 1 fehler', '{}']), (2, 1))
        self.assertIsNone(core.parse_summary(['Traceback']))
        self.assertEqual(core.log_level('04:15:37 ERROR   cli | x'), 'ERROR')
        self.assertEqual(core.log_level('04:15:37 fertig'), '')


class StatusAndSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def cookie(self, name, value='x'):
        d = self.root / 'data' / 'cookies'; d.mkdir(parents=True, exist_ok=True)
        (d / 'tiktok_main.tiktok.json').write_text(json.dumps([{'name': name, 'value': value}]), encoding='utf-8')

    def test_status_variants(self):
        self.assertEqual(core.account_status('tiktok', self.root)[0], 'missing')
        self.assertEqual(core.account_status('youtube', self.root)[0], 'none')
        self.cookie('sessionid'); self.assertEqual(core.account_status('tiktok', self.root)[0], 'saved')
        self.cookie('sessionid', ''); self.assertEqual(core.account_status('tiktok', self.root)[0], 'missing')
        self.cookie('other'); self.assertEqual(core.account_status('tiktok', self.root)[0], 'missing')
        (self.root / 'data/cookies/tiktok_main.tiktok.json').write_text('{kaputt', encoding='utf-8')
        self.assertEqual(core.account_status('tiktok', self.root)[0], 'error')

    def test_settings_edit_keeps_comments_tables_and_crlf(self):
        src = ('# Kommentar\r\nbackend = "local"\r\nheadless = false\r\nmax_comments = 500\r\n\r\n'
               'proxies = []\r\n\r\n[sphere_presets]\r\ntiktok = ""\r\nmax_comments = 7\r\n')
        (self.root / 'settings.toml').write_bytes(src.encode())
        core.write_settings({'max_comments': 20, 'headless': True}, self.root)
        out = (self.root / 'settings.toml').read_bytes().decode()
        self.assertIn('# Kommentar', out); self.assertIn('max_comments = 20\r\n', out)
        self.assertIn('headless = true', out); self.assertIn('[sphere_presets]\r\ntiktok = ""\r\nmax_comments = 7', out)
        s = core.read_settings(self.root); self.assertEqual((s.max_comments, s.headless), (20, True))

    def test_settings_adds_missing_key_before_first_table(self):
        (self.root / 'settings.toml').write_text('backend = "local"\n[sphere_presets]\ntiktok = ""\n', encoding='utf-8')
        core.write_settings({'snapshot': False}, self.root)
        self.assertFalse(core.read_settings(self.root).snapshot)

    def test_settings_rejects_bad_values_and_keeps_file(self):
        (self.root / 'settings.toml').write_text('max_comments = 5\n', encoding='utf-8')
        for bad in ({'max_comments': -1}, {'max_comments': True}, {'headless': 1}, {'proxies': 'x'}):
            with self.assertRaises(ValueError): core.write_settings(bad, self.root)
        self.assertEqual((self.root / 'settings.toml').read_text(), 'max_comments = 5\n')


class DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = pathlib.Path(self.tmp.name) / 'sub dir ä' / 'scrape.db'
        with Store(self.db) as s:
            s.save_result(Video('youtube', 'v1', 'https://youtu.be/v1', author='Anna', caption='Hallo 100% _Welt_', view_count=5),
                          [Comment('youtube', 'v1', 'c1', '=HYPERLINK("x")', author='Bob', like_count=3),
                           Comment('youtube', 'v1', 'c2', 'zweiter', author='Cy', like_count=9)])
            s.save_result(Video('tiktok', 't1', 'https://www.tiktok.com/@u/video/t1', author='Uli'), [])

    def test_missing_db_is_empty_not_an_error(self):
        nope = pathlib.Path(self.tmp.name) / 'none.db'
        self.assertEqual(core.list_videos(nope), []); self.assertEqual(core.counts(nope), (0, 0))

    def test_list_filter_search_and_escape(self):
        self.assertEqual(len(core.list_videos(self.db)), 2)
        self.assertEqual([r[1] for r in core.list_videos(self.db, platform='tiktok')], ['tiktok'])
        self.assertEqual(len(core.list_videos(self.db, search='100%')), 1)
        self.assertEqual(len(core.list_videos(self.db, search='%')), 1)   # nur der Treffer mit echtem %
        self.assertEqual(len(core.list_videos(self.db, search="x' OR 1=1 --")), 0)
        self.assertEqual(core.counts(self.db), (2, 2))

    def test_search_ignores_case_and_umlauts(self):
        with Store(self.db) as s:
            s.save_video(Video('youtube', 'm1', 'https://youtu.be/m1', author='müller', caption='Straße ÄPFEL'))
        for term in ('MÜLLER', 'müller', 'Müller', 'MüLLer'):
            self.assertEqual([r[0] for r in core.list_videos(self.db, search=term)], ['youtube:m1'], term)
        self.assertEqual(len(core.list_videos(self.db, search='äpfel')), 1)
        self.assertEqual(len(core.list_videos(self.db, search='ÄPFEL')), 1)
        self.assertEqual(len(core.list_videos(self.db, search='straSSe')), 1)
        self.assertEqual(len(core.list_videos(self.db, search='100%')), 1)       # Wildcard-Maskierung bleibt
        self.assertEqual(len(core.list_videos(self.db, search='_')), 1)           # nur der Treffer mit echtem _ („_Welt_“)
        self.assertEqual(len(core.list_videos(self.db, search='\\')), 0)

    def test_comments_sorted_by_likes(self):
        self.assertEqual([c[0] for c in core.list_comments(self.db, 'youtube', 'v1')], ['Cy', 'Bob'])

    def test_read_does_not_block_or_modify_writer(self):
        with Store(self.db) as s:
            core.list_videos(self.db)
            s.save_video(Video('youtube', 'v2', 'https://youtu.be/v2'))
        self.assertEqual(core.counts(self.db)[0], 3)

    def test_csv_export_is_excel_safe_and_jsonl_valid(self):
        out = pathlib.Path(self.tmp.name) / 'ex'
        files = core.export_data(self.db, out, 'csv')
        self.assertEqual(files[1].name, 'kommentare.csv')
        with files[1].open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(any(r['Text'] == "'=HYPERLINK(\"x\")" for r in rows))
        files = core.export_data(self.db, out / 'j', 'jsonl')
        recs = [json.loads(l) for l in files[0].read_text(encoding='utf-8').splitlines()]
        self.assertEqual({r['video_id'] for r in recs}, {'v1', 't1'}); self.assertIsInstance(recs[0]['hashtags'], list)
        with self.assertRaises(ValueError): core.export_data(self.db, out, 'xml')


class JobTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        p = patch.dict(os.environ, {'SS_DB_PATH': str(pathlib.Path(tmp.name) / 'scrape.db')}); p.start(); self.addCleanup(p.stop)

    def run_job(self, args, timeout=60):
        lines, done = [], []
        job = core.Job(args, lines.append, done.append); job.start()
        end = time.time() + timeout
        while not done and time.time() < end: time.sleep(0.05)
        return job, lines, done

    def test_runs_real_cli_and_reports_exit_code(self):
        job, lines, done = self.run_job(['scrape', 'https://example.org/x'])
        self.assertEqual(done, [1])  # kein Adapter -> Fehler, aber sauber beendet
        self.assertTrue(any('Kein Adapter' in l for l in lines))

    def test_cancel_kills_process_tree(self):
        import subprocess
        lines, done = [], []
        job = core.Job(['--help'], lines.append, done.append)
        job.proc = subprocess.Popen([core.python_exe(), '-c', 'import time;print("start",flush=True);time.sleep(120)'],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=core.NO_WINDOW | core.NEW_GROUP)
        import threading; threading.Thread(target=job._pump, daemon=True).start()
        time.sleep(1); self.assertTrue(job.running())
        t = time.time(); job.cancel()
        while not done and time.time() - t < 10: time.sleep(0.05)
        self.assertEqual(done, [-1]); self.assertLess(time.time() - t, 8)


@unittest.skipUnless(os.name == 'nt', 'Tk-Fenster nur unter Windows')
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter
        from unittest.mock import patch
        # Tests dürfen die echten Daten des Nutzers nicht berühren: Datenbank und Protokoll in einen Wegwerf-Ordner.
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patches = patch.dict(os.environ, {'SS_DB_PATH': str(pathlib.Path(cls.tmp.name) / 'scrape.db')})
        cls.patches.start()
        try:
            cls.app_mod = __import__('socialscraper.gui', fromlist=['App'])
            from socialscraper.ui import common
            cls.app_mod.GUI_LOG = common.GUI_LOG = pathlib.Path(cls.tmp.name) / 'gui.log'
            cls.app = cls.app_mod.App()
        except tkinter.TclError as error:
            cls.patches.stop(); cls.tmp.cleanup()
            raise unittest.SkipTest(f'Kein Bildschirm: {error}')
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        cls.patches.stop(); cls.tmp.cleanup()

    def pump(self, until, timeout=60):
        end = time.time() + timeout
        while not until() and time.time() < end:
            self.app.update(); time.sleep(0.03)
        self.assertTrue(until(), 'Zeitüberschreitung')

    def test_invalid_input_never_starts_a_job(self):
        from unittest.mock import patch
        a = self.app; t = a.tabs['Abrufen']
        with patch('tkinter.messagebox.showwarning') as warn, patch('tkinter.messagebox.showinfo') as info:
            t.links.delete('1.0', 'end'); t.start_fetch(); info.assert_called()
            t.links.insert('1.0', 'quatsch'); t.start_fetch(); warn.assert_called()
            t.links.delete('1.0', 'end'); t.links.insert('1.0', 'https://youtu.be/a')
            t.max_comments.set(-3); t.start_fetch(); self.assertEqual(warn.call_count, 2)
        self.assertIsNone(a.job)

    def test_tabs_use_the_throwaway_database_not_the_real_one(self):
        a = self.app
        self.assertEqual(core.db_path(), pathlib.Path(os.environ['SS_DB_PATH']))
        a.refresh_tabs()
        self.assertEqual(len(a.tabs['Konten'].tree.get_children()), 4)            # Hauptkonto je Plattform (TikTok, Instagram, Reddit, X), beim ersten Start angelegt
        self.assertEqual(len(a.tabs['Gespeicherte Daten'].tree.get_children()), 0)
        self.assertEqual(list(a.tabs['Abrufen'].acc_boxes['tiktok']['values']),
                         ['Standardkonto', 'Hauptkonto  (Standard)', '🔄 Automatisch wechseln (Rotation)'])

    def test_program_output_is_shown_readable_in_the_log(self):
        a = self.app
        a._clear_log()
        for line in ('', '  ', "{'videos': {'youtube': 1}, 'comments': {'youtube': 5}}", 'fertig: 1 ok, 0 fehler',
                     '04:15:37 ERROR   cli | Ungültiger Cookie-Header'):
            a.events.put(('line', line))
        a._poll()
        shown = a.log_text.get('1.0', 'end').strip().splitlines()
        self.assertEqual(len(shown), 3)                                       # leere Zeilen erscheinen nicht
        self.assertIn('In deiner Sammlung: 1 Video, 5 Kommentare', shown[0]); self.assertNotIn("{'videos'", '\n'.join(shown))
        self.assertIn('Fertig: 1 abgerufen', shown[1])
        self.assertIn('Die Anmeldung konnte aus dieser Datei nicht übernommen werden', shown[2])
        self.assertNotIn('ERROR', shown[2]); self.assertNotIn('cli |', shown[2])
        self.assertFalse(any(not part.strip() or part.strip().replace(':', '').isdigit() for part in shown))   # keine reine Zeitstempel-Zeile

    def test_new_tabs_register_their_buttons_as_busy(self):
        a = self.app
        a.run_steps([('Test', ['scrape', 'https://example.org/x'])], lambda r, c: setattr(a, '_res3', (r, c)))
        acc = a.tabs['Konten']
        self.assertEqual(str(acc.btn['add']['state']), 'disabled'); self.assertEqual(str(a.tabs['Abrufen'].profile_btn['state']), 'disabled')
        self.pump(lambda: a.job is None and not a.steps and hasattr(a, '_res3'))
        self.assertEqual(str(acc.btn['add']['state']), 'normal'); self.assertEqual(str(a.tabs['Abrufen'].profile_btn['state']), 'normal')

    def test_failed_job_is_reported_and_buttons_come_back(self):
        a = self.app; fetch_btn = a.tabs['Abrufen'].fetch_btn
        a.run_steps([('Test', ['scrape', 'https://example.org/x'])], lambda r, c: setattr(a, '_res', (r, c)))
        self.assertEqual(str(a.cancel_btn['state']), 'normal'); self.assertEqual(str(fetch_btn['state']), 'disabled')
        self.pump(lambda: a.job is None and not a.steps and hasattr(a, '_res'))
        self.assertEqual(str(fetch_btn['state']), 'normal'); self.assertEqual(str(a.cancel_btn['state']), 'disabled')
        self.assertEqual(a._res[0][0][1], 1)

    def test_second_job_is_refused_while_running(self):
        from unittest.mock import patch
        a = self.app
        a.run_steps([('A', ['scrape', 'https://example.org/x'])], lambda r, c: None)
        with patch('tkinter.messagebox.showinfo') as info:
            a.run_steps([('B', ['--help'])], lambda r, c: None); info.assert_called_once()
        self.pump(lambda: a.job is None and not a.steps)

    @unittest.skipUnless(os.environ.get('SS_RUN_LOCAL_BROWSER_TESTS') == '1', 'öffnet ein echtes Chrome-Fenster')
    def test_cancel_stops_job(self):
        import shutil
        self.addCleanup(lambda: (shutil.rmtree(core.ROOT / 'data/profiles/gui_test_cancel', ignore_errors=True),
                                 (core.ROOT / 'data/identities/gui_test_cancel.json').unlink(missing_ok=True)))
        # Die echte settings.toml kann einen Proxy-Modus ohne Konten (frei/tor) enthalten; der Login-Befehl
        # würde dann sofort abgelehnt. Der Test braucht einen laufenden Befehl, also Modus „aus“ erzwingen.
        from unittest.mock import patch
        patcher = patch.dict(os.environ, {'SS_PROXY_MODE': 'aus'}); patcher.start(); self.addCleanup(patcher.stop)
        a = self.app
        a.run_steps([('Lang', ['login', '--platform', 'tiktok', '--identity', 'gui_test_cancel', '--mode', 'qr', '--timeout', '60'])],
                    lambda r, c: setattr(a, '_res2', (r, c)))
        time.sleep(1.5); a.update()
        if a.job is None:  # der Befehl endete sofort, z. B. weil ein anderer Browserauftrag die Sperre in data/run.lock haelt
            self.pump(lambda: hasattr(a, '_res2'), 30)
            self.skipTest('Der Login-Befehl endete vor dem Abbruch (Browser-Sperre belegt?)')
        a.cancel()
        self.pump(lambda: hasattr(a, '_res2'), 30)
        self.assertTrue(a._res2[1]); self.assertEqual(str(a.tabs['Abrufen'].fetch_btn['state']), 'normal')


if __name__ == '__main__':
    unittest.main()
