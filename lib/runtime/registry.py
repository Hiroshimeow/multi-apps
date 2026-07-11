from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from ..utils import print_warning
from .models import RunRecord

_LOCK_TIMEOUT = 5.0
_IO_RETRY_TIMEOUT = 1.0
_IO_RETRY_INTERVAL = 0.01
_WINDOWS_TRANSIENT_ERRORS = {5, 32, 33}


def _is_transient_io_error(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (
        _WINDOWS_TRANSIENT_ERRORS
    )


@contextmanager
def _exclusive_file_lock(path: Path, timeout: float = _LOCK_TIMEOUT) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out locking runtime record: {path}") from exc
                time.sleep(_IO_RETRY_INTERVAL)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def atomic_write_json(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(
        f".{target.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp"
    )

    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        deadline = time.monotonic() + _IO_RETRY_TIMEOUT
        while True:
            try:
                os.replace(temp_path, target)
                break
            except OSError as exc:
                if not _is_transient_io_error(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(_IO_RETRY_INTERVAL)

        if os.name != "nt":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


class RuntimeRegistry:
    def __init__(self, runtime_dir: str | os.PathLike[str]):
        self.runtime_dir = Path(runtime_dir)
        self.runs_dir = self.runtime_dir / "runs"
        self.locks_dir = self.runtime_dir / "locks"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def _record_path(self, run_id: str) -> Path:
        normalized = str(run_id)
        if not normalized or Path(normalized).name != normalized:
            raise ValueError(f"Invalid run_id: {run_id!r}")
        return self.runs_dir / f"{normalized}.json"

    def _lock_path(self, run_id: str) -> Path:
        normalized = str(run_id)
        if not normalized or Path(normalized).name != normalized:
            raise ValueError(f"Invalid run_id: {run_id!r}")
        return self.locks_dir / f"{normalized}.lock"

    def save(self, record: RunRecord) -> Path:
        with _exclusive_file_lock(self._lock_path(record.run_id)):
            return self._save_unlocked(record)

    def _save_unlocked(self, record: RunRecord) -> Path:
        record.touch()
        path = self._record_path(record.run_id)
        atomic_write_json(path, record.to_dict())
        return path

    def load(self, run_id: str) -> RunRecord | None:
        return self._load_path(self._record_path(run_id), warn=True)

    def _load_path(self, path: Path, *, warn: bool) -> RunRecord | None:
        deadline = time.monotonic() + _IO_RETRY_TIMEOUT
        while True:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    return RunRecord.from_dict(json.load(handle))
            except FileNotFoundError:
                return None
            except OSError as exc:
                if _is_transient_io_error(exc) and time.monotonic() < deadline:
                    time.sleep(_IO_RETRY_INTERVAL)
                    continue
                if warn:
                    print_warning(f"Skipping invalid runtime record '{path.name}': {exc}")
                return None
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                if warn:
                    print_warning(f"Skipping invalid runtime record '{path.name}': {exc}")
                return None

    def list_records(self) -> list[RunRecord]:
        records = []
        for path in sorted(self.runs_dir.glob("*.json")):
            record = self._load_path(path, warn=True)
            if record is not None:
                records.append(record)
        return records

    def update(
        self,
        run_id: str,
        *,
        expected_updated_at: str | None = None,
        **changes: Any,
    ) -> RunRecord | None:
        with _exclusive_file_lock(self._lock_path(run_id)):
            record = self._load_path(self._record_path(run_id), warn=True)
            if record is None:
                return None
            if (
                expected_updated_at is not None
                and record.updated_at != expected_updated_at
            ):
                return record
            for key, value in changes.items():
                if not hasattr(record, key):
                    raise AttributeError(f"Unknown run record field: {key}")
                setattr(record, key, value)
            record.__post_init__()
            self._save_unlocked(record)
            return record

    def delete(self, run_id: str) -> bool:
        with _exclusive_file_lock(self._lock_path(run_id)):
            path = self._record_path(run_id)
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True
