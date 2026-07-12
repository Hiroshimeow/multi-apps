import os


class CommandRunner:
    """Run one terminal command from one working directory."""

    def __init__(self, app_config, global_config):
        self.app_config = app_config
        self.global_config = global_config
        self.name = app_config["name"]

    @staticmethod
    def normalize_args(args) -> list[str]:
        values = [args] if isinstance(args, str) else (args or [])
        return [
            text
            for value in values
            if (text := str(value).strip())
        ]

    @classmethod
    def args_text(cls, args) -> str:
        return " ".join(cls.normalize_args(args))

    @classmethod
    def build_command_text(cls, command, args) -> str:
        command_text = str(command).strip()
        args_text = cls.args_text(args)
        return " ".join(value for value in (command_text, args_text) if value)

    def build_command(self) -> str:
        return self.build_command_text(
            self.app_config["command"],
            self.app_config.get("args", []),
        )

    def get_workdir(self):
        return self.app_config.get("path")

    def get_env(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        return env

    def should_use_shell(self):
        return True
