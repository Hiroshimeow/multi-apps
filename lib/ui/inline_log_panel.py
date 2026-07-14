from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProxyStyle,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .log_filter import (
    FILTER_WARNING_STANDALONE_BANG,
    LogFilterResultState,
    apply_log_filter,
    parse_filter_expression,
)
from .log_reader import (
    DEFAULT_MAX_BYTES,
    LogChangeKind,
    LogSnapshot,
    LogSnapshotState,
    classify_log_change,
)

PANEL_PREFERRED_HEIGHT = 220
PANEL_NORMAL_MIN_HEIGHT = 180
PANEL_MAX_HEIGHT = 260
PANEL_EMERGENCY_MIN_HEIGHT = 96
SCREEN_MARGIN = 12
PANEL_OUTER_MARGINS_HORIZONTAL = 6
PANEL_OUTER_MARGINS_VERTICAL = 5
PANEL_SPACING = 4
FILTER_SOFT_MIN_WIDTH = 160
REFRESH_INTERVAL_MS = 500
HIDE_DELAY_MS = 300
DEFAULT_REFRESH_INTERVAL_MS = REFRESH_INTERVAL_MS
DEFAULT_HIDE_DELAY_MS = HIDE_DELAY_MS

_FILTER_TOOLTIP = (
    "Comma-separated literal terms; !term excludes; outer brackets are optional; "
    "commas inside terms are unsupported."
)

_PALETTE = (
    ("#FFE082", "#111111"),
    ("#80DEEA", "#111111"),
    ("#A5D6A7", "#111111"),
    ("#F8BBD0", "#111111"),
    ("#FFCC80", "#111111"),
    ("#D1C4E9", "#111111"),
)


@dataclass(frozen=True, slots=True)
class InlineMenuGeometry:
    panel_height: int
    menu_rect: QRect
    width_cap: int
    height_cap: int
    native_menu_overflow: bool


def compute_inline_menu_geometry(
    *,
    base_menu_size: QSize,
    content_width: int,
    desired_panel_height: int,
    available_geometry: QRect,
    anchor_rect: QRect,
) -> InlineMenuGeometry:
    width_cap = max(1, available_geometry.width() - 2 * SCREEN_MARGIN)
    height_cap = max(1, available_geometry.height() - 2 * SCREEN_MARGIN)
    desired = min(PANEL_MAX_HEIGHT, max(PANEL_NORMAL_MIN_HEIGHT, desired_panel_height))
    normal_budget = height_cap - max(0, base_menu_size.height())
    if normal_budget >= PANEL_NORMAL_MIN_HEIGHT:
        panel_height = min(desired, normal_budget)
        overflow = False
    elif normal_budget >= PANEL_EMERGENCY_MIN_HEIGHT:
        panel_height = normal_budget
        overflow = False
    else:
        panel_height = PANEL_EMERGENCY_MIN_HEIGHT
        overflow = True

    menu_width = min(width_cap, max(base_menu_size.width(), int(content_width)))
    natural_height = max(1, base_menu_size.height() + panel_height)
    menu_height = min(height_cap, natural_height)
    overflow = overflow or natural_height > height_cap

    left = available_geometry.left() + SCREEN_MARGIN
    top = available_geometry.top() + SCREEN_MARGIN
    right = available_geometry.right() - SCREEN_MARGIN + 1
    bottom = available_geometry.bottom() - SCREEN_MARGIN + 1
    requested_x = anchor_rect.left()
    requested_y = anchor_rect.bottom() - menu_height + 1
    x = min(max(requested_x, left), max(left, right - menu_width))
    y = min(max(requested_y, top), max(top, bottom - menu_height))
    return InlineMenuGeometry(
        panel_height=int(panel_height),
        menu_rect=QRect(int(x), int(y), int(menu_width), int(menu_height)),
        width_cap=int(width_cap),
        height_cap=int(height_cap),
        native_menu_overflow=bool(overflow),
    )


class NativeScrollableMenuStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Menu_Scrollable:
            return 1
        return super().styleHint(hint, option, widget, returnData)


class LogRangeHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.ranges_by_block: tuple[tuple, ...] = ()
        self._formats = []
        for background, foreground in _PALETTE:
            text_format = QTextCharFormat()
            text_format.setBackground(QColor(background))
            text_format.setForeground(QColor(foreground))
            self._formats.append(text_format)

    def set_ranges(self, ranges_by_block):
        self.ranges_by_block = tuple(tuple(ranges) for ranges in ranges_by_block)
        self.rehighlight()

    def highlightBlock(self, _text):
        block_number = self.currentBlock().blockNumber()
        if block_number < 0 or block_number >= len(self.ranges_by_block):
            return
        for item in self.ranges_by_block[block_number]:
            self.setFormat(
                item.start,
                item.length,
                self._formats[item.term_index % len(self._formats)],
            )


