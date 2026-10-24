"""Logik hinter Probleme-Fenster, Merkzettel, Zwischenablage-Hinweis und Hilfetexten (ohne Fenster)."""
import datetime
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import channels, clipboardlogic as cl, helptexts, problemlogic as pl, proxylogic, repo, uistate
from socialscraper.store import Store

NOW = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc)


class ExplainTests(unittest.TestCase):
    def test_known_errors_get_a_headline_and_a_next_step(self):
        cases = {
            'Menschentest (CAPTCHA) auf TikTok erkannt': ('Menschentest', 'Konten'),
            'Reddit hat diese Internetverbindung gesperrt ("blocked by network security").': ('Sicherheitsprüfung', 'Einstellungen'),
            'Die Anmeldung ist abgelaufen': ('Anmeldung', 'Konten'),
            'Zu viele Anfragen (HTTP 429)': ('bremst', None),
            'Zeitüberschreitung: der Server hat nicht rechtzeitig geantwortet': ('nicht rechtzeitig', None),
            'Das Video ist privat, gelöscht oder in diesem Land nicht verfügbar.': ('nicht abrufbar', None),
            'Unterbrochen (Programm beendet)': ('unterbrochen', None),
            'Der Proxy ist nicht erreichbar': ('Proxy', 'Einstellungen'),
            'Die Plattform hat das Konto gesperrt oder eingeschränkt.': ('Konto gesperrt', 'Konten'),
        }
        for text, (word, tab) in cases.items():
            head, todo, goto = pl.explain(text)
            self.assertIn(word.lower(), head.lower(), text)
            self.assertTrue(todo, text)
            self.assertEqual(goto[0] if goto else None, tab, text)

    def test_unknown_error_keeps_a_cleaned_text(self):
        head, todo, goto = pl.explain('Etwas ganz Neues ist passiert')
        self.assertEqual(head, 'Etwas ganz Neues ist passiert')
        self.assertIn('Technische Details', todo)

    def test_is_known(self):
        self.assertTrue(pl.is_known('Menschentest (CAPTCHA)'))
        self.assertFalse(pl.is_known('Etwas ganz Neues'))
        self.assertFalse(pl.is_known(''))
        self.assertFalse(pl.is_known(None))

    def test_empty_error(self):
        head, todo, _g = pl.explain('')
        self.assertTrue(head and todo)

    def test_secrets_are_scrubbed(self):
        text = pl.scrub('Fehler bei http://nutzer:Geheim123@1.2.3.4:8080 mit password=Hunter2 und token: abc.def')
        for secret in ('Geheim123', 'Hunter2', 'abc.def', 'nutzer:'):
            self.assertNotIn(secret, text)
        head, todo, _g = pl.explain('Proxy http://u:Geheim123@h:1 nicht erreichbar')
        self.assertNotIn('Geheim123', head + todo)

    def test_long_text_is_cut(self):
        self.assertLessEqual(len(pl.scrub('x' * 5000)), 300)


class CollectProblemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'data').mkdir()
        self.db_path = self.root / 'data' / 'scrape.db'

    def test_nothing_without_a_database(self):
        self.assertEqual(pl.collect(self.root, self.db_path, now=NOW), [])
        self.assertFalse(self.db_path.exists())

    def seed(self):
        store = Store(str(self.db_path))
        db = store.db
        repo.ensure_default_accounts(db)
        now = repo.now()
        t = repo.add_target(db, 'profile', 'reddit', 'abc', interval_min=60, max_comments=0)

        def run(status, error, minutes_ago):
            rid = repo.start_run(db, t['id'], 'profile reddit abc', 'daemon')
            repo.finish_run(db, rid, status, error=error)
            stamp = repo.iso(now - datetime.timedelta(minutes=minutes_ago))
            db.execute('UPDATE runs SET started_at=?, finished_at=? WHERE id=?', (stamp, stamp, rid))
        run('failed', 'blocked by network security', 30)
        run('partial', 'Menschentest (CAPTCHA)', 120)
        run('partial', None, 130)                       # teilweise ohne Fehlertext: kein Eintrag
        run('ok', None, 10)
        run('failed', 'alt', 60 * 24 * 20)              # älter als sieben Tage
        ref = channels.parse_channel_link('https://www.youtube.com/@kanal')
        ch = channels.create_channel(db, ref)
        repo.add_channel_items(db, ch['id'], 'youtube', [{'url': f'https://example.test/{k}', 'uid': f'youtube:v{k}'} for k in range(3)])
        db.execute("UPDATE channel_items SET status='failed', last_error='Zeitüberschreitung', tried_at=? WHERE id<=2", (repo.iso(now),))
        repo.update_channel(db, ch['id'], backfill_state='paused', pause_until=repo.iso(now + datetime.timedelta(hours=2)), pause_reason='CAPTCHA')
        z = repo.add_account(db, 'tiktok', 'Zweit')
        (self.root / 'data' / 'cookies').mkdir()
        (self.root / 'data' / 'cookies' / f'{z["identity"]}.tiktok.json').write_text(
            json.dumps([{'name': 'sessionid', 'value': 'x'}]), encoding='utf-8')
        db.execute('UPDATE accounts SET cooldown_until=?, cooldown_reason=? WHERE id=?',
                   (repo.iso(now + datetime.timedelta(hours=4)), 'CAPTCHA', z['id']))
        db.commit()
        store.db.close()

    def test_collects_runs_channels_and_accounts(self):
        self.seed()
        items = pl.collect(self.root, self.db_path)
        kinds = [i['kind'] for i in items]
        self.assertEqual(kinds.count('run'), 2)
        self.assertEqual(kinds.count('account'), 1)
        self.assertEqual(kinds.count('channel'), 2)       # Pause und fehlgeschlagene Videos
        times = [i['time'] for i in items]
        self.assertEqual(times, sorted(times, reverse=True))
        for item in items:
            self.assertTrue(item['what'] and item['cause'] and item['todo'], item)
            self.assertNotIn('Traceback', item['cause'] + item['todo'])
            self.assertIn('key', item)
        fail = next(i for i in items if i['kind'] == 'channel' and 'fehlgeschlagen' in i['what'])
        self.assertIn('Fehlgeschlagene erneut', fail['todo'])
        self.assertIn('Internetverbindung', fail['todo'])             # bekannter Fehler: die Lösung steht dabei
        run = next(i for i in items if i['kind'] == 'run' and 'fehlgeschlagen' in i['what'])
        self.assertIn('Sicherheitsprüfung', run['cause'])
        self.assertEqual(run['goto'][0], 'Einstellungen')

    def test_every_item_that_goes_somewhere_has_a_valid_target(self):
        self.seed()
        tabs = {'Konten', 'Einstellungen', 'Automatik', 'Abrufen', 'Gespeicherte Daten'}
        for item in pl.collect(self.root, self.db_path):
            if item.get('goto'):
                self.assertIn(item['goto'][0], tabs, item)

    def test_daemon_log_errors_show_up(self):
        stamp = datetime.datetime.now().astimezone().strftime('%H:%M:%S')
        (self.root / 'data' / 'daemon.log').write_text(
            f'{stamp} INFO daemon | Dienst gestartet\n{stamp} ERROR daemon | Ziel 4 fehlgeschlagen: Zeitüberschreitung http://u:pw123@x:1\n'
            f'{stamp} ERROR daemon | Ziel 4 fehlgeschlagen: Zeitüberschreitung http://u:pw123@x:1\n', encoding='utf-8')
        items = pl.collect(self.root, self.db_path)
        self.assertEqual(len(items), 1)                   # gleiche Meldung nur einmal
        self.assertEqual(items[0]['kind'], 'service')
        self.assertNotIn('pw123', json.dumps(items[0], default=str))

    def test_daemon_log_in_the_real_file_format_is_read(self):
        """commands/daemon._setup_file_log schreibt „2026-10-13 20:08:07,481 ERROR   daemon | …“ (Datum und Millisekunden)."""
        stamp = datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S,481')
        (self.root / 'data' / 'daemon.log').write_text(
            f'{stamp} INFO    daemon | Dienst gestartet\n'
            f'{stamp} ERROR   daemon | Der Hintergrunddienst ist nicht angelaufen: Unbekannte Einstellung: foo\n', encoding='utf-8')
        items = pl.collect(self.root, self.db_path)
        self.assertEqual([i['kind'] for i in items], ['service'])
        self.assertIn('nicht angelaufen', json.dumps(items[0], default=str, ensure_ascii=False))

    def test_old_daemon_log_is_ignored(self):
        path = self.root / 'data' / 'daemon.log'
        path.write_text('10:00:00 ERROR daemon | kaputt\n', encoding='utf-8')
        old = time.time() - 20 * 86400
        os.utime(path, (old, old))
        self.assertEqual(pl.collect(self.root, self.db_path), [])

    def test_unseen_count(self):
        items = [{'kind': 'run', 'key': 'run:1', 'time': NOW}, {'kind': 'run', 'key': 'run:2', 'time': NOW - datetime.timedelta(hours=5)},
                 {'kind': 'account', 'key': 'cool:1', 'time': NOW}, {'kind': 'channel', 'key': 'chanpause:3', 'time': NOW},
                 {'kind': 'channel', 'key': 'chanfail:3', 'time': NOW}]
        self.assertEqual(pl.unseen_count(items, None), 3)             # Konto-Pause und Kanal-Pause sind Zustände, keine Fehler
        self.assertEqual(pl.unseen_count(items, repo.iso(NOW - datetime.timedelta(hours=1))), 2)
        self.assertEqual(pl.unseen_count(items, repo.iso(NOW + datetime.timedelta(hours=1))), 0)
        self.assertEqual(pl.unseen_count([], None), 0)

    def test_corrupt_database_gives_an_empty_list(self):
        self.db_path.write_bytes(b'kaputt' * 100)
        self.assertEqual(pl.collect(self.root, self.db_path), [])


class UiStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / 'data' / 'ui_state.json'

    def test_missing_file_is_an_empty_state(self):
        self.assertEqual(uistate.load(self.path), {})
        self.assertEqual(uistate.UiState(self.path).get('tab'), None)

    def test_save_and_load_roundtrip(self):
        state = uistate.UiState(self.path)
        state.set('tab', 'Konten')
        state.update_in('home', checklist_hidden=True)
        again = uistate.UiState(self.path)
        self.assertEqual(again.get('tab'), 'Konten')
        self.assertTrue(again.get('home', 'checklist_hidden'))
        self.assertEqual(again.get('home', 'nichts', 'vorgabe'), 'vorgabe')

    def test_corrupt_file_is_ignored(self):
        self.path.parent.mkdir(parents=True)
        for content in ('{kaputt', '', '[1,2]', '"text"', 'null'):
            self.path.write_text(content, encoding='utf-8')
            self.assertEqual(uistate.load(self.path), {}, content)

    def test_binary_garbage_and_huge_file_are_ignored(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'\xff\xfe\x00\x01' * 10)
        self.assertEqual(uistate.load(self.path), {})
        self.path.write_text(json.dumps({'tab': 'x' * (uistate.MAX_BYTES + 10)}), encoding='utf-8')
        self.assertEqual(uistate.load(self.path), {})

    def test_wrong_types_and_unknown_keys_are_dropped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({'tab': 5, 'geometry': 'groß', 'password': 'x', 'log_open': 'ja', 'home': {'a': 1}}), encoding='utf-8')
        self.assertEqual(uistate.load(self.path), {'home': {'a': 1}})

    def test_log_open_accepts_none_and_bool(self):
        self.assertEqual(uistate.sanitize({'log_open': None}), {'log_open': None})
        self.assertEqual(uistate.sanitize({'log_open': False}), {'log_open': False})

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        state = uistate.UiState(self.path)
        state.set('tab', 'Hilfe')
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ['ui_state.json'])

    def test_failed_write_does_not_raise_and_keeps_the_old_file(self):
        state = uistate.UiState(self.path)
        state.set('tab', 'Konten')
        with patch('os.replace', side_effect=PermissionError('gesperrt')):
            self.assertFalse(uistate.save(self.path, {'tab': 'Hilfe'}))
        self.assertEqual(uistate.load(self.path)['tab'], 'Konten')
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ['ui_state.json'])

    def test_unwritable_folder_does_not_raise(self):
        blocker = pathlib.Path(self.tmp.name) / 'datei'
        blocker.write_text('x')
        self.assertFalse(uistate.save(blocker / 'ui_state.json', {'tab': 'x'}))

    def test_set_same_value_does_not_rewrite(self):
        state = uistate.UiState(self.path)
        state.set('tab', 'Konten')
        stamp = self.path.stat().st_mtime_ns
        state.set('tab', 'Konten')
        self.assertEqual(self.path.stat().st_mtime_ns, stamp)

    def test_no_secret_keys_exist_in_the_schema(self):
        for key in uistate.SCHEMA:
            for word in ('pass', 'token', 'key', 'secret', 'cookie'):
                self.assertNotIn(word, key.lower())

    def test_clamp_geometry(self):
        ok = {'w': 1000, 'h': 700, 'x': 50, 'y': 40}
        self.assertEqual(uistate.clamp_geometry(ok, 1920, 1200), (1000, 700, 50, 40))
        self.assertEqual(uistate.clamp_geometry({'w': 300, 'h': 200, 'x': 0, 'y': 0}, 1920, 1200)[:2], (860, 620))     # nie kleiner als das Minimum
        self.assertEqual(uistate.clamp_geometry({'w': 5000, 'h': 5000, 'x': 0, 'y': 0}, 1920, 1200)[:2], (1920, 1200))
        self.assertEqual(uistate.clamp_geometry(dict(ok, x=5000), 1920, 1200), (1000, 700, None, None))     # Bildschirm weg
        self.assertEqual(uistate.clamp_geometry(dict(ok, x=-3000), 1920, 1200), (1000, 700, None, None))
        self.assertEqual(uistate.clamp_geometry(dict(ok, y=1190), 1920, 1200), (1000, 700, None, None))
        self.assertEqual(uistate.clamp_geometry({'w': 1000, 'h': 700}, 1920, 1200), (1000, 700, None, None))
        self.assertIsNone(uistate.clamp_geometry({'w': 'x', 'h': 1}, 1920, 1200))
        self.assertIsNone(uistate.clamp_geometry(None, 1920, 1200))
        self.assertIsNone(uistate.clamp_geometry('x', 1920, 1200))


