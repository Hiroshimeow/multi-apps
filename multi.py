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
from PyQt6.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QMenu,
    QWidget,
    QWidgetAction,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QMessageBox,
)
from PyQt6.QtGui import QIcon, QAction, QFont, QGuiApplication
from PyQt6.QtCore import QTimer, pyqtSignal, Qt, QPoint

# Import from modular library
from lib.core import AppController
from lib.runtime.single_instance import SingleInstanceLock
from lib.utils import is_windows, is_linux

# ==========================================
# 1. WRAPPERS
# ==========================================


class AppManager:
    """
    Thin wrapper around AppController for a specific app.
    Adapts the interface for the GUI.
    """

    def __init__(self, controller, app_name):
        self.controller = controller
        self.name = app_name
        self.app_config = self.controller.config_manager.get_app(app_name)

    def launch(self):
        """Start the app without blocking the Qt event thread for readiness."""
        success = self.controller.start_app(self.name, wait_for_ready=False)
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

    def get_status_text(self, status_info=None):
        """Format all persisted runtime states for the tray UI."""
        status_info = status_info or self.get_status_info()
        status = status_info.get("status", "STOPPED")
        instances = int(status_info.get("instances") or 0)

        if status == "RUNNING":
            if instances > 1:
                return f"Running ({instances} instances)"
            return f"Running ({status_info.get('uptime', 'unknown')})"
        if status == "STARTING":
            return "Starting" if instances <= 1 else f"Starting ({instances} instances)"
        if status == "STOPPING":
            return "Stopping" if instances <= 1 else f"Stopping ({instances} instances)"
        if status == "ORPHANED":
            return "Orphaned" if instances <= 1 else f"Orphaned ({instances} instances)"
        return "Stopped"

    def get_workdir(self):
        """Return the directory opened when the app name is clicked."""
        return self.controller.get_app_workdir(self.name)

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

    def read_log_tail(self, stream, max_lines=7, max_bytes=65536, max_line_chars=220):
        """Read a small tail of one log without loading the full file."""
        path = self.get_log_path(stream)
        label = "output" if stream == "out" else "error"
        if not os.path.exists(path):
            return f"[{label} log does not exist]"

        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                read_size = min(size, max_bytes)
                handle.seek(-read_size, os.SEEK_END)
                raw = handle.read(read_size)
        except OSError as exc:
            return f"[cannot read {label} log: {exc}]"

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()

        # The first line can be partial when reading only the end of a large file.
        if size > read_size and lines:
            lines = lines[1:]
        if not lines:
            return f"[{label} log is empty]"

        tail = []
        for line in lines[-max_lines:]:
            line = line.replace("	", "    ")
            if len(line) > max_line_chars:
                line = line[: max_line_chars - 3] + "..."
            tail.append(line)
        return "\n".join(tail)


# ==========================================
# 2. GUI COMPONENTS (PyQt6)
# ==========================================