@dataclass(frozen=True, slots=True)
class _CursorEndpoint:
    line_text: str
    occurrence: int
    block_index: int
    offset: int


@dataclass(frozen=True, slots=True)
class _CursorState:
    anchor: _CursorEndpoint
    position: _CursorEndpoint
    selected_text: str
    had_selection: bool
    view_had_focus: bool


@dataclass(frozen=True, slots=True)
class _ScrollAnchor:
    line_text: str
    occurrence: int
    block_index: int
    scroll_value: int
    pixel_offset: float


class InlineLogPanel(QFrame):
    pointer_entered = pyqtSignal()
    pointer_left = pyqtSignal()
    filter_changed = pyqtSignal()
    line_count_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("inlineLogPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(PANEL_EMERGENCY_MIN_HEIGHT)
        self.setMaximumHeight(PANEL_MAX_HEIGHT)
        self.setPreferredHeight(PANEL_PREFERRED_HEIGHT)

        self._snapshot: LogSnapshot | None = None
        self._display_lines: tuple[str, ...] = ()
        self._display_signature = None
        self._primary_state = "Log file missing"
        self._suppress_control_signals = False

        panel_layout = QVBoxLayout(self)
        panel_layout.setContentsMargins(
            PANEL_OUTER_MARGINS_HORIZONTAL,
            PANEL_OUTER_MARGINS_VERTICAL,
            PANEL_OUTER_MARGINS_HORIZONTAL,
            PANEL_OUTER_MARGINS_VERTICAL,
        )
        panel_layout.setSpacing(PANEL_SPACING)

        control_row = QWidget(self)
        self.control_row = control_row
        control_layout = QHBoxLayout(control_row)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(PANEL_SPACING)

        self.stream_label = QLabel("stdout", control_row)
        self.stream_label.setAccessibleName("Log stream")
        self.filter_label = QLabel("Filter", control_row)
        self.filter_edit = QLineEdit(control_row)
        self.filter_edit.setAccessibleName("Log filter")
        self.filter_edit.setToolTip(_FILTER_TOOLTIP)
        self.filter_edit.setMinimumWidth(FILTER_SOFT_MIN_WIDTH)
        self.filter_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.filter_label.setBuddy(self.filter_edit)
        self.lines_label = QLabel("Lines", control_row)
        self.line_count = QSpinBox(control_row)
        self.line_count.setAccessibleName("Log tail line count")
        self.line_count.setRange(10, 5000)
        self.line_count.setValue(100)
        self.line_count.setToolTip("Number of newest log lines to display (10-5000).")
        self.lines_label.setBuddy(self.line_count)
        self.state_label = QLabel("Log file missing", control_row)
        self.state_label.setAccessibleName("Live log state")
        self.state_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        control_layout.addWidget(self.stream_label)
        control_layout.addWidget(self.filter_label)
        control_layout.addWidget(self.filter_edit, 1)
        control_layout.addWidget(self.lines_label)
        control_layout.addWidget(self.line_count)
        control_layout.addWidget(self.state_label)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setAccessibleName("Live log text")
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.highlighter = LogRangeHighlighter(self.log_view.document())

        panel_layout.addWidget(control_row)
        panel_layout.addWidget(self.log_view, 1)

        self.filter_edit.textChanged.connect(self._on_filter_text_changed)
        self.line_count.valueChanged.connect(self._on_line_count_changed)
        self.log_view.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.hide()

    def setPreferredHeight(self, height):
        height = min(PANEL_MAX_HEIGHT, max(PANEL_EMERGENCY_MIN_HEIGHT, int(height)))
        self.setFixedHeight(height)

    def enterEvent(self, event):
        self.pointer_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.pointer_left.emit()
        super().leaveEvent(event)

    def set_stream(self, stream):
        if stream not in {"stdout", "stderr"}:
            raise ValueError("stream must be stdout or stderr")
        if self.stream_label.text() != stream:
            self.stream_label.setText(stream)
            self._snapshot = None
            self._display_signature = None
            self._display_lines = ()
            self.highlighter.set_ranges(())
            self.log_view.clear()

    def reset(self):
        self._snapshot = None
        self._display_signature = None
        self._display_lines = ()
        self.highlighter.set_ranges(())
        self.log_view.clear()
        self._set_primary_state("Log file missing", "")

    def _on_filter_text_changed(self, _text):
        if not self._suppress_control_signals:
            self.filter_changed.emit()

    def _on_line_count_changed(self, value):
        if not self._suppress_control_signals:
            self.line_count_changed.emit(int(value))

    def _at_bottom(self):
        bar = self.log_view.verticalScrollBar()
        return bar.value() >= bar.maximum() - 1

    def _on_scroll_changed(self, _value):
        if self._primary_state in {"Live", "Live paused while scrolled"}:
            primary = "Live" if self._at_bottom() else "Live paused while scrolled"
            self.state_label.setText(primary)
            self.state_label.setAccessibleDescription(self.state_label.toolTip())

    def _set_primary_state(self, primary, tooltip):
        self._primary_state = primary
        self.state_label.setText(primary)
        self.state_label.setToolTip(tooltip)
        self.state_label.setAccessibleDescription(tooltip)

    @staticmethod
    def _endpoint_for_position(document, lines, position):
        block = document.findBlock(position)
        if not block.isValid():
            block = document.lastBlock()
        block_index = max(0, block.blockNumber())
        line_text = lines[block_index] if block_index < len(lines) else block.text()
        occurrence = sum(1 for line in lines[: block_index + 1] if line == line_text) - 1
        offset = max(0, min(position - block.position(), len(line_text)))
        return _CursorEndpoint(line_text, max(0, occurrence), block_index, offset)

    def _capture_cursor_state(self):
        cursor = self.log_view.textCursor()
        lines = self._display_lines
        return _CursorState(
            anchor=self._endpoint_for_position(
                self.log_view.document(), lines, cursor.anchor()
            ),
            position=self._endpoint_for_position(
                self.log_view.document(), lines, cursor.position()
            ),
            selected_text=cursor.selectedText(),
            had_selection=cursor.hasSelection(),
            view_had_focus=self.log_view.hasFocus(),
        )

    def _capture_scroll_anchor(self):
        block = self.log_view.firstVisibleBlock()
        if not block.isValid():
            return None
        block_index = block.blockNumber()
        if block_index < 0 or block_index >= len(self._display_lines):
            return None
        line_text = self._display_lines[block_index]
        occurrence = (
            sum(
                1
                for line in self._display_lines[: block_index + 1]
                if line == line_text
            )
            - 1
        )
        pixel_offset = self.log_view.blockBoundingGeometry(block).translated(
            self.log_view.contentOffset()
        ).top()
        return _ScrollAnchor(
            line_text=line_text,
            occurrence=max(0, occurrence),
            block_index=block_index,
            scroll_value=self.log_view.verticalScrollBar().value(),
            pixel_offset=float(pixel_offset),
        )

    @staticmethod
    def _matching_line_index(line_text, occurrence, prior_index, new_lines):
        matches = [index for index, text in enumerate(new_lines) if text == line_text]
        if not matches:
            return None
        if occurrence < len(matches):
            return matches[occurrence]
        return min(matches, key=lambda index: abs(index - prior_index))

    def _restore_scroll_anchor(self, anchor, new_lines):
        new_index = self._matching_line_index(
            anchor.line_text,
            anchor.occurrence,
            anchor.block_index,
            new_lines,
        )
        bar = self.log_view.verticalScrollBar()
        if new_index is None:
            bar.setValue(min(anchor.scroll_value, bar.maximum()))
            return False
        shifted_value = anchor.scroll_value + (new_index - anchor.block_index)
        bar.setValue(max(bar.minimum(), min(shifted_value, bar.maximum())))
        return True

    @staticmethod
    def _map_endpoint(endpoint, new_lines, document):
        block_index = InlineLogPanel._matching_line_index(
            endpoint.line_text,
            endpoint.occurrence,
            endpoint.block_index,
            new_lines,
        )
        if block_index is None:
            return None
        block = document.findBlockByNumber(block_index)
        if not block.isValid():
            return None
        return block.position() + min(endpoint.offset, len(new_lines[block_index]))

    def _restore_cursor_state(self, state, new_lines):
        document = self.log_view.document()
        anchor = self._map_endpoint(state.anchor, new_lines, document)
        position = self._map_endpoint(state.position, new_lines, document)
        if anchor is None or position is None:
            return False
        cursor = QTextCursor(document)
        cursor.setPosition(anchor)
        cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        if state.had_selection and cursor.selectedText() != state.selected_text:
            return False
        self.log_view.setTextCursor(cursor)
        if state.view_had_focus:
            self.log_view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _state_tooltip(self, snapshot, warning):
        details = []
        if snapshot.state == LogSnapshotState.UNREADABLE and snapshot.error:
            details.append(snapshot.error)
        if snapshot.truncated:
            details.append("Showing bounded tail")
        if warning:
            details.append("Standalone ! ignored")
        return "\n".join(details)

    def update_snapshot(self, snapshot: LogSnapshot, *, reset_bottom=False, force=False):
        previous = self._snapshot
        change_kind = classify_log_change(previous, snapshot)
        spec = parse_filter_expression(self.filter_edit.text())
        warning = FILTER_WARNING_STANDALONE_BANG in spec.warnings

        filtered = None
        if snapshot.state == LogSnapshotState.READY:
            filtered = apply_log_filter(snapshot.lines, spec)
            display_lines = tuple(item.text for item in filtered.lines)
            ranges = tuple(item.highlights for item in filtered.lines)
            if filtered.state == LogFilterResultState.FULLY_FILTERED:
                primary = "All lines filtered"
            else:
                primary = "Live"
        elif snapshot.state == LogSnapshotState.MISSING:
            display_lines, ranges, primary = (), (), "Log file missing"
        elif snapshot.state == LogSnapshotState.EMPTY:
            display_lines, ranges, primary = (), (), "Log is empty"
        else:
            display_lines, ranges, primary = (), (), "Cannot read log"

        signature = (
            snapshot.state,
            display_lines,
            ranges,
            primary,
            self.filter_edit.text(),
            snapshot.error,
            snapshot.truncated,
        )
        tooltip = self._state_tooltip(snapshot, warning)
        self._snapshot = snapshot

        if not force and signature == self._display_signature:
            self._set_primary_state(
                primary if primary != "Live" or self._at_bottom() else "Live paused while scrolled",
                tooltip,
            )
            return False

        at_bottom = self._at_bottom()
        old_scroll_value = self.log_view.verticalScrollBar().value()
        cursor_state = None
        scroll_anchor = None
        compatible_append = change_kind == LogChangeKind.APPENDED
        if compatible_append:
            cursor_state = self._capture_cursor_state()
            if not at_bottom:
                scroll_anchor = self._capture_scroll_anchor()

        self.log_view.setPlainText("\n".join(display_lines))
        self._display_lines = display_lines
        self.highlighter.set_ranges(ranges)
        self._display_signature = signature

        if compatible_append and cursor_state is not None:
            self._restore_cursor_state(cursor_state, display_lines)

        bar = self.log_view.verticalScrollBar()
        structural = change_kind in {
            LogChangeKind.TRUNCATED,
            LogChangeKind.ROTATED,
            LogChangeKind.REAPPEARED,
            LogChangeKind.STATE_CHANGED,
            LogChangeKind.INITIAL,
        }
        if reset_bottom or structural or at_bottom:
            bar.setValue(bar.maximum())
        elif scroll_anchor is not None:
            self._restore_scroll_anchor(scroll_anchor, display_lines)
        else:
            bar.setValue(min(old_scroll_value, bar.maximum()))

        if primary == "Live":
            primary = "Live" if self._at_bottom() else "Live paused while scrolled"
        self._set_primary_state(primary, tooltip)
        return True

    def apply_cached_filter(self, *, reset_bottom=True):
        if self._snapshot is None:
            return False
        return self.update_snapshot(
            self._snapshot,
            reset_bottom=reset_bottom,
            force=True,
        )


class InlineLogPanelCoordinator(QObject):
    panel_visibility_changed = pyqtSignal(bool)

    def __init__(
        self,
        parent=None,
        *,
        refresh_interval_ms=DEFAULT_REFRESH_INTERVAL_MS,
        hide_delay_ms=DEFAULT_HIDE_DELAY_MS,
        generation=0,
    ):
        super().__init__(parent)
        self.current_row = None
        self.current_stream = None
        self.generation = int(generation)
        self._active_generation = int(generation)
        self._button_hovered = False
        self._panel_hovered = False
        self._registered_rows = set()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(int(refresh_interval_ms))
        self.refresh_timer.timeout.connect(self.refresh_current)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(int(hide_delay_ms))
        self.hide_timer.timeout.connect(self._hide_if_unkept)
        QApplication.instance().focusChanged.connect(self._focus_changed)

    def register_row(self, row):
        key = id(row)
        if key in self._registered_rows:
            return
        self._registered_rows.add(key)
        panel = row.inline_log_panel
        panel.pointer_entered.connect(lambda current=row: self.panel_entered(current))
        panel.pointer_left.connect(lambda current=row: self.panel_left(current))
        panel.filter_changed.connect(lambda current=row: self._filter_changed(current))
        panel.line_count_changed.connect(
            lambda _value, current=row: self._line_count_changed(current)
        )

    def request_open(self, row, stream):
        if stream not in {"stdout", "stderr"}:
            raise ValueError("stream must be stdout or stderr")
        self.register_row(row)
        self.hide_timer.stop()
        same_row = row is self.current_row
        same_stream = same_row and stream == self.current_stream
        if self.current_row is not None and not same_row:
            self.current_row.inline_log_panel.hide()
            self.current_row.inline_log_panel.reset()
        self.current_row = row
        self.current_stream = stream
        self._button_hovered = True
        if not same_stream:
            row.inline_log_panel.set_stream(stream)
        row.inline_log_panel.show()
        self.panel_visibility_changed.emit(True)
        self.refresh_current(reset_bottom=not same_stream)
        self.refresh_timer.start()

    def button_entered(self, row, stream):
        self._button_hovered = True
        self.request_open(row, stream)

    def button_left(self, row, stream):
        if row is self.current_row and stream == self.current_stream:
            self._button_hovered = False
            self._schedule_hide()

    def panel_entered(self, row):
        if row is self.current_row:
            self._panel_hovered = True
            self.hide_timer.stop()

    def panel_left(self, row):
        if row is self.current_row:
            self._panel_hovered = False
            self._schedule_hide()

    def _focus_changed(self, _old, now):
        if self.current_row is None:
            return
        panel = self.current_row.inline_log_panel
        if now is panel or (now is not None and panel.isAncestorOf(now)):
            self.hide_timer.stop()
        elif not self._button_hovered and not self._panel_hovered:
            self._schedule_hide()

    def _focus_within_panel(self):
        if self.current_row is None:
            return False
        focus = QApplication.focusWidget()
        panel = self.current_row.inline_log_panel
        return focus is panel or (focus is not None and panel.isAncestorOf(focus))

    def _schedule_hide(self):
        if self.current_row is not None and not self._keep_open():
            self.hide_timer.start()

    def _keep_open(self):
        return self._button_hovered or self._panel_hovered or self._focus_within_panel()

    def _hide_if_unkept(self):
        if not self._keep_open():
            self.hide_current()

    def _filter_changed(self, row):
        if row is self.current_row:
            row.inline_log_panel.apply_cached_filter(reset_bottom=True)

    def _line_count_changed(self, row):
        if row is self.current_row:
            self.refresh_current(reset_bottom=True)

    def refresh_current(self, *, reset_bottom=False):
        if self.current_row is None:
            return None
        row = self.current_row
        stream_key = "out" if self.current_stream == "stdout" else "err"
        snapshot = row.manager.get_log_snapshot(
            stream_key,
            max_lines=row.inline_log_panel.line_count.value(),
            max_bytes=DEFAULT_MAX_BYTES,
        )
        row.inline_log_panel.update_snapshot(snapshot, reset_bottom=reset_bottom)
        return snapshot

    def hide_current(self):
        self.refresh_timer.stop()
        self.hide_timer.stop()
        if self.current_row is not None:
            self.current_row.inline_log_panel.hide()
            self.panel_visibility_changed.emit(False)
        self.current_row = None
        self.current_stream = None
        self._button_hovered = False
        self._panel_hovered = False

    def close_for_menu_hide(self):
        self.hide_current()

    def shutdown(self):
        self._active_generation += 1
        self.hide_current()
        try:
            QApplication.instance().focusChanged.disconnect(self._focus_changed)
        except (TypeError, RuntimeError):
            pass


__all__ = [
    "DEFAULT_HIDE_DELAY_MS",
    "DEFAULT_REFRESH_INTERVAL_MS",
    "FILTER_SOFT_MIN_WIDTH",
    "HIDE_DELAY_MS",
    "InlineLogPanel",
    "InlineLogPanelCoordinator",
    "InlineMenuGeometry",
    "LogRangeHighlighter",
    "NativeScrollableMenuStyle",
    "PANEL_EMERGENCY_MIN_HEIGHT",
    "PANEL_MAX_HEIGHT",
    "PANEL_NORMAL_MIN_HEIGHT",
    "PANEL_PREFERRED_HEIGHT",
    "REFRESH_INTERVAL_MS",
    "SCREEN_MARGIN",
    "compute_inline_menu_geometry",
]
