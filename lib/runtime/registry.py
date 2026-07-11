from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..utils import print_warning
from .models import RunRecord


def atomic_write_json(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f"{target.name}.tmp")

    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temp_path, target)

    if os.name != "nt":
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


class RuntimeRegistry:
    def __init__(self, runtime_dir: str | os.PathLike[str]):
        self.runtime_dir = Path(runtime_dir)
        self.runs_dir = self.runtime_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _record_path(self, run_id: str) -> Path:
        normalized = str(run_id)
        if not normalized or Path(normalized).name != normalized:
            raise ValueError(f"Invalid run_id: {run_id!r}")
        return self.runs_dir / f"{normalized}.json"

    def save(self, record: RunRecord) -> Path:
        record.touch()
        path = self._record_path(record.run_id)
        atomic_write_json(path, record.to_dict())
        return path

    def load(self, run_id: str) -> RunRecord | None:
        path = self._record_path(run_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return RunRecord.from_dict(json.load(handle))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            print_warning(f"Skipping invalid runtime record '{path.name}': {exc}")
            return None

    def list_records(self) -> list[RunRecord]:
        records = []
        for path in sorted(self.runs_dir.glob("*.json")):
            record = self.load(path.stem)
            if record is not None:
                records.append(record)
        return records

    def update(self, run_id: str, **changes: Any) -> RunRecord | None:
        record = self.load(run_id)
        if record is None:
            return None
        for key, value in changes.items():
            if not hasattr(record, key):
                raise AttributeError(f"Unknown run record field: {key}")
            setattr(record, key, value)
        record.__post_init__()
        self.save(record)
        return record

    def delete(self, run_id: str) -> bool:
        path = self._record_path(run_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