class ClipboardTests(unittest.TestCase):
    def test_single_video_link(self):
        info = cl.inspect('https://www.youtube.com/watch?v=abc123XYZ99')
        self.assertEqual((info['kind'], info['platforms']), ('video', ['youtube']))
        self.assertEqual(info['text'], 'In der Zwischenablage ist ein YouTube-Link.')

    def test_several_links(self):
        info = cl.inspect('https://youtu.be/abc123XYZ99\nhttps://www.tiktok.com/@a/video/7000000000000000001\n')
        self.assertEqual(len(info['links']), 2)
        self.assertIn('2 Links', info['text'])
        self.assertIn('YouTube und TikTok', info['text'])

    def test_channel_link_is_offered_as_channel(self):
        info = cl.inspect('https://www.youtube.com/@beispielkanal')
        self.assertEqual(info['kind'], 'channel')
        self.assertIn('Kanal-Link', info['text'])

    def test_mixed_video_and_channel_is_not_offered(self):
        self.assertIsNone(cl.inspect('https://www.youtube.com/@kanal\nhttps://youtu.be/abc123XYZ99'))

    def test_other_text_is_never_offered(self):
        for text in ('', '   ', 'hallo welt', 'schau mal https://youtu.be/abc123XYZ99 an', 'https://example.org/x',
                     'https://youtu.be/abc123XYZ99\nnoch ein Satz', 'Passwort: geheim123', 'x' * 30000, None, 5):
            self.assertIsNone(cl.inspect(text), repr(text)[:40])

    def test_lookalike_hosts_are_not_offered(self):
        for text in ('https://youtube.com.evil.com/watch?v=abc123XYZ99', 'https://evil.com/?u=https://youtu.be/abc123XYZ99',
                     'https://user:pw@www.youtube.com/watch?v=abc123XYZ99'):
            self.assertIsNone(cl.inspect(text), text)

    def test_too_many_lines(self):
        self.assertIsNone(cl.inspect('\n'.join(f'https://youtu.be/abc123XYZ{i:02d}' for i in range(40))))

    def test_fingerprint_is_stable_and_does_not_contain_the_text(self):
        self.assertEqual(cl.fingerprint('abc'), cl.fingerprint('abc'))
        self.assertNotEqual(cl.fingerprint('abc'), cl.fingerprint('abd'))
        self.assertIsInstance(cl.fingerprint('abc'), int)

    def test_split_links(self):
        text = 'Schau https://youtu.be/abc123XYZ99, und (https://www.tiktok.com/@a/video/7000000000000000001). Doppelt https://youtu.be/abc123XYZ99'
        self.assertEqual(cl.split_links(text), ['https://youtu.be/abc123XYZ99', 'https://www.tiktok.com/@a/video/7000000000000000001'])
        self.assertEqual(cl.split_links('kein Link'), [])
        self.assertEqual(cl.split_links(None), [])

    def test_merge_into(self):
        self.assertEqual(cl.merge_into('', ['a', 'b']), 'a\nb')
        self.assertEqual(cl.merge_into('x\n', ['a']), 'x\na')
        self.assertEqual(cl.merge_into('a', ['a', 'b']), 'a\nb')
        self.assertEqual(cl.merge_into('a', ['a']), 'a')
        self.assertTrue(cl.all_present('a\nb', ['a', 'b']))
        self.assertFalse(cl.all_present('a', ['a', 'b']))