class ClickableLabel(QLabel):
    """A QLabel subclass that emits a signal when clicked."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


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


class LiveLogPreview(QLabel):
    """Tooltip-like live preview for the last few lines of one log file."""

    def __init__(self, anchor, title, content_provider, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.anchor = anchor
        self.title = title
        self.content_provider = content_provider
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setFont(QFont("Consolas", 9))
        self.setMargin(9)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            "QLabel {"
            " background: #171717; color: #e8e8e8;"
            " border: 1px solid #5c5c5c; border-radius: 5px;"
            " padding: 3px;"
            "}"
        )

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self.refresh_content)

    def show_preview(self):
        self.refresh_content()
        self._position_near_anchor()
        self.show()
        self.raise_()
        self.refresh_timer.start()

    def hide_preview(self):
        self.refresh_timer.stop()
        self.hide()

    def refresh_content(self):
        body = self.content_provider()
        self.setText(f"{self.title}\n{'-' * 72}\n{body}")
        self.adjustSize()
        if self.isVisible():
            self._position_near_anchor()

    def _position_near_anchor(self):
        anchor_pos = self.anchor.mapToGlobal(QPoint(0, self.anchor.height() + 5))
        screen = QGuiApplication.screenAt(anchor_pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()

        x = anchor_pos.x()
        y = anchor_pos.y()
        if screen is not None:
            bounds = screen.availableGeometry()
            x = min(max(x, bounds.left()), bounds.right() - self.width())
            if y + self.height() > bounds.bottom():
                y = self.anchor.mapToGlobal(QPoint(0, -self.height() - 5)).y()
            y = min(max(y, bounds.top()), bounds.bottom() - self.height())
        self.move(x, y)


class AppControlWidget(QWidget):
    """Widget custom display in Menu: [Name | Status | Start | Stop | O.Logs | E.Logs]

    Clicking the name opens the app directory; the log buttons open specific
    files instead of the folder.
    """

    def __init__(self, manager, parent_menu):
        super().__init__()
        self.manager = manager
        self.parent_menu = parent_menu

        layout = QHBoxLayout()
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        # App Name (clickable to open workdir)
        self.lbl_name = ClickableLabel(f"<b>{manager.name}</b>")
        self.lbl_name.setFixedWidth(120)
        self.lbl_name.clicked.connect(self.on_name_clicked)
        self.lbl_name.setToolTip("Open application working directory")

        # Status
        self.lbl_status = QLabel(manager.get_status_text())
        self.lbl_status.setFixedWidth(170)
        self.lbl_status.setStyleSheet("color: gray;")

        # Start Button (+)
        self.btn_start = QPushButton("Start")
        self.btn_start.setFixedWidth(50)
        self.btn_start.clicked.connect(self.on_start)

        # Stop Button (-)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setFixedWidth(50)
        self.btn_stop.clicked.connect(self.on_stop)

        # Output / Error log buttons. Hover shows a live 7-line preview;
        # clicking still opens the corresponding log file.
        self.btn_ologs = HoverLogButton("O.Logs")
        self.btn_ologs.setFixedWidth(40)
        self.btn_ologs.clicked.connect(self.manager.view_output_log)

        self.btn_elogs = HoverLogButton("E.Logs")
        self.btn_elogs.setFixedWidth(40)
        self.btn_elogs.clicked.connect(self.manager.view_error_log)

        self.output_log_preview = LiveLogPreview(
            self.btn_ologs,
            f"{manager.name} - output log (last 7 lines)",
            lambda: self.manager.read_log_tail("out"),
            self,
        )
        self.error_log_preview = LiveLogPreview(
            self.btn_elogs,
            f"{manager.name} - error log (last 7 lines)",
            lambda: self.manager.read_log_tail("err"),
            self,
        )
        self.btn_ologs.hover_entered.connect(self.output_log_preview.show_preview)
        self.btn_ologs.hover_left.connect(self.output_log_preview.hide_preview)
        self.btn_elogs.hover_entered.connect(self.error_log_preview.show_preview)
        self.btn_elogs.hover_left.connect(self.error_log_preview.hide_preview)

        layout.addWidget(self.lbl_name)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)
        layout.addWidget(self.btn_ologs)
        layout.addWidget(self.btn_elogs)

        self.setLayout(layout)

        # Timer update status UI realtime
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(1000)

        self.update_ui()

    def update_ui(self):
        status_info = self.manager.get_status_info()
        status = status_info.get("status", "STOPPED")
        self.lbl_status.setText(self.manager.get_status_text(status_info))

        color = {
            "STARTING": "#c58b00",
            "RUNNING": "green",
            "STOPPING": "#c58b00",
            "ORPHANED": "#b00020",
            "STOPPED": "gray",
        }.get(status, "gray")
        self.lbl_status.setStyleSheet(f"color: {color};")

        multi_run = self.manager.app_config.get("multi_run", False)
        controllable = status in {"STARTING", "RUNNING", "STOPPING"}
        self.btn_stop.setEnabled(controllable)
        self.btn_start.setEnabled(status == "STOPPED" or multi_run)

    def on_start(self):
        success, msg = self.manager.launch()
        # Update immediately
        self.update_ui()
        # Do not close menu

    def on_stop(self):
        self.manager.stop_all()
        self.update_ui()

    def on_name_clicked(self):
        """Open the same working directory used when launching the app."""
        workdir = self.manager.get_workdir() or os.getcwd()
        if os.path.exists(workdir):
            if is_windows():
                os.startfile(workdir)
            else:
                subprocess.Popen(["xdg-open", workdir])

    def hideEvent(self, event):
        self.output_log_preview.hide_preview()
        self.error_log_preview.hide_preview()
        super().hideEvent(event)


class SystemTrayApp(QSystemTrayIcon):
    def __init__(self, icon, parent=None, instance_lock=None):
        super().__init__(icon, parent)
        self.managers = []
        self.controller = None
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
        self.controller.reconcile()
        for mgr in self.managers:
            if self.controller.should_auto_start(mgr.name):
                print(f"  -> Starting: {mgr.name}")
                mgr.launch()

    def load_config(self):
        # Initialize AppController
        # It handles loading setting.yaml
        self.controller = AppController("setting.yaml")

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
            mgr = AppManager(self.controller, app["name"])
            self.managers.append(mgr)

    def refresh_menu(self):
        self.menu.clear()

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
                widget = AppControlWidget(mgr, self.menu)
                action.setDefaultWidget(widget)
                self.menu.addAction(action)

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

    def _stop_launcher_ui_activity(self):
        if hasattr(self, "auto_start_timer"):
            self.auto_start_timer.stop()
        if hasattr(self, "menu"):
            for timer in self.menu.findChildren(QTimer):
                timer.stop()

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
        return subprocess.Popen([sys.executable] + sys.argv, **kwargs)

    def restart_app(self):
        if self._restart_requested:
            return
        self._restart_requested = True
        self.restart_action.setEnabled(False)
        self._stop_launcher_ui_activity()

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
            self.auto_start_timer.start(1000)
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
    app = QApplication(sys.argv)
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

    tray = SystemTrayApp(icon, instance_lock=instance_lock)
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
