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
    QMenu,
    QWidget,
    QWidgetAction,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QMessageBox,
    QSizePolicy,
)
from PyQt6.QtGui import QIcon, QAction, QGuiApplication
from PyQt6.QtCore import QEvent, QTimer, pyqtSignal, Qt, QPoint, QRect, QSize

# Import from modular library
from lib.core import AppController
from lib.runners.command_runner import CommandRunner
from lib.runtime.single_instance import SingleInstanceLock
from lib.ui.app_tools import AppToolAction, AppToolService
from lib.ui.log_reader import (
    BoundedLogReader,
    DEFAULT_MAX_BYTES,
    DEFAULT_TAIL_LINES,
    LogSnapshotState,
)
from lib.ui.inline_log_panel import (
    InlineLogPanel,
    InlineLogPanelCoordinator,
    NativeScrollableMenuStyle,
    PANEL_PREFERRED_HEIGHT,
    compute_inline_menu_geometry,
)
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

    def can_start_manually(self):
        if self.app_config.get("multi_run", False):
            return True
        return self.get_status_info().get("status", "STOPPED") == "STOPPED"

    def launch(self, *, manual=False, parent=None):
        """Start without blocking Qt; edit args only for a manual GUI request."""
        args_override = None
        if manual:
            if not self.can_start_manually():
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
        self.controller.stop_app(self.name)

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

    def get_configured_tools(self):
        return tuple(
            AppToolAction(
                str(tool["id"]),
                str(tool["type"]),
                str(tool["label"]),
                str(tool["path"]),
            )
            for tool in self.app_config.get("tools", [])
        )

    def folder_status(self):
        return self.tool_service.folder_status(self.get_workdir())

    def terminal_status(self):
        return self.tool_service.terminal_status(self.get_workdir())

    def open_workdir(self):
        return self.tool_service.open_folder(self.get_workdir())

    def open_terminal(self):
        return self.tool_service.open_terminal(self.get_workdir())

    def file_status(self, action):
        return self.tool_service.file_status(action)

    def open_configured_tool(self, action):
        return self.tool_service.open_file(action)

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


class AppNameLabel(QLabel):
    """App label with distinct folder and context-tool input signals."""

    left_clicked = pyqtSignal()
    context_requested = pyqtSignal(QPoint)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.left_clicked.emit()
            super().mousePressEvent(event)
            return
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        event.accept()

    def contextMenuEvent(self, event):
        self.context_requested.emit(event.globalPos())
        event.accept()

    def keyPressEvent(self, event):
        is_menu_key = event.key() == Qt.Key.Key_Menu
        is_shift_f10 = (
            event.key() == Qt.Key.Key_F10
            and bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        )
        if is_menu_key or is_shift_f10:
            self.context_requested.emit(self.mapToGlobal(self.rect().bottomLeft()))
            event.accept()
            return
        super().keyPressEvent(event)


class HoverLogButton(QPushButton):
    """A log button that reports pointer enter/leave without changing clicks."""

    hover_entered = pyqtSignal()
    hover_left = pyqtSignal()

    def enterEvent(self, event):
        self.hover_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_left.emit()
        super().leaveEvent(event)


