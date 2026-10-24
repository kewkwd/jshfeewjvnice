"""Befehl `channel` (add, list, status, pause, resume, retry-failed, remove, estimate): Wegwerf-Datenbank, kein Netz."""
import contextlib
import io
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from socialscraper import channels, cli, repo
from socialscraper.config import Settings
from socialscraper.store import Store

os.environ.setdefault('SS_PACE', 'off')
YT = 'https://www.youtube.com/@kanalname'
TT = 'https://www.tiktok.com/@jemand'


class ChannelCliBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='', profiles_dir=str(self.root / 'profiles'))

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, 'load', return_value=self.cfg), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['channel', *args])
        return code, out.getvalue(), err.getvalue()

    def targets(self):
        with Store(self.cfg.db_path) as store:
            return repo.list_channels(store.db)


class AddTests(ChannelCliBase):
    def test_add_with_defaults_and_list(self):
        code, out, _ = self.run_cli('add', YT)
        self.assertEqual(code, 0)
        self.assertIn('Kanal angelegt', out)
        (t,) = self.targets()
        self.assertEqual((t['platform'], t['scope'], t['pace_profile'], t['account_mode'], t['observe'], t['interval_min'], t['max_comments']),
                         ('youtube', 'all', 'normal', 'default', 1, 360, 0))
        code, out, _ = self.run_cli('list')
        self.assertEqual(code, 0)
        self.assertIn('1 Kanal.', out)
        self.assertIn('wartet auf den ersten Lauf', out)

    def test_add_with_all_options(self):
        with Store(self.cfg.db_path) as store:
            repo.ensure_default_accounts(store.db)
            second = repo.add_account(store.db, 'tiktok', 'Zweit')
        code, out, _ = self.run_cli('add', TT, '--latest', '50', '--max-comments', '30', '--group', 'Meine Gruppe',
                                    '--account-id', str(second['id']), '--pace', 'vorsichtig', '--quiet', '23-7',
                                    '--no-watch', '--interval', '720')
        self.assertEqual(code, 0, out)
        self.assertIn('neu angelegt', out)
        (t,) = self.targets()
        self.assertEqual((t['scope'], t['scope_value'], t['max_comments'], t['account_mode'], t['account_id'], t['pace_profile'],
                          t['quiet_hours'], t['observe'], t['interval_min'], t['group_name']),
                         ('latest', '50', 30, 'fixed', second['id'], 'careful', '23-7', 0, 720, 'Meine Gruppe'))

    def test_existing_group_is_reused_case_insensitive_and_since(self):
        with Store(self.cfg.db_path) as store:
            repo.create_group(store.db, 'Eins')
        code, out, _ = self.run_cli('add', YT, '--since', '2026-01-31', '--group', 'eins')
        self.assertEqual(code, 0)
        self.assertNotIn('neu angelegt', out)
        with Store(self.cfg.db_path) as store:
            self.assertEqual(len(repo.list_groups(store.db)), 1)
        self.assertEqual(self.targets()[0]['scope_value'], '2026-01-31')

    def test_rotate_mode(self):
        code, _, _ = self.run_cli('add', TT, '--account-mode', 'rotate')
        self.assertEqual(code, 0)
        self.assertEqual(self.targets()[0]['account_mode'], 'rotate')

    def test_youtube_ignores_account_with_note(self):
        code, out, _ = self.run_cli('add', YT, '--account-mode', 'rotate')
        self.assertEqual(code, 0)
        self.assertIn('YouTube braucht kein Konto', out)
        self.assertEqual(self.targets()[0]['account_mode'], 'default')

    def test_validations_give_exit_1_and_create_nothing(self):
        with Store(self.cfg.db_path) as store:
            repo.ensure_default_accounts(store.db)
            other_account = repo.default_account(store.db, 'instagram')
        bad = [('add', 'https://example.com/x'), ('add', YT, '--scope', 'latest'), ('add', YT, '--scope', 'since'),
               ('add', YT, '--latest', '0'), ('add', YT, '--latest', '5', '--since', '2026-01-01'),
               ('add', YT, '--since', '31.01.2026'), ('add', YT, '--max-comments', '5001'), ('add', YT, '--quiet', '7-7'),
               ('add', YT, '--quiet', '25-3'), ('add', YT, '--interval', '1'), ('add', TT, '--account-mode', 'fixed'),
               ('add', TT, '--account-id', '9999'), ('add', TT, '--account-id', str(other_account['id'])),
               ('add', TT, '--account-mode', 'rotate', '--account-id', '1'), ('add', YT, '--scope', 'all', '--latest', '3'),
               ('add', 'https://www.tiktok.com/@jemand/video/123')]
        for args in bad:
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual(code, 1, (out, err))
                self.assertEqual(self.targets(), [])

    def test_duplicate_is_refused(self):
        self.assertEqual(self.run_cli('add', YT)[0], 0)
        code, _, err = self.run_cli('add', YT)
        self.assertEqual(code, 1)

    def test_quiet_off(self):
        self.assertEqual(self.run_cli('add', YT, '--quiet', 'aus')[0], 0)
        self.assertIsNone(self.targets()[0]['quiet_hours'])


