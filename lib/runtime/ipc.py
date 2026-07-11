from __future__ import annotations

import hashlib
import os
import queue
import threading
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any


def ipc_family() -> str:
    return "AF_PIPE" if os.name == "nt" else "AF_UNIX"


def ipc_address(runtime_dir: str | os.PathLike[str], run_id: str) -> str:
    runtime_path = Path(runtime_dir).resolve()
    namespace = hashlib.sha256(str(runtime_path).encode("utf-8")).hexdigest()[:12]
    if os.name == "nt":
        return rf"\\.\pipe\multi-run-apps-{namespace}-{run_id}"
    ipc_dir = runtime_path / "ipc"
    ipc_dir.mkdir(parents=True, exist_ok=True)
    return str(ipc_dir / f"{run_id}.sock")


def ipc_authkey(runtime_dir: str | os.PathLike[str], run_id: str) -> bytes:
    seed = f"multi-run-apps:{Path(runtime_dir).resolve()}:{run_id}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def create_listener(runtime_dir: str | os.PathLike[str], run_id: str) -> Listener:
    address = ipc_address(runtime_dir, run_id)
    if os.name != "nt":
        try:
            Path(address).unlink()
        except FileNotFoundError:
            pass
    return Listener(
        address=address,
        family=ipc_family(),
        authkey=ipc_authkey(runtime_dir, run_id),
    )


def request(
    runtime_dir: str | os.PathLike[str],
    run_id: str,
    payload: dict[str, Any],
    timeout: float = 2.0,
) -> dict[str, Any]:
    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            connection = Client(
                ipc_address(runtime_dir, run_id),
                family=ipc_family(),
                authkey=ipc_authkey(runtime_dir, run_id),
            )
            try:
                connection.send(payload)
                result_queue.put((True, connection.recv()))
            finally:
                connection.close()
        except Exception as exc:  # IPC surfaces platform-specific exception types.
            result_queue.put((False, exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"Timed out contacting keeper for run {run_id}")

    ok, value = result_queue.get_nowait()
    if not ok:
        raise ConnectionError(f"Keeper IPC failed for run {run_id}: {value}") from value
    if not isinstance(value, dict):
        raise ConnectionError(f"Invalid keeper response for run {run_id}: {value!r}")
    return value
