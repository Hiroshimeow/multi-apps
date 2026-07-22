import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SAMPLE = ROOT / "setting.yaml.sample"


class PublicDocumentationContractTests(unittest.TestCase):
    def test_readme_documents_independent_popup_and_preferences(self):
        text = README.read_text(encoding="utf-8")
        for required in (
            "Open Config",
            "nhấp trái tên app",
            "popup độc lập",
            "không thay đổi geometry của tray panel",
            "latest-wins",
            "## Inline live logs",
            "[alpha,beta,!drop-me]",
            "Live paused while scrolled",
            ".runtime/ui-log-preferences.json",
            "10–5000",
            "100 dòng",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

        for removed in (
            "Open terminal here",
            "## App tools",
            "`tools`",
            "Menu mở rộng lên trên",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, text)

    def test_sample_declares_only_supported_app_fields(self):
        payload = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
        apps = payload["apps"]
        required_fields = {
            "id",
            "name",
            "path",
            "command",
            "args",
            "enabled",
            "auto_start",
            "multi_run",
            "args_edit",
            "close_timeout",
            "os",
        }
        self.assertEqual(len(apps), 11)
        for app in apps:
            with self.subTest(app=app["id"]):
                self.assertEqual(required_fields - set(app), set())
                self.assertNotIn("tools", app)


if __name__ == "__main__":
    unittest.main()
