from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

_MIN_LINE_COUNT = 10
_MAX_LINE_COUNT = 5000
_DEFAULT_LINE_COUNT = 100
_DEFAULT_STREAM = "stdout"
_SCHEMA_VERSION = 1
_FILENAME = "ui-log-preferences.json"


@dataclass(frozen=True, slots=True)
class LogPanelPreference:
    line_count: int = _DEFAULT_LINE_COUNT
    filter_expression: str = ""
    stream: str = _DEFAULT_STREAM


def _normalize_line_count(value) -> int:
    if type(value) is int and _MIN_LINE_COUNT <= value <= _MAX_LINE_COUNT:
        return value
    return _DEFAULT_LINE_COUNT


def _normalize_filter_expression(value) -> str:
    return value if isinstance(value, str) else ""


def _normalize_stream(value) -> str:
    return value if value in {"stdout", "stderr"} else _DEFAULT_STREAM


def _normalize_preference(value) -> LogPanelPreference:
    if isinstance(value, LogPanelPreference):
        value = asdict(value)
    if not isinstance(value, dict):
        value = {}
    return LogPanelPreference(
        line_count=_normalize_line_count(value.get("line_count")),
        filter_expression=_normalize_filter_expression(value.get("filter_expression")),
        stream=_normalize_stream(value.get("stream")),
    )


class LogPanelPreferenceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve(strict=False)

    def _read_apps(self) -> tuple[dict[str, LogPanelPreference], bool]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, True
        except (OSError, UnicodeError):
            return {}, False
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}, True
        if not isinstance(payload, dict) or payload.get("version") != _SCHEMA_VERSION:
            return {}, True
        raw_apps = payload.get("apps")
        if not isinstance(raw_apps, dict):
            return {}, True
        return (
            {
                app_id: _normalize_preference(value)
                for app_id, value in raw_apps.items()
                if isinstance(app_id, str)
            },
            True,
        )

    def _load_apps(self) -> dict[str, LogPanelPreference]:
        apps, _readable = self._read_apps()
        return apps

    def load_all(self) -> dict[str, LogPanelPreference]:
        return self._load_apps()

    def load(self, app_id: str) -> LogPanelPreference:
        return self._load_apps().get(str(app_id), LogPanelPreference())

    def save(self, app_id: str, preference: LogPanelPreference) -> bool:
        apps, readable = self._read_apps()
        if not readable:
            return False
        app_id = str(app_id)
        normalized = _normalize_preference(preference)
        if apps.get(app_id, LogPanelPreference()) == normalized:
            return False
        apps[app_id] = normalized
        return self.replace_all(apps)

    def replace_all(self, apps) -> bool:
        normalized_apps = {
            str(app_id): _normalize_preference(preference)
            for app_id, preference in dict(apps).items()
        }
        payload = {
            "version": _SCHEMA_VERSION,
            "apps": {key: asdict(value) for key, value in normalized_apps.items()},
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
            return False
        return True


def default_log_preferences_path(config_path: str | Path) -> Path:
    config = Path(config_path).expanduser().resolve(strict=False)
    return config.parent / ".runtime" / _FILENAME


__all__ = [
    "LogPanelPreference",
    "LogPanelPreferenceStore",
    "default_log_preferences_path",
]
