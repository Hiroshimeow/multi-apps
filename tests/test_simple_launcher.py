import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lib.config import ConfigManager
from lib.core import AppController
from lib.runners.command_runner import CommandRunner
from lib.runtime.models import RunRecord
from lib.runtime.process_identity import get_process_created_at, process_matches
from lib.runtime.registry import RuntimeRegistry
from lib.session.subprocess_session import SubprocessSessionManager


class SimpleConfigTests(unittest.TestCase):
    def test_path_is_workdir_and_args_are_appended_verbatim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir = root / "app"
            app_dir.mkdir()
            config_file = root / "setting.yaml"
            config_file.write_text(
                """
global:
  log_dir: ./logs
apps:
  - name: Demo
    path: ./app
    command: uv run main.py
    args:
      - --repo E:/python_project
    enabled: true
""".strip(),
                encoding="utf-8",
            )

            app = ConfigManager(config_file).get_app("Demo")
            self.assertEqual(app["path"], str(app_dir.resolve()))
            runner = CommandRunner(app, {})
            self.assertEqual(
                runner.build_command(),
                "uv run main.py --repo E:/python_project",
            )
            self.assertTrue(runner.should_use_shell())

    def test_name_and_command_are_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "setting.yaml"
            config_file.write_text(
                "apps:\n  - name: Missing command\n  - command: echo missing-name\n",
                encoding="utf-8",
            )
            self.assertEqual(ConfigManager(config_file).get_apps(), [])

    def test_os_filter(self):
        manager = ConfigManager.__new__(ConfigManager)
        manager.config = {
            "apps": [
                {"name": "Windows", "enabled": True, "os": "win11"},
                {"name": "Linux", "enabled": True, "os": "linux"},
            ]
        }
        with patch("lib.config.is_windows", return_value=True), patch(
            "lib.config.is_linux", return_value=False
        ):
            self.assertEqual([app["name"] for app in manager.get_apps()], ["Windows"])


