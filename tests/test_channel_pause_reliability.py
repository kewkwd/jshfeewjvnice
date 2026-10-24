"""R45: automatic scheduling may extend a pause, but must not shorten it."""
import contextlib
import datetime
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from socialscraper import channels, repo, scheduler
from socialscraper.config import Settings
from socialscraper.store import Store


UTC = datetime.timezone.utc


class _MinimumRng:
    def uniform(self, low, high):
        return low


class ChannelPauseReliability(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        for method in ('connect', 'connect_ex', 'sendto', 'bind'):
            self.stack.enter_context(patch.object(socket.socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Process launch blocked')))
        self.stack.enter_context(patch('os.system', side_effect=AssertionError('Process launch blocked')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='channel-pause-')))
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='',
            profiles_dir=str(self.root / 'profiles'))
        self.store = self.stack.enter_context(Store(self.cfg.db_path))
        self.db = self.store.db
        self.now = datetime.datetime(2026, 10, 15, 12, tzinfo=UTC)
        self.stack.enter_context(patch.object(repo, 'now', side_effect=lambda: self.now))
        # Keep real quiet-hour calculations deterministic on every host timezone.
        self.stack.enter_context(patch.object(channels, '_local', side_effect=lambda value: value.astimezone(UTC)))
        self.stack.enter_context(patch.object(channels, '_at_local',
            side_effect=lambda day, hour=0: datetime.datetime.combine(day, datetime.time(hour), tzinfo=UTC)))
        self.rng = _MinimumRng()
        self.sequence = 0

    def target(self, *, state='listing', quiet_hours=None):
        self.sequence += 1
        value = 'https://www.youtube.com/@offline_pause_' + str(self.sequence)
        ref = channels.ChannelRef('youtube', 'channel', value, '@offline_pause', value)
        target = channels.create_channel(self.db, ref, comments=0, quiet_hours=quiet_hours)
        repo.update_channel(self.db, target['id'], backfill_state=state)
        return repo.get_target(self.db, target['id'])

    def row(self, target):
        return repo.get_target(self.db, target['id'])

    def test_shorter_repeated_pause_preserves_deadline_reason_and_resume_state(self):
        for hours in (1, 2, -1):
            with self.subTest(new_hours=hours):
                target = self.target(state='listing')
                later = self.now + datetime.timedelta(hours=6)
                channels.pause_channel(self.db, target['id'], later, 'Original CAPTCHA pause', now=self.now)
                channels.pause_channel(self.db, target['id'], self.now + datetime.timedelta(hours=hours),
                    'Short transport retry', now=self.now)
                row = self.row(target)
                self.assertEqual(repo.parse(row['pause_until']), later)
                self.assertEqual(repo.parse(row['next_run_at']), later)
                self.assertEqual(row['pause_reason'], 'Original CAPTCHA pause')
                self.assertEqual((row['backfill_state'], row['resume_state']), ('paused', 'listing'))

    def test_equal_pause_preserves_the_existing_meaningful_reason(self):
        target = self.target(state='watching')
        later = self.now + datetime.timedelta(hours=6)
        channels.pause_channel(self.db, target['id'], later, 'Original manual-check pause', now=self.now)
        channels.pause_channel(self.db, target['id'], later, 'Repeated generic fault', now=self.now)
        row = self.row(target)
        self.assertEqual(repo.parse(row['pause_until']), later)
        self.assertEqual(row['pause_reason'], 'Original manual-check pause')
        self.assertEqual(row['resume_state'], 'watching')

    def test_longer_pause_extends_deadline_and_reason_without_losing_resume_state(self):
        target = self.target(state='fetching')
        earlier = self.now + datetime.timedelta(hours=1)
        later = self.now + datetime.timedelta(hours=6)
        channels.pause_channel(self.db, target['id'], earlier, 'Short retry', now=self.now)
        channels.pause_channel(self.db, target['id'], later, 'Longer provider pause', now=self.now)
        row = self.row(target)
        self.assertEqual(repo.parse(row['pause_until']), later)
        self.assertEqual(repo.parse(row['next_run_at']), later)
        self.assertEqual(row['pause_reason'], 'Longer provider pause')
        self.assertEqual((row['backfill_state'], row['resume_state']), ('paused', 'fetching'))

    def test_reschedule_uses_the_later_active_pause_or_explicit_wait(self):
        cases = [(360, None, 360), (360, 60, 360), (360, 480, 480),
                 (30, None, 30), (360, -60, 360)]
        for pause_minutes, wait_minutes, expected_minutes in cases:
            with self.subTest(pause=pause_minutes, wait=wait_minutes):
                target = self.target()
                until = self.now + datetime.timedelta(minutes=pause_minutes)
                channels.pause_channel(self.db, target['id'], until, 'Stored pause', now=self.now)
                wait = self.now + datetime.timedelta(minutes=wait_minutes) if wait_minutes is not None else None
                result = channels.reschedule(self.db, target['id'], 'cancelled', now=self.now,
                    rng=self.rng, wait_until=wait)
                expected = self.now + datetime.timedelta(minutes=expected_minutes)
                self.assertEqual(result, expected)
                row = self.row(target)
                self.assertEqual(repo.parse(row['next_run_at']), expected)
                self.assertEqual(repo.parse(row['pause_until']), until)
                self.assertEqual((row['pause_reason'], row['resume_state']), ('Stored pause', 'listing'))

    def test_default_hour_is_used_only_without_a_future_pause_or_wait(self):
        cases = [(None, None, 60), (-60, None, 60), (-60, -30, 60),
                 (None, -30, 60), (-60, 30, 30)]
        for pause_minutes, wait_minutes, expected_minutes in cases:
            with self.subTest(pause=pause_minutes, wait=wait_minutes):
                target = self.target()
                pause = self.now + datetime.timedelta(minutes=pause_minutes) if pause_minutes is not None else None
                repo.update_channel(self.db, target['id'], backfill_state='paused',
                    pause_until=repo.iso(pause) if pause else None, pause_reason='Old pause', resume_state='listing')
                wait = self.now + datetime.timedelta(minutes=wait_minutes) if wait_minutes is not None else None
                result = channels.reschedule(self.db, target['id'], 'cancelled', now=self.now,
                    rng=self.rng, wait_until=wait)
                expected = self.now + datetime.timedelta(minutes=expected_minutes)
                self.assertEqual(result, expected)
                self.assertEqual(repo.parse(self.row(target)['next_run_at']), expected)

    def test_quiet_hours_extend_the_selected_pause_and_never_replace_it_with_an_earlier_time(self):
        for wait_hours, expected in ((None, self.now.replace(hour=20, minute=2)),
                                    (9, self.now.replace(hour=21))):
            with self.subTest(wait_hours=wait_hours):
                target = self.target(quiet_hours='17-20')
                until = self.now + datetime.timedelta(hours=6)
                channels.pause_channel(self.db, target['id'], until, 'Stored pause', now=self.now)
                wait = self.now + datetime.timedelta(hours=wait_hours) if wait_hours is not None else None
                result = channels.reschedule(self.db, target['id'], 'cancelled', now=self.now,
                    rng=self.rng, wait_until=wait)
                self.assertEqual(result, expected)
                self.assertGreaterEqual(result, until)
                self.assertEqual(repo.parse(self.row(target)['pause_until']), until)

    def test_explicit_resume_can_end_a_future_pause_and_restore_the_prior_state(self):
        for force in (False, True):
            with self.subTest(force=force):
                target = self.target(state='watching')
                channels.pause_channel(self.db, target['id'], self.now + datetime.timedelta(hours=6),
                    'Stored pause', now=self.now)
                channels.resume_channel(self.db, target['id'], now=self.now, force=force)
                row = self.row(target)
                self.assertEqual(row['backfill_state'], 'watching')
                self.assertEqual((row['pause_until'], row['pause_reason'], row['resume_state']), (None, None, None))
                self.assertEqual(repo.parse(row['next_run_at']), self.now)
                self.assertEqual((row['enabled'], row['force_once']), (1, int(force)))
                self.assertIsNone(channels.gate(self.db, row, now=self.now, rng=self.rng))

    def test_scheduler_gate_prevents_execution_while_the_persisted_pause_is_active(self):
        target = self.target()
        until = self.now + datetime.timedelta(hours=6)
        channels.pause_channel(self.db, target['id'], until, 'Stored pause', now=self.now)
        channels.reschedule(self.db, target['id'], 'cancelled', now=self.now,
            rng=self.rng, wait_until=self.now + datetime.timedelta(minutes=15))
        with patch.object(scheduler, '_execute', side_effect=AssertionError('Execution during active pause')) as execute, \
                patch.object(channels, 'ChannelRun', side_effect=AssertionError('Channel work during active pause')) as run, \
                patch.object(repo, 'start_run', wraps=repo.start_run) as start_run:
            result = scheduler.run_target(self.root, target['id'], cfg=self.cfg)
            self.assertIsNone(result['run_id'])
            self.assertIn('pausiert bis', result['waiting'])
            self.assertEqual(result['videos_seen'], 0)
            execute.assert_not_called()
            run.assert_not_called()
            start_run.assert_not_called()
        self.assertEqual(repo.parse(self.row(target)['next_run_at']), until)

    def test_channel_run_finish_preserves_a_longer_pause_created_after_run_start(self):
        for fault in (False, True):
            with self.subTest(short_fault=fault):
                target = self.target(state='fetching')
                run = channels.ChannelRun(self.db, target, rng=self.rng, clock=lambda: self.now)
                until = self.now + datetime.timedelta(hours=6)
                channels.pause_channel(self.db, target['id'], until, 'Original CAPTCHA pause', now=self.now)
                if fault:
                    run.stop_fault = 'ratelimit'
                    with patch.object(run, '_availability', return_value=self.now + datetime.timedelta(hours=1)):
                        result = run.finish('failed', 'HTTP 429')
                else:
                    result = run.finish('cancelled', None)
                row = self.row(target)
                self.assertEqual(result, until)
                self.assertEqual(repo.parse(row['next_run_at']), until)
                self.assertEqual(repo.parse(row['pause_until']), until)
                self.assertEqual((row['pause_reason'], row['resume_state']), ('Original CAPTCHA pause', 'fetching'))


if __name__ == '__main__':
    unittest.main()
