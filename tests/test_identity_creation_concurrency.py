"""Concurrent first-use must not establish conflicting profiles for one identity."""
import contextlib
import json
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper.net import identity


class IdentityCreationConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))

    def managers(self, case):
        # Separate application runtimes may use different databases while
        # sharing profiles_dir; the persistence key is the shared profile path.
        profiles = self.root / case / 'profiles'
        pool = SimpleNamespace(anonymous=False,
                               acquire=MagicMock(side_effect=AssertionError('Unexpected proxy access')))
        return [identity.IdentityManager(pool, profiles_dir=profiles) for _ in range(2)]

    def concurrent_get(self, managers, configurations):
        start = threading.Barrier(3)
        first_builds = threading.Barrier(2)
        build = identity.build
        write = identity._write
        write_lock = threading.Lock()
        successes, errors = [], []
        result_lock = threading.Lock()

        def coordinated_build(*args, **kwargs):
            # Both unprotected first-use reads reach this point before either
            # save. A valid implementation may serialize the entire get: a
            # bounded barrier then releases its sole builder, without requiring
            # two builds or treating serialization as a test failure.
            try:
                first_builds.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
            return build(*args, **kwargs)

        def orderly_write(*args, **kwargs):
            # Keep this regression above the atomic-save layer: serialized
            # commits exclude an unrelated Windows rename/sharing race while
            # leaving both initial reads and their creation decisions concurrent.
            with write_lock:
                return write(*args, **kwargs)

        def get(index):
            try:
                start.wait(timeout=3)
                country, mobile = configurations[index]
                result = managers[index].get('shared-fixture', country=country, mobile=mobile)
                with result_lock:
                    successes.append((index, result))
            except Exception as error:
                with result_lock:
                    errors.append((index, error))

        threads = [threading.Thread(target=get, args=(index,), daemon=True) for index in range(2)]
        with patch.object(identity, 'build', side_effect=coordinated_build), \
                patch.object(identity, '_write', side_effect=orderly_write):
            for thread in threads:
                thread.start()
            start.wait(timeout=3)
            for thread in threads:
                thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads), 'Local identity creation did not finish')
        for manager in managers:
            manager.pool.acquire.assert_not_called()
        return successes, errors

    def assert_matches_persisted(self, manager, result):
        target = manager.base / 'shared-fixture.json'
        persisted = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(persisted, {'name': result.name, 'country': result.country,
                                     'mobile': result.mobile, 'fp': result.fp.to_dict()})
        self.assertEqual(list(manager.base.glob('*.tmp')), [])

    def test_conflicting_first_use_accepts_one_configuration_and_rejects_the_other(self):
        for case, configurations in (
                ('country-conflict', [('de', False), ('us', False)]),
                ('device-conflict', [('de', False), ('de', True)])):
            with self.subTest(case=case):
                managers = self.managers(case)
                successes, errors = self.concurrent_get(managers, configurations)
                self.assertEqual(len(successes), 1, 'One identity must not return two conflicting new profiles')
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0][1], ValueError)
                winner_index, winner = successes[0]
                self.assertEqual((winner.country, winner.mobile), configurations[winner_index])
                self.assert_matches_persisted(managers[0], winner)
                self.assertEqual(managers[1].get('shared-fixture', *configurations[winner_index]), winner)
                losing_index = errors[0][0]
                with self.assertRaises(ValueError):
                    managers[losing_index].get('shared-fixture', *configurations[losing_index])

    def test_matching_first_use_returns_the_same_complete_persisted_identity_to_both_callers(self):
        for mobile in (False, True):
            with self.subTest(mobile=mobile):
                managers = self.managers('matching-mobile-' + str(mobile))
                successes, errors = self.concurrent_get(managers, [('de', mobile), ('de', mobile)])
                self.assertEqual([(index, type(error).__name__) for index, error in errors], [])
                self.assertEqual(len(successes), 2)
                self.assertEqual(successes[0][1], successes[1][1])
                for _index, result in successes:
                    self.assert_matches_persisted(managers[0], result)

    def test_existing_profile_rejects_concurrent_country_and_device_conflicts_without_modification(self):
        managers = self.managers('existing-profile')
        original = managers[0].get('shared-fixture', 'de', False)
        target = managers[0].base / 'shared-fixture.json'
        before = target.read_bytes()
        successes, errors = self.concurrent_get(managers, [('us', False), ('de', True)])
        self.assertEqual(successes, [])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(error, ValueError) for _index, error in errors))
        self.assertEqual(target.read_bytes(), before)
        self.assert_matches_persisted(managers[1], original)

    def assert_retry_after_failed_creation(self, operation, error_type):
        managers = self.managers('retry-after-' + operation)
        with patch.object(identity, operation, side_effect=error_type('Offline creation failure')) as failed:
            with self.assertRaisesRegex(error_type, 'Offline creation failure'):
                managers[0].get('shared-fixture', 'de', False)
            failed.assert_called_once()
        # A second manager must be able to create the same identity after the
        # first operation unwinds, rather than inheriting a held file lock.
        result = managers[1].get('shared-fixture', 'de', False)
        self.assertEqual((result.name, result.country, result.mobile), ('shared-fixture', 'de', False))
        self.assert_matches_persisted(managers[0], result)

    def test_build_failure_releases_identity_for_a_later_successful_creation(self):
        self.assert_retry_after_failed_creation('build', RuntimeError)

    def test_write_failure_releases_identity_for_a_later_successful_creation(self):
        self.assert_retry_after_failed_creation('_write', OSError)

    def test_blocked_creation_does_not_block_a_different_identity_name(self):
        managers = self.managers('independent-names')
        first_is_building = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()
        original_build = identity.build
        results, errors = {}, []
        result_lock = threading.Lock()

        def held_build(name, country, mobile):
            if name == 'held-fixture':
                first_is_building.set()
                if not release_first.wait(timeout=5):
                    raise RuntimeError('Local fixture release timed out')
            return original_build(name, country, mobile)

        def get(index, name):
            try:
                result = managers[index].get(name, 'de', False)
                with result_lock:
                    results[name] = result
            except Exception as error:
                with result_lock:
                    errors.append((name, type(error).__name__))
            finally:
                if index == 1:
                    second_finished.set()

        first = threading.Thread(target=get, args=(0, 'held-fixture'), daemon=True)
        second = threading.Thread(target=get, args=(1, 'independent-fixture'), daemon=True)
        second_started = False
        with patch.object(identity, 'build', side_effect=held_build):
            first.start()
            try:
                self.assertTrue(first_is_building.wait(timeout=3), 'First local creation did not reach build')
                second.start()
                second_started = True
                finished_while_first_held = second_finished.wait(timeout=2)
            finally:
                release_first.set()
                first.join(timeout=5)
                if second_started:
                    second.join(timeout=5)
        self.assertFalse(first.is_alive() or second.is_alive(), 'Local identity creation did not finish')
        self.assertTrue(finished_while_first_held, 'A different identity must not wait for the first identity lock')
        self.assertEqual(errors, [])
        self.assertEqual(set(results), {'held-fixture', 'independent-fixture'})
        for name, result in results.items():
            stored = json.loads((managers[0].base / (name + '.json')).read_text(encoding='utf-8'))
            self.assertEqual(stored['name'], name)
            self.assertEqual(stored['fp'], result.fp.to_dict())
        self.assertEqual(list(managers[0].base.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