class SubprocessSessionTests(unittest.TestCase):
    def test_command_runs_inside_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = (
                subprocess.list2cmdline([sys.executable])
                if os.name == "nt"
                else shlex.quote(sys.executable)
            )
            command = (
                f'{executable} -c "from pathlib import Path; '
                "Path('marker.txt').write_text('ok', encoding='utf-8')\""
            )
            app = {
                "name": "Smoke",
                "path": str(root),
                "command": command,
                "args": [],
                "multi_run": False,
            }
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )
            success, message = session.start(CommandRunner(app, {}))
            self.assertTrue(success, message)

            marker = root / "marker.txt"
            deadline = time.time() + 5
            while time.time() < deadline and not marker.exists():
                time.sleep(0.05)

            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "ok")

            deadline = time.time() + 5
            while time.time() < deadline:
                if session.get_info("Smoke")["status"] == "STOPPED":
                    break
                time.sleep(0.05)
            self.assertEqual(session.get_info("Smoke")["status"], "STOPPED")
            reap_deadline = time.monotonic() + 2.0
            while session.client._keepers and time.monotonic() < reap_deadline:
                session.client.reap_finished()
                time.sleep(0.02)
            self.assertFalse(session.client._keepers)

    def test_repeated_natural_exits_remain_stopped_without_registry_warnings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = (
                subprocess.list2cmdline([sys.executable])
                if os.name == "nt"
                else shlex.quote(sys.executable)
            )
            app = {
                "id": "natural-exit",
                "name": "Natural Exit",
                "path": str(root),
                "command": f'{executable} -c "pass"',
                "args": [],
                "multi_run": False,
            }
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )

            with patch("lib.runtime.registry.print_warning") as warning:
                for _ in range(5):
                    success, message = session.start(CommandRunner(app, {}))
                    self.assertTrue(success, message)
                    deadline = time.monotonic() + 8.0
                    while time.monotonic() < deadline:
                        if session.get_info("natural-exit")["status"] == "STOPPED":
                            break
                        time.sleep(0.02)
                    self.assertEqual(
                        session.get_info("natural-exit")["status"], "STOPPED"
                    )
                    reap_deadline = time.monotonic() + 2.0
                    while session.client._keepers and time.monotonic() < reap_deadline:
                        session.client.reap_finished()
                        time.sleep(0.02)
                    self.assertFalse(session.client._keepers)

            records = session.registry.list_records()
            self.assertEqual(len(records), 5)
            self.assertTrue(all(record.state == "stopped" for record in records))
            warning.assert_not_called()

    def test_start_accepts_starting_without_waiting_and_blocks_duplicate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )
            created_at = get_process_created_at(os.getpid())
            self.assertIsNotNone(created_at)
            app = {
                "id": "accepted-start",
                "name": "Accepted Start",
                "path": str(root),
                "command": "unused",
                "args": [],
                "multi_run": False,
            }

            with patch.object(
                session.client,
                "launch_keeper",
                return_value=(os.getpid(), created_at),
            ), patch.object(
                session.client,
                "wait_until_ready",
                side_effect=AssertionError("GUI-facing Start must not wait"),
            ):
                success, message = session.start(CommandRunner(app, {}))
                duplicate_success, duplicate_message = session.start(
                    CommandRunner(app, {})
                )

            self.assertTrue(success, message)
            self.assertIn("Accepted run", message)
            record = session.registry.list_records()[0]
            self.assertEqual(record.state, "starting")
            self.assertFalse(duplicate_success, duplicate_message)
            self.assertIn("already running", duplicate_message)
            session.registry.delete(record.run_id)

    def test_accepted_start_transitions_asynchronously(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )
            command_parts = [
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ]
            command = (
                subprocess.list2cmdline(command_parts)
                if os.name == "nt"
                else shlex.join(command_parts)
            )
            app = {
                "id": "async-start",
                "name": "Async Start",
                "path": str(root),
                "command": command,
                "args": [],
                "multi_run": False,
                "close_timeout": 0.2,
            }

            try:
                with patch.object(
                    session.client,
                    "wait_until_ready",
                    side_effect=AssertionError("GUI-facing Start must not wait"),
                ):
                    started_at = time.perf_counter()
                    success, message = session.start(CommandRunner(app, {}))
                    elapsed = time.perf_counter() - started_at

                self.assertTrue(success, message)
                self.assertLess(elapsed, 1.0)
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    record = session.registry.list_records()[0]
                    if record.state == "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(record.state, "running")
            finally:
                session.stop("async-start")
                reap_deadline = time.monotonic() + 2.0
                while session.client._keepers and time.monotonic() < reap_deadline:
                    session.client.reap_finished()
                    time.sleep(0.02)
                self.assertFalse(session.client._keepers)

    def test_readiness_timeout_cleans_live_processes_before_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process_args = [sys.executable, "-c", "import time; time.sleep(30)"]
            keeper = subprocess.Popen(
                process_args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            root_process = subprocess.Popen(
                process_args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )

            def created_at(process):
                deadline = time.monotonic() + 2.0
                value = get_process_created_at(process.pid)
                while value is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                    value = get_process_created_at(process.pid)
                self.assertIsNotNone(value)
                return value

            keeper_created_at = created_at(keeper)
            root_created_at = created_at(root_process)
            registry = session.registry

            class TimeoutClient:
                def __init__(self):
                    self.run_id = None

                def launch_keeper(self, run_id):
                    self.run_id = run_id
                    return keeper.pid, keeper_created_at

                def wait_until_ready(self, run_id, timeout=8.0):
                    registry.update(
                        run_id,
                        state="starting",
                        root_pid=root_process.pid,
                        root_created_at=root_created_at,
                    )
                    raise TimeoutError("forced readiness timeout")

                def status(self, run_id, timeout=1.0):
                    record = registry.load(run_id)
                    return {
                        "ok": True,
                        "state": "starting",
                        "run_id": run_id,
                        "app_id": record.app_id,
                        "keeper_pid": keeper.pid,
                        "keeper_created_at": keeper_created_at,
                        "root_pid": root_process.pid,
                        "root_created_at": root_created_at,
                        "tree_alive": True,
                    }

                def stop(self, run_id, timeout=2.0):
                    registry.update(run_id, state="stopping")
                    for process in (root_process, keeper):
                        if process.poll() is None:
                            process.terminate()
                    for process in (root_process, keeper):
                        process.wait(timeout=3.0)
                    return {"ok": True, "message": "Stop requested"}

                def reap_finished(self, run_id=None):
                    return None

            session.client = TimeoutClient()
            app = {
                "id": "timeout-start",
                "name": "Timeout Start",
                "path": str(root),
                "command": "unused",
                "args": [],
                "multi_run": False,
                "close_timeout": 0.1,
            }

            try:
                success, message = session.start(
                    CommandRunner(app, {}), wait_for_ready=True
                )
                self.assertFalse(success, message)
                record = session.registry.list_records()[0]
                self.assertEqual(record.state, "failed")
                self.assertFalse(
                    process_matches(keeper.pid, keeper_created_at),
                    "keeper remained alive behind failed state",
                )
                self.assertFalse(
                    process_matches(root_process.pid, root_created_at),
                    "root remained alive behind failed state",
                )
            finally:
                for process in (root_process, keeper):
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3.0)

    @unittest.skipUnless(os.name == "nt", "Windows process identity behavior")
    def test_process_identity_rejects_exited_windows_process(self):
        creationflags = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        created_at = get_process_created_at(process.pid)
        self.assertIsNotNone(created_at)
        process.wait(timeout=3.0)
        self.assertFalse(process_matches(process.pid, created_at))

    def test_get_info_self_corrects_dead_active_records_without_ipc(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )
            current_created_at = get_process_created_at(os.getpid())
            self.assertIsNotNone(current_created_at)

            dead = RunRecord.create(
                app_id="stale-dead",
                path=str(root),
                command="unused",
                run_id="stale-dead-run",
            )
            dead.keeper_pid = os.getpid()
            dead.keeper_created_at = current_created_at + 1000.0
            dead.state = "running"
            session.registry.save(dead)

            orphaned = RunRecord.create(
                app_id="stale-root",
                path=str(root),
                command="unused",
                run_id="stale-root-run",
            )
            orphaned.keeper_pid = os.getpid()
            orphaned.keeper_created_at = current_created_at + 1000.0
            orphaned.root_pid = os.getpid()
            orphaned.root_created_at = current_created_at
            orphaned.state = "running"
            session.registry.save(orphaned)

            with patch.object(
                session.client,
                "status",
                side_effect=AssertionError("ordinary status must not use IPC"),
            ):
                dead_info = session.get_info("stale-dead")
                orphaned_info = session.get_info("stale-root")

            self.assertEqual(dead_info["status"], "STOPPED")
            self.assertEqual(
                session.registry.load(dead.run_id).state,
                "stopped",
            )
            self.assertEqual(orphaned_info["status"], "ORPHANED")
            self.assertEqual(
                session.registry.load(orphaned.run_id).state,
                "orphaned",
            )
            self.assertTrue(process_matches(os.getpid(), current_created_at))

    def test_starting_reconcile_keeps_verified_state_during_temporary_ipc_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )
            current_created_at = get_process_created_at(os.getpid())
            self.assertIsNotNone(current_created_at)
            record = RunRecord.create(
                app_id="starting-ipc",
                path=str(root),
                command="unused",
                run_id="starting-ipc-run",
            )
            record.keeper_pid = os.getpid()
            record.keeper_created_at = current_created_at
            record.state = "starting"
            session.registry.save(record)

            with patch.object(
                session.client,
                "status",
                side_effect=ConnectionError("listener not ready"),
            ):
                reconciled = session.reconcile("starting-ipc")[0]

            self.assertEqual(reconciled.state, "starting")
            self.assertEqual(
                session.registry.load(record.run_id).state,
                "starting",
            )
            session.registry.delete(record.run_id)

    def test_controller_and_gui_use_explicit_readiness_semantics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = {
                "id": "caller-semantics",
                "name": "Caller Semantics",
                "path": str(root),
                "command": "unused",
                "args": [],
                "multi_run": False,
            }
            session = SubprocessSessionManager(
                {"log_dir": str(root / "logs"), "_config_dir": str(root)}
            )
            controller = AppController.__new__(AppController)
            controller.config = {"global": {}}
            controller.config_manager = MagicMock()
            controller.config_manager.get_app.return_value = app
            controller.session_manager = session

            with patch.object(
                session,
                "start",
                return_value=(True, "accepted"),
            ) as start:
                self.assertTrue(
                    controller.start_app("Caller Semantics", wait_for_ready=False)
                )
            self.assertFalse(start.call_args.kwargs["wait_for_ready"])

            from multi import AppManager

            gui_controller = MagicMock()
            gui_controller.config_manager.get_app.return_value = app
            gui_controller.start_app.return_value = True
            manager = AppManager(gui_controller, app["name"])
            success, _ = manager.launch()

            self.assertTrue(success)
            gui_controller.start_app.assert_called_once_with(
                app["name"],
                wait_for_ready=False,
            )

    @unittest.skipIf(os.name == "nt", "TUI is Linux-only")
    def test_tui_uses_synchronous_readiness(self):
        from lib.tui.menu import InteractiveMenu

        controller = MagicMock()
        menu = InteractiveMenu.__new__(InteractiveMenu)
        menu.controller = controller
        menu.selected_apps = {"Demo"}

        with patch("lib.tui.menu.time.sleep"):
            menu._action_start()

        controller.start_app.assert_called_once_with(
            "Demo",
            wait_for_ready=True,
        )

    def test_cli_invalid_command_reports_synchronous_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "setting.yaml"
            config_path.write_text(
                """
global:
  session: subprocess
  log_dir: ./logs
apps:
  - id: broken-cli
    name: Broken CLI
    path: .
    command: command-that-does-not-exist-multi-run-apps
    args: []
    enabled: true
    multi_run: false
    close_timeout: 0.2
""".strip(),
                encoding="utf-8",
            )
            project_root = Path(__file__).resolve().parents[1]

            completed = subprocess.run(
                [
                    sys.executable,
                    str(project_root / "cli.py"),
                    "start",
                    "Broken CLI",
                    "--config",
                    str(config_path),
                ],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(
                completed.returncode,
                1,
                f"stdout={completed.stdout}\nstderr={completed.stderr}",
            )
            self.assertIn(
                "Failed to start 'Broken CLI'",
                completed.stdout + completed.stderr,
            )
            records = RuntimeRegistry(root / ".runtime").list_records()
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertIn(record.state, {"failed", "stopped"})
            self.assertFalse(
                process_matches(record.keeper_pid, record.keeper_created_at)
            )
            self.assertFalse(process_matches(record.root_pid, record.root_created_at))


if __name__ == "__main__":
    unittest.main()