class AppControlWidget(QWidget):
    """Launcher row with existing controls and one hidden inline log panel."""

    def __init__(
        self,
        manager,
        parent_menu,
        result_notifier=None,
        coordinator=None,
    ):
        super().__init__()
        self.manager = manager
        self.parent_menu = parent_menu
        self.result_notifier = result_notifier
        self._app_context_menu = None
        self.log_coordinator = coordinator or InlineLogPanelCoordinator(parent_menu)
        self._owns_log_coordinator = coordinator is None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.top_row = QWidget(self)
        layout = QHBoxLayout(self.top_row)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        self.name_container = QWidget(self.top_row)
        self.name_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        name_layout = QVBoxLayout(self.name_container)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(1)

        self.lbl_name = AppNameLabel(manager.name)
        self.lbl_name.setTextFormat(Qt.TextFormat.PlainText)
        name_font = self.lbl_name.font()
        name_font.setBold(True)
        self.lbl_name.setFont(name_font)
        self.lbl_name.left_clicked.connect(self.on_name_clicked)
        self.lbl_name.context_requested.connect(self.show_app_context_menu)
        self.lbl_name.setToolTip(
            f"{manager.name}\n"
            "Left-click to open the application working directory.\n"
            "Right-click or press the context-menu key to open app tools."
        )
        self.lbl_name.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.lbl_name.setMinimumWidth(96)
        self.name_container.setMinimumWidth(96)

        self.lbl_pid = QLabel("PID: -")
        pid_font = self.lbl_pid.font()
        if pid_font.pointSize() > 0:
            pid_font.setPointSize(max(7, pid_font.pointSize() - 2))
        self.lbl_pid.setFont(pid_font)
        self.lbl_pid.setStyleSheet("color: #808080;")
        name_layout.addWidget(self.lbl_name)
        name_layout.addWidget(self.lbl_pid)

        self.lbl_status = QLabel()
        timestamp_width = self.lbl_status.fontMetrics().horizontalAdvance(
            "2000-00-00 00:00:00"
        )
        self.lbl_status.setMinimumWidth(timestamp_width + 8)

        self.btn_start = QPushButton("Start")
        self.btn_start.setToolTip(f"Start {manager.name}")
        self.btn_start.clicked.connect(self.on_start)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setToolTip(f"Stop {manager.name}")
        self.btn_stop.clicked.connect(self.on_stop)

        self.btn_ologs = HoverLogButton("O.Logs")
        self.btn_ologs.setToolTip("Open output log; hover to show inline live log")
        self.btn_ologs.clicked.connect(self.manager.view_output_log)

        self.btn_elogs = HoverLogButton("E.Logs")
        self.btn_elogs.setToolTip("Open error log; hover to show inline live log")
        self.btn_elogs.clicked.connect(self.manager.view_error_log)

        for button in (
            self.btn_start,
            self.btn_stop,
            self.btn_ologs,
            self.btn_elogs,
        ):
            self._fit_button_to_caption(button)

        layout.addWidget(self.name_container)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)
        layout.addWidget(self.btn_ologs)
        layout.addWidget(self.btn_elogs)
        layout.setStretch(0, 1)

        self.inline_log_panel = InlineLogPanel(self)
        root_layout.addWidget(self.top_row)
        root_layout.addWidget(self.inline_log_panel)
        self.log_coordinator.register_row(self)
        self.btn_ologs.hover_entered.connect(
            lambda: self.log_coordinator.button_entered(self, "stdout")
        )
        self.btn_ologs.hover_left.connect(
            lambda: self.log_coordinator.button_left(self, "stdout")
        )
        self.btn_elogs.hover_entered.connect(
            lambda: self.log_coordinator.button_entered(self, "stderr")
        )
        self.btn_elogs.hover_left.connect(
            lambda: self.log_coordinator.button_left(self, "stderr")
        )

        root_layout.activate()
        self.setMinimumWidth(max(self.top_row.sizeHint().width(), 520))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(1000)
        self.update_ui()

    @staticmethod
    def _fit_button_to_caption(button):
        text_width = button.fontMetrics().horizontalAdvance(button.text()) + 24
        button.setMinimumWidth(max(button.sizeHint().width(), text_width))

    def update_ui(self):
        status_info = self.manager.get_status_info()
        status = status_info.get("status", "STOPPED")
        presentation = self.manager.get_status_presentation(status_info)
        self.lbl_status.setText(presentation["text"])
        self.lbl_status.setStyleSheet(f"color: {presentation['color']};")
        self.lbl_status.setToolTip(presentation["tooltip"])

        pid_presentation = self.manager.get_pid_presentation(status_info)
        self.lbl_pid.setText(pid_presentation["text"])
        self.lbl_pid.setToolTip(pid_presentation["tooltip"])

        multi_run = self.manager.app_config.get("multi_run", False)
        controllable = status in {"STARTING", "RUNNING", "STOPPING"}
        self.btn_stop.setEnabled(controllable)
        self.btn_start.setEnabled(status == "STOPPED" or multi_run)

    def on_start(self):
        success, msg = self.manager.launch(manual=True, parent=self)
        # Update immediately
        self.update_ui()
        # Do not close menu

    def on_stop(self):
        self.manager.stop_all()
        self.update_ui()

    def _notify_result(self, result):
        if not result.ok and self.result_notifier is not None:
            self.result_notifier(self.manager.name, result)

    def on_name_clicked(self):
        """Open the configured working directory through the app-tool service."""
        self._notify_result(self.manager.open_workdir())

    def _add_context_action(self, menu, text, status, callback):
        action = QAction(text, menu)
        action.setToolTip(status.message)
        action.setEnabled(status.ok)
        if status.ok:
            action.triggered.connect(
                lambda _checked=False, handler=callback: self._notify_result(handler())
            )
        menu.addAction(action)
        return action

    def build_app_context_menu(self):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self._add_context_action(
            menu,
            "Open folder",
            self.manager.folder_status(),
            self.manager.open_workdir,
        )
        self._add_context_action(
            menu,
            "Open terminal here",
            self.manager.terminal_status(),
            self.manager.open_terminal,
        )
        tools = self.manager.get_configured_tools()
        if tools:
            menu.addSeparator()
        for tool in tools:
            self._add_context_action(
                menu,
                tool.label,
                self.manager.file_status(tool),
                lambda selected=tool: self.manager.open_configured_tool(selected),
            )
        return menu

    @staticmethod
    def _bounded_menu_position(menu, requested):
        menu.ensurePolished()
        size = menu.sizeHint()
        screen = QGuiApplication.screenAt(requested) or QGuiApplication.primaryScreen()
        if screen is None:
            return requested
        bounds = screen.availableGeometry()
        x = min(max(requested.x(), bounds.left()), bounds.right() - size.width() + 1)
        y = min(max(requested.y(), bounds.top()), bounds.bottom() - size.height() + 1)
        return QPoint(x, y)

    def _release_app_context_menu(self, menu):
        if self._app_context_menu is menu:
            self._app_context_menu = None
        menu.deleteLater()

    def show_app_context_menu(self, global_pos):
        if self._app_context_menu is not None:
            self._app_context_menu.close()
        menu = self.build_app_context_menu()
        self._app_context_menu = menu
        menu.aboutToHide.connect(
            lambda current=menu: self._release_app_context_menu(current)
        )
        menu.popup(self._bounded_menu_position(menu, global_pos))
        return menu

    def hideEvent(self, event):
        if self._app_context_menu is not None:
            self._app_context_menu.close()
        super().hideEvent(event)


