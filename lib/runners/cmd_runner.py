"""
CmdRunner — type: "cmd"

Trên Windows, runner này mô phỏng hành vi user mở PowerShell rồi gõ lệnh.

Hỗ trợ:
- Tự detect `cd <path>` đầu tiên để suy ra cwd
- Chuyển `cd` đầu tiên thành `Set-Location` của PowerShell
- Thực thi qua `pwsh`/`powershell` thay vì `cmd.exe`
"""

import os
import re
from .base import BaseRunner
from ..utils import build_powershell_command, is_windows, quote_powershell


def _split_statements(command: str) -> list[str]:
    """
    Tách command string thành danh sách statements.
    Separator: `;` hoặc `&&` (không tách trong dấu ngoặc kép hoặc đơn).
    """
    parts: list[str] = []
    current: list[str] = []
    quote_char = None
    i = 0
    while i < len(command):
        ch = command[i]
        if ch in ('"', "'"):
            if quote_char is None:
                quote_char = ch
            elif quote_char == ch:
                quote_char = None
            current.append(ch)
        elif quote_char is None and ch == ";":
            parts.append("".join(current).strip())
            current = []
        elif quote_char is None and command[i : i + 2] == "&&":
            parts.append("".join(current).strip())
            current = []
            i += 1  # skip second '&'
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _extract_cd_workdir(statements: list[str]):
    """
    Nếu statement đầu tiên là `cd [/d] <path>`:
      → return (normalized_path, remaining_statements)
    Ngược lại:
      → return (None, all_statements)
    """
    if not statements:
        return None, statements

    first = statements[0].strip()
    # Match: cd [/d] <path>  (path có thể có hoặc không quotes)
    m = re.match(r'^cd(?:\s+/d)?\s+"?(.+?)"?\s*$', first, re.IGNORECASE)
    if m:
        raw_path = m.group(1).strip().strip('"').strip("'")
        return raw_path, statements[1:]
    return None, statements


class CmdRunner(BaseRunner):
    """
    Runner cho type: "cmd" — mô phỏng user gõ lệnh trong CMD/Terminal.
    """

    def __init__(self, app_config, global_config):
        super().__init__(app_config, global_config)
        self._parsed = False
        self._detected_workdir: str | None = None
        self._built_cmd: str | list[str] = ""

    def _parse_command(self):
        """Parse command string 1 lần, cache kết quả."""
        if self._parsed:
            return

        raw_command = self.app_config.get("command", "").strip()
        if not raw_command:
            raise ValueError(f"'command' is required for cmd-type app '{self.name}'")

        statements = _split_statements(raw_command)
        cd_path, remaining = _extract_cd_workdir(statements)

        if cd_path:
            self._detected_workdir = os.path.normpath(cd_path)

        if is_windows():
            ps_statements = []
            if self._detected_workdir:
                ps_statements.append(
                    f"Set-Location -LiteralPath {quote_powershell(self._detected_workdir)}"
                )
            ps_statements.extend(remaining if cd_path else statements)
            self._built_cmd = build_powershell_command(
                "; ".join(p for p in ps_statements if p.strip())
            )
        elif cd_path:
            chain = [f'cd "{self._detected_workdir}"']
            chain.extend(remaining)
            self._built_cmd = " && ".join(p for p in chain if p.strip())
        else:
            self._built_cmd = " && ".join(p for p in statements if p.strip())

        self._parsed = True

    def build_command(self) -> str | list[str]:
        self._parse_command()
        return self._built_cmd

    def get_workdir(self) -> str | None:
        # Ưu tiên: config workdir > detected cd workdir
        if self.workdir:
            return self.workdir

        self._parse_command()
        return self._detected_workdir

    def should_use_shell(self) -> bool:
        return not is_windows()
