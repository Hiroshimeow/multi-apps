from .base import BaseRunner
from ..utils import build_powershell_command, is_windows
import shlex

class CommandRunner(BaseRunner):
    def build_command(self):
        command = self.app_config.get('command')
        if not command:
            raise ValueError(f"Command not specified for app '{self.name}'")
            
        if is_windows():
            return build_powershell_command(command)
            
        return shlex.split(command)

    def should_use_shell(self):
        return False
