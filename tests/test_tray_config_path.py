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

from multi import SystemTrayApp, parse_launcher_args


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
        tray.auto_start_timer.stop()
        if hasattr(tray, "menu"):
            for timer in tray.menu.findChildren(type(tray.auto_start_timer)):
                timer.stop()
            tray.menu.clear()
            tray.menu.deleteLater()
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
        with patch.object(SystemTrayApp, "load_config") as load_config, patch.object(
            SystemTrayApp, "refresh_menu"
        ):
            tray = SystemTrayApp(QIcon())
        try:
            self.assertTrue(Path(tray.config_path).is_absolute())
            self.assertEqual(Path(tray.config_path).name, "setting.yaml")
            load_config.assert_called_once_with()
        finally:
            tray.auto_start_timer.stop()
            tray.menu.deleteLater()
            tray.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
