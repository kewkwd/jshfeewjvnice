"""Tests für socialscraper.integration (data/integration.json). Nur Wegwerf-Ordner, keine echten Schlüssel."""
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from socialscraper import integration


class IntegrationConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_missing_file_gives_defaults_without_problem(self):
        cfg, problem = integration.load_with_status(self.root)
        self.assertIsNone(problem)
        self.assertFalse(cfg['api']['enabled'])
        self.assertEqual(cfg['api']['host'], '127.0.0.1')
        self.assertEqual(cfg['sync']['kind'], 'none')
        self.assertFalse(integration.path(self.root).exists())

    def test_save_load_roundtrip_and_normalisation(self):
        cfg = integration.default_config()
        cfg['api'].update(enabled=True, port=9000, token='t' * 30,
                          cors_origins=['http://localhost:5173/', 'http://localhost:5173', ' https://pulse.example.org '])
        saved = integration.save(cfg, self.root)
        self.assertEqual(saved['api']['cors_origins'], ['http://localhost:5173', 'https://pulse.example.org'])
        loaded = integration.load(self.root)
        self.assertEqual(loaded, saved)
        self.assertEqual(json.loads(integration.path(self.root).read_text(encoding='utf-8'))['schema_version'], 1)

    def test_validation_messages_are_german_and_complete(self):
        cfg = integration.default_config()
        cfg['api'].update(enabled=True, port=70000, token='', cors_origins=['*', 'ftp://x', 'localhost'])
        cfg['sync'].update(kind='supabase', url='http://example.org', key='')
        with self.assertRaises(integration.IntegrationError) as ctx:
            integration.save(cfg, self.root)
        message = str(ctx.exception)
        for part in ('Port', 'Token', '„*“', 'ftp://x', 'https://', 'Schlüssel'):
            self.assertIn(part, message)
        self.assertFalse(integration.path(self.root).exists(), 'bei Fehlern wird nichts geschrieben')

    def test_http_allowed_only_for_loopback_cloud_url(self):
        cfg = integration.default_config()
        cfg['sync'].update(kind='supabase', url='http://127.0.0.1:54321', key='k' * 20)
        self.assertEqual(integration.validate(cfg), [])
        cfg['sync']['url'] = 'https://user:pw@example.org'
        self.assertTrue(integration.validate(cfg))

    def test_short_or_wrong_typed_values_rejected(self):
        cfg = integration.default_config()
        cfg['api'].update(token='kurz')
        self.assertTrue(integration.validate(cfg))
        cfg['api'].update(token='', port='80')
        self.assertTrue(integration.validate(cfg))
        cfg['api'].update(port=True)
        self.assertTrue(integration.validate(cfg))

    def test_ensure_token_creates_once_and_is_random(self):
        first = integration.ensure_token(self.root)
        self.assertGreaterEqual(len(first), integration.MIN_TOKEN_LENGTH)
        self.assertEqual(integration.ensure_token(self.root), first)
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.assertNotEqual(integration.ensure_token(other.name), first)
        self.assertNotEqual(integration.regenerate_token(self.root), first)

    def test_ensure_token_keeps_other_settings(self):
        cfg = integration.default_config()
        cfg['api'].update(enabled=True, port=9100, token='x' * 30)
        cfg['sync'].update(kind='supabase', url='https://abc.example.org', key='geheim-geheim-1')
        integration.save(cfg, self.root)
        integration.regenerate_token(self.root)
        loaded = integration.load(self.root)
        self.assertEqual(loaded['api']['port'], 9100)
        self.assertEqual(loaded['sync']['url'], 'https://abc.example.org')

    def test_redacted_hides_secrets_and_leaves_original_alone(self):
        cfg = integration.default_config()
        cfg['api']['token'] = 'A' * 30
        cfg['sync'].update(kind='supabase', url='https://x.example.org', key='SUPERSECRETKEY')
        red = integration.redacted(cfg)
        text = json.dumps(red)
        self.assertNotIn('A' * 30, text)
        self.assertNotIn('SUPERSECRETKEY', text)
        self.assertEqual(cfg['sync']['key'], 'SUPERSECRETKEY')
        self.assertEqual(integration.redacted(integration.default_config())['api']['token'], '')
        self.assertNotIn('SUPERSECRETKEY', integration.scrub('Fehler mit SUPERSECRETKEY im Text', cfg))

    def test_corrupt_file_does_not_crash_and_is_backed_up_on_save(self):
        p = integration.path(self.root)
        p.parent.mkdir(parents=True)
        p.write_text('{ kaputt', encoding='utf-8')
        cfg, problem = integration.load_with_status(self.root)
        self.assertIn('beschädigt', problem)
        self.assertFalse(cfg['api']['enabled'])
        self.assertNotIn('kaputt', problem)
        integration.ensure_token(self.root)
        self.assertTrue(p.with_name('integration.json.defekt').exists())
        self.assertIsNone(integration.load_with_status(self.root)[1])

    def test_file_in_other_encoding_counts_as_corrupt_not_as_crash(self):
        p = integration.path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'api': {'port': 1234}}), encoding='utf-16')     # Windows-Editor „Unicode“
        cfg, problem = integration.load_with_status(self.root)
        self.assertIn('beschädigt', problem)
        self.assertFalse(cfg['api']['enabled'])

    def test_non_object_and_unknown_fields_are_tolerated(self):
        p = integration.path(self.root)
        p.parent.mkdir(parents=True)
        p.write_text('[1,2]', encoding='utf-8')
        self.assertIsNotNone(integration.load_with_status(self.root)[1])
        p.write_text(json.dumps({'api': {'port': 1234, 'bogus': 1}, 'zeug': 5, 'sync': 'x'}), encoding='utf-8')
        cfg, problem = integration.load_with_status(self.root)
        self.assertIsNone(problem)
        self.assertEqual(cfg['api']['port'], 1234)
        self.assertNotIn('bogus', cfg['api'])
        self.assertEqual(cfg['sync']['kind'], 'none')

    def test_bom_is_accepted(self):
        p = integration.path(self.root)
        p.parent.mkdir(parents=True)
        p.write_bytes(b'\xef\xbb\xbf' + json.dumps({'api': {'port': 4321}}).encode())
        self.assertEqual(integration.load(self.root)['api']['port'], 4321)

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        cfg = integration.default_config()
        integration.save(cfg, self.root)
        with mock.patch('socialscraper.integration._replace', side_effect=PermissionError(13, 'Zugriff verweigert')):
            with self.assertRaises(integration.IntegrationError):
                integration.save(cfg, self.root)
        self.assertEqual([f.name for f in integration.path(self.root).parent.iterdir()], ['integration.json'])

    @unittest.skipIf(os.name == 'nt', 'POSIX-Rechte')
    def test_file_permissions_restricted(self):
        integration.ensure_token(self.root)
        self.assertEqual(integration.path(self.root).stat().st_mode & 0o077, 0)

    def test_update_sync_status_only_touches_status_fields(self):
        cfg = integration.default_config()
        cfg['api'].update(token='z' * 30, port=9200)
        integration.save(cfg, self.root)
        self.assertTrue(integration.update_sync_status(self.root, last_push='2026-10-11T00:00:00Z', last_error=None))
        loaded = integration.load(self.root)
        self.assertEqual(loaded['sync']['last_push'], '2026-10-11T00:00:00Z')
        self.assertEqual(loaded['api']['port'], 9200)
        with self.assertRaises(ValueError):
            integration.update_sync_status(self.root, key='x')

    def test_db_path_respects_env_and_defaults(self):
        with mock.patch.dict(os.environ, {'SS_DB_PATH': ''}):
            os.environ.pop('SS_DB_PATH')
            self.assertEqual(integration.db_path(self.root), self.root / 'data' / 'scrape.db')
        with mock.patch.dict(os.environ, {'SS_DB_PATH': str(self.root / 'anders.db')}):
            self.assertEqual(integration.db_path(self.root), self.root / 'anders.db')


if __name__ == '__main__':
    unittest.main()