class ActionTests(ChannelCliBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_cli('add', YT)[0], 0)
        self.tid = self.targets()[0]['id']

    def test_pause_resume(self):
        code, out, _ = self.run_cli('pause', str(self.tid))
        self.assertEqual(code, 0)
        self.assertIn('angehalten', out)
        self.assertEqual(self.targets()[0]['enabled'], 0)
        self.assertIn('angehalten', self.run_cli('list')[1])
        code, out, _ = self.run_cli('resume', str(self.tid))
        self.assertEqual(code, 0)
        self.assertEqual(self.targets()[0]['enabled'], 1)

    def test_retry_failed_and_status(self):
        with Store(self.cfg.db_path) as store:
            repo.add_channel_items(store.db, self.tid, 'youtube', [
                {'url': 'https://www.youtube.com/watch?v=aaaaaaaaaaa', 'uid': 'youtube:aaaaaaaaaaa'},
                {'url': 'https://www.youtube.com/watch?v=bbbbbbbbbbb', 'uid': 'youtube:bbbbbbbbbbb'}])
            items = repo.list_channel_items(store.db, self.tid)
            repo.set_channel_item(store.db, items[0]['id'], 'failed', error='Video nicht verfügbar')
            repo.set_channel_item(store.db, items[1]['id'], 'done')
        code, out, _ = self.run_cli('status', str(self.tid))
        self.assertEqual(code, 0)
        self.assertIn('1 fehlgeschlagen', out)
        self.assertIn('Video nicht verfügbar', out)
        self.assertIn('Tempo: Normal', out)
        code, out, _ = self.run_cli('retry-failed', str(self.tid))
        self.assertEqual(code, 0)
        self.assertIn('1 fehlgeschlagene', out)
        with Store(self.cfg.db_path) as store:
            self.assertEqual(repo.channel_progress(store.db, self.tid)['failed'], 0)
        self.assertIn('keine fehlgeschlagenen', self.run_cli('retry-failed', str(self.tid))[1])

    def test_remove(self):
        code, out, _ = self.run_cli('remove', str(self.tid))
        self.assertEqual(code, 0)
        self.assertIn('entfernt', out)
        self.assertEqual(self.targets(), [])
        self.assertIn('Noch keine Kanäle', self.run_cli('list')[1])

    def test_unknown_id_exits_2(self):
        for action in ('status', 'pause', 'resume', 'retry-failed', 'remove'):
            with self.subTest(action=action):
                code, out, err = self.run_cli(action, '999')
                self.assertEqual(code, 2)
                self.assertIn('999', err)
        self.assertEqual(len(self.targets()), 1)


class NoDatabase(ChannelCliBase):
    def test_list_and_unknown_id_do_not_create_database(self):
        code, out, _ = self.run_cli('list')
        self.assertEqual(code, 0)
        self.assertIn('Noch keine Kanäle', out)
        self.assertEqual(self.run_cli('status', '1')[0], 2)
        self.assertFalse(pathlib.Path(self.cfg.db_path).exists())


class EstimateTests(ChannelCliBase):
    def test_estimate_matches_library_and_needs_no_database(self):
        code, out, _ = self.run_cli('estimate', TT, '--count', '1000', '--pace', 'schnell', '--accounts', '2', '--quiet', '23-7')
        self.assertEqual(code, 0)
        e = channels.estimate(1000, 'tiktok', 'fast', accounts=2, comments=0, quiet_hours='23-7')
        self.assertIn(e['text'], out)
        self.assertIn('Rechnung:', out)
        self.assertIn('Schnell', out)
        self.assertFalse(pathlib.Path(self.cfg.db_path).exists())

    def test_estimate_errors(self):
        for args in (('estimate', YT, '--count', '-1'), ('estimate', YT, '--count', '5', '--accounts', '0'),
                     ('estimate', 'https://example.com', '--count', '5'), ('estimate', YT, '--count', '5', '--quiet', '3-3'),
                     ('estimate', YT, '--count', '5', '--comments', '6000')):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli(*args)[0], 1)

    def test_estimate_without_count_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli('estimate', YT)


if __name__ == '__main__':
    unittest.main()
