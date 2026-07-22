import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QMenu, QStyle, QWidgetAction

from lib.config import ConfigManager
from lib.ui.app_tools import AppToolActionResult, AppToolService
from multi import AppControlWidget, AppNameLabel, SystemTrayApp


_QT_APP = QApplication.instance() or QApplication([])


class ConfigSchemaSimplificationTests(unittest.TestCase):
    def test_per_app_tools_are_not_part_of_normalized_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "setting.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "apps": [
                            {
                                "id": "demo",
                                "name": "Demo",
                                "path": str(root),
                                "command": "python demo.py",
                                "tools": [
                                    {
                                        "type": "open_file",
                                        "label": "Old per-app config",
                                        "path": "demo.yaml",
                                    }
                                ],
                            }
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            app = ConfigManager(config_path).get_apps()[0]

            self.assertNotIn("tools", app)


class AppNameInteractionSimplificationTests(unittest.TestCase):
    def test_app_name_has_no_context_menu_contract(self):
        label = AppNameLabel("Demo")
        try:
            self.assertFalse(hasattr(label, "context_requested"))
            self.assertFalse(hasattr(AppControlWidget, "build_app_context_menu"))
            self.assertFalse(hasattr(AppControlWidget, "show_app_context_menu"))
        finally:
            label.deleteLater()
            QApplication.processEvents()


class TrayConfigActionTests(unittest.TestCase):
    def test_tray_menu_opens_the_active_config_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = str((Path(temp_dir) / "setting.yaml").resolve())
            Path(config_path).write_text("apps: []\n", encoding="utf-8")
            service = MagicMock()
            service.file_status.return_value = AppToolActionResult(
                True,
                "READY",
                f"Open file: {config_path}",
                target=config_path,
            )
            service.open_file.return_value = AppToolActionResult(
                True,
                "OPENED",
                f"Opened: {config_path}",
                target=config_path,
            )
            menu = QMenu()
            tray = SimpleNamespace(
                menu=menu,
                managers=[],
                config_path=config_path,
                tool_service=service,
                refresh_all=lambda: None,
                stop_all_apps=lambda: None,
                restart_app=lambda: None,
                exit_app=lambda: None,
                notify_launcher_tool_failure=MagicMock(),
                _menu_generation=0,
            )
            try:
                SystemTrayApp.refresh_menu(tray)
                actions = [action for action in menu.actions() if not action.isSeparator()]
                open_config = next(action for action in actions if action.text() == "Open Config")

                self.assertTrue(open_config.isEnabled())
                self.assertNotIn("Refresh Menu", [action.text() for action in actions])
                self.assertEqual(
                    [action.text() for action in actions[-4:]],
                    ["Open Config", "Stop All Apps", "Restart Launcher", "Exit Launcher"],
                )
                open_config.trigger()

                service.file_status.assert_called_once_with(config_path)
                service.open_file.assert_called_once_with(config_path)
                tray.notify_launcher_tool_failure.assert_not_called()
            finally:
                if hasattr(tray, "log_coordinator"):
                    tray.log_coordinator.shutdown()
                menu.clear()
                menu.deleteLater()
                QApplication.processEvents()


class TopLogPanelLayoutTests(unittest.TestCase):
    def test_log_panel_host_is_above_all_app_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "setting.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "global": {"log_dir": str(root / "logs")},
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
            tray = SystemTrayApp(
                _QT_APP.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                config_path=str(config_path),
                launcher_argv=("multi.py", "--config", str(config_path)),
            )
            tray.auto_start_timer.stop()
            try:
                row = tray.menu.findChild(AppControlWidget)
                actions = tray.menu.actions()
                row_action = next(
                    action
                    for action in actions
                    if isinstance(action, QWidgetAction)
                    and action.defaultWidget() is row
                )

                self.assertLess(
                    actions.index(tray.log_panel_action),
                    actions.index(row_action),
                )
                self.assertIs(row.inline_log_panel.parentWidget(), tray.log_panel_host)
                self.assertFalse(tray.log_panel_action.isVisible())

                tray.menu.popup(QPoint(100, 100))
                QApplication.processEvents()
                tray.log_coordinator.request_open(row, "stdout")
                QApplication.processEvents()

                self.assertTrue(tray.log_panel_action.isVisible())
                self.assertTrue(row.inline_log_panel.isVisible())
                self.assertEqual(row.layout().count(), 1)
            finally:
                for row in tuple(tray.menu.findChildren(AppControlWidget)):
                    row.shutdown()
                tray.log_coordinator.shutdown()
                tray.menu.clear()
                tray.menu.deleteLater()
                tray.hide()
                tray.deleteLater()
                QApplication.processEvents()


class PathToolServiceTests(unittest.TestCase):
    def test_open_file_accepts_a_plain_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "setting.yaml"
            target.write_text("apps: []\n", encoding="utf-8")
            start_file = MagicMock()
            service = AppToolService(
                platform_name="Windows",
                start_file=start_file,
            )

            result = service.open_file(str(target))

            self.assertTrue(result.ok)
            self.assertEqual(result.target, str(target.resolve()))
            start_file.assert_called_once_with(str(target.resolve()))


if __name__ == "__main__":
    unittest.main()
