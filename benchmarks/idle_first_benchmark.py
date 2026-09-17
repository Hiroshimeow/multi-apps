#!/usr/bin/env python3
"""Isolated fixture and measurement harness for the idle-first launcher work.

The harness never touches the repository's real .runtime or logs. Phase 0
establishes the stable CLI and JSON schema; later phases tighten the scale
assertions as hot-index storage is implemented.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.core import AppController
from lib.runtime.models import RunRecord
from lib.runtime.process_identity import get_process_created_at
from lib.runtime.registry import RuntimeRegistry

ACTIVE_STATUSES = frozenset({"STARTING", "RUNNING", "STOPPING", "ORPHANED"})


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * fraction) + 0.999999) - 1))
    return ordered[index]


def _paths(root: Path) -> tuple[Path, Path, Path]:
    runtime_dir = root / ".runtime"
    runs_dir = runtime_dir / "runs"
    logs_dir = root / "logs"
    return runtime_dir, runs_dir, logs_dir


def _current_process_identity() -> tuple[int, float]:
    pid = os.getpid()
    created_at = get_process_created_at(pid)
    if created_at is None:
        raise RuntimeError(f"Cannot resolve benchmark process identity for PID {pid}")
    return pid, created_at


def seed(root: Path, history: int, active: int) -> dict:
    if history < 0 or active < 0 or active > history:
        raise ValueError("Require 0 <= active <= history")
    root = root.resolve()
    runtime_dir, runs_dir, logs_dir = _paths(root)
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    app_count = max(1, active, min(history, 11))
    apps = []
    for index in range(app_count):
        app_id = f"bench-app-{index:02d}"
        apps.append(
            {
                "id": app_id,
                "name": f"Bench App {index:02d}",
                "path": str(root),
                "command": f'{sys.executable} -c "pass"',
                "enabled": True,
                "multi_run": True,
            }
        )

    config_path = root / "setting.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {"global": {"log_dir": str(logs_dir)}, "apps": apps},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    active_pid, active_created_at = _current_process_identity()
    base = datetime.now(timezone.utc) - timedelta(days=history + 1)
    for index in range(history):
        app_id = apps[index % app_count]["id"]
        run_id = f"bench-{index:08d}"
        timestamp = (base + timedelta(seconds=index)).isoformat()
        is_active = index >= history - active
        record = RunRecord(
            app_id=app_id,
            run_id=run_id,
            path=str(root),
            command="benchmark",
            root_pid=active_pid if is_active else 0,
            root_created_at=active_created_at if is_active else 0.0,
            stdout_path=str(logs_dir / app_id / f"{run_id}.out.log"),
            stderr_path=str(logs_dir / app_id / f"{run_id}.err.log"),
            state="orphaned" if is_active else "stopped",
            created_at=timestamp,
            updated_at=timestamp,
        )
        (runs_dir / f"{run_id}.json").write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        "root": str(root),
        "config_path": str(config_path),
        "history_count": history,
        "requested_active_count": active,
        "observed_active_count": None,
        "active_count": None,
        "active_fixture_pid": active_pid if active else None,
        "active_fixture_scope": "current benchmark process lifetime",
        "app_count": app_count,
    }


def _observed_active_count(snapshot: dict[str, dict]) -> int:
    return sum(
        int(payload.get("instances") or 0)
        for payload in snapshot.values()
        if str(payload.get("status") or "").upper() in ACTIVE_STATUSES
    )


def verify_scale(root: Path, history: int, active: int, iterations: int) -> dict:
    fixture = seed(root, history, active)
    _runtime_dir, runs_dir, _logs_dir = _paths(root.resolve())
    phases = ("migration", "startup", "snapshot")
    history_enumerations = {phase: 0 for phase in phases}
    history_record_opens = {phase: 0 for phase in phases}
    record_opens = {phase: 0 for phase in phases}
    phase = "migration"

    original_list_records = RuntimeRegistry.list_records
    original_path_open = Path.open

    def counted_open(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.parent == runs_dir and "r" in mode:
            record_opens[phase] += 1
        return original_path_open(path, *args, **kwargs)

    def counted_list_records(registry, *args, **kwargs):
        history_enumerations[phase] += 1
        before = record_opens[phase]
        records = original_list_records(registry, *args, **kwargs)
        history_record_opens[phase] += record_opens[phase] - before
        return records

    Path.open = counted_open
    RuntimeRegistry.list_records = counted_list_records
    try:
        controller = AppController(fixture["config_path"])
        app_ids = [
            str(app["id"])
            for app in controller.list_apps()
            if app.get("enabled", True)
        ]

        phase = "startup"
        initialized_snapshot = controller.get_status_snapshot(app_ids)
        observed_active = _observed_active_count(initialized_snapshot)

        phase = "snapshot"
        timings = []
        last_snapshot = {}
        for _ in range(max(1, iterations)):
            started = time.perf_counter()
            last_snapshot = controller.get_status_snapshot(app_ids)
            timings.append((time.perf_counter() - started) * 1000.0)
    finally:
        RuntimeRegistry.list_records = original_list_records
        Path.open = original_path_open

    mean_ms = statistics.fmean(timings) if timings else 0.0
    p95_ms = _percentile(timings, 0.95)
    return {
        "history_count": history,
        "requested_active_count": active,
        "observed_active_count": observed_active,
        "active_count": observed_active,
        "active_count_match": observed_active == active,
        "active_fixture_pid": fixture["active_fixture_pid"],
        "active_fixture_scope": fixture["active_fixture_scope"],
        "fixture_reseeded_for_verify": True,
        "migration_history_enumerations": history_enumerations["migration"],
        "migration_history_opens": history_record_opens["migration"],
        "normal_startup_history_enumerations": history_enumerations["startup"],
        "normal_startup_history_opens": history_record_opens["startup"],
        "snapshot_history_enumerations": history_enumerations["snapshot"],
        "snapshot_history_opens": history_record_opens["snapshot"],
        "snapshot_exact_record_opens": (
            record_opens["snapshot"] - history_record_opens["snapshot"]
        ),
        "snapshot_exact_record_opens_per_iteration": round(
            (record_opens["snapshot"] - history_record_opens["snapshot"])
            / max(1, iterations),
            3,
        ),
        "snapshot_mean_ms": round(mean_ms, 3),
        "snapshot_p95_ms": round(p95_ms, 3),
        "panel_mean_ms": None,
        "panel_p95_ms": None,
        "active_qtimers_hidden": None,
        "active_watchers_hidden": None,
        "worker_threads_hidden": None,
        "launcher_process_count": None,
        "snapshot_app_count": len(last_snapshot),
    }


def verify_visible(root: Path, timeout_seconds: float = 3.0) -> dict:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QStyle

    from multi import SystemTrayApp

    root = root.resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    config_path = _write_idle_config(root)
    application = QApplication.instance() or QApplication([])
    tray = SystemTrayApp(
        application.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
        config_path=str(config_path),
        launcher_argv=("multi.py", "--config", str(config_path)),
    )
    tray.auto_start_timer.stop()
    registry = tray.controller.session_manager.registry
    history_enumerations = 0
    original_list_records = registry.list_records

    def counted_list_records(*args, **kwargs):
        nonlocal history_enumerations
        history_enumerations += 1
        return original_list_records(*args, **kwargs)

    def wait_status(expected: str) -> float:
        started = time.perf_counter()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            application.processEvents()
            if tray.row_widgets[0]._status_info.get("status") == expected:
                return (time.perf_counter() - started) * 1000.0
            time.sleep(0.01)
        actual = tray.row_widgets[0]._status_info.get("status")
        raise TimeoutError(f"Visible panel did not reach {expected}; actual={actual}")

    try:
        tray.show_panel()
        application.processEvents()
        state_sequence = [tray.row_widgets[0]._status_info.get("status")]
        visible_initial_snapshots = tray.status_snapshot_count
        visible_watcher_paths = len(
            tray.runtime_refresh.watcher.files()
            + tray.runtime_refresh.watcher.directories()
        )
        registry.list_records = counted_list_records

        pid, created_at = _current_process_identity()
        record = RunRecord.create(
            app_id="idle-probe",
            path=str(root),
            command="benchmark",
            run_id="visible-run",
        )
        transition_ms = []
        registry.save(record)
        transition_ms.append(wait_status("STARTING"))
        state_sequence.append("STARTING")

        registry.update(
            record.run_id,
            state="running",
            keeper_pid=pid,
            keeper_created_at=created_at,
            root_pid=pid,
            root_created_at=created_at,
        )
        transition_ms.append(wait_status("RUNNING"))
        state_sequence.append("RUNNING")

        registry.update(record.run_id, state="stopping")
        transition_ms.append(wait_status("STOPPING"))
        state_sequence.append("STOPPING")

        registry.update(record.run_id, state="stopped", exit_code=0)
        transition_ms.append(wait_status("STOPPED"))
        state_sequence.append("STOPPED")

        visible_total_snapshots = tray.status_snapshot_count
        tray.tray_panel.hide()
        application.processEvents()
        hidden_snapshot_count = tray.status_snapshot_count
        hidden_watcher_paths = len(
            tray.runtime_refresh.watcher.files()
            + tray.runtime_refresh.watcher.directories()
        )
        registry.save(
            RunRecord.create(
                app_id="idle-probe",
                path=str(root),
                command="benchmark",
                run_id="hidden-run",
            )
        )
        hidden_deadline = time.monotonic() + 0.25
        while time.monotonic() < hidden_deadline:
            application.processEvents()
            time.sleep(0.01)
        hidden_refreshes = tray.status_snapshot_count - hidden_snapshot_count

        return {
            "state_sequence": state_sequence,
            "history_enumerations": history_enumerations,
            "visible_initial_snapshots": visible_initial_snapshots,
            "visible_refresh_snapshots": (
                visible_total_snapshots - visible_initial_snapshots
            ),
            "hidden_refresh_snapshots": hidden_refreshes,
            "visible_runtime_watcher_paths": visible_watcher_paths,
            "hidden_runtime_watcher_paths": hidden_watcher_paths,
            "panel_mean_ms": round(statistics.fmean(transition_ms), 3),
            "panel_p95_ms": round(_percentile(transition_ms, 0.95), 3),
            "transition_ms": [round(value, 3) for value in transition_ms],
        }
    finally:
        registry.list_records = original_list_records
        tray.shutdown_ui()
        tray.hide()
        tray.deleteLater()
        application.processEvents()


def _write_idle_config(root: Path) -> Path:
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    config_path = root / "setting.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "global": {"log_dir": str(logs_dir)},
                "apps": [
                    {
                        "id": "idle-probe",
                        "name": "Idle Probe",
                        "path": str(root),
                        "command": f'{sys.executable} -c "pass"',
                        "enabled": True,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config_path


def verify_idle(
    root: Path,
    *,
    stabilization_seconds: float = 10.0,
    observation_seconds: float = 60.0,
    sample_seconds: float = 1.0,
) -> dict:
    if stabilization_seconds < 0 or observation_seconds <= 0 or sample_seconds <= 0:
        raise ValueError("Require stabilization >= 0, observation > 0, and sample > 0")

    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config_path = _write_idle_config(root)
    ready_path = root / "idle-probe-ready.json"
    child_code = textwrap.dedent(
        """
        import json
        import os
        import sys
        import threading
        from pathlib import Path

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PyQt6.QtCore import QFileSystemWatcher, QTimer
        from PyQt6.QtWidgets import QApplication, QStyle
        from multi import SystemTrayApp

        application = QApplication([])
        config_path = sys.argv[1]
        ready_path = Path(sys.argv[2])
        tray = SystemTrayApp(
            application.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
            config_path=config_path,
            launcher_argv=("multi.py", "--config", config_path),
        )
        tray.auto_start_timer.stop()
        forbidden_workers = {
            "latest-log-reader",
            "pinned-log-reader",
            "launcher-command-worker",
            "log-preference-writer",
        }
        watcher_paths = sum(
            len(watcher.files()) + len(watcher.directories())
            for watcher in tray.findChildren(QFileSystemWatcher)
        )
        active_timers = sum(
            1 for timer in tray.findChildren(QTimer) if timer.isActive()
        )
        named_workers = sorted(
            forbidden_workers.intersection(
                thread.name for thread in threading.enumerate()
            )
        )
        ready_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "parent_pid": os.getppid(),
                    "executable": sys.executable,
                    "hidden_status_snapshots": tray.status_snapshot_count,
                    "hidden_runtime_watcher_paths": watcher_paths,
                    "hidden_active_qtimers": active_timers,
                    "hidden_named_workers": named_workers,
                }
            ),
            encoding="utf-8",
        )
        raise SystemExit(application.exec())
        """
    )
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(config_path), str(ready_path)],
        cwd=str(PROJECT_ROOT),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    launcher = None

    try:
        deadline = time.monotonic() + 15.0
        while not ready_path.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready_path.is_file():
            error = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(
                f"Qt launcher did not become ready: exit={process.poll()} stderr={error}"
            )

        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        measured_pid = int(ready["pid"])
        launcher = psutil.Process(measured_pid)
        time.sleep(stabilization_seconds)
        io_start = launcher.io_counters()
        previous_cpu = sum(launcher.cpu_times()[:2])
        previous_wall = time.perf_counter()
        cpu_samples = []
        threads_max = launcher.num_threads()
        working_set_max = launcher.memory_info().rss
        child_processes_max = len(launcher.children(recursive=True))

        observation_deadline = time.monotonic() + observation_seconds
        while time.monotonic() < observation_deadline:
            remaining = observation_deadline - time.monotonic()
            time.sleep(min(sample_seconds, max(0.0, remaining)))
            if not launcher.is_running():
                raise RuntimeError("Qt launcher exited during idle observation")
            current_cpu = sum(launcher.cpu_times()[:2])
            current_wall = time.perf_counter()
            cpu_samples.append(
                max(0.0, (current_cpu - previous_cpu) / (current_wall - previous_wall) * 100.0)
            )
            previous_cpu = current_cpu
            previous_wall = current_wall
            threads_max = max(threads_max, launcher.num_threads())
            working_set_max = max(working_set_max, launcher.memory_info().rss)
            child_processes_max = max(
                child_processes_max,
                len(launcher.children(recursive=True)),
            )

        io_end = launcher.io_counters()
        return {
            "observer_pid": os.getpid(),
            "spawn_pid": process.pid,
            "launched_pid": measured_pid,
            "measured_pid": measured_pid,
            "pid_resolved_from_handshake": True,
            "launcher_parent_pid": int(ready["parent_pid"]),
            "launcher_executable": str(ready["executable"]),
            "stabilization_seconds": stabilization_seconds,
            "observation_seconds": observation_seconds,
            "sample_seconds": sample_seconds,
            "cpu_one_core_mean_percent": round(statistics.fmean(cpu_samples), 4),
            "cpu_one_core_p95_percent": round(_percentile(cpu_samples, 0.95), 4),
            "io_read_ops_delta": io_end.read_count - io_start.read_count,
            "io_read_bytes_delta": io_end.read_bytes - io_start.read_bytes,
            "io_write_ops_delta": io_end.write_count - io_start.write_count,
            "io_write_bytes_delta": io_end.write_bytes - io_start.write_bytes,
            "hidden_status_snapshots": int(ready["hidden_status_snapshots"]),
            "hidden_runtime_watcher_paths": int(
                ready["hidden_runtime_watcher_paths"]
            ),
            "hidden_active_qtimers": int(ready["hidden_active_qtimers"]),
            "hidden_named_workers": list(ready["hidden_named_workers"]),
            "hidden_runtime_log_reads": io_end.read_count - io_start.read_count,
            "launcher_threads_max": threads_max,
            "launcher_working_set_bytes_max": working_set_max,
            "launcher_child_processes_max": child_processes_max,
            "gpu": "N/A",
        }
    finally:
        if launcher is not None:
            try:
                if launcher.is_running():
                    launcher.terminate()
                    launcher.wait(timeout=5.0)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                try:
                    launcher.kill()
                except psutil.NoSuchProcess:
                    pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        if process.stderr is not None:
            process.stderr.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser("seed")
    seed_parser.add_argument("--root", type=Path, required=True)
    seed_parser.add_argument("--history", type=int, required=True)
    seed_parser.add_argument("--active", type=int, required=True)

    scale_parser = subparsers.add_parser("verify-scale")
    scale_parser.add_argument("--root", type=Path, required=True)
    scale_parser.add_argument("--history", type=int, required=True)
    scale_parser.add_argument("--active", type=int, required=True)
    scale_parser.add_argument("--iterations", type=int, default=50)
    scale_parser.add_argument("--assert-no-history-enumeration", action="store_true")
    scale_parser.add_argument("--assert-snapshot-p95-ms", type=float)
    scale_parser.add_argument("--assert-panel-p95-ms", type=float)

    visible_parser = subparsers.add_parser("verify-visible")
    visible_parser.add_argument("--root", type=Path, required=True)
    visible_parser.add_argument("--timeout-seconds", type=float, default=3.0)

    idle_parser = subparsers.add_parser("verify-idle")
    idle_parser.add_argument("--root", type=Path, required=True)
    idle_parser.add_argument("--stabilization-seconds", type=float, default=10.0)
    idle_parser.add_argument("--observation-seconds", type=float, default=60.0)
    idle_parser.add_argument("--sample-seconds", type=float, default=1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "seed":
        result = seed(args.root, args.history, args.active)
    elif args.command == "verify-scale":
        result = verify_scale(args.root, args.history, args.active, args.iterations)
    elif args.command == "verify-visible":
        result = verify_visible(args.root, timeout_seconds=args.timeout_seconds)
    else:
        result = verify_idle(
            args.root,
            stabilization_seconds=args.stabilization_seconds,
            observation_seconds=args.observation_seconds,
            sample_seconds=args.sample_seconds,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))

    if args.command == "verify-scale":
        if not result["active_count_match"]:
            return 5
        if args.assert_no_history_enumeration and any(
            result[key] != 0
            for key in (
                "normal_startup_history_enumerations",
                "normal_startup_history_opens",
                "snapshot_history_enumerations",
                "snapshot_history_opens",
            )
        ):
            return 2
        if (
            args.assert_snapshot_p95_ms is not None
            and result["snapshot_p95_ms"] > args.assert_snapshot_p95_ms
        ):
            return 3
        if args.assert_panel_p95_ms is not None:
            if result["panel_p95_ms"] is None:
                return 4
            if result["panel_p95_ms"] > args.assert_panel_p95_ms:
                return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
