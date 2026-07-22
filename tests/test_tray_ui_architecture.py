import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import threading
import time
import unittest
from pathlib import Path

import yaml
from PyQt6.QtCore import QPoint, QRect, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QStyle

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
        tray.shutdown_ui()
        tray.log_popup.deleteLater()
        tray.app_context_popup.deleteLater()
        tray.tray_panel.deleteLater()
        tray.hide()
        tray.deleteLater()
        QApplication.processEvents()
        self.temp_dir.cleanup()

    def test_system_uses_panel_and_one_independent_popup_not_qmenu(self):
        self.assertIsInstance(self.tray.tray_panel, TrayPanelWindow)
        self.assertIsInstance(self.tray.log_popup, LogPopupWindow)
        self.assertIsInstance(self.tray.log_controller, LogHoverController)
        self.assertTrue(self.tray.lifecycle_controller.is_alive())
        self.assertTrue(self.tray.pinned_logs.reader.is_alive())
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
        self.assertFalse(self.tray.lifecycle_controller.is_alive())
        self.assertFalse(self.tray.pinned_logs.reader.is_alive())
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


    def test_user_adjusted_pin_geometry_survives_tray_reopen_and_refresh(self):
        self.tray.show_panel()
        row = self.tray.row_widgets[0]
        self.tray.log_controller.open_target(row.log_target, "stdout")
        self.assertTrue(wait_until(lambda: self.tray.log_popup.isVisible()))
        QTest.mouseClick(self.tray.log_popup.panel.pin_button, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_until(lambda: self.tray.pinned_logs.count() == 1))
        pinned = self.tray.pinned_logs.windows()[0]
        pinned.setGeometry(QRect(120, 140, 700, 320))
        QApplication.processEvents()
        user_geometry = QRect(pinned.frameGeometry())

        self.tray.tray_panel.hide()
        self.tray.show_panel()
        QApplication.processEvents()
        self.assertEqual(pinned.frameGeometry(), user_geometry)

        self.tray.refresh_all()
        QApplication.processEvents()
        self.assertEqual(pinned.frameGeometry(), user_geometry)
        self.assertTrue(pinned.isVisible())

        log_path = Path(self.tray.row_widgets[0].manager.get_log_path("out"))
        log_path.write_text("demo-output\nafter-refresh\n", encoding="utf-8")
        self.assertTrue(
            wait_until(
                lambda: "after-refresh" in pinned.panel.log_view.toPlainText()
            )
        )

    def test_pin_survives_tray_hide_outside_click_and_unpins_from_own_window(self):
        self.tray.show_panel()
        row = self.tray.row_widgets[0]
        self.tray.log_controller.open_target(row.log_target, "stdout")
        self.assertTrue(wait_until(lambda: self.tray.log_popup.isVisible()))

        QTest.mouseClick(self.tray.log_popup.panel.pin_button, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_until(lambda: self.tray.pinned_logs.count() == 1))
        pinned = self.tray.pinned_logs.windows()[0]
        self.assertTrue(pinned.isVisible())

        self.tray.app_context_popup.show_action(
            QPoint(300, 300),
            text="Open terminal here",
            callback=lambda: None,
            enabled=True,
        )
        outside = QPushButton("outside")
        outside.show()
        QApplication.processEvents()
        try:
            QTest.mouseClick(self.tray.log_popup.panel.filter_edit, Qt.MouseButton.LeftButton)
            QApplication.processEvents()
            self.assertTrue(self.tray.tray_panel.isVisible())

            QTest.mouseClick(outside, Qt.MouseButton.LeftButton)
            QApplication.processEvents()
            self.assertFalse(self.tray.tray_panel.isVisible())
            self.assertFalse(self.tray.log_popup.isVisible())
            self.assertFalse(self.tray.app_context_popup.isVisible())
            self.assertTrue(pinned.isVisible())

            QTest.mouseClick(pinned.panel.pin_button, Qt.MouseButton.LeftButton)
            self.assertTrue(wait_until(lambda: self.tray.pinned_logs.count() == 0))
        finally:
            outside.deleteLater()
            QApplication.processEvents()

    def test_stop_button_is_nonblocking_and_duplicate_requests_are_deduplicated(self):
        row = self.tray.row_widgets[0]
        started = threading.Event()
        release = threading.Event()
        calls = []

        def blocking_stop():
            calls.append(time.monotonic())
            started.set()
            release.wait(timeout=2)
            return True

        row.manager.stop_all = blocking_stop
        row.btn_stop.setEnabled(True)
        gui_callback_ran = []
        try:
            began = time.perf_counter()
            row.on_stop()
            row.on_stop()
            elapsed_ms = (time.perf_counter() - began) * 1000
            self.assertLess(elapsed_ms, 20)
            self.assertTrue(started.wait(timeout=1))

            QTimer.singleShot(10, lambda: gui_callback_ran.append(time.monotonic()))
            self.assertTrue(wait_until(lambda: bool(gui_callback_ran), timeout_ms=300))
            self.assertEqual(len(calls), 1)
            self.assertTrue(self.tray.lifecycle_controller.is_pending(row.manager.app_id))
        finally:
            release.set()
        self.assertTrue(
            wait_until(
                lambda: not self.tray.lifecycle_controller.is_pending(row.manager.app_id)
            )
        )

    def test_terminal_context_action_routes_to_exact_manager(self):
        row = self.tray.row_widgets[0]
        row.manager.terminal_status = lambda: type(
            "Status", (), {"ok": True, "message": "ready"}
        )()
        calls = []
        row.manager.open_terminal = lambda: (
            calls.append(row.manager.get_workdir())
            or type("Result", (), {"ok": True, "message": "opened"})()
        )
        self.tray.show_panel()
        self.tray.show_app_context(row.manager, QPoint(400, 300))
        QApplication.processEvents()
        self.assertTrue(self.tray.app_context_popup.isVisible())
        QTest.mouseClick(
            self.tray.app_context_popup.action_button,
            Qt.MouseButton.LeftButton,
        )
        QApplication.processEvents()
        self.assertEqual(calls, [row.manager.get_workdir()])
        self.assertFalse(self.tray.app_context_popup.isVisible())



if __name__ == "__main__":
    unittest.main()
