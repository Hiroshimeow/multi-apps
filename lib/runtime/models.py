from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

RUN_STATES = frozenset(
    {
        "starting",
        "running",
        "stopping",
        "stopped",
        "orphaned",
        "failed",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class RunRecord:
    app_id: str
    run_id: str
    path: str
    command: str
    args: list[str] = field(default_factory=list)
    keeper_pid: int = 0
    keeper_created_at: float = 0.0
    root_pid: int = 0
    root_created_at: float = 0.0
    stdout_path: str = ""
    stderr_path: str = ""
    close_timeout: float = 5.0
    state: str = "starting"
    exit_code: int | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.app_id = str(self.app_id)
        self.run_id = str(self.run_id)
        self.path = str(self.path)
        self.command = str(self.command)
        self.args = [str(value) for value in self.args]
        self.keeper_pid = int(self.keeper_pid)
        self.keeper_created_at = float(self.keeper_created_at)
        self.root_pid = int(self.root_pid)
        self.root_created_at = float(self.root_created_at)
        self.stdout_path = str(self.stdout_path)
        self.stderr_path = str(self.stderr_path)
        self.close_timeout = float(self.close_timeout)
        self.exit_code = None if self.exit_code is None else int(self.exit_code)
        if self.close_timeout <= 0:
            raise ValueError("close_timeout must be positive")
        if self.state not in RUN_STATES:
            raise ValueError(f"Invalid run state: {self.state}")
        if not self.app_id or not self.run_id:
            raise ValueError("app_id and run_id are required")

    @classmethod
    def create(
        cls,
        *,
        app_id: str,
        path: str,
        command: str,
        args: list[str] | None = None,
        stdout_path: str = "",
        stderr_path: str = "",
        close_timeout: float = 5.0,
        run_id: str | None = None,
    ) -> "RunRecord":
        return cls(
            app_id=app_id,
            run_id=run_id or uuid4().hex,
            path=path,
            command=command,
            args=list(args or []),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            close_timeout=close_timeout,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunRecord":
        if not isinstance(value, dict):
            raise TypeError("Run record must be a mapping")
        return cls(
            app_id=value["app_id"],
            run_id=value["run_id"],
            path=value.get("path", ""),
            command=value.get("command", ""),
            args=value.get("args") or [],
            keeper_pid=value.get("keeper_pid", 0),
            keeper_created_at=value.get("keeper_created_at", 0.0),
            root_pid=value.get("root_pid", 0),
            root_created_at=value.get("root_created_at", 0.0),
            stdout_path=value.get("stdout_path", ""),
            stderr_path=value.get("stderr_path", ""),
            close_timeout=value.get("close_timeout", 5.0),
            state=value.get("state", "starting"),
            exit_code=value.get("exit_code"),
            created_at=value.get("created_at") or utc_now_iso(),
            updated_at=value.get("updated_at") or utc_now_iso(),
        )

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
