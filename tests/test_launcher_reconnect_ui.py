import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lib.core import AppController
from lib.runtime.models import RunRecord
from lib.runtime.process_identity import get_process_created_at
from lib.session.subprocess_session import SubprocessSessionManager
from multi import AppControlWidget, AppManager, SystemTrayApp


class RecoveredRuntimeInfoTests(unittest.TestCase):
    def _session(self, root):
        return SubprocessSessionManager(
            {"log_dir": str(root / "logs"), "_config_dir": str(root)}
        )

    def test_stopped_info_retains_newest_historical_run_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = self._session(root)
            older = RunRecord.create(
                app_id="history",
                path=str(root),
                command="old",
                run_id="old-run",
                stdout_path=str(root / "old.out.log"),
                stderr_path=str(root / "old.err.log"),
            )
            older.state = "stopped"
            older.created_at = (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ).isoformat()
            newer = RunRecord.create(
                app_id="history",
                path=str(root),
                command="new",
                run_id="new-run",
                stdout_path=str(root / "new.out.log"),
                stderr_path=str(root / "new.err.log"),
            )
            newer.state = "failed"
            newer.exit_code = 7
            newer.created_at = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
            session.registry.save(older)
            session.registry.save(newer)

            info = session.get_info("history")

            self.assertEqual(info["status"], "STOPPED")
            self.assertEqual(info["run_id"], "new-run")
            self.assertEqual(info["stdout_path"], str(root / "new.out.log"))
            self.assertEqual(info["stderr_path"], str(root / "new.err.log"))
            self.assertEqual(info["exit_code"], 7)
            self.assertEqual(info["instances"], 0)

    def test_recovered_manager_uses_same_run_identity_uptime_and_continuing_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = self._session(root)
            created_at = get_process_created_at(os.getpid())
            self.assertIsNotNone(created_at)
            stdout_path = root / "logs" / "recovered" / "same-run.out.log"
            stderr_path = root / "logs" / "recovered" / "same-run.err.log"
            stdout_path.parent.mkdir(parents=True)
            stdout_path.write_text("first line\n", encoding="utf-8")
            stderr_path.write_text("", encoding="utf-8")
            record = RunRecord.create(
                app_id="recovered",
                path=str(root),
                command="unused",
                run_id="same-run",
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
            )
            record.keeper_pid = os.getpid()
            record.keeper_created_at = created_at
            record.root_pid = os.getpid()
            record.root_created_at = created_at
            record.state = "running"
            record.created_at = (
                datetime.now(timezone.utc) - timedelta(minutes=2)
            ).isoformat()
            session.registry.save(record)

            controller = MagicMock()
            controller.config_manager.get_app.return_value = {
                "id": "recovered",
                "name": "Recovered",
                "multi_run": False,
            }
            controller.config_manager.get_log_dir.return_value = str(root / "logs")
            controller.get_app_status.side_effect = lambda name: session.get_info(
                "recovered"
            )
            manager = AppManager(controller, "Recovered")

            info = manager.get_status_info()
            self.assertEqual(info["status"], "RUNNING")
            self.assertEqual(info["run_id"], "same-run")
            self.assertEqual(info["keeper_pid"], os.getpid())
            self.assertEqual(info["pid"], os.getpid())
            self.assertEqual(info["stdout_path"], str(stdout_path))
            self.assertEqual(info["stderr_path"], str(stderr_path))
            self.assertIn("Running (0d 0h 2m", manager.get_status_text(info))
            self.assertEqual(manager.get_log_path("out"), str(stdout_path.resolve()))

            with stdout_path.open("a", encoding="utf-8") as handle:
                handle.write("continued after reconnect\n")
            self.assertIn("continued after reconnect", manager.read_log_tail("out"))


