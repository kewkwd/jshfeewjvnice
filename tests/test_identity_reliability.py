"""Local profile consistency and concurrent atomic persistence regressions."""
import contextlib
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from socialscraper.net import fingerprint, identity


class IdentityReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            self.stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))

    def test_editing_one_profile_language_list_preserves_other_profiles_and_defaults(self):
        for country in ('de', 'us'):
            with self.subTest(country=country):
                defaults = fingerprint.GEO[country][2]
                original_languages = list(defaults)
                try:
                    edited = fingerprint.build('edited-fixture', country)
                    existing = fingerprint.build('existing-fixture', country, mobile=True)
                    edited.languages[0] = 'fixture-language'
                    edited.languages.append('additional-fixture-language')
                    created_later = fingerprint.build('later-fixture', country)
                    actual = {'existing_profile': existing.languages,
                              'later_profile': created_later.languages,
                              'country_defaults': fingerprint.GEO[country][2]}
                    expected = {name: original_languages for name in actual}
                    self.assertEqual(actual, expected)
                finally:
                    # A failing regression must not leak its mutation into the
                    # rest of the suite or into either subsequent country case.
                    defaults[:] = original_languages

    def test_concurrent_writers_publish_their_own_complete_json_without_collisions(self):
        target = self.root / 'shared-profile.json'
        previous = {'writer': 'previous', 'values': ['original']}
        target.write_text(json.dumps(previous), encoding='utf-8')
        payloads = {
            'first': {'writer': 'first', 'values': ['first-fixture'] * 41},
            'second': {'writer': 'second', 'values': ['second-fixture'] * 67},
        }
        ready_to_publish = threading.Barrier(2)
        write_lock = threading.Lock()
        commit_lock = threading.Lock()
        errors = []
        publications = []
        original_write = Path.write_text
        original_replace = identity.os.replace

        def orderly_write(path, data, *args, **kwargs):
            # Serializing individual staging writes makes the publication
            # collision deterministic without introducing malformed JSON noise.
            with write_lock:
                return original_write(path, data, *args, **kwargs)

        def publish(source, destination, *args, **kwargs):
            ready_to_publish.wait(timeout=5)
            with commit_lock:
                before = json.loads(target.read_text(encoding='utf-8'))
                staged = json.loads(Path(source).read_text(encoding='utf-8'))
                result = original_replace(source, destination, *args, **kwargs)
                after = json.loads(target.read_text(encoding='utf-8'))
                publications.append((threading.current_thread().name, before, staged, after))
                return result

        def write(name):
            try:
                identity._write(target, payloads[name])
            except Exception as error:
                errors.append((name, type(error).__name__))

        threads = [threading.Thread(target=write, args=(name,), name=name, daemon=True)
                   for name in payloads]
        with patch.object(Path, 'write_text', orderly_write), \
                patch.object(identity.os, 'replace', publish):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=7)
        self.assertFalse(any(thread.is_alive() for thread in threads), 'Local writer did not finish')
        self.assertEqual(errors, [], 'Concurrent profile writes must not fail or consume another staging file')
        self.assertEqual(len(publications), 2)
        for name, before, staged, after in publications:
            with self.subTest(writer=name):
                self.assertIn(before, [previous, *payloads.values()])
                self.assertEqual(staged, payloads[name])
                self.assertEqual(after, payloads[name])
        self.assertIn(json.loads(target.read_text(encoding='utf-8')), list(payloads.values()))
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_profile_write_preserves_an_unrelated_existing_temporary_file(self):
        target = self.root / 'profile.json'
        unrelated = self.root / 'profile.json.tmp'
        unrelated.write_text('UNRELATED_LOCAL_FIXTURE', encoding='utf-8')
        payload = {'writer': 'fixture', 'values': ['complete', 'document']}
        identity._write(target, payload)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')), payload)
        self.assertTrue(unrelated.exists(), 'A profile save must not consume a pre-existing neighboring file')
        self.assertEqual(unrelated.read_text(encoding='utf-8'), 'UNRELATED_LOCAL_FIXTURE')
        self.assertEqual(set(self.root.iterdir()), {target, unrelated})

    def assert_failed_write_preserves_files(self, failure_point):
        target = self.root / 'profile.json'
        unrelated = self.root / 'profile.json.tmp'
        original = b'{"writer": "original", "values": [1, 2, 3]}\r\n'
        neighbor = b'UNRELATED_LOCAL_TEMPORARY_FIXTURE\r\n'
        target.write_bytes(original)
        unrelated.write_bytes(neighbor)
        existing = {target, unrelated}
        staged_at_failure = []

        def fail(*args, **kwargs):
            staged_at_failure.extend(set(self.root.iterdir()) - existing)
            raise OSError('Offline simulated persistence failure')

        with patch.object(identity.os, failure_point, side_effect=fail) as failing_operation:
            with self.assertRaisesRegex(OSError, 'Offline simulated persistence failure'):
                identity._write(target, {'writer': 'replacement', 'values': ['new-fixture']})
            failing_operation.assert_called_once()
        self.assertEqual(len(staged_at_failure), 1, 'The failed write must own a separate staging file')
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(unrelated.read_bytes(), neighbor)
        self.assertEqual(set(self.root.iterdir()), existing)
        self.assertFalse(staged_at_failure[0].exists())

    def test_replace_failure_preserves_original_and_removes_only_owned_staging_file(self):
        self.assert_failed_write_preserves_files('replace')

    def test_fsync_failure_preserves_original_and_removes_only_owned_staging_file(self):
        self.assert_failed_write_preserves_files('fsync')


if __name__ == '__main__':
    unittest.main()
