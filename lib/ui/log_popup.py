from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QVBoxLayout

from .inline_log_panel import (
    InlineLogPanel,
    PANEL_EMERGENCY_MIN_HEIGHT,
    PANEL_PREFERRED_HEIGHT,
)

POPUP_MARGIN = 12
POPUP_RIGHT_MARGIN = 0
POPUP_PANEL_GAP = 8


def compute_log_popup_rect(
    requested_size: QSize,
    tray_panel_rect: QRect,
    available_geometry: QRect,
) -> QRect:
    """Place the live-log popup above the tray panel without moving either."""
    width = min(
        max(1, requested_size.width()),
        max(1, available_geometry.width() - POPUP_MARGIN - POPUP_RIGHT_MARGIN),
    )
    height = min(
        max(1, requested_size.height()),
        max(1, available_geometry.height() - 2 * POPUP_MARGIN),
    )

    left_limit = available_geometry.left() + POPUP_MARGIN
    right_limit = available_geometry.right() - POPUP_RIGHT_MARGIN
    top_limit = available_geometry.top() + POPUP_MARGIN
    bottom_limit = available_geometry.bottom() - POPUP_MARGIN

    x = max(left_limit, right_limit - width + 1)
    y = tray_panel_rect.top() - POPUP_PANEL_GAP - height
    y = min(max(y, top_limit), max(top_limit, bottom_limit - height + 1))

    return QRect(int(x), int(y), int(width), int(height))


class LogPopupWindow(QFrame):
    hidden = pyqtSignal()
    reserved_height = PANEL_PREFERRED_HEIGHT + POPUP_PANEL_GAP

    def __init__(
        self,
        parent=None,
        *,
        window_flags=None,
        object_name="logPopupWindow",
        show_without_activating=True,
    ):
        flags = window_flags or (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setObjectName(object_name)
        self.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating,
            bool(show_without_activating),
        )
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.panel = InlineLogPanel(self)
        for widget in (
            self.panel.filter_edit,
            self.panel.line_count,
            self.panel.log_view,
            self.panel.pin_button,
        ):
            widget.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout.addWidget(self.panel)
        self.panel.show()
        self.hide()

    def show_for(self, tray_panel_rect: QRect, available_geometry: QRect):
        requested = QSize(
            max(1, tray_panel_rect.width()),
            PANEL_PREFERRED_HEIGHT,
        )
        geometry = compute_log_popup_rect(
            requested,
            tray_panel_rect,
            available_geometry,
        )
        self.panel.setPreferredHeight(geometry.height())
        self.setGeometry(geometry)
        self.panel.show()
        self.show()
        self.raise_()
        return self.frameGeometry()

    def hideEvent(self, event):
        self.hidden.emit()
        super().hideEvent(event)


class PinnedLogWindow(LogPopupWindow):
    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        super().__init__(
            parent,
            window_flags=flags,
            object_name="pinnedLogWindow",
            show_without_activating=False,
        )
        self.setMinimumSize(520, PANEL_EMERGENCY_MIN_HEIGHT)

    def set_source_title(self, name, stream):
        self.setWindowTitle(f"{str(name).strip()} — {stream}")

    def closeEvent(self, event):
        self.close_requested.emit()
        event.ignore()


__all__ = [
    "LogPopupWindow",
    "PinnedLogWindow",
    "POPUP_MARGIN",
    "POPUP_PANEL_GAP",
    "POPUP_RIGHT_MARGIN",
    "compute_log_popup_rect",
]
