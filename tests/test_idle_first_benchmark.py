from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = PROJECT_ROOT / "tools" / "idle_first_benchmark.py"
SPEC = importlib.util.spec_from_file_location("idle_first_benchmark", HARNESS_PATH)
HARNESS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(HARNESS)


class IdleFirstBenchmarkTests(unittest.TestCase):
    def test_verify_scale_reports_requested_and_observed_active_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = HARNESS.verify_scale(Path(temp_dir), 20, 2, 1)

        self.assertEqual(result["requested_active_count"], 2)
        self.assertEqual(result["observed_active_count"], 2)
        self.assertEqual(result["active_count"], 2)
        self.assertTrue(result["active_count_match"])
        self.assertEqual(result["migration_history_enumerations"], 1)
        self.assertEqual(result["migration_history_opens"], 20)
        self.assertEqual(result["normal_startup_history_enumerations"], 0)
        self.assertEqual(result["normal_startup_history_opens"], 0)
        self.assertEqual(result["snapshot_history_enumerations"], 0)
        self.assertEqual(result["snapshot_history_opens"], 0)
        self.assertGreater(result["snapshot_exact_record_opens"], 0)

    def test_verify_scale_cli_fails_when_active_fixture_mismatches(self):
        result = {
            "requested_active_count": 2,
            "observed_active_count": 0,
            "active_count": 0,
            "active_count_match": False,
            "snapshot_history_enumerations": 1,
            "snapshot_p95_ms": 0.0,
            "panel_p95_ms": None,
        }
        with (
            patch.object(HARNESS, "verify_scale", return_value=result),
            patch("builtins.print"),
        ):
            exit_code = HARNESS.main(
                [
                    "verify-scale",
                    "--root",
                    ".",
                    "--history",
                    "20",
                    "--active",
                    "2",
                ]
            )

        self.assertEqual(exit_code, 5)

    def test_verify_visible_tracks_transitions_and_stops_when_hidden(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = HARNESS.verify_visible(Path(temp_dir), timeout_seconds=2.0)

        self.assertEqual(
            result["state_sequence"],
            ["STOPPED", "STARTING", "RUNNING", "STOPPING", "STOPPED"],
        )
        self.assertEqual(result["history_enumerations"], 0)
        self.assertEqual(result["visible_initial_snapshots"], 1)
        self.assertEqual(result["visible_refresh_snapshots"], 4)
        self.assertEqual(result["hidden_refresh_snapshots"], 0)
        self.assertEqual(result["visible_runtime_watcher_paths"], 1)
        self.assertEqual(result["hidden_runtime_watcher_paths"], 0)

    def test_verify_idle_measures_direct_qt_launcher_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = HARNESS.verify_idle(
                Path(temp_dir),
                stabilization_seconds=0.1,
                observation_seconds=0.3,
                sample_seconds=0.1,
            )

        self.assertEqual(result["launched_pid"], result["measured_pid"])
        self.assertNotEqual(result["observer_pid"], result["measured_pid"])
        self.assertEqual(result["launcher_child_processes_max"], 0)
        self.assertEqual(result["hidden_status_snapshots"], 0)
        self.assertEqual(result["hidden_runtime_watcher_paths"], 0)
        self.assertEqual(result["hidden_active_qtimers"], 0)
        self.assertEqual(result["hidden_named_workers"], [])
        self.assertEqual(result["hidden_runtime_log_reads"], 0)
        self.assertGreaterEqual(result["launcher_threads_max"], 1)
        self.assertGreater(result["launcher_working_set_bytes_max"], 0)


if __name__ == "__main__":
    unittest.main()
