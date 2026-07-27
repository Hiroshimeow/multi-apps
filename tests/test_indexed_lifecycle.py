from __future__ import annotations

import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib.runtime.models import RunRecord
from lib.runtime.process_identity import get_process_created_at
from lib.runtime.registry import RuntimeRegistry
from lib.session.subprocess_session import SubprocessSessionManager


class _ProcessLaunchClient:
    def launch_keeper(self, _run_id):
        return os.getpid(), get_process_created_at(os.getpid())

    def reap_finished(self):
        return None


def _hold_app_lock_worker(root: str, gate, events) -> None:
    registry = RuntimeRegistry(Path(root) / ".runtime")
    gate.wait(10)
    with registry.app_lock("demo"):
        events.put(("enter", os.getpid(), time.monotonic()))
        time.sleep(0.25)
        events.put(("exit", os.getpid(), time.monotonic()))


def _start_process_worker(
    root: str,
    multi_run: bool,
    ready,
    gate,
    release,
    results,
) -> None:
    path = Path(root)
    session = SubprocessSessionManager(
        {"_config_dir": str(path), "log_dir": str(path / "logs")}
    )
    session.client = _ProcessLaunchClient()
    runner = SimpleNamespace(
        name="Demo",
        app_config={
            "id": "demo",
            "name": "Demo",
            "command": "python -c pass",
            "args": [],
            "multi_run": multi_run,
            "close_timeout": 1.0,
        },
        get_workdir=lambda: str(path),
    )
    ready.put(os.getpid())
    gate.wait(10)
    results.put(session.start(runner, wait_for_ready=False))
    release.wait(10)


class _LaunchClient:
    def __init__(self, release: threading.Event | None = None):
        self.release = release
        self.calls: list[str] = []
        self.first_launch = threading.Event()
        self._lock = threading.Lock()

    def launch_keeper(self, run_id):
        with self._lock:
            self.calls.append(run_id)
            call_number = len(self.calls)
        self.first_launch.set()
        if self.release is not None:
            self.release.wait(5)
        return 1000 + call_number, 100.0 + call_number

    def reap_finished(self):
        return None

    def wait_for_reap(self, _run_id, timeout=2.0):
        return True


