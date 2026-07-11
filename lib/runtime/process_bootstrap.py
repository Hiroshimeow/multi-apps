from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--path", default="")
    parser.add_argument("--command", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ready_file = Path(args.ready_file)
    deadline = time.monotonic() + 10.0
    while not ready_file.exists():
        if time.monotonic() >= deadline:
            return 125
        time.sleep(0.01)

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        args.command,
        cwd=args.path or None,
        stdin=subprocess.DEVNULL,
        shell=True,
        creationflags=creationflags,
    )
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
