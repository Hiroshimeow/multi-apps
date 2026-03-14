import os
from .base import BaseRunner
from ..utils import find_python_interpreter, resolve_path

class PythonRunner(BaseRunner):
    def build_command(self):
        # 1. Resolve Python Interpreter
        env_config = self.app_config.get('env')
        default_env = self.global_config.get('default_conda_env')
        
        interpreter = find_python_interpreter(env_config, default_env)
        
        # 2. Resolve Script Path
        script_path = resolve_path(self.app_config.get('path'))
        
        if not script_path:
             raise ValueError(f"Path not specified for python app '{self.name}'")
             
        # 3. Build Args
        args = self.app_config.get('args') or []
        
        return [interpreter, script_path] + args

    def get_workdir(self):
        # If workdir is explicitly set in config, use it
        if self.workdir:
            return self.workdir
        
        # Otherwise, default to the script's directory
        # This fixes issues where apps rely on CWD to find their local resources
        script_path = resolve_path(self.app_config.get('path'))
        if script_path:
            return os.path.dirname(script_path)
            
        return None
