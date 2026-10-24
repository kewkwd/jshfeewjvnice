"""Planer mit Konten-Rotation: Wechsel nach N Videos, Wechsel bei Kontofehlern, jedes Video höchstens einmal je Konto.
Simulierte Plattform (Attrappen), Wegwerf-Ordner; kein Browser, kein Netz."""
import os
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from socialscraper import repo, scheduler
from socialscraper.config import Settings
from socialscraper.models import Video
from socialscraper.net.session_guard import SessionExpired, State
from socialscraper.store import Store
from tests.test_rotation import make_login

os.environ.setdefault('SS_PACE', 'off')
URLS = [f'https://www.tiktok.com/@a/video/{n}' for n in range(1, 6)]
CAPTCHA = 'Die Plattform zeigt eine Sicherheitsabfrage (CAPTCHA). Später noch einmal versuchen.'


class RotationRunBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'data' / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'data' / 'profiles'), rotation_every=2)
        self.store = Store(self.cfg.db_path); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.a = repo.default_account(self.db, 'tiktok')
        self.b = repo.add_account(self.db, 'tiktok', 'Zweit')
        self.c = repo.add_account(self.db, 'tiktok', 'Dritt')
        for acc in (self.a, self.b, self.c):
            make_login(self.root, 'tiktok', acc['identity'])
        self.calls, self.faults, self.auth = [], {}, {}
        self.sessions = Mock()
        self.adapter = Mock(platform='tiktok', needs_browser=True, needs_auth=True)
        self.adapter.fetch.side_effect = self.fetch
        for target, kwargs in (
                (scheduler.cli, dict(make_runtime=Mock(return_value=(Mock(), Mock(), self.sessions)))),
                (scheduler.cli, dict(shutdown_runtime=Mock())),
                (scheduler, dict(resolve=lambda url: self.adapter))):
            p = patch.multiple(target, **kwargs); p.start(); self.addCleanup(p.stop)
        p = patch.object(scheduler.cli, 'auth_state', side_effect=self.auth_state); p.start(); self.addCleanup(p.stop)

    def auth_state(self, adapter, cfg, identity, country, mobile, backend, im):
        state = self.auth.get(identity, State.OK)
        if state == 'raise':
            raise SessionExpired('abgelaufen')
        return state == State.OK, state

    def fetch(self, url, ctx):
        vid = url.rsplit('/', 1)[-1]
        self.calls.append((vid, ctx.identity))
        fault = self.faults.get((vid, ctx.identity)) or self.faults.get((vid, None)) or self.faults.get((None, ctx.identity))
        if fault is not None:
            raise fault
        return Video('tiktok', vid, url, comment_count=0), []

    def target(self, mode='rotate', urls=URLS, **kw):
        kw.setdefault('max_comments', 0)
        t = repo.add_target(self.db, 'search', 'tiktok', 'katzen', account_mode=mode, **kw)
        patcher = patch.object(scheduler.discovery, 'discover', return_value=list(urls))
        patcher.start(); self.addCleanup(patcher.stop)
        return t

    def run_target(self, t):
        return scheduler.run_target(self.root, t['id'], 'manual', cfg=self.cfg)

    def identities(self):
        return [identity for _vid, identity in self.calls]

    def row(self, account):
        return repo.get_account(self.db, account['id'])


