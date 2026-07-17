import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.ui.log_preferences import (
    LogPanelPreference,
    LogPanelPreferenceStore,
    default_log_preferences_path,
)


class LogPanelPreferenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.path = self.root / ".runtime" / "ui-log-preferences.json"
        self.store = LogPanelPreferenceStore(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_file_returns_defaults(self):
        self.assertEqual(self.store.load("demo"), LogPanelPreference())
        self.assertFalse(self.path.exists())

    def test_round_trip_all_fields(self):
        preference = LogPanelPreference(5000, "[alpha,!drop-me]", "stderr")
        self.assertTrue(self.store.save("demo", preference))
        self.assertEqual(self.store.load("demo"), preference)

    def test_app_ids_have_independent_values(self):
        first = LogPanelPreference(5000, "alpha", "stderr")
        second = LogPanelPreference(10, "", "stdout")
        self.store.save("first", first)
        self.store.save("second", second)
        self.assertEqual(self.store.load("first"), first)
        self.assertEqual(self.store.load("second"), second)

    def test_malformed_json_returns_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.store.load("demo"), LogPanelPreference())

    def test_wrong_version_returns_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps({"version": 2, "apps": {"demo": {"line_count": 5000}}}),
            encoding="utf-8",
        )
        self.assertEqual(self.store.load("demo"), LogPanelPreference())

    def test_partially_invalid_fields_are_normalized_independently(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "apps": {
                        "demo": {
                            "line_count": "5000",
                            "filter_expression": "alpha",
                            "stream": "stderr",
                        },
                        "other": {
                            "line_count": 10,
                            "filter_expression": 12,
                            "stream": "invalid",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            self.store.load("demo"),
            LogPanelPreference(100, "alpha", "stderr"),
        )
        self.assertEqual(
            self.store.load("other"),
            LogPanelPreference(10, "", "stdout"),
        )

    def test_unchanged_save_does_not_replace_file(self):
        preference = LogPanelPreference(5000, "alpha", "stderr")
        self.assertTrue(self.store.save("demo", preference))
        before = self.path.read_bytes()
        before_mtime = self.path.stat().st_mtime_ns
        with patch("lib.ui.log_preferences.os.replace") as replace:
            self.assertFalse(self.store.save("demo", preference))
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, before_mtime)

    def test_failed_replace_preserves_previous_valid_file(self):
        original = LogPanelPreference(100, "before", "stdout")
        self.assertTrue(self.store.save("demo", original))
        before = self.path.read_bytes()
        with patch(
            "lib.ui.log_preferences.os.replace",
            side_effect=OSError("replace failed"),
        ):
            self.assertFalse(
                self.store.save("demo", LogPanelPreference(5000, "after", "stderr"))
            )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.load("demo"), original)
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())

    def test_hostile_unicode_app_id_is_only_a_json_key(self):
        app_id = "../Ứng dụng/日本語\\..\\escape"
        preference = LogPanelPreference(10, "x", "stderr")
        self.assertTrue(self.store.save(app_id, preference))
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn(app_id, payload["apps"])
        self.assertEqual(self.store.load(app_id), preference)
        self.assertEqual(
            sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*")),
            [".runtime", ".runtime/ui-log-preferences.json"],
        )

    def test_default_path_uses_config_directory_not_process_cwd(self):
        config = self.root / "nested" / "active.yaml"
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        with patch.object(Path, "cwd", return_value=elsewhere), patch(
            "lib.ui.log_preferences.os.getcwd", return_value=str(elsewhere)
        ):
            result = default_log_preferences_path(config)
        self.assertEqual(
            result,
            config.resolve(strict=False).parent / ".runtime" / "ui-log-preferences.json",
        )

    def test_uses_plain_json_file_not_registry_or_qsettings(self):
        source = Path(__file__).parents[1] / "lib" / "ui" / "log_preferences.py"
        text = source.read_text(encoding="utf-8").lower()
        self.assertNotIn("winreg", text)
        self.assertNotIn("qsettings", text)
        self.assertNotIn("registry", text)


if __name__ == "__main__":
    unittest.main()
