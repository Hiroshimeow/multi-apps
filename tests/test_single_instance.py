import tempfile
import unittest
from pathlib import Path

from lib.runtime.single_instance import SingleInstanceLock


class FakeLockFile:
    def __init__(self, path, try_results, stale_result=False):
        self.path = path
        self.try_results = list(try_results)
        self.stale_result = stale_result
        self.try_calls = 0
        self.remove_calls = 0
        self.unlock_calls = 0

    def tryLock(self, timeout):
        self.try_calls += 1
        return self.try_results.pop(0)

    def removeStaleLockFile(self):
        self.remove_calls += 1
        return self.stale_result

    def unlock(self):
        self.unlock_calls += 1


class SingleInstanceLockTests(unittest.TestCase):
    def test_active_lock_is_not_removed(self):
        fake = FakeLockFile("unused", [False], stale_result=False)
        lock = SingleInstanceLock(
            Path(tempfile.gettempdir()) / "active.lock",
            lock_factory=lambda path: fake,
        )

        self.assertFalse(lock.acquire())
        self.assertFalse(lock.acquired)
        self.assertEqual(fake.try_calls, 1)
        self.assertEqual(fake.remove_calls, 1)

    def test_stale_lock_is_removed_and_retried_once(self):
        fake = FakeLockFile("unused", [False, True], stale_result=True)
        lock = SingleInstanceLock(
            Path(tempfile.gettempdir()) / "stale.lock",
            lock_factory=lambda path: fake,
        )

        self.assertTrue(lock.acquire())
        self.assertTrue(lock.acquired)
        self.assertEqual(fake.try_calls, 2)
        self.assertEqual(fake.remove_calls, 1)

        lock.release()
        self.assertFalse(lock.acquired)
        self.assertEqual(fake.unlock_calls, 1)

    def test_real_lock_rejects_second_holder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".runtime" / "launcher.lock"
            first = SingleInstanceLock(path)
            second = SingleInstanceLock(path)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()
