from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from lib.ui.runtime_refresh import RuntimeRefreshCoordinator


_QT_APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout_ms=1000):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(5)
    return bool(predicate())


class RuntimeRefreshCoordinatorTests(unittest.TestCase):
    def test_start_watches_one_directory_and_coalesces_bursts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            coordinator = RuntimeRefreshCoordinator(
                Path(temp_dir) / ".runtime",
                lambda: calls.append(time.monotonic()),
                debounce_ms=30,
                fallback_ms=10_000,
            )
            try:
                coordinator.start()
                self.assertTrue(coordinator.is_active)
                self.assertTrue(coordinator.fallback_timer.isActive())
                self.assertEqual(
                    coordinator.watcher.directories(),
                    [str((Path(temp_dir) / ".runtime").resolve())],
                )

                for _ in range(5):
                    coordinator.watcher.directoryChanged.emit(
                        str((Path(temp_dir) / ".runtime").resolve())
                    )
                self.assertTrue(wait_until(lambda: len(calls) == 1))
                QTest.qWait(50)
                QApplication.processEvents()
                self.assertEqual(len(calls), 1)
            finally:
                coordinator.stop()
                coordinator.deleteLater()
                QApplication.processEvents()

    def test_fallback_refreshes_only_while_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            coordinator = RuntimeRefreshCoordinator(
                Path(temp_dir) / ".runtime",
                lambda: calls.append(time.monotonic()),
                debounce_ms=10,
                fallback_ms=30,
            )
            try:
                coordinator.start()
                self.assertTrue(wait_until(lambda: len(calls) >= 1, timeout_ms=300))
                coordinator.stop()
                stopped_count = len(calls)
                QTest.qWait(80)
                QApplication.processEvents()
                self.assertEqual(len(calls), stopped_count)
            finally:
                coordinator.stop()
                coordinator.deleteLater()
                QApplication.processEvents()

    def test_stop_removes_paths_and_cancels_debounce_and_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []
            coordinator = RuntimeRefreshCoordinator(
                Path(temp_dir) / ".runtime",
                lambda: calls.append(time.monotonic()),
                debounce_ms=30,
                fallback_ms=50,
            )
            try:
                coordinator.start()
                coordinator.watcher.directoryChanged.emit(
                    str((Path(temp_dir) / ".runtime").resolve())
                )
                coordinator.stop()
                self.assertFalse(coordinator.is_active)
                self.assertEqual(coordinator.watcher.directories(), [])
                self.assertEqual(coordinator.watcher.files(), [])
                self.assertFalse(coordinator.debounce_timer.isActive())
                self.assertFalse(coordinator.fallback_timer.isActive())
                QTest.qWait(100)
                QApplication.processEvents()
                self.assertEqual(calls, [])
            finally:
                coordinator.stop()
                coordinator.deleteLater()
                QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
