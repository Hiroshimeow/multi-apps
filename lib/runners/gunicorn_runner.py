from .base import BaseRunner
from ..utils import find_python_interpreter, resolve_path, is_windows
import os

class GunicornRunner(BaseRunner):
    def build_command(self):
        # 1. Resolve Python/Gunicorn Environment
        env_config = self.app_config.get('env')
        default_env = self.global_config.get('default_conda_env')
        
        # We need the python interpreter to run "python -m gunicorn"
        # This is safer than finding "gunicorn" binary directly
        interpreter = find_python_interpreter(env_config, default_env)
        
        # 2. Get Gunicorn Config
        gunicorn_cfg = self.app_config.get('gunicorn', {})
        module = self.app_config.get('module')
        
        if not module:
             raise ValueError(f"Module not specified for gunicorn app '{self.name}'")
             
        # 3. Build Command
        # cmd: python -m gunicorn module [options]
        cmd = [interpreter, "-m", "gunicorn", module]
        
        # Add options
        for key, value in gunicorn_cfg.items():
            if value is None: continue
            
            # Convert "bind" -> "--bind"
            flag = f"--{key.replace('_', '-')}"
            
            # Handle boolean flags (e.g. --preload)
            if isinstance(value, bool):
                if value:
                    cmd.append(flag)
            else:
                cmd.append(flag)
                cmd.append(str(value))
        
        # Add extra args if any
        cmd.extend(self.app_config.get('args') or [])
        
        return cmd

    def get_workdir(self):
        # Workdir for gunicorn is usually the project root where the app module is
        path = self.app_config.get('path')
        if path:
            return resolve_path(path)
        return super().get_workdir()
