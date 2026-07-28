from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .log_hover import LogTarget


class AppNameLabel(QLabel):
    """Clickable app label that opens the configured working directory."""

    left_clicked = pyqtSignal()
    context_requested = pyqtSignal(QPoint)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.left_clicked.emit()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

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
    """Log button with live hover and an explicit right-click file action."""

    hover_entered = pyqtSignal()
    hover_left = pyqtSignal()
    secondary_clicked = pyqtSignal()

    def enterEvent(self, event):
        self.hover_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_left.emit()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.secondary_clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class AppControlWidget(QWidget):
    """Presenter-only app row; the tray coordinator owns status refreshes."""

    context_requested = pyqtSignal(object, object)
    refresh_requested = pyqtSignal(str)

    def __init__(
        self,
        manager,
        parent=None,
        result_notifier=None,
        log_controller=None,
        lifecycle_controller=None,
    ):
        super().__init__(parent)
        self.manager = manager
        self.result_notifier = result_notifier
        self.log_controller = log_controller
        self.lifecycle_controller = lifecycle_controller
        self.log_target = LogTarget(manager.app_id, manager)
        self._shutdown = False
        self._status_info = {"status": "STOPPED", "instances": 0}

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
        self.lbl_name.context_requested.connect(self._context_requested)
        self.lbl_name.setToolTip(
            f"{manager.name}\n"
            "Left-click to open the application working directory.\n"
            "Right-click, Menu, or Shift+F10 to open app actions."
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
        self.btn_ologs.setToolTip(
            "Click to view output log; right-click to open the physical file"
        )
        if self.log_controller is not None:
            self.btn_ologs.clicked.connect(
                lambda: self.log_controller.open_target(self.log_target, "stdout")
            )
        else:
            self.btn_ologs.clicked.connect(self.manager.view_output_log)
        self.btn_ologs.secondary_clicked.connect(self.manager.view_output_log)

        self.btn_elogs = HoverLogButton("E.Logs")
        self.btn_elogs.setToolTip(
            "Click to view error log; right-click to open the physical file"
        )
        if self.log_controller is not None:
            self.btn_elogs.clicked.connect(
                lambda: self.log_controller.open_target(self.log_target, "stderr")
            )
        else:
            self.btn_elogs.clicked.connect(self.manager.view_error_log)
        self.btn_elogs.secondary_clicked.connect(self.manager.view_error_log)

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
        root_layout.addWidget(self.top_row)

        if self.log_controller is not None:
            self.btn_ologs.hover_entered.connect(self._stdout_entered)
            self.btn_ologs.hover_left.connect(self._stdout_left)
            self.btn_elogs.hover_entered.connect(self._stderr_entered)
            self.btn_elogs.hover_left.connect(self._stderr_left)
        if self.lifecycle_controller is not None:
            self.lifecycle_controller.pending_changed.connect(self._pending_changed)

        root_layout.activate()
        self.setMinimumWidth(max(self.top_row.sizeHint().width(), 520))

        self.apply_status(self._status_info)

    def _stdout_entered(self):
        self.log_controller.hover_enter(self.log_target, "stdout")

    def _stdout_left(self):
        self.log_controller.hover_leave(self.log_target, "stdout")

    def _stderr_entered(self):
        self.log_controller.hover_enter(self.log_target, "stderr")

    def _stderr_left(self):
        self.log_controller.hover_leave(self.log_target, "stderr")

    def _context_requested(self, global_pos):
        self.context_requested.emit(self.manager, global_pos)

    def _pending_changed(self, app_id, _pending):
        if str(app_id) == str(self.manager.app_id):
            self.update_ui()

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        try:
            self.lbl_name.context_requested.disconnect(self._context_requested)
        except (TypeError, RuntimeError):
            pass
        if self.log_controller is not None:
            for signal, callback in (
                (self.btn_ologs.hover_entered, self._stdout_entered),
                (self.btn_ologs.hover_left, self._stdout_left),
                (self.btn_elogs.hover_entered, self._stderr_entered),
                (self.btn_elogs.hover_left, self._stderr_left),
            ):
                try:
                    signal.disconnect(callback)
                except (TypeError, RuntimeError):
                    pass
        if self.lifecycle_controller is not None:
            try:
                self.lifecycle_controller.pending_changed.disconnect(self._pending_changed)
            except (TypeError, RuntimeError):
                pass
    @staticmethod
    def _fit_button_to_caption(button):
        text_width = button.fontMetrics().horizontalAdvance(button.text()) + 24
        button.setMinimumWidth(max(button.sizeHint().width(), text_width))

    def apply_status(self, status_info):
        self._status_info = dict(status_info or {"status": "STOPPED", "instances": 0})
        self.update_ui()

    def update_ui(self, status_info=None):
        if status_info is not None:
            self._status_info = dict(status_info)
        status_info = getattr(
            self,
            "_status_info",
            {"status": "STOPPED", "instances": 0},
        )
        status = status_info.get("status", "STOPPED")
        presentation = self.manager.get_status_presentation(status_info)
        self.lbl_status.setText(presentation["text"])
        self.lbl_status.setStyleSheet(f"color: {presentation['color']};")
        self.lbl_status.setToolTip(presentation["tooltip"])

        pid_presentation = self.manager.get_pid_presentation(status_info)
        self.lbl_pid.setText(pid_presentation["text"])
        self.lbl_pid.setToolTip(pid_presentation["tooltip"])

        multi_run = self.manager.app_config.get("multi_run", False)
        lifecycle_controller = getattr(self, "lifecycle_controller", None)
        pending = (
            lifecycle_controller is not None
            and lifecycle_controller.is_pending(self.manager.app_id)
        )
        if pending:
            self.lbl_status.setText("Stopping...")
            self.lbl_status.setStyleSheet("color: #c58b00;")
            self.lbl_status.setToolTip("Stop request is running in the background")
        controllable = status in {"STARTING", "RUNNING", "STOPPING"}
        self.btn_stop.setEnabled(controllable and not pending)
        self.btn_start.setEnabled((status == "STOPPED" or multi_run) and not pending)

    def on_start(self):
        self.manager.launch(
            manual=True,
            parent=self,
            status_info=self._status_info,
        )
        self.refresh_requested.emit(str(self.manager.app_id))

    def on_stop(self):
        lifecycle_controller = getattr(self, "lifecycle_controller", None)
        if lifecycle_controller is not None:
            lifecycle_controller.request_stop(self.manager)
        else:
            self.manager.stop_all()
        self.refresh_requested.emit(str(self.manager.app_id))

    def _notify_result(self, result):
        if not result.ok and self.result_notifier is not None:
            self.result_notifier(self.manager.name, result)

    def on_name_clicked(self):
        self._notify_result(self.manager.open_workdir())
