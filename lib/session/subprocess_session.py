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
        dead_apps = [
            name
            for name, info in self.processes.items()
            if info["process"].poll() is not None
        ]
        for name in dead_apps:
            info = self.processes.pop(name)
            for handle in ["stdout", "stderr"]:
                if handle in info:
                    try:
                        info[handle].close()
                    except Exception:
                        pass

    def start(self, runner):
        self._cleanup_dead()
        app_name = runner.name

        if self.is_running(app_name):
            return False, f"App '{app_name}' is already running."

        cmd = runner.build_command()
        workdir = runner.get_workdir()
        env = runner.get_env()
        use_shell = runner.should_use_shell()

        # Prepare log files
        # Use global log dir if specified, otherwise fallback to app workdir/logs
        global_log_dir = self.config.get("log", {}).get("dir", "./logs")

        # Resolve log dir relative to launcher root if it's relative
        launcher_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        if not os.path.isabs(global_log_dir):
            log_dir = os.path.abspath(os.path.join(launcher_root, global_log_dir))
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

            self.processes[app_name] = {
                "process": process,
                "start_time": datetime.now(),
                "pid": process.pid,
                "stdout": stdout_file,
                "stderr": stderr_file,
            }
            return True, f"Started with PID {process.pid}"
        except Exception as e:
            stdout_file.close()
            stderr_file.close()
            return False, str(e)

    def stop(self, app_name):
        if app_name not in self.processes:
            return False, "App not found in session manager."

        info = self.processes.pop(app_name)
        process = info["process"]
        pid = process.pid

        try:
            kill_process_tree(pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            return True, "Stopped."
        except Exception as e:
            return False, f"Error stopping: {e}"
        finally:
            for handle in ["stdout", "stderr"]:
                if handle in info:
                    try:
                        info[handle].close()
                    except Exception:
                        pass

    def is_running(self, app_name):
        if app_name not in self.processes:
            return False
        proc = self.processes[app_name]["process"]
        if proc.poll() is None:
            return True
        else:
            info = self.processes[app_name]
            if "stdout" in info:
                try:
                    info["stdout"].close()
                except Exception:
                    pass
            if "stderr" in info:
                try:
                    info["stderr"].close()
                except Exception:
                    pass

            del self.processes[app_name]
            return False

    def get_info(self, app_name):
        if not self.is_running(app_name):
            return {"status": "STOPPED"}
        info = self.processes[app_name]
        uptime = datetime.now() - info["start_time"]
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            "status": "RUNNING",
            "pid": info["pid"],
            "uptime": f"{days}d {hours}h {minutes}m {seconds}s",
            "start_time": info["start_time"].strftime("%Y-%m-%d %H:%M:%S"),
        }
