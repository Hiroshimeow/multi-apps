import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.config import ConfigManager, slugify_app_id


class ConfigLifecycleFieldTests(unittest.TestCase):
    def _write_config(self, root: Path, apps_yaml: str) -> Path:
        config_path = root / "setting.yaml"
        config_path.write_text(f"apps:\n{apps_yaml}", encoding="utf-8")
        return config_path

    def test_missing_id_generates_deterministic_slug_and_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(
                root,
                "  - name: Đemo App / Worker\n"
                "    command: python worker.py\n",
            )

            app = ConfigManager(config_path).get_apps()[0]

            self.assertEqual(app["id"], "demo-app-worker")
            self.assertFalse(app["args_edit"])
            self.assertEqual(app["close_timeout"], 5.0)

    def test_explicit_id_is_trimmed_but_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(
                root,
                "  - id: '  Stable.ID_1  '\n"
                "    name: Demo\n"
                "    command: python demo.py\n"
                "    args_edit: true\n"
                "    close_timeout: 1.25\n",
            )

            app = ConfigManager(config_path).get_apps()[0]

            self.assertEqual(app["id"], "Stable.ID_1")
            self.assertTrue(app["args_edit"])
            self.assertEqual(app["close_timeout"], 1.25)

    def test_duplicate_ids_are_skipped_with_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(
                root,
                "  - id: duplicate\n"
                "    name: First\n"
                "    command: echo first\n"
                "  - id: duplicate\n"
                "    name: Second\n"
                "    command: echo second\n",
            )

            with patch("lib.config.print_warning") as warning:
                apps = ConfigManager(config_path).get_apps()

            self.assertEqual([app["name"] for app in apps], ["First"])
            warning.assert_called_once_with(
                "Skipping app 'Second': duplicate id 'duplicate'."
            )

    def test_invalid_close_timeout_uses_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(
                root,
                "  - name: Demo\n"
                "    command: echo demo\n"
                "    close_timeout: 0\n",
            )

            with patch("lib.config.print_warning") as warning:
                app = ConfigManager(config_path).get_apps()[0]

            self.assertEqual(app["close_timeout"], 5.0)
            warning.assert_called_once()

    def test_slug_falls_back_for_non_alphanumeric_name(self):
        self.assertEqual(slugify_app_id("***"), "app")


if __name__ == "__main__":
    unittest.main()
