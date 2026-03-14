from .base import BaseRunner
from ..utils import build_powershell_command, resolve_path
import os


class UvRunner(BaseRunner):
    def build_command(self) -> str | list[str]:
        # 1. Hỗ trợ cmd: "cd xxx & uv run yy"
        command = self.app_config.get("command") or self.app_config.get("cmd")
        if command:
            if os.name == "nt":
                return build_powershell_command(command)
            return command

        # 2. Hỗ trợ chạy path thông thường
        path = self.app_config.get("path")
        args = self.app_config.get("args") or []

        cmd = ["uv", "run"]
        if path:
            script_path = resolve_path(path)
            if (
                script_path
                and os.path.exists(script_path)
                and os.path.isfile(script_path)
            ):
                cmd.append(os.path.basename(script_path))
            else:
                cmd.append(path)

        return cmd + args

    def get_workdir(self):
        if self.workdir:
            return self.workdir

        path = self.app_config.get("path")
        if path:
            script_path = resolve_path(path)
            if script_path:
                if os.path.exists(script_path) and os.path.isfile(script_path):
                    return os.path.dirname(script_path)

        return os.getcwd()

    def get_env(self):
        """
        Ghi đè cấu hình môi trường riêng cho uv.
        """
        # Lấy môi trường cấu hình từ BaseRunner
        env = super().get_env()

        # XÓA biến VIRTUAL_ENV của Launcher để tránh uv cảnh báo/lỗi môi trường chéo
        env.pop("VIRTUAL_ENV", None)

        return env

    def should_use_shell(self):
        return False
