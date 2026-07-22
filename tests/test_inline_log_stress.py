import gc
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yaml
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
from PyQt6.QtWidgets import QApplication, QStyle

from lib.ui.inline_log_panel import InlineLogPanel
from multi import AppControlWidget, SystemTrayApp


_QT_APP = QApplication.instance() or QApplication([])


def _flush_deferred_deletes():
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    gc.collect()


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class IndependentLogLifecycleStressTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.log_dir = self.root / "logs"
        self.log_dir.mkdir()
        self.config_path = self.root / "setting.yaml"
        self.preference_path = self.root / "ui-log-preferences.json"
        self.apps = (("stress-a", "Stress App A"), ("stress-b", "Stress App B"))
        self.config_path.write_text(
            yaml.safe_dump(
                {
                    "global": {
                        "log_dir": str(self.log_dir),
                        "session": "subprocess",
                        "multi_run": False,
                    },
                    "apps": [
                        {
                            "id": app_id,
                            "name": name,
                            "path": str(self.root),
                            "command": f'{sys.executable} -c "pass"',
                            "args": [],
                            "enabled": True,
                            "auto_start": False,
                            "multi_run": False,
                            "args_edit": False,
                            "close_timeout": 5.0,
                            "os": "win11",
                        }
                        for app_id, name in self.apps
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        for _app_id, name in self.apps:
            for suffix in ("out", "err"):
                (self.log_dir / f"{name}.{suffix}.log").write_text(
                    "initial keep\n", encoding="utf-8"
                )
        self.tray = SystemTrayApp(
            _QT_APP.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
            config_path=str(self.config_path),
            launcher_argv=(sys.executable, "--config", str(self.config_path)),
            log_preferences_path=self.preference_path,
        )
        self.tray.auto_start_timer.stop()
        self.tray.log_controller.refresh_timer.setInterval(10000)

    def tearDown(self):
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.auto_start_timer.stop()
            tray.log_controller.shutdown()
            for row in tuple(tray.row_widgets):
                row.shutdown()
            tray.log_popup.hide()
            tray.log_popup.deleteLater()
            tray.tray_panel.hide()
            tray.tray_panel.deleteLater()
            tray.hide()
            tray.deleteLater()
        _flush_deferred_deletes()
        self.temp_dir.cleanup()

    def _row(self, app_id):
        return next(row for row in self.tray.row_widgets if row.manager.app_id == app_id)

    def test_fast_writer_survives_rebuild_switch_filter_and_shutdown(self):
        writer_errors = []
        writer_started = threading.Event()

        def write_logs():
            try:
                paths = [
                    self.log_dir / f"{name}.{suffix}.log"
                    for _app_id, name in self.apps
                    for suffix in ("out", "err")
                ]
                handles = [path.open("a", encoding="utf-8") for path in paths]
                try:
                    writer_started.set()
                    for index in range(2400):
                        marker = "drop" if index % 7 == 0 else "keep"
                        for handle in handles:
                            handle.write(
                                f"line-{index:04d} {marker} Unicode-日本語\n"
                            )
                        if index % 20 == 0:
                            for handle in handles:
                                handle.flush()
                            time.sleep(0.001)
                    for handle in handles:
                        handle.write("final-marker keep\n")
                        handle.flush()
                finally:
                    for handle in handles:
                        handle.close()
            except BaseException as exc:
                writer_errors.append(exc)

        writer = threading.Thread(target=write_logs, daemon=True)
        writer.start()
        self.assertTrue(writer_started.wait(timeout=2.0))

        controller = self.tray.log_controller
        panel = self.tray.log_popup.panel
        for cycle in range(30):
            self.tray.rebuild_panel()
            row = self._row("stress-a" if cycle % 2 == 0 else "stress-b")
            stream = "stdout" if cycle % 3 else "stderr"
            panel.line_count.setValue(5000 if cycle % 2 else 100)
            panel.filter_edit.setText("[keep,!drop]" if cycle % 4 else "")
            controller.open_target(row.log_target, stream)
            QApplication.processEvents()
            _flush_deferred_deletes()

            self.assertEqual(len(self.tray.row_widgets), 2)
            self.assertEqual(
                len(self.tray.log_popup.findChildren(InlineLogPanel)), 1
            )
            self.assertEqual(len(controller.findChildren(QTimer)), 3)
            self.assertTrue(all(item.timer.isActive() for item in self.tray.row_widgets))
            self.assertTrue(controller.reader.is_alive())

        writer.join(timeout=5.0)
        self.assertFalse(writer.is_alive())
        self.assertEqual(writer_errors, [])

        self.tray.rebuild_panel()
        row = self._row("stress-a")
        panel.line_count.setValue(5000)
        panel.filter_edit.setText("")
        controller.open_target(row.log_target, "stdout")
        self.assertTrue(
            _wait_until(
                lambda: "final-marker keep" in panel.log_view.toPlainText()
            )
        )

        displayed = panel.log_view.toPlainText()
        self.assertLessEqual(len(displayed.splitlines()), 5000)
        self.assertNotIn("<html", displayed.lower())

        rows = tuple(self.tray.row_widgets)
        for item in rows:
            item.shutdown()
        controller.shutdown()
        QApplication.processEvents()

        self.assertFalse(controller.refresh_timer.isActive())
        self.assertFalse(controller.hide_timer.isActive())
        self.assertFalse(controller.open_timer.isActive())
        self.assertIsNone(controller.current_target)
        self.assertFalse(controller.reader.is_alive())
        self.assertTrue(all(not item.timer.isActive() for item in rows))


if __name__ == "__main__":
    unittest.main()