class IndexedLifecycleTests(unittest.TestCase):
    def _session(self, root: Path) -> SubprocessSessionManager:
        return SubprocessSessionManager(
            {"_config_dir": str(root), "log_dir": str(root / "logs")}
        )

    @staticmethod
    def _runner(root: Path, *, multi_run: bool = False):
        return SimpleNamespace(
            name="Demo",
            app_config={
                "id": "demo",
                "name": "Demo",
                "command": "python -c pass",
                "args": [],
                "multi_run": multi_run,
                "close_timeout": 1.0,
            },
            get_workdir=lambda: str(root),
        )

    def test_two_concurrent_non_multi_starts_create_one_reservation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_a = self._session(root)
            session_b = self._session(root)
            release = threading.Event()
            client = _LaunchClient(release)
            session_a.client = client
            session_b.client = client
            runner = self._runner(root)
            barrier = threading.Barrier(3)
            results = []

            def start(session):
                barrier.wait()
                results.append(session.start(runner, wait_for_ready=False))

            threads = [
                threading.Thread(target=start, args=(session_a,)),
                threading.Thread(target=start, args=(session_b,)),
            ]
            for thread in threads:
                thread.start()
            barrier.wait()
            self.assertTrue(client.first_launch.wait(2))
            release.set()
            for thread in threads:
                thread.join(5)

            records = session_a.registry.list_records()
            self.assertEqual(len(records), 1)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(sum(1 for ok, _message in results if ok), 1)
            self.assertEqual(sum(1 for ok, _message in results if not ok), 1)

    def test_app_lock_serializes_cross_process_holders(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as temp_dir:
            gate = context.Event()
            events = context.Queue()
            processes = [
                context.Process(
                    target=_hold_app_lock_worker,
                    args=(temp_dir, gate, events),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            gate.set()
            for process in processes:
                process.join(10)
                if process.is_alive():
                    process.terminate()
                self.assertEqual(process.exitcode, 0)

            timeline = [events.get(timeout=2) for _ in range(4)]
            intervals = {}
            for event, pid, timestamp in timeline:
                intervals.setdefault(pid, {})[event] = timestamp
            ordered = sorted(intervals.values(), key=lambda value: value["enter"])
            self.assertGreaterEqual(
                ordered[1]["enter"],
                ordered[0]["exit"],
                timeline,
            )

    def test_cross_process_start_sources_share_the_same_reservation_boundary(self):
        context = multiprocessing.get_context("spawn")
        for multi_run, expected_records, expected_successes in (
            (False, 1, 1),
            (True, 2, 2),
        ):
            with self.subTest(multi_run=multi_run), tempfile.TemporaryDirectory() as temp_dir:
                ready = context.Queue()
                gate = context.Event()
                release = context.Event()
                results = context.Queue()
                processes = [
                    context.Process(
                        target=_start_process_worker,
                        args=(temp_dir, multi_run, ready, gate, release, results),
                    )
                    for _ in range(2)
                ]
                for process in processes:
                    process.start()
                ready_pids = [ready.get(timeout=10) for _ in processes]
                self.assertEqual(len(set(ready_pids)), 2)
                gate.set()
                outcomes = [results.get(timeout=10) for _ in processes]
                release.set()
                for process in processes:
                    process.join(15)
                    if process.is_alive():
                        process.terminate()
                    self.assertEqual(process.exitcode, 0)
                registry = self._session(Path(temp_dir)).registry
                records = registry.list_records()
                diagnostics = {
                    "outcomes": outcomes,
                    "records": [record.to_dict() for record in records],
                    "locks": [path.name for path in registry.locks_dir.glob("*.lock")],
                }
                self.assertEqual(
                    len(records),
                    expected_records,
                    diagnostics,
                )
                self.assertEqual(
                    sum(1 for ok, _message in outcomes if ok),
                    expected_successes,
                    diagnostics,
                )

    def test_two_concurrent_multi_run_starts_create_two_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_a = self._session(root)
            session_b = self._session(root)
            client = _LaunchClient()
            session_a.client = client
            session_b.client = client
            runner = self._runner(root, multi_run=True)
            barrier = threading.Barrier(3)
            results = []

            def start(session):
                barrier.wait()
                results.append(session.start(runner, wait_for_ready=False))

            threads = [
                threading.Thread(target=start, args=(session_a,)),
                threading.Thread(target=start, args=(session_b,)),
            ]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(5)

            records = session_a.registry.list_records()
            self.assertEqual(len(records), 2)
            self.assertEqual(len(client.calls), 2)
            self.assertTrue(all(ok for ok, _message in results))

    def test_snapshot_reconcile_and_stop_use_index_candidates_without_history_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = self._session(root)
            session.registry.save(
                RunRecord.create(
                    app_id="demo",
                    path=str(root),
                    command="python -c pass",
                    run_id="run-1",
                )
            )
            session.registry.update(
                "run-1",
                state="orphaned",
                root_pid=222,
                root_created_at=22.0,
            )

            with patch.object(
                session.registry,
                "list_records",
                side_effect=AssertionError("normal lifecycle enumerated history"),
            ), patch(
                "lib.session.subprocess_session.process_matches",
                side_effect=lambda pid, _created: int(pid) == 222,
            ):
                snapshot = session.get_status_snapshot(["demo"])
                info = session.get_info("demo")
                reconciled = session.reconcile("demo")
                stopped, message = session.stop("demo")

            self.assertEqual(snapshot["demo"]["status"], "ORPHANED")
            self.assertEqual(info["status"], "ORPHANED")
            self.assertEqual(reconciled[0].state, "orphaned")
            self.assertFalse(stopped)
            self.assertIn("orphaned", message)

    def test_failed_launch_remains_latest_without_history_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = self._session(root)
            session.client = SimpleNamespace(
                launch_keeper=lambda _run_id: (_ for _ in ()).throw(RuntimeError("boom")),
                reap_finished=lambda: None,
            )

            with patch.object(
                session.registry,
                "list_records",
                wraps=session.registry.list_records,
            ) as history_scan:
                ok, message = session.start(self._runner(root), wait_for_ready=False)
                history_scan.reset_mock()
                info = session.get_info("demo")
                history_scan.assert_not_called()

            self.assertFalse(ok)
            self.assertIn("boom", message)
            self.assertEqual(info["status"], "STOPPED")
            self.assertEqual(info["state"], "failed")


if __name__ == "__main__":
    unittest.main()
