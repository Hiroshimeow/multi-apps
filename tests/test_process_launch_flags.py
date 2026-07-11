import argparse
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lib.runtime import process_bootstrap
from lib.runtime.process_client import ProcessClient


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

    def test_bootstrap_shell_uses_no_window_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ready_file = Path(temp_dir) / "ready"
            ready_file.write_text("ready\n", encoding="utf-8")
            process = MagicMock()
            process.wait.return_value = 0
            args = argparse.Namespace(
                ready_file=str(ready_file),
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


if __name__ == "__main__":
    unittest.main()
