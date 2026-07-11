from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QLockFile


class SingleInstanceLock:
    def __init__(
        self,
        path: str | os.PathLike[str],
        lock_factory: Callable[[str], QLockFile] = QLockFile,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = lock_factory(str(self.path))
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def acquire(self) -> bool:
        if self._acquired:
            return True
        if self._lock.tryLock(0):
            self._acquired = True
            return True
        if self._lock.removeStaleLockFile() and self._lock.tryLock(0):
            self._acquired = True
            return True
        return False

    def release(self) -> None:
        if not self._acquired:
            return
        self._lock.unlock()
        self._acquired = False

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RuntimeError(f"Another launcher holds {self.path}")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
