from abc import ABC, abstractmethod
import os
from ..utils import resolve_path


class BaseRunner(ABC):
    def __init__(self, app_config, global_config):
        self.app_config = app_config
        self.global_config = global_config
        self.name = app_config.get("name")
        self.workdir = resolve_path(app_config.get("workdir"))
        self.env_vars = app_config.get("env_vars", {}).copy()

    def should_use_shell(self) -> bool:
        """Return True if command should be run in a shell."""
        return False

    @abstractmethod
    def build_command(self) -> list[str] | str:
        """
        Return the command to execute.

        - list[str]: e.g. ['/path/to/python', 'main.py', '--arg']
          Used with shell=False (default).
        - str: e.g. "pnpm openclaw --profile repo gateway"
          Used with shell=True (CmdRunner, CommandRunner on Windows).

        Popen accepts both forms; the caller must set shell= accordingly
        via should_use_shell().
        """
        pass

    def get_workdir(self):
        return self.workdir

    def get_env(self):
        """
        Return environment variables to set.
        Merges system env with config env_vars.
        Ensures a healthy PATH on Windows.
        """
        env = os.environ.copy()

        # --- BẢN VÁ: NGĂN CHẶN RÒ RỈ MÔI TRƯỜNG ---
        keys_to_remove = ["VIRTUAL_ENV", "UV_ACTIVE", "UV_PROJECT_ENVIRONMENT"]

        # Xóa không phân biệt chữ hoa/thường để tránh lỗi trên Windows
        keys_in_env = list(env.keys())
        for key in keys_in_env:
            if key.upper() in keys_to_remove:
                del env[key]
        # ------------------------------------------

        # --- BẢN VÁ: ĐẢM BẢO PATH KHỎE MẠNH (Windows) ---
        if os.name == "nt":
            # Tìm key PATH (Windows case-insensitive)
            path_key = "PATH"
            for k in env:
                if k.upper() == "PATH":
                    path_key = k
                    break

            current_path = env.get(path_key, "")
            current_path_upper = current_path.upper()

            # Các thư mục hệ thống Windows tối thiểu
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            ensure_dirs = [
                os.path.join(system_root, "System32"),
                system_root,
                os.path.join(system_root, r"System32\Wbem"),
                os.path.join(system_root, r"System32\WindowsPowerShell\v1.0"),
                # Node.js / npm / pnpm
                r"C:\Program Files\nodejs",
                os.path.join(os.environ.get("APPDATA", ""), "npm"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "pnpm"),
            ]

            to_add = []
            for d in ensure_dirs:
                if os.path.isdir(d) and d.upper() not in current_path_upper:
                    to_add.append(d)

            if to_add:
                env[path_key] = os.pathsep.join(to_add) + os.pathsep + current_path
        # ------------------------------------------

        for k, v in self.env_vars.items():
            if isinstance(v, str):
                for existing_k, existing_v in env.items():
                    placeholder = f"${{{existing_k}}}"
                    if placeholder in v:
                        v = v.replace(placeholder, existing_v)
            env[k] = v

        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        return env
