import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.config import ConfigManager
from lib.runners.command_runner import CommandRunner
from lib.session.subprocess_session import SubprocessSessionManager


class SimpleConfigTests(unittest.TestCase):
    def test_path_is_workdir_and_args_are_appended_verbatim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir = root / "app"
            app_dir.mkdir()
            config_file = root / "setting.yaml"
            config_file.write_text(
                """
global:
  log_dir: ./logs
apps:
  - name: Demo
    path: ./app
    command: uv run main.py
    args:
      - --repo E:/python_project
    enabled: true
""".strip(),
                encoding="utf-8",
            )

            app = ConfigManager(config_file).get_app("Demo")
            self.assertEqual(app["path"], str(app_dir.resolve()))
            runner = CommandRunner(app, {})
            self.assertEqual(
                runner.build_command(),
                "uv run main.py --repo E:/python_project",
            )
            self.assertTrue(runner.should_use_shell())

    def test_name_and_command_are_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "setting.yaml"
            config_file.write_text(
                "apps:\n  - name: Missing command\n  - command: echo missing-name\n",
                encoding="utf-8",
            )
            self.assertEqual(ConfigManager(config_file).get_apps(), [])

    def test_os_filter(self):
        manager = ConfigManager.__new__(ConfigManager)
        manager.config = {
            "apps": [
                {"name": "Windows", "enabled": True, "os": "win11"},
                {"name": "Linux", "enabled": True, "os": "linux"},
            ]
        }
        with patch("lib.config.is_windows", return_value=True), patch(
            "lib.config.is_linux", return_value=False
        ):
            self.assertEqual([app["name"] for app in manager.get_apps()], ["Windows"])


class SubprocessSessionTests(unittest.TestCase):
    def test_command_runs_inside_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = (
                subprocess.list2cmdline([sys.executable])
                if os.name == "nt"
                else shlex.quote(sys.executable)
            )
            command = (
                f'{executable} -c "from pathlib import Path; '
                "Path('marker.txt').write_text('ok', encoding='utf-8')\""
            )
            app = {
                "name": "Smoke",
                "path": str(root),
                "command": command,
                "args": [],
                "multi_run": False,
            }
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )
            success, message = session.start(CommandRunner(app, {}))
            self.assertTrue(success, message)

            marker = root / "marker.txt"
            deadline = time.time() + 5
            while time.time() < deadline and not marker.exists():
                time.sleep(0.05)

            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "ok")
            session.get_info("Smoke")


if __name__ == "__main__":
    unittest.main()
