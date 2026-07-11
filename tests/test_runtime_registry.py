import json
import tempfile
import unittest
from pathlib import Path

from lib.runtime.models import RunRecord
from lib.runtime.registry import RuntimeRegistry


class RuntimeRegistryTests(unittest.TestCase):
    def test_round_trip_preserves_minimum_run_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = RuntimeRegistry(Path(temp_dir) / ".runtime")
            record = RunRecord.create(
                app_id="demo",
                launcher_id="launcher-1",
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
                launcher_id="launcher-1",
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
                launcher_id="launcher-1",
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
                launcher_id="launcher-1",
                path="C:/demo",
                command="echo ok",
                run_id="run-1",
            )
            path = registry.save(record)

            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["app_id"], "demo")
            self.assertEqual(payload["state"], "starting")


if __name__ == "__main__":
    unittest.main()
