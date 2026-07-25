import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from lib.core import AppController
from lib.runners.command_runner import CommandRunner
from multi import (
    ArgsEditDialog,
    ArgsEditModel,
    AppControlWidget,
    AppManager,
    SystemTrayApp,
)


class ArgsEditModelTests(unittest.TestCase):
    def test_prefill_preview_and_override_preserve_shell_text_verbatim(self):
        model = ArgsEditModel(
            {
                "command": "python tool.py",
                "args": [
                    '--name "Jane Doe"',
                    "&& echo '$HOME' | sed 's/x/y/'",
                ],
            }
        )

        self.assertEqual(
            model.args_text,
            '--name "Jane Doe" && echo \'$HOME\' | sed \'s/x/y/\'',
        )
        self.assertEqual(
            model.final_command(),
            "python tool.py " + model.args_text,
        )

        edited = '  --path "C:/Program Files/App" && echo "a|b"  '
        model.set_args_text(edited)

        self.assertEqual(
            model.args_override(),
            ['--path "C:/Program Files/App" && echo "a|b"'],
        )
        self.assertEqual(
            model.final_command(),
            'python tool.py --path "C:/Program Files/App" && echo "a|b"',
        )

    def test_command_runner_preview_uses_same_normalization_as_runtime(self):
        args = ['  --one "two words" && echo done  ']
        self.assertEqual(
            CommandRunner.build_command_text("  python app.py  ", args),
            'python app.py --one "two words" && echo done',
        )
        self.assertEqual(
            CommandRunner.normalize_args(args),
            ['--one "two words" && echo done'],
        )
        self.assertEqual(
            CommandRunner.normalize_args('--raw "one value" && echo ok'),
            ['--raw "one value" && echo ok'],
        )


class ArgsEditDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _config(self):
        return {
            "name": "Demo",
            "command": "python demo.py",
            "args": ['--yaml "original value"'],
        }

    def test_dialog_prefills_yaml_args_updates_exact_preview_and_enter_accepts(self):
        dialog = ArgsEditDialog(self._config())
        self.assertEqual(dialog.args_input.text(), '--yaml "original value"')
        self.assertEqual(
            dialog.command_preview.text(),
            'python demo.py --yaml "original value"',
        )

        edited = '--edited "two words" && echo "x|y"'
        dialog.args_input.setText(edited)
        self.assertEqual(
            dialog.command_preview.text(),
            "python demo.py " + edited,
        )

        dialog.show()
        QTest.keyClick(dialog.args_input, Qt.Key.Key_Return)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.args_override(), [edited])
        dialog.close()

    def test_cancel_button_rejects(self):
        dialog = ArgsEditDialog(self._config())
        cancel = dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.close()


