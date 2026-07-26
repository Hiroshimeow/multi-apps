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
        self.force_write_failure = False
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
            if self.force_write_failure:
                return False
            if self.fail_writes:
                self.fail_writes -= 1
                return False
            self.initial = dict(apps)
            return True
        finally:
            self._leave_call()


class BlockingSecondFailureBackend(RecordingBackend):
    def __init__(self):
        super().__init__()
        self.second_write_started = threading.Event()
        self.release_second_write = threading.Event()

    def replace_all(self, apps):
        self._enter_call()
        try:
            self.write_threads.append(threading.get_ident())
            self.write_calls.append(dict(apps))
            call_number = len(self.write_calls)
            self.write_started.set()
            if call_number == 2:
                self.second_write_started.set()
                self.release_second_write.wait(timeout=2)
            return False
        finally:
            self._leave_call()


class FinalExitGateCondition(threading.Condition):
    """Pause a writer after final-exit state is externally observable."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.armed = False
        self.mode = None
        self.boundary_reached = threading.Event()
        self.external_checked_owner = threading.Event()
        self.release_exit = threading.Event()

    def arm(self):
        self.armed = True

    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        current = threading.current_thread()
        owner = self.owner
        if self.boundary_reached.is_set() and current.name != "log-preference-writer":
            self.external_checked_owner.set()
        if (
            self.armed
            and current.name == "log-preference-writer"
            and owner._generation > 0
            and owner._generation == owner._persisted_generation
        ):
            if owner._thread is None and not owner._closing:
                self.mode = "released"
            elif owner._thread is current and getattr(owner, "_exiting", False):
                self.mode = "owned"
            else:
                return result
            self.armed = False
            self.boundary_reached.set()
            self.release_exit.wait(timeout=2)
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

    def test_reopen_at_final_exit_boundary_keeps_one_live_worker(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        gate = FinalExitGateCondition(owner)
        owner._condition = gate
        first = LogPanelPreference(10, "first", "stdout")
        second = LogPanelPreference(5000, "second", "stderr")
        demand_started = threading.Event()
        demand_done = threading.Event()
        demand_result = []

        def save_second():
            demand_started.set()
            demand_result.append(owner.save("second", second))
            demand_done.set()

        demand = threading.Thread(target=save_second, daemon=True)
        try:
            self.assertTrue(owner.save("first", first))
            self.assertTrue(wait_until(lambda: backend.initial.get("first") == first))
            worker = owner._thread

            gate.arm()
            owner.request_shutdown()
            self.assertTrue(gate.boundary_reached.wait(timeout=1))
            self.assertIn(gate.mode, {"released", "owned"})

            demand.start()
            self.assertTrue(demand_started.wait(timeout=1))
            self.assertTrue(gate.external_checked_owner.wait(timeout=1))
            self.assertIs(owner._thread, worker)
            self.assertFalse(demand_done.is_set())
            self.assertEqual(
                sum(
                    thread.is_alive() and thread.name == "log-preference-writer"
                    for thread in threading.enumerate()
                ),
                1,
            )

            gate.release_exit.set()
            demand.join(timeout=1)
            self.assertTrue(demand_done.is_set())
            self.assertEqual(demand_result, [True])
            self.assertTrue(
                wait_until(lambda: backend.initial.get("second") == second),
                "reopen/save at final exit stranded a pending generation",
            )
            self.assertEqual(backend.max_concurrent_calls, 1)
            owner.request_shutdown()
            self.assertTrue(wait_until(lambda: not owner.is_alive()))
        finally:
            gate.release_exit.set()
            demand.join(timeout=1)
            owner.shutdown()

    def test_final_shutdown_joins_worker_at_final_exit_boundary(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        gate = FinalExitGateCondition(owner)
        owner._condition = gate
        first = LogPanelPreference(10, "first", "stdout")
        shutdown_started = threading.Event()
        shutdown_done = threading.Event()

        def shutdown_owner():
            shutdown_started.set()
            owner.shutdown()
            shutdown_done.set()

        shutdown_thread = threading.Thread(target=shutdown_owner, daemon=True)
        try:
            self.assertTrue(owner.save("first", first))
            self.assertTrue(wait_until(lambda: backend.initial.get("first") == first))
            worker = owner._thread

            gate.arm()
            owner.request_shutdown()
            self.assertTrue(gate.boundary_reached.wait(timeout=1))

            shutdown_thread.start()
            self.assertTrue(shutdown_started.wait(timeout=1))
            self.assertTrue(gate.external_checked_owner.wait(timeout=1))
            self.assertFalse(shutdown_done.is_set())
            self.assertTrue(worker.is_alive())

            gate.release_exit.set()
            shutdown_thread.join(timeout=1)
            self.assertTrue(shutdown_done.is_set())
            self.assertFalse(worker.is_alive())
            self.assertFalse(owner.is_alive())
        finally:
            gate.release_exit.set()
            shutdown_thread.join(timeout=1)
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

    def test_failed_final_write_exits_once_and_retries_after_reopen(self):
        backend = RecordingBackend()
        backend.release_load.clear()
        backend.force_write_failure = True
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        expected = LogPanelPreference(5000, "pending", "stderr")
        try:
            self.assertTrue(owner.save("demo", expected))
            worker = owner._thread
            self.assertTrue(backend.load_started.wait(timeout=1))

            owner.request_shutdown()
            backend.release_load.set()
            self.assertTrue(backend.write_started.wait(timeout=1))
            self.assertTrue(wait_until(lambda: not owner.is_alive()))
            self.assertEqual(len(backend.write_calls), 1)
            self.assertFalse(worker.is_alive())
            with owner._condition:
                self.assertEqual(owner._cache["demo"], expected)
                self.assertGreater(owner._generation, owner._persisted_generation)

            backend.force_write_failure = False
            backend.write_started.clear()
            backend.release_write.clear()
            self.assertEqual(owner.load("demo"), expected)
            replacement = owner._thread
            self.assertIsNot(replacement, worker)
            self.assertTrue(backend.write_started.wait(timeout=1))
            self.assertFalse(worker.is_alive())
            self.assertEqual(
                sum(
                    thread.is_alive() and thread.name == "log-preference-writer"
                    for thread in threading.enumerate()
                ),
                1,
            )

            backend.release_write.set()
            self.assertTrue(
                wait_until(lambda: backend.initial.get("demo") == expected)
            )
            self.assertEqual(len(backend.write_calls), 2)
            self.assertEqual(backend.max_concurrent_calls, 1)
            owner.request_shutdown()
            self.assertTrue(wait_until(lambda: not owner.is_alive()))
        finally:
            backend.force_write_failure = False
            backend.release_load.set()
            backend.release_write.set()
            owner.shutdown()

    def test_final_shutdown_retries_pending_failed_idle_drain_and_joins(self):
        backend = RecordingBackend()
        backend.release_load.clear()
        backend.fail_writes = 1
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        expected = LogPanelPreference(5000, "pending", "stderr")
        shutdown_done = threading.Event()
        shutdown_thread = threading.Thread(
            target=lambda: (owner.shutdown(timeout=1), shutdown_done.set()),
            daemon=True,
        )
        try:
            self.assertTrue(owner.save("demo", expected))
            first_worker = owner._thread
            self.assertTrue(backend.load_started.wait(timeout=1))

            owner.request_shutdown()
            backend.release_load.set()
            self.assertTrue(backend.write_started.wait(timeout=1))
            self.assertTrue(wait_until(lambda: not owner.is_alive()))
            self.assertEqual(len(backend.write_calls), 1)
            self.assertFalse(first_worker.is_alive())
            with owner._condition:
                self.assertGreater(owner._generation, owner._persisted_generation)

            backend.write_started.clear()
            backend.release_write.clear()
            shutdown_thread.start()
            self.assertTrue(backend.write_started.wait(timeout=1))
            replacement = owner._thread
            self.assertIsNot(replacement, first_worker)
            self.assertTrue(replacement.is_alive())
            self.assertFalse(shutdown_done.is_set())

            backend.release_write.set()
            shutdown_thread.join(timeout=1)
            self.assertTrue(shutdown_done.is_set())
            self.assertEqual(len(backend.write_calls), 2)
            self.assertEqual(backend.initial["demo"], expected)
            self.assertEqual(backend.max_concurrent_calls, 1)
            self.assertFalse(owner.is_alive())
        finally:
            backend.release_load.set()
            backend.release_write.set()
            shutdown_thread.join(timeout=1)
            owner.shutdown()

    def test_final_shutdown_permanent_failure_attempts_once_and_exits(self):
        backend = RecordingBackend()
        backend.release_load.clear()
        backend.force_write_failure = True
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        expected = LogPanelPreference(5000, "pending", "stderr")
        original_request_shutdown = owner.request_shutdown
        try:
            self.assertTrue(owner.save("demo", expected))
            self.assertTrue(backend.load_started.wait(timeout=1))

            owner.request_shutdown()
            backend.release_load.set()
            self.assertTrue(backend.write_started.wait(timeout=1))
            self.assertTrue(wait_until(lambda: not owner.is_alive()))
            self.assertEqual(len(backend.write_calls), 1)

            backend.write_started.clear()

            def request_after_replacement_write_starts():
                self.assertTrue(backend.write_started.wait(timeout=1))
                original_request_shutdown()

            owner.request_shutdown = request_after_replacement_write_starts
            owner.shutdown(timeout=1)
            self.assertEqual(len(backend.write_calls), 2)
            self.assertEqual(backend.max_concurrent_calls, 1)
            self.assertFalse(owner.is_alive())
            with owner._condition:
                self.assertEqual(owner._cache["demo"], expected)
                self.assertGreater(owner._generation, owner._persisted_generation)
        finally:
            owner.request_shutdown = original_request_shutdown
            backend.force_write_failure = False
            backend.release_load.set()
            backend.release_write.set()
            owner.shutdown()

    def test_final_shutdown_overlapping_failed_idle_drain_retries_and_joins(self):
        backend = RecordingBackend()
        backend.release_load.clear()
        backend.release_write.clear()
        backend.fail_writes = 1
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        expected = LogPanelPreference(5000, "overlap", "stderr")
        shutdown_done = threading.Event()
        shutdown_thread = threading.Thread(
            target=lambda: (owner.shutdown(timeout=1), shutdown_done.set()),
            daemon=True,
        )
        try:
            self.assertTrue(owner.save("demo", expected))
            first_worker = owner._thread
            self.assertTrue(backend.load_started.wait(timeout=1))

            owner.request_shutdown()
            backend.release_load.set()
            self.assertTrue(backend.write_started.wait(timeout=1))
            shutdown_thread.start()
            self.assertFalse(shutdown_done.wait(timeout=0.05))
            self.assertTrue(first_worker.is_alive())

            backend.release_write.set()
            shutdown_thread.join(timeout=1)
            self.assertTrue(shutdown_done.is_set())
            self.assertEqual(len(backend.write_calls), 2)
            self.assertEqual(backend.initial["demo"], expected)
            self.assertEqual(backend.max_concurrent_calls, 1)
            self.assertFalse(first_worker.is_alive())
            self.assertFalse(owner.is_alive())
        finally:
            backend.release_load.set()
            backend.release_write.set()
            shutdown_thread.join(timeout=1)
            owner.shutdown()

    def test_final_shutdown_overlap_permanent_failure_has_one_retry(self):
        backend = RecordingBackend()
        backend.release_load.clear()
        backend.release_write.clear()
        backend.force_write_failure = True
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        expected = LogPanelPreference(5000, "overlap", "stderr")
        shutdown_done = threading.Event()
        shutdown_thread = threading.Thread(
            target=lambda: (owner.shutdown(timeout=1), shutdown_done.set()),
            daemon=True,
        )
        try:
            self.assertTrue(owner.save("demo", expected))
            first_worker = owner._thread
            self.assertTrue(backend.load_started.wait(timeout=1))

            owner.request_shutdown()
            backend.release_load.set()
            self.assertTrue(backend.write_started.wait(timeout=1))
            shutdown_thread.start()
            self.assertFalse(shutdown_done.wait(timeout=0.05))

            backend.release_write.set()
            shutdown_thread.join(timeout=1)
            self.assertTrue(shutdown_done.is_set())
            self.assertEqual(len(backend.write_calls), 2)
            self.assertEqual(backend.max_concurrent_calls, 1)
            self.assertFalse(first_worker.is_alive())
            self.assertFalse(owner.is_alive())
            with owner._condition:
                self.assertEqual(owner._cache["demo"], expected)
                self.assertGreater(owner._generation, owner._persisted_generation)
        finally:
            backend.force_write_failure = False
            backend.release_load.set()
            backend.release_write.set()
            shutdown_thread.join(timeout=1)
            owner.shutdown()

    def test_final_shutdown_overlapping_normal_write_recovers_on_same_worker(self):
        backend = RecordingBackend()
        backend.release_write.clear()
        backend.fail_writes = 1
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        expected = LogPanelPreference(5000, "normal-overlap", "stderr")
        shutdown_done = threading.Event()
        shutdown_thread = threading.Thread(
            target=lambda: (owner.shutdown(timeout=1), shutdown_done.set()),
            daemon=True,
        )
        try:
            self.assertTrue(owner.save("demo", expected))
            worker = owner._thread
            self.assertTrue(backend.write_started.wait(timeout=1))
            with owner._condition:
                self.assertFalse(owner._closing)

            shutdown_thread.start()
            self.assertFalse(shutdown_done.wait(timeout=0.05))
            backend.release_write.set()
            shutdown_thread.join(timeout=1)

            self.assertTrue(shutdown_done.is_set())
            self.assertEqual(len(backend.write_calls), 2)
            self.assertEqual(set(backend.write_threads), {worker.ident})
            self.assertEqual(backend.initial["demo"], expected)
            self.assertEqual(backend.max_concurrent_calls, 1)
            self.assertFalse(owner.is_alive())
        finally:
            backend.release_write.set()
            shutdown_thread.join(timeout=1)
            owner.shutdown()

    def test_final_shutdown_overlapping_normal_write_permanent_failure_stops_at_two(self):
        backend = RecordingBackend()
        backend.release_write.clear()
        backend.force_write_failure = True
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        expected = LogPanelPreference(5000, "normal-overlap", "stderr")
        shutdown_done = threading.Event()
        shutdown_thread = threading.Thread(
            target=lambda: (owner.shutdown(timeout=1), shutdown_done.set()),
            daemon=True,
        )
        try:
            self.assertTrue(owner.save("demo", expected))
            worker = owner._thread
            self.assertTrue(backend.write_started.wait(timeout=1))
            with owner._condition:
                self.assertFalse(owner._closing)

            shutdown_thread.start()
            self.assertFalse(shutdown_done.wait(timeout=0.05))
            backend.release_write.set()
            shutdown_thread.join(timeout=1)

            self.assertTrue(shutdown_done.is_set())
            self.assertEqual(len(backend.write_calls), 2)
            self.assertEqual(set(backend.write_threads), {worker.ident})
            self.assertEqual(backend.max_concurrent_calls, 1)
            self.assertFalse(owner.is_alive())
            with owner._condition:
                self.assertEqual(owner._cache["demo"], expected)
                self.assertGreater(owner._generation, owner._persisted_generation)
        finally:
            backend.force_write_failure = False
            backend.release_write.set()
            shutdown_thread.join(timeout=1)
            owner.shutdown()

    def test_concurrent_final_shutdown_callers_share_one_replacement(self):
        backend = BlockingSecondFailureBackend()
        backend.release_load.clear()
        owner = LogPreferenceOwner(backend, debounce_seconds=0)
        expected = LogPanelPreference(5000, "shared-final", "stderr")
        first_done = threading.Event()
        second_done = threading.Event()
        first_shutdown = threading.Thread(
            target=lambda: (owner.shutdown(timeout=1), first_done.set()),
            daemon=True,
        )
        second_shutdown = threading.Thread(
            target=lambda: (owner.shutdown(timeout=1), second_done.set()),
            daemon=True,
        )
        original_request_shutdown = owner.request_shutdown
        request_lock = threading.Lock()
        request_count = 0
        second_request_seen = threading.Event()

        def tracked_request_shutdown():
            nonlocal request_count
            with request_lock:
                request_count += 1
                if request_count == 2:
                    second_request_seen.set()
            original_request_shutdown()

        try:
            self.assertTrue(owner.save("demo", expected))
            self.assertTrue(backend.load_started.wait(timeout=1))
            owner.request_shutdown()
            backend.release_load.set()
            self.assertTrue(wait_until(lambda: not owner.is_alive()))
            self.assertEqual(len(backend.write_calls), 1)

            owner.request_shutdown = tracked_request_shutdown
            first_shutdown.start()
            self.assertTrue(backend.second_write_started.wait(timeout=1))
            second_shutdown.start()
            self.assertTrue(second_request_seen.wait(timeout=1))
            self.assertFalse(first_done.is_set())
            self.assertFalse(second_done.is_set())

            backend.release_second_write.set()
            first_shutdown.join(timeout=1)
            second_shutdown.join(timeout=1)

            self.assertTrue(first_done.is_set())
            self.assertTrue(second_done.is_set())
            self.assertEqual(len(backend.write_calls), 2)
            self.assertEqual(backend.max_concurrent_calls, 1)
            self.assertFalse(owner.is_alive())
            with owner._condition:
                self.assertEqual(owner._cache["demo"], expected)
                self.assertGreater(owner._generation, owner._persisted_generation)
        finally:
            owner.request_shutdown = original_request_shutdown
            backend.release_load.set()
            backend.release_second_write.set()
            first_shutdown.join(timeout=1)
            second_shutdown.join(timeout=1)
            backend.force_write_failure = False
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
