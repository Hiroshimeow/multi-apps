import subprocess
import os
import time
import datetime
import signal
from .base import BaseSessionManager
from ..utils import resolve_path, ensure_dir

class TmuxSessionManager(BaseSessionManager):
    def __init__(self, global_config):
        super().__init__(global_config)
        self.prefix = self.config.get('session', {}).get('tmux', {}).get('prefix', 'app-')
        # Store PIDs for apps we start
        self._pids = {}
        
    def _get_session_name(self, app_name):
        # Slugify name: "API Server" -> "app-api-server"
        slug = app_name.lower().replace(' ', '-')
        return f"{self.prefix}{slug}"
    
    def _run_tmux(self, args):
        try:
            return subprocess.check_output(['tmux'] + args, text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:
            return None

    def is_running(self, app_name):
        sname = self._get_session_name(app_name)
        result = self._run_tmux(['has-session', '-t', sname])
        return result is not None

    def start(self, runner):
        app_name = runner.name
        sname = self._get_session_name(app_name)
        
        if self.is_running(app_name):
            return False, "Session already exists"
            
        cmd_list = runner.build_command()
        
        # Prepare Log
        log_dir = resolve_path(self.config.get('log', {}).get('dir', './logs'))
        ensure_dir(log_dir)
        
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        safe_name = app_name.lower().replace(' ', '-')
        log_file = os.path.join(log_dir, f"{safe_name}_{date_str}.log")
        
        # Construct full shell command with logging
        # We need to quote arguments properly for shell execution inside tmux
        import shlex
        cmd_str = ' '.join(shlex.quote(str(arg)) for arg in cmd_list)
        full_cmd = f"{cmd_str} >> {shlex.quote(log_file)} 2>&1"
        
        # Get Environment
        env_vars = runner.get_env()
        env_cmds = []
        for k, v in env_vars.items():
            env_cmds.append(f"export {k}={shlex.quote(str(v))}")
        
        # Working Directory
        workdir = runner.get_workdir()
        
        # Create session detached
        # -d: detached
        # -s: session name
        # -c: working dir
        try:
            subprocess.run(['tmux', 'new-session', '-d', '-s', sname, '-c', workdir], check=True)
            
            # Run app command directly (without exporting all env vars)
            subprocess.run(['tmux', 'send-keys', '-t', sname, full_cmd, 'C-m'], check=True)
            
            return True, f"Started in tmux session '{sname}'"
        except Exception as e:
            return False, str(e)

    def stop(self, app_name):
        sname = self._get_session_name(app_name)
        if not self.is_running(app_name):
            return False, "Not running"
            
        try:
            # Get PID of shell running in tmux pane
            pane_pid = self._run_tmux(['list-panes', '-t', sname, '-F', '#{pane_pid}'])
            
            if pane_pid:
                # Find all child processes of the shell and kill them
                try:
                    # Get child PIDs
                    result = subprocess.run(
                        ['pgrep', '-P', pane_pid],
                        capture_output=True, text=True
                    )
                    child_pids = result.stdout.strip().split('\n')
                    
                    # Kill children first (the actual app processes)
                    for pid in child_pids:
                        if pid:
                            try:
                                os.kill(int(pid), signal.SIGTERM)
                            except (ProcessLookupError, ValueError):
                                pass
                    
                    time.sleep(1)
                    
                    # Force kill if still running
                    for pid in child_pids:
                        if pid:
                            try:
                                os.kill(int(pid), signal.SIGKILL)
                            except (ProcessLookupError, ValueError):
                                pass
                except Exception:
                    pass
            
            # Kill the tmux session
            subprocess.run(['tmux', 'kill-session', '-t', sname], check=True, stderr=subprocess.DEVNULL)
            return True, "Stopped"
        except Exception as e:
            return False, str(e)

    def get_info(self, app_name):
        if not self.is_running(app_name):
            return {"status": "STOPPED"}
            
        # Try to get uptime/pid if possible (hard with tmux detached)
        # We can inspect the pane to see if the process is still running
        return {"status": "RUNNING", "session": self._get_session_name(app_name)}
