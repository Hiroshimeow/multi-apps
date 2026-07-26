import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PyQt6.QtCore import QRect, QTimer
from PyQt6.QtWidgets import QApplication

from lib.ui.log_hover import LogHoverController, LogTarget
from lib.ui.log_popup import LogPopupWindow
from lib.ui.log_preferences import LogPanelPreference
from lib.ui.log_preference_owner import LogPreferenceOwner


_QT_APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return bool(predicate())


class RecordingBackend:
    def __init__(self, initial=None):
        self.initial = dict(initial or {})
        self.load_calls = 0
        self.load_threads = []
        self.load_started = threading.Event()
        self.release_load = threading.Event()
        self.release_load.set()
        self.write_calls = []
        self.write_threads = []
        self.write_started = threading.Event()
        self.release_write = threading.Event()
        self.release_write.set()
        self.fail_writes = 0
        self._call_lock = threading.Lock()
        self._active_calls = 0
        self.max_concurrent_calls = 0

    def _enter_call(self):
        with self._call_lock:
            self._active_calls += 1
            self.max_concurrent_calls = max(
                self.max_concurrent_calls,
                self._active_calls,
            )

    def _leave_call(self):
        with self._call_lock:
            self._active_calls -= 1

    def load_all(self):
        self._enter_call()
        try:
            self.load_calls += 1
            self.load_threads.append(threading.get_ident())
            self.load_started.set()
            self.release_load.wait(timeout=2)
            return dict(self.initial)
        finally:
            self._leave_call()

    def replace_all(self, apps):
        self._enter_call()
        try:
            self.write_threads.append(threading.get_ident())
            self.write_calls.append(dict(apps))
            self.write_started.set()
            self.release_write.wait(timeout=2)
            if self.fail_writes:
                self.fail_writes -= 1
                return False
            self.initial = dict(apps)
            return True
        finally:
            self._leave_call()


