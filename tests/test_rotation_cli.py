"""Befehlszeile mit --rotate / --account-id / --accounts / --rotation-every. Attrappen statt Browser, Wegwerf-Ordner."""
import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from socialscraper import cli, repo, rotation
from socialscraper.config import Settings
from socialscraper.models import Video
from socialscraper.net.session_guard import SessionExpired, State
from socialscraper.store import Store
from tests.test_rotation import make_login

os.environ.setdefault('SS_PACE', 'off')
LINKS = [f'https://www.tiktok.com/@a/video/{n}' for n in range(1, 6)]
CAPTCHA = 'Die Plattform zeigt eine Sicherheitsabfrage (CAPTCHA). Später noch einmal versuchen.'


class CliRotationBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='', profiles_dir=str(self.root / 'profiles'))
        with Store(self.cfg.db_path) as store:
            repo.ensure_default_accounts(store.db)
            self.a = repo.default_account(store.db, 'tiktok')
            self.b = repo.add_account(store.db, 'tiktok', 'Zweit')
            self.c = repo.add_account(store.db, 'tiktok', 'Dritt')
        for acc in (self.a, self.b, self.c):
            make_login(self.root, 'tiktok', acc['identity'])
        self.calls, self.faults, self.states = [], {}, {}
        self.sessions, self.im = Mock(), Mock()
        self.im.stored.return_value = (None, None)
        self.adapter = Mock(platform='tiktok', needs_browser=True, needs_auth=True)
        self.adapter.fetch.side_effect = self.fetch

    def fetch(self, url, ctx):
        vid = url.rsplit('/', 1)[-1]
        self.calls.append((vid, ctx.identity))
        fault = self.faults.get((vid, ctx.identity)) or self.faults.get((vid, None))
        if fault is not None:
            raise fault
        return Video('tiktok', vid, url, comment_count=0), []

    def ensure(self, backend, im, identity, platform, country, mobile, max_age=None):
        state = self.states.get(identity, State.OK)
        if state == 'raise':
            raise SessionExpired('abgelaufen')
        return state

    def run_cli(self, args, cfg=None):
        out = io.StringIO()
        cfg = cfg or self.cfg
        with patch.object(cli, 'load', return_value=cfg), patch.object(cli, 'resolve', return_value=self.adapter), \
                patch.object(cli, '_ctx_and_backend', return_value=(self.im, Mock(), self.sessions)), \
                patch.object(cli, 'ensure', side_effect=self.ensure), contextlib.redirect_stdout(out):
            code = cli.main(args)
        return code, out.getvalue()

    def db_account(self, account):
        with Store(self.cfg.db_path) as store:
            return repo.get_account(store.db, account['id'])

    def events(self, text):
        return [json.loads(line[len('[SS_FETCH] '):]) for line in text.splitlines() if line.startswith('[SS_FETCH]')]


