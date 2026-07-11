import json
import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.runtime.models import RunRecord
from lib.runtime.registry import RuntimeRegistry, _exclusive_file_lock


def _locked_root_update_worker(runtime_dir, acquired, release):
    registry = RuntimeRegistry(runtime_dir)
    with _exclusive_file_lock(registry._lock_path("run-1")):
        record = registry._load_path(registry._record_path("run-1"), warn=True)
        if record is None:
            raise RuntimeError("run-1 disappeared")
        record.root_pid = 111
        acquired.set()
        if not release.wait(5):
            raise TimeoutError("release signal was not received")
        registry._save_unlocked(record)


def _regular_keeper_update_worker(runtime_dir, started, done):
    registry = RuntimeRegistry(runtime_dir)
    started.set()
    registry.update("run-1", keeper_pid=222)
    done.set()


class RuntimeRegistryTests(unittest.TestCase):
    def test_round_trip_preserves_minimum_run_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            record = RunRecord.create(
                app_id="demo",
                path="C:/demo",
                command="python main.py",
                args=["--name exact value"],
                stdout_path="out.log",
                stderr_path="err.log",
                run_id="run-1",
            )

            path = registry.save(record)
            loaded = registry.load("run-1")

            self.assertTrue(path.exists())
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.to_dict(), record.to_dict())

    def test_interrupted_temp_file_does_not_replace_valid_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            record = RunRecord.create(
                app_id="demo",
                path="C:/demo",
                command="echo ok",
                run_id="run-1",
            )
            target = registry.save(record)
            temp_path = target.with_name(f"{target.name}.tmp")
            temp_path.write_text('{"state": "broken"', encoding="utf-8")

            loaded = registry.load("run-1")
            listed = registry.list_records()

            self.assertEqual(loaded.run_id, "run-1")
            self.assertEqual([item.run_id for item in listed], ["run-1"])
            self.assertTrue(temp_path.exists())

    def test_update_validates_state_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            record = RunRecord.create(
                app_id="demo",
                path="C:/demo",
                command="echo ok",
                run_id="run-1",
            )
            registry.save(record)

            updated = registry.update("run-1", state="running", root_pid=123)

            self.assertEqual(updated.state, "running")
            self.assertEqual(updated.root_pid, 123)
            with self.assertRaises(AttributeError):
                registry.update("run-1", missing=True)
            with self.assertRaises(ValueError):
                registry.update("run-1", state="unknown")

    def test_json_file_is_complete_and_parseable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            record = RunRecord.create(
                app_id="demo",
                path="C:/demo",
                command="echo ok",
                run_id="run-1",
            )
            path = registry.save(record)

            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["app_id"], "demo")
            self.assertEqual(payload["state"], "starting")

    def test_cross_process_update_waits_for_record_lock_and_preserves_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = str(Path(temp_dir) / ".runtime")
            registry = RuntimeRegistry(runtime_dir)
            registry.save(
                RunRecord.create(
                    app_id="demo",
                    path="C:/demo",
                    command="echo ok",
                    run_id="run-1",
                )
            )

            context = multiprocessing.get_context("spawn")
            acquired = context.Event()
            release = context.Event()
            started = context.Event()
            done = context.Event()
            lock_holder = context.Process(
                target=_locked_root_update_worker,
                args=(runtime_dir, acquired, release),
            )
            updater = context.Process(
                target=_regular_keeper_update_worker,
                args=(runtime_dir, started, done),
            )
            lock_holder.start()
            self.assertTrue(acquired.wait(5), "lock holder did not acquire the record lock")
            updater.start()
            self.assertTrue(started.wait(5), "updater did not start")
            time.sleep(0.2)
            self.assertFalse(done.is_set(), "update bypassed the cross-process lock")
            release.set()
            lock_holder.join(5)
            updater.join(5)
            if lock_holder.is_alive():
                lock_holder.terminate()
            if updater.is_alive():
                updater.terminate()
            self.assertEqual(lock_holder.exitcode, 0)
            self.assertEqual(updater.exitcode, 0)

            loaded = registry.load("run-1")
            self.assertEqual(loaded.root_pid, 111)
            self.assertEqual(loaded.keeper_pid, 222)

    def test_stale_expected_update_cannot_overwrite_terminal_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            registry.save(
                RunRecord.create(
                    app_id="demo",
                    path="C:/demo",
                    command="echo ok",
                    run_id="run-1",
                )
            )
            stale = registry.load("run-1")
            stopped = registry.update("run-1", state="stopped", exit_code=0)

            result = registry.update(
                "run-1",
                expected_updated_at=stale.updated_at,
                state="orphaned",
            )

            self.assertEqual(result.updated_at, stopped.updated_at)
            self.assertEqual(registry.load("run-1").state, "stopped")

    def test_transient_permission_error_is_retried_without_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            registry.save(
                RunRecord.create(
                    app_id="demo",
                    path="C:/demo",
                    command="echo ok",
                    run_id="run-1",
                )
            )
            target = registry._record_path("run-1")
            original_open = Path.open
            attempts = {"count": 0}

            def flaky_open(path, *args, **kwargs):
                mode = args[0] if args else kwargs.get("mode", "r")
                if path == target and "r" in mode and attempts["count"] < 2:
                    attempts["count"] += 1
                    raise PermissionError(13, "simulated sharing violation", str(path))
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", new=flaky_open), patch(
                "lib.runtime.registry.print_warning"
            ) as warning:
                loaded = registry.load("run-1")

            self.assertEqual(loaded.run_id, "run-1")
            self.assertEqual(attempts["count"], 2)
            warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
