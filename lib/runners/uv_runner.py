from .base import BaseRunner
from ..utils import build_powershell_command, resolve_path
import os
import re


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

        # Keep path-based UV apps unchanged: run/open from the script directory.
        path = self.app_config.get("path")
        if path:
            script_path = resolve_path(path)
            if script_path:
                if os.path.exists(script_path) and os.path.isfile(script_path):
                    return os.path.dirname(script_path)

        return os.getcwd()

    @staticmethod
    def _infer_workdir_from_command(command):
        if not isinstance(command, str) or not command.strip():
            return None

        def existing_directory(raw_path):
            candidate = str(raw_path or "").strip().strip('"\'')
            if not candidate:
                return None
            resolved = resolve_path(candidate)
            if not resolved or not os.path.exists(resolved):
                return None
            if os.path.isfile(resolved):
                return os.path.dirname(resolved)
            if os.path.isdir(resolved):
                return resolved
            return None

        # Prefer an explicit UV project/directory option wherever it appears.
        option_pattern = re.compile(
            r"(?:--directory|--project)(?:=|\s+)(?:\"([^\"]+)\"|'([^']+)'|([^\s;&|]+))",
            re.IGNORECASE,
        )
        option_match = option_pattern.search(command)
        if option_match:
            inferred = existing_directory(next(value for value in option_match.groups() if value))
            if inferred:
                return inferred

        # Preserve support for shell commands such as:
        # "cd E:/repo && uv run main.py" or "Set-Location 'E:/repo'; uv run main.py".
        cd_pattern = re.compile(
            r"(?:^|[;&|]\s*)(?:cd|set-location)\s+(?:/d\s+)?(?:\"([^\"]+)\"|'([^']+)'|([^;&|]+?))(?=\s*(?:&&|;|\||$))",
            re.IGNORECASE,
        )
        cd_match = cd_pattern.search(command)
        if cd_match:
            inferred = existing_directory(next(value for value in cd_match.groups() if value))
            if inferred:
                return inferred

        # For command-style UV entries, inspect arguments after "uv run" and
        # use the first existing absolute directory/file as the repo location.
        run_match = re.search(r"\buv(?:\.exe)?\b.*?\brun\b(?P<tail>.*)$", command, re.IGNORECASE)
        if not run_match:
            return None

        token_pattern = re.compile(r'"([^"]*)"|\'([^\']*)\'|([^\s;&|]+)')
        for token_match in token_pattern.finditer(run_match.group("tail")):
            token = next(value for value in token_match.groups() if value is not None)
            if not token or token.startswith("-"):
                continue
            inferred = existing_directory(token)
            if inferred:
                return inferred

        return None

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
