from __future__ import annotations

import ctypes
import os

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QWidget



def _default_buttons_provider():
    if os.name != "nt":
        return QApplication.mouseButtons()
    state = Qt.MouseButton.NoButton
    user32 = ctypes.windll.user32
    if user32.GetAsyncKeyState(0x01) & 0x8000:
        state |= Qt.MouseButton.LeftButton
    if user32.GetAsyncKeyState(0x02) & 0x8000:
        state |= Qt.MouseButton.RightButton
    if user32.GetAsyncKeyState(0x04) & 0x8000:
        state |= Qt.MouseButton.MiddleButton
    return state

class TransientUiController(QObject):
    """Dismiss transient launcher windows on a real outside interaction."""

    def __init__(
        self,
        *,
        windows_provider,
        dismiss_callback,
        buttons_provider=None,
        cursor_provider=None,
        poll_interval_ms=16,
        parent=None,
    ):
        super().__init__(parent)
        self.windows_provider = windows_provider
        self.dismiss_callback = dismiss_callback
        self._shutdown = False
        self._dismiss_scheduled = False
        self.buttons_provider = buttons_provider or _default_buttons_provider
        self.cursor_provider = cursor_provider or QCursor.pos
        self._button_down = False
        self._monitoring_active = False
        self.pointer_timer = QTimer(self)
        self.pointer_timer.setInterval(max(8, int(poll_interval_ms)))
        self.pointer_timer.timeout.connect(self._poll_global_pointer)
        self.application = QApplication.instance()
        self.sync_activity()

    def _windows(self):
        return tuple(
            window
            for window in self.windows_provider()
            if window is not None and window.isVisible()
        )

    def sync_activity(self):
        if self._shutdown:
            return False
        should_monitor = bool(self._windows())
        if should_monitor and not self._monitoring_active:
            self._monitoring_active = True
            self._button_down = self.buttons_provider() != Qt.MouseButton.NoButton
            if self.application is not None:
                self.application.installEventFilter(self)
            self.pointer_timer.start()
        elif not should_monitor and self._monitoring_active:
            self.pointer_timer.stop()
            if self.application is not None:
                self.application.removeEventFilter(self)
            self._monitoring_active = False
            self._button_down = False
        return self._monitoring_active

    def _belongs_to_window(self, watched, window):
        if watched is window:
            return True
        if not isinstance(watched, QWidget):
            return False
        return watched.window() is window or window.isAncestorOf(watched)

    def _inside_transient(self, watched, global_pos: QPoint | None = None):
        windows = self._windows()
        if any(self._belongs_to_window(watched, window) for window in windows):
            return True
        point = global_pos if global_pos is not None else QCursor.pos()
        return any(window.frameGeometry().contains(point) for window in windows)

    @staticmethod
    def _event_global_pos(event):
        getter = getattr(event, "globalPosition", None)
        if callable(getter):
            return getter().toPoint()
        return QCursor.pos()

    def _poll_global_pointer(self):
        if self._shutdown or not self._windows():
            self.sync_activity()
            return
        down = self.buttons_provider() != Qt.MouseButton.NoButton
        if down and not self._button_down:
            self._button_down = True
            if self._windows() and not self._inside_transient(
                None,
                self.cursor_provider(),
            ):
                self._dismiss()
        elif not down:
            self._button_down = False

    def _dismiss(self):
        if self._shutdown:
            return
        self.dismiss_callback()
        self.sync_activity()

    def _schedule_deactivation_check(self):
        if self._shutdown or self._dismiss_scheduled:
            return
        self._dismiss_scheduled = True
        QTimer.singleShot(0, self._dismiss_if_inactive)

    def _dismiss_if_inactive(self):
        self._dismiss_scheduled = False
        if self._shutdown:
            return
        active = QApplication.activeWindow()
        if active is not None and any(
            active is window or window.isAncestorOf(active) for window in self._windows()
        ):
            return
        self._dismiss()

    def eventFilter(self, watched, event):
        if self._shutdown:
            return False
        event_type = event.type()
        if event_type in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.NonClientAreaMouseButtonPress,
            QEvent.Type.TouchBegin,
        }:
            self._button_down = True
            if self._windows() and not self._inside_transient(
                watched,
                self._event_global_pos(event),
            ):
                self._dismiss()
        elif event_type in {
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.NonClientAreaMouseButtonRelease,
            QEvent.Type.TouchEnd,
        }:
            self._button_down = False
        elif event_type == QEvent.Type.ApplicationDeactivate:
            self._schedule_deactivation_check()
        return False

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        self.pointer_timer.stop()
        if self._monitoring_active and self.application is not None:
            self.application.removeEventFilter(self)
        self._monitoring_active = False


__all__ = ["TransientUiController"]
