from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QRect, QSize, QTimer, pyqtSignal

from .inline_log_panel import PANEL_EMERGENCY_MIN_HEIGHT, PANEL_PREFERRED_HEIGHT
from .log_hover import LogTarget
from .log_popup import LogPopupWindow
from .log_preferences import LogPanelPreference
from .log_reader import BoundedLogReader, DEFAULT_MAX_BYTES, LogSnapshot, LogSnapshotState

PINNED_GAP = 8
PINNED_MARGIN = 12


@dataclass(frozen=True, slots=True)
class _PinnedReadRequest:
    key: tuple[str, str]
    request_id: int
    source: object
    max_lines: int
    max_bytes: int


class MultiLogReader(QObject):
    """One fair worker with one replaceable pending request per pinned log."""

    snapshot_ready = pyqtSignal(object, int, object)

    def __init__(self, reader=None, reader_factory=None, parent=None):
        super().__init__(parent)
        self._shared_reader = reader
        self._reader_factory = reader_factory or BoundedLogReader
        self._readers = {}
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._latest_ids = {}
        self._next_id = 0
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="pinned-log-reader",
            daemon=True,
        )
        self._thread.start()

    def request(self, key, source, *, max_lines, max_bytes=DEFAULT_MAX_BYTES):
        normalized = (str(key[0]), str(key[1]))
        with self._condition:
            if self._closed:
                raise RuntimeError("pinned log reader is shut down")
            self._next_id += 1
            request = _PinnedReadRequest(
                normalized,
                self._next_id,
                source,
                int(max_lines),
                int(max_bytes),
            )
            self._latest_ids[normalized] = request.request_id
            self._pending[normalized] = request
            self._pending.move_to_end(normalized)
            self._condition.notify()
            return request.request_id

    def remove(self, key):
        normalized = (str(key[0]), str(key[1]))
        with self._condition:
            self._pending.pop(normalized, None)
            self._latest_ids.pop(normalized, None)
            self._readers.pop(normalized, None)

    def _reader_for(self, key):
        if self._shared_reader is not None:
            return self._shared_reader
        reader = self._readers.get(key)
        if reader is None:
            reader = self._reader_factory()
            self._readers[key] = reader
        return reader

    def _run(self):
        while True:
            with self._condition:
                while not self._pending and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                _key, request = self._pending.popitem(last=False)
            path = ""
            try:
                path = request.source() if callable(request.source) else request.source
                snapshot = self._reader_for(request.key).read(
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
                    not self._closed
                    and self._latest_ids.get(request.key) == request.request_id
                )
            if is_latest:
                self.snapshot_ready.emit(request.key, request.request_id, snapshot)

    def shutdown(self, timeout=5.0):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
            self._latest_ids.clear()
            self._readers.clear()
            self._condition.notify_all()
        self._thread.join(timeout=float(timeout))

    def is_alive(self):
        return self._thread.is_alive()


@dataclass(slots=True)
class _PinnedSession:
    key: tuple[str, str]
    target: LogTarget
    stream: str
    window: LogPopupWindow
    latest_read_id: int | None = None
    request_reset_bottom: dict[int, bool] | None = None

    def __post_init__(self):
        if self.request_reset_bottom is None:
            self.request_reset_bottom = {}


