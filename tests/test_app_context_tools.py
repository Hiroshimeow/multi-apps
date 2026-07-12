import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from lib.config import ConfigManager
from lib.ui.app_tools import (
    AppToolAction,
    AppToolActionResult,
    AppToolService,
    SystemTerminalAdapter,
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


class AppToolServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workdir = self.root / "workspace with spaces & paren(test) — 日本語"
        self.workdir.mkdir()
        self.file_path = self.workdir / "config.yaml"
        self.file_path.write_text("value: 1\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def resolver(mapping):
        return lambda name: mapping.get(name)

    def test_folder_and_file_status_cover_existing_missing_and_wrong_kind_targets(self):
        service = AppToolService(platform_name="Windows")

        folder_ready = service.folder_status(str(self.workdir))
        self.assertEqual(
            folder_ready,
            AppToolActionResult(
                True,
                "READY",
                f"Open folder: {self.workdir.resolve()}",
                target=str(self.workdir.resolve()),
            ),
        )
        self.assertEqual(service.folder_status(str(self.file_path)).code, "TARGET_NOT_DIRECTORY")
        self.assertEqual(service.folder_status(str(self.root / "missing")).code, "TARGET_MISSING")

        action = AppToolAction("config", "open_file", "Open config.yaml", str(self.file_path))
        file_ready = service.file_status(action)
        self.assertEqual(
            file_ready,
            AppToolActionResult(
                True,
                "READY",
                f"Open file: {self.file_path.resolve()}",
                target=str(self.file_path.resolve()),
            ),
        )
        self.assertEqual(
            service.file_status(AppToolAction("dir", "open_file", "Dir", str(self.workdir))).code,
            "TARGET_NOT_FILE",
        )
        self.assertEqual(
            service.file_status(
                AppToolAction("missing", "open_file", "Missing", str(self.root / "missing.yaml"))
            ).code,
            "TARGET_MISSING",
        )

    def test_windows_folder_and_file_dispatch_use_exact_startfile_target(self):
        start_file = MagicMock()
        service = AppToolService(platform_name="Windows", start_file=start_file)

        folder_result = service.open_folder(str(self.workdir))
        file_result = service.open_file(
            AppToolAction("config", "open_file", "Open config", str(self.file_path))
        )

        self.assertEqual(folder_result.code, "OPENED")
        self.assertEqual(file_result.code, "OPENED")
        self.assertEqual(
            start_file.call_args_list,
            [
                unittest.mock.call(str(self.workdir.resolve())),
                unittest.mock.call(str(self.file_path.resolve())),
            ],
        )

    def test_linux_opener_uses_exact_argument_list_and_missing_xdg_open_mapping(self):
        launcher = MagicMock()
        service = AppToolService(
            platform_name="Linux",
            which=self.resolver({"xdg-open": "/usr/bin/xdg-open"}),
            process_launcher=launcher,
        )
        result = service.open_file(
            AppToolAction("config", "open_file", "Open config", str(self.file_path))
        )
        self.assertEqual(result.code, "OPENED")
        self.assertEqual(result.argv, ("/usr/bin/xdg-open", str(self.file_path.resolve())))
        args, kwargs = launcher.call_args
        self.assertEqual(args[0], ["/usr/bin/xdg-open", str(self.file_path.resolve())])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["start_new_session"])
        self.assertNotIn("shell", kwargs)

        missing = AppToolService(platform_name="Linux", which=lambda _name: None)
        target = str(self.file_path.resolve())
        self.assertEqual(
            missing.open_file(AppToolAction("config", "open_file", "Open config", target)),
            AppToolActionResult(
                False,
                "LAUNCH_FAILED",
                "xdg-open is unavailable.",
                target=target,
                argv=(),
            ),
        )

    def test_unsupported_platform_and_unexpected_tool_type_have_exact_mappings(self):
        target = str(self.file_path.resolve())
        unsupported = AppToolService(platform_name="Darwin")
        self.assertEqual(
            unsupported.open_folder(str(self.workdir)),
            AppToolActionResult(
                False,
                "UNSUPPORTED_PLATFORM",
                "App tools are unsupported on platform: Darwin.",
                target=str(self.workdir.resolve()),
                argv=(),
            ),
        )
        action = AppToolAction("bad", "command", "Bad", target)
        self.assertEqual(
            AppToolService(platform_name="Windows").open_file(action),
            AppToolActionResult(
                False,
                "UNSUPPORTED_TOOL_TYPE",
                "Unsupported app tool type: command.",
                target=target,
                argv=(),
            ),
        )

    def test_launch_exceptions_return_structured_failure(self):
        def fail(*_args, **_kwargs):
            raise OSError("dispatch failed")

        folder = AppToolService(platform_name="Windows", start_file=fail).open_folder(
            str(self.workdir)
        )
        self.assertFalse(folder.ok)
        self.assertEqual(folder.code, "LAUNCH_FAILED")
        self.assertIn("dispatch failed", folder.message)

        adapter = SystemTerminalAdapter(
            platform_name="Windows",
            which=self.resolver({"wt.exe": "C:/Windows/wt.exe"}),
            process_launcher=fail,
        )
        terminal = adapter.launch(str(self.workdir))
        self.assertFalse(terminal.ok)
        self.assertEqual(terminal.code, "LAUNCH_FAILED")
        self.assertEqual(
            terminal.argv,
            ("C:/Windows/wt.exe", "-d", str(self.workdir.resolve())),
        )

    def test_windows_terminal_discovery_exact_argv_and_cwd(self):
        cases = [
            (
                {"wt.exe": "C:/Windows/wt.exe", "powershell.exe": "C:/Windows/powershell.exe"},
                TerminalCommand(
                    ("C:/Windows/wt.exe", "-d", str(self.workdir.resolve())),
                    cwd=None,
                ),
            ),
            (
                {"powershell.exe": "C:/Windows/powershell.exe", "cmd.exe": "C:/Windows/cmd.exe"},
                TerminalCommand(
                    ("C:/Windows/powershell.exe", "-NoProfile", "-NoExit"),
                    cwd=str(self.workdir.resolve()),
                ),
            ),
            (
                {"cmd.exe": "C:/Windows/cmd.exe"},
                TerminalCommand(
                    ("C:/Windows/cmd.exe", "/D", "/K"),
                    cwd=str(self.workdir.resolve()),
                ),
            ),
        ]
        for mapping, expected in cases:
            with self.subTest(mapping=mapping):
                adapter = SystemTerminalAdapter(
                    platform_name="Windows",
                    which=self.resolver(mapping),
                )
                self.assertEqual(adapter.discover(str(self.workdir)), expected)

    def test_linux_terminal_environment_generic_and_candidates(self):
        workdir = str(self.workdir.resolve())
        env_adapter = SystemTerminalAdapter(
            platform_name="Linux",
            environ={"TERMINAL": "kitty --single-instance"},
            which=self.resolver({"kitty": "/usr/bin/kitty"}),
        )
        self.assertEqual(
            env_adapter.discover(workdir),
            TerminalCommand(("/usr/bin/kitty", "--single-instance"), cwd=workdir),
        )

        generic = SystemTerminalAdapter(
            platform_name="Linux",
            environ={},
            which=self.resolver({"x-terminal-emulator": "/usr/bin/x-terminal-emulator"}),
        )
        command = generic.discover(workdir)
        self.assertEqual(command, TerminalCommand(("/usr/bin/x-terminal-emulator",), cwd=workdir))
        self.assertNotIn("--working-directory", command.argv)

        generic_launcher = MagicMock()
        generic_launch = SystemTerminalAdapter(
            platform_name="Linux",
            environ={},
            which=self.resolver(
                {"x-terminal-emulator": "/usr/bin/x-terminal-emulator"}
            ),
            process_launcher=generic_launcher,
        )
        result = generic_launch.launch(workdir)
        self.assertEqual(result.code, "TERMINAL_OPENED")
        args, kwargs = generic_launcher.call_args
        self.assertEqual(args[0], ["/usr/bin/x-terminal-emulator"])
        self.assertEqual(kwargs["cwd"], workdir)
        self.assertNotIn("--working-directory", args[0])
        self.assertNotIn("shell", kwargs)

        candidate_cases = [
            ("gnome-terminal", ("/bin/gnome-terminal", "--working-directory", workdir), None),
            ("konsole", ("/bin/konsole", "--workdir", workdir), None),
            ("xfce4-terminal", ("/bin/xfce4-terminal", "--working-directory", workdir), None),
            ("kitty", ("/bin/kitty", "--directory", workdir), None),
            ("alacritty", ("/bin/alacritty", "--working-directory", workdir), None),
            ("wezterm", ("/bin/wezterm", "start", "--cwd", workdir), None),
            ("xterm", ("/bin/xterm",), workdir),
        ]
        for name, argv, cwd in candidate_cases:
            with self.subTest(name=name):
                adapter = SystemTerminalAdapter(
                    platform_name="Linux",
                    environ={},
                    which=self.resolver({name: f"/bin/{name}"}),
                )
                self.assertEqual(adapter.discover(workdir), TerminalCommand(argv, cwd=cwd))

    def test_invalid_terminal_environment_falls_back_and_none_is_unavailable(self):
        workdir = str(self.workdir.resolve())
        invalid = SystemTerminalAdapter(
            platform_name="Linux",
            environ={"TERMINAL": "'unterminated"},
            which=self.resolver({"xterm": "/usr/bin/xterm"}),
        )
        self.assertEqual(
            invalid.discover(workdir),
            TerminalCommand(("/usr/bin/xterm",), cwd=workdir),
        )

        none = SystemTerminalAdapter(platform_name="Linux", environ={}, which=lambda _name: None)
        self.assertIsNone(none.discover(workdir))
        self.assertEqual(
            none.launch(workdir),
            AppToolActionResult(
                False,
                "TERMINAL_UNAVAILABLE",
                "No supported terminal was found for this system.",
                target=workdir,
                argv=(),
            ),
        )

    def test_terminal_launch_preserves_literal_path_and_global_cwd(self):
        before = os.getcwd()
        launcher = MagicMock()
        adapter = SystemTerminalAdapter(
            platform_name="Windows",
            which=self.resolver({"powershell.exe": "C:/Windows/powershell.exe"}),
            process_launcher=launcher,
        )

        result = adapter.launch(str(self.workdir))

        self.assertEqual(os.getcwd(), before)
        self.assertEqual(result.code, "TERMINAL_OPENED")
        self.assertEqual(result.target, str(self.workdir.resolve()))
        self.assertEqual(
            result.argv,
            ("C:/Windows/powershell.exe", "-NoProfile", "-NoExit"),
        )
        args, kwargs = launcher.call_args
        self.assertEqual(args[0], list(result.argv))
        self.assertEqual(kwargs["cwd"], str(self.workdir.resolve()))
        self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NEW_CONSOLE)
        self.assertNotIn("stdin", kwargs)
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)
        self.assertNotIn("shell", kwargs)
        self.assertNotIn(str(self.workdir.resolve()), result.argv)


if __name__ == "__main__":
    unittest.main()
