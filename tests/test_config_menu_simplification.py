import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from PyQt6.QtWidgets import QApplication, QStyle

from lib.config import ConfigManager
from lib.ui.app_tools import AppToolActionResult, AppToolService
from lib.ui.tray_panel import TrayActionButton
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
            self.assertNotIn("tools", ConfigManager(config_path).get_apps()[0])


class AppNameInteractionSimplificationTests(unittest.TestCase):
    def test_app_name_has_terminal_only_independent_context_contract(self):
        label = AppNameLabel("Demo")
        try:
            self.assertTrue(hasattr(label, "context_requested"))
            self.assertFalse(hasattr(AppControlWidget, "build_app_context_menu"))
            self.assertFalse(hasattr(AppControlWidget, "show_app_context_menu"))
        finally:
            label.deleteLater()
            QApplication.processEvents()


class TrayPanelActionTests(unittest.TestCase):
    def test_panel_opens_active_config_and_has_only_four_global_actions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "setting.yaml"
            config_path.write_text("apps: []\n", encoding="utf-8")
            tray = SystemTrayApp(
                _QT_APP.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                config_path=str(config_path),
                launcher_argv=("multi.py", "--config", str(config_path)),
            )
            tray.auto_start_timer.stop()
            service = MagicMock()
            service.open_file.return_value = AppToolActionResult(
                True,
                "OPENED",
                "opened",
                target=str(config_path),
            )
            tray.tool_service = service
            try:
                actions = [
                    tray.tray_panel.actions_layout.itemAt(index).widget()
                    for index in range(tray.tray_panel.actions_layout.count())
                ]
                self.assertTrue(all(isinstance(action, TrayActionButton) for action in actions))
                self.assertEqual(
                    [action.text() for action in actions],
                    [
                        "Open Config",
                        "Stop All Apps",
                        "Restart Launcher",
                        "Exit Launcher",
                    ],
                )
                self.assertFalse(hasattr(tray, "menu"))

                tray.open_config_action.click()
                service.open_file.assert_called_once_with(str(config_path.resolve()))
            finally:
                tray.shutdown_ui()
                tray.log_popup.deleteLater()
                tray.tray_panel.deleteLater()
                tray.deleteLater()
                QApplication.processEvents()


class PathToolServiceTests(unittest.TestCase):
    def test_open_file_accepts_a_plain_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "setting.yaml"
            target.write_text("apps: []\n", encoding="utf-8")
            start_file = MagicMock()
            service = AppToolService(platform_name="Windows", start_file=start_file)
            result = service.open_file(str(target))
            self.assertTrue(result.ok)
            self.assertEqual(result.target, str(target.resolve()))
            start_file.assert_called_once_with(str(target.resolve()))


if __name__ == "__main__":
    unittest.main()
