from __future__ import annotations

import argparse
import time
from pathlib import Path

from lib.runtime.process_keeper import ProcessKeeper


class FailingAfterSpawnKeeper(ProcessKeeper):
    def _monitor(self) -> int:
        parent_pid = Path(self.record.path) / "parent.pid"
        child_pid = Path(self.record.path) / "child.pid"
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if parent_pid.exists() and child_pid.exists():
                raise RuntimeError("injected keeper failure after spawn")
            time.sleep(0.05)
        raise RuntimeError("fixture tree did not become ready before injected failure")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return FailingAfterSpawnKeeper(Path(args.runtime_dir), args.run_id).run()


if __name__ == "__main__":
    raise SystemExit(main())
