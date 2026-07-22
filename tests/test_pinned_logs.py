import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import threading
import time
import unittest
from pathlib import Path

from PyQt6.QtCore import QRect, QSize
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from lib.ui.log_hover import LogTarget
from lib.ui.log_preferences import LogPanelPreferenceStore
from lib.ui.pinned_logs import MultiLogReader, PinnedLogManager, compute_pinned_log_rects


_QT_APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout_ms=2500):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())


class FakeManager:
    def __init__(self, app_id, name, out_path, err_path=None):
        self.app_id = app_id
        self.name = name
        self.paths = {"out": out_path, "err": err_path or out_path}

    def get_log_path(self, stream):
        return str(self.paths[stream])


class MultiLogReaderTests(unittest.TestCase):
    def test_each_pinned_key_owns_an_independent_reader_cache(self):
        backends = []
        results = []

        class RecordingReader:
            def __init__(self):
                self.paths = []
                backends.append(self)

            def read(self, path, *, max_lines, max_bytes):
                self.paths.append(str(path))
                return type(
                    "Snapshot",
                    (), {"path": str(path), "lines": (str(path),)},
                )()

        reader = MultiLogReader(reader_factory=RecordingReader)
        reader.snapshot_ready.connect(
            lambda key, request_id, snapshot: results.append(
                (tuple(key), request_id, snapshot.path)
            )
        )
        try:
            requests = (
                (("first", "stdout"), "first.log"),
                (("second", "stderr"), "second.log"),
                (("first", "stdout"), "first.log"),
            )
            for key, path in requests:
                expected = len(results) + 1
                reader.request(key, path, max_lines=100, max_bytes=1024)
                self.assertTrue(wait_until(lambda: len(results) == expected))

            self.assertEqual(len(backends), 2)
            self.assertEqual(backends[0].paths, ["first.log", "first.log"])
            self.assertEqual(backends[1].paths, ["second.log"])
        finally:
            reader.shutdown()
        self.assertFalse(reader.is_alive())


class PinnedLogGeometryTests(unittest.TestCase):
    def test_eleven_pinned_logs_stay_inside_screen_without_overlap(self):
        available = QRect(0, 0, 1920, 1032)
        tray = QRect(1288, 610, 620, 380)
        rects = compute_pinned_log_rects(
            11,
            QSize(620, 220),
            tray,
            available,
        )
        self.assertEqual(len(rects), 11)
        for index, rect in enumerate(rects):
            self.assertTrue(available.contains(rect), (index, rect))
            self.assertGreaterEqual(rect.height(), 96)
            for other in rects[index + 1 :]:
                self.assertFalse(rect.intersects(other), (rect, other))


class PinnedLogManagerTests(unittest.TestCase):
    def test_multiple_pinned_logs_stack_without_overlap_stay_live_and_unpin_independently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_path = root / "first.log"
            second_path = root / "second.log"
            first_path.write_text("first-1\n", encoding="utf-8")
            second_path.write_text("second-1\n", encoding="utf-8")
            first = LogTarget("first", FakeManager("first", "First", first_path))
            second = LogTarget("second", FakeManager("second", "Second", second_path))
            manager = PinnedLogManager(
                LogPanelPreferenceStore(root / "preferences.json"),
                placement_provider=lambda: (
                    QRect(700, 600, 620, 240),
                    QRect(0, 0, 1600, 1000),
                ),
                refresh_interval_ms=50,
            )
            try:
                first_window = manager.pin(first, "stdout", line_count=100, filter_expression="")
                second_window = manager.pin(second, "stderr", line_count=100, filter_expression="")
                self.assertEqual(manager.count(), 2)
                self.assertTrue(first_window.panel.pin_button.isChecked())
                self.assertTrue(second_window.panel.pin_button.isChecked())
                self.assertFalse(first_window.frameGeometry().intersects(second_window.frameGeometry()))
                self.assertTrue(wait_until(lambda: first_window.panel.log_view.toPlainText() == "first-1"))
                self.assertTrue(wait_until(lambda: second_window.panel.log_view.toPlainText() == "second-1"))

                first_path.write_text("first-1\nfirst-2\n", encoding="utf-8")
                self.assertTrue(wait_until(lambda: "first-2" in first_window.panel.log_view.toPlainText()))

                manager.unpin(("first", "stdout"))
                QApplication.processEvents()
                self.assertEqual(manager.count(), 1)
                self.assertFalse(first_window.isVisible())
                self.assertTrue(second_window.isVisible())
            finally:
                manager.shutdown()
            self.assertFalse(manager.reader.is_alive())


if __name__ == "__main__":
    unittest.main()
