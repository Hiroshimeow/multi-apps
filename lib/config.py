import os
import re
import unicodedata
from copy import deepcopy
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
        self.load()

    def load(self):
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
        }

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