def compute_pinned_log_rects(
    count: int,
    requested_size: QSize,
    tray_rect: QRect,
    available: QRect,
):
    """Tile pinned logs above the tray anchor without overlap.

    The grid grows leftward and upward. Width and height shrink only as much as
    needed, never below the renderer emergency height.
    """
    count = max(0, int(count))
    if count == 0:
        return ()

    left = available.left() + PINNED_MARGIN
    right = available.right() - PINNED_MARGIN
    top = available.top() + PINNED_MARGIN
    bottom = min(
        available.bottom() - PINNED_MARGIN,
        tray_rect.top() - PINNED_GAP - 1,
    )
    if bottom < top:
        bottom = available.bottom() - PINNED_MARGIN

    available_width = max(1, right - left + 1)
    available_height = max(PANEL_EMERGENCY_MIN_HEIGHT, bottom - top + 1)
    requested_width = min(max(1, requested_size.width()), available_width)
    requested_height = min(
        max(PANEL_EMERGENCY_MIN_HEIGHT, requested_size.height()),
        available_height,
    )

    max_rows = max(
        1,
        (available_height + PINNED_GAP)
        // (PANEL_EMERGENCY_MIN_HEIGHT + PINNED_GAP),
    )
    rows = min(count, max_rows)
    columns = math.ceil(count / rows)

    fit_width = max(1, (available_width - (columns - 1) * PINNED_GAP) // columns)
    fit_height = max(
        PANEL_EMERGENCY_MIN_HEIGHT,
        (available_height - (rows - 1) * PINNED_GAP) // rows,
    )
    width = min(requested_width, fit_width)
    height = min(requested_height, fit_height)

    grid_width = columns * width + (columns - 1) * PINNED_GAP
    grid_right = min(right, max(left + grid_width - 1, tray_rect.right()))
    grid_left = max(left, grid_right - grid_width + 1)
    if grid_left + grid_width - 1 > right:
        grid_left = right - grid_width + 1

    rects = []
    for index in range(count):
        column = index // rows
        row = index % rows
        x = grid_left + (columns - 1 - column) * (width + PINNED_GAP)
        y = bottom - height + 1 - row * (height + PINNED_GAP)
        rects.append(QRect(int(x), int(y), int(width), int(height)))
    return tuple(rects)


class PinnedLogManager(QObject):
    """Own multiple persistent live-log windows backed by one reader thread."""

    changed = pyqtSignal()

    def __init__(
        self,
        preference_store,
        *,
        placement_provider,
        reader=None,
        refresh_interval_ms=500,
        parent=None,
    ):
        super().__init__(parent)
        self.preference_store = preference_store
        self.placement_provider = placement_provider
        self.reader = reader or MultiLogReader(parent=self)
        self.sessions = OrderedDict()
        self._shutdown = False
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(int(refresh_interval_ms))
        self.refresh_timer.timeout.connect(self.refresh_all)
        self.reader.snapshot_ready.connect(self._snapshot_ready)

    @staticmethod
    def key_for(target, stream):
        return (str(target.app_id), str(stream))

    def count(self):
        return len(self.sessions)

    def windows(self):
        return tuple(session.window for session in self.sessions.values())

    def is_pinned(self, target, stream):
        return self.key_for(target, stream) in self.sessions

    def pin(self, target, stream, *, line_count, filter_expression):
        if self._shutdown:
            return None
        key = self.key_for(target, stream)
        existing = self.sessions.get(key)
        if existing is not None:
            existing.window.show()
            existing.window.raise_()
            return existing.window
        window = LogPopupWindow()
        window.panel.set_source_name(getattr(target.manager, "name", target.app_id))
        window.panel.configure_controls(
            line_count=line_count,
            filter_expression=filter_expression,
            stream=stream,
        )
        window.panel.set_pin_state(True)
        session = _PinnedSession(key, target, stream, window)
        self.sessions[key] = session
        window.panel.pin_changed.connect(
            lambda pinned, current=key: None if pinned else self.unpin(current)
        )
        window.panel.filter_changed.connect(
            lambda current=key: self._filter_changed(current)
        )
        window.panel.line_count_changed.connect(
            lambda _value, current=key: self._line_count_changed(current)
        )
        self.reposition_all()
        self._request(session, reset_bottom=True)
        if not self.refresh_timer.isActive():
            self.refresh_timer.start()
        self.changed.emit()
        return window

    def unpin(self, key):
        normalized = (str(key[0]), str(key[1]))
        session = self.sessions.pop(normalized, None)
        if session is None:
            return False
        self.reader.remove(normalized)
        session.window.hide()
        session.window.deleteLater()
        if not self.sessions:
            self.refresh_timer.stop()
        else:
            self.reposition_all()
        self.changed.emit()
        return True

    def _save_preference(self, session):
        self.preference_store.save(
            session.target.app_id,
            LogPanelPreference(
                line_count=session.window.panel.line_count.value(),
                filter_expression=session.window.panel.filter_edit.text(),
                stream=session.stream,
            ),
        )

    def _filter_changed(self, key):
        session = self.sessions.get(key)
        if session is None:
            return
        session.window.panel.apply_cached_filter(reset_bottom=True)
        self._save_preference(session)

    def _line_count_changed(self, key):
        session = self.sessions.get(key)
        if session is None:
            return
        self._save_preference(session)
        self._request(session, reset_bottom=True)

    def _request(self, session, *, reset_bottom=False):
        stream_key = "out" if session.stream == "stdout" else "err"
        manager = session.target.manager
        request_id = self.reader.request(
            session.key,
            lambda current=manager, selected=stream_key: current.get_log_path(selected),
            max_lines=session.window.panel.line_count.value(),
            max_bytes=DEFAULT_MAX_BYTES,
        )
        session.latest_read_id = request_id
        session.request_reset_bottom = {request_id: bool(reset_bottom)}
        return request_id

    def refresh_all(self):
        for session in tuple(self.sessions.values()):
            self._request(session, reset_bottom=False)

    def _snapshot_ready(self, key, request_id, snapshot):
        session = self.sessions.get(tuple(key))
        if session is None or request_id != session.latest_read_id:
            return
        reset_bottom = session.request_reset_bottom.pop(request_id, False)
        session.window.panel.update_snapshot(snapshot, reset_bottom=reset_bottom)

    def reposition_all(self):
        if not self.sessions:
            return ()
        tray_rect, available = self.placement_provider()
        rects = compute_pinned_log_rects(
            len(self.sessions),
            QSize(max(1, tray_rect.width()), PANEL_PREFERRED_HEIGHT),
            tray_rect,
            available,
        )
        for session, rect in zip(self.sessions.values(), rects):
            session.window.panel.setPreferredHeight(rect.height())
            session.window.setGeometry(rect)
            session.window.panel.show()
            session.window.show()
            session.window.raise_()
        return rects

    def transient_anchor_rect(self, tray_rect):
        visible = [window.frameGeometry() for window in self.windows() if window.isVisible()]
        if not visible:
            return tray_rect
        top = min(rect.top() for rect in visible)
        return QRect(tray_rect.left(), top, tray_rect.width(), tray_rect.height())

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        self.refresh_timer.stop()
        try:
            self.reader.snapshot_ready.disconnect(self._snapshot_ready)
        except (TypeError, RuntimeError):
            pass
        self.reader.shutdown()
        for session in tuple(self.sessions.values()):
            session.window.hide()
            session.window.deleteLater()
        self.sessions.clear()


__all__ = [
    "MultiLogReader",
    "PinnedLogManager",
    "compute_pinned_log_rects",
]