class AutoStartDecisionTests(unittest.TestCase):
    def _controller_for_status(self, status):
        controller = AppController.__new__(AppController)
        controller.config_manager = MagicMock()
        controller.config_manager.get_app.return_value = {
            "id": "auto",
            "name": "Auto",
            "enabled": True,
            "auto_start": True,
            "multi_run": True,
        }
        controller.get_app_status = MagicMock(return_value={"status": status})
        return controller

    def test_recovered_active_or_orphaned_run_suppresses_auto_start_even_multi_run(self):
        for status in ("STARTING", "RUNNING", "STOPPING", "ORPHANED"):
            with self.subTest(status=status):
                controller = self._controller_for_status(status)
                self.assertFalse(controller.should_auto_start("Auto"))

    def test_stopped_auto_start_is_eligible(self):
        controller = self._controller_for_status("STOPPED")
        self.assertTrue(controller.should_auto_start("Auto"))

    def test_tray_reconciles_before_auto_start_and_launches_only_eligible_apps(self):
        events = []

        class FakeController:
            def reconcile(self):
                events.append("reconcile")

            def should_auto_start(self, name):
                events.append(f"check:{name}")
                return name == "Stopped"

        def manager(name):
            return SimpleNamespace(
                name=name,
                launch=lambda: events.append(f"launch:{name}"),
            )

        tray = SimpleNamespace(
            controller=FakeController(),
            managers=[manager("Recovered"), manager("Stopped")],
        )
        SystemTrayApp.auto_start_apps(tray)

        self.assertEqual(
            events,
            ["reconcile", "check:Recovered", "check:Stopped", "launch:Stopped"],
        )


class RecoveredUiStateTests(unittest.TestCase):
    def _manager(self, status_info, multi_run=False):
        controller = MagicMock()
        controller.config_manager.get_app.return_value = {
            "id": "demo",
            "name": "Demo",
            "multi_run": multi_run,
        }
        controller.get_app_status.return_value = status_info
        return AppManager(controller, "Demo")

    def _update_widget(self, manager):
        widget = SimpleNamespace(
            manager=manager,
            lbl_status=MagicMock(),
            btn_start=MagicMock(),
            btn_stop=MagicMock(),
        )
        AppControlWidget.update_ui(widget)
        return widget

    def test_status_text_formats_all_recovered_states_and_instance_count(self):
        cases = [
            ({"status": "STARTING", "instances": 1}, "Starting"),
            ({"status": "RUNNING", "instances": 1, "uptime": "0d 1h 2m 3s"}, "Running (0d 1h 2m 3s)"),
            ({"status": "RUNNING", "instances": 2, "uptime": "ignored"}, "Running (2 instances)"),
            ({"status": "STOPPING", "instances": 1}, "Stopping"),
            ({"status": "ORPHANED", "instances": 1}, "Orphaned"),
            ({"status": "STOPPED", "instances": 0}, "Stopped"),
        ]
        for status_info, expected in cases:
            with self.subTest(status=status_info["status"]):
                self.assertEqual(
                    self._manager(status_info).get_status_text(status_info), expected
                )

    def test_starting_keeps_stop_enabled_and_non_multi_start_disabled(self):
        widget = self._update_widget(
            self._manager({"status": "STARTING", "instances": 1})
        )
        widget.btn_stop.setEnabled.assert_called_once_with(True)
        widget.btn_start.setEnabled.assert_called_once_with(False)
        widget.lbl_status.setText.assert_called_once_with("Starting")

    def test_orphaned_disables_stop_and_stopped_enables_start(self):
        orphaned = self._update_widget(
            self._manager({"status": "ORPHANED", "instances": 1})
        )
        orphaned.btn_stop.setEnabled.assert_called_once_with(False)
        orphaned.btn_start.setEnabled.assert_called_once_with(False)

        stopped = self._update_widget(
            self._manager({"status": "STOPPED", "instances": 0})
        )
        stopped.btn_stop.setEnabled.assert_called_once_with(False)
        stopped.btn_start.setEnabled.assert_called_once_with(True)


