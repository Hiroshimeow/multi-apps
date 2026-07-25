from __future__ import annotations

import threading
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .log_preferences import LogPanelPreference
from .log_reader import (
    BoundedLogReader,
    DEFAULT_MAX_BYTES,
    LogSnapshot,
    LogSnapshotState,
)


@dataclass(frozen=True, slots=True)
class LogTarget:
    app_id: str
    manager: object


@dataclass(frozen=True, slots=True)
class _ReadRequest:
    request_id: int
    source: object
    max_lines: int
    max_bytes: int


class LatestLogReader(QObject):
    """One worker thread with a replaceable latest request slot."""

    snapshot_ready = pyqtSignal(int, object)

    def __init__(self, reader=None, parent=None):
        super().__init__(parent)
        self._reader = reader or BoundedLogReader()
        self._condition = threading.Condition()
        self._pending: _ReadRequest | None = None
        self._latest_id = 0
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="latest-log-reader",
            daemon=True,
        )
        self._thread.start()

    def request(self, source, *, max_lines, max_bytes=DEFAULT_MAX_BYTES):
        with self._condition:
            if self._closed:
                raise RuntimeError("log reader is shut down")
            self._latest_id += 1
            request = _ReadRequest(
                self._latest_id,
                source,
                int(max_lines),
                int(max_bytes),
            )
            self._pending = request
            self._condition.notify()
            return request.request_id

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                request = self._pending
                self._pending = None

            path = ""
            try:
                path = request.source() if callable(request.source) else request.source
                snapshot = self._reader.read(
                    path,
                    max_lines=request.max_lines,
                    max_bytes=request.max_bytes,
                )
            except Exception as exc:
                snapshot = LogSnapshot(
                    path=str(path),
                    state=LogSnapshotState.UNREADABLE,
                    error=f"{type(exc).__name__}: {exc}",
                )

            with self._condition:
                is_latest = (
                    not self._closed and request.request_id == self._latest_id
                )
            if is_latest:
                self.snapshot_ready.emit(request.request_id, snapshot)

    def shutdown(self, timeout=2.0):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._thread.join(timeout=float(timeout))

    def is_alive(self):
        return self._thread.is_alive()


