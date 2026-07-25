from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

PANEL_MARGIN = 12
PANEL_RIGHT_MARGIN = 0
PANEL_ANCHOR_GAP = 8


def compute_tray_panel_rect(
    requested_size: QSize,
    anchor_rect: QRect,
    available_geometry: QRect,
) -> QRect:
    """Place a tray panel above its tray icon and clamp it to the screen."""
    width = min(
        max(1, requested_size.width()),
        max(1, available_geometry.width() - PANEL_MARGIN - PANEL_RIGHT_MARGIN),
    )
    height = min(
        max(1, requested_size.height()),
        max(1, available_geometry.height() - 2 * PANEL_MARGIN),
    )

    left_limit = available_geometry.left() + PANEL_MARGIN
    right_limit = available_geometry.right() - PANEL_RIGHT_MARGIN
    top_limit = available_geometry.top() + PANEL_MARGIN
    bottom_limit = available_geometry.bottom() - PANEL_MARGIN

    x = max(left_limit, right_limit - width + 1)

    y = anchor_rect.top() - PANEL_ANCHOR_GAP - height
    if y < top_limit:
        y = bottom_limit - height + 1
    y = min(max(y, top_limit), max(top_limit, bottom_limit - height + 1))

    return QRect(int(x), int(y), int(width), int(height))


class TrayActionButton(QPushButton):
    """Compact left-aligned action used at the bottom of the tray panel."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("text-align: left; padding: 5px 8px;")


class TrayPanelWindow(QFrame):
    hidden = pyqtSignal()

    def __init__(self, title="Launcher Control Center", parent=None):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setObjectName("trayPanelWindow")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        self.header = QLabel(title, self)
        self.header.setContentsMargins(4, 3, 4, 5)
        root.addWidget(self.header)

        self.rows_scroll = QScrollArea(self)
        self.rows_scroll.setWidgetResizable(True)
        self.rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.rows_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.rows_container = QWidget(self.rows_scroll)
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.rows_scroll.setWidget(self.rows_container)
        root.addWidget(self.rows_scroll, 1)

        self.actions_container = QWidget(self)
        self.actions_layout = QVBoxLayout(self.actions_container)
        self.actions_layout.setContentsMargins(0, 4, 0, 0)
        self.actions_layout.setSpacing(0)
        root.addWidget(self.actions_container)

    @staticmethod
    def _clear_layout(layout, *, delete_widgets=False):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                if delete_widgets:
                    widget.deleteLater()
                else:
                    widget.setParent(None)

    def set_rows(self, rows):
        self._clear_layout(self.rows_layout, delete_widgets=True)
        width = self.minimumWidth()
        for row in rows:
            self.rows_layout.addWidget(row)
            width = max(width, row.minimumWidth(), row.sizeHint().width())
        self.rows_layout.addStretch(1)
        self.setMinimumWidth(width + 8)
        self.rows_container.adjustSize()
        self.updateGeometry()

    def clear_actions(self):
        self._clear_layout(self.actions_layout, delete_widgets=True)

    def add_action(self, text, callback, *, enabled=True, tooltip=""):
        button = TrayActionButton(text, self.actions_container)
        button.setEnabled(bool(enabled))
        button.setToolTip(str(tooltip or ""))
        if enabled and callback is not None:
            button.clicked.connect(callback)
        self.actions_layout.addWidget(button)
        return button

    def show_at(
        self,
        anchor_rect: QRect,
        available_geometry: QRect,
        *,
        reserved_top_height=0,
    ):
        self.ensurePolished()
        natural = (
            self.sizeHint()
            .expandedTo(self.minimumSizeHint())
            .expandedTo(self.minimumSize())
        )
        reserved_top_height = max(0, int(reserved_top_height))
        reserved_top = (
            available_geometry.top() + PANEL_MARGIN + reserved_top_height
        )
        anchored_bottom = min(
            available_geometry.bottom() - PANEL_MARGIN,
            anchor_rect.top() - PANEL_ANCHOR_GAP - 1,
        )
        maximum_height = max(1, anchored_bottom - reserved_top + 1)
        requested = QSize(natural.width(), min(natural.height(), maximum_height))
        self.setGeometry(
            compute_tray_panel_rect(requested, anchor_rect, available_geometry)
        )
        self.show()
        actual = self.frameGeometry()
        aligned = compute_tray_panel_rect(
            actual.size(),
            anchor_rect,
            available_geometry,
        )
        if actual.topLeft() != aligned.topLeft():
            delta = aligned.topLeft() - actual.topLeft()
            self.move(self.pos() + delta)
        self.raise_()
        return self.frameGeometry()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self.hidden.emit()
        super().hideEvent(event)


__all__ = [
    "PANEL_ANCHOR_GAP",
    "PANEL_MARGIN",
    "PANEL_RIGHT_MARGIN",
    "TrayActionButton",
    "TrayPanelWindow",
    "compute_tray_panel_rect",
]
