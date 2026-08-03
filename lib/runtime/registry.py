from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator
from uuid import uuid4

from ..utils import print_warning
from .models import RunRecord

_LOCK_TIMEOUT = 5.0
_IO_RETRY_TIMEOUT = 1.0
_IO_RETRY_INTERVAL = 0.01
_WINDOWS_TRANSIENT_ERRORS = {5, 32, 33}
_HOT_INDEX_VERSION = 1
_HOT_ACTIVE_STATES = frozenset({"starting", "running", "stopping", "orphaned"})
_TERMINAL_STATES = frozenset({"stopped", "failed"})


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
        self.hot_index_path = self.runtime_dir / "hot-index-v1.json"
        self.hot_index_dirty_path = self.runtime_dir / ".hot-index-v1.dirty"
        self.hot_index_lock_path = self.locks_dir / "hot-index-v1.lock"
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

    def _app_lock_path(self, app_id: str) -> Path:
        value = str(app_id)
        if not value:
            raise ValueError("app_id is required")
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "app"
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        return self.locks_dir / f"app-{slug[:48]}-{digest}.lock"

    @contextmanager
    def app_lock(self, app_id: str) -> Iterator[None]:
        with _exclusive_file_lock(self._app_lock_path(app_id)):
            yield

    def save(self, record: RunRecord) -> Path:
        with _exclusive_file_lock(self.hot_index_lock_path):
            index = self._ensure_hot_index_unlocked()
            with _exclusive_file_lock(self._lock_path(record.run_id)):
                previous = self._load_path(self._record_path(record.run_id), warn=True)
                self._mark_hot_index_dirty_unlocked()
                path = self._save_unlocked(record)
            if previous is not None and (
                previous.app_id != record.app_id
                or previous.created_at != record.created_at
            ):
                index = self._rebuild_hot_index_unlocked()
            else:
                self._apply_record_to_index_unlocked(index, record)
                self._write_hot_index_unlocked(index)
            self._clear_hot_index_dirty_unlocked()
            return path

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

    def read_hot_index(self) -> dict[str, Any]:
        with _exclusive_file_lock(self.hot_index_lock_path):
            return self._copy_index(self._ensure_hot_index_unlocked())

    def rebuild_hot_index(self) -> dict[str, Any]:
        with _exclusive_file_lock(self.hot_index_lock_path):
            index = self._rebuild_hot_index_unlocked()
            self._clear_hot_index_dirty_unlocked()
            return self._copy_index(index)

    def indexed_records(
        self,
        app_ids: Iterable[str] | None = None,
        *,
        include_latest: bool = True,
    ) -> list[RunRecord]:
        requested = None if app_ids is None else tuple(dict.fromkeys(str(v) for v in app_ids))
        for _attempt in range(2):
            index = self.read_hot_index()
            selected_apps = (
                tuple(sorted(set(index["active"]).union(index["latest"])))
                if requested is None
                else requested
            )
            references: list[tuple[str, str, bool]] = []
            seen: set[str] = set()
            for app_id in selected_apps:
                for run_id in index["active"].get(app_id, []):
                    if run_id not in seen:
                        references.append((app_id, run_id, True))
                        seen.add(run_id)
                if include_latest:
                    run_id = index["latest"].get(app_id)
                    if run_id and run_id not in seen:
                        references.append((app_id, run_id, False))
                        seen.add(run_id)

            records: list[RunRecord] = []
            stale = False
            for app_id, run_id, must_be_active in references:
                record = self.load(run_id)
                if (
                    record is None
                    or record.app_id != app_id
                    or (must_be_active and record.state not in _HOT_ACTIVE_STATES)
                ):
                    stale = True
                    break
                records.append(record)
            if not stale:
                return records
            self.rebuild_hot_index()
        return []

    def update(
        self,
        run_id: str,
        *,
        expected_updated_at: str | None = None,
        **changes: Any,
    ) -> RunRecord | None:
        with _exclusive_file_lock(self.hot_index_lock_path):
            index = self._ensure_hot_index_unlocked()
            with _exclusive_file_lock(self._lock_path(run_id)):
                record = self._load_path(self._record_path(run_id), warn=True)
                if record is None:
                    return None
                if (
                    expected_updated_at is not None
                    and record.updated_at != expected_updated_at
                ):
                    return record
                requested_state = changes.get("state")
                if (
                    record.state in _TERMINAL_STATES
                    and requested_state is not None
                    and requested_state not in _TERMINAL_STATES
                ):
                    return record
                for key in changes:
                    if not hasattr(record, key):
                        raise AttributeError(f"Unknown run record field: {key}")
                previous_app_id = record.app_id
                previous_created_at = record.created_at
                self._mark_hot_index_dirty_unlocked()
                for key, value in changes.items():
                    setattr(record, key, value)
                record.__post_init__()
                self._save_unlocked(record)
            if (
                record.app_id != previous_app_id
                or record.created_at != previous_created_at
            ):
                index = self._rebuild_hot_index_unlocked()
            else:
                self._apply_record_to_index_unlocked(index, record)
                self._write_hot_index_unlocked(index)
            self._clear_hot_index_dirty_unlocked()
            return record

    def delete(self, run_id: str) -> bool:
        with _exclusive_file_lock(self.hot_index_lock_path):
            index = self._ensure_hot_index_unlocked()
            with _exclusive_file_lock(self._lock_path(run_id)):
                path = self._record_path(run_id)
                record = self._load_path(path, warn=True)
                if record is None:
                    return False
                self._mark_hot_index_dirty_unlocked()
                try:
                    path.unlink()
                except FileNotFoundError:
                    return False
            if index["latest"].get(record.app_id) == run_id:
                index = self._rebuild_hot_index_unlocked()
            else:
                active = index["active"].get(record.app_id, [])
                remaining = [value for value in active if value != run_id]
                if remaining:
                    index["active"][record.app_id] = remaining
                else:
                    index["active"].pop(record.app_id, None)
                self._write_hot_index_unlocked(index)
            self._clear_hot_index_dirty_unlocked()
            return True

    def _ensure_hot_index_unlocked(self) -> dict[str, Any]:
        index = None if self.hot_index_dirty_path.exists() else self._load_hot_index_unlocked()
        if index is None or self._runs_directory_is_newer_than_index():
            index = self._rebuild_hot_index_unlocked()
            self._clear_hot_index_dirty_unlocked()
        return index

    def _load_hot_index_unlocked(self) -> dict[str, Any] | None:
        try:
            with self.hot_index_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != _HOT_INDEX_VERSION:
            return None
        active = payload.get("active")
        latest = payload.get("latest")
        if not isinstance(active, dict) or not isinstance(latest, dict):
            return None
        normalized_active: dict[str, list[str]] = {}
        for app_id, run_ids in active.items():
            if not isinstance(app_id, str) or not isinstance(run_ids, list):
                return None
            if any(not isinstance(run_id, str) or not run_id for run_id in run_ids):
                return None
            if run_ids:
                normalized_active[app_id] = list(dict.fromkeys(run_ids))
        normalized_latest: dict[str, str] = {}
        for app_id, run_id in latest.items():
            if not isinstance(app_id, str) or not isinstance(run_id, str) or not run_id:
                return None
            normalized_latest[app_id] = run_id
        return {
            "version": _HOT_INDEX_VERSION,
            "active": normalized_active,
            "latest": normalized_latest,
        }

    def _rebuild_hot_index_unlocked(self) -> dict[str, Any]:
        index: dict[str, Any] = {
            "version": _HOT_INDEX_VERSION,
            "active": {},
            "latest": {},
        }
        records = self.list_records()
        by_id = {record.run_id: record for record in records}
        for record in records:
            if record.state in _HOT_ACTIVE_STATES:
                index["active"].setdefault(record.app_id, []).append(record.run_id)
            latest_id = index["latest"].get(record.app_id)
            latest = by_id.get(latest_id) if latest_id else None
            if latest is None or self._record_key(record) > self._record_key(latest):
                index["latest"][record.app_id] = record.run_id
        for app_id, run_ids in tuple(index["active"].items()):
            run_ids.sort(key=lambda value: self._record_key(by_id[value]))
            if not run_ids:
                index["active"].pop(app_id, None)
        self._write_hot_index_unlocked(index)
        return index

    def _apply_record_to_index_unlocked(
        self,
        index: dict[str, Any],
        record: RunRecord,
    ) -> None:
        active = [
            run_id
            for run_id in index["active"].get(record.app_id, [])
            if run_id != record.run_id
        ]
        if record.state in _HOT_ACTIVE_STATES:
            active.append(record.run_id)
        if active:
            loaded = {run_id: self.load(run_id) for run_id in active}
            active = [run_id for run_id in active if loaded.get(run_id) is not None]
            active.sort(key=lambda run_id: self._record_key(loaded[run_id]))
            index["active"][record.app_id] = active
        else:
            index["active"].pop(record.app_id, None)

        latest_id = index["latest"].get(record.app_id)
        latest = self.load(latest_id) if latest_id and latest_id != record.run_id else None
        if (
            latest_id == record.run_id
            or latest is None
            or self._record_key(record) > self._record_key(latest)
        ):
            index["latest"][record.app_id] = record.run_id

    def _write_hot_index_unlocked(self, index: dict[str, Any]) -> None:
        index["active"] = {
            app_id: list(run_ids)
            for app_id, run_ids in sorted(index["active"].items())
            if run_ids
        }
        index["latest"] = dict(sorted(index["latest"].items()))
        atomic_write_json(self.hot_index_path, index)

    def _mark_hot_index_dirty_unlocked(self) -> None:
        self.hot_index_dirty_path.parent.mkdir(parents=True, exist_ok=True)
        with self.hot_index_dirty_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("dirty\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _clear_hot_index_dirty_unlocked(self) -> None:
        try:
            self.hot_index_dirty_path.unlink()
        except FileNotFoundError:
            pass

    def _runs_directory_is_newer_than_index(self) -> bool:
        try:
            return self.runs_dir.stat().st_mtime_ns > self.hot_index_path.stat().st_mtime_ns
        except FileNotFoundError:
            return True

    @staticmethod
    def _record_key(record: RunRecord) -> tuple[str, str]:
        return record.created_at, record.run_id

    @staticmethod
    def _copy_index(index: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": _HOT_INDEX_VERSION,
            "active": {
                app_id: list(run_ids) for app_id, run_ids in index["active"].items()
            },
            "latest": dict(index["latest"]),
        }
