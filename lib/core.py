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

    def _get_runner(self, app_config, args_override=None):
        runtime_config = dict(app_config)
        if args_override is not None:
            runtime_config["args"] = CommandRunner.normalize_args(args_override)
        return CommandRunner(runtime_config, self.config["global"])

    def list_apps(self):
        return self.config_manager.get_apps()

    def get_app_workdir(self, app_name):
        app = self.config_manager.get_app(app_name)
        return app.get("path") if app else None

    def get_status_snapshot(self, app_ids=None):
        apps = self.config_manager.get_apps()
        by_name = {str(app["name"]): app for app in apps}
        by_id = {str(app.get("id") or app["name"]): app for app in apps}
        requested = tuple(
            dict.fromkeys(
                str(value)
                for value in (by_id.keys() if app_ids is None else app_ids)
            )
        )

        mappings = []
        for value in requested:
            app = by_id.get(value) or by_name.get(value)
            if app is None:
                mappings.append((value, value))
                continue
            stable_id = str(app.get("id") or app["name"])
            mappings.append((stable_id, str(self._session_key(app))))

        backend_keys = tuple(dict.fromkeys(key for _stable_id, key in mappings))
        raw = self.session_manager.get_status_snapshot(backend_keys)
        default = {"status": "STOPPED", "instances": 0}
        return {
            stable_id: dict(raw.get(backend_key) or default)
            for stable_id, backend_key in mappings
        }

    def get_app_status(self, app_name):
        app = self.config_manager.get_app(app_name)
        if not app:
            return {"status": "STOPPED", "instances": 0}
        stable_id = str(app.get("id") or app["name"])
        return self.get_status_snapshot((stable_id,)).get(
            stable_id,
            {"status": "STOPPED", "instances": 0},
        )

    def reconcile(self):
        reconcile = getattr(self.session_manager, "reconcile", None)
        if callable(reconcile):
            reconcile()

    def should_auto_start(self, app_name):
        app = self.config_manager.get_app(app_name)
        if not app or not app.get("enabled", True) or not app.get("auto_start", False):
            return False
        return self.get_app_status(app_name).get("status") == "STOPPED"

    def start_app(self, app_name, *, wait_for_ready=True, args_override=None):
        app = self.config_manager.get_app(app_name)
        if not app:
            print_error(f"App '{app_name}' not found.")
            return False

        try:
            runner = self._get_runner(app, args_override=args_override)
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
