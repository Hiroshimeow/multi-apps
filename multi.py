# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pyqt6",
#     "PyYAML",
# ]
# ///
import sys
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QLabel,
    QVBoxLayout,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QMessageBox,
)
from PyQt6.QtGui import QCursor, QIcon, QGuiApplication
from PyQt6.QtCore import QTimer, Qt, QRect

# Import from modular library
from lib.core import AppController
from lib.runners.command_runner import CommandRunner
from lib.runtime.single_instance import SingleInstanceLock
from lib.ui.app_context_popup import AppContextPopup
from lib.ui.app_tools import AppToolService
from lib.ui.app_row import AppControlWidget, AppNameLabel, HoverLogButton
from lib.ui.log_preference_owner import LogPreferenceOwner
from lib.ui.log_preferences import (
    LogPanelPreferenceStore,
    default_log_preferences_path,
)
from lib.ui.log_reader import (
    BoundedLogReader,
    DEFAULT_MAX_BYTES,
    DEFAULT_TAIL_LINES,
    LogSnapshotState,
)
from lib.ui.lifecycle_commands import LifecycleCommandController
from lib.ui.log_hover import LogHoverController
from lib.ui.log_popup import LogPopupWindow
from lib.ui.pinned_logs import PinnedLogManager
from lib.ui.runtime_refresh import RuntimeRefreshCoordinator
from lib.ui.tray_panel import TrayPanelWindow
from lib.ui.transient_ui import TransientUiController
from lib.utils import is_windows, is_linux


@dataclass(frozen=True, slots=True)
class LauncherArgs:
    config_path: str
    qt_argv: tuple[str, ...]
    launcher_argv: tuple[str, ...]


def parse_launcher_args(argv):
    """Extract launcher-owned arguments while preserving all Qt arguments."""
    values = [str(value) for value in argv] or ["multi.py"]
    program = values[0]
    qt_argv = [program]
    config_value = "setting.yaml"

    index = 1
    while index < len(values):
        value = values[index]
        if value == "--config":
            index += 1
            if index >= len(values):
                raise ValueError("--config requires a path")
            config_value = values[index]
        elif value.startswith("--config="):
            config_value = value.split("=", 1)[1]
            if not config_value:
                raise ValueError("--config requires a path")
        else:
            qt_argv.append(value)
        index += 1

    config_path = str(Path(config_value).expanduser().resolve(strict=False))
    launcher_argv = (program, "--config", config_path, *qt_argv[1:])
    return LauncherArgs(
        config_path=config_path,
        qt_argv=tuple(qt_argv),
        launcher_argv=tuple(launcher_argv),
    )


# ==========================================
# 1. WRAPPERS
# ==========================================


class ArgsEditModel:
    """One-run argument override without parsing shell syntax."""

    def __init__(self, app_config):
        self.command = app_config["command"]
        self.args_text = CommandRunner.args_text(app_config.get("args", []))

    def set_args_text(self, value):
        self.args_text = str(value)

    def args_override(self):
        return CommandRunner.normalize_args([self.args_text])

    def final_command(self):
        return CommandRunner.build_command_text(
            self.command,
            self.args_override(),
        )


class ArgsEditDialog(QDialog):
    """Edit arguments for exactly one manual GUI run."""

    def __init__(self, app_config, parent=None):
        super().__init__(parent)
        self.model = ArgsEditModel(app_config)
        self.setWindowTitle(f"Start {app_config['name']} with arguments")
        self.setMinimumWidth(620)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Arguments"))

        self.args_input = QLineEdit(self.model.args_text)
        self.args_input.setPlaceholderText("Arguments for this run only")
        layout.addWidget(self.args_input)

        layout.addWidget(QLabel("Final command"))
        self.command_preview = QLabel()
        self.command_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.command_preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.command_preview.setWordWrap(True)
        layout.addWidget(self.command_preview)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.args_input.textChanged.connect(self._update_preview)
        self.args_input.returnPressed.connect(self.accept)
        self._update_preview(self.args_input.text())
        self.args_input.selectAll()
        self.args_input.setFocus()

    def _update_preview(self, value):
        self.model.set_args_text(value)
        self.command_preview.setText(self.model.final_command())

    def args_override(self):
        return self.model.args_override()


