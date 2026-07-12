from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol


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


def _canonical_path(value: str | os.PathLike[str] | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(Path(os.path.expanduser(str(value))).resolve(strict=False))


class SystemTerminalAdapter:
    def __init__(
        self,
        *,
        platform_name: str | None = None,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        process_launcher: Callable[..., object] = subprocess.Popen,
    ) -> None:
        self.platform_name = platform_name or platform.system()
        self.environ = dict(os.environ if environ is None else environ)
        self.which = which
        self.process_launcher = process_launcher

    @property
    def _platform_key(self) -> str:
        return self.platform_name.casefold()

    def discover(self, workdir: str) -> TerminalCommand | None:
        target = _canonical_path(workdir)
        if target is None:
            return None
        if self._platform_key == "windows":
            resolved = self.which("wt.exe")
            if resolved:
                return TerminalCommand((resolved, "-d", target), cwd=None)
            resolved = self.which("powershell.exe")
            if resolved:
                return TerminalCommand(
                    (resolved, "-NoProfile", "-NoExit"),
                    cwd=target,
                )
            resolved = self.which("cmd.exe")
            if resolved:
                return TerminalCommand((resolved, "/D", "/K"), cwd=target)
            return None

        if self._platform_key != "linux":
            return None

        terminal_env = self.environ.get("TERMINAL", "").strip()
        if terminal_env:
            try:
                tokens = shlex.split(terminal_env, posix=True)
            except ValueError:
                tokens = []
            if tokens:
                executable = tokens[0]
                if os.path.isabs(executable):
                    resolved = executable if os.path.isfile(executable) and os.access(executable, os.X_OK) else None
                else:
                    resolved = self.which(executable)
                if resolved:
                    return TerminalCommand((resolved, *tokens[1:]), cwd=target)

        resolved = self.which("x-terminal-emulator")
        if resolved:
            return TerminalCommand((resolved,), cwd=target)

        candidates: tuple[tuple[str, tuple[str, ...], bool], ...] = (
            ("gnome-terminal", ("--working-directory", target), False),
            ("konsole", ("--workdir", target), False),
            ("xfce4-terminal", ("--working-directory", target), False),
            ("kitty", ("--directory", target), False),
            ("alacritty", ("--working-directory", target), False),
            ("wezterm", ("start", "--cwd", target), False),
            ("xterm", (), True),
        )
        for name, suffix, inherit_cwd in candidates:
            resolved = self.which(name)
            if resolved:
                return TerminalCommand(
                    (resolved, *suffix),
                    cwd=target if inherit_cwd else None,
                )
        return None

    def launch(self, workdir: str) -> AppToolActionResult:
        target = _canonical_path(workdir)
        if self._platform_key not in {"windows", "linux"}:
            return AppToolActionResult(
                False,
                "UNSUPPORTED_PLATFORM",
                f"App tools are unsupported on platform: {self.platform_name}.",
                target=target,
            )
        if target is None:
            return AppToolActionResult(
                False,
                "TARGET_MISSING",
                "Application working directory is unavailable.",
            )
        path = Path(target)
        if not path.exists():
            return AppToolActionResult(
                False,
                "TARGET_MISSING",
                f"Folder does not exist: {target}",
                target=target,
            )
        if not path.is_dir():
            return AppToolActionResult(
                False,
                "TARGET_NOT_DIRECTORY",
                f"Working directory is not a directory: {target}",
                target=target,
            )

        command = self.discover(target)
        if command is None:
            return AppToolActionResult(
                False,
                "TERMINAL_UNAVAILABLE",
                "No supported terminal was found for this system.",
                target=target,
            )

        kwargs = {"cwd": command.cwd}
        executable_name = Path(command.argv[0]).name.casefold()
        if self._platform_key == "windows" and executable_name in {
            "powershell.exe",
            "pwsh.exe",
            "cmd.exe",
        }:
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        else:
            kwargs.update(
                {
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
            )
            if self._platform_key == "linux":
                kwargs["start_new_session"] = True
        try:
            self.process_launcher(list(command.argv), **kwargs)
        except Exception as exc:
            return AppToolActionResult(
                False,
                "LAUNCH_FAILED",
                f"Failed to open terminal: {exc}",
                target=target,
                argv=command.argv,
            )
        return AppToolActionResult(
            True,
            "TERMINAL_OPENED",
            f"Opened terminal in: {target}",
            target=target,
            argv=command.argv,
        )


class AppToolService:
    def __init__(
        self,
        *,
        platform_name: str | None = None,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        start_file: Callable[[str], object] | None = None,
        process_launcher: Callable[..., object] = subprocess.Popen,
        terminal_adapter: TerminalAdapter | None = None,
    ) -> None:
        self.platform_name = platform_name or platform.system()
        self.environ = dict(os.environ if environ is None else environ)
        self.which = which
        self.start_file = start_file if start_file is not None else getattr(os, "startfile", None)
        self.process_launcher = process_launcher
        self.terminal_adapter = terminal_adapter or SystemTerminalAdapter(
            platform_name=self.platform_name,
            environ=self.environ,
            which=self.which,
            process_launcher=self.process_launcher,
        )

    @property
    def _platform_key(self) -> str:
        return self.platform_name.casefold()

    def _unsupported(self, target: str | None) -> AppToolActionResult:
        return AppToolActionResult(
            False,
            "UNSUPPORTED_PLATFORM",
            f"App tools are unsupported on platform: {self.platform_name}.",
            target=target,
        )

    def folder_status(self, workdir: str | None) -> AppToolActionResult:
        target = _canonical_path(workdir)
        if self._platform_key not in {"windows", "linux"}:
            return self._unsupported(target)
        if target is None:
            return AppToolActionResult(
                False,
                "TARGET_MISSING",
                "Application working directory is unavailable.",
            )
        path = Path(target)
        if not path.exists():
            return AppToolActionResult(
                False,
                "TARGET_MISSING",
                f"Folder does not exist: {target}",
                target=target,
            )
        if not path.is_dir():
            return AppToolActionResult(
                False,
                "TARGET_NOT_DIRECTORY",
                f"Working directory is not a directory: {target}",
                target=target,
            )
        return AppToolActionResult(
            True,
            "READY",
            f"Open folder: {target}",
            target=target,
        )

    def terminal_status(self, workdir: str | None) -> AppToolActionResult:
        folder = self.folder_status(workdir)
        if not folder.ok:
            return folder
        command = self.terminal_adapter.discover(folder.target)
        if command is None:
            return AppToolActionResult(
                False,
                "TERMINAL_UNAVAILABLE",
                "No supported terminal was found for this system.",
                target=folder.target,
            )
        return AppToolActionResult(
            True,
            "READY",
            f"Open terminal in: {folder.target}",
            target=folder.target,
            argv=command.argv,
        )

    def file_status(self, action: AppToolAction) -> AppToolActionResult:
        if action.type != "open_file":
            return AppToolActionResult(
                False,
                "UNSUPPORTED_TOOL_TYPE",
                f"Unsupported app tool type: {action.type}.",
                target=action.path,
            )
        target = _canonical_path(action.path)
        if self._platform_key not in {"windows", "linux"}:
            return self._unsupported(target)
        if target is None or not Path(target).exists():
            return AppToolActionResult(
                False,
                "TARGET_MISSING",
                f"File does not exist: {target or action.path}",
                target=target or action.path,
            )
        if not Path(target).is_file():
            return AppToolActionResult(
                False,
                "TARGET_NOT_FILE",
                f"Configured target is not a file: {target}",
                target=target,
            )
        return AppToolActionResult(
            True,
            "READY",
            f"Open file: {target}",
            target=target,
        )

    def _open_target(self, target: str) -> AppToolActionResult:
        try:
            if self._platform_key == "windows":
                if self.start_file is None:
                    raise OSError("os.startfile is unavailable")
                self.start_file(target)
                return AppToolActionResult(
                    True,
                    "OPENED",
                    f"Opened: {target}",
                    target=target,
                )
            if self._platform_key == "linux":
                opener = self.which("xdg-open")
                if not opener:
                    return AppToolActionResult(
                        False,
                        "LAUNCH_FAILED",
                        "xdg-open is unavailable.",
                        target=target,
                    )
                argv = (opener, target)
                self.process_launcher(
                    list(argv),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return AppToolActionResult(
                    True,
                    "OPENED",
                    f"Opened: {target}",
                    target=target,
                    argv=argv,
                )
            return self._unsupported(target)
        except Exception as exc:
            return AppToolActionResult(
                False,
                "LAUNCH_FAILED",
                f"Failed to open target: {exc}",
                target=target,
            )

    def open_folder(self, workdir: str | None) -> AppToolActionResult:
        status = self.folder_status(workdir)
        if not status.ok:
            return status
        return self._open_target(status.target)

    def open_terminal(self, workdir: str | None) -> AppToolActionResult:
        status = self.terminal_status(workdir)
        if not status.ok:
            return status
        return self.terminal_adapter.launch(status.target)

    def open_file(self, action: AppToolAction) -> AppToolActionResult:
        status = self.file_status(action)
        if not status.ok:
            return status
        return self._open_target(status.target)
