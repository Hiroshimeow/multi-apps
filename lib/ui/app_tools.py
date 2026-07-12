from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AppToolAction:
    id: str
    type: str
    label: str
    path: str


@dataclass(frozen=True, slots=True)
class AppToolActionResult:
    ok: bool
    code: str
    message: str
    target: str | None = None
    argv: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TerminalCommand:
    argv: tuple[str, ...]
    cwd: str | None = None


class TerminalAdapter(Protocol):
    def discover(self, workdir: str) -> TerminalCommand | None: ...

    def launch(self, workdir: str) -> AppToolActionResult: ...
