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
)
from PyQt6.QtGui import QIcon, QAction, QFont, QGuiApplication
from PyQt6.QtCore import QTimer, pyqtSignal, Qt, QPoint

# Import from modular library
from lib.core import AppController
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
        """Start the app via controller."""
        success = self.controller.start_app(self.name)
        return success, "Started" if success else "Failed"

    def stop_all(self):
        """Stop the app via controller."""
        self.controller.stop_app(self.name)

    def view_logs(self):
        """Open the logs directory for this app (legacy use).

        This is now only used as a fallback when specific log files
        cannot be located.  The new UI exposes separate buttons for
        output and error logs.
        """
        log_dir = self.controller.config_manager.get_log_dir() or os.path.join(
            os.getcwd(), "logs"
        )
        if os.path.exists(log_dir):
            if is_windows():
                os.startfile(log_dir)
            else:
                subprocess.Popen(["xdg-open", log_dir])

    def view_output_log(self):
        """Open the application's stdout log file if present."""
        log_dir = self.controller.config_manager.get_log_dir() or os.path.join(
            os.getcwd(), "logs"
        )
        out_path = os.path.join(log_dir, f"{self.name}.out.log")
        if os.path.exists(out_path):
            if is_windows():
                os.startfile(out_path)
            else:
                subprocess.Popen(["xdg-open", out_path])
        else:
            # fallback to folder so user can inspect
            self.view_logs()

    def view_error_log(self):
        """Open the application's stderr log file if present."""
        log_dir = self.controller.config_manager.get_log_dir() or os.path.join(
            os.getcwd(), "logs"
        )
        err_path = os.path.join(log_dir, f"{self.name}.err.log")
        if os.path.exists(err_path):
            if is_windows():
                os.startfile(err_path)
            else:
                subprocess.Popen(["xdg-open", err_path])
        else:
            self.view_logs()

    def get_status_text(self):
        """Get status text formatted for UI."""
        status_info = self.controller.get_app_status(self.name)
        status = status_info.get("status", "STOPPED")

        if status == "RUNNING":
            uptime = status_info.get("uptime", "unknown")
            return f"Running ({uptime})"

        return "Stopped"

    def get_workdir(self):
        """Return the directory opened when the app name is clicked."""
        return self.controller.get_app_workdir(self.name)

    def get_log_path(self, stream):
        """Return the stdout/stderr log path for this app."""
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
        self.lbl_status.setFixedWidth(120)
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
        status = self.manager.get_status_text()
        self.lbl_status.setText(status)
        is_running = "Running" in status
        
        if is_running:
            self.lbl_status.setStyleSheet("color: green;")
            self.btn_stop.setEnabled(True)
            
            # Disable start if multi_run is false
            multi_run = self.manager.app_config.get("multi_run", False)
            self.btn_start.setEnabled(multi_run)
        else:
            self.lbl_status.setStyleSheet("color: red;")
            self.btn_stop.setEnabled(False)
            self.btn_start.setEnabled(True)

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
    def __init__(self, icon, parent=None):
        super().__init__(icon, parent)
        self.managers = []
        self.controller = None
        self.load_config()

        # Setup Menu
        self.menu = QMenu()
        self.refresh_menu()
        self.setContextMenu(self.menu)

        # Click handler (Left click -> Toast)
        self.activated.connect(self.on_tray_activated)

        # Auto-start configured apps
        QTimer.singleShot(1000, self.auto_start_apps)

    def auto_start_apps(self):
        print("Auto-starting apps with auto_start=true...")
        for mgr in self.managers:
            # Check if app has auto_start: true in its config
            if mgr.app_config.get('auto_start', False):
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

        restart_action = QAction("Restart Launcher", self.menu)
        restart_action.triggered.connect(self.restart_app)
        self.menu.addAction(restart_action)

        exit_action = QAction("Exit Launcher (Kill All)", self.menu)
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
        # Build message
        active_apps = []
        for mgr in self.managers:
            status = mgr.get_status_text()
            if "Running" in status:
                active_apps.append(f"• {mgr.name}: {status}")

        if active_apps:
            title = f"{len(active_apps)} Apps Running"
            msg = "\n".join(active_apps)
        else:
            title = "Launcher Idle"
            msg = "No applications are currently running."

        self.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Information, 3000)

    def restart_app(self):
        # Kill all apps before restart
        if self.controller:
            self.controller.stop_all()
        
        # Restart current process
        QApplication.quit()
        subprocess.Popen([sys.executable] + sys.argv)

    def exit_app(self):
        # Kill all apps before exit
        if self.controller:
            self.controller.stop_all()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(
        False
    )  # Important: Do not exit when window is closed (since we have no window)

    # Create icon
    if os.path.exists("icon.png"):
        icon = QIcon("icon.png")
    else:
        # Fallback system icon
        icon = app.style().standardIcon(app.style().StandardPixmap.SP_ComputerIcon)

    tray = SystemTrayApp(icon)
    tray.show()

    # Start Toast
    tray.showMessage(
        "Launcher Started",
        "Right-click icon to manage apps.",
        QSystemTrayIcon.MessageIcon.Information,
        2000,
    )

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
