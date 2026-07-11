import os
import platform
import signal
import subprocess
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

try:
    from rich.console import Console

    console = Console()
except ImportError:
    console = None


def is_windows():
    return platform.system() == "Windows"


def is_linux():
    return platform.system() == "Linux"


def _print(message, style=None):
    if console and style:
        console.print(message, style=style)
    else:
        print(message)


def print_success(message):
    _print(f"[OK] {message}", "bold green")


def print_warning(message):
    _print(f"[WARN] {message}", "bold yellow")


def print_error(message):
    _print(f"[ERROR] {message}", "bold red")


def resolve_path(path_value, base_dir=None):
    if not path_value:
        return None
    path = Path(os.path.expanduser(str(path_value)))
    if not path.is_absolute() and base_dir:
        path = Path(base_dir) / path
    return str(path.resolve())


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def kill_process_tree(pid):
    if is_windows():
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    if psutil:
        try:
            parent = psutil.Process(pid)
            processes = parent.children(recursive=True) + [parent]
            for process in processes:
                try:
                    process.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            _, alive = psutil.wait_procs(processes, timeout=3)
            for process in alive:
                try:
                    process.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return

    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
    except OSError:
        pass
