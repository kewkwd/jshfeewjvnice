"""Tests für die Einstellungs-Oberfläche: Schreiber/Leser, Prüfung, Hilfen (ohne Tk) und der Tab selbst (Fenstertest).

Alles läuft in Wegwerf-Ordnern; die echte settings.toml, data/integration.json und die echte Datenbank
werden nicht angefasst.
"""
import contextlib
import gc
import json
import os
import pathlib
import socket
import sqlite3
import sys
import tempfile
import threading
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import formlogic as fl
from socialscraper import guicore as core
from socialscraper.config import load as load_config
from socialscraper.models import Comment, Video
from socialscraper.store import Store

SOURCE = ('# Kommentar oben\n'
          'backend = "local"\n'
          'headless = true  # unsichtbar\n'
          'max_comments = 500\n'
          'proxies = []\n'
          '# proxies = ["http://user:pw@gw.provider.io:8000?country=us"]\n'
          'notify_file = "data/alerts.log"\n'
          'notify_webhook = ""\n'
          'session_max_age = 24.0\n'
          '\n'
          '[sphere_presets]\n'
          'tiktok = ""\n'
          'max_comments = 7\n')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        env = patch.dict(os.environ, {'SS_DB_PATH': str(self.root / 'data' / 'scrape.db')})
        env.start()
        self.addCleanup(env.stop)

    def put(self, text, crlf=False):
        data = text.replace('\n', '\r\n') if crlf else text
        (self.root / 'settings.toml').write_bytes(data.encode('utf-8'))

    def raw(self):
        return (self.root / 'settings.toml').read_bytes().decode('utf-8')


