from .base import BaseRunner
from ..utils import resolve_path, is_windows
import os

class ShellRunner(BaseRunner):
    def build_command(self):
        script_path = resolve_path(self.app_config.get('path'))
        
        if not script_path or not os.path.exists(script_path):
             raise ValueError(f"Shell script not found: {script_path}")
             
        args = self.app_config.get('args') or []
        
        if is_windows():
            if script_path.lower().endswith('.bat') or script_path.lower().endswith('.cmd'):
                return ["cmd", "/c", script_path] + args
            elif script_path.lower().endswith('.vbs'):
                return ["cscript", "//nologo", script_path] + args
            else:
                # Default to just running the path, Windows might handle it via associations
                return [script_path] + args
        else:
            # Linux/Unix
            return ["/bin/bash", script_path] + args

    def get_workdir(self):
        if self.workdir:
            return self.workdir
        
        script_path = resolve_path(self.app_config.get('path'))
        if script_path:
            return os.path.dirname(script_path)
            
        return None
