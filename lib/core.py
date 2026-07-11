from .config import ConfigManager
from .runners.command_runner import CommandRunner
from .session.subprocess_session import SubprocessSessionManager
from .session.tmux_session import TmuxSessionManager
from .utils import is_linux, print_error, print_success, print_warning


class AppController:
    def __init__(self, config_path=None):
        self.config_manager = ConfigManager(config_path)
        self.config = self.config_manager.config
        self.session_manager = self._init_session_manager()

    def _init_session_manager(self):
        session_type = self.config_manager.get_global("session", "subprocess")
        if is_linux() and session_type == "tmux":
            return TmuxSessionManager(self.config["global"])
        return SubprocessSessionManager(self.config["global"])

    def _get_runner(self, app_config):
        return CommandRunner(app_config, self.config["global"])

    def list_apps(self):
        return self.config_manager.get_apps()

    def get_app_workdir(self, app_name):
        app = self.config_manager.get_app(app_name)
        return app.get("path") if app else None

    def get_app_status(self, app_name):
        app = self.config_manager.get_app(app_name)
        if not app:
            return {"status": "STOPPED"}
        return self.session_manager.get_info(self._session_key(app))

    def start_app(self, app_name, *, wait_for_ready=True):
        app = self.config_manager.get_app(app_name)
        if not app:
            print_error(f"App '{app_name}' not found.")
            return False

        try:
            runner = self._get_runner(app)
            if isinstance(self.session_manager, SubprocessSessionManager):
                success, message = self.session_manager.start(
                    runner,
                    wait_for_ready=wait_for_ready,
                )
            else:
                success, message = self.session_manager.start(runner)
        except Exception as exc:
            print_error(f"Error starting '{app_name}': {exc}")
            return False

        if success:
            print_success(f"Started '{app_name}': {message}")
            return True

        print_error(f"Failed to start '{app_name}': {message}")
        return False

    def stop_app(self, app_name):
        app = self.config_manager.get_app(app_name)
        if not app:
            print_warning(f"Could not stop '{app_name}': app not found.")
            return False
        success, message = self.session_manager.stop(self._session_key(app))
        if success:
            print_success(f"Stopped '{app_name}': {message}")
        else:
            print_warning(f"Could not stop '{app_name}': {message}")
        return success

    def start_all(self, *, wait_for_ready=True):
        for app in self.config_manager.get_apps(enabled_only=True):
            self.start_app(app["name"], wait_for_ready=wait_for_ready)

    def stop_all(self):
        for app in self.config_manager.get_apps(enabled_only=True):
            self.stop_app(app["name"])

    def _session_key(self, app):
        if isinstance(self.session_manager, SubprocessSessionManager):
            return app["id"]
        return app["name"]