class LauncherLifecycleTests(unittest.TestCase):
    class FakeTimer:
        def __init__(self, name, active, events, remaining_ms=-1):
            self.name = name
            self.active = active
            self.events = events
            self.remaining_ms = remaining_ms

        def isActive(self):
            return self.active

        def remainingTime(self):
            return self.remaining_ms if self.active else -1

        def stop(self):
            self.events.append(f"{self.name}:stop")
            self.active = False

        def start(self, *args):
            self.events.append(f"{self.name}:start:{args}")
            self.active = True
            if args:
                self.remaining_ms = args[0]

    class FakeLock:
        def __init__(self, events):
            self.events = events
            self.acquired = True

        def release(self):
            self.events.append("lock:release")
            self.acquired = False

        def acquire(self):
            self.events.append("lock:acquire")
            self.acquired = True
            return True

    @staticmethod
    def _bind_timer_activity_methods(tray):
        tray._capture_launcher_ui_activity = lambda: (
            SystemTrayApp._capture_launcher_ui_activity(tray)
        )
        tray._stop_launcher_ui_activity = lambda: (
            SystemTrayApp._stop_launcher_ui_activity(tray)
        )
        tray._restore_launcher_ui_activity = lambda snapshot: (
            SystemTrayApp._restore_launcher_ui_activity(snapshot)
        )

    def test_restart_releases_lock_before_spawn_and_never_stops_apps(self):
        events = []
        controller = MagicMock()
        lock = SimpleNamespace(
            acquired=True,
            release=lambda: events.append("release"),
            acquire=lambda: events.append("acquire") or True,
        )
        tray = SimpleNamespace(
            _restart_requested=False,
            restart_action=MagicMock(),
            _stop_launcher_ui_activity=lambda: events.append("stop-ui"),
            instance_lock=lock,
            _spawn_replacement_launcher=lambda: events.append("spawn"),
            controller=controller,
        )

        with patch("multi.QApplication.quit", side_effect=lambda: events.append("quit")):
            SystemTrayApp.restart_app(tray)

        self.assertEqual(events, ["stop-ui", "release", "spawn", "quit"])
        controller.stop_all.assert_not_called()
        tray.restart_action.setEnabled.assert_called_once_with(False)

    def test_failed_restart_after_completed_auto_start_restores_only_active_ui_timers(self):
        events = []
        controller = MagicMock()
        auto_start = self.FakeTimer("auto-start", False, events)
        status = self.FakeTimer("status", True, events)
        hover = self.FakeTimer("hover", True, events)
        lock = self.FakeLock(events)
        menu = SimpleNamespace(findChildren=lambda timer_type: [status, hover])
        tray = SimpleNamespace(
            _restart_requested=False,
            restart_action=MagicMock(),
            auto_start_timer=auto_start,
            menu=menu,
            instance_lock=lock,
            _spawn_replacement_launcher=MagicMock(
                side_effect=OSError("spawn failed")
            ),
            showMessage=MagicMock(),
            controller=controller,
        )
        self._bind_timer_activity_methods(tray)

        with patch("multi.QApplication.quit") as quit_app:
            SystemTrayApp.restart_app(tray)

        self.assertEqual(
            events,
            [
                "auto-start:stop",
                "status:stop",
                "hover:stop",
                "lock:release",
                "lock:acquire",
                "status:start:()",
                "hover:start:()",
            ],
        )
        self.assertFalse(auto_start.active)
        self.assertTrue(status.active)
        self.assertTrue(hover.active)
        self.assertTrue(lock.acquired)
        self.assertFalse(tray._restart_requested)
        tray.restart_action.setEnabled.assert_any_call(False)
        tray.restart_action.setEnabled.assert_any_call(True)
        quit_app.assert_not_called()
        controller.start_app.assert_not_called()
        controller.stop_app.assert_not_called()
        controller.stop_all.assert_not_called()
        controller.reconcile.assert_not_called()

    def test_failed_restart_restores_pending_auto_start_remaining_delay(self):
        events = []
        controller = MagicMock()
        auto_start = self.FakeTimer("auto-start", True, events, remaining_ms=437)
        status = self.FakeTimer("status", True, events)
        hidden_hover = self.FakeTimer("hover", False, events)
        lock = self.FakeLock(events)
        menu = SimpleNamespace(findChildren=lambda timer_type: [status, hidden_hover])
        tray = SimpleNamespace(
            _restart_requested=False,
            restart_action=MagicMock(),
            auto_start_timer=auto_start,
            menu=menu,
            instance_lock=lock,
            _spawn_replacement_launcher=MagicMock(
                side_effect=OSError("spawn failed")
            ),
            showMessage=MagicMock(),
            controller=controller,
        )
        self._bind_timer_activity_methods(tray)

        with patch("multi.QApplication.quit") as quit_app:
            SystemTrayApp.restart_app(tray)

        self.assertEqual(
            events,
            [
                "auto-start:stop",
                "status:stop",
                "hover:stop",
                "lock:release",
                "lock:acquire",
                "auto-start:start:(437,)",
                "status:start:()",
            ],
        )
        self.assertTrue(auto_start.active)
        self.assertEqual(auto_start.remaining_ms, 437)
        self.assertTrue(status.active)
        self.assertFalse(hidden_hover.active)
        self.assertTrue(lock.acquired)
        self.assertFalse(tray._restart_requested)
        tray.restart_action.setEnabled.assert_any_call(True)
        quit_app.assert_not_called()
        controller.start_app.assert_not_called()
        controller.stop_app.assert_not_called()
        controller.stop_all.assert_not_called()
        controller.reconcile.assert_not_called()

        suppressed_ids = {"demo"}
        controller.config_manager.get_app.return_value = {
            "id": "demo",
            "name": "Demo",
        }
        tray.managers = [AppManager(controller, "Demo", suppressed_ids)]
        tray.startup_auto_start_suppressed_ids = suppressed_ids
        controller.reset_mock()

        self.assertEqual(tray.startup_auto_start_suppressed_ids, {"demo"})
        self.assertTrue(tray.managers[0].startup_auto_start_suppressed)

        SystemTrayApp.auto_start_apps(tray)

        self.assertEqual(tray.startup_auto_start_suppressed_ids, set())
        self.assertFalse(tray.managers[0].startup_auto_start_suppressed)
        controller.reconcile.assert_called_once_with()
        controller.should_auto_start.assert_not_called()
        controller.start_app.assert_not_called()
        controller.stop_app.assert_not_called()
        controller.stop_all.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows launcher process flags")
    def test_replacement_launcher_is_detached_from_console_and_stdio(self):
        tray = SimpleNamespace()
        with patch("multi.subprocess.Popen") as popen, patch(
            "multi.is_windows", return_value=True
        ), patch("multi.sys.argv", ["multi.py"]):
            SystemTrayApp._spawn_replacement_launcher(tray)

        args, kwargs = popen.call_args
        self.assertEqual(args[0][0], sys.executable)
        self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)

    def test_plain_exit_never_stops_apps(self):
        events = []
        controller = MagicMock()
        tray = SimpleNamespace(
            _stop_launcher_ui_activity=lambda: events.append("stop-ui"),
            controller=controller,
        )
        with patch("multi.QApplication.quit", side_effect=lambda: events.append("quit")):
            SystemTrayApp.exit_app(tray)

        self.assertEqual(events, ["stop-ui", "quit"])
        controller.stop_all.assert_not_called()

    def test_stop_all_apps_remains_explicit(self):
        controller = MagicMock()
        tray = SimpleNamespace(controller=controller)
        SystemTrayApp.stop_all_apps(tray)
        controller.stop_all.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
