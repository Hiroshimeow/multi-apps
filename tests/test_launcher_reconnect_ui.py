import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMenu

from lib.core import AppController
from lib.runtime.models import RunRecord
from lib.runtime.process_identity import get_process_created_at
from lib.session.subprocess_session import SubprocessSessionManager
from lib.ui.log_reader import LogSnapshot, LogSnapshotState
from multi import AppControlWidget, AppManager, SystemTrayApp


_QT_APP = QApplication.instance() or QApplication([])


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
            newer.root_pid = 4321
            newer.created_at = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
            terminal_updated_at = "2026-07-12T03:04:05+00:00"
            expected_last_used = datetime.fromisoformat(
                terminal_updated_at
            ).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            session.registry.save(older)
            with patch(
                "lib.runtime.models.utc_now_iso",
                return_value=terminal_updated_at,
            ):
                session.registry.save(newer)

            info = session.get_info("history")

            self.assertEqual(info["status"], "STOPPED")
            self.assertEqual(info["run_id"], "new-run")
            self.assertEqual(info["pid"], 4321)
            self.assertEqual(info["stdout_path"], str(root / "new.out.log"))
            self.assertEqual(info["stderr_path"], str(root / "new.err.log"))
            self.assertEqual(info["exit_code"], 7)
            self.assertEqual(info["instances"], 0)
            self.assertEqual(info["updated_at"], terminal_updated_at)
            self.assertEqual(info["last_used_time"], expected_last_used)

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
            self.assertTrue(manager.get_status_text(info).startswith("0d 0h 2m"))
            self.assertNotIn("Running", manager.get_status_text(info))
            self.assertEqual(manager.get_log_path("out"), str(stdout_path.resolve()))

            with stdout_path.open("a", encoding="utf-8") as handle:
                handle.write("continued after reconnect\n")
            self.assertIn("continued after reconnect", manager.read_log_tail("out"))

    def test_stopped_hover_preview_reads_newest_historical_run_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stdout_path = root / "history.out.log"
            stderr_path = root / "history.err.log"
            stdout_path.write_text("historical output tail\n", encoding="utf-8")
            stderr_path.write_text("historical error tail\n", encoding="utf-8")

            controller = MagicMock()
            controller.config_manager.get_app.return_value = {
                "id": "history",
                "name": "History",
                "multi_run": False,
            }
            controller.config_manager.get_log_dir.return_value = str(root)
            controller.get_app_status.return_value = {
                "status": "STOPPED",
                "instances": 0,
                "pid": 1234,
                "last_used_time": "2026-07-12 10:04:05",
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
            manager = AppManager(controller, "History")
            menu = QMenu()
            widget = AppControlWidget(manager, menu)
            try:
                self.assertIn(
                    "historical output tail",
                    widget.output_log_preview.content_provider(),
                )
                self.assertIn(
                    "historical error tail",
                    widget.error_log_preview.content_provider(),
                )
                self.assertTrue(widget.btn_ologs.isEnabled())
                self.assertTrue(widget.btn_elogs.isEnabled())
            finally:
                widget.timer.stop()
                widget.output_log_preview.hide_preview()
                widget.error_log_preview.hide_preview()
                widget.deleteLater()
                menu.deleteLater()


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
    def _manager(self, status_info, multi_run=False, name="Demo"):
        controller = MagicMock()
        controller.config_manager.get_app.return_value = {
            "id": "demo",
            "name": name,
            "multi_run": multi_run,
        }
        controller.get_app_status.return_value = status_info
        return AppManager(controller, name)

    def _update_widget(self, manager):
        widget = SimpleNamespace(
            manager=manager,
            lbl_status=MagicMock(),
            lbl_pid=MagicMock(),
            btn_start=MagicMock(),
            btn_stop=MagicMock(),
        )
        AppControlWidget.update_ui(widget)
        return widget

    @staticmethod
    def _dispose_widget(widget):
        widget.timer.stop()
        widget.output_log_preview.hide_preview()
        widget.error_log_preview.hide_preview()
        widget.deleteLater()

    def test_running_primary_text_is_elapsed_only_green_and_instances_in_tooltip(self):
        single_info = {
            "status": "RUNNING",
            "instances": 1,
            "uptime": "0d 1h 2m 3s",
        }
        single = self._manager(single_info).get_status_presentation(single_info)
        self.assertEqual(single["text"], "0d 1h 2m 3s")
        self.assertEqual(single["color"], "green")
        self.assertEqual(single["tooltip"], "Running\n1 active instance")
        self.assertNotIn("Running", single["text"])

        multi_info = {
            "status": "RUNNING",
            "instances": 2,
            "uptime": "0d 0h 5m 6s",
        }
        multiple = self._manager(multi_info).get_status_presentation(multi_info)
        self.assertEqual(multiple["text"], "0d 0h 5m 6s")
        self.assertIn("2 active instances", multiple["tooltip"])

    def test_tmux_running_without_elapsed_metadata_uses_honest_fallback(self):
        status_info = {"status": "RUNNING", "session": "app-demo"}
        presentation = self._manager(status_info).get_status_presentation(status_info)

        self.assertEqual(presentation["text"], "—")
        self.assertEqual(presentation["color"], "green")
        self.assertIn("Elapsed time unavailable", presentation["tooltip"])
        self.assertIn("1 active instance", presentation["tooltip"])
        self.assertNotIn("0d 0h 0m 0s", presentation["text"])
        self.assertNotIn("0 active instances", presentation["tooltip"])

    def test_stopped_primary_text_is_terminal_last_used_red_not_uptime(self):
        status_info = {
            "status": "STOPPED",
            "instances": 0,
            "uptime": "999d 23h 59m 59s",
            "last_used_time": "2026-07-12 10:04:05",
        }
        presentation = self._manager(status_info).get_status_presentation(status_info)
        self.assertEqual(presentation["text"], "2026-07-12 10:04:05")
        self.assertEqual(presentation["color"], "#c62828")
        self.assertEqual(
            presentation["tooltip"],
            "Last used: 2026-07-12 10:04:05",
        )
        self.assertNotIn(status_info["uptime"], presentation["text"])

    def test_tmux_stopped_without_history_uses_honest_fallback_and_remains_start_eligible(self):
        status_info = {"status": "STOPPED"}
        manager = self._manager(status_info)
        presentation = manager.get_status_presentation(status_info)

        self.assertEqual(presentation["text"], "—")
        self.assertEqual(presentation["color"], "#c62828")
        self.assertEqual(presentation["tooltip"], "Last-used time unavailable")
        self.assertNotEqual(presentation["text"], "Never")

        widget = self._update_widget(manager)
        widget.btn_stop.setEnabled.assert_called_once_with(False)
        widget.btn_start.setEnabled.assert_called_once_with(True)
        widget.lbl_status.setText.assert_called_once_with("—")

    def test_transitional_state_text_and_colors_are_preserved(self):
        cases = [
            ("STARTING", 1, "Starting", "#c58b00"),
            ("STARTING", 2, "Starting (2 instances)", "#c58b00"),
            ("STOPPING", 1, "Stopping", "#c58b00"),
            ("ORPHANED", 1, "Orphaned", "#b00020"),
            ("ORPHANED", 2, "Orphaned (2 instances)", "#b00020"),
        ]
        for status, instances, text, color in cases:
            with self.subTest(status=status, instances=instances):
                status_info = {"status": status, "instances": instances}
                presentation = self._manager(status_info).get_status_presentation(
                    status_info
                )
                self.assertEqual(presentation["text"], text)
                self.assertEqual(presentation["color"], color)

    def test_pid_subtitle_uses_current_historical_and_placeholder_meanings(self):
        active = self._manager(
            {"status": "RUNNING", "instances": 1, "pid": 4321}
        ).get_pid_presentation(
            {"status": "RUNNING", "instances": 1, "pid": 4321}
        )
        self.assertEqual(active["text"], "PID: 4321")
        self.assertEqual(active["tooltip"], "Managed root PID: 4321")

        stopped = self._manager(
            {"status": "STOPPED", "instances": 0, "pid": 8765}
        ).get_pid_presentation(
            {"status": "STOPPED", "instances": 0, "pid": 8765}
        )
        self.assertEqual(stopped["text"], "PID: 8765")
        self.assertEqual(stopped["tooltip"], "Last run PID: 8765")

        missing = self._manager(
            {"status": "STOPPED", "instances": 0}
        ).get_pid_presentation({"status": "STOPPED", "instances": 0})
        self.assertEqual(missing["text"], "PID: -")
        self.assertEqual(missing["tooltip"], "Last run PID unavailable")

    def test_button_eligibility_is_unchanged_for_all_states_and_multi_run(self):
        cases = [
            ("STARTING", False, False, True),
            ("RUNNING", False, False, True),
            ("STOPPING", False, False, True),
            ("ORPHANED", False, False, False),
            ("STOPPED", False, True, False),
            ("STARTING", True, True, True),
            ("RUNNING", True, True, True),
            ("STOPPING", True, True, True),
            ("ORPHANED", True, True, False),
            ("STOPPED", True, True, False),
        ]
        for status, multi_run, start_enabled, stop_enabled in cases:
            with self.subTest(status=status, multi_run=multi_run):
                status_info = {"status": status, "instances": 1}
                widget = self._update_widget(
                    self._manager(status_info, multi_run=multi_run)
                )
                widget.btn_start.setEnabled.assert_called_once_with(start_enabled)
                widget.btn_stop.setEnabled.assert_called_once_with(stop_enabled)

    def test_layout_uses_full_captions_pid_subtitle_and_name_stretch(self):
        name = "Screens-trans-chatbot with a fully visible launcher name"
        manager = self._manager(
            {
                "status": "STOPPED",
                "instances": 0,
                "pid": 2468,
                "last_used_time": "2026-07-12 10:04:05",
            },
            name=name,
        )
        menu = QMenu()
        widget = AppControlWidget(manager, menu)
        try:
            self.assertEqual(widget.lbl_name.text(), name)
            self.assertEqual(widget.lbl_name.textFormat(), Qt.TextFormat.PlainText)
            self.assertTrue(widget.lbl_name.font().bold())
            self.assertIn(name, widget.lbl_name.toolTip())
            self.assertIn("working directory", widget.lbl_name.toolTip())
            expected_name_width = (
                widget.lbl_name.fontMetrics().horizontalAdvance(name) + 8
            )
            self.assertGreaterEqual(
                widget.name_container.minimumWidth(),
                expected_name_width,
            )
            self.assertEqual(widget.layout().stretch(0), 1)
            self.assertEqual(widget.lbl_pid.text(), "PID: 2468")
            if widget.lbl_name.font().pointSize() > 0:
                self.assertLess(
                    widget.lbl_pid.font().pointSize(),
                    widget.lbl_name.font().pointSize(),
                )

            captions = {
                widget.btn_start: "Start",
                widget.btn_stop: "Stop",
                widget.btn_ologs: "O.Logs",
                widget.btn_elogs: "E.Logs",
            }
            for button, caption in captions.items():
                self.assertEqual(button.text(), caption)
                required_width = button.fontMetrics().horizontalAdvance(caption) + 24
                self.assertGreaterEqual(button.minimumWidth(), required_width)
                self.assertGreaterEqual(button.minimumWidth(), button.sizeHint().width())
            self.assertGreaterEqual(widget.minimumWidth(), widget.sizeHint().width())
        finally:
            self._dispose_widget(widget)
            menu.deleteLater()

    def test_menu_minimum_width_tracks_embedded_row_size_hint(self):
        manager = self._manager(
            {"status": "STOPPED", "instances": 0},
            name="Long application name for menu sizing",
        )
        menu = QMenu()
        tray = SimpleNamespace(
            menu=menu,
            managers=[manager],
            refresh_all=lambda: None,
            stop_all_apps=lambda: None,
            restart_app=lambda: None,
            exit_app=lambda: None,
        )
        try:
            SystemTrayApp.refresh_menu(tray)
            row_action = next(
                action
                for action in menu.actions()
                if hasattr(action, "defaultWidget") and action.defaultWidget() is not None
            )
            row_widget = row_action.defaultWidget()
            self.assertGreaterEqual(
                menu.minimumWidth(),
                row_widget.minimumWidth() + 8,
            )
        finally:
            for widget in menu.findChildren(AppControlWidget):
                self._dispose_widget(widget)
            menu.deleteLater()


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


class AppManagerStructuredLogTests(unittest.TestCase):
    def _manager(self, root, status=None, log_reader=None):
        controller = MagicMock()
        controller.config_manager.get_app.return_value = {"id": "demo", "name": "Demo"}
        controller.config_manager.get_log_dir.return_value = str(Path(root) / "logs")
        controller.get_app_status.return_value = status or {"status": "STOPPED"}
        return AppManager(controller, "Demo", tool_service=MagicMock(), log_reader=log_reader)

    def test_injected_reader_receives_exact_selected_path_and_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            selected = Path(root) / "active.out.log"
            reader = MagicMock()
            expected = LogSnapshot(str(selected.resolve()), LogSnapshotState.READY, ("line",), 4)
            reader.read.return_value = expected
            manager = self._manager(root, {"status": "RUNNING", "stdout_path": str(selected)}, reader)

            actual = manager.get_log_snapshot("out", max_lines=123, max_bytes=4096)

            self.assertIs(actual, expected)
            reader.read.assert_called_once_with(str(selected.resolve()), max_lines=123, max_bytes=4096)

    def test_historical_path_and_legacy_fallback_are_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            historical = Path(root) / "history.err.log"
            manager = self._manager(root, {"status": "STOPPED", "stderr_path": str(historical)})
            self.assertEqual(manager.get_log_path("err"), str(historical.resolve()))
            fallback = self._manager(root)
            self.assertEqual(fallback.get_log_path("out"), str(Path(root) / "logs" / "Demo.out.log"))

    def test_compatibility_wrapper_formats_states(self):
        with tempfile.TemporaryDirectory() as root:
            reader = MagicMock()
            manager = self._manager(root, log_reader=reader)
            path = str((Path(root) / "x.log").resolve())
            cases = [
                (LogSnapshot(path, LogSnapshotState.MISSING), "[output log does not exist]"),
                (LogSnapshot(path, LogSnapshotState.EMPTY), "[output log is empty]"),
                (LogSnapshot(path, LogSnapshotState.UNREADABLE, error="PermissionError: denied"), "[cannot read output log: PermissionError: denied]"),
            ]
            for snapshot, expected in cases:
                with self.subTest(state=snapshot.state):
                    reader.read.return_value = snapshot
                    self.assertEqual(manager.read_log_tail("out"), expected)

    def test_compatibility_wrapper_keeps_seven_newest_lines_and_presentation_rules(self):
        with tempfile.TemporaryDirectory() as root:
            reader = MagicMock()
            lines = tuple([f"old-{i}" for i in range(3)] + ["tab\there"] + [f"new-{i}" for i in range(6)] + ["x" * 230])
            path = str((Path(root) / "x.log").resolve())
            reader.read.return_value = LogSnapshot(path, LogSnapshotState.READY, lines, 999)
            manager = self._manager(root, log_reader=reader)

            rendered = manager.read_log_tail("out")

            reader.read.assert_called_once_with(manager.get_log_path("out"), max_lines=7, max_bytes=65536)
            shown = rendered.splitlines()
            self.assertEqual(len(shown), 7)
            self.assertNotIn("old-0", rendered)
            self.assertTrue(shown[-1].endswith("..."))
            self.assertLessEqual(len(shown[-1]), 220)

    def test_tab_replacement_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            reader = MagicMock()
            path = str((Path(root) / "x.log").resolve())
            reader.read.return_value = LogSnapshot(path, LogSnapshotState.READY, ("a\tb",), 3)
            manager = self._manager(root, log_reader=reader)
            self.assertEqual(manager.read_log_tail("err"), "a    b")


if __name__ == "__main__":
    unittest.main()
