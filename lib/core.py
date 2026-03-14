from .config import ConfigManager
from .utils import (
    is_linux,
    is_windows,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from .runners.python_runner import PythonRunner
from .runners.gunicorn_runner import GunicornRunner
from .runners.shell_runner import ShellRunner
from .runners.command_runner import CommandRunner
from .runners.cmd_runner import CmdRunner
from .runners.uv_runner import UvRunner
from .session.tmux_session import TmuxSessionManager
from .session.subprocess_session import SubprocessSessionManager


class AppController:
    def __init__(self, config_path=None):
        self.config_manager = ConfigManager(config_path)
        self.config = self.config_manager.config

        # Initialize Session Manager
        self.session_manager = self._init_session_manager()

    def _init_session_manager(self):
        # Default logic: Linux -> Tmux (if configured), others -> Subprocess
        session_type = self.config_manager.get_global("session", {}).get("type", "tmux")

        if is_linux() and session_type == "tmux":
            return TmuxSessionManager(self.config["global"])

        return SubprocessSessionManager(self.config["global"])

    def _get_runner(self, app_config):
        app_type = app_config.get("type")
        path = app_config.get("path")
        command = app_config.get("command")

        # 1. Auto-detect type if not explicitly set, or if it contradicts fields
        if not app_type:
            if command:
                app_type = "command"
            elif path:
                if path.endswith(".py"):
                    app_type = "python"
                elif any(path.endswith(ext) for ext in [".sh", ".bat", ".cmd", ".vbs"]):
                    app_type = "shell"
                else:
                    app_type = "python"  # Default fallback
            else:
                app_type = "python"

        # 2. Refine type based on content if it was set to 'python' but lacks 'path'
        if app_type == "python" and not path and command:
            app_type = "command"

        # 3. Create Runner
        if app_type == "python":
            return PythonRunner(app_config, self.config["global"])
        elif app_type == "gunicorn":
            return GunicornRunner(app_config, self.config["global"])
        elif app_type == "shell":
            return ShellRunner(app_config, self.config["global"])
        elif app_type == "command":
            return CommandRunner(app_config, self.config["global"])
        elif app_type == "cmd":
            return CmdRunner(app_config, self.config["global"])
        elif app_type in ["uv", "uv run"]:
            return UvRunner(app_config, self.config["global"])

        # Fallback
        print_warning(
            f"Unknown app type '{app_type}' for {app_config['name']}. Defaulting to PythonRunner."
        )
        return PythonRunner(app_config, self.config["global"])

    def list_apps(self):
        return self.config_manager.get_apps()

    def get_app_status(self, app_name):
        if not self.session_manager:
            return {"status": "UNKNOWN"}
        info = self.session_manager.get_info(app_name)
        return info

    def start_app(self, app_name):
        app_config = self.config_manager.get_app(app_name)
        if not app_config:
            print_error(f"App '{app_name}' not found.")
            return False

        if not self.session_manager:
            print_error("Session manager not initialized.")
            return False

        runner = self._get_runner(app_config)
        try:
            success, msg = self.session_manager.start(runner)
            if success:
                print_success(f"Started '{app_name}': {msg}")
                return True
            else:
                print_error(f"Failed to start '{app_name}': {msg}")
                return False
        except Exception as e:
            print_error(f"Error starting '{app_name}': {e}")
            return False

    def stop_app(self, app_name):
        if not self.session_manager:
            return False

        success, msg = self.session_manager.stop(app_name)
        if success:
            print_success(f"Stopped '{app_name}': {msg}")
        else:
            print_warning(f"Could not stop '{app_name}': {msg}")
        return success

    def start_all(self):
        apps = self.config_manager.get_apps(enabled_only=True)
        for app in apps:
            self.start_app(app["name"])

    def stop_all(self):
        apps = self.config_manager.get_apps(enabled_only=True)
        for app in apps:
            self.stop_app(app["name"])
