#!/usr/bin/env python3
"""Isolated fixture and measurement harness for the idle-first launcher work.

The harness never touches the repository's real .runtime or logs. Phase 0
establishes the stable CLI and JSON schema; later phases tighten the scale
assertions as hot-index storage is implemented.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.core import AppController
from lib.runtime.models import RunRecord


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


def seed(root: Path, history: int, active: int) -> dict:
    if history < 0 or active < 0 or active > history:
        raise ValueError("Require 0 <= active <= history")
    root = root.resolve()
    runtime_dir, runs_dir, logs_dir = _paths(root)
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

    base = datetime.now(timezone.utc) - timedelta(days=history + 1)
    for index in range(history):
        app_id = apps[index % app_count]["id"]
        run_id = f"bench-{index:08d}"
        timestamp = (base + timedelta(seconds=index)).isoformat()
        state = "orphaned" if index >= history - active else "stopped"
        record = RunRecord(
            app_id=app_id,
            run_id=run_id,
            path=str(root),
            command="benchmark",
            stdout_path=str(logs_dir / app_id / f"{run_id}.out.log"),
            stderr_path=str(logs_dir / app_id / f"{run_id}.err.log"),
            state=state,
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
        "active_count": active,
        "app_count": app_count,
    }


def verify_scale(root: Path, history: int, active: int, iterations: int) -> dict:
    config_path = root.resolve() / "setting.yaml"
    if not config_path.is_file():
        seed(root, history, active)

    controller = AppController(str(config_path))
    app_ids = [str(app["id"]) for app in controller.list_apps() if app.get("enabled", True)]
    registry = getattr(controller.session_manager, "registry", None)
    history_enumerations = 0
    original_list_records = getattr(registry, "list_records", None)

    if callable(original_list_records):
        def counted_list_records(*args, **kwargs):
            nonlocal history_enumerations
            history_enumerations += 1
            return original_list_records(*args, **kwargs)

        registry.list_records = counted_list_records

    timings = []
    last_snapshot = {}
    for _ in range(max(1, iterations)):
        started = time.perf_counter()
        last_snapshot = controller.get_status_snapshot(app_ids)
        timings.append((time.perf_counter() - started) * 1000.0)

    mean_ms = statistics.fmean(timings) if timings else 0.0
    p95_ms = _percentile(timings, 0.95)
    return {
        "history_count": history,
        "active_count": active,
        "migration_history_opens": None,
        "normal_startup_history_opens": None,
        "snapshot_history_enumerations": history_enumerations,
        "snapshot_exact_record_opens": None,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("seed", "verify-scale"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--root", type=Path, required=True)
        sub.add_argument("--history", type=int, required=True)
        sub.add_argument("--active", type=int, required=True)
        if name == "verify-scale":
            sub.add_argument("--iterations", type=int, default=50)
            sub.add_argument("--assert-no-history-enumeration", action="store_true")
            sub.add_argument("--assert-snapshot-p95-ms", type=float)
            sub.add_argument("--assert-panel-p95-ms", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "seed":
        result = seed(args.root, args.history, args.active)
    else:
        result = verify_scale(args.root, args.history, args.active, args.iterations)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))

    if args.command == "verify-scale":
        if args.assert_no_history_enumeration and result["snapshot_history_enumerations"] != 0:
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