class BlockSwitchTests(RotationRunBase):
    def test_switches_after_n_videos_and_names_accounts(self):
        t = self.target()
        with self.assertLogs('socialscraper.rotation', 'INFO') as logs:
            r = self.run_target(t)
        self.assertEqual((r['status'], r['videos_seen']), ('ok', 5))
        main, zweit, dritt = self.a['identity'], self.b['identity'], self.c['identity']
        self.assertEqual(self.identities(), [main, main, zweit, zweit, dritt])
        self.assertEqual(r['accounts_used'], ['Hauptkonto', 'Zweit', 'Dritt'])
        row = repo.list_runs(self.db)[0]
        self.assertEqual(repo.accounts_used_list(row['accounts_used']), ['Hauptkonto', 'Zweit', 'Dritt'])
        self.assertEqual([m for m in logs.output if 'Konto: ' in m and 'pausiert' not in m],
                         [f'INFO:socialscraper.rotation:Konto: {n}' for n in ('Hauptkonto', 'Zweit', 'Dritt')])
        self.assertEqual(self.row(self.a)['use_count'], 2)
        self.assertEqual(self.row(self.c)['use_count'], 1)

    def test_old_browser_of_previous_account_is_closed_on_switch(self):
        self.run_target(self.target())
        dropped = [c.args[0] for c in self.sessions.drop.call_args_list]
        self.assertEqual(dropped, [self.a['identity'], self.b['identity']])

    def test_next_run_starts_with_longest_unused_account(self):
        t = self.target(urls=URLS[:2])
        self.run_target(t)
        self.run_target(t)
        self.assertEqual(self.identities(), [self.a['identity']] * 2 + [self.b['identity']] * 2)

    def test_rotation_every_comes_from_settings(self):
        self.cfg.rotation_every = 1
        self.run_target(self.target(urls=URLS[:3]))
        self.assertEqual(self.identities(), [self.a['identity'], self.b['identity'], self.c['identity']])

    def test_daily_limit_ends_block_early(self):
        self.cfg.rotation_every = 10
        repo.set_account_daily_limit(self.db, self.a['id'], 1)
        self.run_target(self.target(urls=URLS[:3]))
        self.assertEqual(self.identities(), [self.a['identity'], self.b['identity'], self.b['identity']])

    def test_single_available_account_just_continues(self):
        for acc in (self.b, self.c):
            repo.set_account_rotation(self.db, acc['id'], False)
        r = self.run_target(self.target())
        self.assertEqual((r['status'], r['videos_seen']), ('ok', 5))
        self.assertEqual(set(self.identities()), {self.a['identity']})
        self.sessions.drop.assert_not_called()

    def test_fixed_and_default_modes_use_one_account_without_switching(self):
        t = self.target('fixed', account_id=self.c['id'])
        self.run_target(t)
        self.assertEqual(set(self.identities()), {self.c['identity']})
        self.calls.clear()
        self.run_target(self.target('default', value=None) if False else repo.add_target(self.db, 'search', 'tiktok', 'hunde', max_comments=0))
        self.assertEqual(set(self.identities()), {self.a['identity']})