class RotateTests(CliRotationBase):
    def test_rotate_switches_after_n_videos_and_names_accounts(self):
        code, out = self.run_cli(['scrape', *LINKS, '--rotate', '--rotation-every', '2', '--max-comments', '0'])
        self.assertEqual(code, 0, out)
        main, zweit, dritt = self.a['identity'], self.b['identity'], self.c['identity']
        self.assertEqual([i for _v, i in self.calls], [main, main, zweit, zweit, dritt])
        self.assertIn('Benutzte Konten: Hauptkonto, Zweit, Dritt', out)
        self.assertIn('fertig: 5 ok, 0 fehler', out)
        self.sessions.drop.assert_any_call(main)          # Browser des vorigen Kontos wurde geschlossen
        self.assertEqual(self.db_account(self.a)['use_count'], 2)
        self.assertEqual(self.cfg.rotation_every, 2)

    def test_rotation_every_comes_from_settings_when_not_given(self):
        self.cfg.rotation_every = 3
        code, out = self.run_cli(['scrape', *LINKS, '--rotate', '--max-comments', '0'])
        self.assertEqual(code, 0)
        self.assertEqual([i for _v, i in self.calls],
                         [self.a['identity']] * 3 + [self.b['identity']] * 2)

    def test_captcha_switches_at_once_and_retries_same_video(self):
        self.faults[('2', self.a['identity'])] = RuntimeError(CAPTCHA)
        code, out = self.run_cli(['scrape', *LINKS[:3], '--rotate', '--rotation-every', '5', '--max-comments', '0'])
        self.assertEqual(code, 0, out)
        main, zweit = self.a['identity'], self.b['identity']
        self.assertEqual(self.calls, [('1', main), ('2', main), ('2', zweit), ('3', zweit)])
        self.assertEqual(self.db_account(self.a)['cooldown_reason'], 'CAPTCHA')
        self.assertEqual(sum(e['success'] for e in self.events(out) if e['stage'] == 'result'), 3)

    def test_session_expired_switches_account(self):
        self.faults[('1', self.a['identity'])] = SessionExpired('abgelaufen')
        code, out = self.run_cli(['scrape', *LINKS[:2], '--rotate', '--max-comments', '0'])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls[:2], [('1', self.a['identity']), ('1', self.b['identity'])])
        self.assertEqual(self.db_account(self.a)['cooldown_reason'], 'Anmeldung abgelaufen')

    def test_each_video_once_per_account_then_message_with_time(self):
        self.faults[('1', None)] = RuntimeError(CAPTCHA)
        code, out = self.run_cli(['scrape', *LINKS[:3], '--rotate', '--max-comments', '0'])
        self.assertEqual(code, 1)
        self.assertEqual([v for v, _i in self.calls].count('1'), 3)
        self.assertEqual(len({i for v, i in self.calls if v == '1'}), 3)
        self.assertNotIn('2', [v for v, _i in self.calls])                    # später keine Konten mehr
        messages = [e['message'] for e in self.events(out) if e['stage'] == 'result' and not e['success']]
        self.assertTrue(any('wieder frei ab' in m for m in messages), messages)

    def test_login_check_skips_expired_account(self):
        self.states[self.a['identity']] = State.EXPIRED
        self.cfg.require_auth = True
        code, out = self.run_cli(['scrape', *LINKS[:2], '--rotate', '--max-comments', '0'])
        self.assertEqual(code, 0, out)
        self.assertEqual({i for _v, i in self.calls}, {self.b['identity']})
        self.assertEqual(self.db_account(self.a)['cooldown_reason'], 'Anmeldung abgelaufen')

    def test_accounts_option_limits_the_rotation(self):
        code, out = self.run_cli(['scrape', *LINKS[:4], '--rotate', '--rotation-every', '1', '--accounts', f"{self.b['id']},{self.c['id']}",
                                  '--max-comments', '0'])
        self.assertEqual(code, 0)
        self.assertEqual([i for _v, i in self.calls], [self.b['identity'], self.c['identity']] * 2)

    def test_no_account_available_gives_clear_message(self):
        for acc in (self.a, self.b, self.c):
            with Store(self.cfg.db_path) as store:
                rotation.mark_failure(store.db, acc['id'], 'x', 'block')
        code, out = self.run_cli(['scrape', *LINKS[:2], '--rotate', '--max-comments', '0'])
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [])
        self.assertIn('wieder frei ab', ' '.join(e['message'] for e in self.events(out)))

    def test_profile_rotate_uses_rotating_account(self):
        identities = []

        def listing(self_, username, ctx, limit):
            identities.append(ctx.identity)
            return LINKS[:2]
        with patch('socialscraper.adapters.tiktok.TikTokAdapter.profile_urls', listing):
            code, out = self.run_cli(['profile', 'user', '--rotate', '--rotation-every', '1', '--max-comments', '0'])
        self.assertEqual(code, 0, out)
        self.assertEqual(identities, [self.a['identity']])
        self.assertEqual([i for _v, i in self.calls], [self.a['identity'], self.b['identity']])

    def test_profile_rotate_switches_on_captcha_while_listing(self):
        identities = []

        def listing(self_, username, ctx, limit):
            identities.append(ctx.identity)
            if len(identities) == 1:
                raise RuntimeError(CAPTCHA)
            return LINKS[:1]
        with patch('socialscraper.adapters.tiktok.TikTokAdapter.profile_urls', listing):
            code, out = self.run_cli(['profile', 'user', '--rotate', '--max-comments', '0'])
        self.assertEqual(code, 0, out)
        self.assertEqual(identities, [self.a['identity'], self.b['identity']])

    def test_no_rotation_without_flag_old_behaviour(self):
        code, out = self.run_cli(['scrape', *LINKS[:3], '--max-comments', '0'])
        self.assertEqual(code, 0)
        self.assertEqual({i for _v, i in self.calls}, {'default'})
        self.assertNotIn('Benutzte Konten', out)
        self.assertEqual(self.db_account(self.a)['use_count'], 0)


