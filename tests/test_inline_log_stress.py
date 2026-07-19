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

from lib.ui.inline_log_panel import InlineLogPanelCoordinator
from multi import AppControlWidget, SystemTrayApp


_QT_APP = QApplication.instance() or QApplication([])


def _flush_deferred_deletes():
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    gc.collect()


class InlineLogLifecycleStressTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.log_dir = self.root / "logs"
        self.log_dir.mkdir()
        self.config_path = self.root / "setting.yaml"
        self.preference_path = self.root / "ui-log-preferences.json"
        self.apps = (
            ("stress-a", "Stress App A"),
            ("stress-b", "Stress App B"),
        )
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

    def tearDown(self):
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.auto_start_timer.stop()
            for row in tuple(tray.menu.findChildren(AppControlWidget)):
                row.shutdown()
            coordinator = getattr(tray, "log_coordinator", None)
            if coordinator is not None:
                coordinator.shutdown()
            tray.menu.clear()
            tray.menu.close()
            tray.menu.deleteLater()
            tray.hide()
            tray.deleteLater()
        _flush_deferred_deletes()
        self.temp_dir.cleanup()

    def _row(self, app_id):
        return next(
            row
            for row in self.tray.menu.findChildren(AppControlWidget)
            if row.manager.app_id == app_id
        )

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
                            handle.write(f"line-{index:04d} {marker} Unicode-日本語\n")
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
            except BaseException as exc:  # surfaced in the test thread
                writer_errors.append(exc)

        writer = threading.Thread(target=write_logs, daemon=True)
        writer.start()
        self.assertTrue(writer_started.wait(timeout=2.0))

        for cycle in range(30):
            self.tray.refresh_menu()
            row = self._row("stress-a" if cycle % 2 == 0 else "stress-b")
            stream = "stdout" if cycle % 3 else "stderr"
            self.tray.log_coordinator.request_open(row, stream)
            row.inline_log_panel.line_count.setValue(5000 if cycle % 2 else 100)
            row.inline_log_panel.filter_edit.setText(
                "[keep,!drop]" if cycle % 4 else ""
            )
            self.tray.log_coordinator.refresh_current()
            QApplication.processEvents()
            _flush_deferred_deletes()

            rows = self.tray.menu.findChildren(AppControlWidget)
            coordinators = self.tray.menu.findChildren(InlineLogPanelCoordinator)
            timers = self.tray.menu.findChildren(QTimer)
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(coordinators), 1)
            self.assertEqual(len(timers), 4)
            self.assertTrue(all(item.timer.isActive() for item in rows))
            self.assertTrue(self.tray.log_coordinator.refresh_timer.isActive())

        writer.join(timeout=5.0)
        self.assertFalse(writer.is_alive())
        self.assertEqual(writer_errors, [])

        self.tray.refresh_menu()
        row = self._row("stress-a")
        row.inline_log_panel.line_count.setValue(5000)
        row.inline_log_panel.filter_edit.setText("")
        self.tray.log_coordinator.request_open(row, "stdout")
        self.tray.log_coordinator.refresh_current()
        QApplication.processEvents()

        displayed = row.inline_log_panel.log_view.toPlainText()
        self.assertIn("final-marker keep", displayed)
        self.assertLessEqual(len(displayed.splitlines()), 5000)
        self.assertNotIn("<html", displayed.lower())

        coordinator = self.tray.log_coordinator
        rows = tuple(self.tray.menu.findChildren(AppControlWidget))
        for item in rows:
            item.shutdown()
        coordinator.shutdown()
        QApplication.processEvents()

        self.assertFalse(coordinator.refresh_timer.isActive())
        self.assertFalse(coordinator.hide_timer.isActive())
        self.assertIsNone(coordinator.current_row)
        self.assertTrue(all(not item.timer.isActive() for item in rows))


if __name__ == "__main__":
    unittest.main()
