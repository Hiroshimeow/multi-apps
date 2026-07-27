from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer


class RuntimeRefreshCoordinator(QObject):
    """Refresh runtime status only while the tray panel is visible."""

    def __init__(
        self,
        runtime_dir,
        refresh_callback: Callable[[], object],
        *,
        debounce_ms: int = 100,
        fallback_ms: int = 3000,
        parent=None,
    ):
        super().__init__(parent)
        self.runtime_dir = Path(runtime_dir).expanduser().resolve(strict=False)
        self.refresh_callback = refresh_callback
        self.debounce_ms = max(1, int(debounce_ms))
        self.fallback_ms = max(1, int(fallback_ms))
        self.is_active = False

        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.request_refresh)
        self.watcher.fileChanged.connect(self.request_refresh)

        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(self.debounce_ms)
        self.debounce_timer.timeout.connect(self._refresh)

        self.fallback_timer = QTimer(self)
        self.fallback_timer.setInterval(self.fallback_ms)
        self.fallback_timer.timeout.connect(self._refresh)

    def start(self) -> None:
        if self.is_active:
            return
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        path = str(self.runtime_dir)
        if path not in self.watcher.directories():
            self.watcher.addPath(path)
        self.is_active = True
        self.fallback_timer.start()

    def stop(self) -> None:
        self.is_active = False
        self.debounce_timer.stop()
        self.fallback_timer.stop()
        paths = self.watcher.files() + self.watcher.directories()
        if paths:
            self.watcher.removePaths(paths)

    def request_refresh(self, _path: str = "") -> None:
        if self.is_active:
            self.debounce_timer.start(self.debounce_ms)

    def refresh_now(self) -> None:
        if self.is_active:
            self.debounce_timer.stop()
            self._refresh()

    def _refresh(self) -> None:
        if self.is_active:
            self.refresh_callback()
