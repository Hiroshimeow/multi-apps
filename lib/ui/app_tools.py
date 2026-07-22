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
    """Open launcher folders and files without changing process-wide cwd."""

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
