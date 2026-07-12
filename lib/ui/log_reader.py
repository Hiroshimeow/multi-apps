from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class LogSnapshotState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class LogSnapshot:
    path: str
    state: LogSnapshotState
    lines: tuple[str, ...] = ()
    size_bytes: int = 0
    file_identity: tuple[int, int] | None = None
    truncated: bool = False
    error: str | None = None


class LogReader(Protocol):
    def read(self, path: str, *, max_lines: int, max_bytes: int) -> LogSnapshot: ...
