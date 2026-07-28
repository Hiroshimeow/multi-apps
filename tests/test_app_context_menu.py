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
from multi import AppControlWidget, AppManager, AppNameLabel


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

    def test_windows_run_in_terminal_uses_exact_command_and_cwd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            launcher = MagicMock()
            service = AppToolService(
                platform_name="Windows",
                which=lambda name: "C:/Windows/System32/cmd.exe" if name == "cmd.exe" else None,
                process_launcher=launcher,
            )

            result = service.open_terminal(
                str(root),
                'uv run server.py --port 8000 --name "Demo App"',
            )

            self.assertTrue(result.ok)
            args, kwargs = launcher.call_args
            self.assertEqual(
                args[0],
                [
                    "C:/Windows/System32/cmd.exe",
                    "/D",
                    "/K",
                    'uv run server.py --port 8000 --name "Demo App"',
                ],
            )
            self.assertEqual(Path(kwargs["cwd"]), root)
            self.assertEqual(
                kwargs["creationflags"],
                getattr(__import__("subprocess"), "CREATE_NEW_CONSOLE", 0),
            )


class AppManagerTerminalTests(unittest.TestCase):
    def test_run_with_terminal_uses_yaml_command_without_managed_start(self):
        controller = MagicMock()
        controller.config_manager.get_app.return_value = {
            "id": "demo",
            "name": "Demo",
            "path": "C:/work/demo",
            "command": "uv run server.py",
            "args": ["--port 8000", '--name "Demo App"'],
        }
        controller.get_app_workdir.return_value = "C:/work/demo"
        tool_service = MagicMock()
        tool_service.open_terminal.return_value = success("C:/work/demo")
        manager = AppManager(controller, "Demo", tool_service=tool_service)

        result = manager.run_with_terminal()

        self.assertTrue(result.ok)
        tool_service.open_terminal.assert_called_once_with(
            "C:/work/demo",
            'uv run server.py --port 8000 --name "Demo App"',
        )
        controller.start_app.assert_not_called()


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
    def test_popup_contains_run_and_open_terminal_actions(self):
        popup = AppContextPopup()
        run_callback = MagicMock()
        open_callback = MagicMock()
        try:
            popup.show_actions(
                QPoint(400, 300),
                run_callback=run_callback,
                run_enabled=True,
                run_tooltip="run ready",
                terminal_callback=open_callback,
                terminal_enabled=True,
                terminal_tooltip="terminal ready",
            )
            QApplication.processEvents()
            self.assertTrue(popup.isVisible())
            self.assertEqual(popup.run_button.text(), "Run with terminal")
            self.assertEqual(popup.terminal_button.text(), "Open terminal here")

            QTest.mouseClick(popup.run_button, Qt.MouseButton.LeftButton)
            QApplication.processEvents()
            run_callback.assert_called_once_with()
            open_callback.assert_not_called()
            self.assertFalse(popup.isVisible())

            popup.show_actions(
                QPoint(400, 300),
                run_callback=run_callback,
                run_enabled=True,
                terminal_callback=open_callback,
                terminal_enabled=True,
            )
            QTest.mouseClick(popup.terminal_button, Qt.MouseButton.LeftButton)
            QApplication.processEvents()
            open_callback.assert_called_once_with()
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
