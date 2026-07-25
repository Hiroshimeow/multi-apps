import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from lib.ui.log_preferences import (
    LogPanelPreference,
    LogPanelPreferenceStore,
    default_log_preferences_path,
)
from multi import AppControlWidget, SystemTrayApp, parse_launcher_args


_QT_APP = QApplication.instance() or QApplication([])


class LauncherConfigPathTests(unittest.TestCase):
    def _write_config(self, root: Path, app_name: str) -> Path:
        workdir = root / "work"
        workdir.mkdir(exist_ok=True)
        config_path = root / f"{app_name}.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "global": {"log_dir": str(root / "logs")},
                    "apps": [
                        {
                            "id": app_name.lower(),
                            "name": app_name,
                            "command": "python -c pass",
                            "path": str(workdir),
                            "enabled": True,
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return config_path

    @staticmethod
    def _dispose_tray(tray: SystemTrayApp):
        tray.shutdown_ui()
        tray.log_popup.deleteLater()
        tray.tray_panel.deleteLater()
        tray.hide()
        tray.deleteLater()
        QApplication.processEvents()

    def test_default_config_path_is_canonical_and_qt_args_are_unchanged(self):
        parsed = parse_launcher_args(["multi.py", "-platform", "offscreen"])
        self.assertEqual(Path(parsed.config_path).name, "setting.yaml")
        self.assertTrue(Path(parsed.config_path).is_absolute())
        self.assertEqual(parsed.qt_argv, ("multi.py", "-platform", "offscreen"))
        self.assertEqual(
            parsed.launcher_argv,
            ("multi.py", "--config", parsed.config_path, "-platform", "offscreen"),
        )

    def test_explicit_config_path_is_resolved_once_and_removed_from_qt_args(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw = Path(temp_dir) / "nested" / ".." / "custom.yaml"
            parsed = parse_launcher_args(
                ["multi.py", "-style", "fusion", "--config", str(raw), "-platform", "offscreen"]
            )
            expected = str(raw.resolve(strict=False))
            self.assertEqual(parsed.config_path, expected)
            self.assertEqual(
                parsed.qt_argv,
                ("multi.py", "-style", "fusion", "-platform", "offscreen"),
            )
            self.assertEqual(
                parsed.launcher_argv,
                (
                    "multi.py",
                    "--config",
                    expected,
                    "-style",
                    "fusion",
                    "-platform",
                    "offscreen",
                ),
            )

    def test_unknown_qt_args_are_preserved_verbatim(self):
        parsed = parse_launcher_args(
            ["multi.py", "--mystery-qt-arg", "value", "--config=C:/Temp/test.yaml"]
        )
        self.assertEqual(
            parsed.qt_argv,
            ("multi.py", "--mystery-qt-arg", "value"),
        )
        self.assertEqual(parsed.launcher_argv.count("--config"), 1)
        self.assertNotIn("--config=C:/Temp/test.yaml", parsed.launcher_argv)

    def test_missing_config_value_is_rejected_before_qt_startup(self):
        with self.assertRaisesRegex(ValueError, "--config requires a path"):
            parse_launcher_args(["multi.py", "--config"])

    def test_tray_loads_only_supplied_real_temporary_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supplied = self._write_config(root, "Supplied")
            decoy = self._write_config(root, "Decoy")
            with patch("multi.os.getcwd", return_value=str(decoy.parent)):
                tray = SystemTrayApp(
                    QIcon(),
                    config_path=str(supplied),
                    launcher_argv=("multi.py", "--config", str(supplied)),
                )
            try:
                self.assertEqual(tray.config_path, str(supplied.resolve(strict=False)))
                self.assertEqual([manager.name for manager in tray.managers], ["Supplied"])
                self.assertEqual(
                    tray.controller.config_manager.config_path,
                    str(supplied.resolve(strict=False)),
                )
            finally:
                self._dispose_tray(tray)

    def test_temporary_config_uses_adjacent_default_preference_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supplied = self._write_config(root, "Adjacent")
            tray = SystemTrayApp(
                QIcon(),
                config_path=str(supplied),
                launcher_argv=("multi.py", "--config", str(supplied)),
            )
            try:
                self.assertEqual(
                    tray.log_preference_store.path,
                    default_log_preferences_path(supplied),
                )
                self.assertFalse(tray.log_preference_store.path.exists())
            finally:
                self._dispose_tray(tray)

    def test_injected_preference_path_restores_values_after_panel_rebuild(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supplied = self._write_config(root, "Before")
            preference_path = root / "evidence" / "preferences.json"
            tray = SystemTrayApp(
                QIcon(),
                config_path=str(supplied),
                launcher_argv=("multi.py", "--config", str(supplied)),
                log_preferences_path=preference_path,
            )
            tray.auto_start_timer.stop()
            try:
                row = tray.row_widgets[0]
                panel = tray.log_popup.panel
                tray.log_controller.open_target(row.log_target, "stdout")
                panel.line_count.setValue(5000)
                panel.filter_edit.setText("[alpha,!drop-me]")
                tray.log_controller.open_target(row.log_target, "stderr")
                QApplication.processEvents()

                tray.rebuild_panel()
                restored = tray.row_widgets[0]
                tray.log_controller.open_target(restored.log_target, "stderr")
                QApplication.processEvents()

                self.assertIsNot(restored, row)
                self.assertEqual(panel.line_count.value(), 5000)
                self.assertEqual(panel.filter_edit.text(), "[alpha,!drop-me]")
                self.assertEqual(panel.stream_label.text(), "stderr")
                self.assertTrue(tray.log_preference_owner.flush(timeout=1))
                self.assertEqual(
                    LogPanelPreferenceStore(preference_path).load("before"),
                    LogPanelPreference(5000, "[alpha,!drop-me]", "stderr"),
                )
            finally:
                self._dispose_tray(tray)

    def test_invalid_preference_falls_back_without_changing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supplied = self._write_config(root, "Stable")
            before = supplied.read_bytes()
            preference_path = root / "preferences.json"
            preference_path.write_text(
                '{"version":1,"apps":{"stable":{"line_count":0,"filter_expression":4,"stream":"bad"}}}',
                encoding="utf-8",
            )
            tray = SystemTrayApp(
                QIcon(),
                config_path=str(supplied),
                launcher_argv=("multi.py", "--config", str(supplied)),
                log_preferences_path=preference_path,
            )
            tray.auto_start_timer.stop()
            try:
                tray.log_controller.open_target(
                    tray.row_widgets[0].log_target,
                    "stdout",
                )
                panel = tray.log_popup.panel
                self.assertEqual(panel.line_count.value(), 100)
                self.assertEqual(panel.filter_edit.text(), "")
                self.assertEqual(panel.stream_label.text(), "stdout")
                self.assertEqual(supplied.read_bytes(), before)
            finally:
                self._dispose_tray(tray)

    def test_refresh_reuses_same_config_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            supplied = self._write_config(root, "Before")
            tray = SystemTrayApp(
                QIcon(),
                config_path=str(supplied),
                launcher_argv=("multi.py", "--config", str(supplied)),
            )
            try:
                config = yaml.safe_load(supplied.read_text(encoding="utf-8"))
                config["apps"][0]["name"] = "After"
                config["apps"][0]["id"] = "after"
                supplied.write_text(
                    yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
                )
                tray.refresh_all()
                self.assertEqual([manager.name for manager in tray.managers], ["After"])
                self.assertEqual(
                    tray.controller.config_manager.config_path,
                    str(supplied.resolve(strict=False)),
                )
            finally:
                self._dispose_tray(tray)

    def test_replacement_launcher_preserves_config_and_qt_args_exactly_once(self):
        config_path = str(Path("C:/Temp/config.yaml").resolve(strict=False))
        launcher_argv = (
            "multi.py",
            "--config",
            config_path,
            "-platform",
            "offscreen",
            "--mystery",
            "value",
        )
        tray = SimpleNamespace(launcher_argv=launcher_argv)
        with patch("multi.subprocess.Popen") as popen, patch(
            "multi.is_windows", return_value=True
        ):
            SystemTrayApp._spawn_replacement_launcher(tray)

        args, _kwargs = popen.call_args
        self.assertEqual(args[0][1:], list(launcher_argv))
        self.assertEqual(args[0].count("--config"), 1)
        self.assertEqual(args[0][args[0].index("--config") + 1], config_path)

    def test_existing_constructor_call_remains_compatible(self):
        signature = inspect.signature(SystemTrayApp.__init__)
        self.assertEqual(signature.parameters["config_path"].default, "setting.yaml")
        tray = SystemTrayApp(QIcon())
        tray.auto_start_timer.stop()
        try:
            self.assertTrue(Path(tray.config_path).is_absolute())
            self.assertEqual(Path(tray.config_path).name, "setting.yaml")
            self.assertFalse(hasattr(tray, "menu"))
        finally:
            self._dispose_tray(tray)


if __name__ == "__main__":
    unittest.main()
