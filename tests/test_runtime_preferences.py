import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.runtime.preferences import RuntimePreferences


class RuntimePreferencesTests(unittest.TestCase):
    def test_restart_and_exit_choices_are_stored_separately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".runtime" / "preferences.json"
            preferences = RuntimePreferences(path)

            preferences.set("demo", "restart", "keep")
            preferences.set("demo", "exit", "close")
            reloaded = RuntimePreferences(path)

            self.assertEqual(reloaded.get("demo", "restart"), "keep")
            self.assertEqual(reloaded.get("demo", "exit"), "close")
            self.assertEqual(reloaded.get("missing", "restart"), "ask")

    def test_clear_can_restore_defaults_for_selected_apps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "preferences.json"
            preferences = RuntimePreferences(path)
            preferences.set("first", "restart", "keep")
            preferences.set("second", "restart", "close")

            preferences.clear(["first"])

            self.assertEqual(preferences.get("first", "restart"), "ask")
            self.assertEqual(preferences.get("second", "restart"), "close")

    def test_corrupt_preferences_are_renamed_and_defaults_are_used(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "preferences.json"
            path.write_text("{broken", encoding="utf-8")

            with patch("lib.runtime.preferences.print_warning") as warning:
                preferences = RuntimePreferences(path)

            self.assertEqual(preferences.as_dict(), {})
            self.assertFalse(path.exists())
            self.assertEqual(len(list(path.parent.glob("preferences.json.corrupt-*"))), 1)
            warning.assert_called_once()

    def test_invalid_choice_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            preferences = RuntimePreferences(Path(temp_dir) / "preferences.json")

            with self.assertRaises(ValueError):
                preferences.set("demo", "restart", "later")
            with self.assertRaises(ValueError):
                preferences.get("demo", "shutdown")


if __name__ == "__main__":
    unittest.main()