class FaultTests(RotationRunBase):
    def test_captcha_switches_at_once_and_retries_same_video(self):
        self.faults[('2', self.a['identity'])] = RuntimeError(CAPTCHA)
        r = self.run_target(self.target())
        self.assertEqual((r['status'], r['videos_seen']), ('ok', 5))
        main, zweit, dritt = self.a['identity'], self.b['identity'], self.c['identity']
        self.assertEqual(self.calls[:4], [('1', main), ('2', main), ('2', zweit), ('3', zweit)])
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'CAPTCHA')
        self.assertIsNotNone(self.row(self.a)['cooldown_until'])
        self.assertEqual(r['accounts_used'][:2], ['Hauptkonto', 'Zweit'])
        # Hauptkonto bleibt für den Rest des Laufs draußen (Abkühlung)
        self.assertNotIn(main, [i for v, i in self.calls if v in ('3', '4', '5')])

    def test_session_expired_switches_and_marks_login_cooldown(self):
        self.faults[('1', self.a['identity'])] = SessionExpired('abgelaufen')
        r = self.run_target(self.target(urls=URLS[:2]))
        self.assertEqual((r['status'], r['videos_seen']), ('ok', 2))
        self.assertEqual(self.calls[:2], [('1', self.a['identity']), ('1', self.b['identity'])])
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'Anmeldung abgelaufen')
        self.sessions.drop.assert_any_call(self.a['identity'], 'tiktok')

    def test_rate_limit_and_block_kinds(self):
        self.faults[('1', self.a['identity'])] = RuntimeError('HTTP Error 429: Too Many Requests')
        self.faults[('1', self.b['identity'])] = RuntimeError('Das Konto wurde gesperrt (Konto gesperrt).')
        self.run_target(self.target(urls=URLS[:1]))
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'zu viele Anfragen')
        self.assertEqual(self.row(self.b)['cooldown_reason'], 'Sperre')
        self.assertIsNone(self.row(self.c)['cooldown_reason'])
        self.assertEqual(self.identities(), [self.a['identity'], self.b['identity'], self.c['identity']])

    def test_each_video_once_per_account_and_all_exhausted_message(self):
        self.faults[('1', None)] = RuntimeError(CAPTCHA)          # alle Konten scheitern an Video 1
        r = self.run_target(self.target(urls=URLS[:3]))
        self.assertEqual(len([c for c in self.calls if c[0] == '1']), 3)          # je Konto genau einmal
        self.assertEqual(len({i for v, i in self.calls if v == '1'}), 3)
        self.assertEqual(r['status'], 'failed')
        self.assertIn('Alle Konten für TikTok sind gerade nicht verfügbar', r['error'])
        self.assertIn('wieder frei ab', r['error'])
        self.assertEqual([v for v, _ in self.calls], ['1', '1', '1'])              # danach keine weiteren Videos
        for acc in (self.a, self.b, self.c):
            self.assertEqual(self.row(acc)['cooldown_reason'], 'CAPTCHA')
        self.assertEqual(sorted(r['accounts_used']), ['Dritt', 'Hauptkonto', 'Zweit'])

    def test_single_403_is_a_video_problem_not_an_account_block(self):
        self.faults[('2', None)] = RuntimeError('HTTP Error 403: Forbidden')           # z. B. regionale Sperre dieses Videos
        r = self.run_target(self.target())
        self.assertEqual((r['status'], r['videos_seen']), ('partial', 4))
        main, zweit, dritt = self.a['identity'], self.b['identity'], self.c['identity']
        self.assertEqual(self.identities(), [main, main, zweit, zweit, dritt])      # Video 2 nur einmal versucht, kein Kontowechsel dafür
        for acc in (self.a, self.b, self.c):
            self.assertIsNone(self.row(acc)['cooldown_until'])

    def test_three_different_videos_with_403_in_a_row_cool_the_account_down(self):
        self.cfg.rotation_every = 10
        for n in ('1', '2', '3'):
            self.faults[(n, self.a['identity'])] = RuntimeError('HTTP Error 403: Forbidden')
        r = self.run_target(self.target())
        main, zweit = self.a['identity'], self.b['identity']
        self.assertEqual(self.identities(), [main, main, main, zweit, zweit, zweit])
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'Sperre')
        self.assertIsNotNone(self.row(self.a)['cooldown_until'])
        self.assertEqual(r['accounts_used'][:2], ['Hauptkonto', 'Zweit'])
        self.assertEqual(r['videos_seen'], 3)                       # Video 3 wird mit dem zweiten Konto erneut versucht und klappt

    def test_403_with_captcha_text_is_an_account_fault_at_once(self):
        self.faults[('2', self.a['identity'])] = RuntimeError('HTTP Error 403: Prove your humanity')
        self.run_target(self.target())
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'CAPTCHA')

    def test_non_account_error_does_not_switch_or_cool_down(self):
        self.faults[('1', self.a['identity'])] = RuntimeError('Das Video ist privat, gelöscht oder in diesem Land nicht verfügbar.')
        r = self.run_target(self.target(urls=URLS[:2]))
        self.assertEqual(r['status'], 'partial')
        self.assertEqual(self.identities(), [self.a['identity']] * 2)
        self.assertIsNone(self.row(self.a)['cooldown_until'])

    def test_partial_when_accounts_run_out_midway(self):
        self.faults[('3', None)] = RuntimeError(CAPTCHA)
        r = self.run_target(self.target())
        self.assertEqual(r['status'], 'partial')
        self.assertEqual(r['videos_seen'], 2)
        self.assertIn('Alle Konten', r['error'])

    def test_success_clears_failure_streak(self):
        rot_streak = lambda: self.row(self.b)['fail_streak']
        repo.get_account(self.db, self.b['id'])
        self.db.execute('UPDATE accounts SET fail_streak=3 WHERE id=?', (self.b['id'],)); self.db.commit()
        self.cfg.rotation_every = 1
        self.run_target(self.target(urls=URLS[:2]))
        self.assertEqual(rot_streak(), 0)

    def test_login_check_failure_skips_account_at_start_and_on_switch(self):
        self.auth[self.a['identity']] = State.EXPIRED
        r = self.run_target(self.target(urls=URLS[:3]))
        self.assertEqual(r['status'], 'ok')
        self.assertNotIn(self.a['identity'], self.identities())
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'Anmeldung abgelaufen')

    def test_missing_login_skips_account_without_cooldown(self):
        self.auth[self.a['identity']] = State.MISSING
        r = self.run_target(self.target(urls=URLS[:2]))
        self.assertEqual(r['status'], 'ok')
        self.assertNotIn(self.a['identity'], self.identities())
        self.assertIsNone(self.row(self.a)['cooldown_until'])            # „Anmeldung fehlt“ ist keine Abkühlung

    def test_all_logins_expired_fails_with_both_messages(self):
        for acc in (self.a, self.b, self.c):
            self.auth[acc['identity']] = State.EXPIRED
        r = self.run_target(self.target())
        self.assertEqual(r['status'], 'failed')
        self.assertIn('Anmeldung abgelaufen für Konto', r['error'])
        self.assertIn('Alle Konten für TikTok', r['error'])
        self.assertEqual(self.calls, [])

    def test_no_account_available_at_start_gives_message_with_time(self):
        from socialscraper import rotation
        for acc in (self.a, self.b, self.c):
            rotation.mark_failure(self.db, acc['id'], 'x', 'block')
        r = self.run_target(self.target())
        self.assertEqual(r['status'], 'failed')
        self.assertIn('wieder frei ab', r['error'])
        self.assertEqual(self.calls, [])

    def test_discovery_fault_switches_account(self):
        t = repo.add_target(self.db, 'search', 'tiktok', 'neu', account_mode='rotate', max_comments=0)
        seen = []

        def discover(kind, platform, value, ctx, limit):
            seen.append(ctx.identity)
            if len(seen) == 1:
                raise RuntimeError(CAPTCHA)
            return URLS[:1]
        with patch.object(scheduler.discovery, 'discover', side_effect=discover):
            r = self.run_target(t)
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(seen, [self.a['identity'], self.b['identity']])
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'CAPTCHA')


