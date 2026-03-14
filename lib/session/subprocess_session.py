import os
import subprocess
import time
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None

from .base import BaseSessionManager
from ..utils import is_windows, kill_process_tree


class SubprocessSessionManager(BaseSessionManager):
    def __init__(self, global_config):
        super().__init__(global_config)
        self.processes = {}

    def _cleanup_dead(self):
        dead_apps = []
        for name, infos in self.processes.items():
            alive_infos = []
            for info in infos:
                if info["process"].poll() is None:
                    alive_infos.append(info)
                    continue
                for handle in ["stdout", "stderr"]:
                    if handle in info:
                        try:
                            info[handle].close()
                        except Exception:
                            pass
            if alive_infos:
                self.processes[name] = alive_infos
            else:
                dead_apps.append(name)
        for name in dead_apps:
            self.processes.pop(name, None)

    def start(self, runner):
        self._cleanup_dead()
        app_name = runner.name

        if self.is_running(app_name) and not runner.app_config.get("multi_run", False):
            return False, f"App '{app_name}' is already running."

        cmd = runner.build_command()
        workdir = runner.get_workdir()
        env = runner.get_env()
        use_shell = runner.should_use_shell()

        # Prepare log files
        # Use global log dir if specified, otherwise fallback to app workdir/logs
        global_log_dir = self.config.get("log", {}).get("dir", "./logs")

        # Resolve log dir relative to launcher root if it's relative
        config_dir = self.config.get("_config_dir") or os.getcwd()
        if not os.path.isabs(global_log_dir):
            log_dir = os.path.abspath(os.path.join(config_dir, global_log_dir))
        else:
            log_dir = global_log_dir

        os.makedirs(log_dir, exist_ok=True)

        stdout_path = os.path.join(log_dir, f"{app_name}.out.log")
        stderr_path = os.path.join(log_dir, f"{app_name}.err.log")

        try:
            stdout_file = open(stdout_path, "a", encoding="utf-8")
            stderr_file = open(stderr_path, "a", encoding="utf-8")
        except OSError as e:
            return False, f"Failed to open log files: {e}"

        creationflags = 0
        if is_windows():
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            process = subprocess.Popen(
                cmd,
                cwd=workdir,
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                shell=use_shell,
            )

            self.processes.setdefault(app_name, []).append({
                "process": process,
                "start_time": datetime.now(),
                "pid": process.pid,
                "stdout": stdout_file,
                "stderr": stderr_file,
            })
            return True, f"Started with PID {process.pid}"
        except Exception as e:
            stdout_file.close()
            stderr_file.close()
            return False, str(e)

    def stop(self, app_name):
        if app_name not in self.processes:
            return False, "App not found in session manager."

        infos = self.processes.pop(app_name)
        failures = []

        for info in infos:
            process = info["process"]
            pid = process.pid

            try:
                kill_process_tree(pid)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            except Exception as e:
                failures.append(str(e))
            finally:
                for handle in ["stdout", "stderr"]:
                    if handle in info:
                        try:
                            info[handle].close()
                        except Exception:
                            pass

        if failures:
            return False, "; ".join(failures)
        return True, "Stopped."

    def is_running(self, app_name):
        self._cleanup_dead()
        return app_name in self.processes and bool(self.processes[app_name])

    def get_info(self, app_name):
        if not self.is_running(app_name):
            return {"status": "STOPPED"}
        infos = self.processes[app_name]
        info = min(infos, key=lambda item: item["start_time"])
        uptime = datetime.now() - info["start_time"]
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            "status": "RUNNING",
            "pid": info["pid"],
            "instances": len(infos),
            "uptime": f"{days}d {hours}h {minutes}m {seconds}s",
            "start_time": info["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
        }
