from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Any

from .ipc import create_listener, ipc_address
from .models import RunRecord
from .process_identity import get_process_created_at
from .registry import RuntimeRegistry


class WindowsJobObject:
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1

    def __init__(self) -> None:
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self._extended_info_type = JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        self._accounting_info_type = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        self._kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel32.TerminateJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self._handle,
            self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "SetInformationJobObject failed")

    def assign_pid(self, pid: int) -> None:
        process_set_quota = 0x0100
        process_terminate = 0x0001
        process_handle = self._kernel32.OpenProcess(
            process_set_quota | process_terminate,
            False,
            int(pid),
        )
        if not process_handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not self._kernel32.AssignProcessToJobObject(
                self._handle, process_handle
            ):
                raise OSError(
                    ctypes.get_last_error(), "AssignProcessToJobObject failed"
                )
        finally:
            self._kernel32.CloseHandle(process_handle)

    def active_process_count(self) -> int:
        info = self._accounting_info_type()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            self.JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            raise OSError(ctypes.get_last_error(), "QueryInformationJobObject failed")
        return int(info.ActiveProcesses)

    def terminate(self, exit_code: int = 1) -> None:
        if not self._kernel32.TerminateJobObject(self._handle, exit_code):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class ProcessKeeper:
    def __init__(self, runtime_dir: Path, run_id: str) -> None:
        self.runtime_dir = runtime_dir
        self.run_id = run_id
        self.registry = RuntimeRegistry(runtime_dir)
        self.is_windows = os.name == "nt"
        self.record = self.registry.load(run_id)
        if self.record is None:
            raise RuntimeError(f"Run record not found: {run_id}")

        self.listener: Listener | None = None
        self.listener_thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None
        self.job: WindowsJobObject | None = None
        self.stop_requested = threading.Event()
        self.shutdown = threading.Event()
        self.state_lock = threading.Lock()
        self.stdout_handle = None
        self.stderr_handle = None
        self.process_group_id = 0
        self.ready_file: Path | None = None

    def run(self) -> int:
        try:
            if self.is_windows:
                self.job = WindowsJobObject()
            self._update_record(
                keeper_pid=os.getpid(),
                keeper_created_at=float(get_process_created_at(os.getpid()) or 0.0),
            )
            self.listener = create_listener(self.runtime_dir, self.run_id)
            self.listener_thread = threading.Thread(
                target=self._serve_ipc,
                name=f"keeper-ipc-{self.run_id}",
                daemon=True,
            )
            self.listener_thread.start()
            self._open_logs()
            self._write_marker(f"RUN START {self.run_id} {self._timestamp()}")
            self._start_managed_process()
            self._update_record(
                root_pid=self.process.pid,
                root_created_at=float(get_process_created_at(self.process.pid) or 0.0),
                state="running",
            )
            return self._monitor()
        except Exception as exc:
            cleanup_ok = self._emergency_cleanup()
            final_state = "failed" if cleanup_ok else "orphaned"
            try:
                self._write_marker(
                    f"RUN STOP {final_state} cleanup={cleanup_ok} "
                    f"{self._timestamp()} {exc}"
                )
                self._update_record(state=final_state, exit_code=1)
            except Exception:
                pass
            return 1
        finally:
            self.shutdown.set()
            if self.listener is not None:
                try:
                    self.listener.close()
                except OSError:
                    pass
            if not self.is_windows:
                try:
                    Path(ipc_address(self.runtime_dir, self.run_id)).unlink()
                except FileNotFoundError:
                    pass
            if self.ready_file is not None:
                try:
                    self.ready_file.unlink()
                except FileNotFoundError:
                    pass
            self._close_logs()
            if self.job is not None:
                self.job.close()

    def _open_logs(self) -> None:
        Path(self.record.stdout_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.record.stderr_path).parent.mkdir(parents=True, exist_ok=True)
        self.stdout_handle = open(self.record.stdout_path, "a", encoding="utf-8")
        self.stderr_handle = open(self.record.stderr_path, "a", encoding="utf-8")

    def _close_logs(self) -> None:
        for handle in (self.stdout_handle, self.stderr_handle):
            if handle is not None:
                try:
                    handle.flush()
                    handle.close()
                except OSError:
                    pass

    def _write_marker(self, text: str) -> None:
        line = f"===== {text} =====\n"
        for handle in (self.stdout_handle, self.stderr_handle):
            if handle is not None:
                handle.write(line)
                handle.flush()

    def _start_managed_process(self) -> None:
        command = " ".join(
            [self.record.command, *[str(value) for value in self.record.args if str(value)]]
        )
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"

        if self.is_windows:
            assert self.job is not None
            ready_dir = self.runtime_dir / "start"
            ready_dir.mkdir(parents=True, exist_ok=True)
            self.ready_file = ready_dir / f"{self.run_id}.ready"
            try:
                self.ready_file.unlink()
            except FileNotFoundError:
                pass
            bootstrap_command = [
                sys.executable,
                "-m",
                "lib.runtime.process_bootstrap",
                "--ready-file",
                str(self.ready_file),
                "--path",
                self.record.path or "",
                "--command",
                command,
            ]
            self.process = subprocess.Popen(
                bootstrap_command,
                cwd=Path(__file__).resolve().parents[2],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self.stdout_handle,
                stderr=self.stderr_handle,
                creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
            try:
                self.job.assign_pid(self.process.pid)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=2)
                raise
            self.ready_file.write_text("ready\n", encoding="utf-8")
            return

        self.process = subprocess.Popen(
            command,
            cwd=self.record.path or None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=self.stdout_handle,
            stderr=self.stderr_handle,
            shell=True,
            text=True,
            start_new_session=True,
        )
        self.process_group_id = self.process.pid

    def _monitor(self) -> int:
        assert self.process is not None
        while True:
            if self.stop_requested.is_set():
                return self._stop_managed_process("requested")

            if not self._managed_tree_alive():
                exit_code = self._poll_exit_code()
                self._write_marker(
                    f"RUN STOP natural exit={exit_code} {self._timestamp()}"
                )
                self._update_record(state="stopped", exit_code=exit_code)
                return 0

            time.sleep(0.2)

    def _stop_managed_process(self, reason: str) -> int:
        assert self.process is not None
        self._update_record(state="stopping")
        self._request_graceful_termination()
        deadline = time.monotonic() + self.record.close_timeout
        while time.monotonic() < deadline:
            if not self._managed_tree_alive():
                exit_code = self._poll_exit_code()
                self._write_marker(
                    f"RUN STOP graceful:{reason} exit={exit_code} {self._timestamp()}"
                )
                self._update_record(state="stopped", exit_code=exit_code)
                return 0
            time.sleep(0.05)

        forced_exit_code = 1 if self.is_windows else -int(signal.SIGKILL)
        self._write_marker(
            f"RUN STOP forcing:{reason} exit={forced_exit_code} {self._timestamp()}"
        )
        self._force_terminate_tree(forced_exit_code)
        if not self._wait_for_tree_exit(timeout=3.0):
            raise RuntimeError("Forced termination did not empty the managed process tree")
        exit_code = self._poll_exit_code()
        self._write_marker(
            f"RUN STOP forced:{reason} exit={exit_code} {self._timestamp()}"
        )
        self._update_record(state="stopped", exit_code=exit_code)
        return 0

    def _force_terminate_tree(self, exit_code: int) -> None:
        if self.is_windows:
            assert self.job is not None
            self.job.terminate(exit_code)
            return
        if self.process_group_id <= 0:
            raise RuntimeError("Managed process group identity is unavailable")
        try:
            os.killpg(self.process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _wait_for_tree_exit(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None:
                self.process.poll()
            if not self._managed_tree_alive():
                return True
            time.sleep(0.05)
        return not self._managed_tree_alive()

    def _emergency_cleanup(self) -> bool:
        if self.process is None:
            return True
        try:
            if not self._managed_tree_alive():
                self._poll_exit_code()
                return True
            exit_code = 1 if self.is_windows else -int(signal.SIGKILL)
            self._force_terminate_tree(exit_code)
            return self._wait_for_tree_exit(timeout=3.0)
        except Exception:
            return False

    def _poll_exit_code(self) -> int | None:
        assert self.process is not None
        exit_code = self.process.poll()
        if exit_code is not None:
            return exit_code
        try:
            return self.process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            return None

    def _request_graceful_termination(self) -> None:
        assert self.process is not None
        if self.is_windows:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=min(max(self.record.close_timeout, 0.5), 3.0),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            return

        try:
            os.killpg(self.process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def _managed_tree_alive(self) -> bool:
        if self.is_windows:
            assert self.job is not None
            try:
                return self.job.active_process_count() > 0
            except OSError:
                return self.process is not None and self.process.poll() is None
        if self.process_group_id <= 0:
            return self.process is not None and self.process.poll() is None
        try:
            os.killpg(self.process_group_id, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _serve_ipc(self) -> None:
        assert self.listener is not None
        while not self.shutdown.is_set():
            try:
                connection = self.listener.accept()
            except (OSError, EOFError):
                return
            try:
                payload = connection.recv()
                connection.send(self._handle_command(payload))
            except (OSError, EOFError):
                pass
            finally:
                connection.close()

    def _handle_command(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"ok": False, "message": "Invalid payload"}
        command = payload.get("command")
        if command == "ping":
            return {"ok": True, "message": "pong"}
        if command == "status":
            return {
                "ok": True,
                "state": self.record.state,
                "run_id": self.record.run_id,
                "app_id": self.record.app_id,
                "keeper_pid": self.record.keeper_pid,
                "keeper_created_at": self.record.keeper_created_at,
                "root_pid": self.record.root_pid,
                "root_created_at": self.record.root_created_at,
                "tree_alive": self._managed_tree_alive(),
                "stdout_path": self.record.stdout_path,
                "stderr_path": self.record.stderr_path,
                "exit_code": self.record.exit_code,
            }
        if command == "stop":
            if self.record.state == "stopping":
                return {"ok": True, "message": "Stop already requested"}
            self._update_record(state="stopping")
            self.stop_requested.set()
            return {"ok": True, "message": "Stop requested"}
        return {"ok": False, "message": f"Unknown command: {command}"}

    def _update_record(self, **changes: Any) -> None:
        with self.state_lock:
            updated = self.registry.update(self.run_id, **changes)
            if updated is None:
                raise RuntimeError(f"Run record disappeared: {self.run_id}")
            self.record = updated

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return ProcessKeeper(Path(args.runtime_dir), args.run_id).run()


if __name__ == "__main__":
    raise SystemExit(main())
