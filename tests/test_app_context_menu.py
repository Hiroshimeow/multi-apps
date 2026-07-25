import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QWidget

from lib.ui.app_context_popup import AppContextPopup
from lib.ui.app_tools import AppToolActionResult, AppToolService
from multi import AppControlWidget, AppNameLabel


_QT_APP = QApplication.instance() or QApplication([])


def success(target="C:/work"):
    return AppToolActionResult(True, "OPENED", "opened", target=target)


def failure(message="failed"):
    return AppToolActionResult(False, "LAUNCH_FAILED", message, target="C:/work")


class AppToolTerminalTests(unittest.TestCase):
    def test_windows_terminal_launch_uses_exact_cwd_without_global_chdir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            launcher = MagicMock()
            service = AppToolService(
                platform_name="Windows",
                which=lambda name: "C:/Windows/System32/cmd.exe" if name == "cmd.exe" else None,
                process_launcher=launcher,
            )
            old_cwd = Path.cwd()

            result = service.open_terminal(str(root))

            self.assertTrue(result.ok)
            self.assertEqual(Path.cwd(), old_cwd)
            self.assertEqual(result.target, str(root))
            launcher.assert_called_once()
            args, kwargs = launcher.call_args
            self.assertEqual(
                args[0],
                ["C:/Windows/System32/cmd.exe", "/D", "/K"],
            )
            self.assertEqual(Path(kwargs["cwd"]), root)
            self.assertEqual(
                kwargs["creationflags"],
                getattr(__import__("subprocess"), "CREATE_NEW_CONSOLE", 0),
            )
            self.assertNotIn("stdin", kwargs)

    def test_windows_terminal_prefers_wt_and_passes_directory_argument(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            launcher = MagicMock()
            service = AppToolService(
                platform_name="Windows",
                which=lambda name: "C:/Windows/System32/wt.exe" if name == "wt.exe" else None,
                process_launcher=launcher,
            )

            result = service.open_terminal(str(root))

            self.assertTrue(result.ok)
            args, kwargs = launcher.call_args
            self.assertEqual(
                args[0],
                ["C:/Windows/System32/wt.exe", "-d", str(root)],
            )
            self.assertNotIn("cwd", kwargs)
            self.assertIn("stdin", kwargs)



class AppNameLabelTests(unittest.TestCase):
    def test_left_click_and_context_keyboard_have_distinct_signals(self):
        label = AppNameLabel("Demo")
        label.resize(160, 30)
        label.show()
        label.setFocus()
        QApplication.processEvents()
        try:
            left = QSignalSpy(label.left_clicked)
            context = QSignalSpy(label.context_requested)
            QTest.mouseClick(label, Qt.MouseButton.LeftButton)
            QTest.mouseClick(label, Qt.MouseButton.RightButton)
            QTest.keyClick(label, Qt.Key.Key_Menu)
            QTest.keyClick(label, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(len(left), 1)
            self.assertEqual(len(context), 3)
        finally:
            label.close()
            label.deleteLater()
            QApplication.processEvents()


class AppContextPopupTests(unittest.TestCase):
    def test_popup_contains_only_terminal_action_and_hides_after_trigger(self):
        popup = AppContextPopup()
        callback = MagicMock()
        try:
            popup.show_action(
                QPoint(400, 300),
                text="Open terminal here",
                callback=callback,
                enabled=True,
                tooltip="ready",
            )
            QApplication.processEvents()
            self.assertTrue(popup.isVisible())
            self.assertEqual(popup.action_button.text(), "Open terminal here")
            QTest.mouseClick(popup.action_button, Qt.MouseButton.LeftButton)
            QApplication.processEvents()
            callback.assert_called_once_with()
            self.assertFalse(popup.isVisible())
        finally:
            popup.deleteLater()
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
        manager.open_workdir.return_value = success()
        return manager

    def make_widget(self, manager=None, notifier=None, controller=None, lifecycle=None):
        host = QWidget()
        widget = AppControlWidget(
            manager or self.make_manager(),
            parent=host,
            result_notifier=notifier,
            log_controller=controller,
            lifecycle_controller=lifecycle,
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

    def test_name_left_click_opens_folder_and_context_routes_manager_and_position(self):
        manager = self.make_manager()
        widget = self.make_widget(manager)
        context = QSignalSpy(widget.context_requested)

        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.LeftButton)
        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.RightButton)
        QApplication.processEvents()

        manager.open_workdir.assert_called_once_with()
        self.assertEqual(len(context), 1)
        self.assertIs(context[0][0], manager)
        self.assertIsInstance(context[0][1], QPoint)
        self.assertIn("Right-click", widget.lbl_name.toolTip())

    def test_folder_failure_notifies_once_success_does_not_notify(self):
        notifier = MagicMock()
        manager = self.make_manager()
        manager.open_workdir.return_value = failure("folder vanished")
        widget = self.make_widget(manager, notifier)

        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.LeftButton)
        notifier.assert_called_once_with(manager.name, manager.open_workdir.return_value)

        notifier.reset_mock()
        manager.open_workdir.return_value = success()
        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.LeftButton)
        notifier.assert_not_called()


if __name__ == "__main__":
    unittest.main()
