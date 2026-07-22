import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import threading
import time
import unittest
from pathlib import Path

from PyQt6.QtCore import QObject, QRect, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from lib.ui.log_hover import LatestLogReader, LogHoverController, LogTarget
from lib.ui.log_popup import LogPopupWindow
from lib.ui.log_preferences import LogPanelPreferenceStore
from lib.ui.log_reader import LogSnapshot, LogSnapshotState


_QT_APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout_ms=1500):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    QApplication.processEvents()
    return bool(predicate())


class BlockingReader:
    def __init__(self):
        self.calls = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def read(self, path, *, max_lines, max_bytes):
        self.calls.append((str(path), max_lines, max_bytes))
        if len(self.calls) == 1:
            self.first_started.set()
            self.release_first.wait(timeout=2)
        return LogSnapshot(
            path=str(path),
            state=LogSnapshotState.READY,
            lines=(Path(path).name,),
            size_bytes=1,
            file_identity=(1, len(self.calls)),
        )


class LatestLogReaderTests(unittest.TestCase):
    def test_running_read_is_nonblocking_and_pending_requests_collapse_to_latest(self):
        backend = BlockingReader()
        reader = LatestLogReader(backend)
        results = []
        reader.snapshot_ready.connect(lambda request_id, snapshot: results.append((request_id, snapshot)))
        try:
            first_id = reader.request("first.log", max_lines=100, max_bytes=1024)
            self.assertTrue(backend.first_started.wait(timeout=1))

            started = time.perf_counter()
            reader.request("second.log", max_lines=100, max_bytes=1024)
            latest_id = reader.request("third.log", max_lines=5000, max_bytes=2048)
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertLess(elapsed_ms, 20)

            backend.release_first.set()
            self.assertTrue(wait_until(lambda: len(results) == 1))

            self.assertEqual(first_id + 2, latest_id)
            self.assertEqual(
                [Path(call[0]).name for call in backend.calls],
                ["first.log", "third.log"],
            )
            self.assertEqual(results[0][0], latest_id)
            self.assertEqual(results[0][1].lines, ("third.log",))
        finally:
            reader.shutdown()
        self.assertFalse(reader.is_alive())


    def test_path_provider_runs_on_worker_and_failure_has_no_stale_path(self):
        backend = BlockingReader()
        backend.release_first.set()
        reader = LatestLogReader(backend)
        results = []
        provider_threads = []
        reader.snapshot_ready.connect(
            lambda request_id, snapshot: results.append((request_id, snapshot))
        )
        main_thread = threading.get_ident()
        try:
            reader.request(
                lambda: (
                    provider_threads.append(threading.get_ident())
                    or "worker-path.log"
                ),
                max_lines=100,
                max_bytes=1024,
            )
            self.assertTrue(wait_until(lambda: len(results) == 1))
            self.assertNotEqual(provider_threads, [main_thread])
            self.assertEqual(results[0][1].path, "worker-path.log")

            results.clear()

            def broken_provider():
                provider_threads.append(threading.get_ident())
                raise PermissionError("registry locked")

            reader.request(
                broken_provider,
                max_lines=100,
                max_bytes=1024,
            )
            self.assertTrue(wait_until(lambda: len(results) == 1))
            snapshot = results[0][1]
            self.assertEqual(snapshot.state, LogSnapshotState.UNREADABLE)
            self.assertEqual(snapshot.path, "")
            self.assertIn("PermissionError", snapshot.error)
        finally:
            reader.shutdown()

class ManualReader(QObject):
    snapshot_ready = pyqtSignal(int, object)

    def __init__(self):
        super().__init__()
        self.requests = []
        self._next_id = 0
        self._alive = True

    def request(self, source, *, max_lines, max_bytes):
        self._next_id += 1
        self.requests.append((self._next_id, source, max_lines, max_bytes))
        return self._next_id

    def shutdown(self):
        self._alive = False

    def is_alive(self):
        return self._alive


class FakeManager:
    def __init__(self, app_id, paths):
        self.app_id = app_id
        self.paths = paths

    def get_log_path(self, stream):
        return str(self.paths[stream])


