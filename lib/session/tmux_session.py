import datetime
import os
import shlex
import signal
import subprocess
import time

from .base import BaseSessionManager
from ..utils import ensure_dir, resolve_path


class TmuxSessionManager(BaseSessionManager):
    def __init__(self, global_config):
        super().__init__(global_config)
        self.prefix = self.config.get("tmux_prefix", "app-")

    def _get_session_name(self, app_name):
        return f"{self.prefix}{app_name.lower().replace(' ', '-')}"

    def _run_tmux(self, args):
        try:
            return subprocess.check_output(
                ["tmux", *args], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    def is_running(self, app_name):
        return self._run_tmux(["has-session", "-t", self._get_session_name(app_name)]) is not None

    def start(self, runner):
        app_name = runner.name
        session_name = self._get_session_name(app_name)
        if self.is_running(app_name):
            return False, "Session already exists"

        workdir = runner.get_workdir()
        if workdir and not os.path.isdir(workdir):
            return False, f"Path is not a directory: {workdir}"

        log_dir = resolve_path(
            self.config.get("log_dir", "./logs"), self.config.get("_config_dir")
        )
        ensure_dir(log_dir)
        date = datetime.datetime.now().strftime("%Y-%m-%d")
        safe_name = app_name.lower().replace(" ", "-")
        log_file = os.path.join(log_dir, f"{safe_name}_{date}.log")
        full_command = f"{runner.build_command()} >> {shlex.quote(log_file)} 2>&1"

        try:
            tmux_command = ["tmux", "new-session", "-d", "-s", session_name]
            if workdir:
                tmux_command.extend(["-c", workdir])
            subprocess.run(tmux_command, check=True)
            subprocess.run(
                ["tmux", "send-keys", "-t", session_name, full_command, "C-m"],
                check=True,
            )
            return True, f"Started in tmux session '{session_name}'"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    def stop(self, app_name):
        session_name = self._get_session_name(app_name)
        if not self.is_running(app_name):
            return False, "Not running"

        try:
            pane_pid = self._run_tmux(
                ["list-panes", "-t", session_name, "-F", "#{pane_pid}"]
            )
            if pane_pid:
                result = subprocess.run(
                    ["pgrep", "-P", pane_pid], capture_output=True, text=True
                )
                child_pids = [value for value in result.stdout.splitlines() if value]
                for child_pid in child_pids:
                    try:
                        os.kill(int(child_pid), signal.SIGTERM)
                    except (ProcessLookupError, ValueError):
                        pass
                time.sleep(1)

            subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                check=True,
                stderr=subprocess.DEVNULL,
            )
            return True, "Stopped"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    def get_info(self, app_name):
        if not self.is_running(app_name):
            return {"status": "STOPPED"}
        return {"status": "RUNNING", "session": self._get_session_name(app_name)}