class HelpTextTests(unittest.TestCase):
    def test_glossary_covers_the_hard_words(self):
        for key in ('proxy_mode', 'rotation', 'cooldown', 'pace', 'follow', 'creator', 'commenter', 'ai_rating', 'groups', 'daemon'):
            self.assertIn(key, helptexts.GLOSSARY)

    def test_explanations_are_short_and_plain(self):
        for key, (name, text) in helptexts.GLOSSARY.items():
            self.assertTrue(name and text, key)
            self.assertLessEqual(text.count('. ') + text.count('.'), 6, key)
            self.assertLess(len(text), 330, key)
            self.assertTrue(text.endswith('.'), key)
            for word in ('Traceback', 'API', 'Thread', 'SQL', 'Token'):
                self.assertNotIn(word, text, key)

    def test_tip_and_term(self):
        self.assertTrue(helptexts.tip('rotation').startswith('Rotation: '))
        self.assertEqual(helptexts.term('cooldown')[0], 'Abkühlung')

    def test_first_steps_are_five(self):
        self.assertEqual(len(helptexts.FIRST_STEPS), 5)

    def test_problem_actions_point_to_real_tabs(self):
        tabs = {'Konten', 'Einstellungen', 'Automatik', 'Abrufen', 'Gespeicherte Daten', 'Probleme'}
        sections = {'proxys', 'ki', 'system', 'abruf', 'dienst'}
        for question, answers, action in helptexts.PROBLEMS:
            self.assertTrue(answers)
            if action:
                self.assertIn(action[1], tabs, question)
                if action[1] == 'Einstellungen':
                    self.assertIn(action[2], sections, question)

    def test_help_covers_the_marc_problems(self):
        questions = ' '.join(q for q, _a, _x in helptexts.PROBLEMS)
        for word in ('Anmeldung', 'Abruf', 'CAPTCHA', 'Proxy'):
            self.assertIn(word, questions)

    def test_shortcuts_list_the_real_ones(self):
        keys = {k for k, _w in helptexts.SHORTCUTS}
        for key in ('F1', 'Strg+Enter', 'Strg+S', 'Strg+A', 'Esc', 'Enter'):
            self.assertIn(key, keys)

    def test_mode_explanations_cover_all_modes(self):
        for mode in proxylogic.MODE_TITLES:
            self.assertIn(mode, helptexts.MODE_EXPLAIN)


class SetModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'data').mkdir()
        os.environ.pop('SS_PROXY_MODE', None)
        text = '# Kommentar bleibt\nmax_comments = 20\nproxy_mode = "tor"\n'
        (self.root / 'settings.toml').write_text(text, encoding='utf-8', newline='')

    def test_set_mode_writes_like_the_settings_form(self):
        self.assertEqual(proxylogic.current_mode(self.root), 'tor')
        self.assertEqual(proxylogic.set_mode(self.root, 'aus'), 'aus')
        self.assertEqual(proxylogic.current_mode(self.root), 'aus')
        text = (self.root / 'settings.toml').read_text(encoding='utf-8')
        self.assertIn('# Kommentar bleibt', text)
        self.assertIn('max_comments = 20', text)
        self.assertTrue((self.root / 'settings.toml.bak').is_file())

    def test_invalid_mode_changes_nothing(self):
        before = (self.root / 'settings.toml').read_bytes()
        with self.assertRaises(ValueError):
            proxylogic.set_mode(self.root, 'quatsch')
        self.assertEqual((self.root / 'settings.toml').read_bytes(), before)

    def test_environment_variable_wins_and_is_reported(self):
        with patch.dict(os.environ, {'SS_PROXY_MODE': 'tor'}):
            with self.assertRaises(ValueError) as caught:
                proxylogic.set_mode(self.root, 'aus')
        self.assertIn('SS_PROXY_MODE', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
