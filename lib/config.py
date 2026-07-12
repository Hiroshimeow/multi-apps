import os
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .utils import is_linux, is_windows, print_error, print_warning, resolve_path

DEFAULT_CONFIG_PATH = "setting.yaml"
DEFAULT_GLOBAL_CONFIG = {
    "log_dir": "./logs",
    "session": "subprocess",
    "multi_run": False,
}
DEFAULT_CLOSE_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class ConfigWarning:
    code: str
    app_id: str | None
    app_name: str | None
    tool_index: int | None
    detail: str | None = None


def slugify_app_id(value: str) -> str:
    text = str(value).translate(str.maketrans({"Đ": "D", "đ": "d"}))
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "app"


class ConfigManager:
    def __init__(self, config_path=None):
        if config_path is None:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(root, DEFAULT_CONFIG_PATH)

        self.config_path = os.path.abspath(resolve_path(config_path) or config_path)
        self.config_dir = os.path.dirname(self.config_path)
        self.config = {}
        self.warnings: list[ConfigWarning] = []
        self.load()

    def load(self):
        self.warnings = []
        if not os.path.exists(self.config_path):
            print_warning(f"Config file not found: {self.config_path}")
            self.config = self._empty_config()
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                raw_config = yaml.safe_load(handle) or {}
            if not isinstance(raw_config, dict):
                raise ValueError("Top-level config must be a mapping.")
            self.config = self._normalize_config(raw_config)
        except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
            print_error(f"Failed to load config: {exc}")
            self.config = self._empty_config()

    def _empty_config(self):
        global_config = deepcopy(DEFAULT_GLOBAL_CONFIG)
        global_config["_config_dir"] = self.config_dir
        return {"global": global_config, "apps": []}

    def _normalize_config(self, raw_config: dict[str, Any]):
        raw_global = raw_config.get("global") or {}
        global_config = deepcopy(DEFAULT_GLOBAL_CONFIG)
        for key in DEFAULT_GLOBAL_CONFIG:
            if key in raw_global:
                global_config[key] = raw_global[key]
        global_config["_config_dir"] = self.config_dir

        apps = []
        seen_ids = set()
        for raw_app in raw_config.get("apps") or []:
            app = self._normalize_app(raw_app, global_config)
            if not app:
                continue
            app_id = app["id"]
            if app_id in seen_ids:
                print_warning(f"Skipping app '{app['name']}': duplicate id '{app_id}'.")
                continue
            seen_ids.add(app_id)
            apps.append(app)

        return {"global": global_config, "apps": apps}

    def _normalize_app(self, raw_app: dict[str, Any], global_config: dict[str, Any]):
        if not isinstance(raw_app, dict):
            print_warning(f"Skipping invalid app config: {raw_app!r}")
            return None

        name = str(raw_app.get("name") or "").strip()
        command = str(raw_app.get("command") or "").strip()
        if not name or not command:
            print_warning("Skipping app: both 'name' and 'command' are required.")
            return None

        safe_name = name.replace("..", "").replace("/", "_").replace("\\", "_")
        raw_id = str(raw_app.get("id") or "").strip()
        app_id = raw_id or slugify_app_id(name)
        path = resolve_path(raw_app.get("path") or self.config_dir, self.config_dir)
        tools = self._normalize_tools(
            raw_app.get("tools"),
            app_id=app_id,
            app_name=safe_name,
            workdir=path,
        )

        raw_args = raw_app.get("args") or []
        if isinstance(raw_args, str):
            args = [raw_args]
        elif isinstance(raw_args, list):
            args = [str(value) for value in raw_args if str(value).strip()]
        else:
            print_warning(f"Ignoring invalid args for '{safe_name}': expected string or list.")
            args = []

        raw_close_timeout = raw_app.get("close_timeout", DEFAULT_CLOSE_TIMEOUT)
        try:
            close_timeout = float(raw_close_timeout)
            if close_timeout <= 0:
                raise ValueError
        except (TypeError, ValueError):
            print_warning(
                f"Invalid close_timeout for '{safe_name}'; using {DEFAULT_CLOSE_TIMEOUT}."
            )
            close_timeout = DEFAULT_CLOSE_TIMEOUT

        return {
            "id": app_id,
            "name": safe_name,
            "path": path,
            "command": command,
            "args": args,
            "enabled": bool(raw_app.get("enabled", True)),
            "auto_start": bool(raw_app.get("auto_start", False)),
            "multi_run": bool(raw_app.get("multi_run", global_config["multi_run"])),
            "args_edit": bool(raw_app.get("args_edit", False)),
            "close_timeout": close_timeout,
            "os": raw_app.get("os"),
            "tools": tools,
        }

    def _record_tool_warning(
        self,
        code: str,
        *,
        app_id: str,
        app_name: str,
        tool_index: int | None,
        detail: str | None = None,
        message: str,
    ) -> None:
        self.warnings.append(
            ConfigWarning(
                code=code,
                app_id=app_id,
                app_name=app_name,
                tool_index=tool_index,
                detail=detail,
            )
        )
        print_warning(message)

    def _normalize_tools(
        self,
        raw_tools: Any,
        *,
        app_id: str,
        app_name: str,
        workdir: str,
    ) -> list[dict[str, str]]:
        if raw_tools is None:
            return []
        if not isinstance(raw_tools, list):
            self._record_tool_warning(
                "TOOLS_INVALID_CONTAINER",
                app_id=app_id,
                app_name=app_name,
                tool_index=None,
                message=f"Ignoring tools for '{app_name}': expected a list.",
            )
            return []

        normalized: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for index, raw_tool in enumerate(raw_tools):
            if not isinstance(raw_tool, dict):
                self._record_tool_warning(
                    "TOOL_INVALID_ENTRY",
                    app_id=app_id,
                    app_name=app_name,
                    tool_index=index,
                    message=f"Ignoring tool {index} for '{app_name}': expected a mapping.",
                )
                continue

            raw_type = raw_tool.get("type")
            tool_type = str(raw_type or "").strip().lower()
            if not tool_type:
                self._record_tool_warning(
                    "TOOL_MISSING_TYPE",
                    app_id=app_id,
                    app_name=app_name,
                    tool_index=index,
                    message=f"Ignoring tool {index} for '{app_name}': type is required.",
                )
                continue
            if tool_type != "open_file":
                self._record_tool_warning(
                    "TOOL_UNKNOWN_TYPE",
                    app_id=app_id,
                    app_name=app_name,
                    tool_index=index,
                    detail=tool_type,
                    message=(
                        f"Ignoring tool {index} for '{app_name}': "
                        f"unknown type '{tool_type}'."
                    ),
                )
                continue

            label = str(raw_tool.get("label") or "").strip()
            if not label:
                self._record_tool_warning(
                    "TOOL_EMPTY_LABEL",
                    app_id=app_id,
                    app_name=app_name,
                    tool_index=index,
                    message=f"Ignoring tool {index} for '{app_name}': label is required.",
                )
                continue

            raw_path = str(raw_tool.get("path") or "").strip()
            if not raw_path:
                self._record_tool_warning(
                    "TOOL_EMPTY_PATH",
                    app_id=app_id,
                    app_name=app_name,
                    tool_index=index,
                    message=f"Ignoring tool {index} for '{app_name}': path is required.",
                )
                continue

            raw_id = raw_tool.get("id")
            if raw_id is None or (isinstance(raw_id, str) and not raw_id.strip()):
                tool_id = f"{tool_type}-{slugify_app_id(label)}"
            elif isinstance(raw_id, str):
                tool_id = raw_id.strip()
            else:
                detail = f"{type(raw_id).__name__}: {raw_id!r}"
                self._record_tool_warning(
                    "TOOL_INVALID_ID",
                    app_id=app_id,
                    app_name=app_name,
                    tool_index=index,
                    detail=detail,
                    message=(
                        f"Ignoring tool {index} for '{app_name}': "
                        f"id must be a string ({detail})."
                    ),
                )
                continue

            if tool_id in seen_ids:
                self._record_tool_warning(
                    "TOOL_DUPLICATE_ID",
                    app_id=app_id,
                    app_name=app_name,
                    tool_index=index,
                    detail=tool_id,
                    message=(
                        f"Ignoring tool {index} for '{app_name}': "
                        f"duplicate id '{tool_id}'."
                    ),
                )
                continue

            expanded = Path(os.path.expanduser(raw_path))
            if not expanded.is_absolute():
                expanded = Path(workdir) / expanded
            resolved_path = str(expanded.resolve(strict=False))

            seen_ids.add(tool_id)
            normalized.append(
                {
                    "id": tool_id,
                    "type": tool_type,
                    "label": label,
                    "path": resolved_path,
                }
            )

        return normalized

    def get_global(self, key, default=None):
        return self.config.get("global", {}).get(key, default)

    def get_log_dir(self):
        return resolve_path(self.get_global("log_dir", "./logs"), self.config_dir)

    def get_apps(self, enabled_only=False):
        current_os = "windows" if is_windows() else "linux" if is_linux() else "unknown"
        result = []

        for app in self.config.get("apps", []):
            if enabled_only and not app["enabled"]:
                continue

            allowed_os = app.get("os")
            if allowed_os:
                values = [allowed_os] if isinstance(allowed_os, str) else allowed_os
                values = {str(value).lower() for value in values}
                windows_names = {"win", "windows", "win10", "win11"}
                linux_names = {"linux", "ubuntu", "debian"}
                if current_os == "windows" and not values.intersection(windows_names):
                    continue
                if current_os == "linux" and not values.intersection(linux_names):
                    continue
                if current_os == "unknown":
                    continue

            result.append(app)

        return result

    def get_app(self, name):
        return next(
            (app for app in self.config.get("apps", []) if app["name"] == name),
            None,
        )

    def get_app_by_id(self, app_id):
        return next(
            (app for app in self.config.get("apps", []) if app["id"] == app_id),
            None,
        )
