from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class AppToolActionResult:
    ok: bool
    code: str
    message: str
    target: str | None = None
    argv: tuple[str, ...] = ()


def _canonical_path(value: str | os.PathLike[str] | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(Path(os.path.expanduser(str(value))).resolve(strict=False))


class AppToolService:
    """Open folders, files, and terminals without changing process-wide cwd."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        which: Callable[[str], str | None] = shutil.which,
        start_file: Callable[[str], object] | None = None,
        process_launcher: Callable[..., object] = subprocess.Popen,
    ) -> None:
        self.platform_name = platform_name or platform.system()
        self.which = which
        self.start_file = (
            start_file if start_file is not None else getattr(os, "startfile", None)
        )
        self.process_launcher = process_launcher

    @property
    def _platform_key(self) -> str:
        return self.platform_name.casefold()

    def _unsupported(self, target: str | None) -> AppToolActionResult:
        return AppToolActionResult(
            False,
            "UNSUPPORTED_PLATFORM",
            f"File actions are unsupported on platform: {self.platform_name}.",
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

    def file_status(self, file_path: str | None) -> AppToolActionResult:
        target = _canonical_path(file_path)
        if self._platform_key not in {"windows", "linux"}:
            return self._unsupported(target)
        if target is None or not Path(target).exists():
            return AppToolActionResult(
                False,
                "TARGET_MISSING",
                f"File does not exist: {target or file_path}",
                target=target or file_path,
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

    def _terminal_argv(self, workdir: str) -> tuple[str, ...]:
        if self._platform_key == "windows":
            resolved = self.which("wt.exe")
            if resolved:
                return (resolved, "-d", workdir)
            for candidate in ("pwsh.exe", "powershell.exe"):
                resolved = self.which(candidate)
                if resolved:
                    return (resolved, "-NoProfile", "-NoExit")
            resolved = self.which("cmd.exe")
            if resolved:
                return (resolved, "/D", "/K")
            return ()
        if self._platform_key == "linux":
            candidates = (
                ("x-terminal-emulator", ()),
                ("gnome-terminal", ("--working-directory", workdir)),
                ("konsole", ("--workdir", workdir)),
                ("xfce4-terminal", ("--working-directory", workdir)),
                ("xterm", ()),
            )
            for candidate, suffix in candidates:
                executable = self.which(candidate)
                if executable:
                    return (executable, *suffix)
        return ()

    def terminal_status(self, workdir: str | None) -> AppToolActionResult:
        folder = self.folder_status(workdir)
        if not folder.ok:
            return folder
        argv = self._terminal_argv(folder.target)
        if not argv:
            return AppToolActionResult(
                False,
                "TERMINAL_UNAVAILABLE",
                "No supported terminal executable was found.",
                target=folder.target,
            )
        return AppToolActionResult(
            True,
            "READY",
            f"Open terminal in: {folder.target}",
            target=folder.target,
            argv=argv,
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

    def open_file(self, file_path: str | None) -> AppToolActionResult:
        status = self.file_status(file_path)
        if not status.ok:
            return status
        return self._open_target(status.target)

    def open_terminal(self, workdir: str | None) -> AppToolActionResult:
        status = self.terminal_status(workdir)
        if not status.ok:
            return status
        try:
            executable = Path(status.argv[0]).name.casefold()
            kwargs = {}
            if self._platform_key == "windows":
                if executable in {"pwsh.exe", "powershell.exe", "cmd.exe"}:
                    kwargs["cwd"] = status.target
                    kwargs["creationflags"] = getattr(
                        subprocess,
                        "CREATE_NEW_CONSOLE",
                        0,
                    )
                else:
                    kwargs.update(
                        {
                            "stdin": subprocess.DEVNULL,
                            "stdout": subprocess.DEVNULL,
                            "stderr": subprocess.DEVNULL,
                        }
                    )
            else:
                if executable in {"x-terminal-emulator", "xterm"}:
                    kwargs["cwd"] = status.target
                kwargs.update(
                    {
                        "stdin": subprocess.DEVNULL,
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                        "start_new_session": True,
                    }
                )
            self.process_launcher(list(status.argv), **kwargs)
            return AppToolActionResult(
                True,
                "TERMINAL_OPENED",
                f"Opened terminal in: {status.target}",
                target=status.target,
                argv=status.argv,
            )
        except Exception as exc:
            return AppToolActionResult(
                False,
                "LAUNCH_FAILED",
                f"Failed to open terminal: {exc}",
                target=status.target,
                argv=status.argv,
            )
