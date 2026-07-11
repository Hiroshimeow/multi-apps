import os


class CommandRunner:
    """Run one terminal command from one working directory."""

    def __init__(self, app_config, global_config):
        self.app_config = app_config
        self.global_config = global_config
        self.name = app_config["name"]

    def build_command(self) -> str:
        command = self.app_config["command"].strip()
        args = [str(value).strip() for value in self.app_config.get("args", [])]
        return " ".join([command, *[value for value in args if value]])

    def get_workdir(self):
        return self.app_config.get("path")

    def get_env(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        return env

    def should_use_shell(self):
        return True
