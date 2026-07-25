from __future__ import annotations

import threading
import time

from .log_preferences import LogPanelPreference, _normalize_preference


class LogPreferenceOwner:
    """In-memory preference cache with one coalescing persistence worker."""

    def __init__(self, backend, *, debounce_seconds=0.75):
        self.backend = backend
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self._cache = {}
        self._loaded = False
        self._condition = threading.Condition()
        self._generation = 0
        self._persisted_generation = 0
        self._attempted_generation = 0
        self._deadline = 0.0
        self._closing = False
        self._thread = None

    def _ensure_started(self):
        with self._condition:
            if not self._loaded:
                try:
                    loaded = self.backend.load_all()
                except Exception:
                    loaded = {}
                self._cache = {
                    str(app_id): _normalize_preference(preference)
                    for app_id, preference in dict(loaded).items()
                }
                self._loaded = True
            if self._thread is None or not self._thread.is_alive():
                self._closing = False
                self._attempted_generation = self._persisted_generation
                self._thread = threading.Thread(
                    target=self._run,
                    name="log-preference-writer",
                    daemon=True,
                )
                self._thread.start()

    def load(self, app_id: str) -> LogPanelPreference:
        self._ensure_started()
        with self._condition:
            return self._cache.get(str(app_id), LogPanelPreference())

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

    def _run(self):
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
                if final_attempt:
                    return

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

    def shutdown(self, timeout=5.0):
        with self._condition:
            thread = self._thread
            if thread is None:
                return
            self._closing = True
            self._condition.notify_all()
        thread.join(timeout=float(timeout))
        with self._condition:
            if self._thread is thread and not thread.is_alive():
                self._thread = None
                self._closing = False
                self._attempted_generation = self._persisted_generation

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()


__all__ = ["LogPreferenceOwner"]
