import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from lib.core import AppController
from lib.runners.command_runner import CommandRunner
from multi import ArgsEditDialog, ArgsEditModel, AppControlWidget, AppManager


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

        AppControlWidget.on_start(widget)

        manager.launch.assert_called_once_with(manual=True, parent=widget)
        widget.update_ui.assert_called_once_with()

    def test_manual_gui_accept_passes_one_run_override_non_blocking(self):
        manager, controller, _app = self._manager()
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.args_override.return_value = ['--edited "two words" && echo done']

        with patch("multi.ArgsEditDialog", return_value=dialog) as dialog_class:
            success, message = manager.launch(manual=True)

        self.assertTrue(success, message)
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
                preview = controller.preview_app_command(
                    "Args Runtime",
                    args_override=[override],
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
