import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lib.runners.command_runner import CommandRunner
from lib.runtime.process_identity import get_process_created_at, process_matches
from lib.session.subprocess_session import SubprocessSessionManager


class ProcessTreeCleanupTests(unittest.TestCase):
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

    def _start_tree(self, root, *extra):
        fixture = Path(__file__).parent / "fixtures" / "process_tree_helper.py"
        pid_file = root / "parent.pid"
        child_pid_file = root / "child.pid"
        app = {
            "id": "tree-helper",
            "name": "Tree Helper",
            "path": str(root),
            "command": self._command(
                fixture, pid_file, child_pid_file, *extra
            ),
            "args": [],
            "multi_run": False,
            "close_timeout": 0.5,
        }
        session = SubprocessSessionManager(
            {"log_dir": str(root / "logs"), "_config_dir": str(root)}
        )
        try:
            success, message = session.start(
                CommandRunner(app, {}),
                wait_for_ready=True,
            )
            self.assertTrue(success, message)
            self._wait_for_file(pid_file)
            self._wait_for_file(child_pid_file)
            return session, pid_file, child_pid_file
        except Exception:
            self._force_cleanup(session)
            raise

    def _wait_for_file(self, path, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline and not path.exists():
            time.sleep(0.05)
        self.assertTrue(path.exists(), f"Timed out waiting for {path}")

    def _assert_process_stops(self, pid, created_at, timeout=6):
        deadline = time.time() + timeout
        while time.time() < deadline and process_matches(pid, created_at):
            time.sleep(0.05)
        self.assertFalse(process_matches(pid, created_at), f"PID {pid} is still alive")

    def _force_cleanup(self, session):
        for record in session.registry.list_records():
            if process_matches(record.keeper_pid, record.keeper_created_at):
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

        deadline = time.monotonic() + 3.0
        while session.client._keepers and time.monotonic() < deadline:
            session.client.reap_finished()
            time.sleep(0.05)

    def test_normal_stop_closes_parent_and_descendant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session, pid_file, child_pid_file = self._start_tree(root)
            try:
                parent_pid = int(pid_file.read_text(encoding="utf-8"))
                child_pid = int(child_pid_file.read_text(encoding="utf-8"))
                parent_created_at = get_process_created_at(parent_pid)
                child_created_at = get_process_created_at(child_pid)
                self.assertIsNotNone(parent_created_at)
                self.assertIsNotNone(child_created_at)

                success, message = session.stop("tree-helper")
                self.assertTrue(success, message)
                self._assert_process_stops(parent_pid, parent_created_at)
                self._assert_process_stops(child_pid, child_created_at)
            finally:
                self._force_cleanup(session)

    def test_immediate_stop_during_starting_leaves_no_verified_processes(self):
        fixture = Path(__file__).parent / "fixtures" / "process_tree_helper.py"
        for index in range(3):
            with self.subTest(run=index), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                pid_file = root / "parent.pid"
                child_pid_file = root / "child.pid"
                app_id = f"immediate-tree-{index}"
                app = {
                    "id": app_id,
                    "name": f"Immediate Tree {index}",
                    "path": str(root),
                    "command": self._command(fixture, pid_file, child_pid_file),
                    "args": [],
                    "multi_run": False,
                    "close_timeout": 0.5,
                }
                session = SubprocessSessionManager(
                    {"log_dir": str(root / "logs"), "_config_dir": str(root)}
                )
                try:
                    success, message = session.start(CommandRunner(app, {}))
                    self.assertTrue(success, message)

                    success, message = session.stop(app_id)
                    self.assertTrue(success, message)

                    records = session.registry.list_records()
                    self.assertEqual(len(records), 1)
                    record = records[0]
                    self.assertEqual(record.state, "stopped")

                    for label, pid, created_at in (
                        (
                            "keeper",
                            record.keeper_pid,
                            record.keeper_created_at,
                        ),
                        (
                            "root",
                            record.root_pid,
                            record.root_created_at,
                        ),
                    ):
                        self.assertGreater(pid, 0, f"missing {label} PID")
                        self.assertGreater(
                            created_at,
                            0.0,
                            f"missing {label} creation time",
                        )
                        self.assertFalse(
                            process_matches(pid, created_at),
                            f"verified {label} identity is still alive",
                        )

                    for label, path in (
                        ("parent", pid_file),
                        ("child", child_pid_file),
                    ):
                        if not path.exists():
                            continue
                        pid_text = path.read_text(encoding="utf-8").strip()
                        self.assertTrue(pid_text, f"{label} PID file is empty")
                        pid = int(pid_text)
                        self.assertIsNone(
                            get_process_created_at(pid),
                            f"materialized {label} PID {pid} is still alive",
                        )
                finally:
                    self._force_cleanup(session)

    def test_force_timeout_closes_termination_resistant_descendant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session, pid_file, child_pid_file = self._start_tree(
                root,
                "--ignore-termination",
                "--ignore-child-termination",
            )
            try:
                parent_pid = int(pid_file.read_text(encoding="utf-8"))
                child_pid = int(child_pid_file.read_text(encoding="utf-8"))
                parent_created_at = get_process_created_at(parent_pid)
                child_created_at = get_process_created_at(child_pid)

                success, message = session.stop("tree-helper")
                self.assertTrue(success, message)
                self._assert_process_stops(parent_pid, parent_created_at)
                self._assert_process_stops(child_pid, child_created_at)

                record = session.registry.list_records()[0]
                output = Path(record.stdout_path).read_text(
                    encoding="utf-8", errors="replace"
                )
                self.assertIn("RUN STOP", output)
            finally:
                self._force_cleanup(session)


if __name__ == "__main__":
    unittest.main()
