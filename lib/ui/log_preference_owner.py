from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from .log_preferences import LogPanelPreference, _normalize_preference


class LogPreferenceOwner(QObject):
    """Demand-owned async preference cache and coalescing persistence worker."""

    preference_loaded = pyqtSignal(str, object)

    def __init__(self, backend, *, debounce_seconds=0.75, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self._cache = {}
        self._loaded = False
        self._load_requests = set()
        self._condition = threading.Condition()
        self._generation = 0
        self._persisted_generation = 0
        self._attempted_generation = 0
        self._deadline = 0.0
        self._closing = False
        self._thread = None

    def _ensure_started(self):
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                if self._closing:
                    self._closing = False
                    self._condition.notify_all()
                return
            self._closing = False
            self._attempted_generation = self._persisted_generation
            self._thread = threading.Thread(
                target=self._run,
                name="log-preference-writer",
                daemon=True,
            )
            self._thread.start()

    def load_with_status(self, app_id: str) -> tuple[LogPanelPreference, bool]:
        key = str(app_id)
        self._ensure_started()
        with self._condition:
            if self._loaded:
                return self._cache.get(key, LogPanelPreference()), True
            self._load_requests.add(key)
            self._condition.notify_all()
            return LogPanelPreference(), False

    def load(self, app_id: str) -> LogPanelPreference:
        preference, _loaded = self.load_with_status(app_id)
        return preference

    def save(self, app_id: str, preference: LogPanelPreference) -> bool:
        self._ensure_started()
        normalized = _normalize_preference(preference)
        key = str(app_id)
        with self._condition:
            if self._closing:
                return False
            if self._cache.get(key, LogPanelPreference()) == normalized:
                return False
            self._cache[key] = normalized
            self._generation += 1
            self._deadline = time.monotonic() + self.debounce_seconds
            self._condition.notify_all()
        return True

    def _load_backend(self):
        try:
            loaded = self.backend.load_all()
        except Exception:
            loaded = {}
        normalized = {
            str(app_id): _normalize_preference(preference)
            for app_id, preference in dict(loaded).items()
        }
        with self._condition:
            normalized.update(self._cache)
            self._cache = normalized
            self._loaded = True
            requested = tuple(self._load_requests)
            self._load_requests.clear()
            preferences = {
                key: self._cache.get(key, LogPanelPreference()) for key in requested
            }
            self._condition.notify_all()
        for key, preference in preferences.items():
            self.preference_loaded.emit(key, preference)

    def _run(self):
        worker = threading.current_thread()
        try:
            with self._condition:
                needs_load = not self._loaded
            if needs_load:
                self._load_backend()

            while True:
                with self._condition:
                    while True:
                        if self._closing:
                            if self._generation <= self._persisted_generation:
                                return
                            version = self._generation
                            snapshot = dict(self._cache)
                            final_attempt = True
                            break
                        if self._generation > self._attempted_generation:
                            remaining = self._deadline - time.monotonic()
                            if remaining > 0:
                                self._condition.wait(timeout=remaining)
                                continue
                            version = self._generation
                            snapshot = dict(self._cache)
                            final_attempt = False
                            break
                        self._condition.wait()

                try:
                    persisted = bool(self.backend.replace_all(snapshot))
                except Exception:
                    persisted = False

                with self._condition:
                    self._attempted_generation = max(
                        self._attempted_generation,
                        version,
                    )
                    if persisted:
                        self._persisted_generation = max(
                            self._persisted_generation,
                            version,
                        )
                    self._condition.notify_all()
                    if final_attempt and self._closing:
                        return
        finally:
            with self._condition:
                if self._thread is worker:
                    self._thread = None
                    self._closing = False
                    self._attempted_generation = self._persisted_generation
                    self._condition.notify_all()

    def flush(self, timeout=5.0):
        if self._thread is None:
            return True
        deadline = time.monotonic() + float(timeout)
        with self._condition:
            target = self._generation
            if target <= self._persisted_generation:
                return True
            self._deadline = 0.0
            self._condition.notify_all()
            while (
                self._persisted_generation < target
                and self._attempted_generation < target
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return self._persisted_generation >= target

    def request_shutdown(self) -> None:
        """Request idle worker drain and return without joining the worker."""
        with self._condition:
            if self._thread is None:
                return
            self._closing = True
            self._load_requests.clear()
            self._condition.notify_all()

    def shutdown(self, timeout=5.0):
        with self._condition:
            thread = self._thread
        self.request_shutdown()
        if thread is None:
            return
        thread.join(timeout=float(timeout))
        with self._condition:
            if self._thread is thread and not thread.is_alive():
                self._thread = None
                self._closing = False
                self._attempted_generation = self._persisted_generation

    def is_loaded(self):
        with self._condition:
            return self._loaded

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()


__all__ = ["LogPreferenceOwner"]
