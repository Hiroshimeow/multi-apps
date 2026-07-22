import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path

import yaml
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStyle

from lib.ui.log_hover import LogHoverController
from lib.ui.log_popup import LogPopupWindow
from lib.ui.tray_panel import TrayPanelWindow
from multi import AppControlWidget, SystemTrayApp


_QT_APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout_ms=1500):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())


class TrayUiArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        logs = root / "logs"
        logs.mkdir()
        self.config_path = root / "setting.yaml"
        self.config_path.write_text(
            yaml.safe_dump(
                {
                    "global": {"log_dir": str(logs)},
                    "apps": [
                        {
                            "id": "demo",
                            "name": "Demo",
                            "path": str(root),
                            "command": "python -c pass",
                            "enabled": True,
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (logs / "Demo.out.log").write_text("demo-output\n", encoding="utf-8")
        (logs / "Demo.err.log").write_text("demo-error\n", encoding="utf-8")
        self.tray = SystemTrayApp(
            _QT_APP.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
            config_path=str(self.config_path),
            launcher_argv=("multi.py", "--config", str(self.config_path)),
        )
        self.tray.auto_start_timer.stop()

    def tearDown(self):
        tray = self.tray
        tray.log_controller.shutdown()
        for row in tuple(tray.tray_panel.findChildren(AppControlWidget)):
            row.shutdown()
        tray.log_popup.hide()
        tray.log_popup.deleteLater()
        tray.tray_panel.hide()
        tray.tray_panel.deleteLater()
        tray.hide()
        tray.deleteLater()
        QApplication.processEvents()
        self.temp_dir.cleanup()

    def test_system_uses_panel_and_one_independent_popup_not_qmenu(self):
        self.assertIsInstance(self.tray.tray_panel, TrayPanelWindow)
        self.assertIsInstance(self.tray.log_popup, LogPopupWindow)
        self.assertIsInstance(self.tray.log_controller, LogHoverController)
        self.assertFalse(hasattr(self.tray, "menu"))

        rows = self.tray.tray_panel.findChildren(AppControlWidget)
        self.assertEqual(len(rows), 1)
        self.assertFalse(hasattr(rows[0], "inline_log_panel"))
        self.assertIs(rows[0].log_controller, self.tray.log_controller)

    def test_first_log_open_does_not_change_tray_panel_geometry(self):
        self.tray.tray_panel.show_at(
            QRect(1880, 1000, 32, 32),
            QRect(0, 0, 1920, 1032),
        )
        QApplication.processEvents()
        row = self.tray.tray_panel.findChild(AppControlWidget)
        before = QRect(self.tray.tray_panel.frameGeometry())

        self.tray.log_controller.open_target(row.log_target, "stdout")
        self.assertTrue(
            wait_until(
                lambda: self.tray.log_popup.panel.log_view.toPlainText()
                == "demo-output"
            )
        )

        self.assertEqual(self.tray.tray_panel.frameGeometry(), before)
        self.assertTrue(self.tray.log_popup.isVisible())

    def test_one_hundred_rebuilds_keep_one_controller_popup_and_live_row_set(self):
        controller = self.tray.log_controller
        popup = self.tray.log_popup
        reader_thread = controller.reader._thread
        previous_rows = tuple(self.tray.row_widgets)

        for _ in range(100):
            self.tray.rebuild_panel()
            QApplication.processEvents()

        self.assertIs(self.tray.log_controller, controller)
        self.assertIs(self.tray.log_popup, popup)
        self.assertIs(controller.reader._thread, reader_thread)
        self.assertTrue(controller.reader.is_alive())
        self.assertEqual(len(self.tray.row_widgets), 1)
        self.assertTrue(all(row.timer.isActive() for row in self.tray.row_widgets))
        self.assertTrue(all(not row.timer.isActive() for row in previous_rows))
        self.assertEqual(
            len(self.tray.log_popup.findChildren(type(self.tray.log_popup.panel))),
            1,
        )

    def test_shutdown_ui_is_idempotent_and_stops_rows_popup_and_worker(self):
        self.tray.tray_panel.show()
        self.tray.log_popup.show()
        rows = tuple(self.tray.row_widgets)

        self.tray.shutdown_ui()
        self.tray.shutdown_ui()
        QApplication.processEvents()

        self.assertFalse(self.tray.tray_panel.isVisible())
        self.assertFalse(self.tray.log_popup.isVisible())
        self.assertFalse(self.tray.log_controller.reader.is_alive())
        self.assertTrue(all(not row.timer.isActive() for row in rows))

    def test_context_activation_toggles_panel_and_internal_click_does_not_hide(self):
        reason = self.tray.ActivationReason.Context
        self.tray.on_tray_activated(reason)
        QApplication.processEvents()
        self.assertTrue(self.tray.tray_panel.isVisible())

        row = self.tray.tray_panel.findChild(AppControlWidget)
        QTest.mouseClick(row.top_row, Qt.MouseButton.RightButton)
        QApplication.processEvents()
        self.assertTrue(self.tray.tray_panel.isVisible())

        self.tray.on_tray_activated(reason)
        QApplication.processEvents()
        self.assertFalse(self.tray.tray_panel.isVisible())


if __name__ == "__main__":
    unittest.main()
