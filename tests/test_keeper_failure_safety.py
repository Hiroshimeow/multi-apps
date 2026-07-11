import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lib.runtime.models import RunRecord
from lib.runtime.process_identity import get_process_created_at, process_matches
from lib.runtime.process_keeper import ProcessKeeper
from lib.runtime.registry import RuntimeRegistry


class _FakeProcess:
    pid = 43210

    def __init__(self, job):
        self.job = job

    def poll(self):
        return None if self.job.alive else 1

    def wait(self, timeout=None):
        if self.job.alive:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 1


class _RecordingJob:
    def __init__(self, registry, run_id, *, fail_terminate=False):
        self.registry = registry
        self.run_id = run_id
        self.fail_terminate = fail_terminate
        self.alive = True
        self.state_when_terminated = None

    def active_process_count(self):
        return 1 if self.alive else 0

    def terminate(self, exit_code=1):
        self.state_when_terminated = self.registry.load(self.run_id).state
        if self.fail_terminate:
            raise OSError("simulated TerminateJobObject failure")
        self.alive = False


class KeeperStopOrderingTests(unittest.TestCase):
    def _keeper(self, root, *, fail_terminate=False):
        runtime_dir = root / ".runtime"
        registry = RuntimeRegistry(runtime_dir)
        record = RunRecord.create(
            app_id="ordering",
            path=str(root),
            command="unused",
            run_id="ordering-run",
            stdout_path=str(root / "out.log"),
            stderr_path=str(root / "err.log"),
            close_timeout=0.01,
        )
        record.state = "running"
        registry.save(record)
        keeper = ProcessKeeper(runtime_dir, record.run_id)
        keeper.is_windows = True
        keeper.job = _RecordingJob(
            registry,
            record.run_id,
            fail_terminate=fail_terminate,
        )
        keeper.process = _FakeProcess(keeper.job)
        keeper.stdout_handle = io.StringIO()
        keeper.stderr_handle = io.StringIO()
        keeper._request_graceful_termination = lambda: None
        return keeper, registry

    def test_force_stop_stays_stopping_until_tree_is_verified_gone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            keeper, registry = self._keeper(Path(temp_dir))

            result = keeper._stop_managed_process("test")

            self.assertEqual(result, 0)
            self.assertEqual(keeper.job.state_when_terminated, "stopping")
            stopped = registry.load("ordering-run")
            self.assertEqual(stopped.state, "stopped")
            self.assertFalse(keeper.job.alive)

    def test_force_termination_failure_never_records_stopped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            keeper, registry = self._keeper(
                Path(temp_dir), fail_terminate=True
            )

            with self.assertRaisesRegex(OSError, "simulated"):
                keeper._stop_managed_process("test")

            self.assertEqual(keeper.job.state_when_terminated, "stopping")
            self.assertEqual(registry.load("ordering-run").state, "stopping")
            self.assertTrue(keeper.job.alive)


@unittest.skipIf(os.name == "nt", "Linux process-group cleanup test")
class LinuxKeeperFailureCleanupTests(unittest.TestCase):
    def test_keeper_failure_after_spawn_cleans_owned_process_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / ".runtime"
            fixture = Path(__file__).parent / "fixtures" / "process_tree_helper.py"
            helper = Path(__file__).parent / "fixtures" / "keeper_failure_helper.py"
            command = shlex.join(
                [
                    sys.executable,
                    str(fixture),
                    "--pid-file",
                    str(root / "parent.pid"),
                    "--child-pid-file",
                    str(root / "child.pid"),
                    "--ignore-termination",
                    "--ignore-child-termination",
                ]
            )
            registry = RuntimeRegistry(runtime_dir)
            record = RunRecord.create(
                app_id="failure-helper",
                path=str(root),
                command=command,
                run_id="failure-run",
                stdout_path=str(root / "failure.out.log"),
                stderr_path=str(root / "failure.err.log"),
                close_timeout=0.1,
            )
            registry.save(record)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

            completed = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "--runtime-dir",
                    str(runtime_dir),
                    "--run-id",
                    record.run_id,
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            parent_pid = int((root / "parent.pid").read_text(encoding="utf-8"))
            child_pid = int((root / "child.pid").read_text(encoding="utf-8"))
            parent_created_at = get_process_created_at(parent_pid)
            child_created_at = get_process_created_at(child_pid)
            self.assertFalse(process_matches(parent_pid, parent_created_at))
            self.assertFalse(process_matches(child_pid, child_created_at))
            failed = registry.load(record.run_id)
            self.assertEqual(failed.state, "failed")
            output = Path(failed.stdout_path).read_text(
                encoding="utf-8", errors="replace"
            )
            self.assertIn("RUN STOP failed", output)


if __name__ == "__main__":
    unittest.main()
