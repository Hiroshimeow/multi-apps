import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from lib.ui.app_tools import AppToolActionResult, AppToolService


class AppToolActionResultTests(unittest.TestCase):
    def test_result_is_data_only(self):
        result = AppToolActionResult(
            True,
            "OPENED",
            "Opened",
            target="C:/work/setting.yaml",
            argv=("xdg-open", "C:/work/setting.yaml"),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.code, "OPENED")
        self.assertEqual(result.target, "C:/work/setting.yaml")
        self.assertEqual(result.argv, ("xdg-open", "C:/work/setting.yaml"))


class AppToolServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.folder = self.root / "work"
        self.folder.mkdir()
        self.file = self.root / "setting.yaml"
        self.file.write_text("apps: []\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_folder_and_file_status_cover_ready_missing_and_wrong_kind(self):
        service = AppToolService(platform_name="Windows", start_file=MagicMock())

        self.assertEqual(service.folder_status(str(self.folder)).code, "READY")
        self.assertEqual(service.folder_status(str(self.file)).code, "TARGET_NOT_DIRECTORY")
        self.assertEqual(
            service.folder_status(str(self.root / "missing")).code,
            "TARGET_MISSING",
        )

        self.assertEqual(service.file_status(str(self.file)).code, "READY")
        self.assertEqual(service.file_status(str(self.folder)).code, "TARGET_NOT_FILE")
        self.assertEqual(
            service.file_status(str(self.root / "missing.yaml")).code,
            "TARGET_MISSING",
        )

    def test_windows_folder_and_file_open_exact_canonical_targets(self):
        start_file = MagicMock()
        service = AppToolService(platform_name="Windows", start_file=start_file)

        folder_result = service.open_folder(str(self.folder))
        file_result = service.open_file(str(self.file))

        self.assertTrue(folder_result.ok)
        self.assertTrue(file_result.ok)
        self.assertEqual(
            start_file.call_args_list,
            [
                unittest.mock.call(str(self.folder.resolve())),
                unittest.mock.call(str(self.file.resolve())),
            ],
        )

    def test_linux_uses_xdg_open_without_shell_or_global_cwd_change(self):
        launcher = MagicMock()
        service = AppToolService(
            platform_name="Linux",
            which=lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None,
            process_launcher=launcher,
        )
        original_cwd = os.getcwd()

        result = service.open_file(str(self.file))

        self.assertTrue(result.ok)
        self.assertEqual(os.getcwd(), original_cwd)
        launcher.assert_called_once_with(
            ["/usr/bin/xdg-open", str(self.file.resolve())],
            stdin=unittest.mock.ANY,
            stdout=unittest.mock.ANY,
            stderr=unittest.mock.ANY,
            start_new_session=True,
        )

    def test_missing_opener_and_launch_exception_return_structured_failures(self):
        missing = AppToolService(
            platform_name="Linux",
            which=lambda _name: None,
        )
        self.assertEqual(missing.open_file(str(self.file)).code, "LAUNCH_FAILED")

        failing = AppToolService(
            platform_name="Windows",
            start_file=MagicMock(side_effect=OSError("blocked")),
        )
        result = failing.open_file(str(self.file))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "LAUNCH_FAILED")
        self.assertIn("blocked", result.message)

    def test_unsupported_platform_is_explicit(self):
        service = AppToolService(platform_name="Darwin")

        result = service.open_file(str(self.file))

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "UNSUPPORTED_PLATFORM")
        self.assertIn("Darwin", result.message)


if __name__ == "__main__":
    unittest.main()