class FinalExitGateCondition(threading.Condition):
    """Expose whether final exit released the lock before ownership cleanup."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.armed = False
        self.mode = None
        self.boundary_reached = threading.Event()
        self.release_unsafe_gap = threading.Event()

    def arm(self):
        self.armed = True

    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        worker = threading.current_thread()
        owner = self.owner
        if (
            self.armed
            and worker.name == "log-preference-writer"
            and owner._generation > 0
            and owner._generation == owner._persisted_generation
        ):
            if owner._thread is worker and owner._closing:
                self.mode = "unsafe"
            elif owner._thread is None and not owner._closing:
                self.mode = "atomic"
            else:
                return result
            self.armed = False
            self.boundary_reached.set()
            if self.mode == "unsafe":
                self.release_unsafe_gap.wait(timeout=2)
        return result


class ManualReader:
    class Signal:
        def connect(self, _callback):
            pass

        def disconnect(self, _callback):
            pass

    def __init__(self):
        self.snapshot_ready = self.Signal()
        self.next_id = 0

    def request(self, _source, *, max_lines, max_bytes):
        self.next_id += 1
        return self.next_id

    def shutdown(self):
        pass


class FakeManager:
    name = "Demo"

    def __init__(self, path):
        self.path = Path(path)

    def get_log_path(self, _stream):
        return str(self.path)


class LogPreferenceOwnerTests(unittest.TestCase):
    def test_viewer_close_requests_shutdown_without_waiting_for_blocked_load(self):
        backend = RecordingBackend()
        backend.release_load.clear()
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        popup = LogPopupWindow()
        controller = LogHoverController(
            popup,
            owner,
            placement_provider=lambda: (
                QRect(600, 400, 620, 300),
                QRect(0, 0, 1400, 900),
            ),
            reader=ManualReader(),
            refresh_interval_ms=10000,
        )
        main_thread = threading.get_ident()
        heartbeat = []
        safety_release = threading.Timer(1.0, backend.release_load.set)
        try:
            self.assertTrue(hasattr(owner, "request_shutdown"))
            controller.viewer_closed.connect(owner.request_shutdown)
            with TemporaryDirectory() as temp_dir:
                target = LogTarget("demo", FakeManager(Path(temp_dir) / "demo.log"))
                safety_release.start()
                controller.open_target(target, "stderr")
                self.assertTrue(backend.load_started.wait(timeout=1))

                controller.hide_popup()
                self.assertFalse(
                    backend.release_load.is_set(),
                    "viewer close waited for blocked preference load",
                )

                QTimer.singleShot(0, lambda: heartbeat.append(threading.get_ident()))
                self.assertTrue(wait_until(lambda: bool(heartbeat)))
                self.assertEqual(heartbeat, [main_thread])
                self.assertNotEqual(backend.load_threads, [main_thread])

                backend.release_load.set()
                self.assertTrue(wait_until(lambda: not owner.is_alive()))
        finally:
            backend.release_load.set()
            safety_release.cancel()
            controller.shutdown()
            owner.shutdown()
            popup.deleteLater()
            QApplication.processEvents()

    def test_loaded_preference_is_applied_only_to_the_current_target(self):
        backend = RecordingBackend(
            {
                "first": LogPanelPreference(10, "first-filter", "stdout"),
                "second": LogPanelPreference(5000, "second-filter", "stderr"),
            }
        )
        backend.release_load.clear()
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        popup = LogPopupWindow()
        controller = LogHoverController(
            popup,
            owner,
            placement_provider=lambda: (
                QRect(600, 400, 620, 300),
                QRect(0, 0, 1400, 900),
            ),
            reader=ManualReader(),
            refresh_interval_ms=10000,
        )
        safety_release = threading.Timer(1.0, backend.release_load.set)
        try:
            with TemporaryDirectory() as temp_dir:
                first = LogTarget("first", FakeManager(Path(temp_dir) / "first.log"))
                second = LogTarget("second", FakeManager(Path(temp_dir) / "second.log"))
                safety_release.start()
                controller.open_target(first, "stdout")
                controller.open_target(second, "stderr")
                self.assertFalse(backend.release_load.is_set())

                backend.release_load.set()
                self.assertTrue(
                    wait_until(
                        lambda: popup.panel.line_count.value() == 5000
                        and popup.panel.filter_edit.text() == "second-filter"
                    )
                )
                self.assertIs(controller.current_target, second)
                self.assertEqual(controller.current_stream, "stderr")
                self.assertNotEqual(popup.panel.filter_edit.text(), "first-filter")
        finally:
            backend.release_load.set()
            safety_release.cancel()
            controller.shutdown()
            owner.shutdown()
            popup.deleteLater()
            QApplication.processEvents()

    def test_last_consumer_release_does_not_wait_for_blocked_write(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        popup = LogPopupWindow()
        controller = LogHoverController(
            popup,
            owner,
            placement_provider=lambda: (
                QRect(600, 400, 620, 300),
                QRect(0, 0, 1400, 900),
            ),
            reader=ManualReader(),
            refresh_interval_ms=10000,
        )
        heartbeat = []
        safety_release = threading.Timer(1.0, backend.release_write.set)
        try:
            self.assertTrue(hasattr(owner, "request_shutdown"))
            controller.viewer_closed.connect(owner.request_shutdown)
            with TemporaryDirectory() as temp_dir:
                target = LogTarget("demo", FakeManager(Path(temp_dir) / "demo.log"))
                controller.open_target(target, "stderr")
                self.assertTrue(wait_until(owner.is_loaded))

                backend.release_write.clear()
                safety_release.start()
                popup.panel.filter_edit.setText("durable")
                self.assertTrue(backend.write_started.wait(timeout=1))
                expected = LogPanelPreference(
                    popup.panel.line_count.value(),
                    "durable",
                    "stderr",
                )

                controller.hide_popup()
                self.assertFalse(
                    backend.release_write.is_set(),
                    "last viewer close waited for blocked preference write",
                )
                QTimer.singleShot(0, lambda: heartbeat.append(True))
                self.assertTrue(wait_until(lambda: bool(heartbeat)))
                self.assertFalse(backend.release_write.is_set())

                backend.release_write.set()
                self.assertTrue(
                    wait_until(
                        lambda: backend.initial.get("demo") == expected
                        and not owner.is_alive()
                    )
                )
        finally:
            backend.release_write.set()
            safety_release.cancel()
            controller.shutdown()
            owner.shutdown()
            popup.deleteLater()
            QApplication.processEvents()

    def test_reopen_during_final_drain_keeps_new_generation_on_same_worker(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=60)
        popup = LogPopupWindow()
        controller = LogHoverController(
            popup,
            owner,
            placement_provider=lambda: (
                QRect(600, 400, 620, 300),
                QRect(0, 0, 1400, 900),
            ),
            reader=ManualReader(),
            refresh_interval_ms=10000,
        )
        safety_release = threading.Timer(1.0, backend.release_write.set)
        try:
            controller.viewer_closed.connect(owner.request_shutdown)
            with TemporaryDirectory() as temp_dir:
                first = LogTarget("first", FakeManager(Path(temp_dir) / "first.log"))
                second = LogTarget("second", FakeManager(Path(temp_dir) / "second.log"))
                controller.open_target(first, "stdout")
                self.assertTrue(wait_until(owner.is_loaded))
                worker = owner._thread

                backend.release_write.clear()
                safety_release.start()
                popup.panel.filter_edit.setText("first-pending")
                controller.hide_popup()
                self.assertTrue(backend.write_started.wait(timeout=1))
                self.assertFalse(backend.release_write.is_set())

                controller.open_target(second, "stderr")
                self.assertIs(owner._thread, worker)
                owner.debounce_seconds = 0
                popup.panel.filter_edit.setText("second-new")
                expected = LogPanelPreference(
                    popup.panel.line_count.value(),
                    "second-new",
                    "stderr",
                )

                backend.release_write.set()
                self.assertTrue(
                    wait_until(lambda: backend.initial.get("second") == expected)
                )
                self.assertEqual(len(backend.write_calls), 2)
                self.assertEqual(backend.max_concurrent_calls, 1)
                self.assertTrue(owner.is_alive())

                controller.hide_popup()
                self.assertTrue(wait_until(lambda: not owner.is_alive()))
        finally:
            backend.release_write.set()
            safety_release.cancel()
            controller.shutdown()
            owner.shutdown()
            popup.deleteLater()
            QApplication.processEvents()

    def test_reopen_at_final_exit_boundary_does_not_strand_generation(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        gate = FinalExitGateCondition(owner)
        owner._condition = gate
        first = LogPanelPreference(10, "first", "stdout")
        second = LogPanelPreference(5000, "second", "stderr")
        try:
            self.assertTrue(owner.save("first", first))
            self.assertTrue(wait_until(lambda: backend.initial.get("first") == first))
            worker = owner._thread

            gate.arm()
            owner.request_shutdown()
            self.assertTrue(gate.boundary_reached.wait(timeout=1))
            self.assertIn(gate.mode, {"unsafe", "atomic"})
            if gate.mode == "atomic":
                self.assertTrue(wait_until(lambda: not worker.is_alive()))

            self.assertTrue(owner.save("second", second))
            if gate.mode == "unsafe":
                self.assertIs(owner._thread, worker)
                gate.release_unsafe_gap.set()

            self.assertTrue(
                wait_until(lambda: backend.initial.get("second") == second),
                "reopen/save at final exit stranded a pending generation",
            )
            self.assertEqual(backend.max_concurrent_calls, 1)
            owner.request_shutdown()
            self.assertTrue(wait_until(lambda: not owner.is_alive()))
        finally:
            gate.release_unsafe_gap.set()
            owner.shutdown()

    def test_reopen_during_drain_reuses_single_worker_and_preserves_latest_target(self):
        backend = RecordingBackend(
            {
                "first": LogPanelPreference(10, "first-old", "stdout"),
                "second": LogPanelPreference(10, "second-old", "stdout"),
            }
        )
        backend.release_load.clear()
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        popup = LogPopupWindow()
        controller = LogHoverController(
            popup,
            owner,
            placement_provider=lambda: (
                QRect(600, 400, 620, 300),
                QRect(0, 0, 1400, 900),
            ),
            reader=ManualReader(),
            refresh_interval_ms=10000,
        )
        safety_release = threading.Timer(1.0, backend.release_load.set)
        try:
            self.assertTrue(hasattr(owner, "request_shutdown"))
            controller.viewer_closed.connect(owner.request_shutdown)
            with TemporaryDirectory() as temp_dir:
                first = LogTarget("first", FakeManager(Path(temp_dir) / "first.log"))
                second = LogTarget("second", FakeManager(Path(temp_dir) / "second.log"))
                safety_release.start()
                controller.open_target(first, "stdout")
                self.assertTrue(backend.load_started.wait(timeout=1))
                worker = owner._thread

                controller.hide_popup()
                controller.open_target(second, "stderr")
                self.assertIs(owner._thread, worker)
                self.assertEqual(
                    sum(
                        thread.is_alive() and thread.name == "log-preference-writer"
                        for thread in threading.enumerate()
                    ),
                    1,
                )

                popup.panel.filter_edit.setText("second-new")
                expected = LogPanelPreference(
                    popup.panel.line_count.value(),
                    "second-new",
                    "stderr",
                )
                backend.release_load.set()

                self.assertTrue(
                    wait_until(lambda: backend.initial.get("second") == expected)
                )
                self.assertIs(controller.current_target, second)
                self.assertEqual(controller.current_stream, "stderr")
                self.assertEqual(popup.panel.filter_edit.text(), "second-new")
                self.assertNotEqual(popup.panel.filter_edit.text(), "first-old")
                self.assertEqual(backend.max_concurrent_calls, 1)
                self.assertTrue(owner.is_alive())

                controller.hide_popup()
                self.assertTrue(wait_until(lambda: not owner.is_alive()))
        finally:
            backend.release_load.set()
            safety_release.cancel()
            controller.shutdown()
            owner.shutdown()
            popup.deleteLater()
            QApplication.processEvents()

    def test_rapid_updates_coalesce_to_one_final_whole_file_write(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=0.05)
        try:
            for value in ("a", "ab", "abc", "final"):
                owner.save("demo", LogPanelPreference(100, value, "stdout"))
            self.assertTrue(wait_until(lambda: len(backend.write_calls) == 1))
            self.assertEqual(
                backend.write_calls[0]["demo"],
                LogPanelPreference(100, "final", "stdout"),
            )
        finally:
            owner.shutdown()

    def test_default_debounce_coalesces_slow_filter_typing(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend)
        try:
            for value in ("k", "ke", "kee", "keep"):
                owner.save("demo", LogPanelPreference(5000, value, "stderr"))
                time.sleep(0.2)
                self.assertEqual(backend.write_calls, [])
            self.assertTrue(wait_until(lambda: len(backend.write_calls) == 1, timeout=2))
            self.assertEqual(
                backend.write_calls[0]["demo"],
                LogPanelPreference(5000, "keep", "stderr"),
            )
        finally:
            owner.shutdown()
        self.assertFalse(owner.is_alive())

    def test_different_apps_keep_all_latest_values_in_one_snapshot(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=0.02)
        try:
            owner.save("hover-app", LogPanelPreference(5000, "hover", "stderr"))
            owner.save("pinned-app", LogPanelPreference(10, "pinned", "stdout"))
            self.assertTrue(wait_until(lambda: len(backend.write_calls) == 1))
            self.assertEqual(
                backend.write_calls[0],
                {
                    "hover-app": LogPanelPreference(5000, "hover", "stderr"),
                    "pinned-app": LogPanelPreference(10, "pinned", "stdout"),
                },
            )
        finally:
            owner.shutdown()

    def test_explicit_flush_waits_for_durable_snapshot(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=60)
        try:
            expected = LogPanelPreference(5000, "flush", "stderr")
            owner.save("demo", expected)
            self.assertTrue(owner.flush(timeout=1))
            self.assertEqual(backend.initial["demo"], expected)
        finally:
            owner.shutdown()

    def test_final_shutdown_flushes_and_joins_worker(self):
        backend = RecordingBackend()
        backend.release_write.clear()
        owner = LogPreferenceOwner(backend, debounce_seconds=60)
        expected = LogPanelPreference(5000, "latest", "stderr")
        shutdown_done = threading.Event()
        safety_release = threading.Timer(1.0, backend.release_write.set)
        owner.save("demo", expected)
        thread = threading.Thread(
            target=lambda: (owner.shutdown(), shutdown_done.set()),
            daemon=True,
        )
        try:
            safety_release.start()
            thread.start()
            self.assertTrue(backend.write_started.wait(timeout=1))
            self.assertFalse(shutdown_done.is_set())

            backend.release_write.set()
            thread.join(timeout=1)
            self.assertTrue(shutdown_done.is_set())
            owner.shutdown()

            self.assertEqual(len(backend.write_calls), 1)
            self.assertEqual(backend.write_calls[0]["demo"], expected)
            self.assertFalse(owner.is_alive())
        finally:
            backend.release_write.set()
            safety_release.cancel()
            thread.join(timeout=1)
            owner.shutdown()

    def test_failed_write_keeps_memory_and_later_state_is_not_defaulted(self):
        backend = RecordingBackend()
        backend.fail_writes = 1
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        first = LogPanelPreference(5000, "first", "stderr")
        second = LogPanelPreference(10, "second", "stdout")
        try:
            owner.save("demo", first)
            self.assertTrue(wait_until(lambda: len(backend.write_calls) == 1))
            self.assertEqual(owner.load("demo"), first)

            owner.save("demo", second)
            self.assertTrue(wait_until(lambda: len(backend.write_calls) == 2))
            self.assertEqual(owner.load("demo"), second)
            self.assertEqual(backend.initial["demo"], second)
        finally:
            owner.shutdown()


if __name__ == "__main__":
    unittest.main()