class ArgsEditCallerTests(unittest.TestCase):
    def _manager(self, *, status="STOPPED", multi_run=False, args_edit=True):
        controller = MagicMock()
        app = {
            "id": "demo",
            "name": "Demo",
            "path": ".",
            "command": "python demo.py",
            "args": ['--yaml "original"'],
            "multi_run": multi_run,
            "args_edit": args_edit,
        }
        controller.config_manager.get_app.return_value = app
        controller.get_app_status.return_value = {
            "status": status,
            "instances": 1 if status != "STOPPED" else 0,
        }
        controller.start_app.return_value = True
        return AppManager(controller, "Demo"), controller, app

    def test_start_button_marks_request_as_manual(self):
        manager = MagicMock()
        manager.launch.return_value = (True, "Started")
        widget = MagicMock()
        widget.manager = manager
        widget._status_info = {"status": "STOPPED", "instances": 0}

        AppControlWidget.on_start(widget)

        manager.launch.assert_called_once_with(
            manual=True,
            parent=widget,
            status_info=widget._status_info,
        )
        widget.refresh_requested.emit.assert_called_once_with(str(manager.app_id))

    def test_manual_gui_accept_passes_one_run_override_non_blocking(self):
        manager, controller, _app = self._manager()
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.args_override.return_value = ['--edited "two words" && echo done']

        with patch("multi.ArgsEditDialog", return_value=dialog) as dialog_class:
            success, message = manager.launch(manual=True)

        self.assertTrue(success, message)
        self.assertTrue(manager.startup_auto_start_suppressed)
        dialog_class.assert_called_once_with(manager.app_config, None)
        controller.start_app.assert_called_once_with(
            "Demo",
            wait_for_ready=False,
            args_override=['--edited "two words" && echo done'],
        )

    def test_auto_start_path_never_opens_dialog_and_uses_yaml_args(self):
        manager, controller, _app = self._manager()

        with patch("multi.ArgsEditDialog") as dialog_class:
            success, message = manager.launch()

        self.assertTrue(success, message)
        self.assertFalse(manager.startup_auto_start_suppressed)
        dialog_class.assert_not_called()
        controller.start_app.assert_called_once_with(
            "Demo",
            wait_for_ready=False,
        )

    def test_manual_start_with_args_edit_disabled_never_opens_dialog(self):
        manager, controller, _app = self._manager(args_edit=False)

        with patch("multi.ArgsEditDialog") as dialog_class:
            success, message = manager.launch(manual=True)

        self.assertTrue(success, message)
        dialog_class.assert_not_called()
        controller.start_app.assert_called_once_with(
            "Demo",
            wait_for_ready=False,
        )

    def test_non_multi_active_or_orphaned_run_blocks_before_dialog(self):
        for status in ("STARTING", "RUNNING", "STOPPING", "ORPHANED"):
            with self.subTest(status=status):
                manager, controller, _app = self._manager(status=status)
                with patch("multi.ArgsEditDialog") as dialog_class:
                    success, message = manager.launch(manual=True)
                self.assertFalse(success)
                self.assertIn("blocked", message)
                dialog_class.assert_not_called()
                controller.start_app.assert_not_called()

    def test_multi_run_active_manual_start_still_allows_editor(self):
        manager, controller, _app = self._manager(
            status="RUNNING",
            multi_run=True,
        )
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.args_override.return_value = ["--second"]

        with patch("multi.ArgsEditDialog", return_value=dialog):
            success, message = manager.launch(manual=True)

        self.assertTrue(success, message)
        controller.start_app.assert_called_once_with(
            "Demo",
            wait_for_ready=False,
            args_override=["--second"],
        )

    def test_cancel_creates_no_run_record_and_does_not_mutate_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "setting.yaml"
            payload = {
                "global": {"session": "subprocess", "log_dir": "./logs"},
                "apps": [
                    {
                        "id": "cancel-demo",
                        "name": "Cancel Demo",
                        "path": str(root),
                        "command": "echo yaml",
                        "args": ['--yaml "original"'],
                        "args_edit": True,
                        "multi_run": False,
                    }
                ],
            }
            config_path.write_text(
                yaml.safe_dump(payload, sort_keys=False),
                encoding="utf-8",
            )
            original_bytes = config_path.read_bytes()
            controller = AppController(str(config_path))
            manager = AppManager(controller, "Cancel Demo")
            original_args = list(manager.app_config["args"])
            dialog = MagicMock()
            dialog.exec.return_value = QDialog.DialogCode.Rejected

            with patch("multi.ArgsEditDialog", return_value=dialog):
                success, message = manager.launch(manual=True)

            self.assertFalse(success)
            self.assertEqual(message, "Cancelled")
            self.assertTrue(manager.startup_auto_start_suppressed)
            self.assertEqual(controller.session_manager.registry.list_records(), [])
            self.assertEqual(manager.app_config["args"], original_args)
            self.assertEqual(config_path.read_bytes(), original_bytes)

    def test_controller_direct_start_for_cli_tui_uses_yaml_args_without_override(self):
        controller = AppController.__new__(AppController)
        controller.config = {"global": {}}
        controller.config_manager = MagicMock()
        yaml_args = ['--yaml "original" && echo yaml']
        controller.config_manager.get_app.return_value = {
            "id": "direct",
            "name": "Direct",
            "path": ".",
            "command": "python demo.py",
            "args": yaml_args,
        }
        controller.session_manager = MagicMock()
        controller.session_manager.start.return_value = (True, "started")

        success = controller.start_app("Direct", wait_for_ready=True)

        self.assertTrue(success)
        runner = controller.session_manager.start.call_args.args[0]
        self.assertEqual(runner.app_config["args"], yaml_args)
        self.assertEqual(
            runner.build_command(),
            'python demo.py --yaml "original" && echo yaml',
        )


class ArgsEditNestedEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _command(self, sleep_seconds=10):
        parts = [
            sys.executable,
            "-c",
            (
                "import sys,time; "
                "print('|'.join(sys.argv[1:]), flush=True); "
                f"time.sleep({sleep_seconds})"
            ),
        ]
        return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)

    def _controller_and_managers(
        self,
        root,
        *,
        multi_run=False,
        include_other=False,
        command_sleep=10,
    ):
        apps = [
            {
                "id": "race",
                "name": "Race",
                "path": str(root),
                "command": self._command(command_sleep),
                "args": ["--yaml"],
                "enabled": True,
                "auto_start": True,
                "args_edit": True,
                "multi_run": multi_run,
                "close_timeout": 0.5,
            }
        ]
        if include_other:
            apps.append(
                {
                    "id": "other",
                    "name": "Other",
                    "path": str(root),
                    "command": self._command(command_sleep),
                    "args": ["--other-yaml"],
                    "enabled": True,
                    "auto_start": True,
                    "args_edit": False,
                    "multi_run": False,
                    "close_timeout": 0.5,
                }
            )
        config_path = root / "setting.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "global": {"session": "subprocess", "log_dir": "./logs"},
                    "apps": apps,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        suppressed_ids = set()
        controller = AppController(str(config_path))
        managers = [
            AppManager(controller, app["name"], suppressed_ids)
            for app in apps
        ]
        tray = SimpleNamespace(
            controller=controller,
            managers=managers,
            startup_auto_start_suppressed_ids=suppressed_ids,
            config_path=config_path,
            controllers=[controller],
        )
        tray.load_config = lambda: SystemTrayApp.load_config(tray)
        tray.tray_panel = SimpleNamespace(
            isVisible=lambda: False,
            hide=lambda: None,
        )
        tray.rebuild_panel = lambda: None
        tray.show_panel = lambda: None
        return controller, managers, tray

    @staticmethod
    def _track_starts(controller, calls):
        original_start = controller.start_app

        def tracked_start(app_name, **kwargs):
            calls.append((app_name, dict(kwargs)))
            return original_start(app_name, **kwargs)

        controller.start_app = tracked_start

    def _refresh_tray(self, tray, count=1, calls=None):
        for _refresh in range(count):
            with patch(
                "multi.AppController",
                side_effect=lambda _path: AppController(str(tray.config_path)),
            ):
                SystemTrayApp.refresh_all(tray)
            tray.controllers.append(tray.controller)
            if calls is not None:
                self._track_starts(tray.controller, calls)

    def _cleanup(self, tray, app_names):
        controller = tray.controller
        for app_name in app_names:
            for _attempt in range(3):
                if controller.get_app_status(app_name)["status"] == "STOPPED":
                    break
                if controller.stop_app(app_name):
                    break
                time.sleep(0.1)
        for known_controller in tray.controllers:
            client = known_controller.session_manager.client
            deadline = time.monotonic() + 3.0
            while client._keepers and time.monotonic() < deadline:
                client.reap_finished()
                time.sleep(0.02)
            self.assertFalse(client._keepers)

    def _run_nested_case(self, *, accept, multi_run=False, include_other=False):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller, managers, tray = self._controller_and_managers(
                root,
                multi_run=multi_run,
                include_other=include_other,
            )
            selected = managers[0]
            edited = '--edited "two words"'
            app_names = [manager.name for manager in managers]

            class TimedDialog(ArgsEditDialog):
                def __init__(self, app_config, parent=None):
                    super().__init__(app_config, parent)
                    self.args_input.setText(edited)
                    QTimer.singleShot(
                        0,
                        lambda: SystemTrayApp.auto_start_apps(tray),
                    )
                    QTimer.singleShot(50, self.accept if accept else self.reject)

            try:
                before_count = len(controller.session_manager.registry.list_records())
                with patch("multi.ArgsEditDialog", TimedDialog), patch.object(
                    controller,
                    "start_app",
                    wraps=controller.start_app,
                ) as start_app:
                    result = selected.launch(manual=True)
                QApplication.processEvents()
                records = controller.session_manager.registry.list_records()
                calls = list(start_app.call_args_list)
                suppressed = selected.startup_auto_start_suppressed
            finally:
                self._cleanup(tray, app_names)

            return {
                "before_count": before_count,
                "result": result,
                "records": records,
                "calls": calls,
                "suppressed": suppressed,
            }

    def test_pending_auto_start_cancel_creates_no_run_in_nested_event_loop(self):
        outcome = self._run_nested_case(accept=False)

        self.assertEqual(outcome["result"], (False, "Cancelled"))
        self.assertEqual(outcome["before_count"], 0)
        self.assertEqual(outcome["records"], [])
        self.assertEqual(outcome["calls"], [])
        self.assertFalse(outcome["suppressed"])

    def test_pending_auto_start_accept_non_multi_creates_one_edited_run(self):
        outcome = self._run_nested_case(accept=True, multi_run=False)

        self.assertEqual(outcome["result"], (True, "Started"))
        self.assertEqual(len(outcome["records"]), 1)
        self.assertEqual(outcome["records"][0].app_id, "race")
        self.assertEqual(outcome["records"][0].args, ['--edited "two words"'])
        self.assertEqual(len(outcome["calls"]), 1)
        self.assertEqual(outcome["calls"][0].args[0], "Race")
        self.assertEqual(
            outcome["calls"][0].kwargs,
            {
                "wait_for_ready": False,
                "args_override": ['--edited "two words"'],
            },
        )

    def test_pending_auto_start_accept_multi_run_has_no_extra_yaml_run(self):
        outcome = self._run_nested_case(accept=True, multi_run=True)

        race_records = [
            record for record in outcome["records"] if record.app_id == "race"
        ]
        self.assertEqual(outcome["result"], (True, "Started"))
        self.assertEqual(len(race_records), 1)
        self.assertEqual(race_records[0].args, ['--edited "two words"'])
        self.assertEqual(len(outcome["calls"]), 1)
        self.assertIn("args_override", outcome["calls"][0].kwargs)

    def test_pending_editor_does_not_block_other_auto_start_app(self):
        outcome = self._run_nested_case(
            accept=False,
            include_other=True,
        )

        self.assertEqual(outcome["result"], (False, "Cancelled"))
        self.assertEqual(len(outcome["records"]), 1)
        self.assertEqual(outcome["records"][0].app_id, "other")
        self.assertEqual(outcome["records"][0].args, ["--other-yaml"])
        self.assertEqual(len(outcome["calls"]), 1)
        self.assertEqual(outcome["calls"][0].args[0], "Other")
        self.assertEqual(
            outcome["calls"][0].kwargs,
            {"wait_for_ready": False},
        )

    def _wait_stopped(self, controller, app_name, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if controller.get_app_status(app_name)["status"] == "STOPPED":
                return
            time.sleep(0.02)
        self.fail(f"{app_name} did not stop")

    def _run_refresh_case(
        self,
        *,
        accept,
        multi_run=False,
        include_other=False,
        command_sleep=10,
        wait_selected_stopped=False,
        refresh_count=2,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            controller, managers, tray = self._controller_and_managers(
                root,
                multi_run=multi_run,
                include_other=include_other,
                command_sleep=command_sleep,
            )
            selected = managers[0]
            app_names = [manager.name for manager in managers]
            edited = '--edited "two words"'
            calls = []
            self._track_starts(controller, calls)

            class TimedDialog(ArgsEditDialog):
                def __init__(self, app_config, parent=None):
                    super().__init__(app_config, parent)
                    self.args_input.setText(edited)
                    QTimer.singleShot(0, self.accept if accept else self.reject)

            try:
                with patch("multi.ArgsEditDialog", TimedDialog):
                    result = selected.launch(manual=True)
                if wait_selected_stopped:
                    self._wait_stopped(controller, "Race")

                self._refresh_tray(tray, count=refresh_count, calls=calls)
                replacement_selected = next(
                    manager for manager in tray.managers if manager.app_id == "race"
                )
                replacement_suppressed_before = (
                    replacement_selected.startup_auto_start_suppressed
                )
                suppressed_ids_before = set(
                    tray.startup_auto_start_suppressed_ids
                )
                SystemTrayApp.auto_start_apps(tray)
                QApplication.processEvents()
                records = tray.controller.session_manager.registry.list_records()
                suppressed_ids_after = set(
                    tray.startup_auto_start_suppressed_ids
                )
                replacement_suppressed_after = (
                    replacement_selected.startup_auto_start_suppressed
                )
            finally:
                self._cleanup(tray, app_names)

            return {
                "result": result,
                "records": records,
                "calls": calls,
                "suppressed_ids_before": suppressed_ids_before,
                "suppressed_ids_after": suppressed_ids_after,
                "replacement_suppressed_before": replacement_suppressed_before,
                "replacement_suppressed_after": replacement_suppressed_after,
                "refresh_count": refresh_count,
            }

    def test_cancel_refresh_then_pending_auto_start_creates_no_selected_run(self):
        outcome = self._run_refresh_case(accept=False, refresh_count=3)

        self.assertEqual(outcome["result"], (False, "Cancelled"))
        self.assertEqual(outcome["records"], [])
        self.assertEqual(outcome["calls"], [])
        self.assertEqual(outcome["suppressed_ids_before"], {"race"})
        self.assertEqual(outcome["suppressed_ids_after"], set())
        self.assertTrue(outcome["replacement_suppressed_before"])
        self.assertFalse(outcome["replacement_suppressed_after"])

    def test_non_multi_accept_refresh_after_fast_exit_keeps_one_edited_run(self):
        outcome = self._run_refresh_case(
            accept=True,
            command_sleep=0.2,
            wait_selected_stopped=True,
        )

        race_records = [
            record for record in outcome["records"] if record.app_id == "race"
        ]
        self.assertEqual(outcome["result"], (True, "Started"))
        self.assertEqual(len(race_records), 1)
        self.assertEqual(race_records[0].args, ['--edited "two words"'])
        self.assertEqual(
            outcome["calls"],
            [
                (
                    "Race",
                    {
                        "wait_for_ready": False,
                        "args_override": ['--edited "two words"'],
                    },
                )
            ],
        )

    def test_multi_run_accept_refresh_has_no_automatic_yaml_run(self):
        outcome = self._run_refresh_case(
            accept=True,
            multi_run=True,
        )

        race_records = [
            record for record in outcome["records"] if record.app_id == "race"
        ]
        self.assertEqual(outcome["result"], (True, "Started"))
        self.assertEqual(len(race_records), 1)
        self.assertEqual(race_records[0].args, ['--edited "two words"'])
        self.assertEqual(len(outcome["calls"]), 1)
        self.assertIn("args_override", outcome["calls"][0][1])

    def test_refresh_suppression_still_allows_other_auto_start_once(self):
        outcome = self._run_refresh_case(
            accept=False,
            include_other=True,
        )

        self.assertEqual(outcome["result"], (False, "Cancelled"))
        self.assertEqual(
            [(record.app_id, record.args) for record in outcome["records"]],
            [("other", ["--other-yaml"])],
        )
        self.assertEqual(
            outcome["calls"],
            [("Other", {"wait_for_ready": False})],
        )


class ArgsEditRuntimeTests(unittest.TestCase):
    def _command(self):
        parts = [
            sys.executable,
            "-c",
            (
                "import sys,time; "
                "print('|'.join(sys.argv[1:]), flush=True); "
                "time.sleep(1.5)"
            ),
        ]
        return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)

    def _wait_stopped(self, controller, app_name, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if controller.get_app_status(app_name)["status"] == "STOPPED":
                return
            time.sleep(0.02)
        self.fail(f"{app_name} did not stop")

    def _reap_local_keepers(self, controller, timeout=3.0):
        client = controller.session_manager.client
        deadline = time.monotonic() + timeout
        while client._keepers and time.monotonic() < deadline:
            client.reap_finished()
            time.sleep(0.02)
        self.assertFalse(client._keepers)

    def test_each_override_is_persisted_only_for_its_run_and_logs_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "setting.yaml"
            payload = {
                "global": {"session": "subprocess", "log_dir": "./logs"},
                "apps": [
                    {
                        "id": "args-runtime",
                        "name": "Args Runtime",
                        "path": str(root),
                        "command": self._command(),
                        "args": ['--yaml "original value"'],
                        "args_edit": True,
                        "multi_run": False,
                        "close_timeout": 0.5,
                    }
                ],
            }
            config_path.write_text(
                yaml.safe_dump(payload, sort_keys=False),
                encoding="utf-8",
            )
            original_bytes = config_path.read_bytes()
            controller = AppController(str(config_path))
            original_args = list(
                controller.config_manager.get_app("Args Runtime")["args"]
            )
            overrides = [
                '--edited "two words"',
                '--next "another value"',
            ]
            records = []

            for override in overrides:
                existing_ids = {
                    item.run_id
                    for item in controller.session_manager.registry.list_records()
                }
                app_config = controller.config_manager.get_app("Args Runtime")
                preview = CommandRunner.build_command_text(
                    app_config["command"],
                    [override],
                )
                self.assertTrue(
                    controller.start_app(
                        "Args Runtime",
                        wait_for_ready=True,
                        args_override=[override],
                    )
                )
                self._wait_stopped(controller, "Args Runtime")
                self._reap_local_keepers(controller)
                new_records = [
                    item
                    for item in controller.session_manager.registry.list_records()
                    if item.run_id not in existing_ids
                ]
                self.assertEqual(len(new_records), 1)
                record = new_records[0]
                records.append(record)

                self.assertEqual(record.args, [override])
                self.assertEqual(
                    preview,
                    " ".join([record.command, *record.args]),
                )
                self.assertEqual(
                    Path(record.stdout_path).parent,
                    root / "logs" / "args-runtime",
                )
                self.assertEqual(
                    Path(record.stderr_path).parent,
                    root / "logs" / "args-runtime",
                )
                output = Path(record.stdout_path).read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                expected_value = "two words" if "two words" in override else "another value"
                self.assertIn(expected_value, output)

            self.assertNotEqual(records[0].run_id, records[1].run_id)
            self.assertNotEqual(records[0].stdout_path, records[1].stdout_path)
            self.assertNotEqual(records[0].stderr_path, records[1].stderr_path)
            self.assertEqual(records[0].args, [overrides[0]])
            self.assertEqual(records[1].args, [overrides[1]])
            self.assertEqual(
                controller.config_manager.get_app("Args Runtime")["args"],
                original_args,
            )
            self.assertEqual(config_path.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
