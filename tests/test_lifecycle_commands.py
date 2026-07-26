import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import unittest

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from lib.ui.lifecycle_commands import LifecycleCommandController


_QT_APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout_ms=2000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())


class BlockingManager:
    def __init__(self, app_id):
        self.app_id = app_id
        self.name = app_id
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def stop_all(self):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=2)
        return True


class LifecycleCommandControllerTests(unittest.TestCase):
    def test_stop_is_nonblocking_deduplicated_and_gui_heartbeat_continues(self):
        controller = LifecycleCommandController()
        manager = BlockingManager("demo")
        gui_callback_ran = []
        safety_release = threading.Timer(1.0, manager.release.set)
        try:
            safety_release.start()
            self.assertTrue(controller.request_stop(manager))
            self.assertFalse(manager.release.is_set(), "request_stop waited for blocking work")
            self.assertFalse(controller.request_stop(manager))
            self.assertTrue(manager.started.wait(timeout=1))

            QTimer.singleShot(0, lambda: gui_callback_ran.append(time.monotonic()))
            self.assertTrue(wait_until(lambda: bool(gui_callback_ran), timeout_ms=300))
            self.assertTrue(controller.is_pending("demo"))
            self.assertFalse(manager.release.is_set(), "GUI heartbeat ran only after stop completed")
            self.assertEqual(manager.calls, 1)

            manager.release.set()
            self.assertTrue(wait_until(lambda: not controller.is_pending("demo")))
        finally:
            manager.release.set()
            safety_release.cancel()
            controller.shutdown()
        self.assertFalse(controller.is_alive())

    def test_stop_all_submits_each_app_once_without_blocking(self):
        controller = LifecycleCommandController()
        first = BlockingManager("first")
        second = BlockingManager("second")
        safety_release = threading.Timer(
            1.0,
            lambda: (first.release.set(), second.release.set()),
        )
        try:
            safety_release.start()
            accepted = controller.request_stop_all((first, second, first))
            self.assertEqual(accepted, 2)
            self.assertFalse(first.release.is_set(), "request_stop_all waited for blocking work")
            self.assertFalse(second.release.is_set(), "request_stop_all waited for blocking work")
            self.assertTrue(first.started.wait(timeout=1))
            self.assertEqual(set(controller.pending_ids()), {"first", "second"})
            self.assertEqual(first.calls, 1)
            self.assertEqual(second.calls, 0)

            first.release.set()
            self.assertTrue(second.started.wait(timeout=1))
            self.assertEqual(second.calls, 1)
            second.release.set()
            self.assertTrue(wait_until(lambda: not controller.pending_ids()))
        finally:
            first.release.set()
            second.release.set()
            safety_release.cancel()
            controller.shutdown()


if __name__ == "__main__":
    unittest.main()
