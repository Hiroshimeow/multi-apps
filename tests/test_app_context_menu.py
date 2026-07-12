import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from lib.ui.app_tools import AppToolAction, AppToolActionResult
from multi import AppControlWidget, AppNameLabel, SystemTrayApp


_QT_APP = QApplication.instance() or QApplication([])


def ready(message, target="C:/work", argv=()):
    return AppToolActionResult(True, "READY", message, target=target, argv=argv)


def failure(code="LAUNCH_FAILED", message="failed", target="C:/work"):
    return AppToolActionResult(False, code, message, target=target)


class AppNameLabelTests(unittest.TestCase):
    def setUp(self):
        self.label = AppNameLabel("Demo")
        self.label.resize(160, 30)
        self.label.show()
        self.label.setFocus()
        QApplication.processEvents()

    def tearDown(self):
        self.label.close()
        self.label.deleteLater()
        QApplication.processEvents()

    def test_left_middle_and_right_mouse_signals_are_separated(self):
        left = QSignalSpy(self.label.left_clicked)
        context = QSignalSpy(self.label.context_requested)

        QTest.mouseClick(self.label, Qt.MouseButton.LeftButton)
        self.assertEqual(len(left), 1)
        self.assertEqual(len(context), 0)

        QTest.mouseClick(self.label, Qt.MouseButton.MiddleButton)
        self.assertEqual(len(left), 1)
        self.assertEqual(len(context), 0)

        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            QPoint(10, 10),
            self.label.mapToGlobal(QPoint(10, 10)),
        )
        QApplication.sendEvent(self.label, event)
        self.assertEqual(len(left), 1)
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0][0], self.label.mapToGlobal(QPoint(10, 10)))

    def test_menu_key_and_shift_f10_request_one_context_at_bottom_left(self):
        context = QSignalSpy(self.label.context_requested)
        expected = self.label.mapToGlobal(self.label.rect().bottomLeft())

        QTest.keyClick(self.label, Qt.Key.Key_Menu)
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0][0], expected)

        QTest.keyClick(
            self.label,
            Qt.Key.Key_F10,
            Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(len(context), 2)
        self.assertEqual(context[1][0], expected)
        self.assertEqual(self.label.focusPolicy(), Qt.FocusPolicy.StrongFocus)


class AppContextMenuTests(unittest.TestCase):
    def make_manager(self, tools=()):
        manager = MagicMock()
        manager.name = "Demo"
        manager.app_config = {"id": "demo", "multi_run": False}
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
        manager.read_log_tail.return_value = "log"
        manager.folder_status.return_value = ready("Open folder: C:/work")
        manager.terminal_status.return_value = ready(
            "Open terminal in: C:/work",
            argv=("wt.exe", "-d", "C:/work"),
        )
        manager.get_configured_tools.return_value = tuple(tools)
        manager.file_status.return_value = ready("Open file: C:/work/config.yaml", "C:/work/config.yaml")
        manager.open_workdir.return_value = AppToolActionResult(
            True, "OPENED", "opened", target="C:/work"
        )
        manager.open_terminal.return_value = AppToolActionResult(
            True, "TERMINAL_OPENED", "opened", target="C:/work"
        )
        manager.open_configured_tool.return_value = AppToolActionResult(
            True, "OPENED", "opened", target="C:/work/config.yaml"
        )
        return manager

    def make_widget(self, manager=None, notifier=None):
        parent = QMenu()
        parent.popup(QPoint(200, 200))
        QApplication.processEvents()
        widget = AppControlWidget(manager or self.make_manager(), parent, notifier)
        widget.show()
        QApplication.processEvents()
        self.addCleanup(self.dispose, widget, parent)
        return widget, parent

    @staticmethod
    def dispose(widget, parent):
        widget.timer.stop()
        widget.output_log_preview.hide_preview()
        widget.error_log_preview.hide_preview()
        if widget._app_context_menu is not None:
            widget._app_context_menu.close()
        widget.close()
        parent.close()
        widget.deleteLater()
        parent.deleteLater()
        QApplication.processEvents()

    def test_left_click_calls_folder_once_and_tooltip_documents_both_behaviors(self):
        manager = self.make_manager()
        widget, _parent = self.make_widget(manager)

        QTest.mouseClick(widget.lbl_name, Qt.MouseButton.LeftButton)

        manager.open_workdir.assert_called_once_with()
        manager.open_terminal.assert_not_called()
        self.assertIn("Left-click", widget.lbl_name.toolTip())
        self.assertIn("Right-click", widget.lbl_name.toolTip())

    def test_menu_order_separator_labels_tooltips_and_enabled_states(self):
        tools = (
            AppToolAction("config", "open_file", "Open config.yaml", "C:/work/config.yaml"),
            AppToolAction("missing", "open_file", "Open missing.yaml", "C:/work/missing.yaml"),
        )
        manager = self.make_manager(tools)
        manager.file_status.side_effect = [
            ready("Open file: C:/work/config.yaml", "C:/work/config.yaml"),
            failure("TARGET_MISSING", "File does not exist: C:/work/missing.yaml", "C:/work/missing.yaml"),
        ]
        widget, _parent = self.make_widget(manager)

        menu = widget.build_app_context_menu()
        actions = menu.actions()

        self.assertTrue(menu.toolTipsVisible())
        self.assertEqual(
            [action.text() if not action.isSeparator() else "<separator>" for action in actions],
            [
                "Open folder",
                "Open terminal here",
                "<separator>",
                "Open config.yaml",
                "Open missing.yaml",
            ],
        )
        self.assertEqual(actions[0].toolTip(), "Open folder: C:/work")
        self.assertEqual(actions[1].toolTip(), "Open terminal in: C:/work")
        self.assertEqual(actions[3].toolTip(), "Open file: C:/work/config.yaml")
        self.assertEqual(
            actions[4].toolTip(),
            "File does not exist: C:/work/missing.yaml",
        )
        self.assertTrue(actions[0].isEnabled())
        self.assertTrue(actions[1].isEnabled())
        self.assertTrue(actions[3].isEnabled())
        self.assertFalse(actions[4].isEnabled())

    def test_no_configured_tools_means_no_separator(self):
        widget, _parent = self.make_widget(self.make_manager())
        menu = widget.build_app_context_menu()
        self.assertEqual(
            [action.text() for action in menu.actions()],
            ["Open folder", "Open terminal here"],
        )
        self.assertFalse(any(action.isSeparator() for action in menu.actions()))

    def test_unavailable_terminal_and_wrong_file_type_are_disabled(self):
        wrong_file = AppToolAction(
            "directory",
            "open_file",
            "Open directory as file",
            "C:/work/directory",
        )
        manager = self.make_manager((wrong_file,))
        manager.terminal_status.return_value = failure(
            "TERMINAL_UNAVAILABLE",
            "No supported terminal was found for this system.",
        )
        manager.file_status.return_value = failure(
            "TARGET_NOT_FILE",
            "Configured target is not a file: C:/work/directory",
            "C:/work/directory",
        )
        widget, _parent = self.make_widget(manager)

        actions = [
            action
            for action in widget.build_app_context_menu().actions()
            if not action.isSeparator()
        ]

        self.assertFalse(actions[1].isEnabled())
        self.assertEqual(
            actions[1].toolTip(),
            "No supported terminal was found for this system.",
        )
        self.assertFalse(actions[2].isEnabled())
        self.assertEqual(
            actions[2].toolTip(),
            "Configured target is not a file: C:/work/directory",
        )

    def test_each_enabled_action_invokes_exactly_one_handler(self):
        tool = AppToolAction("config", "open_file", "Open config.yaml", "C:/work/config.yaml")
        manager = self.make_manager((tool,))
        widget, _parent = self.make_widget(manager)
        menu = widget.build_app_context_menu()
        actions = [action for action in menu.actions() if not action.isSeparator()]

        actions[0].trigger()
        actions[1].trigger()
        actions[2].trigger()

        manager.open_workdir.assert_called_once_with()
        manager.open_terminal.assert_called_once_with()
        manager.open_configured_tool.assert_called_once_with(tool)
        manager.launch.assert_not_called()
        manager.stop_all.assert_not_called()
        manager.controller.start_app.assert_not_called()
        manager.controller.stop_app.assert_not_called()
        manager.controller.stop_all.assert_not_called()

    def test_disabled_action_never_invokes_handler(self):
        manager = self.make_manager()
        manager.folder_status.return_value = failure(
            "TARGET_MISSING", "Folder does not exist: C:/missing", "C:/missing"
        )
        widget, _parent = self.make_widget(manager)
        action = widget.build_app_context_menu().actions()[0]

        self.assertFalse(action.isEnabled())
        action.trigger()
        manager.open_workdir.assert_not_called()

    def test_failure_notifies_once_success_does_not_notify(self):
        notifier = MagicMock()
        manager = self.make_manager()
        manager.open_workdir.return_value = failure(message="folder vanished")
        widget, _parent = self.make_widget(manager, notifier)

        widget.build_app_context_menu().actions()[0].trigger()
        notifier.assert_called_once_with(manager.name, manager.open_workdir.return_value)

        notifier.reset_mock()
        manager.open_workdir.return_value = AppToolActionResult(True, "OPENED", "opened")
        widget.build_app_context_menu().actions()[0].trigger()
        notifier.assert_not_called()

    def test_context_menu_cancel_and_mocked_success_do_not_explicitly_hide_parent(self):
        manager = self.make_manager()
        parent = MagicMock()
        parent.isVisible.return_value = True
        widget = AppControlWidget(manager, parent)
        widget.show()
        QApplication.processEvents()
        self.addCleanup(self.dispose, widget, parent)

        menu = widget.show_app_context_menu(widget.lbl_name.mapToGlobal(QPoint(0, 0)))
        QApplication.processEvents()
        menu.close()
        QApplication.processEvents()

        menu = widget.build_app_context_menu()
        menu.actions()[0].trigger()
        QApplication.processEvents()

        self.assertTrue(parent.isVisible())
        parent.hide.assert_not_called()
        parent.close.assert_not_called()

    def test_context_menu_reference_is_replaced_and_released_after_close(self):
        widget, _parent = self.make_widget(self.make_manager())
        first = widget.show_app_context_menu(QPoint(300, 300))
        self.assertIs(widget._app_context_menu, first)
        first.close()
        QApplication.processEvents()
        self.assertIsNone(widget._app_context_menu)

        second = widget.show_app_context_menu(QPoint(320, 320))
        self.assertIs(widget._app_context_menu, second)
        self.assertIsNot(first, second)


class TrayToolNotifierTests(unittest.TestCase):
    def test_failure_notification_is_non_modal_and_has_locked_fields(self):
        tray = SimpleNamespace(showMessage=MagicMock())
        result = failure(message="could not open")

        SystemTrayApp.notify_app_tool_failure(tray, "Demo", result)

        tray.showMessage.assert_called_once_with(
            "App tool failed",
            "Demo: could not open",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )


if __name__ == "__main__":
    unittest.main()
