from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass(frozen=True, slots=True)
class _Command:
    key: str
    callback: object


class BackgroundCommandExecutor(QObject):
    """One bounded worker for blocking launcher commands with key deduplication."""

    finished = pyqtSignal(object, bool, object)

    def __init__(self, *, max_pending=64, parent=None):
        super().__init__(parent)
        self.max_pending = max(1, int(max_pending))
        self._condition = threading.Condition()
        self._queue = deque()
        self._keys = set()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="launcher-command-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, key, callback):
        normalized = str(key)
        with self._condition:
            if self._closed or normalized in self._keys:
                return False
            if len(self._queue) >= self.max_pending:
                return False
            self._keys.add(normalized)
            self._queue.append(_Command(normalized, callback))
            self._condition.notify()
            return True

    def _run(self):
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if self._closed and not self._queue:
                    return
                command = self._queue.popleft()
            try:
                result = command.callback()
                ok = result is not False
            except Exception as exc:
                result = exc
                ok = False
            with self._condition:
                self._keys.discard(command.key)
            self.finished.emit(command.key, ok, result)

    def is_pending(self, key):
        with self._condition:
            return str(key) in self._keys

    def shutdown(self, timeout=5.0):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._keys.clear()
            self._condition.notify_all()
        self._thread.join(timeout=float(timeout))

    def is_alive(self):
        return self._thread.is_alive()


class LifecycleCommandController(QObject):
    """Qt-facing nonblocking lifecycle command coordinator."""

    pending_changed = pyqtSignal(str, bool)
    command_finished = pyqtSignal(str, bool, object)

    def __init__(self, *, executor=None, parent=None):
        super().__init__(parent)
        self.executor = executor or BackgroundCommandExecutor(parent=self)
        self._pending = set()
        self._shutdown = False
        self.executor.finished.connect(self._on_finished)

    @staticmethod
    def _app_id(manager):
        return str(getattr(manager, "app_id", None) or getattr(manager, "name", ""))

    def request_stop(self, manager):
        if self._shutdown:
            return False
        app_id = self._app_id(manager)
        if not app_id or app_id in self._pending:
            return False
        self._pending.add(app_id)
        accepted = self.executor.submit(app_id, manager.stop_all)
        if not accepted:
            self._pending.discard(app_id)
            return False
        self.pending_changed.emit(app_id, True)
        return True

    def request_stop_all(self, managers):
        accepted = 0
        seen = set()
        for manager in managers:
            app_id = self._app_id(manager)
            if app_id in seen:
                continue
            seen.add(app_id)
            accepted += int(self.request_stop(manager))
        return accepted

    def _on_finished(self, app_id, ok, result):
        app_id = str(app_id)
        self._pending.discard(app_id)
        self.pending_changed.emit(app_id, False)
        self.command_finished.emit(app_id, bool(ok), result)

    def is_pending(self, app_id):
        return str(app_id) in self._pending

    def pending_ids(self):
        return tuple(sorted(self._pending))

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        try:
            self.executor.finished.disconnect(self._on_finished)
        except (TypeError, RuntimeError):
            pass
        self._pending.clear()
        self.executor.shutdown()

    def is_alive(self):
        return self.executor.is_alive()


__all__ = ["BackgroundCommandExecutor", "LifecycleCommandController"]