class WriterTests(Base):
    def test_keeps_comments_trailing_comment_and_other_tables(self):
        self.put(SOURCE)
        core.write_settings({'headless': False, 'max_comments': 20}, self.root)
        out = self.raw()
        self.assertIn('# Kommentar oben\n', out)
        self.assertIn('headless = false  # unsichtbar\n', out)
        self.assertIn('# proxies = ["http://user:pw@gw.provider.io:8000?country=us"]\n', out)
        self.assertIn('[sphere_presets]\ntiktok = ""\nmax_comments = 7\n', out)
        data = tomllib.loads(out)
        self.assertEqual((data['max_comments'], data['headless']), (20, False))
        self.assertEqual(data['sphere_presets']['max_comments'], 7)

    def test_crlf_stays_crlf_and_lf_stays_lf(self):
        self.put(SOURCE, crlf=True)
        core.write_settings({'max_comments': 11, 'proxies': ['http://a:b@h.de:80']}, self.root)
        raw = (self.root / 'settings.toml').read_bytes()
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        self.assertIn(b'max_comments = 11\r\n', raw)
        self.put(SOURCE)
        core.write_settings({'max_comments': 12}, self.root)
        self.assertNotIn('\r', self.raw())

    def test_all_value_kinds_round_trip(self):
        self.put(SOURCE)
        values = {'max_comments': 0, 'max_retries': 5, 'request_timeout': 45, 'snapshot': False, 'headless': False,
                  'require_auth': True, 'session_max_age': 36.5, 'retry_backoff': 3, 'log_level': 'DEBUG',
                  'engine': 'nodriver', 'notify_file': 'C:\\Users\\Ä "x"\\a.log', 'notify_webhook': 'https://hooks.example.com/a?b=c#d',
                  'proxies': ['http://u:p@h.de:80', 'socks5://10.0.0.1:1080']}
        core.write_settings(values, self.root)
        cfg = core.read_settings(self.root)
        for key, value in values.items():
            self.assertEqual(getattr(cfg, key), value, key)

    def test_special_characters_in_strings(self):
        self.put(SOURCE)
        text = 'Ärger "quoted" \\ back # hash \t tab äöüß 😀 \x7f'
        core.write_settings({'notify_file': text}, self.root)
        self.assertEqual(tomllib.loads(self.raw())['notify_file'], text)

    def test_replaces_multiline_list_completely(self):
        self.put('max_comments = 5\nproxies = [\n  "http://a:b@h.de:1",  # eins\n  "http://c:d@h.de:2",\n]\nheadless = true\n')
        core.write_settings({'proxies': ['http://x:y@z.de:3']}, self.root)
        data = tomllib.loads(self.raw())
        self.assertEqual(data['proxies'], ['http://x:y@z.de:3'])
        self.assertEqual((data['max_comments'], data['headless']), (5, True))
        self.assertEqual(self.raw().count('proxies'), 1)

    def test_missing_key_is_added_before_first_table(self):
        self.put('backend = "local"\n\n[sphere_presets]\ntiktok = ""\n')
        core.write_settings({'snapshot': False, 'proxies': ['http://a:b@h.de:1']}, self.root)
        out = self.raw()
        self.assertLess(out.index('snapshot'), out.index('[sphere_presets]'))
        data = tomllib.loads(out)
        self.assertEqual(data['snapshot'], False)
        self.assertEqual(data['sphere_presets'], {'tiktok': ''})
        self.assertIn('\n\n[sphere_presets]', out)

    def test_empty_or_missing_file_gets_created(self):
        core.write_settings({'max_comments': 9}, self.root)
        self.assertEqual(core.read_settings(self.root).max_comments, 9)

    def test_key_inside_table_is_not_touched(self):
        self.put(SOURCE)
        core.write_settings({'max_comments': 1}, self.root)
        self.assertEqual(tomllib.loads(self.raw())['sphere_presets']['max_comments'], 7)

    def test_rejects_wrong_types_and_keeps_file(self):
        self.put(SOURCE)
        before = self.raw()
        bad = [{'max_comments': -1}, {'max_comments': True}, {'max_comments': '5'}, {'headless': 1}, {'proxies': 'x'},
               {'proxies': [1]}, {'session_max_age': float('nan')}, {'session_max_age': 0}, {'session_max_age': True},
               {'log_level': 5}, {'notify_file': None}, {'unbekannt': 1}, {'sphere_presets': {}}]
        for values in bad:
            with self.assertRaises(ValueError, msg=str(values)):
                core.write_settings(values, self.root)
        self.assertEqual(self.raw(), before)

    def test_rejects_values_config_would_refuse_and_keeps_file(self):
        self.put(SOURCE)
        before = self.raw()
        for values in ({'engine': 'unsupported-browser'}, {'max_retries': 0}, {'request_timeout': 0}, {'notify_file': '  '}):
            with self.assertRaises(ValueError, msg=str(values)):
                core.write_settings(values, self.root)
        self.assertEqual(self.raw(), before)
        self.assertFalse((self.root / 'settings.toml.tmp').exists())

    def test_broken_existing_file_is_not_touched(self):
        self.put('max_comments = [[[\n')
        with self.assertRaises(ValueError) as ctx:
            core.write_settings({'max_comments': 3}, self.root)
        self.assertIn('beschädigt', str(ctx.exception))
        self.assertEqual(self.raw(), 'max_comments = [[[\n')

    def test_locked_file_gives_clear_message(self):
        self.put(SOURCE)
        with patch('socialscraper.guicore.os.replace', side_effect=PermissionError('x')), patch('socialscraper.guicore.time.sleep'):
            with self.assertRaises(ValueError) as ctx:
                core.write_settings({'max_comments': 3}, self.root)
        self.assertIn('gesperrt', str(ctx.exception))
        self.assertEqual(self.raw(), SOURCE)
        self.assertFalse((self.root / 'settings.toml.tmp').exists())

    def test_backup_and_undo(self):
        self.put(SOURCE)
        core.write_settings({'max_comments': 20}, self.root)
        self.assertEqual(tomllib.loads((self.root / 'settings.toml.bak').read_text(encoding='utf-8'))['max_comments'], 500)
        core.write_settings({'max_comments': 30}, self.root)
        core.restore_settings_backup(self.root)
        self.assertEqual(core.read_settings(self.root).max_comments, 20)
        core.restore_settings_backup(self.root)   # „Rückgängig“ lässt sich selbst rückgängig machen
        self.assertEqual(core.read_settings(self.root).max_comments, 30)

    def test_undo_without_backup_or_with_bad_backup(self):
        self.put(SOURCE)
        with self.assertRaises(ValueError):
            core.restore_settings_backup(self.root)
        (self.root / 'settings.toml.bak').write_text('engine = "unsupported-browser"\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            core.restore_settings_backup(self.root)
        self.assertEqual(self.raw(), SOURCE)

    def test_reset_keeps_a_copy_of_the_broken_file(self):
        self.put('kaputt = [[\n')
        core.reset_settings(fl.DEFAULT_SETTINGS_TEXT, self.root)
        self.assertEqual((self.root / 'settings.toml.defekt').read_text(encoding='utf-8'), 'kaputt = [[\n')
        cfg = core.read_settings(self.root)
        self.assertEqual((cfg.max_comments, cfg.headless), (500, True))

    def test_default_template_matches_real_settings_file_defaults(self):
        self.put(fl.DEFAULT_SETTINGS_TEXT)
        cfg = core.read_settings(self.root)
        self.assertEqual((cfg.engine, cfg.backend, cfg.proxies), ('patchright', 'local', []))

    def test_old_api_still_works(self):
        self.put('max_comments = 5\n')
        core.write_settings({'snapshot': False}, self.root)
        self.assertFalse(core.read_settings(self.root).snapshot)
        for bad in ({'max_comments': -1}, {'headless': 1}, {'proxies': 'x'}):
            with self.assertRaises(ValueError):
                core.write_settings(bad, self.root)


class ValueTests(unittest.TestCase):
    def test_proxy_formats(self):
        good = ['http://gw.provider.io:8000', 'http://user:pw@gw.provider.io:8000?country=us', 'https://h.de:443',
                'socks5://10.0.0.5:1080', 'socks5h://u:p@[::1]:1080', 'http://user-session-{identity}-country-{country}:pw@h.de:80',
                'http://u:p%40ss@h.de:80', 'socks4://h.de:1']
        for p in good:
            self.assertEqual(fl.parse_proxy(p), (p, None), p)
        self.assertEqual(fl.parse_proxy('  http://h.de:80  ')[0], 'http://h.de:80')
        bad = ['', '   ', 'h.de:80', 'ftp://h.de:80', 'http://h.de', 'http://h.de:99999', 'http://h.de:0', 'http://:80',
               'http://a b@h.de:80', 'http://h.de:80 x', 'http//h.de:80', 'http://u@p@h.de:80']
        for p in bad:
            self.assertIsNone(fl.parse_proxy(p)[0], p)
            self.assertTrue(fl.parse_proxy(p)[1], p)

    def test_proxy_error_text_never_contains_the_password(self):
        for p in ('http://user:Geheim123@h.de', 'ftp://user:Geheim123@h.de:80', 'http://user:Geheim123@h.de:99999'):
            self.assertNotIn('Geheim123', fl.parse_proxy(p)[1])

    def test_mask_proxy(self):
        self.assertEqual(fl.mask_proxy('http://kunde:geheim@gw.de:8000?country=de'), 'http://kunde:*****@gw.de:8000?country=de')
        self.assertEqual(fl.mask_proxy('socks5://10.0.0.5:1080'), 'socks5://10.0.0.5:1080')
        self.assertEqual(fl.mask_proxy('http://nur@h.de:80'), 'http://nur@h.de:80')
        self.assertNotIn('geheim', fl.mask_proxy('http://kunde:geheim@gw.de:8000'))

    def test_webhook(self):
        self.assertEqual(fl.validate_webhook(''), ('', None))
        self.assertEqual(fl.validate_webhook(' https://hooks.example.com/x ')[0], 'https://hooks.example.com/x')
        self.assertEqual(fl.validate_webhook('http://localhost:9000/x')[0], 'http://localhost:9000/x')
        for bad in ('http://example.com/x', 'hooks.example.com', 'https://', 'https://u:p@example.com/x', 'https://a b.de', 'ftp://x.de'):
            self.assertIsNone(fl.validate_webhook(bad)[0], bad)

    def test_origin(self):
        for good in ('http://localhost:5173', 'https://pulse.example.com', 'http://127.0.0.1:3000/', 'http://[::1]:8080'):
            self.assertIsNotNone(fl.validate_origin(good)[0], good)
        self.assertEqual(fl.validate_origin('http://localhost:5173/')[0], 'http://localhost:5173')
        for bad in ('*', '', 'localhost:5173', 'http://x.de/pfad', 'http://x.de:99999', 'javascript:alert(1)', 'http://a b'):
            self.assertIsNone(fl.validate_origin(bad)[0], bad)

    def test_notify_file(self):
        for good in ('data/alerts.log', 'C:\\Users\\x\\alerts.log', 'meldungen.txt'):
            self.assertEqual(fl.validate_notify_file(good)[0], good)
        for bad in ('', '  ', 'data/', 'a<b', 'a|b', 'a?b', 'x' * 300, 'data:alerts', '1:\\x'):
            self.assertIsNone(fl.validate_notify_file(bad)[0], bad)

    def test_numbers(self):
        self.assertEqual(fl.parse_int(' 12 ', 0, 20)[0], 12)
        for bad in ('', 'abc', '1.5', '-1', '21', '1e3', '٣'):
            self.assertIsNone(fl.parse_int(bad, 0, 20)[0], bad)
        self.assertEqual(fl.parse_float('24,5', 1, 720)[0], 24.5)
        self.assertEqual(fl.parse_float('24', 1, 720)[0], 24.0)
        for bad in ('', '0', '0,5', 'nan', 'inf', '721', '-3', 'abc', '1e2'):
            self.assertIsNone(fl.parse_float(bad, 1, 720)[0], bad)

    def test_token(self):
        self.assertEqual(fl.validate_token('', False), ('', None))
        self.assertTrue(fl.validate_token('', True)[1])
        self.assertTrue(fl.validate_token('kurz', False)[1])
        self.assertTrue(fl.validate_token('x' * 30 + ' y', False)[1])
        self.assertEqual(fl.validate_token('x' * 40, True), ('x' * 40, None))

    def test_cloud_url(self):
        self.assertEqual(fl.validate_cloud_url('https://abc.supabase.co/')[0], 'https://abc.supabase.co')
        self.assertEqual(fl.validate_cloud_url('http://localhost:54321')[0], 'http://localhost:54321')
        for bad in ('', 'http://abc.supabase.co', 'abc.supabase.co', 'https://u:p@abc.co', 'ftp://abc.supabase.co',
                    'https://', 'https://abc', 'https://ab c.supabase.co', 'https://abc.co/pfad mit leerzeichen', 'Hallo Welt'):
            value, error = fl.validate_cloud_url(bad)
            self.assertIsNone(value, bad); self.assertTrue(error, bad)         # immer mit klarer Meldung
        self.assertIn('https://', fl.validate_cloud_url('http://abc.supabase.co')[1])

    def test_cloud_url_is_cleaned_up(self):
        good = 'https://abc.supabase.co'
        for text in ('  https://abc.supabase.co  ', 'https://abc.supabase.co/', 'https://abc.supabase.co///',
                     'https://abc.supabase.co/rest/v1', 'https://abc.supabase.co/rest/v1/', 'https://abc.supabase.co/REST/v1/videos',
                     'https://abc.supabase.co/?apikey=geheim', 'https://abc.supabase.co/rest/v1/?select=*#x',
                     'https://ABC.supabase.co:443/rest/v1/'.replace(':443', '')):
            self.assertEqual(fl.validate_cloud_url(text), (good, None), text)
        self.assertEqual(fl.validate_cloud_url('http://localhost:54321/rest/v1/')[0], 'http://localhost:54321')
        self.assertEqual(fl.validate_cloud_url('https://host.example/mein/prefix/rest/v1')[0], 'https://host.example/mein/prefix')
        self.assertEqual(fl.validate_cloud_url('https://host.example/rest/v10')[0], 'https://host.example/rest/v10')
        self.assertNotIn('geheim', fl.validate_cloud_url('https://abc.supabase.co/?apikey=geheim')[0])


class FormTests(unittest.TestCase):
    def good(self):
        raw = fl.default_raw()
        raw.update({'api_enabled': True, 'api_token': 't' * 32, 'api_origins': ['http://localhost:5173'],
                    'sync_kind': 'supabase', 'sync_url': 'https://abc.supabase.co', 'sync_key': 'k' * 40})
        return raw

    def test_defaults_are_valid(self):
        settings, patch_, errors = fl.validate_form(fl.default_raw())
        self.assertEqual(errors, {})
        self.assertEqual(settings['max_comments'], 500)
        self.assertEqual(patch_['api']['enabled'], False)

    def test_full_valid_form(self):
        settings, patch_, errors = fl.validate_form(self.good())
        self.assertEqual(errors, {})
        self.assertEqual(patch_['api']['cors_origins'], ['http://localhost:5173'])
        self.assertEqual(patch_['sync']['url'], 'https://abc.supabase.co')

    def test_each_invalid_field_reports_its_own_key(self):
        cases = {'max_comments': '-1', 'max_retries': '0', 'request_timeout': '1', 'session_max_age': '0',
                 'log_level': 'LAUT', 'engine': 'x', 'notify_file': '', 'notify_webhook': 'http://x.de',
                 'api_port': '80', 'api_token': 'kurz', 'sync_kind': 'ftp'}
        for key, value in cases.items():
            raw = self.good()
            raw[key] = value
            self.assertIn(key, fl.validate_form(raw)[2], key)
        raw = self.good(); raw['proxies'] = ['kaputt']
        self.assertIn('proxies', fl.validate_form(raw)[2])
        raw = self.good(); raw['api_origins'] = ['*']
        self.assertIn('api_origins', fl.validate_form(raw)[2])
        raw = self.good(); raw['sync_url'] = 'http://x.de'
        self.assertIn('sync_url', fl.validate_form(raw)[2])
        raw = self.good(); raw['sync_key'] = ''
        self.assertIn('sync_key', fl.validate_form(raw)[2])
        raw = self.good(); raw['sync_key'] = 'a b'
        self.assertIn('sync_key', fl.validate_form(raw)[2])

    def test_enabled_api_needs_token_but_disabled_may_be_empty(self):
        raw = self.good(); raw['api_token'] = ''
        self.assertIn('api_token', fl.validate_form(raw)[2])
        raw['api_enabled'] = False
        self.assertNotIn('api_token', fl.validate_form(raw)[2])

    def test_sync_off_ignores_url_and_key(self):
        raw = self.good(); raw.update({'sync_kind': 'none', 'sync_url': 'quatsch', 'sync_key': ''})
        self.assertEqual(fl.validate_form(raw)[2], {})

    def test_duplicates_are_removed(self):
        raw = self.good()
        raw['proxies'] = ['http://a:b@h.de:1', 'http://a:b@h.de:1']
        raw['api_origins'] = ['http://localhost:5173', 'http://localhost:5173/']
        settings, patch_, errors = fl.validate_form(raw)
        self.assertEqual(errors, {})
        self.assertEqual(len(settings['proxies']), 1)
        self.assertEqual(len(patch_['api']['cors_origins']), 1)

    def test_error_messages_never_contain_secrets(self):
        raw = self.good()
        raw.update({'proxies': ['http://u:GeheimPw@h.de'], 'api_token': 'Geheimtoken Leer', 'sync_key': 'Geheim Key',
                    'notify_webhook': 'http://geheim.example/Webhook'})
        errors = fl.validate_form(raw)[2]
        text = json.dumps(errors, ensure_ascii=False)
        for secret in ('GeheimPw', 'Geheimtoken', 'Geheim Key', 'Webhook'):
            self.assertNotIn(secret, text)

    def test_roundtrip_settings_to_raw_and_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'settings.toml'
            p.write_text(SOURCE.replace('proxies = []', 'proxies = ["http://a:b@h.de:1"]'), encoding='utf-8')
            cfg = load_config(str(p))
        raw = fl.default_raw(); raw.update(fl.settings_to_raw(cfg))
        settings, _patch, errors = fl.validate_form(raw)
        self.assertEqual(errors, {})
        self.assertEqual(settings['proxies'], ['http://a:b@h.de:1'])
        self.assertEqual(settings['session_max_age'], 24.0)
        self.assertEqual(raw['session_max_age'], '24')

    def test_changed_keys_and_sections(self):
        a, b = fl.default_raw(), fl.default_raw()
        self.assertEqual(fl.changed_keys(a, b), [])
        b['proxies'] = ['http://a:b@h.de:1']; b['api_port'] = '9000'
        self.assertEqual(fl.changed_sections(a, b), {'proxys', 'pulse'})
        text = fl.describe_changes(fl.changed_keys(a, b))
        self.assertNotIn('9000', text); self.assertNotIn('a:b', text)


class HelperTests(Base):
    def test_port_in_use(self):
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(1)
        self.addCleanup(sock.close)
        self.assertTrue(fl.port_in_use(sock.getsockname()[1]))
        free = socket.socket(); free.bind(('127.0.0.1', 0)); port = free.getsockname()[1]; free.close()
        self.assertFalse(fl.port_in_use(port))

    def test_example_url(self):
        self.assertEqual(fl.example_url(8765), 'http://127.0.0.1:8765/v1/stats')
        self.assertEqual(fl.health_url(1, '::1'), 'http://[::1]:1/v1/health')

    def test_check_api_nobody_listening(self):
        free = socket.socket(); free.bind(('127.0.0.1', 0)); port = free.getsockname()[1]; free.close()
        ok, text = fl.check_api(port, 'x' * 30, timeout=2)
        self.assertFalse(ok)
        self.assertIn('Hintergrunddienst', text)
        self.assertNotIn('Traceback', text)

    def test_check_api_foreign_service(self):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"hallo": 1}'
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass
        server = HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        ok, text = fl.check_api(server.server_address[1], 'x' * 30, timeout=3)
        self.assertFalse(ok); self.assertIn('anderes Programm', text)

    def test_check_api_against_the_real_interface(self):
        from socialscraper import api
        token = 'T' * 32
        with Store(self.root / 'data' / 'scrape.db'):
            pass
        handle = api.start(self.root, port=0, token=token, cfg=_api_cfg())
        self.addCleanup(handle.stop)
        ok, text = fl.check_api(handle.port, token, timeout=5)
        self.assertTrue(ok, text)
        self.assertIn('Token passt', text)
        ok, text = fl.check_api(handle.port, 'F' * 32, timeout=5)
        self.assertFalse(ok); self.assertIn('Token', text)

    def test_backup_database(self):
        src = self.root / 'data' / 'scrape.db'
        with Store(src) as s:
            s.save_result(Video('youtube', 'v1', 'https://youtu.be/v1'), [Comment('youtube', 'v1', 'c1', 'hi'), Comment('youtube', 'v1', 'c2', 'ho')])
            dest = self.root / 'sicherung ä.db'
            result = fl.backup_database(src, dest)   # während die Datenbank offen ist
        self.assertEqual((result['videos'], result['comments']), (1, 2))
        self.assertGreater(result['bytes'], 0)
        with contextlib.closing(sqlite3.connect(dest)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM comments').fetchone()[0], 2)
        self.assertFalse((self.root / 'sicherung ä.db.tmp').exists())

    def test_backup_errors(self):
        with self.assertRaises(ValueError):
            fl.backup_database(self.root / 'nope.db', self.root / 'x.db')
        src = self.root / 'data' / 'scrape.db'
        Store(src).close()
        with self.assertRaises(ValueError):
            fl.backup_database(src, src)
        with self.assertRaises(ValueError):
            fl.backup_database(src, self.root / 'gibtsnicht' / 'x.db')
        (self.root / 'kaputt.db').write_bytes(b'das ist keine datenbank' * 50)
        with self.assertRaises(ValueError) as ctx:
            fl.backup_database(self.root / 'kaputt.db', self.root / 'k2.db')
        self.assertFalse((self.root / 'k2.db').exists())
        self.assertNotIn('Traceback', str(ctx.exception))

    def test_human_size(self):
        self.assertEqual(fl.human_size(10), '10 Byte')
        self.assertEqual(fl.human_size(2048), '2,0 KB')


def _api_cfg():
    from socialscraper import integration
    cfg = integration.default_config()
    cfg['api'].update({'enabled': True, 'token': 'T' * 32})
    return cfg


class SummaryTests(unittest.TestCase):
    def test_summarize_sync(self):
        from socialscraper.ui.tab_settings import summarize_sync
        text, kind = summarize_sync('push', 0, ['  Videos: 3 gesendet', '  Kommentare: 10 gesendet', 'Fertig: 13 Zeilen gesendet.', 'INFO x'])
        self.assertEqual(kind, 'ok'); self.assertIn('Videos: 3', text); self.assertNotIn('INFO', text)
        text, kind = summarize_sync('test', 1, ['Fehler: Schlüssel abgelehnt', '  Videos: fehlt'])
        self.assertEqual(kind, 'err'); self.assertIn('abgelehnt', text)
        text, kind = summarize_sync('pull', 1, [])
        self.assertEqual(kind, 'err'); self.assertIn('Details im Protokoll', text)


# ---------- Fenstertests ----------

try:
    import tkinter
    from ui_fake import FakeApp
    from socialscraper.ui.tab_settings import SettingsTab
except Exception:  # pragma: no cover
    FakeApp = None


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class SettingsTabTests(Base):
    def setUp(self):
        super().setUp()
        try:
            self.app = FakeApp(self.root, settings_text=SOURCE)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self._close)
        for name in ('showinfo', 'showwarning', 'showerror'):
            p = patch(f'tkinter.messagebox.{name}'); setattr(self, name, p.start()); self.addCleanup(p.stop)
        p = patch('tkinter.messagebox.askyesno', return_value=True); self.ask = p.start(); self.addCleanup(p.stop)
        self.tab = self.app.add('Einstellungen', SettingsTab)
        self.app.update()

    def _close(self):
        # Tk-Variablen dürfen nicht in einem Hintergrund-Thread aufgeräumt werden: Fenster hier schließen, Müll im Haupt-Thread sammeln.
        tab = getattr(self, 'tab', None)
        if tab is not None and hasattr(tab, 'loading'):
            self.app.pump(lambda: not tab.loading, 5)
        self.app.destroy()
        self.app = self.tab = None
        gc.collect()

    def test_builds_with_values_from_file_and_is_clean(self):
        t = self.tab
        self.assertEqual(t.vars['max_comments'].get(), '500')
        self.assertTrue(t.vars['headless'].get())
        self.assertFalse(t.dirty)
        self.assertEqual(str(t.save_btn['state']), 'disabled')
        self.assertEqual(t.state_label['text'], 'Alles gespeichert.')
        self.assertEqual(t.choice_vars['engine'].get(), 'Chrome / Patchright (empfohlen)')

    def test_edit_marks_unsaved_and_reset_restores(self):
        t = self.tab
        t.vars['max_comments'].set('42')
        self.assertTrue(t.dirty)
        self.assertEqual(str(t.save_btn['state']), 'normal')
        self.assertIn('Ungespeichert', t.state_label['text'])
        self.assertIn('●', t.nav.get(0))
        t.reset()
        self.assertFalse(t.dirty); self.assertEqual(t.vars['max_comments'].get(), '500')
        self.assertEqual(self.raw(), SOURCE)

    def test_invalid_input_shows_error_at_field_and_writes_nothing(self):
        t = self.tab
        t.vars['max_comments'].set('abc'); t.vars['max_retries'].set('99'); t.vars['notify_file'].set('')
        self.assertFalse(t.save())
        self.assertEqual(self.raw(), SOURCE)
        for key in ('max_comments', 'max_retries', 'notify_file'):
            self.assertTrue(t.err_labels[key].winfo_ismapped() or t.err_labels[key].grid_info(), key)
            self.assertIn('✖', t.err_labels[key]['text'])
        self.assertIn('⚠', t.nav.get(0))
        self.assertEqual(t.current, 'abruf')
        self.assertEqual(self.app.statuses[-1][1], 'err')
        t.vars['max_comments'].set('10')   # Eingabe räumt die Meldung am Feld auf
        self.assertEqual(t.err_labels['max_comments']['text'], '✖ Kommentare pro Video: bitte eine ganze Zahl von 0 bis 5000 eingeben.')
        self.assertFalse(t.err_labels['max_comments'].winfo_ismapped())

    def test_jumps_to_section_with_error(self):
        t = self.tab
        t.vars['api_port'].set('80')
        t.save()
        self.assertEqual(t.current, 'pulse')

    def test_save_writes_file_and_keeps_comments(self):
        t = self.tab
        t.vars['max_comments'].set('25'); t.vars['headless'].set(False); t.choice_vars['log_level'].set('Ausführlich (zur Fehlersuche)')
        self.assertTrue(t.save())
        out = self.raw()
        self.assertIn('# Kommentar oben', out)
        self.assertIn('headless = false  # unsichtbar', out)
        cfg = core.read_settings(self.root)
        self.assertEqual((cfg.max_comments, cfg.headless, cfg.log_level), (25, False, 'DEBUG'))
        self.assertFalse(t.dirty)
        self.assertIn('gespeichert', self.app.statuses[-1][0])
        self.assertTrue(any('Einstellungen gespeichert' in l for l, _ in self.app.logs))

    def test_only_changed_keys_are_written(self):
        t = self.tab
        t.vars['max_comments'].set('77')
        t.save()
        data = tomllib.loads(self.raw())
        self.assertEqual(data['notify_file'], 'data/alerts.log')
        self.assertEqual(self.raw().replace('max_comments = 77', 'max_comments = 500', 1), SOURCE)

    def test_proxy_list_add_remove_mask_and_save(self):
        t = self.tab
        ed = t.proxy_editor
        ed.entry_var.set('kein proxy'); self.assertFalse(ed.add_from_entry())
        self.assertIn('Leerzeichen', ed.check_label['text'])
        ed.entry_var.set('http://kunde:GeheimPw@gw.de:8000?country=de'); self.assertTrue(ed.add_from_entry())
        self.assertNotIn('GeheimPw', ed.listbox.get(0)); self.assertIn('*****', ed.listbox.get(0))
        ed.entry_var.set('http://kunde:GeheimPw@gw.de:8000?country=de'); self.assertFalse(ed.add_from_entry())   # doppelt
        self.assertTrue(t.dirty)
        self.assertTrue(t.save())
        self.assertEqual(core.read_settings(self.root).proxies, ['http://kunde:GeheimPw@gw.de:8000?country=de'])
        self.assertTrue(all('GeheimPw' not in l for l, _ in self.app.logs))
        ed.listbox.selection_set(0); ed.remove_selected()
        t.save()
        self.assertEqual(core.read_settings(self.root).proxies, [])

    def test_broken_settings_file_shows_banner_and_can_be_reset(self):
        self.put('max_comments = [[[\n')
        t = self.tab
        t.refresh()
        self.assertIsNotNone(t.settings_problem)
        self.assertTrue(t.banner.winfo_children())
        self.assertEqual(t.vars['max_comments'].get(), '500')   # Standardwerte statt Absturz
        t.vars['max_comments'].set('5')
        self.assertFalse(t.save())
        self.assertEqual(self.raw(), 'max_comments = [[[\n')
        self.showwarning.assert_called()
        t.reset_to_defaults()
        self.assertIsNone(t.settings_problem)
        self.assertEqual((self.root / 'settings.toml.defekt').read_text(encoding='utf-8'), 'max_comments = [[[\n')

    def test_settings_with_invalid_value_is_handled_like_broken(self):
        self.put('engine = "unsupported-browser"\n')
        self.tab.refresh()
        self.assertIsNotNone(self.tab.settings_problem)

    def test_broken_integration_file_shows_banner_and_is_replaced_on_demand(self):
        (self.root / 'data' / 'integration.json').write_text('{kaputt', encoding='utf-8')
        t = self.tab
        t.refresh()
        self.assertIsNotNone(t.integration_problem)
        t.replace_integration()
        self.assertIsNone(t.integration_problem)
        self.assertTrue((self.root / 'data' / 'integration.json.defekt').exists())

    def test_api_settings_saved_to_integration_json(self):
        from socialscraper import integration
        t = self.tab
        t.vars['api_enabled'].set(True)
        t._api_toggled()
        self.assertGreaterEqual(len(t.vars['api_token'].get()), 24)   # Token wird beim Einschalten erzeugt
        port = _free_port()
        t.vars['api_port'].set(str(port))
        t.origin_editor.entry_var.set('http://localhost:5173'); t.origin_editor.add_from_entry()
        self.assertTrue(t.save(), self.app.statuses[-1])
        cfg = integration.load(self.root)
        self.assertEqual((cfg['api']['enabled'], cfg['api']['port']), (True, port))
        self.assertEqual(cfg['api']['cors_origins'], ['http://localhost:5173'])
        self.assertEqual(cfg['api']['token'], t.vars['api_token'].get())
        self.assertEqual(self.raw(), SOURCE)   # settings.toml unverändert

    def test_saving_keeps_sync_status_written_by_the_service(self):
        from socialscraper import integration
        cfg = integration.default_config()
        cfg['sync'].update({'kind': 'supabase', 'url': 'https://a.supabase.co', 'key': 'k' * 40, 'last_push': '2026-10-11T09:00:00Z'})
        integration.save(cfg, self.root)
        t = self.tab; t.refresh()
        t.vars['sync_auto'].set(False)
        self.assertTrue(t.save())
        self.assertEqual(integration.load(self.root)['sync']['last_push'], '2026-10-11T09:00:00Z')

    def test_enabled_api_with_busy_port_is_refused(self):
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(1); self.addCleanup(sock.close)
        t = self.tab
        t.vars['api_enabled'].set(True); t._api_toggled()
        t.vars['api_port'].set(str(sock.getsockname()[1]))
        self.assertFalse(t.save())
        self.assertIn('belegt', t.err_labels['api_port']['text'])
        self.assertFalse((self.root / 'data' / 'integration.json').exists())

    def test_token_not_shown_by_default_and_toggle(self):
        t = self.tab
        self.assertEqual(t.token_entry['show'], '•')
        self.assertEqual(t.key_entry['show'], '•')

    def test_new_token_asks_when_one_exists_and_copy_works(self):
        t = self.tab
        t.vars['api_token'].set('a' * 30)
        self.ask.return_value = False
        t._new_token(); self.assertEqual(t.vars['api_token'].get(), 'a' * 30)
        self.ask.return_value = True
        t._new_token(); self.assertNotEqual(t.vars['api_token'].get(), 'a' * 30)
        t._copy_token(); self.assertEqual(t.clipboard_get(), t.vars['api_token'].get())

    def test_test_api_against_real_interface_and_when_nobody_listens(self):
        from socialscraper import api
        token = 'T' * 32
        with Store(self.root / 'data' / 'scrape.db'):
            pass
        handle = api.start(self.root, port=0, token=token, cfg=_api_cfg()); self.addCleanup(handle.stop)
        t = self.tab
        t.vars['api_port'].set(str(handle.port)); t.vars['api_token'].set(token)
        t.test_api()
        self.assertTrue(self.app.pump(lambda: 'api' in t.last_result))
        self.assertTrue(t.last_result['api'][0], t.last_result)
        t.last_result.clear()
        t.vars['api_port'].set(str(_free_port()))
        t.test_api()
        self.assertTrue(self.app.pump(lambda: 'api' in t.last_result))
        self.assertFalse(t.last_result['api'][0])
        self.assertIn('Hintergrunddienst', t.api_result['text'])

    def test_test_api_with_invalid_port_does_not_call_network(self):
        t = self.tab
        t.vars['api_port'].set('abc')
        with patch('socialscraper.formlogic.check_api') as call:
            t.test_api()
        call.assert_not_called()
        self.assertFalse(t.last_result['api'][0])

    def _enable_cloud(self):
        from socialscraper import integration
        cfg = integration.default_config()
        cfg['sync'].update({'kind': 'supabase', 'url': 'https://a.supabase.co', 'key': 'k' * 40})
        integration.save(cfg, self.root)
        self.tab.refresh()

    def test_cloud_buttons_start_cli_jobs_and_show_counts(self):
        self._enable_cloud()
        t = self.tab
        for action in ('test', 'push', 'pull'):
            t.cloud_action(action)
            steps, finish = self.app.steps[-1]
            self.assertEqual(steps[0][1], ['sync', action])
        lines = ['  Videos: 3 gesendet', '  Kommentare: 10 gesendet', 'Fertig: 13 Zeilen gesendet.']
        finish([('Cloud', 0, lines)], False)
        self.assertIn('Videos: 3', t.cloud_result['text'])
        self.assertEqual(self.app.statuses[-1][1], 'ok')
        finish([('Cloud', 1, ['Fehler: Schlüssel abgelehnt'])], False)
        self.assertIn('abgelehnt', t.cloud_result['text'])
        finish([], True)
        self.assertEqual(t.cloud_result['text'], 'Abgebrochen.')

    def test_cloud_off_gives_hint_without_job(self):
        t = self.tab
        t.cloud_action('push')
        self.assertEqual(self.app.steps, [])
        self.showinfo.assert_called()

    def test_cloud_with_unsaved_changes_asks_to_save_first(self):
        self._enable_cloud()
        t = self.tab
        t.vars['sync_url'].set('https://b.supabase.co')
        self.ask.return_value = False
        t.cloud_action('push'); self.assertEqual(self.app.steps, [])
        self.ask.return_value = True
        t.cloud_action('push')
        self.assertEqual(len(self.app.steps), 1)
        from socialscraper import integration
        self.assertEqual(integration.load(self.root)['sync']['url'], 'https://b.supabase.co')

    def test_refresh_does_not_discard_unsaved_edits(self):
        t = self.tab
        t.vars['max_comments'].set('88')
        t.refresh()
        self.assertEqual(t.vars['max_comments'].get(), '88')
        self.assertTrue(t.dirty)

    def test_refresh_picks_up_external_change_when_clean(self):
        self.put(SOURCE.replace('max_comments = 500', 'max_comments = 321'))
        self.tab.refresh()
        self.assertEqual(self.tab.vars['max_comments'].get(), '321')

    def test_cloud_fields_disabled_when_off(self):
        t = self.tab
        self.assertEqual(str(t.url_entry['state']), 'disabled')
        t.choice_vars['sync_kind'].set('Supabase')
        self.assertEqual(str(t.url_entry['state']), 'normal')

    def test_backup_button_creates_a_copy(self):
        with Store(self.root / 'data' / 'scrape.db') as s:
            s.save_result(Video('youtube', 'v1', 'https://youtu.be/v1'), [Comment('youtube', 'v1', 'c1', 'x')])
        dest = self.root / 'kopie.db'
        with patch('tkinter.filedialog.asksaveasfilename', return_value=str(dest)):
            self.tab.backup()
        self.assertTrue(self.app.pump(lambda: 'backup' in self.tab.last_result))
        self.assertEqual(self.tab.last_result['backup'][0], 'ok', self.tab.last_result)
        self.assertTrue(dest.is_file())

    def test_backup_without_database_says_so(self):
        self.tab.backup()
        self.showinfo.assert_called()

    def test_backup_to_locked_target_reports_error_without_traceback(self):
        Store(self.root / 'data' / 'scrape.db').close()
        with patch('tkinter.filedialog.asksaveasfilename', return_value=str(self.root / 'x.db')), \
                patch('socialscraper.formlogic.os.replace', side_effect=PermissionError('x')):
            self.tab.backup()
            self.assertTrue(self.app.pump(lambda: 'backup' in self.tab.last_result))
        self.assertEqual(self.tab.last_result['backup'][0], 'err')
        self.assertNotIn('Traceback', self.tab.last_result['backup'][1])

    def test_system_check_runs_in_background(self):
        self.tab.show_check()
        self.assertTrue(self.app.pump(lambda: 'check' in self.tab.last_result, 30))
        rows, _ok = self.tab.last_result['check']
        self.assertTrue(any('Chrome' in r for r in rows))

    def test_undo_button_restores_previous_settings(self):
        t = self.tab
        t.vars['max_comments'].set('15'); t.save()
        self.assertEqual(str(t.undo_btn['state']), 'normal')
        t.undo_last()
        self.assertEqual(t.vars['max_comments'].get(), '500')
        self.assertEqual(core.read_settings(self.root).max_comments, 500)

    def test_system_paths_show_wegwerf_ordner_and_open(self):
        t = self.tab
        self.assertIn(str(self.root), t.path_labels['program'].full)
        t._open_location('data')
        self.assertEqual(self.app.opened[-1][0], self.root / 'data')

    def test_service_panel_shows_off_without_creating_files(self):
        self.tab.service.refresh()
        self.assertIn('Aus', self.tab.service.state_label['text'])
        self.assertFalse((self.root / 'data' / 'daemon.lock').exists())
        self.assertEqual(str(self.tab.service.stop_btn['state']), 'disabled')

    def test_service_start_stop_calls_daemon_api(self):
        from socialscraper import daemon
        panel = self.tab.service
        running = {'running': True, 'pid': 1, 'started_at': None, 'last_beat': None, 'current': None, 'stale': False, 'age_s': 1}
        with patch.object(daemon, 'start_detached', return_value=running) as start:
            panel.start()
            self.assertTrue(self.app.pump(lambda: not panel.busy))
        start.assert_called_once_with(self.root)
        with patch.object(daemon, 'stop', return_value=True) as stop:
            panel.stop(ask=False)
            self.assertTrue(self.app.pump(lambda: not panel.busy))
        stop.assert_called_once_with(self.root)
        self.assertEqual(panel.last_message, 'Dienst gestoppt.')

    def test_service_start_failure_shows_message_not_traceback(self):
        from socialscraper import daemon
        panel = self.tab.service
        with patch.object(daemon, 'start_detached', side_effect=RuntimeError('Der Hintergrunddienst ist nicht angelaufen.')):
            panel.start()
            self.assertTrue(self.app.pump(lambda: not panel.busy))
        self.showerror.assert_called()
        self.assertIn('nicht angelaufen', self.showerror.call_args[0][1])

    def test_autostart_toggle_calls_daemon_and_reverts_on_error(self):
        from socialscraper import daemon
        panel = self.tab.service
        with patch.object(daemon, 'set_autostart', return_value=True) as call:
            panel.autostart.set(True); panel._toggle_autostart()
            self.assertTrue(self.app.pump(lambda: not panel._autostart_busy))
        call.assert_called_once_with(True, self.root)
        with patch.object(daemon, 'set_autostart', side_effect=RuntimeError('Die Verknüpfung konnte nicht angelegt werden.')), \
                patch.object(daemon, 'autostart_enabled', return_value=False):
            panel.autostart.set(True); panel._toggle_autostart()
            self.assertTrue(self.app.pump(lambda: not panel._autostart_busy))
            self.assertFalse(panel.autostart.get())
        self.showerror.assert_called()

    def test_keyboard_tab_order_reaches_nav_then_fields(self):
        self.app.deiconify(); self.app.update()
        self.tab.nav.focus_force(); self.app.update()
        self.assertEqual(self.app.focus_get(), self.tab.nav)
        self.tab._focus_first(); self.app.update()
        self.assertEqual(str(self.app.focus_get().winfo_class()), 'TSpinbox')


def _free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    return port


if __name__ == '__main__':
    unittest.main()