class LogHoverControllerTests(unittest.TestCase):
    def test_target_switch_keeps_current_text_until_new_snapshot_arrives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_path = root / "first.log"
            second_path = root / "second.log"
            first_path.write_text("old-content\n", encoding="utf-8")
            second_path.write_text("new-content\n", encoding="utf-8")
            reader = ManualReader()
            popup = LogPopupWindow()
            controller = LogHoverController(
                popup,
                LogPanelPreferenceStore(root / "preferences.json"),
                placement_provider=lambda: (
                    QRect(600, 400, 620, 300),
                    QRect(0, 0, 1400, 900),
                ),
                reader=reader,
                refresh_interval_ms=10000,
            )
            first = LogTarget(
                "first",
                FakeManager("first", {"out": first_path, "err": first_path}),
            )
            second = LogTarget(
                "second",
                FakeManager("second", {"out": second_path, "err": second_path}),
            )
            try:
                popup.panel.update_snapshot(
                    LogSnapshot(
                        path=str(first_path),
                        state=LogSnapshotState.READY,
                        lines=("old-content",),
                        size_bytes=12,
                        file_identity=(1, 1),
                    )
                )
                controller.open_target(first, "stdout")
                first_request = controller._latest_read_id
                reader.snapshot_ready.emit(
                    first_request,
                    LogSnapshot(
                        path=str(first_path),
                        state=LogSnapshotState.READY,
                        lines=("old-content",),
                        size_bytes=12,
                        file_identity=(1, 1),
                    ),
                )
                QApplication.processEvents()

                controller.open_target(second, "stderr")
                second_request = controller._latest_read_id

                self.assertEqual(popup.panel.log_view.toPlainText(), "old-content")
                self.assertEqual(popup.panel.state_label.text(), "Loading?")
                self.assertEqual(popup.panel.stream_label.text(), "stderr")

                reader.snapshot_ready.emit(
                    second_request,
                    LogSnapshot(
                        path=str(second_path),
                        state=LogSnapshotState.READY,
                        lines=("new-content",),
                        size_bytes=12,
                        file_identity=(2, 1),
                    ),
                )
                QApplication.processEvents()
                self.assertEqual(popup.panel.log_view.toPlainText(), "new-content")
                self.assertEqual(popup.panel.state_label.text(), "Live")
            finally:
                controller.shutdown()
                popup.deleteLater()
                QApplication.processEvents()

    def test_late_leave_from_previous_button_does_not_cancel_new_hover_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_path = root / "first.log"
            second_path = root / "second.log"
            first_path.write_text("first\n", encoding="utf-8")
            second_path.write_text("second\n", encoding="utf-8")
            first = LogTarget(
                "first",
                FakeManager("first", {"out": first_path, "err": first_path}),
            )
            second = LogTarget(
                "second",
                FakeManager("second", {"out": second_path, "err": second_path}),
            )
            popup = LogPopupWindow()
            controller = LogHoverController(
                popup,
                LogPanelPreferenceStore(root / "preferences.json"),
                placement_provider=lambda: (
                    QRect(600, 400, 620, 300),
                    QRect(0, 0, 1400, 900),
                ),
                open_delay_ms=20,
                hide_delay_ms=30,
                refresh_interval_ms=10000,
            )
            try:
                controller.open_target(first, "stdout")
                self.assertTrue(wait_until(lambda: controller.current_target is first))

                controller.hover_enter(second, "stderr")
                controller.hover_leave(first, "stdout")

                self.assertTrue(
                    wait_until(
                        lambda: controller.current_target is second
                        and popup.panel.log_view.toPlainText() == "second"
                    )
                )
                self.assertEqual(controller.current_stream, "stderr")
            finally:
                controller.shutdown()
                popup.deleteLater()
                QApplication.processEvents()

    def test_rapid_hover_opens_only_latest_target_in_one_popup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_out = root / "first.out.log"
            second_err = root / "second.err.log"
            first_out.write_text("first-output\n", encoding="utf-8")
            second_err.write_text("second-error\n", encoding="utf-8")
            first = LogTarget(
                "first",
                FakeManager("first", {"out": first_out, "err": first_out}),
            )
            second = LogTarget(
                "second",
                FakeManager("second", {"out": second_err, "err": second_err}),
            )
            popup = LogPopupWindow()
            controller = LogHoverController(
                popup,
                LogPanelPreferenceStore(root / "preferences.json"),
                placement_provider=lambda: (
                    QRect(600, 400, 620, 300),
                    QRect(0, 0, 1400, 900),
                ),
                open_delay_ms=20,
                hide_delay_ms=20,
                refresh_interval_ms=10000,
            )
            try:
                controller.hover_enter(first, "stdout")
                controller.hover_leave(first, "stdout")
                controller.hover_enter(second, "stderr")

                self.assertTrue(
                    wait_until(
                        lambda: popup.panel.log_view.toPlainText() == "second-error"
                    )
                )
                self.assertIs(controller.current_target, second)
                self.assertEqual(controller.current_stream, "stderr")
                self.assertTrue(popup.isVisible())
                self.assertEqual(popup.panel.stream_label.text(), "stderr")

                controller.hover_leave(second, "stderr")
                self.assertTrue(wait_until(lambda: not popup.isVisible()))
            finally:
                controller.shutdown()
                popup.deleteLater()
                QApplication.processEvents()
            self.assertFalse(controller.reader.is_alive())


if __name__ == "__main__":
    unittest.main()
