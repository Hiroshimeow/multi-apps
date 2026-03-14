from .base import BaseRunner
from ..utils import is_windows
import shlex

class CommandRunner(BaseRunner):
    def build_command(self):
        command = self.app_config.get('command')
        if not command:
            raise ValueError(f"Command not specified for app '{self.name}'")
            
        if is_windows():
            # On Windows, we often want to pass the raw string to Popen(shell=True)
            # but SubprocessSessionManager expects a list.
            # If we use shell=True, we can pass either.
            return command
            
        return shlex.split(command)

    def should_use_shell(self):
        return is_windows()
