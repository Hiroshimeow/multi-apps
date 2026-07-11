import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def write_pid(path):
    Path(path).write_text(str(os.getpid()), encoding="utf-8")


def ignore_termination():
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)


def run_child(args):
    if args.ignore_termination:
        ignore_termination()
    write_pid(args.child_pid_file)
    while True:
        print(f"child-heartbeat {time.monotonic():.6f}", flush=True)
        time.sleep(0.2)


def run_parent(args):
    if args.ignore_termination:
        ignore_termination()
    write_pid(args.pid_file)
    command = [
        sys.executable,
        __file__,
        "--child",
        "--child-pid-file",
        args.child_pid_file,
    ]
    if args.ignore_child_termination:
        command.append("--ignore-termination")
    child = subprocess.Popen(command)
    try:
        while child.poll() is None:
            print(f"parent-heartbeat {time.monotonic():.6f}", flush=True)
            time.sleep(0.2)
    finally:
        if child.poll() is None:
            child.terminate()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid-file")
    parser.add_argument("--child-pid-file", required=True)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--ignore-termination", action="store_true")
    parser.add_argument("--ignore-child-termination", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.child:
        run_child(args)
    else:
        if not args.pid_file:
            raise SystemExit("--pid-file is required for parent mode")
        run_parent(args)


if __name__ == "__main__":
    main()