class AccountOptionTests(CliRotationBase):
    def test_account_id_selects_that_account(self):
        code, out = self.run_cli(['scrape', *LINKS[:2], '--account-id', str(self.c['id']), '--max-comments', '0'])
        self.assertEqual(code, 0, out)
        self.assertEqual({i for _v, i in self.calls}, {self.c['identity']})
        self.assertIn('Benutzte Konten: Dritt', out)
        self.assertEqual(self.db_account(self.c)['use_count'], 2)

    def test_fixed_account_stops_after_an_account_fault(self):
        """Festes Konto: nach CAPTCHA (Abkühlung) laufen die übrigen Videos nicht mehr mit demselben Konto."""
        for n in range(1, 5):
            self.faults[(str(n), None)] = RuntimeError(CAPTCHA)
        code, out = self.run_cli(['scrape', *LINKS[:4], '--account-id', str(self.c['id']), '--max-comments', '0'])
        self.assertEqual([vid for vid, _ in self.calls], ['1'])
        results = [e for e in self.events(out) if e['stage'] == 'result']
        self.assertEqual(len(results), 4)
        self.assertTrue(all(not e['success'] for e in results))
        self.assertTrue(any('pausiert' in e['message'] for e in results), [e['message'] for e in results])
        self.assertEqual(self.db_account(self.c)['cooldown_reason'], 'CAPTCHA')

    def test_account_id_errors(self):
        code, _ = self.run_cli(['scrape', *LINKS[:1], '--account-id', '999', '--max-comments', '0'])
        self.assertEqual(code, 1)
        with Store(self.cfg.db_path) as store:
            reddit = repo.default_account(store.db, 'reddit')
        code, _ = self.run_cli(['scrape', *LINKS[:1], '--account-id', str(reddit['id']), '--max-comments', '0'])
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [])

    def test_conflicting_options(self):
        for args in (['--rotate', '--account-id', '1'], ['--accounts', '1,2']):
            code, _ = self.run_cli(['scrape', *LINKS[:1], *args, '--max-comments', '0'])
            self.assertEqual(code, 1, args)
        self.assertEqual(self.calls, [])

    def test_option_values_are_validated(self):
        for args in (['--rotation-every', '0'], ['--rotation-every', '101'], ['--rotation-every', 'x'], ['--accounts', 'a,b'],
                     ['--account-id', '0']):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.run_cli(['scrape', *LINKS[:1], *args])

    def test_free_and_tor_modes_refuse_rotation_and_account_choice(self):
        for mode in ('frei', 'tor'):
            cfg = Settings(db_path=self.cfg.db_path, notify_file='', profiles_dir=self.cfg.profiles_dir, proxy_mode=mode)
            for extra in (['--rotate'], ['--account-id', str(self.a['id'])]):
                code, _ = self.run_cli(['scrape', *LINKS[:1], *extra, '--max-comments', '0'], cfg=cfg)
                self.assertEqual(code, 1, (mode, extra))
        self.assertEqual(self.calls, [])

    def test_successful_login_and_check_clear_the_login_cooldown(self):
        with Store(self.cfg.db_path) as store:
            rotation.mark_failure(store.db, self.a['id'], 'x', 'login')
            rotation.mark_failure(store.db, self.b['id'], 'x', 'captcha')
        with patch.object(cli, 'load', return_value=self.cfg), patch.object(cli, '_ctx_and_backend', return_value=(self.im, Mock(), Mock())), \
                patch('socialscraper.login.login', return_value=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['login', '--identity', self.a['identity'], '--mode', 'qr']), 0)
        with patch.object(cli, 'load', return_value=self.cfg), patch.object(cli, '_ctx_and_backend', return_value=(self.im, Mock(), Mock())), \
                patch('socialscraper.login.check', return_value=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['check', '--identity', self.b['identity']]), 0)
        self.assertIsNone(self.db_account(self.a)['cooldown_until'])
        self.assertIsNotNone(self.db_account(self.b)['cooldown_until'])      # CAPTCHA-Pause bleibt


    def test_import_cookies_clears_the_login_cooldown(self):
        from socialscraper.net import cookies
        with Store(self.cfg.db_path) as store:
            rotation.mark_failure(store.db, self.a['id'], 'x', 'login')
        source = self.root / 'meine_cookies.json'
        source.write_text(json.dumps([{'name': 'sessionid', 'value': 'x', 'domain': '.tiktok.com', 'path': '/'}]), encoding='utf-8')
        with patch.object(cookies, 'BASE', self.root / 'cookiespeicher'), patch.object(cli, 'load', return_value=self.cfg),                 contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(['import-cookies', '--platform', 'tiktok', '--identity', self.a['identity'], '--file', str(source)])
        self.assertEqual(code, 0)
        self.assertIsNone(self.db_account(self.a)['cooldown_until'])


if __name__ == '__main__':
    unittest.main()
