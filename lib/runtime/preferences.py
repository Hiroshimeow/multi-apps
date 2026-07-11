from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..utils import print_warning
from .registry import atomic_write_json

ACTIONS = frozenset({"restart", "exit"})
CHOICES = frozenset({"ask", "close", "keep"})


class RuntimePreferences:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._data: dict[str, dict[str, str]] = {}
        self.load()

    def load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            self._data = {}
            return self.as_dict()

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            self._data = self._normalize(raw)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            corrupt_path = self._move_corrupt_file()
            print_warning(
                f"Invalid runtime preferences moved to '{corrupt_path.name}': {exc}"
            )
            self._data = {}
        return self.as_dict()

    def _move_corrupt_file(self) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        corrupt_path = self.path.with_name(f"{self.path.name}.corrupt-{timestamp}")
        try:
            self.path.replace(corrupt_path)
        except OSError:
            return self.path
        return corrupt_path

    @staticmethod
    def _normalize(raw: object) -> dict[str, dict[str, str]]:
        if not isinstance(raw, dict):
            raise ValueError("Preference file must contain an object")

        normalized: dict[str, dict[str, str]] = {}
        for raw_app_id, raw_actions in raw.items():
            app_id = str(raw_app_id).strip()
            if not app_id or not isinstance(raw_actions, dict):
                continue
            actions = {
                action: choice
                for action, choice in raw_actions.items()
                if action in ACTIONS and choice in CHOICES
            }
            if actions:
                normalized[app_id] = actions
        return normalized

    def save(self) -> None:
        atomic_write_json(self.path, self._data)

    def get(self, app_id: str, action: str) -> str:
        self._validate_action(action)
        return self._data.get(app_id, {}).get(action, "ask")

    def set(self, app_id: str, action: str, choice: str) -> None:
        self._validate_action(action)
        if choice not in CHOICES:
            raise ValueError(f"Invalid shutdown choice: {choice}")
        app_id = str(app_id).strip()
        if not app_id:
            raise ValueError("app_id is required")
        self._data.setdefault(app_id, {})[action] = choice
        self.save()

    def clear(
        self,
        app_ids: Iterable[str] | None = None,
        action: str | None = None,
    ) -> None:
        if action is not None:
            self._validate_action(action)
        selected = set(self._data) if app_ids is None else {str(value) for value in app_ids}
        for app_id in selected:
            if app_id not in self._data:
                continue
            if action is None:
                self._data.pop(app_id, None)
            else:
                self._data[app_id].pop(action, None)
                if not self._data[app_id]:
                    self._data.pop(app_id, None)
        self.save()

    def as_dict(self) -> dict[str, dict[str, str]]:
        return {app_id: dict(actions) for app_id, actions in self._data.items()}

    @staticmethod
    def _validate_action(action: str) -> None:
        if action not in ACTIONS:
            raise ValueError(f"Invalid shutdown action: {action}")
