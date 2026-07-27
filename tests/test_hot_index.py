from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from lib.runtime.models import RunRecord
from lib.runtime.registry import RuntimeRegistry


def _save_record_worker(runtime_dir: str, app_id: str, run_id: str) -> None:
    registry = RuntimeRegistry(runtime_dir)
    registry.save(
        RunRecord.create(
            app_id=app_id,
            path="C:/demo",
            command="echo ok",
            run_id=run_id,
        )
    )


class HotIndexTests(unittest.TestCase):
    def _record(
        self,
        app_id: str,
        run_id: str,
        *,
        state: str = "starting",
        created_at: str | None = None,
    ) -> RunRecord:
        timestamp = created_at or datetime.now(timezone.utc).isoformat()
        return RunRecord(
            app_id=app_id,
            run_id=run_id,
            path="C:/demo",
            command="echo ok",
            state=state,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def test_missing_corrupt_and_dirty_index_rebuild_from_full_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            registry._save_unlocked(self._record("alpha", "run-1", state="running"))

            rebuilt = registry.read_hot_index()
            self.assertEqual(rebuilt["active"], {"alpha": ["run-1"]})
            self.assertEqual(rebuilt["latest"], {"alpha": "run-1"})

            registry.hot_index_path.write_text("{broken", encoding="utf-8")
            rebuilt = registry.read_hot_index()
            self.assertEqual(rebuilt["active"], {"alpha": ["run-1"]})

            registry.hot_index_path.write_text(
                json.dumps({"version": 99, "active": {}, "latest": {}}),
                encoding="utf-8",
            )
            rebuilt = registry.read_hot_index()
            self.assertEqual(rebuilt["latest"], {"alpha": "run-1"})

            registry.hot_index_dirty_path.write_text("dirty\n", encoding="utf-8")
            registry.hot_index_path.write_text(
                json.dumps({"version": 1, "active": {}, "latest": {}}),
                encoding="utf-8",
            )
            rebuilt = registry.read_hot_index()
            self.assertEqual(rebuilt["active"], {"alpha": ["run-1"]})
            self.assertFalse(registry.hot_index_dirty_path.exists())

    def test_active_and_latest_follow_state_transitions_and_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            older = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            newer = datetime.now(timezone.utc).isoformat()
            registry.save(self._record("alpha", "run-old", state="running", created_at=older))
            registry.save(self._record("alpha", "run-new", state="starting", created_at=newer))

            index = registry.read_hot_index()
            self.assertEqual(index["active"]["alpha"], ["run-old", "run-new"])
            self.assertEqual(index["latest"]["alpha"], "run-new")

            registry.update("run-new", state="stopped", exit_code=0)
            index = registry.read_hot_index()
            self.assertEqual(index["active"]["alpha"], ["run-old"])
            self.assertEqual(index["latest"]["alpha"], "run-new")

            self.assertTrue(registry.delete("run-new"))
            index = registry.read_hot_index()
            self.assertEqual(index["active"]["alpha"], ["run-old"])
            self.assertEqual(index["latest"]["alpha"], "run-old")

    def test_stale_terminal_update_cannot_reactivate_index_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            registry.save(self._record("alpha", "run-1", state="starting"))
            registry.update("run-1", state="stopped", exit_code=0)

            result = registry.update("run-1", state="running", root_pid=123)

            self.assertEqual(result.state, "stopped")
            self.assertNotIn("alpha", registry.read_hot_index()["active"])

    def test_concurrent_saves_do_not_lose_index_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = str(Path(temp_dir) / ".runtime")
            context = multiprocessing.get_context("spawn")
            processes = [
                context.Process(
                    target=_save_record_worker,
                    args=(runtime_dir, f"app-{index}", f"run-{index}"),
                )
                for index in range(4)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)
                if process.is_alive():
                    process.terminate()
                self.assertEqual(process.exitcode, 0)

            registry = RuntimeRegistry(runtime_dir)
            index = registry.read_hot_index()
            self.assertEqual(
                index["active"],
                {f"app-{index}": [f"run-{index}"] for index in range(4)},
            )
            self.assertEqual(
                index["latest"],
                {f"app-{index}": f"run-{index}" for index in range(4)},
            )

    def test_normal_indexed_read_opens_only_referenced_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            base = datetime.now(timezone.utc) - timedelta(days=1)
            for index in range(200):
                state = "running" if index == 199 else "stopped"
                record = self._record(
                    "alpha",
                    f"run-{index:05d}",
                    state=state,
                    created_at=(base + timedelta(seconds=index)).isoformat(),
                )
                registry._record_path(record.run_id).write_text(
                    json.dumps(record.to_dict()),
                    encoding="utf-8",
                )

            rebuilt = registry.read_hot_index()
            self.assertEqual(rebuilt["active"], {"alpha": ["run-00199"]})
            exact_opens = 0
            original_open = Path.open

            def counted_open(path, *args, **kwargs):
                nonlocal exact_opens
                mode = args[0] if args else kwargs.get("mode", "r")
                if path.parent == registry.runs_dir and "r" in mode:
                    exact_opens += 1
                return original_open(path, *args, **kwargs)

            with patch.object(
                registry,
                "list_records",
                side_effect=AssertionError("normal indexed read enumerated history"),
            ), patch.object(Path, "open", new=counted_open):
                records = registry.indexed_records(["alpha"], include_latest=True)

            self.assertEqual([record.run_id for record in records], ["run-00199"])
            self.assertEqual(exact_opens, 1)

    def test_demonstrably_stale_index_rebuilds_when_runs_directory_is_newer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            registry.save(self._record("alpha", "run-1", state="stopped"))
            registry.read_hot_index()
            registry._record_path("run-2").write_text(
                json.dumps(self._record("beta", "run-2", state="running").to_dict()),
                encoding="utf-8",
            )
            index_mtime = registry.hot_index_path.stat().st_mtime_ns
            os.utime(
                registry.runs_dir,
                ns=(index_mtime + 2_000_000_000, index_mtime + 2_000_000_000),
            )

            rebuilt = registry.read_hot_index()

            self.assertEqual(rebuilt["active"], {"beta": ["run-2"]})
            self.assertEqual(rebuilt["latest"]["beta"], "run-2")


if __name__ == "__main__":
    unittest.main()
