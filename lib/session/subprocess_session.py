import os
import subprocess
from datetime import datetime

from .base import BaseSessionManager
from ..utils import is_windows, kill_process_tree


class SubprocessSessionManager(BaseSessionManager):
    def __init__(self, global_config):
        super().__init__(global_config)
        self.processes = {}

    def _cleanup_dead(self):
        dead_apps = []
        for name, infos in self.processes.items():
            alive = []
            for info in infos:
                if info["process"].poll() is None:
                    alive.append(info)
                else:
                    self._close_logs(info)
            if alive:
                self.processes[name] = alive
            else:
                dead_apps.append(name)
        for name in dead_apps:
            self.processes.pop(name, None)

    @staticmethod
    def _close_logs(info):
        for key in ("stdout", "stderr"):
            handle = info.get(key)
            if handle:
                try:
                    handle.close()
                except OSError:
                    pass

    def start(self, runner):
        self._cleanup_dead()
        app_name = runner.name

        if self.is_running(app_name) and not runner.app_config.get("multi_run", False):
            return False, f"App '{app_name}' is already running."

        command = runner.build_command()
        workdir = runner.get_workdir()
        if workdir and not os.path.isdir(workdir):
            return False, f"Path is not a directory: {workdir}"
        env = runner.get_env()

        log_dir = self.config.get("log_dir", "./logs")
        config_dir = self.config.get("_config_dir") or os.getcwd()
        if not os.path.isabs(log_dir):
            log_dir = os.path.abspath(os.path.join(config_dir, log_dir))
        os.makedirs(log_dir, exist_ok=True)

        stdout_path = os.path.join(log_dir, f"{app_name}.out.log")
        stderr_path = os.path.join(log_dir, f"{app_name}.err.log")

        try:
            stdout_file = open(stdout_path, "a", encoding="utf-8")
            stderr_file = open(stderr_path, "a", encoding="utf-8")
        except OSError as exc:
            return False, f"Failed to open log files: {exc}"

        creationflags = subprocess.CREATE_NO_WINDOW if is_windows() else 0
        try:
            process = subprocess.Popen(
                command,
                cwd=workdir,
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                shell=True,
            )
        except (OSError, ValueError) as exc:
            stdout_file.close()
            stderr_file.close()
            return False, str(exc)

        self.processes.setdefault(app_name, []).append(
            {
                "process": process,
                "start_time": datetime.now(),
                "pid": process.pid,
                "stdout": stdout_file,
                "stderr": stderr_file,
            }
        )
        return True, f"Started with PID {process.pid}"

    def stop(self, app_name):
        infos = self.processes.pop(app_name, None)
        if not infos:
            return False, "App not found in session manager."

        failures = []
        for info in infos:
            process = info["process"]
            try:
                kill_process_tree(process.pid)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append(str(exc))
            finally:
                self._close_logs(info)

        return (False, "; ".join(failures)) if failures else (True, "Stopped.")

    def is_running(self, app_name):
        self._cleanup_dead()
        return bool(self.processes.get(app_name))

    def get_info(self, app_name):
        if not self.is_running(app_name):
            return {"status": "STOPPED"}

        infos = self.processes[app_name]
        info = min(infos, key=lambda item: item["start_time"])
        uptime = datetime.now() - info["start_time"]
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            "status": "RUNNING",
            "pid": info["pid"],
            "instances": len(infos),
            "uptime": f"{uptime.days}d {hours}h {minutes}m {seconds}s",
            "start_time": info["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
        }
