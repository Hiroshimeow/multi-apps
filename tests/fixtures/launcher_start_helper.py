import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from lib.runners.command_runner import CommandRunner
from lib.session.subprocess_session import SubprocessSessionManager


def build_command(fixture, pid_file, child_pid_file):
    parts = [
        sys.executable,
        str(fixture),
        "--pid-file",
        str(pid_file),
        "--child-pid-file",
        str(child_pid_file),
    ]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def wait_for(path, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    pid_file = root / "parent.pid"
    child_pid_file = root / "child.pid"
    app = {
        "id": "persistent-helper",
        "name": "Persistent Helper",
        "path": str(root),
        "command": build_command(Path(args.fixture), pid_file, child_pid_file),
        "args": [],
        "multi_run": False,
        "close_timeout": 0.5,
    }
    session = SubprocessSessionManager(
        {"log_dir": str(root / "logs"), "_config_dir": str(root)}
    )
    success, message = session.start(CommandRunner(app, {}))
    if not success:
        raise RuntimeError(message)

    wait_for(pid_file)
    wait_for(child_pid_file)
    record = session.registry.list_records()[0]
    metadata = {
        "record": record.to_dict(),
        "parent_pid": int(pid_file.read_text(encoding="utf-8")),
        "child_pid": int(child_pid_file.read_text(encoding="utf-8")),
    }
    Path(args.metadata).write_text(json.dumps(metadata), encoding="utf-8")


if __name__ == "__main__":
    main()