class SystemTrayApp(QSystemTrayIcon):
    def __init__(
        self,
        icon,
        parent=None,
        instance_lock=None,
        config_path="setting.yaml",
        launcher_argv=None,
    ):
        super().__init__(icon, parent)
        self.config_path = str(Path(config_path).expanduser().resolve(strict=False))
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
        self.controller = None
        self.tool_service = AppToolService()
        self.startup_auto_start_suppressed_ids = set()
        self.instance_lock = instance_lock
        self._restart_requested = False
        self.load_config()

        # Setup Menu
        self.menu = QMenu()
        self.refresh_menu()
        self.setContextMenu(self.menu)

        # Click handler (Left click -> Toast)
        self.activated.connect(self.on_tray_activated)

        # Auto-start only after controller/session reconciliation and menu recovery.
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
        # Initialize AppController with the selected launcher config.
        self.controller = AppController(self.config_path)
        tool_service = getattr(self, "tool_service", None)
        if tool_service is None:
            tool_service = AppToolService()
            self.tool_service = tool_service

        # Get all apps
        apps = self.controller.list_apps()

        for app in apps:
            # Check enabled
            if not app.get("enabled", True):
                continue

            # OS Filter
            # Keep OS filtering logic as requested
            allowed_os = app.get("os")
            if allowed_os:
                if isinstance(allowed_os, str):
                    allowed_os = [allowed_os]
                allowed_os = [str(x).lower() for x in allowed_os]

                is_visible = False
                if is_windows():
                    if any(
                        x in ["win", "windows", "win10", "win11"] for x in allowed_os
                    ):
                        is_visible = True
                elif is_linux():
                    if any(x in ["linux", "ubuntu", "debian"] for x in allowed_os):
                        is_visible = True

                if not is_visible:
                    continue

            # Create Manager wrapper
            mgr = AppManager(
                self.controller,
                app["name"],
                self.startup_auto_start_suppressed_ids,
                tool_service,
            )
            self.managers.append(mgr)

    def refresh_menu(self):
        old_coordinator = getattr(self, "log_coordinator", None)
        if old_coordinator is not None:
            old_coordinator.shutdown()
            old_coordinator.deleteLater()
        self._menu_generation = getattr(self, "_menu_generation", 0) + 1

        if not hasattr(self.menu, "_native_scroll_style"):
            self.menu._native_scroll_style = NativeScrollableMenuStyle()
            self.menu._native_scroll_style.setParent(self.menu)
            self.menu.setStyle(self.menu._native_scroll_style)

        self.menu.clear()
        self.menu.setMinimumWidth(0)
        self.menu.setMaximumWidth(16777215)
        self.menu.setMaximumHeight(16777215)
        self._inline_menu_base_geometry = QRect()
        self.log_coordinator = InlineLogPanelCoordinator(
            self.menu,
            generation=self._menu_generation,
        )
        self.log_coordinator.panel_visibility_changed.connect(
            lambda _visible, current=self: SystemTrayApp._resize_menu_for_inline_panel(
                current
            )
        )
        self.menu.aboutToHide.connect(self.log_coordinator.close_for_menu_hide)
        row_minimum_width = 0

        # Header
        header = QAction("Launcher Control Center", self.menu)
        header.setEnabled(False)
        self.menu.addAction(header)
        self.menu.addSeparator()

        # List Apps (Use QWidgetAction to embed custom widget)
        if not self.managers:
            empty_action = QAction("No apps configured", self.menu)
            empty_action.setEnabled(False)
            self.menu.addAction(empty_action)
        else:
            for mgr in self.managers:
                action = QWidgetAction(self.menu)
                widget = AppControlWidget(
                    mgr,
                    self.menu,
                    getattr(self, "notify_app_tool_failure", None),
                    self.log_coordinator,
                )
                action.setDefaultWidget(widget)
                self.menu.addAction(action)
                row_minimum_width = max(
                    row_minimum_width,
                    widget.minimumWidth(),
                    widget.sizeHint().width(),
                )

        self.menu.addSeparator()

        # Global Actions
        refresh_action = QAction("Refresh Menu", self.menu)
        refresh_action.triggered.connect(self.refresh_all)
        self.menu.addAction(refresh_action)

        stop_all_action = QAction("Stop All Apps", self.menu)
        stop_all_action.triggered.connect(self.stop_all_apps)
        self.menu.addAction(stop_all_action)

        self.restart_action = QAction("Restart Launcher", self.menu)
        self.restart_action.triggered.connect(self.restart_app)
        self.menu.addAction(self.restart_action)

        exit_action = QAction("Exit Launcher", self.menu)
        exit_action.triggered.connect(self.exit_app)
        self.menu.addAction(exit_action)

        if row_minimum_width:
            self.menu.setMinimumWidth(
                max(self.menu.minimumSizeHint().width(), row_minimum_width + 8)
            )

    def _resize_menu_for_inline_panel(self):
        if getattr(self, "_inline_menu_resize_in_progress", False):
            return
        self._inline_menu_resize_in_progress = True
        try:
            SystemTrayApp._resize_menu_for_inline_panel_once(self)
        finally:
            self._inline_menu_resize_in_progress = False

    def _resize_menu_for_inline_panel_once(self):
        menu = self.menu
        menu.ensurePolished()
        visible_anchor = menu.frameGeometry() if menu.isVisible() else QRect()

        def invalidate_widget_actions():
            coordinator = getattr(self, "log_coordinator", None)
            current_row = (
                coordinator.current_row if coordinator is not None else None
            )
            if current_row is not None:
                actions = menu.actions()
                current_action = next(
                    (
                        action
                        for action in actions
                        if isinstance(action, QWidgetAction)
                        and action.defaultWidget() is current_row
                    ),
                    None,
                )
                if current_action is not None:
                    index = actions.index(current_action)
                    before = actions[index + 1] if index + 1 < len(actions) else None
                    menu.removeAction(current_action)
                    menu.insertAction(before, current_action)

            widget_actions = [
                action
                for action in menu.actions()
                if isinstance(action, QWidgetAction)
                and action.defaultWidget() is not None
            ]
            for action in widget_actions:
                action.defaultWidget().updateGeometry()
            QApplication.sendEvent(menu, QEvent(QEvent.Type.LayoutRequest))
            for action in widget_actions:
                action.changed.emit()
            menu_layout = menu.layout()
            if menu_layout is not None:
                menu_layout.activate()

        invalidate_widget_actions()
        menu.adjustSize()
        screen_anchor = (
            visible_anchor.center()
            if not visible_anchor.isNull()
            else menu.frameGeometry().center()
        )
        screen = QGuiApplication.screenAt(screen_anchor)
        if screen is None and hasattr(menu, "screen"):
            screen = menu.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return

        panel = None
        coordinator = getattr(self, "log_coordinator", None)
        if coordinator is not None and coordinator.current_row is not None:
            panel = coordinator.current_row.inline_log_panel
        panel_is_open = panel is not None and panel.isVisible()
        base_geometry = getattr(self, "_inline_menu_base_geometry", QRect())
        if panel_is_open and base_geometry.isNull() and not visible_anchor.isNull():
            base_geometry = QRect(visible_anchor)
            self._inline_menu_base_geometry = QRect(base_geometry)
        elif not panel_is_open and not base_geometry.isNull():
            menu.setMaximumWidth(16777215)
            menu.setMaximumHeight(16777215)
            invalidate_widget_actions()
            menu.adjustSize()
            if menu.isVisible():
                menu.setGeometry(base_geometry)
            self._inline_menu_base_geometry = QRect()
            return

        panel_height = panel.height() if panel_is_open else 0
        natural = menu.sizeHint()
        base_height = max(1, natural.height() - panel_height)
        anchor = (
            base_geometry
            if not base_geometry.isNull()
            else visible_anchor if not visible_anchor.isNull() else menu.frameGeometry()
        )
        if anchor.isNull():
            anchor = QRect(screen.availableGeometry().bottomRight(), QSize(1, 1))
        geometry = compute_inline_menu_geometry(
            base_menu_size=QSize(natural.width(), base_height),
            content_width=max(menu.minimumWidth(), natural.width()),
            desired_panel_height=PANEL_PREFERRED_HEIGHT if panel_is_open else 0,
            available_geometry=screen.availableGeometry(),
            anchor_rect=anchor,
            preserve_origin=not visible_anchor.isNull(),
        )
        if panel_is_open:
            panel.setPreferredHeight(geometry.panel_height)
        menu.setMaximumWidth(geometry.width_cap)
        menu.setMaximumHeight(geometry.height_cap)
        invalidate_widget_actions()
        menu.adjustSize()
        if menu.isVisible():
            menu.setGeometry(geometry.menu_rect)
        self._last_inline_menu_geometry = geometry

    def refresh_all(self):
        """Reload config and refresh menu."""
        self.managers = []
        self.load_config()
        self.refresh_menu()

    def on_tray_activated(self, reason):
        # Trigger left click
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_toast()

    def show_toast(self):
        # Build message from recovered registry status, not launcher-local memory.
        active_apps = []
        for mgr in self.managers:
            status_info = mgr.get_status_info()
            status = status_info.get("status", "STOPPED")
            if status != "STOPPED":
                active_apps.append(
                    f"• {mgr.name}: {mgr.get_status_text(status_info)}"
                )

        if active_apps:
            title = f"{len(active_apps)} Apps Active"
            msg = "\n".join(active_apps)
        else:
            title = "Launcher Idle"
            msg = "No applications are currently running."

        self.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Information, 3000)

    def stop_all_apps(self):
        if self.controller:
            self.controller.stop_all()

    def notify_app_tool_failure(self, app_name, result):
        self.showMessage(
            "App tool failed",
            f"{app_name}: {result.message}",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def _capture_launcher_ui_activity(self):
        snapshot = {"auto_start": None, "menu_timers": []}
        if hasattr(self, "auto_start_timer"):
            timer = self.auto_start_timer
            active = timer.isActive()
            remaining_ms = timer.remainingTime() if active else -1
            snapshot["auto_start"] = (timer, active, remaining_ms)
        if hasattr(self, "menu"):
            snapshot["menu_timers"] = [
                (timer, timer.isActive()) for timer in self.menu.findChildren(QTimer)
            ]
        return snapshot

    def _stop_launcher_ui_activity(self):
        snapshot = self._capture_launcher_ui_activity()
        auto_start = snapshot["auto_start"]
        if auto_start is not None:
            auto_start[0].stop()
        for timer, _was_active in snapshot["menu_timers"]:
            timer.stop()
        return snapshot

    @staticmethod
    def _restore_launcher_ui_activity(snapshot):
        auto_start = snapshot.get("auto_start")
        if auto_start is not None:
            timer, was_active, remaining_ms = auto_start
            if was_active:
                timer.start(max(1, remaining_ms))
        for timer, was_active in snapshot.get("menu_timers", []):
            if was_active:
                timer.start()

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

        QApplication.quit()

    def exit_app(self):
        self._stop_launcher_ui_activity()
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
