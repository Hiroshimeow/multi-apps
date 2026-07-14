import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

MIN_TAIL_LINES = 1
DEFAULT_TAIL_LINES = 100
MAX_TAIL_LINES = 5000
MIN_READ_BYTES = 1024
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
MAX_READ_BYTES = 8 * 1024 * 1024


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
    def read(
        self,
        path: str | os.PathLike[str],
        *,
        max_lines: int = DEFAULT_TAIL_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> LogSnapshot: ...


class BoundedLogReader:
    def __init__(self, *, opener=open):
        self._opener = opener

    @staticmethod
    def _validate_int(name, value, minimum, maximum):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an int")
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")

    @staticmethod
    def _canonical_path(path):
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str):
            raise TypeError("path must resolve to str, not bytes")
        if raw_path == "":
            raise ValueError("path must not be empty")
        return str(Path(raw_path).expanduser().resolve(strict=False))

    def read(
        self,
        path: str | os.PathLike[str],
        *,
        max_lines: int = DEFAULT_TAIL_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> LogSnapshot:
        self._validate_int("max_lines", max_lines, MIN_TAIL_LINES, MAX_TAIL_LINES)
        self._validate_int("max_bytes", max_bytes, MIN_READ_BYTES, MAX_READ_BYTES)
        canonical = self._canonical_path(path)

        try:
            with self._opener(canonical, "rb") as handle:
                stat = os.fstat(handle.fileno())
                size_bytes = int(stat.st_size)
                identity = (
                    (int(stat.st_dev), int(stat.st_ino))
                    if stat.st_dev or stat.st_ino
                    else None
                )
                if size_bytes == 0:
                    return LogSnapshot(
                        path=canonical,
                        state=LogSnapshotState.EMPTY,
                        size_bytes=0,
                        file_identity=identity,
                    )

                read_start = max(0, size_bytes - max_bytes)
                read_length = size_bytes - read_start
                preceding_lf = False
                if read_start > 0:
                    handle.seek(read_start - 1)
                    preceding_lf = handle.read(1) == b"\n"

                handle.seek(read_start)
                raw = handle.read(read_length)
        except FileNotFoundError:
            return LogSnapshot(path=canonical, state=LogSnapshotState.MISSING)
        except OSError as exc:
            return LogSnapshot(
                path=canonical,
                state=LogSnapshotState.UNREADABLE,
                error=f"{type(exc).__name__}: {exc}",
            )

        truncated = read_start > 0
        if read_start > 0 and not preceding_lf:
            first_lf = raw.find(b"\n")
            if first_lf >= 0:
                if first_lf + 1 < len(raw):
                    raw = raw[first_lf + 1 :]
                else:
                    raw = raw[:first_lf]

        lines = raw.decode("utf-8", errors="replace").splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            truncated = True

        return LogSnapshot(
            path=canonical,
            state=LogSnapshotState.READY,
            lines=tuple(lines),
            size_bytes=size_bytes,
            file_identity=identity,
            truncated=truncated,
        )


class LogChangeKind(StrEnum):
    INITIAL = "initial"
    UNCHANGED = "unchanged"
    APPENDED = "appended"
    TRUNCATED = "truncated"
    ROTATED = "rotated"
    BECAME_MISSING = "became_missing"
    REAPPEARED = "reappeared"
    STATE_CHANGED = "state_changed"


def _same_snapshot(previous: LogSnapshot, current: LogSnapshot) -> bool:
    return (
        previous.state == current.state
        and previous.size_bytes == current.size_bytes
        and previous.file_identity == current.file_identity
        and previous.lines == current.lines
        and previous.truncated == current.truncated
        and previous.error == current.error
    )


def classify_log_change(
    previous: LogSnapshot | None,
    current: LogSnapshot,
) -> LogChangeKind:
    if previous is None:
        return LogChangeKind.INITIAL
    if previous.state != LogSnapshotState.MISSING and current.state == LogSnapshotState.MISSING:
        return LogChangeKind.BECAME_MISSING
    if previous.state == LogSnapshotState.MISSING and current.state != LogSnapshotState.MISSING:
        return LogChangeKind.REAPPEARED

    previous_unreadable = previous.state == LogSnapshotState.UNREADABLE
    current_unreadable = current.state == LogSnapshotState.UNREADABLE
    if previous_unreadable or current_unreadable:
        if previous_unreadable != current_unreadable:
            return LogChangeKind.STATE_CHANGED
        return LogChangeKind.UNCHANGED if _same_snapshot(previous, current) else LogChangeKind.STATE_CHANGED

    if (
        previous.file_identity is not None
        and current.file_identity is not None
        and previous.file_identity != current.file_identity
    ):
        return LogChangeKind.ROTATED

    empty_ready = {previous.state, current.state} == {
        LogSnapshotState.EMPTY,
        LogSnapshotState.READY,
    }
    if empty_ready:
        if previous.state == LogSnapshotState.EMPTY and current.size_bytes > previous.size_bytes:
            return LogChangeKind.APPENDED
        if previous.state == LogSnapshotState.READY and current.size_bytes < previous.size_bytes:
            return LogChangeKind.TRUNCATED
        return LogChangeKind.STATE_CHANGED

    if previous.state != current.state:
        return LogChangeKind.STATE_CHANGED
    if current.size_bytes > previous.size_bytes:
        return LogChangeKind.APPENDED
    if current.size_bytes < previous.size_bytes:
        return LogChangeKind.TRUNCATED
    if _same_snapshot(previous, current):
        return LogChangeKind.UNCHANGED
    return LogChangeKind.STATE_CHANGED
