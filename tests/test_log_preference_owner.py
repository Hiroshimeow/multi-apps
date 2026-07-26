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

    def load_all(self):
        self.load_calls += 1
        self.load_threads.append(threading.get_ident())
        self.load_started.set()
        self.release_load.wait(timeout=2)
        return dict(self.initial)

    def replace_all(self, apps):
        self.write_threads.append(threading.get_ident())
        self.write_calls.append(dict(apps))
        self.write_started.set()
        self.release_write.wait(timeout=2)
        if self.fail_writes:
            self.fail_writes -= 1
            return False
        self.initial = dict(apps)
        return True


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
    def test_open_target_does_not_wait_for_load_and_applies_preference_after_release(self):
        expected = LogPanelPreference(5000, "loaded", "stdout")
        backend = RecordingBackend({"demo": expected})
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
        controller.viewer_closed.connect(owner.shutdown)
        main_thread = threading.get_ident()
        heartbeat = []
        safety_release = threading.Timer(1.0, backend.release_load.set)
        try:
            with TemporaryDirectory() as temp_dir:
                target = LogTarget("demo", FakeManager(Path(temp_dir) / "demo.log"))
                safety_release.start()
                controller.open_target(target, "stderr")

                self.assertTrue(backend.load_started.wait(timeout=1))
                self.assertFalse(
                    backend.release_load.is_set(),
                    "open_target waited for preference filesystem load",
                )
                self.assertEqual(popup.panel.line_count.value(), LogPanelPreference().line_count)
                self.assertEqual(popup.panel.filter_edit.text(), "")
                self.assertEqual(popup.panel.stream_label.text(), "stderr")

                QTimer.singleShot(0, lambda: heartbeat.append(threading.get_ident()))
                self.assertTrue(wait_until(lambda: bool(heartbeat)))
                self.assertEqual(heartbeat, [main_thread])
                self.assertNotEqual(backend.load_threads, [main_thread])

                backend.release_load.set()
                self.assertTrue(
                    wait_until(
                        lambda: popup.panel.line_count.value() == expected.line_count
                        and popup.panel.filter_edit.text() == expected.filter_expression
                    )
                )
                self.assertIs(controller.current_target, target)
                self.assertEqual(controller.current_stream, "stderr")

                controller.hide_popup()
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

    def test_shutdown_flushes_latest_value_and_stops_worker(self):
        backend = RecordingBackend()
        owner = LogPreferenceOwner(backend, debounce_seconds=60)
        owner.save("demo", LogPanelPreference(5000, "latest", "stderr"))
        owner.shutdown()
        owner.shutdown()

        self.assertEqual(len(backend.write_calls), 1)
        self.assertEqual(
            backend.write_calls[0]["demo"],
            LogPanelPreference(5000, "latest", "stderr"),
        )
        self.assertFalse(owner.is_alive())

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
