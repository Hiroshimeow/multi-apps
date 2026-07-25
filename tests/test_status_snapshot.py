from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from lib.core import AppController
from lib.runtime.models import RunRecord
from lib.session.base import BaseSessionManager
from lib.session.subprocess_session import SubprocessSessionManager


class _FallbackSession(BaseSessionManager):
    def __init__(self):
        super().__init__({})
        self.calls = []

    def start(self, runner):
        raise NotImplementedError

    def stop(self, app_name):
        raise NotImplementedError

    def is_running(self, app_name):
        return False

    def get_info(self, app_name):
        self.calls.append(str(app_name))
        return {"status": "RUNNING", "instances": 1, "run_id": str(app_name)}


class StatusSnapshotTests(unittest.TestCase):
    def test_base_session_fallback_returns_defaults_and_one_payload_per_id(self):
        session = _FallbackSession()
        snapshot = session.get_status_snapshot(["alpha", "beta", "alpha"])

        self.assertEqual(tuple(snapshot), ("alpha", "beta"))
        self.assertEqual(session.calls, ["alpha", "beta"])
        self.assertEqual(snapshot["alpha"]["run_id"], "alpha")

    def test_subprocess_snapshot_scans_registry_once_for_multiple_apps(self):
        now = datetime.now(timezone.utc)
        records = [
            RunRecord(
                app_id="alpha",
                run_id="alpha-old",
                path="",
                command="x",
                state="stopped",
                created_at=(now - timedelta(minutes=2)).isoformat(),
                updated_at=(now - timedelta(minutes=2)).isoformat(),
            ),
            RunRecord(
                app_id="alpha",
                run_id="alpha-new",
                path="",
                command="x",
                state="stopped",
                created_at=(now - timedelta(minutes=1)).isoformat(),
                updated_at=(now - timedelta(minutes=1)).isoformat(),
            ),
        ]
        manager = SubprocessSessionManager.__new__(SubprocessSessionManager)
        manager.registry = MagicMock()
        manager.registry.list_records.return_value = records
        manager.registry.update.side_effect = lambda _run_id, **_changes: None
        manager.client = MagicMock()

        snapshot = manager.get_status_snapshot(["alpha", "missing"])

        manager.registry.list_records.assert_called_once_with()
        manager.client.reap_finished.assert_called_once_with()
        self.assertEqual(snapshot["alpha"]["run_id"], "alpha-new")
        self.assertEqual(snapshot["alpha"]["status"], "STOPPED")
        self.assertEqual(snapshot["missing"], {"status": "STOPPED", "instances": 0})

    def test_controller_maps_configured_app_ids_to_one_session_batch(self):
        controller = AppController.__new__(AppController)
        controller.config_manager = MagicMock()
        controller.config_manager.get_apps.return_value = [
            {"id": "alpha-id", "name": "Alpha", "enabled": True},
            {"id": "beta-id", "name": "Beta", "enabled": True},
        ]
        controller.session_manager = MagicMock()
        controller.session_manager.get_status_snapshot.return_value = {
            "Alpha": {"status": "RUNNING", "instances": 1},
            "Beta": {"status": "STOPPED", "instances": 0},
        }

        snapshot = controller.get_status_snapshot(["alpha-id", "beta-id"])

        controller.session_manager.get_status_snapshot.assert_called_once_with(
            ("Alpha", "Beta")
        )
        self.assertEqual(snapshot["alpha-id"]["status"], "RUNNING")
        self.assertEqual(snapshot["beta-id"]["status"], "STOPPED")


if __name__ == "__main__":
    unittest.main()
