from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QFrame, QPushButton, QVBoxLayout

CONTEXT_MARGIN = 8


def compute_context_popup_rect(
    requested_size: QSize,
    anchor: QPoint,
    available_geometry: QRect,
) -> QRect:
    width = min(
        max(1, requested_size.width()),
        max(1, available_geometry.width() - 2 * CONTEXT_MARGIN),
    )
    height = min(
        max(1, requested_size.height()),
        max(1, available_geometry.height() - 2 * CONTEXT_MARGIN),
    )
    left = available_geometry.left() + CONTEXT_MARGIN
    right = available_geometry.right() - CONTEXT_MARGIN
    top = available_geometry.top() + CONTEXT_MARGIN
    bottom = available_geometry.bottom() - CONTEXT_MARGIN
    x = min(max(anchor.x(), left), max(left, right - width + 1))
    y = min(max(anchor.y(), top), max(top, bottom - height + 1))
    return QRect(int(x), int(y), int(width), int(height))


class AppContextPopup(QFrame):
    """Small independent app action popup; never owns the main tray layout."""

    hidden = pyqtSignal()

    def __init__(self, parent=None):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setObjectName("appContextPopup")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        self.run_button = QPushButton("Run with terminal", self)
        self.terminal_button = QPushButton("Open terminal here", self)
        for button in (self.run_button, self.terminal_button):
            button.setFlat(True)
            button.setStyleSheet("text-align: left; padding: 6px 10px;")
            layout.addWidget(button)
        self._run_callback = None
        self._terminal_callback = None
        self.run_button.clicked.connect(self._trigger_run)
        self.terminal_button.clicked.connect(self._trigger_terminal)
        self.hide()

    def show_actions(
        self,
        anchor: QPoint,
        *,
        run_callback,
        run_enabled: bool,
        terminal_callback,
        terminal_enabled: bool,
        run_tooltip: str = "",
        terminal_tooltip: str = "",
        available_geometry: QRect | None = None,
    ):
        self._run_callback = run_callback if run_enabled else None
        self._terminal_callback = terminal_callback if terminal_enabled else None
        self.run_button.setEnabled(bool(run_enabled))
        self.run_button.setToolTip(str(run_tooltip or ""))
        self.terminal_button.setEnabled(bool(terminal_enabled))
        self.terminal_button.setToolTip(str(terminal_tooltip or ""))
        self.ensurePolished()
        size = self.sizeHint().expandedTo(QSize(190, 1))
        if available_geometry is None:
            screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
            available_geometry = (
                screen.availableGeometry()
                if screen is not None
                else QRect(anchor.x(), anchor.y(), size.width(), size.height())
            )
        self.setGeometry(compute_context_popup_rect(size, anchor, available_geometry))
        self.show()
        self.raise_()
        return self.frameGeometry()

    def _trigger_run(self):
        self._trigger(self._run_callback)

    def _trigger_terminal(self):
        self._trigger(self._terminal_callback)

    def _trigger(self, callback):
        self.hide()
        if callback is not None:
            callback()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self._run_callback = None
        self._terminal_callback = None
        self.hidden.emit()
        super().hideEvent(event)


__all__ = ["AppContextPopup", "compute_context_popup_rect"]
