import gc
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path

from lib.runners.command_runner import CommandRunner
from lib.runtime.models import RunRecord
from lib.runtime.process_identity import get_process_created_at, process_matches
from lib.runtime.registry import RuntimeRegistry
from lib.session.subprocess_session import SubprocessSessionManager


class PersistentSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.tree_fixture = (
            Path(__file__).parent / "fixtures" / "process_tree_helper.py"
        )

    def _command(self, fixture, pid_file, child_pid_file, *extra):
        parts = [
            sys.executable,
            str(fixture),
            "--pid-file",
            str(pid_file),
            "--child-pid-file",
            str(child_pid_file),
            *extra,
        ]
        return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)

    def _app(self, root, app_id="persistent-helper"):
        return {
            "id": app_id,
            "name": "Persistent Helper",
            "path": str(root),
            "command": self._command(
                self.tree_fixture,
                root / "parent.pid",
                root / "child.pid",
            ),
            "args": [],
            "multi_run": False,
            "close_timeout": 0.5,
        }

    @staticmethod
    def _session(root):
        return SubprocessSessionManager(
            {"log_dir": str(root / "logs"), "_config_dir": str(root)}
        )

    def _wait_for_file(self, path, timeout=8.0):
        self._wait_until(lambda: path.exists(), f"file {path}", timeout)

    def _wait_until(self, predicate, description, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {description}")

    def _assert_process_stops(self, pid, created_at, timeout=8.0):
        self._wait_until(
            lambda: not process_matches(pid, created_at),
            f"PID {pid} to stop",
            timeout,
        )

    def _force_cleanup(self, runtime_dir):
        registry = RuntimeRegistry(runtime_dir)
        for record in registry.list_records():
            if not process_matches(record.keeper_pid, record.keeper_created_at):
                continue
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(record.keeper_pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                try:
                    os.kill(record.keeper_pid, 9)
                except ProcessLookupError:
                    pass

    def test_session_replacement_reconnects_same_run_and_continuing_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / ".runtime"
            session_a = self._session(root)
            app = self._app(root)
            try:
                success, message = session_a.start(CommandRunner(app, {}))
                self.assertTrue(success, message)
                self._wait_for_file(root / "parent.pid")
                self._wait_for_file(root / "child.pid")

                record_a = session_a.registry.list_records()[0]
                parent_pid = int((root / "parent.pid").read_text(encoding="utf-8"))
                child_pid = int((root / "child.pid").read_text(encoding="utf-8"))
                parent_created_at = get_process_created_at(parent_pid)
                child_created_at = get_process_created_at(child_pid)
                keeper_created_at = record_a.keeper_created_at
                stdout_path = Path(record_a.stdout_path)
                self._wait_until(
                    lambda: stdout_path.exists() and stdout_path.stat().st_size > 0,
                    "initial supervisor log output",
                )

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", ResourceWarning)
                    session_a = None
                    gc.collect()

                session_b = self._session(root)
                info = session_b.get_info("persistent-helper")
                record_b = session_b.registry.load(record_a.run_id)

                self.assertEqual(info["status"], "RUNNING")
                self.assertEqual(info["run_id"], record_a.run_id)
                self.assertEqual(info["keeper_pid"], record_a.keeper_pid)
                self.assertEqual(info["pid"], record_a.root_pid)
                self.assertEqual(info["stdout_path"], record_a.stdout_path)
                self.assertEqual(info["stderr_path"], record_a.stderr_path)
                self.assertEqual(record_b.run_id, record_a.run_id)
                self.assertEqual(record_b.keeper_pid, record_a.keeper_pid)
                self.assertEqual(record_b.root_pid, record_a.root_pid)

                initial_size = stdout_path.stat().st_size
                self._wait_until(
                    lambda: stdout_path.stat().st_size > initial_size,
                    "logs to continue after session replacement",
                )

                duplicate_success, duplicate_message = session_b.start(
                    CommandRunner(app, {})
                )
                self.assertFalse(duplicate_success, duplicate_message)

                success, message = session_b.stop("persistent-helper")
                self.assertTrue(success, message)
                self._assert_process_stops(parent_pid, parent_created_at)
                self._assert_process_stops(child_pid, child_created_at)
                self._assert_process_stops(record_a.keeper_pid, keeper_created_at)

                stopped = session_b.registry.load(record_a.run_id)
                self.assertEqual(stopped.state, "stopped")
                output = stdout_path.read_text(encoding="utf-8", errors="replace")
                self.assertIn("RUN START", output)
                self.assertIn("RUN STOP", output)
            finally:
                self._force_cleanup(runtime_dir)

    def test_launcher_helper_exit_does_not_stop_supervisor_or_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / ".runtime"
            metadata_path = root / "metadata.json"
            helper = Path(__file__).parent / "fixtures" / "launcher_start_helper.py"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(self.project_root)
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(helper),
                        "--root",
                        str(root),
                        "--metadata",
                        str(metadata_path),
                        "--fixture",
                        str(self.tree_fixture),
                    ],
                    cwd=self.project_root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"stdout={completed.stdout}\nstderr={completed.stderr}",
                )
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                original = RunRecord.from_dict(metadata["record"])
                parent_pid = int(metadata["parent_pid"])
                child_pid = int(metadata["child_pid"])
                parent_created_at = get_process_created_at(parent_pid)
                child_created_at = get_process_created_at(child_pid)

                self.assertTrue(
                    process_matches(original.keeper_pid, original.keeper_created_at)
                )
                self.assertTrue(process_matches(parent_pid, parent_created_at))
                self.assertTrue(process_matches(child_pid, child_created_at))

                stdout_path = Path(original.stdout_path)
                initial_size = stdout_path.stat().st_size
                self._wait_until(
                    lambda: stdout_path.stat().st_size > initial_size,
                    "logs after helper launcher exit",
                )

                replacement = self._session(root)
                info = replacement.get_info("persistent-helper")
                self.assertEqual(info["status"], "RUNNING")
                self.assertEqual(info["run_id"], original.run_id)
                self.assertEqual(info["keeper_pid"], original.keeper_pid)
                self.assertEqual(info["pid"], original.root_pid)

                success, message = replacement.stop("persistent-helper")
                self.assertTrue(success, message)
                self._assert_process_stops(parent_pid, parent_created_at)
                self._assert_process_stops(child_pid, child_created_at)
                self._assert_process_stops(
                    original.keeper_pid, original.keeper_created_at
                )
            finally:
                self._force_cleanup(runtime_dir)

    def test_mismatched_keeper_identity_is_orphaned_without_killing_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / ".runtime"
            registry = RuntimeRegistry(runtime_dir)
            current_pid = os.getpid()
            current_created_at = get_process_created_at(current_pid)
            self.assertIsNotNone(current_created_at)
            record = RunRecord.create(
                app_id="uncertain-helper",
                path=str(root),
                command="unused",
                run_id="uncertain-run",
            )
            record.keeper_pid = current_pid
            record.keeper_created_at = current_created_at + 1000.0
            record.root_pid = current_pid
            record.root_created_at = current_created_at
            record.state = "running"
            registry.save(record)

            session = self._session(root)
            info = session.get_info("uncertain-helper")

            self.assertEqual(info["status"], "ORPHANED")
            self.assertTrue(process_matches(current_pid, current_created_at))
            success, message = session.stop("uncertain-helper")
            self.assertFalse(success)
            self.assertIn("refusing to stop", message)
            self.assertTrue(process_matches(current_pid, current_created_at))


if __name__ == "__main__":
    unittest.main()
