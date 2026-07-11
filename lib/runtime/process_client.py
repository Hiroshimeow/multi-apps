from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .ipc import request
from .process_identity import get_process_created_at


class ProcessClient:
    def __init__(
        self,
        runtime_dir: str | os.PathLike[str],
        project_root: str | os.PathLike[str],
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.project_root = Path(project_root)
        self._keepers: dict[str, subprocess.Popen[bytes]] = {}

    def launch_keeper(self, run_id: str) -> tuple[int, float]:
        command = [
            sys.executable,
            "-m",
            "lib.runtime.process_keeper",
            "--runtime-dir",
            str(self.runtime_dir),
            "--run-id",
            run_id,
        ]
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(self.project_root)
        if existing_pythonpath:
            env["PYTHONPATH"] += os.pathsep + existing_pythonpath

        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            start_new_session = True

        process = subprocess.Popen(
            command,
            cwd=self.project_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=start_new_session,
            close_fds=True,
        )
        self._keepers[run_id] = process
        deadline = time.monotonic() + 2.0
        created_at = get_process_created_at(process.pid)
        while created_at is None and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.02)
            created_at = get_process_created_at(process.pid)
        if created_at is None:
            self._keepers.pop(run_id, None)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            raise RuntimeError(f"Could not verify keeper identity for run {run_id}")
        return process.pid, float(created_at)

    def wait_until_ready(self, run_id: str, timeout: float = 8.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                response = self.status(run_id, timeout=0.5)
                state = response.get("state")
                if response.get("ok") and state == "running":
                    if int(response.get("root_pid") or 0) > 0:
                        return response
                if state in {"failed", "stopped"}:
                    raise RuntimeError(
                        response.get("message") or f"Keeper entered terminal state: {state}"
                    )
            except (ConnectionError, TimeoutError) as exc:
                last_error = exc
            time.sleep(0.05)
        raise TimeoutError(f"Keeper for run {run_id} did not become ready: {last_error}")

    def ping(self, run_id: str, timeout: float = 1.0) -> dict[str, Any]:
        return request(self.runtime_dir, run_id, {"command": "ping"}, timeout)

    def status(self, run_id: str, timeout: float = 1.0) -> dict[str, Any]:
        response = request(self.runtime_dir, run_id, {"command": "status"}, timeout)
        self.reap_finished(run_id)
        return response

    def stop(self, run_id: str, timeout: float = 2.0) -> dict[str, Any]:
        return request(self.runtime_dir, run_id, {"command": "stop"}, timeout)

    def reap_finished(self, run_id: str | None = None) -> None:
        selected = [run_id] if run_id is not None else list(self._keepers)
        for selected_run_id in selected:
            process = self._keepers.get(selected_run_id)
            if process is not None and process.poll() is not None:
                self._keepers.pop(selected_run_id, None)