class AppManager:
    """
    Thin wrapper around AppController for a specific app.
    Adapts the interface for the GUI.
    """

    def __init__(
        self,
        controller,
        app_name,
        startup_auto_start_suppressed_ids=None,
        tool_service=None,
        log_reader=None,
    ):
        self.controller = controller
        self.name = app_name
        self.app_config = self.controller.config_manager.get_app(app_name)
        self._startup_auto_start_suppressed_ids = (
            startup_auto_start_suppressed_ids
            if startup_auto_start_suppressed_ids is not None
            else set()
        )
        self.tool_service = tool_service or AppToolService()
        self.log_reader = log_reader or BoundedLogReader()

    @property
    def app_id(self):
        return str(self.app_config.get("id") or self.name)

    @property
    def startup_auto_start_suppressed(self):
        return self.app_id in self._startup_auto_start_suppressed_ids

    def can_start_manually(self, status_info=None):
        if self.app_config.get("multi_run", False):
            return True
        status_info = status_info or self.get_status_info()
        return status_info.get("status", "STOPPED") == "STOPPED"

    def launch(self, *, manual=False, parent=None, status_info=None):
        """Start without blocking Qt; edit args only for a manual GUI request."""
        args_override = None
        if manual:
            if not self.can_start_manually(status_info):
                return False, "Start is blocked by an active or orphaned run"
            if self.app_config.get("args_edit", False):
                # Dequeue this app from the one-shot startup auto-start before
                # entering QDialog.exec(), which runs a nested Qt event loop.
                self._startup_auto_start_suppressed_ids.add(self.app_id)
                dialog = ArgsEditDialog(self.app_config, parent)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return False, "Cancelled"
                args_override = dialog.args_override()

        kwargs = {"wait_for_ready": False}
        if args_override is not None:
            kwargs["args_override"] = args_override
        success = self.controller.start_app(self.name, **kwargs)
        return success, "Started" if success else "Failed"

    def stop_all(self):
        """Stop the app via controller."""
        return self.controller.stop_app(self.name)

    def get_status_info(self):
        """Return the registry-backed status snapshot for this app."""
        return self.controller.get_app_status(self.name)

    def view_logs(self):
        """Open the directory containing the newest relevant run logs."""
        for stream in ("out", "err"):
            path = self.get_log_path(stream)
            if path:
                log_dir = os.path.dirname(path)
                if os.path.isdir(log_dir):
                    self._open_path(log_dir)
                    return

        log_dir = self.controller.config_manager.get_log_dir() or os.path.join(
            os.getcwd(), "logs"
        )
        if os.path.isdir(log_dir):
            self._open_path(log_dir)

    def view_output_log(self):
        """Open the newest relevant run's stdout log file."""
        self._view_log("out")

    def view_error_log(self):
        """Open the newest relevant run's stderr log file."""
        self._view_log("err")

    def _view_log(self, stream):
        path = self.get_log_path(stream)
        if path and os.path.exists(path):
            self._open_path(path)
        else:
            self.view_logs()

    @staticmethod
    def _open_path(path):
        if is_windows():
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def get_status_presentation(self, status_info=None):
        """Return compact row text, color, and accessible status detail."""
        status_info = status_info or self.get_status_info()
        status = status_info.get("status", "STOPPED")
        instances = int(status_info.get("instances") or 0)

        if status == "RUNNING":
            uptime = status_info.get("uptime")
            active_instances = max(1, instances)
            instance_text = (
                f"{active_instances} active instances"
                if active_instances != 1
                else "1 active instance"
            )
            tooltip_lines = ["Running"]
            if uptime:
                text = uptime
            else:
                text = "—"
                tooltip_lines.append("Elapsed time unavailable")
            tooltip_lines.append(instance_text)
            return {
                "text": text,
                "color": "green",
                "tooltip": "\n".join(tooltip_lines),
            }
        if status == "STARTING":
            text = "Starting" if instances <= 1 else f"Starting ({instances} instances)"
            return {"text": text, "color": "#c58b00", "tooltip": text}
        if status == "STOPPING":
            text = "Stopping" if instances <= 1 else f"Stopping ({instances} instances)"
            return {"text": text, "color": "#c58b00", "tooltip": text}
        if status == "ORPHANED":
            text = "Orphaned" if instances <= 1 else f"Orphaned ({instances} instances)"
            return {"text": text, "color": "#b00020", "tooltip": text}

        last_used_time = status_info.get("last_used_time")
        text = last_used_time or "—"
        tooltip = (
            f"Last used: {last_used_time}"
            if last_used_time
            else "Last-used time unavailable"
        )
        return {"text": text, "color": "#c62828", "tooltip": tooltip}

    def get_status_text(self, status_info=None):
        """Return the compact primary status text used by launcher rows."""
        return self.get_status_presentation(status_info)["text"]

    def get_pid_presentation(self, status_info=None):
        """Return the managed root PID subtitle without implying stale PIDs are live."""
        status_info = status_info or self.get_status_info()
        status = status_info.get("status", "STOPPED")
        pid = int(status_info.get("pid") or 0)
        text = f"PID: {pid}" if pid > 0 else "PID: -"
        if status == "STOPPED":
            tooltip = f"Last run PID: {pid}" if pid > 0 else "Last run PID unavailable"
        else:
            tooltip = (
                f"Managed root PID: {pid}"
                if pid > 0
                else "Managed root PID unavailable"
            )
        return {"text": text, "tooltip": tooltip}

    def get_workdir(self):
        """Return the directory opened when the app name is clicked."""
        return self.controller.get_app_workdir(self.name)

    def open_workdir(self):
        return self.tool_service.open_folder(self.get_workdir())

    def terminal_status(self):
        return self.tool_service.terminal_status(self.get_workdir())

    def open_terminal(self):
        return self.tool_service.open_terminal(self.get_workdir())

    def get_log_path(self, stream):
        """Return the newest active, otherwise newest historical, run log path."""
        status_info = self.get_status_info()
        key = "stdout_path" if stream == "out" else "stderr_path"
        path = status_info.get(key)
        if path:
            return os.path.abspath(path)

        log_dir = self.controller.config_manager.get_log_dir() or os.path.join(
            os.getcwd(), "logs"
        )
        suffix = "out" if stream == "out" else "err"
        return os.path.join(log_dir, f"{self.name}.{suffix}.log")

    def get_log_snapshot(
        self,
        stream,
        *,
        max_lines=DEFAULT_TAIL_LINES,
        max_bytes=DEFAULT_MAX_BYTES,
    ):
        return self.log_reader.read(
            self.get_log_path(stream),
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    def read_log_tail(self, stream, max_lines=7, max_bytes=65536, max_line_chars=220):
        """Return the existing formatted preview from a bounded structured snapshot."""
        snapshot = self.get_log_snapshot(
            stream,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )
        label = "output" if stream == "out" else "error"
        if snapshot.state == LogSnapshotState.MISSING:
            return f"[{label} log does not exist]"
        if snapshot.state == LogSnapshotState.EMPTY:
            return f"[{label} log is empty]"
        if snapshot.state == LogSnapshotState.UNREADABLE:
            return f"[cannot read {label} log: {snapshot.error}]"

        tail = []
        for line in snapshot.lines[-max_lines:]:
            line = line.replace("	", "    ")
            if len(line) > max_line_chars:
                line = line[: max_line_chars - 3] + "..."
            tail.append(line)
        return "\n".join(tail)


# ==========================================
# 2. GUI COMPONENTS (PyQt6)
# ==========================================


class SystemTrayApp(QSystemTrayIcon):
    def __init__(
        self,
        icon,
        parent=None,
        instance_lock=None,
        config_path="setting.yaml",
        launcher_argv=None,
        log_preferences_path=None,
    ):
        super().__init__(icon, parent)
        self.config_path = str(Path(config_path).expanduser().resolve(strict=False))
        preference_path = (
            log_preferences_path
            if log_preferences_path is not None
            else default_log_preferences_path(self.config_path)
        )
        self.log_preference_store = LogPanelPreferenceStore(preference_path)
        self.log_preference_owner = LogPreferenceOwner(self.log_preference_store)
        if launcher_argv is None:
            parsed = parse_launcher_args(sys.argv)
            launcher_argv = (
                parsed.qt_argv[0],
                "--config",
                self.config_path,
                *parsed.qt_argv[1:],
            )
        self.launcher_argv = tuple(launcher_argv)
        self.managers = []
        self.row_widgets = []
        self.controller = None
        self.tool_service = AppToolService()
        self.startup_auto_start_suppressed_ids = set()
        self.instance_lock = instance_lock
        self._restart_requested = False
        self._ui_shutdown = False
        self.status_snapshot_count = 0
        self.load_config()

        self.tray_panel = TrayPanelWindow()
        self.log_popup = LogPopupWindow()
        self.app_context_popup = AppContextPopup()
        self.lifecycle_controller = LifecycleCommandController(parent=self)
        self.pinned_logs = PinnedLogManager(
            self.log_preference_owner,
            placement_provider=self._pinned_log_placement,
            parent=self,
        )
        self.log_controller = LogHoverController(
            self.log_popup,
            self.log_preference_owner,
            placement_provider=self._log_popup_placement,
            pin_handler=self._handle_log_pin,
            is_pinned=self.pinned_logs.is_pinned,
            parent=self,
        )
        self.pinned_logs.changed.connect(self._pinned_logs_changed)
        self.log_controller.viewer_closed.connect(self._log_viewer_closed)
        self.lifecycle_controller.command_finished.connect(self._lifecycle_finished)
        self.tray_panel.hidden.connect(self._tray_panel_hidden)
        runtime_dir = getattr(
            self.controller.session_manager,
            "runtime_dir",
            Path(self.config_path).parent / ".runtime",
        )
        self.runtime_refresh = RuntimeRefreshCoordinator(
            runtime_dir,
            self.refresh_status_snapshot,
            parent=self,
        )
        self.transient_ui = TransientUiController(
            windows_provider=self._protected_windows,
            dismiss_callback=self.hide_transient_ui,
            parent=self,
        )
        self.rebuild_panel()

        self.activated.connect(self.on_tray_activated)
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.shutdown_ui)

        self.auto_start_timer = QTimer(self)
        self.auto_start_timer.setSingleShot(True)
        self.auto_start_timer.timeout.connect(self.auto_start_apps)
        self.auto_start_timer.start(1000)

    def auto_start_apps(self):
        print("Auto-starting apps with auto_start=true...")
        suppressed_ids = getattr(
            self,
            "startup_auto_start_suppressed_ids",
            None,
        )
        try:
            self.controller.reconcile()
            for mgr in self.managers:
                if getattr(mgr, "startup_auto_start_suppressed", False):
                    continue
                if self.controller.should_auto_start(mgr.name):
                    print(f"  -> Starting: {mgr.name}")
                    mgr.launch()
        finally:
            if suppressed_ids is not None:
                suppressed_ids.clear()

    def load_config(self):
        self.controller = AppController(self.config_path)
        tool_service = getattr(self, "tool_service", None)
        if tool_service is None:
            tool_service = AppToolService()
            self.tool_service = tool_service

        apps = self.controller.list_apps()
        for app in apps:
            if not app.get("enabled", True):
                continue

            allowed_os = app.get("os")
            if allowed_os:
                if isinstance(allowed_os, str):
                    allowed_os = [allowed_os]
                allowed_os = [str(x).lower() for x in allowed_os]

                is_visible = False
                if is_windows():
                    is_visible = any(
                        x in ["win", "windows", "win10", "win11"]
                        for x in allowed_os
                    )
                elif is_linux():
                    is_visible = any(
                        x in ["linux", "ubuntu", "debian"] for x in allowed_os
                    )
                if not is_visible:
                    continue

            self.managers.append(
                AppManager(
                    self.controller,
                    app["name"],
                    self.startup_auto_start_suppressed_ids,
                    tool_service,
                )
            )

    def rebuild_panel(self):
        self.log_controller.hide_popup()
        for row in tuple(self.row_widgets):
            row.shutdown()
            row.deleteLater()
        self.row_widgets = [
            AppControlWidget(
                manager,
                parent=self.tray_panel.rows_container,
                result_notifier=self.notify_app_tool_failure,
                log_controller=self.log_controller,
                lifecycle_controller=self.lifecycle_controller,
            )
            for manager in self.managers
        ]
        for row in self.row_widgets:
            row.context_requested.connect(self.show_app_context)
            row.refresh_requested.connect(self._row_refresh_requested)
        if self.row_widgets:
            rows = self.row_widgets
        else:
            empty = QLabel("No apps configured", self.tray_panel.rows_container)
            empty.setContentsMargins(10, 8, 10, 8)
            rows = [empty]
        self.tray_panel.set_rows(rows)

        self.tray_panel.clear_actions()
        config_status = self.tool_service.file_status(self.config_path)
        self.open_config_action = self.tray_panel.add_action(
            "Open Config",
            self.open_config,
            enabled=config_status.ok,
            tooltip=config_status.message,
        )
        self.stop_all_action = self.tray_panel.add_action(
            "Stop All Apps",
            self.stop_all_apps,
        )
        self.restart_action = self.tray_panel.add_action(
            "Restart Launcher",
            self.restart_app,
        )
        self.exit_action = self.tray_panel.add_action(
            "Exit Launcher",
            self.exit_app,
        )

    def _tray_anchor_and_screen(self):
        anchor = self.geometry()
        if anchor.isNull() or not anchor.isValid():
            cursor = QCursor.pos()
            anchor = QRect(cursor.x(), cursor.y(), 1, 1)
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return anchor, QRect(anchor.x(), anchor.y(), 1, 1)
        return anchor, screen.availableGeometry()

    def _pinned_log_placement(self):
        tray_rect = self.tray_panel.frameGeometry()
        if tray_rect.isNull() or not tray_rect.isValid():
            _anchor, available = self._tray_anchor_and_screen()
            tray_rect = QRect(
                available.right() - max(1, self.tray_panel.minimumWidth()) + 1,
                available.bottom() - 260,
                max(1, self.tray_panel.minimumWidth()),
                240,
            )
        screen = (
            QGuiApplication.screenAt(tray_rect.center())
            or QGuiApplication.primaryScreen()
        )
        available = screen.availableGeometry() if screen is not None else tray_rect
        return tray_rect, available

    def _log_popup_placement(self):
        tray_rect, available = self._pinned_log_placement()
        return self.pinned_logs.transient_anchor_rect(tray_rect), available

    def _protected_windows(self):
        return (
            self.tray_panel,
            self.log_popup,
            self.app_context_popup,
            *self.pinned_logs.windows(),
        )

    def _tray_panel_hidden(self):
        self.runtime_refresh.stop()
        self.log_controller.hide_popup()
        self.app_context_popup.hide()
        self._release_idle_log_preferences()
        self._sync_transient_activity()

    def hide_transient_ui(self):
        self.app_context_popup.hide()
        self.log_controller.hide_popup()
        self.tray_panel.hide()
        self._release_idle_log_preferences()
        self._sync_transient_activity()

    def show_app_context(self, manager, global_pos):
        status = manager.terminal_status()
        self.app_context_popup.show_action(
            global_pos,
            text="Open terminal here",
            callback=lambda current=manager: self._open_terminal(current),
            enabled=status.ok,
            tooltip=status.message,
        )
        self._sync_transient_activity()

    def _open_terminal(self, manager):
        result = manager.open_terminal()
        if not result.ok:
            self.showMessage(
                "Open terminal failed",
                f"{manager.name}: {result.message}",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

    def _handle_log_pin(self, target, stream, pinned, panel):
        key = self.pinned_logs.key_for(target, stream)
        if pinned:
            self.pinned_logs.pin(
                target,
                stream,
                line_count=panel.line_count.value(),
                filter_expression=panel.filter_edit.text(),
            )
        else:
            self.pinned_logs.unpin(key)
        self._sync_transient_activity()
        return True

    def _pinned_logs_changed(self):
        self.log_controller.refresh_pin_state()
        self.log_controller.reposition_popup()
        self._release_idle_log_preferences()
        self._sync_transient_activity()

    def _log_viewer_closed(self):
        self._release_idle_log_preferences()
        self._sync_transient_activity()

    def _lifecycle_finished(self, app_id, ok, result):
        self._refresh_visible((str(app_id),))
        if not ok:
            message = str(result)
            self.showMessage(
                "Stop failed",
                f"{app_id}: {message}",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

    def _row_refresh_requested(self, app_id):
        self._refresh_visible((str(app_id),))

    def _refresh_visible(self, app_ids=None):
        if self.tray_panel.isVisible():
            return self.refresh_status_snapshot(app_ids)
        return None

    def refresh_status_snapshot(self, app_ids=None):
        self.status_snapshot_count += 1
        requested = tuple(
            str(value)
            for value in (
                app_ids
                if app_ids is not None
                else (manager.app_id for manager in self.managers)
            )
        )
        snapshot = self.controller.get_status_snapshot(requested)
        requested_set = set(requested)
        for row in self.row_widgets:
            app_id = str(row.manager.app_id)
            if app_ids is None or app_id in requested_set:
                row.apply_status(
                    snapshot.get(app_id, {"status": "STOPPED", "instances": 0})
                )
        return snapshot

    def _release_idle_log_preferences(self):
        if self.log_popup.isVisible() or self.pinned_logs.count():
            return
        self.log_preference_owner.request_shutdown()

    def _sync_transient_activity(self):
        transient = getattr(self, "transient_ui", None)
        if transient is not None:
            transient.sync_activity()

    def show_panel(self):
        anchor, available = self._tray_anchor_and_screen()
        self.refresh_status_snapshot()
        self.tray_panel.show_at(
            anchor,
            available,
            reserved_top_height=self.log_popup.reserved_height,
        )
        self.runtime_refresh.start()
        self.pinned_logs.ensure_visible_all()
        self.log_controller.reposition_popup()
        self._sync_transient_activity()

    def toggle_panel(self):
        if self.tray_panel.isVisible():
            self.tray_panel.hide()
        else:
            self.show_panel()

    def refresh_all(self):
        was_visible = self.tray_panel.isVisible()
        self.tray_panel.hide()
        self.managers = []
        self.load_config()
        self.rebuild_panel()
        if was_visible:
            self.show_panel()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self.toggle_panel()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_toast()

    def show_toast(self):
        active_apps = []
        snapshot = self.controller.get_status_snapshot(
            tuple(manager.app_id for manager in self.managers)
        )
        for mgr in self.managers:
            status_info = snapshot.get(
                mgr.app_id,
                {"status": "STOPPED", "instances": 0},
            )
            status = status_info.get("status", "STOPPED")
            if status != "STOPPED":
                active_apps.append(
                    f"? {mgr.name}: {mgr.get_status_text(status_info)}"
                )

        if active_apps:
            title = f"{len(active_apps)} Apps Active"
            msg = "\n".join(active_apps)
        else:
            title = "Launcher Idle"
            msg = "No applications are currently running."
        self.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Information, 3000)

    def open_config(self):
        result = self.tool_service.open_file(self.config_path)
        if not result.ok:
            self.notify_launcher_tool_failure(result)

    def stop_all_apps(self):
        lifecycle_controller = getattr(self, "lifecycle_controller", None)
        if lifecycle_controller is not None:
            return lifecycle_controller.request_stop_all(tuple(self.managers))
        if self.controller:
            return self.controller.stop_all()
        return 0

    def notify_launcher_tool_failure(self, result):
        self.showMessage(
            "Launcher action failed",
            result.message,
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def notify_app_tool_failure(self, app_name, result):
        self.showMessage(
            "Open folder failed",
            f"{app_name}: {result.message}",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def _capture_launcher_ui_activity(self):
        snapshot = {
            "auto_start": None,
            "runtime_refresh": None,
            "ui_timers": [],
        }
        if hasattr(self, "auto_start_timer"):
            timer = self.auto_start_timer
            active = timer.isActive()
            remaining_ms = timer.remainingTime() if active else -1
            snapshot["auto_start"] = (timer, active, remaining_ms)

        timers = []
        runtime_refresh = getattr(self, "runtime_refresh", None)
        if runtime_refresh is not None:
            snapshot["runtime_refresh"] = (
                runtime_refresh,
                runtime_refresh.is_active,
            )
        if hasattr(self, "tray_panel"):
            timers.extend(self.tray_panel.findChildren(QTimer))
        if hasattr(self, "log_controller"):
            timers.extend(self.log_controller.findChildren(QTimer))
        if hasattr(self, "pinned_logs"):
            timers.extend(self.pinned_logs.findChildren(QTimer))
        unique = {id(timer): timer for timer in timers}
        snapshot["ui_timers"] = [
            (timer, timer.isActive()) for timer in unique.values()
        ]
        return snapshot

    def _stop_launcher_ui_activity(self):
        snapshot = self._capture_launcher_ui_activity()
        auto_start = snapshot["auto_start"]
        if auto_start is not None:
            auto_start[0].stop()
        runtime_refresh = getattr(self, "runtime_refresh", None)
        if runtime_refresh is not None:
            runtime_refresh.stop()
        for timer, _was_active in snapshot["ui_timers"]:
            timer.stop()
        return snapshot

    @staticmethod
    def _restore_launcher_ui_activity(snapshot):
        auto_start = snapshot.get("auto_start")
        if auto_start is not None:
            timer, was_active, remaining_ms = auto_start
            if was_active:
                timer.start(max(1, remaining_ms))
        for timer, was_active in snapshot.get("ui_timers", []):
            if was_active:
                timer.start()
        runtime_refresh = snapshot.get("runtime_refresh")
        if runtime_refresh is not None:
            coordinator, was_active = runtime_refresh
            if was_active:
                coordinator.start()

    def shutdown_ui(self):
        if getattr(self, "_ui_shutdown", False):
            return
        self._ui_shutdown = True
        auto_start = getattr(self, "auto_start_timer", None)
        if auto_start is not None:
            auto_start.stop()
        runtime_refresh = getattr(self, "runtime_refresh", None)
        if runtime_refresh is not None:
            runtime_refresh.stop()
        transient = getattr(self, "transient_ui", None)
        if transient is not None:
            transient.shutdown()
        for row in tuple(getattr(self, "row_widgets", ())):
            row.shutdown()
        lifecycle = getattr(self, "lifecycle_controller", None)
        if lifecycle is not None:
            lifecycle.shutdown()
        controller = getattr(self, "log_controller", None)
        if controller is not None:
            controller.shutdown()
        pinned = getattr(self, "pinned_logs", None)
        if pinned is not None:
            pinned.shutdown()
        preferences = getattr(self, "log_preference_owner", None)
        if preferences is not None:
            preferences.shutdown()
        context = getattr(self, "app_context_popup", None)
        if context is not None:
            context.hide()
        popup = getattr(self, "log_popup", None)
        if popup is not None:
            popup.hide()
        panel = getattr(self, "tray_panel", None)
        if panel is not None:
            panel.hide()

    def _shutdown_log_controller(self):
        self.shutdown_ui()

    def _spawn_replacement_launcher(self):
        project_root = os.path.dirname(os.path.abspath(__file__))
        kwargs = {
            "cwd": project_root,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if is_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        launcher_argv = getattr(self, "launcher_argv", tuple(sys.argv))
        return subprocess.Popen([sys.executable] + list(launcher_argv), **kwargs)

    def restart_app(self):
        if self._restart_requested:
            return
        self._restart_requested = True
        self.restart_action.setEnabled(False)
        ui_activity = self._stop_launcher_ui_activity()

        released_lock = bool(self.instance_lock and self.instance_lock.acquired)
        if released_lock:
            self.instance_lock.release()

        try:
            self._spawn_replacement_launcher()
        except Exception as exc:
            lock_restored = not released_lock or self.instance_lock.acquire()
            if not lock_restored:
                self.showMessage(
                    "Restart failed",
                    f"Could not restore launcher lock: {exc}",
                    QSystemTrayIcon.MessageIcon.Critical,
                    5000,
                )
                QApplication.quit()
                return
            self._restart_requested = False
            self.restart_action.setEnabled(True)
            self._restore_launcher_ui_activity(ui_activity)
            self.showMessage(
                "Restart failed",
                str(exc),
                QSystemTrayIcon.MessageIcon.Critical,
                5000,
            )
            return

        SystemTrayApp.shutdown_ui(self)
        QApplication.quit()

    def exit_app(self):
        self._stop_launcher_ui_activity()
        SystemTrayApp.shutdown_ui(self)
        QApplication.quit()


def main():
    try:
        launcher_args = parse_launcher_args(sys.argv)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 2

    app = QApplication(list(launcher_args.qt_argv))
    app.setQuitOnLastWindowClosed(
        False
    )  # Important: Do not exit when window is closed (since we have no window)

    runtime_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".runtime")
    instance_lock = SingleInstanceLock(os.path.join(runtime_dir, "launcher.lock"))
    if not instance_lock.acquire():
        QMessageBox.information(
            None,
            "Launcher already running",
            "Another launcher instance is already active.",
        )
        return 1
    app._single_instance_lock = instance_lock

    # Create icon
    if os.path.exists("icon.png"):
        icon = QIcon("icon.png")
    else:
        # Fallback system icon
        icon = app.style().standardIcon(app.style().StandardPixmap.SP_ComputerIcon)

    tray = SystemTrayApp(
        icon,
        instance_lock=instance_lock,
        config_path=launcher_args.config_path,
        launcher_argv=launcher_args.launcher_argv,
    )
    tray.show()

    # Start Toast
    tray.showMessage(
        "Launcher Started",
        "Right-click icon to manage apps.",
        QSystemTrayIcon.MessageIcon.Information,
        2000,
    )

    try:
        return app.exec()
    finally:
        instance_lock.release()


if __name__ == "__main__":
    sys.exit(main())
