import yaml
import os
from copy import deepcopy
from typing import Dict, Any
from .utils import print_error, print_warning, resolve_path, is_linux, is_windows

DEFAULT_CONFIG_PATH = "setting.yaml"

DEFAULT_GLOBAL_CONFIG = {
    "default_conda_env": "base",
    "log": {"dir": "./logs", "rotate_days": 7, "format": "{name}_{date}.log"},
    "session": {
        "type": "tmux",
        "tmux": {"prefix": "app-", "socket": None},
        "supervisor": {"config_dir": "/etc/supervisor/conf.d", "auto_generate": True},
    },
    "multi_run": False,
    "interactive": True,
    "plugins": {"health_check": False, "auto_restart": False, "notifications": False},
}


class ConfigManager:
    def __init__(self, config_path=None):
        # Use provided path or default, resolve relative to script dir
        if config_path is None:
            # Default: look for setting.yaml in the same directory as this module
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(script_dir, DEFAULT_CONFIG_PATH)

        self.config_path = os.path.abspath(resolve_path(config_path) or config_path)
        self.config_dir = os.path.dirname(self.config_path)
        self.config = {}
        self.load()

    def load(self):
        if not os.path.exists(self.config_path):
            print_warning(
                f"Config file not found at {self.config_path}. Using defaults."
            )
            self.config = {"global": DEFAULT_GLOBAL_CONFIG, "apps": []}
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f) or {}

            self.config = self._normalize_config(raw_config)

        except Exception as e:
            print_error(f"Failed to load config: {e}")
            self.config = {"global": DEFAULT_GLOBAL_CONFIG, "apps": []}

    def _normalize_config(self, raw_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize config to match new schema while maintaining backward compatibility.
        """
        normalized = {}

        # 1. Normalize Global Settings
        # Legacy: 'default_conda_env' at root
        # New: 'global' -> 'default_conda_env'

        global_cfg = deepcopy(DEFAULT_GLOBAL_CONFIG)

        # Merge existing global config if present
        if "global" in raw_config:
            self._deep_update(global_cfg, raw_config["global"])

        # Handle legacy root keys
        if "default_conda_env" in raw_config:
            global_cfg["default_conda_env"] = raw_config["default_conda_env"]
        if "multi_run" in raw_config:
            global_cfg["multi_run"] = raw_config["multi_run"]
        global_cfg["_config_dir"] = self.config_dir

        normalized["global"] = global_cfg

        # 2. Normalize Apps
        apps = []
        raw_apps = raw_config.get("apps", [])

        for app in raw_apps:
            norm_app = self._normalize_app(app, global_cfg)
            if norm_app:
                apps.append(norm_app)

        normalized["apps"] = apps
        return normalized

    def _normalize_app(
        self, app: Dict[str, Any], global_cfg: Dict[str, Any]
    ) -> Dict[str, Any] | None:
        name = app.get("name")
        path = app.get("path")
        command = app.get("command")

        if not name:
            print_warning(f"Skipping app config with missing name: {app}")
            return None

        # Sanitize name to prevent path traversal
        name = str(name).replace("..", "").replace("/", "_").replace("\\", "_")

        if not path and not command:
            print_warning(f"Skipping app '{name}': missing both path and command.")
            return None

        # Defaults
        path = resolve_path(path, self.config_dir) if path else None
        workdir = app.get("workdir")
        if workdir:
            workdir = resolve_path(workdir, self.config_dir)

        env = app.get("env")
        if isinstance(env, dict) and env.get("type") == "venv" and env.get("path"):
            env = {**env, "path": resolve_path(env["path"], self.config_dir)}

        norm = {
            "name": name,
            "type": app.get("type", "python"),  # Default type is python (legacy)
            "path": path,
            "command": app.get("command"),
            "enabled": app.get("enabled", True),
            "multi_run": app.get("multi_run", global_cfg["multi_run"]),
            "background": app.get("background", True),
            "workdir": workdir,
            "env": env,
            "args": app.get("args") or [],
            "env_vars": app.get("env_vars") or {},
            "session": app.get("session"),  # Override global session type
            # Specific configs
            "gunicorn": app.get("gunicorn") or {},
            "module": app.get("module"),  # for gunicorn
            # Future plugins
            "auto_start": app.get("auto_start", False),
            "health_check": app.get("health_check"),
            "depends_on": app.get("depends_on") or [],
            "start_delay": app.get("start_delay", 0),
            "os": app.get("os"),
        }

        # Resolve Workdir
        if not norm["workdir"] and norm["path"]:
            # If path is a file, workdir is parent
            # If path is dir (e.g. gunicorn root), workdir is path
            path_obj = resolve_path(norm["path"], self.config_dir)
            if path_obj and os.path.isdir(path_obj):
                norm["workdir"] = path_obj
            elif path_obj:
                norm["workdir"] = os.path.dirname(path_obj)

        return norm

    def _deep_update(self, base_dict, update_dict):
        for k, v in update_dict.items():
            if (
                isinstance(v, dict)
                and k in base_dict
                and isinstance(base_dict[k], dict)
            ):
                self._deep_update(base_dict[k], v)
            else:
                base_dict[k] = v

    def get_global(self, key, default=None):
        return self.config.get("global", {}).get(key, default)

    def get_log_dir(self):
        log_dir = self.get_global("log", {}).get("dir", "./logs")
        return resolve_path(log_dir, self.config_dir)

    def get_apps(self, enabled_only=False):
        apps = self.config.get("apps", [])

        # Determine current OS
        current_os = "windows" if is_windows() else "linux" if is_linux() else "unknown"

        filtered_apps = []
        for app in apps:
            if enabled_only and not app["enabled"]:
                continue

            # Filter by OS if specified
            allowed_os = app.get("os")
            if allowed_os:
                # Normalize to list
                if isinstance(allowed_os, str):
                    allowed_os = [allowed_os]

                # Normalize items to lower case
                allowed_os = [str(x).lower() for x in allowed_os]

                is_visible = False

                # Check Windows
                if current_os == "windows":
                    if any(
                        x in ["win", "windows", "win10", "win11"] for x in allowed_os
                    ):
                        is_visible = True

                # Check Linux
                elif current_os == "linux":
                    if any(x in ["linux", "ubuntu", "debian"] for x in allowed_os):
                        is_visible = True

                # If OS is specified but doesn't match current, skip
                if not is_visible:
                    continue

            filtered_apps.append(app)

        return filtered_apps

    def get_app(self, name):
        for app in self.config.get("apps", []):
            if app["name"] == name:
                return app
        return None
