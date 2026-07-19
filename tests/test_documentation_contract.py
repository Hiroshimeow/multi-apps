import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SAMPLE = ROOT / "setting.yaml.sample"


class PublicDocumentationContractTests(unittest.TestCase):
    def test_readme_documents_app_tools_inline_logs_and_preferences(self):
        text = README.read_text(encoding="utf-8")

        for required in (
            "## App tools và thao tác trên tên app",
            "## Inline live logs",
            "[alpha,beta,!drop-me]",
            "Live paused while scrolled",
            ".runtime/ui-log-preferences.json",
            "10–5000",
            "100 dòng",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_sample_explicitly_declares_tools_for_every_app(self):
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
            "tools",
        }

        self.assertEqual(len(apps), 11)
        for app in apps:
            with self.subTest(app=app["id"]):
                self.assertEqual(required_fields - set(app), set())
                self.assertIsInstance(app["tools"], list)

        configured = [tool for app in apps for tool in app["tools"]]
        self.assertEqual(
            configured,
            [
                {
                    "id": "open-launcher-readme",
                    "type": "open_file",
                    "label": "Open launcher README",
                    "path": "README.md",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
