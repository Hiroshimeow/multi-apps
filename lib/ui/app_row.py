from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
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

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.left_clicked.emit()
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


class AppControlWidget(QWidget):
    """One app row; live-log state is owned by the shared hover controller."""

    def __init__(
        self,
        manager,
        parent=None,
        result_notifier=None,
        log_controller=None,
    ):
        super().__init__(parent)
        self.manager = manager
        self.result_notifier = result_notifier
        self.log_controller = log_controller
        self.log_target = LogTarget(manager.app_id, manager)
        self._shutdown = False

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
        self.lbl_name.setToolTip(
            f"{manager.name}\nLeft-click to open the application working directory."
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
        self.btn_ologs.setToolTip("Open output log; hover to show live log popup")
        self.btn_ologs.clicked.connect(self.manager.view_output_log)

        self.btn_elogs = HoverLogButton("E.Logs")
        self.btn_elogs.setToolTip("Open error log; hover to show live log popup")
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
        root_layout.addWidget(self.top_row)

        if self.log_controller is not None:
            self.btn_ologs.hover_entered.connect(self._stdout_entered)
            self.btn_ologs.hover_left.connect(self._stdout_left)
            self.btn_elogs.hover_entered.connect(self._stderr_entered)
            self.btn_elogs.hover_left.connect(self._stderr_left)

        root_layout.activate()
        self.setMinimumWidth(max(self.top_row.sizeHint().width(), 520))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(1000)
        self.update_ui()

    def _stdout_entered(self):
        self.log_controller.hover_enter(self.log_target, "stdout")

    def _stdout_left(self):
        self.log_controller.hover_leave(self.log_target, "stdout")

    def _stderr_entered(self):
        self.log_controller.hover_enter(self.log_target, "stderr")

    def _stderr_left(self):
        self.log_controller.hover_leave(self.log_target, "stderr")

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        self.timer.stop()
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
        self.manager.launch(manual=True, parent=self)
        self.update_ui()

    def on_stop(self):
        self.manager.stop_all()
        self.update_ui()

    def _notify_result(self, result):
        if not result.ok and self.result_notifier is not None:
            self.result_notifier(self.manager.name, result)

    def on_name_clicked(self):
        self._notify_result(self.manager.open_workdir())
