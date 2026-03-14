import os
import sys
import platform
import shutil
import subprocess
import signal
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

# Try to import rich for better output, fallback to standard print if not available
try:
    from rich.console import Console

    console = Console()
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    console = None


def is_windows():
    return platform.system() == "Windows"


def is_linux():
    return platform.system() == "Linux"


def print_info(msg):
    if RICH_AVAILABLE and console:
        console.print(f"[bold blue][INFO][/] {msg}")
    else:
        print(f"[INFO] {msg}")


def print_success(msg):
    if RICH_AVAILABLE and console:
        console.print(f"[bold green][OK][/] {msg}")
    else:
        print(f"[OK] {msg}")


def print_warning(msg):
    if RICH_AVAILABLE and console:
        console.print(f"[bold yellow][WARN][/] {msg}")
    else:
        print(f"[WARN] {msg}")


def print_error(msg):
    if RICH_AVAILABLE and console:
        console.print(f"[bold red][ERROR][/] {msg}")
    else:
        print(f"[ERROR] {msg}")


def resolve_path(path_str, base_dir=None):
    """
    Resolve absolute path, expanding user (~).
    If base_dir is provided and path is relative, resolve against base_dir.
    """
    if not path_str:
        return None

    path = Path(os.path.expanduser(path_str))

    if not path.is_absolute() and base_dir:
        path = Path(base_dir) / path

    return str(path.resolve())


def get_conda_base_path():
    """
    Attempt to find Conda base path.
    1. CONDA_EXE env var
    2. Current python executable if in conda
    3. Common installation paths
    """
    # 1. Check environment variable
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe and os.path.exists(conda_exe):
        # bin/conda -> base dir
        return os.path.dirname(os.path.dirname(conda_exe))

    # 2. Check current python
    current_python = sys.executable
    if "miniconda" in current_python.lower() or "anaconda" in current_python.lower():
        if "envs" in current_python:
            return current_python.split(os.sep + "envs")[0]
        else:
            # Assumes python is in bin/ or root of env
            if is_windows():
                return os.path.dirname(current_python)
            else:
                return os.path.dirname(os.path.dirname(current_python))

    # 3. Check common paths
    common_paths = [
        Path.home() / "miniconda3",
        Path.home() / "anaconda3",
        Path("/opt/miniconda3"),
        Path("/usr/local/miniconda3"),
        Path("C:/Users/admin/miniconda3"),  # Legacy from old code
        Path("C:/ProgramData/Miniconda3"),
        Path("C:/ProgramData/Anaconda3"),
    ]

    for p in common_paths:
        if p.exists():
            return str(p)

    # 4. Try `conda info --base` if conda is in PATH
    try:
        base = subprocess.check_output(["conda", "info", "--base"], text=True).strip()
        if base and os.path.exists(base):
            return base
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return None


def find_python_interpreter(env_config, default_conda_env=None):
    """
    Resolve python interpreter path based on env config.
    env_config: {'type': 'conda', 'name': '...'} or {'type': 'venv', 'path': '...'}
    """
    # Handle legacy config or missing env
    if not env_config:
        if not default_conda_env:
            return sys.executable
        env_config = {"type": "conda", "name": default_conda_env}

    env_type = env_config.get("type", "system")

    if env_type == "system":
        return sys.executable

    if env_type == "conda":
        env_name = env_config.get("name")
        if not env_name:
            return sys.executable

        conda_base = get_conda_base_path()
        if not conda_base:
            print_warning("Conda base path not found, using system python")
            return sys.executable

        base_path = str(conda_base)

        if is_windows():
            path_parts = ["envs", env_name, "python.exe"]
        else:
            path_parts = ["envs", env_name, "bin", "python"]

        # Ensure base_path is not None before joining
        if base_path:
            exe_path = os.path.join(str(base_path), *path_parts)
            if os.path.exists(exe_path):
                return exe_path
            print_warning(f"Conda env '{env_name}' interpreter not found at {exe_path}")
        elif env_name == "base" and conda_base:
            # Fallback for base env
            base_path_str = str(conda_base)
            if is_windows():
                exe_path = os.path.join(base_path_str, "python.exe")
            else:
                exe_path = os.path.join(base_path_str, "bin", "python")
            if os.path.exists(exe_path):
                return exe_path
            print_warning(f"Conda base interpreter not found at {exe_path}")
        else:
            print_warning(f"Conda env '{env_name}' base path not found")
        return sys.executable

    if env_type == "venv":
        venv_path = env_config.get("path")
        if not venv_path:
            return sys.executable

        if venv_path:
            venv_path_resolved = resolve_path(venv_path)

            if is_windows():
                exe_path = os.path.join(
                    str(venv_path_resolved), "Scripts", "python.exe"
                )
            else:
                exe_path = os.path.join(str(venv_path_resolved), "bin", "python")

            if os.path.exists(exe_path):
                return exe_path
            print_warning(f"Venv interpreter not found at {exe_path}")
        else:
            print_warning("Venv path not specified")

        return sys.executable

    return sys.executable


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def is_script_running(script_path):
    """
    Check if a python script is already running using psutil.
    Matches the absolute path of the script in the command line.
    """
    if not psutil:
        return False

    script_path = str(Path(script_path).resolve())
    current_pid = os.getpid()

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == current_pid:
                continue

            cmdline = proc.info["cmdline"]
            if not cmdline:
                continue

            # Simple check: if python is running the script
            # We look for the script path in the arguments
            for arg in cmdline:
                if not arg:
                    continue
                try:
                    p = str(Path(arg).resolve())
                    if p == script_path:
                        return True
                except (OSError, ValueError):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return False


def kill_process_tree(pid):
    """
    Kill a process and its children using SIGTERM then SIGKILL.
    """
    if is_windows():
        # On Windows, taskkill /F /T /PID is very reliable for killing process trees
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        except Exception:
            pass

    if not psutil:
        # Fallback for Linux/Unix if psutil is missing
        try:
            if not is_windows():
                # os.getpgid and os.killpg are only available on Unix
                try:
                    pgid = getattr(os, "getpgid")(pid)
                    if pgid == pid:
                        getattr(os, "killpg")(pgid, signal.SIGTERM)
                        time.sleep(0.5)
                        try:
                            # Use SIGKILL if available
                            sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                            getattr(os, "killpg")(pgid, sigkill)
                        except OSError:
                            pass
                        return
                except (OSError, AttributeError):
                    pass
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        return

    try:
        parent = psutil.Process(pid)
        try:
            procs = parent.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            procs = []
        procs.append(parent)

        # 1. Send SIGTERM to all
        for p in procs:
            try:
                p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        # 2. Wait for termination
        gone, alive = psutil.wait_procs(procs, timeout=3)

        # 3. Send SIGKILL to any remaining processes
        for p in alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass
