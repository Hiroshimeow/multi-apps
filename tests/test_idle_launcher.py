from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6.QtCore import QFileSystemWatcher, QRect, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame, QStyle, QWidget

import yaml

from lib.ui.app_row import AppControlWidget
from lib.ui.lifecycle_commands import LifecycleCommandController
from lib.ui.log_hover import LogHoverController, LogTarget
from lib.ui.log_popup import LogPopupWindow
from lib.ui.log_preferences import LogPanelPreference
from lib.ui.pinned_logs import PinnedLogManager
from lib.ui.transient_ui import TransientUiController
from multi import SystemTrayApp


_QT_APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout_ms=2000):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())


class _Preferences:
    def __init__(self):
        self.values = {}

    def load(self, app_id):
        return self.values.get(str(app_id), LogPanelPreference())

    def save(self, app_id, preference):
        self.values[str(app_id)] = preference
        return True


class _QuickManager:
    app_id = "demo"
    name = "Demo"

    def stop_all(self):
        return True


class IdleLauncherArchitectureTests(unittest.TestCase):
    def test_app_row_is_presenter_without_status_timer_or_constructor_query(self):
        manager = MagicMock()
        manager.name = "Demo"
        manager.app_id = "demo"
        manager.app_config = {"id": "demo", "name": "Demo", "multi_run": False}
        manager.get_status_presentation.side_effect = lambda info: {
            "text": info.get("status", "STOPPED"),
            "color": "green",
            "tooltip": "status",
        }
        manager.get_pid_presentation.return_value = {"text": "PID: -", "tooltip": "pid"}
        manager.get_status_info = MagicMock(return_value={"status": "RUNNING", "instances": 1})
        host = QWidget()
        widget = AppControlWidget(manager, host)
        try:
            manager.get_status_info.assert_not_called()
            self.assertFalse(hasattr(widget, "timer"))

            widget.apply_status({"status": "RUNNING", "instances": 1})
            manager.get_status_info.assert_not_called()
            manager.get_status_presentation.assert_called_with(
                {"status": "RUNNING", "instances": 1}
            )
        finally:
            widget.shutdown()
            widget.deleteLater()
            host.deleteLater()
            QApplication.processEvents()

    def test_log_buttons_use_click_for_inline_and_right_click_for_os_file(self):
        manager = MagicMock()
        manager.name = "Demo"
        manager.app_id = "demo"
        manager.app_config = {"id": "demo", "name": "Demo", "multi_run": False}
        manager.get_status_presentation.return_value = {
            "text": "Stopped",
            "color": "red",
            "tooltip": "status",
        }
        manager.get_pid_presentation.return_value = {"text": "PID: -", "tooltip": "pid"}
        log_controller = MagicMock()
        host = QWidget()
        widget = AppControlWidget(
            manager,
            host,
            log_controller=log_controller,
        )
        widget.show()
        try:
            QTest.mouseClick(widget.btn_ologs, Qt.MouseButton.LeftButton)
            log_controller.open_target.assert_called_once_with(widget.log_target, "stdout")
            manager.view_output_log.assert_not_called()

            QTest.mouseClick(widget.btn_ologs, Qt.MouseButton.RightButton)
            manager.view_output_log.assert_called_once_with()
            self.assertEqual(log_controller.open_target.call_count, 1)
        finally:
            widget.shutdown()
            widget.deleteLater()
            host.deleteLater()
            QApplication.processEvents()

    def test_lifecycle_worker_is_created_on_demand_and_released_after_drain(self):
        controller = LifecycleCommandController()
        try:
            self.assertFalse(controller.is_alive())
            self.assertTrue(controller.request_stop(_QuickManager()))
            self.assertTrue(wait_until(lambda: not controller.pending_ids()))
            self.assertTrue(wait_until(lambda: not controller.is_alive()))
        finally:
            controller.shutdown()

    def test_log_hover_is_inert_and_reader_lives_only_while_viewer_is_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "demo.log"
            path.write_text("hello\n", encoding="utf-8")
            popup = LogPopupWindow()
            manager = SimpleNamespace(
                name="Demo",
                get_log_path=lambda _stream: str(path),
            )
            target = LogTarget("demo", manager)
            controller = LogHoverController(
                popup,
                _Preferences(),
                placement_provider=lambda: (QRect(0, 0, 400, 300), QRect(0, 0, 1200, 800)),
                open_delay_ms=0,
                hide_delay_ms=0,
                refresh_interval_ms=10000,
            )
            try:
                self.assertIsNone(controller.reader)
                controller.hover_enter(target, "stdout")
                QApplication.processEvents()
                self.assertIsNone(controller.reader)
                self.assertFalse(popup.isVisible())

                controller.open_target(target, "stdout")
                self.assertIsNotNone(controller.reader)
                self.assertTrue(controller.reader.is_alive())
                controller.hide_popup()
                self.assertIsNone(controller.reader)
            finally:
                controller.shutdown()
                popup.deleteLater()
                QApplication.processEvents()

    def test_real_tray_releases_log_reader_and_preference_worker_on_viewer_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs = root / "logs"
            logs.mkdir()
            (logs / "Demo.out.log").write_text("hello\n", encoding="utf-8")
            config_path = root / "setting.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "global": {"log_dir": str(logs)},
                        "apps": [
                            {
                                "id": "demo",
                                "name": "Demo",
                                "path": str(root),
                                "command": "python -c pass",
                                "enabled": True,
                            }
                        ],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            tray = SystemTrayApp(
                _QT_APP.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                config_path=str(config_path),
                launcher_argv=("multi.py", "--config", str(config_path)),
            )
            tray.auto_start_timer.stop()
            try:
                tray.show_panel()
                QApplication.processEvents()
                row = tray.row_widgets[0]
                QTest.mouseClick(row.btn_ologs, Qt.MouseButton.LeftButton)
                self.assertTrue(
                    wait_until(
                        lambda: tray.log_controller.reader is not None
                        and tray.log_controller.reader.is_alive()
                        and tray.log_preference_owner.is_alive()
                        and tray.log_popup.isVisible()
                    )
                )

                tray.log_controller.hide_popup()
                self.assertTrue(
                    wait_until(
                        lambda: tray.log_controller.reader is None
                        and not tray.log_preference_owner.is_alive()
                    )
                )
            finally:
                tray.shutdown_ui()
                tray.hide()
                tray.deleteLater()
                QApplication.processEvents()

    def test_pinned_reader_is_created_on_first_pin_and_removed_after_last_unpin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "demo.log"
            path.write_text("hello\n", encoding="utf-8")
            manager = SimpleNamespace(name="Demo", get_log_path=lambda _stream: str(path))
            target = LogTarget("demo", manager)
            pinned = PinnedLogManager(
                _Preferences(),
                placement_provider=lambda: (QRect(800, 500, 300, 250), QRect(0, 0, 1200, 800)),
                refresh_interval_ms=10000,
            )
            try:
                self.assertIsNone(pinned.reader)
                pinned.pin(target, "stdout", line_count=20, filter_expression="")
                self.assertIsNotNone(pinned.reader)
                self.assertTrue(pinned.reader.is_alive())
                self.assertTrue(pinned.unpin(("demo", "stdout")))
                self.assertIsNone(pinned.reader)
                self.assertFalse(pinned.refresh_timer.isActive())
            finally:
                pinned.shutdown()
                QApplication.processEvents()

    def test_transient_monitoring_is_active_only_while_a_protected_window_is_visible(self):
        frame = QFrame()
        controller = TransientUiController(
            windows_provider=lambda: (frame,),
            dismiss_callback=lambda: None,
            poll_interval_ms=10000,
        )
        try:
            self.assertFalse(controller.pointer_timer.isActive())
            frame.show()
            QApplication.processEvents()
            controller.sync_activity()
            self.assertTrue(controller.pointer_timer.isActive())

            frame.hide()
            QApplication.processEvents()
            controller.sync_activity()
            self.assertFalse(controller.pointer_timer.isActive())
        finally:
            controller.shutdown()
            frame.deleteLater()
            QApplication.processEvents()

    def test_hidden_tray_has_zero_status_work_timers_watchers_and_workers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "setting.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "global": {"log_dir": str(root / "logs")},
                        "apps": [
                            {
                                "id": "demo",
                                "name": "Demo",
                                "path": str(root),
                                "command": "python -c pass",
                                "enabled": True,
                            }
                        ],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            tray = SystemTrayApp(
                _QT_APP.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                config_path=str(config_path),
                launcher_argv=("multi.py", "--config", str(config_path)),
            )
            tray.auto_start_timer.stop()
            status_snapshot = MagicMock(wraps=tray.controller.get_status_snapshot)
            tray.controller.get_status_snapshot = status_snapshot
            try:
                self.assertFalse(tray.tray_panel.isVisible())
                QTest.qWait(50)
                QApplication.processEvents()
                status_snapshot.assert_not_called()
                self.assertEqual(
                    [timer for timer in tray.findChildren(QTimer) if timer.isActive()],
                    [],
                )
                self.assertEqual(tray.findChildren(QFileSystemWatcher), [])
                forbidden = {
                    "latest-log-reader",
                    "pinned-log-reader",
                    "launcher-command-worker",
                    "log-preference-writer",
                }
                names = {thread.name for thread in threading.enumerate()}
                self.assertTrue(forbidden.isdisjoint(names), names & forbidden)

                tray.show_panel()
                QApplication.processEvents()
                self.assertEqual(status_snapshot.call_count, 1)

                tray.tray_panel.hide()
                QApplication.processEvents()
                status_snapshot.reset_mock()
                QTest.qWait(50)
                QApplication.processEvents()
                status_snapshot.assert_not_called()
                self.assertEqual(
                    [timer for timer in tray.findChildren(QTimer) if timer.isActive()],
                    [],
                )
            finally:
                tray.shutdown_ui()
                tray.hide()
                tray.deleteLater()
                QApplication.processEvents()

    def test_named_idle_workers_are_absent_before_explicit_use(self):
        forbidden = {
            "latest-log-reader",
            "pinned-log-reader",
            "launcher-command-worker",
            "log-preference-writer",
        }
        names = {thread.name for thread in threading.enumerate()}
        self.assertTrue(forbidden.isdisjoint(names), names & forbidden)


if __name__ == "__main__":
    unittest.main()