class ProxyModeRuleTests(RotationRunBase):
    def test_free_and_tor_modes_keep_accounts_locked(self):
        for mode in ('frei', 'tor'):
            self.cfg.proxy_mode = mode
            t = repo.add_target(self.db, 'search', 'tiktok', f'k{mode}', account_mode='rotate', max_comments=0)
            r = self.run_target(t)           # TikTok-Suche braucht ein Konto
            self.assertEqual(r['status'], 'failed')
            self.assertIn('keine Konten benutzt', r['error'])
            self.assertEqual(r['accounts_used'], [])
        self.assertEqual(self.calls, [])

    def test_free_mode_public_video_uses_anonymous_identity_not_rotation(self):
        self.cfg.proxy_mode = 'frei'
        t = repo.add_target(self.db, 'video', 'tiktok', URLS[0], account_mode='rotate', max_comments=0)
        r = self.run_target(t)
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(self.identities(), ['anon_public'])
        self.assertEqual(r['accounts_used'], [])
        self.assertEqual(self.row(self.a)['use_count'], 0)

    def test_list_and_off_modes_allow_rotation(self):
        for mode in ('aus', 'liste'):
            self.cfg.proxy_mode = mode
            self.cfg.proxies = ['http://127.0.0.1:9'] if mode == 'liste' else []
            self.calls.clear()
            r = self.run_target(self.target(urls=URLS[:2]) if mode == 'aus' else repo.add_target(
                self.db, 'search', 'tiktok', 'liste', account_mode='rotate', max_comments=0))
            self.assertEqual(r['status'], 'ok', r)


class NoRotationFaultTests(RotationRunBase):
    """Ohne Rotation (Standardkonto oder festes Konto) endet der Lauf beim ersten Kontofehler: das Konto ist gerade in
    die Abkühlung gegangen, weitere Videos mit ihm würden die Sperre nur verschärfen (ARCHITEKTUR.md: „sofortiger Stopp
    bei CAPTCHA/Sperre/429“). Die restlichen Videos bleiben offen; ein Problem eines einzelnen Videos stoppt nichts."""

    def test_fixed_account_stops_after_captcha_instead_of_hammering(self):
        self.faults[(None, self.c['identity'])] = RuntimeError(CAPTCHA)          # jedes Video: CAPTCHA
        r = self.run_target(self.target('fixed', urls=URLS[:4], account_id=self.c['id']))
        self.assertEqual(self.calls, [('1', self.c['identity'])])                 # vorher: alle vier Videos mit demselben Konto
        self.assertEqual(r['status'], 'failed')
        self.assertIn('CAPTCHA', r['error'])
        self.assertEqual(self.row(self.c)['cooldown_reason'], 'CAPTCHA')
        self.assertEqual(r['accounts_used'], ['Dritt'])

    def test_default_account_stops_after_rate_limit_and_keeps_what_it_has(self):
        self.faults[('2', self.a['identity'])] = RuntimeError('HTTP Error 429: Too Many Requests')
        r = self.run_target(self.target('default', urls=URLS[:4]))
        self.assertEqual(self.calls, [('1', self.a['identity']), ('2', self.a['identity'])])
        self.assertEqual((r['status'], r['videos_seen']), ('partial', 1))
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'zu viele Anfragen')
        self.assertIsNone(self.row(self.b)['cooldown_until'])                    # kein Wechsel: andere Konten bleiben unberührt

    def test_video_problem_does_not_stop_the_run_without_rotation(self):
        self.faults[('2', self.a['identity'])] = RuntimeError('HTTP Error 403: Forbidden')   # einzelnes 403: Problem des Videos
        r = self.run_target(self.target('default', urls=URLS[:3]))
        self.assertEqual([v for v, _ in self.calls], ['1', '2', '3'])
        self.assertEqual((r['status'], r['videos_seen']), ('partial', 2))
        self.assertIsNone(self.row(self.a)['cooldown_until'])


if __name__ == '__main__':
    unittest.main()
