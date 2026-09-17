import argparse
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lib.runtime import process_bootstrap
from lib.runtime.models import RunRecord
from lib.runtime.process_client import ProcessClient
from lib.runtime.process_keeper import ProcessKeeper


@unittest.skipUnless(os.name == "nt", "Windows console suppression test")
class WindowsProcessLaunchFlagTests(unittest.TestCase):
    def test_keeper_launch_uses_no_window_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            process = MagicMock()
            process.pid = os.getpid()
            process.poll.return_value = None
            with patch("lib.runtime.process_client.subprocess.Popen", return_value=process) as popen, patch(
                "lib.runtime.process_client.get_process_created_at", return_value=1.0
            ):
                client = ProcessClient(Path(temp_dir) / ".runtime", Path.cwd())
                client.launch_keeper("run-1")

            creationflags = popen.call_args.kwargs["creationflags"]
            self.assertTrue(creationflags & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(creationflags & subprocess.CREATE_NEW_PROCESS_GROUP)
            self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_unverified_keeper_identity_terminates_spawned_keeper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            process = MagicMock()
            process.pid = os.getpid()
            process.poll.return_value = None
            process.wait.return_value = 0
            with patch(
                "lib.runtime.process_client.subprocess.Popen",
                return_value=process,
            ), patch(
                "lib.runtime.process_client.get_process_created_at",
                return_value=None,
            ), patch(
                "lib.runtime.process_client.time.monotonic",
                side_effect=[0.0, 3.0],
            ):
                client = ProcessClient(Path(temp_dir) / ".runtime", Path.cwd())
                with self.assertRaisesRegex(RuntimeError, "Could not verify"):
                    client.launch_keeper("run-identity-failure")

            process.terminate.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=2.0)
            self.assertNotIn("run-identity-failure", client._keepers)

    def test_managed_bootstrap_command_carries_app_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / ".runtime"
            record = RunRecord.create(
                app_id="demo-app",
                path=str(root),
                command="uv run main.py",
                run_id="run-demo",
                stdout_path=str(root / "out.log"),
                stderr_path=str(root / "err.log"),
            )
            keeper = ProcessKeeper.__new__(ProcessKeeper)
            keeper.runtime_dir = runtime_dir
            keeper.run_id = record.run_id
            keeper.record = record
            keeper.is_windows = True
            keeper.job = MagicMock()
            keeper.ready_file = None
            keeper.stdout_handle = None
            keeper.stderr_handle = None
            process = MagicMock()
            process.pid = os.getpid()

            with patch(
                "lib.runtime.process_keeper.subprocess.Popen",
                return_value=process,
            ) as popen:
                keeper._start_managed_process()

            argv = popen.call_args.args[0]
            self.assertIn("--app-id", argv)
            self.assertEqual(argv[argv.index("--app-id") + 1], "demo-app")
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["MULTI_RUN_APP_ID"], "demo-app")
            self.assertEqual(env["MULTI_RUN_RUN_ID"], "run-demo")
            keeper.job.assign_pid.assert_called_once_with(process.pid)

    def test_bootstrap_shell_uses_no_window_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ready_file = Path(temp_dir) / "ready"
            ready_file.write_text("ready\n", encoding="utf-8")
            process = MagicMock()
            process.wait.return_value = 0
            args = argparse.Namespace(
                ready_file=str(ready_file),
                app_id="demo-app",
                path=temp_dir,
                command="echo ok",
            )
            with patch("lib.runtime.process_bootstrap.parse_args", return_value=args), patch(
                "lib.runtime.process_bootstrap.subprocess.Popen", return_value=process
            ) as popen:
                result = process_bootstrap.main()

            self.assertEqual(result, 0)
            self.assertTrue(
                popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
            )
            self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue(popen.call_args.kwargs["shell"])
            self.assertEqual(
                popen.call_args.kwargs["env"]["MULTI_RUN_APP_ID"],
                "demo-app",
            )


if __name__ == "__main__":
    unittest.main()