class LogHoverController(QObject):
    """Own one explicit log viewer, one demand-owned reader, and preferences."""

    viewer_closed = pyqtSignal()

    def __init__(
        self,
        popup,
        preference_store,
        *,
        placement_provider,
        reader=None,
        pin_handler=None,
        is_pinned=None,
        open_delay_ms=80,
        hide_delay_ms=300,
        refresh_interval_ms=500,
        parent=None,
    ):
        super().__init__(parent)
        self.popup = popup
        self.panel = popup.panel
        self.preference_store = preference_store
        self.placement_provider = placement_provider
        self.reader = reader
        self._owns_reader = reader is None
        self.pin_handler = pin_handler
        self.is_pinned = is_pinned or (lambda _target, _stream: False)
        self.current_target: LogTarget | None = None
        self.current_stream: str | None = None
        self._pending_target: LogTarget | None = None
        self._pending_stream: str | None = None
        self._latest_read_id: int | None = None
        self._request_reset_bottom: dict[int, bool] = {}
        self._hover_owner: tuple[LogTarget, str] | None = None
        self._popup_hovered = False
        self._shutdown = False

        self.open_timer = QTimer(self)
        self.open_timer.setSingleShot(True)
        self.open_timer.setInterval(int(open_delay_ms))
        self.open_timer.timeout.connect(self._open_pending)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(int(hide_delay_ms))
        self.hide_timer.timeout.connect(self._hide_if_unkept)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(int(refresh_interval_ms))
        self.refresh_timer.timeout.connect(self.request_refresh)

        if self.reader is not None:
            self.reader.snapshot_ready.connect(self._snapshot_ready)
        self.panel.pointer_entered.connect(self.popup_entered)
        self.panel.pointer_left.connect(self.popup_left)
        self.panel.filter_changed.connect(self._filter_changed)
        self.panel.line_count_changed.connect(self._line_count_changed)
        self.panel.pin_changed.connect(self._pin_changed)

        application = QApplication.instance()
        if application is not None:
            application.focusChanged.connect(self._focus_changed)

    @staticmethod
    def _validate_stream(stream):
        if stream not in {"stdout", "stderr"}:
            raise ValueError("stream must be stdout or stderr")

    def _ensure_reader(self):
        if self.reader is None:
            self.reader = LatestLogReader(parent=self)
            self.reader.snapshot_ready.connect(self._snapshot_ready)
        return self.reader

    def _release_reader(self):
        if not self._owns_reader or self.reader is None:
            return
        reader = self.reader
        try:
            reader.snapshot_ready.disconnect(self._snapshot_ready)
        except (TypeError, RuntimeError):
            pass
        self.reader = None
        reader.shutdown()
        reader.deleteLater()

    def hover_enter(self, target: LogTarget, stream: str):
        self._validate_stream(stream)
        return None

    def _owns_hover(self, target: LogTarget, stream: str):
        owner = self._hover_owner
        return owner is not None and owner[0] is target and owner[1] == stream

    def hover_leave(self, target: LogTarget, stream: str):
        self._validate_stream(stream)
        return None

    def _open_pending(self):
        target = self._pending_target
        stream = self._pending_stream
        self._pending_target = None
        self._pending_stream = None
        if (
            self._shutdown
            or target is None
            or stream is None
            or not self._owns_hover(target, stream)
        ):
            return
        self.open_target(target, stream)

    def open_target(self, target: LogTarget, stream: str):
        self._validate_stream(stream)
        if self._shutdown:
            return

        target_changed = target is not self.current_target
        if target_changed:
            preference = self.preference_store.load(target.app_id)
            self.panel.configure_controls(
                line_count=preference.line_count,
                filter_expression=preference.filter_expression,
                stream=stream,
            )
        else:
            self.panel.prepare_stream(stream)

        self.panel.set_source_name(getattr(target.manager, "name", target.app_id))
        self.panel.set_pin_state(self.is_pinned(target, stream))
        self.current_target = target
        self.current_stream = stream
        self._save_preference()

        tray_rect, available_rect = self.placement_provider()
        self.popup.show_for(tray_rect, available_rect)
        self._ensure_reader()
        self.request_refresh(reset_bottom=True)
        self.refresh_timer.start()

    def request_refresh(self, *, reset_bottom=False):
        if self._shutdown or self.current_target is None:
            return None
        stream_key = "out" if self.current_stream == "stdout" else "err"
        manager = self.current_target.manager
        request_id = self._ensure_reader().request(
            lambda current=manager, selected=stream_key: current.get_log_path(selected),
            max_lines=self.panel.line_count.value(),
            max_bytes=DEFAULT_MAX_BYTES,
        )
        self._latest_read_id = request_id
        self._request_reset_bottom = {request_id: bool(reset_bottom)}
        return request_id

    def _snapshot_ready(self, request_id, snapshot):
        if (
            self._shutdown
            or self.current_target is None
            or request_id != self._latest_read_id
        ):
            return
        reset_bottom = self._request_reset_bottom.pop(request_id, False)
        self.panel.queue_snapshot(snapshot, reset_bottom=reset_bottom)

    def _pin_changed(self, pinned):
        if self.current_target is None or self.current_stream is None:
            self.panel.set_pin_state(False)
            return
        if self.pin_handler is None:
            self.panel.set_pin_state(False)
            return
        accepted = self.pin_handler(
            self.current_target,
            self.current_stream,
            bool(pinned),
            self.panel,
        )
        self.panel.set_pin_state(
            self.is_pinned(self.current_target, self.current_stream)
            if accepted is not False
            else not bool(pinned)
        )
        self.reposition_popup()

    def refresh_pin_state(self):
        if self.current_target is not None and self.current_stream is not None:
            self.panel.set_pin_state(
                self.is_pinned(self.current_target, self.current_stream)
            )

    def reposition_popup(self):
        if not self.popup.isVisible() or self.current_target is None:
            return None
        tray_rect, available_rect = self.placement_provider()
        return self.popup.show_for(tray_rect, available_rect)

    def popup_entered(self):
        if self.current_target is None:
            return
        self._popup_hovered = True
        self.hide_timer.stop()

    def popup_left(self):
        self._popup_hovered = False
        self._schedule_hide()

    def _focus_changed(self, _old, now):
        if self.current_target is None:
            return
        if now is self.popup or (
            now is not None and self.popup.isAncestorOf(now)
        ):
            self.hide_timer.stop()
        elif self._hover_owner is None and not self._popup_hovered:
            self._schedule_hide()

    def _focus_within_popup(self):
        focus = QApplication.focusWidget()
        return focus is self.popup or (
            focus is not None and self.popup.isAncestorOf(focus)
        )

    def _keep_open(self):
        return (
            self._hover_owner is not None
            or self._popup_hovered
            or self._focus_within_popup()
        )

    def _schedule_hide(self):
        if self.current_target is not None and not self._keep_open():
            self.hide_timer.start()

    def _hide_if_unkept(self):
        if not self._keep_open():
            self.hide_popup()

    def _filter_changed(self):
        if self.current_target is None:
            return
        self.panel.apply_cached_filter(reset_bottom=True)
        self._save_preference()

    def _line_count_changed(self, _value):
        if self.current_target is None:
            return
        self._save_preference()
        self.request_refresh(reset_bottom=True)

    def _save_preference(self):
        if self.current_target is None or self.current_stream is None:
            return False
        return self.preference_store.save(
            self.current_target.app_id,
            LogPanelPreference(
                line_count=self.panel.line_count.value(),
                filter_expression=self.panel.filter_edit.text(),
                stream=self.current_stream,
            ),
        )

    def hide_popup(self):
        was_open = self.popup.isVisible() or self.current_target is not None
        self.open_timer.stop()
        self.hide_timer.stop()
        self.refresh_timer.stop()
        self._pending_target = None
        self._pending_stream = None
        self._latest_read_id = None
        self._request_reset_bottom.clear()
        self._hover_owner = None
        self._popup_hovered = False
        self.current_target = None
        self.current_stream = None
        self.popup.hide()
        self.panel.reset()
        self._release_reader()
        if was_open:
            self.viewer_closed.emit()

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        self.hide_popup()

        application = QApplication.instance()
        if application is not None:
            try:
                application.focusChanged.disconnect(self._focus_changed)
            except (TypeError, RuntimeError):
                pass
        signals = [
            (self.panel.pointer_entered, self.popup_entered),
            (self.panel.pointer_left, self.popup_left),
            (self.panel.filter_changed, self._filter_changed),
            (self.panel.line_count_changed, self._line_count_changed),
            (self.panel.pin_changed, self._pin_changed),
        ]
        if self.reader is not None:
            signals.insert(0, (self.reader.snapshot_ready, self._snapshot_ready))
        for signal, callback in signals:
            try:
                signal.disconnect(callback)
            except (TypeError, RuntimeError):
                pass
        reader = self.reader
        self.reader = None
        if reader is not None:
            reader.shutdown()


__all__ = [
    "LatestLogReader",
    "LogHoverController",
    "LogTarget",
]
