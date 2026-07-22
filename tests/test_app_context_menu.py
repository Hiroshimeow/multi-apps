import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import MagicMock

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from lib.ui.app_tools import AppToolActionResult
from multi import AppControlWidget, AppNameLabel, SystemTrayApp


_QT_APP = QApplication.instance() or QApplication([])


def failure(message="failed"):
    return AppToolActionResult(False, "LAUNCH_FAILED", message, target="C:/work")


class AppNameLabelTests(unittest.TestCase):
    def test_only_left_click_emits_an_action(self):
        label = AppNameLabel("Demo")
        label.resize(160, 30)
        label.show()
        QApplication.processEvents()
        try:
            clicked = QSignalSpy(label.left_clicked)
            QTest.mouseClick(label, Qt.MouseButton.LeftButton)
            QTest.mouseClick(label, Qt.MouseButton.RightButton)
            QTest.mouseClick(label, Qt.MouseButton.MiddleButton)
            self.assertEqual(len(clicked), 1)
            self.assertFalse(hasattr(label, "context_requested"))
        finally:
            label.close()
            label.deleteLater()
            QApplication.processEvents()


class AppRowInteractionTests(unittest.TestCase):
    def make_manager(self):
        manager = MagicMock()
        manager.name = "Demo"
        manager.app_config = {"id": "demo", "multi_run": False}
        manager.app_id = "demo"
        manager.get_status_info.return_value = {"status": "STOPPED", "instances": 0}
        manager.get_status_presentation.return_value = {
            "text": "—",
            "color": "#c62828",
            "tooltip": "Last-used time unavailable",
        }
        manager.get_pid_presentation.return_value = {
            "text": "PID: -",
            "tooltip": "Last run PID unavailable",
        }
        manager.open_workdir.return_value = AppToolActionResult(
            True,
            "OPENED",
            "opened",
            target="C:/work",
        )
        return manager

    def make_widget(self, manager=None, notifier=None, controller=None):
        host = QWidget()
        widget = AppControlWidget(
            manager or self.make_manager(),
            parent=host,
            result_notifier=notifier,
            log_controller=controller,
        )
        widget.show()
        QApplication.processEvents()
        self.addCleanup(self.dispose, widget, host)
        return widget

    @staticmethod
    def dispose(widget, host):
        widget.shutdown()
        widget.close()
        widget.deleteLater()
        host.deleteLater()
        QApplication.processEvents()

    def test_name_left_click_opens_folder_and_right_click_does_nothing(self):
        manager = self.make_manager()
        widget = self.make_widget(manager)
        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.LeftButton)
        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.RightButton)
        manager.open_workdir.assert_called_once_with()
        self.assertIn("Left-click", widget.lbl_name.toolTip())
        self.assertNotIn("Right-click", widget.lbl_name.toolTip())
        self.assertFalse(hasattr(widget, "build_app_context_menu"))
        self.assertFalse(hasattr(widget, "show_app_context_menu"))

    def test_folder_failure_notifies_once_success_does_not_notify(self):
        notifier = MagicMock()
        manager = self.make_manager()
        manager.open_workdir.return_value = failure("folder vanished")
        widget = self.make_widget(manager, notifier)

        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.LeftButton)
        notifier.assert_called_once_with(manager.name, manager.open_workdir.return_value)

        notifier.reset_mock()
        manager.open_workdir.return_value = AppToolActionResult(True, "OPENED", "opened")
        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.LeftButton)
        notifier.assert_not_called()

    def test_row_owns_no_log_panel_and_routes_hover_to_shared_controller(self):
        manager = self.make_manager()
        controller = MagicMock()
        widget = self.make_widget(manager, controller=controller)

        self.assertFalse(hasattr(widget, "inline_log_panel"))
        self.assertIs(widget.log_controller, controller)

        widget.btn_ologs.hover_entered.emit()
        widget.btn_ologs.hover_left.emit()
        widget.btn_elogs.hover_entered.emit()
        widget.btn_elogs.hover_left.emit()
        QTest.mouseClick(widget.btn_ologs, Qt.MouseButton.LeftButton)
        QTest.mouseClick(widget.btn_elogs, Qt.MouseButton.LeftButton)

        controller.hover_enter.assert_any_call(widget.log_target, "stdout")
        controller.hover_leave.assert_any_call(widget.log_target, "stdout")
        controller.hover_enter.assert_any_call(widget.log_target, "stderr")
        controller.hover_leave.assert_any_call(widget.log_target, "stderr")
        manager.view_output_log.assert_called_once_with(False)
        manager.view_error_log.assert_called_once_with(False)


class TrayFailureNotificationTests(unittest.TestCase):
    def test_folder_failure_notification_is_non_modal(self):
        tray = type("Tray", (), {"showMessage": MagicMock()})()
        result = failure("could not open")
        SystemTrayApp.notify_app_tool_failure(tray, "Demo", result)
        tray.showMessage.assert_called_once_with(
            "Open folder failed",
            "Demo: could not open",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )


if __name__ == "__main__":
    unittest.main()
