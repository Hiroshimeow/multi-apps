import os
import tempfile
import unittest
from pathlib import Path

import yaml

from lib.config import ConfigManager
from lib.ui.app_tools import (
    AppToolAction,
    AppToolActionResult,
    TerminalCommand,
)
from lib.ui.log_filter import HighlightRange, LogFilterSpec
from lib.ui.log_reader import LogSnapshot, LogSnapshotState


class AppContextToolConfigTests(unittest.TestCase):
    def _load(self, root: Path, tools_marker=...):
        workdir = root / "workdir"
        workdir.mkdir(exist_ok=True)
        app = {
            "id": "demo-app",
            "name": "Demo App",
            "command": "python demo.py",
            "path": str(workdir),
        }
        if tools_marker is not ...:
            app["tools"] = tools_marker
        config_path = root / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"apps": [app]}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        manager = ConfigManager(config_path)
        return manager, manager.get_apps()[0], workdir

    def _warning(self, manager: ConfigManager, code: str):
        matches = [warning for warning in manager.warnings if warning.code == code]
        self.assertEqual(len(matches), 1, manager.warnings)
        return matches[0]

    def test_missing_and_null_tools_normalize_to_empty_without_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_manager, missing_app, _ = self._load(root)
            self.assertEqual(missing_app["tools"], [])
            self.assertEqual(missing_manager.warnings, [])

        with tempfile.TemporaryDirectory() as temp_dir:
            null_manager, null_app, _ = self._load(Path(temp_dir), None)
            self.assertEqual(null_app["tools"], [])
            self.assertEqual(null_manager.warnings, [])

    def test_invalid_container_warns_with_app_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, app, _ = self._load(Path(temp_dir), {"type": "open_file"})
            self.assertEqual(app["tools"], [])
            warning = self._warning(manager, "TOOLS_INVALID_CONTAINER")
            self.assertEqual(warning.app_id, "demo-app")
            self.assertEqual(warning.app_name, "Demo App")
            self.assertIsNone(warning.tool_index)

    def test_valid_open_file_normalizes_relative_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, app, workdir = self._load(
                Path(temp_dir),
                [
                    {
                        "id": " app-config ",
                        "type": " OPEN_FILE ",
                        "label": " Open config.yaml ",
                        "path": "./nested/../config.yaml",
                    }
                ],
            )
            self.assertEqual(manager.warnings, [])
            self.assertEqual(
                app["tools"],
                [
                    {
                        "id": "app-config",
                        "type": "open_file",
                        "label": "Open config.yaml",
                        "path": str((workdir / "config.yaml").resolve(strict=False)),
                    }
                ],
            )

    def test_absolute_tool_path_remains_absolute_and_display_casing_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "MixedCase" / "Config.yaml"
            manager, app, _ = self._load(
                root,
                [
                    {
                        "id": "absolute",
                        "type": "open_file",
                        "label": "Absolute",
                        "path": str(target),
                    }
                ],
            )
            self.assertEqual(manager.warnings, [])
            self.assertEqual(app["tools"][0]["path"], str(target.resolve(strict=False)))
            if os.name == "nt":
                self.assertEqual(
                    os.path.normcase(app["tools"][0]["path"]),
                    os.path.normcase(str(target.resolve(strict=False))),
                )

    def test_absent_null_empty_and_whitespace_ids_derive_without_warning(self):
        raw_tools = [
            {"type": "open_file", "label": "First Config", "path": "a.yaml"},
            {"id": None, "type": "open_file", "label": "Second Config", "path": "b.yaml"},
            {"id": "", "type": "open_file", "label": "Third Config", "path": "c.yaml"},
            {"id": "   ", "type": "open_file", "label": "Fourth Config", "path": "d.yaml"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, app, _ = self._load(Path(temp_dir), raw_tools)
            self.assertEqual(manager.warnings, [])
            self.assertEqual(
                [tool["id"] for tool in app["tools"]],
                [
                    "open_file-first-config",
                    "open_file-second-config",
                    "open_file-third-config",
                    "open_file-fourth-config",
                ],
            )

    def test_non_empty_string_id_is_trimmed_and_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager, app, _ = self._load(
                Path(temp_dir),
                [{"id": " Stable.ID_1 ", "type": "open_file", "label": "Config", "path": "x"}],
            )
            self.assertEqual(manager.warnings, [])
            self.assertEqual(app["tools"][0]["id"], "Stable.ID_1")

    def test_non_string_ids_are_invalid_and_do_not_remove_app(self):
        invalid_values = [123, True, ["x"], {"x": 1}]
        for invalid in invalid_values:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temp_dir:
                manager, app, _ = self._load(
                    Path(temp_dir),
                    [{"id": invalid, "type": "open_file", "label": "Config", "path": "x"}],
                )
                self.assertEqual(app["id"], "demo-app")
                self.assertEqual(app["tools"], [])
                warning = self._warning(manager, "TOOL_INVALID_ID")
                self.assertEqual(warning.tool_index, 0)
                self.assertIn(type(invalid).__name__, warning.detail)

    def test_duplicate_detection_runs_after_derivation_and_normalization(self):
        cases = [
            [
                {"type": "open_file", "label": "Same Label", "path": "a"},
                {"type": "open_file", "label": "Same Label", "path": "b"},
            ],
            [
                {"id": "open_file-same-label", "type": "open_file", "label": "First", "path": "a"},
                {"type": "open_file", "label": "Same Label", "path": "b"},
            ],
        ]
        for raw_tools in cases:
            with self.subTest(raw_tools=raw_tools), tempfile.TemporaryDirectory() as temp_dir:
                manager, app, _ = self._load(Path(temp_dir), raw_tools)
                self.assertEqual(len(app["tools"]), 1)
                warning = self._warning(manager, "TOOL_DUPLICATE_ID")
                self.assertEqual(warning.tool_index, 1)
                self.assertEqual(warning.detail, "open_file-same-label")

    def test_each_malformed_tool_case_has_stable_code_and_context(self):
        cases = [
            ("not-a-map", "TOOL_INVALID_ENTRY", None),
            ({"label": "Config", "path": "x"}, "TOOL_MISSING_TYPE", None),
            ({"type": "command", "label": "Config", "path": "x"}, "TOOL_UNKNOWN_TYPE", "command"),
            ({"type": "open_file", "label": " ", "path": "x"}, "TOOL_EMPTY_LABEL", None),
            ({"type": "open_file", "label": "Config", "path": " "}, "TOOL_EMPTY_PATH", None),
        ]
        for raw_tool, code, detail in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp_dir:
                manager, app, _ = self._load(Path(temp_dir), [raw_tool])
                self.assertEqual(app["tools"], [])
                warning = self._warning(manager, code)
                self.assertEqual(warning.app_id, "demo-app")
                self.assertEqual(warning.app_name, "Demo App")
                self.assertEqual(warning.tool_index, 0)
                self.assertEqual(warning.detail, detail)

    def test_warnings_reset_on_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager, _app, _ = self._load(root, ["invalid"])
            self.assertEqual([warning.code for warning in manager.warnings], ["TOOL_INVALID_ENTRY"])
            config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
            config["apps"][0]["tools"] = []
            (root / "config.yaml").write_text(
                yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
            )
            manager.load()
            self.assertEqual(manager.warnings, [])
            self.assertEqual(manager.get_apps()[0]["tools"], [])


class PhaseOneContractModelTests(unittest.TestCase):
    def test_contract_models_are_data_only(self):
        action = AppToolAction("config", "open_file", "Open config", "C:/x")
        result = AppToolActionResult(True, "OK", "Opened", target="C:/x")
        terminal = TerminalCommand(("wt.exe", "-d", "C:/work"), cwd=None)
        filter_spec = LogFilterSpec(("error",), ("ignored",), ())
        highlight = HighlightRange(2, 5, 0)
        snapshot = LogSnapshot("C:/x.log", LogSnapshotState.READY, ("line",), 4)

        self.assertEqual(action.type, "open_file")
        self.assertTrue(result.ok)
        self.assertEqual(terminal.argv[0], "wt.exe")
        self.assertEqual(filter_spec.exclusion_terms, ("ignored",))
        self.assertEqual(highlight.length, 5)
        self.assertEqual(snapshot.state, LogSnapshotState.READY)


if __name__ == "__main__":
    unittest.main()
