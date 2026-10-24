"""Two-thread regression for initializing a new Windows byte-range lock file."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from socialscraper.runlock import OsLock


@unittest.skipUnless(os.name == 'nt', 'Windows mandatory byte-range locking')
class RunlockInitializationTests(unittest.TestCase):
    def test_stale_empty_file_observation_is_safe_after_another_descriptor_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'new.lock'
            path.touch()
            locks = {name: OsLock(path) for name in ('delayed', 'other')}
            first_ready = threading.Event()
            other_finished = threading.Event()
            attempts_finished = {name: threading.Event() for name in locks}
            release = threading.Event()
            results, errors, snapshots = {}, {}, []
            descriptors = set()
            descriptor_guard = threading.Lock()
            real_open, real_close, real_fstat = os.open, os.close, os.fstat
            delayed = None
            intercepted = False

            def tracked_open(filename, *args, **kwargs):
                fd = real_open(filename, *args, **kwargs)
                if Path(filename) == path:
                    with descriptor_guard:
                        descriptors.add(fd)
                return fd

            def tracked_close(fd):
                real_close(fd)
                with descriptor_guard:
                    descriptors.discard(fd)

            def delayed_fstat(fd):
                nonlocal intercepted
                observed = real_fstat(fd)
                if threading.current_thread() is delayed and not intercepted:
                    intercepted = True
                    snapshots.append(observed.st_size)
                    first_ready.set()
                    if not other_finished.wait(5):
                        raise AssertionError('The competing acquisition did not finish')
                return observed

            def attempt(name):
                try:
                    results[name] = locks[name].try_acquire({'owner': name})
                except BaseException as error:
                    errors[name] = (type(error).__name__, getattr(error, 'errno', None))
                finally:
                    attempts_finished[name].set()
                    if name == 'delayed':
                        # A corrected implementation may no longer call fstat.
                        first_ready.set()
                    else:
                        other_finished.set()
                    release.wait(5)
                    locks[name].release()

            delayed = threading.Thread(target=attempt, args=('delayed',), daemon=True)
            other = threading.Thread(target=attempt, args=('other',), daemon=True)
            with patch('os.open', side_effect=tracked_open), \
                    patch('os.close', side_effect=tracked_close), \
                    patch('os.fstat', side_effect=delayed_fstat):
                started = []
                try:
                    delayed.start()
                    started.append(delayed)
                    self.assertTrue(first_ready.wait(5), 'First acquisition did not reach its rendezvous')
                    other.start()
                    started.append(other)
                    self.assertTrue(other_finished.wait(5), 'Competing acquisition did not finish')
                    self.assertTrue(attempts_finished['delayed'].wait(5), 'Delayed acquisition did not finish')
                finally:
                    release.set()
                    other_finished.set()
                    for thread in started:
                        thread.join(5)
                    # The old _open failure leaks the not-yet-owned descriptor.
                    # Close only descriptors opened for this exact test file.
                    with descriptor_guard:
                        remaining = list(descriptors)
                        descriptors.clear()
                    for fd in remaining:
                        real_close(fd)

            self.assertFalse(any(thread.is_alive() for thread in started), 'Owned thread did not terminate')
            self.assertEqual(errors, {}, 'Concurrent lock initialization must not raise; snapshots=' + repr(snapshots))
            self.assertEqual(sorted(results.values()), [False, True], 'Exactly one contender must hold the lock')
            following = OsLock(path)
            try:
                self.assertTrue(following.try_acquire({'owner': 'following'}))
            finally:
                following.release()


if __name__ == '__main__':
    unittest.main()
