from __future__ import annotations

from dataclasses import dataclass
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
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
PANEL_MAX_HEIGHT = 260
PANEL_EMERGENCY_MIN_HEIGHT = 96
PANEL_OUTER_MARGINS_HORIZONTAL = 6
PANEL_OUTER_MARGINS_VERTICAL = 5
PANEL_SPACING = 4
FILTER_SOFT_MIN_WIDTH = 160

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


@dataclass(frozen=True, slots=True)
class _AppendLineMapping:
    dropped_prefix: int
    overlap_length: int

    def map_old_index(self, old_index):
        if old_index < self.dropped_prefix:
            return None
        new_index = old_index - self.dropped_prefix
        if new_index >= self.overlap_length:
            return None
        return new_index


class InlineLogPanel(QFrame):
    pointer_entered = pyqtSignal()
    pointer_left = pyqtSignal()
    filter_changed = pyqtSignal()
    line_count_changed = pyqtSignal(int)
    pin_changed = pyqtSignal(bool)

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
        self._suppress_pin_signal = False

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

        self.source_label = QLabel("", control_row)
        self.source_label.setAccessibleName("Log source")
        self.source_label.hide()
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
        self.pin_button = QPushButton("PIN", control_row)
        self.pin_button.setAccessibleName("Pin live log")
        self.pin_button.setCheckable(True)
        self.pin_button.setToolTip("Keep this live log visible when the tray panel closes.")
        self.state_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )

        control_layout.addWidget(self.source_label)
        control_layout.addWidget(self.stream_label)
        control_layout.addWidget(self.filter_label)
        control_layout.addWidget(self.filter_edit, 1)
        control_layout.addWidget(self.lines_label)
        control_layout.addWidget(self.line_count)
        control_layout.addWidget(self.state_label)
        control_layout.addWidget(self.pin_button)

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
        self.pin_button.toggled.connect(self._on_pin_toggled)
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

    @staticmethod
    def _validate_stream(stream):
        if stream not in {"stdout", "stderr"}:
            raise ValueError("stream must be stdout or stderr")

    def set_source_name(self, name):
        value = str(name or "").strip()
        self.source_label.setText(value)
        self.source_label.setVisible(bool(value))

    def set_pin_state(self, pinned):
        self._suppress_pin_signal = True
        try:
            self.pin_button.setChecked(bool(pinned))
            self.pin_button.setText("UNPIN" if pinned else "PIN")
        finally:
            self._suppress_pin_signal = False

    def _on_pin_toggled(self, pinned):
        self.pin_button.setText("UNPIN" if pinned else "PIN")
        if not self._suppress_pin_signal:
            self.pin_changed.emit(bool(pinned))

    def set_stream(self, stream):
        self._validate_stream(stream)
        if self.stream_label.text() != stream:
            self.stream_label.setText(stream)
            self._snapshot = None
            self._display_signature = None
            self._display_lines = ()
            self.highlighter.set_ranges(())
            self.log_view.clear()

    def prepare_stream(self, stream):
        """Switch target metadata without repainting the document twice."""
        self._validate_stream(stream)
        self.stream_label.setText(stream)
        self._snapshot = None
        self._display_signature = None
        self._set_primary_state("Loading?", "Reading newest log snapshot")

    def configure_controls(self, *, line_count, filter_expression, stream):
        """Apply persisted controls as one silent renderer operation."""
        self._suppress_control_signals = True
        try:
            self.line_count.setValue(int(line_count))
            self.filter_edit.setText(str(filter_expression))
        finally:
            self._suppress_control_signals = False
        self.prepare_stream(stream)

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
    def _append_line_mapping(old_lines, new_lines):
        if not old_lines or not new_lines:
            return _AppendLineMapping(len(old_lines), 0)

        prefix = [0] * len(new_lines)
        matched = 0
        for index in range(1, len(new_lines)):
            while matched and new_lines[index] != new_lines[matched]:
                matched = prefix[matched - 1]
            if new_lines[index] == new_lines[matched]:
                matched += 1
            prefix[index] = matched

        matched = 0
        last_old_index = len(old_lines) - 1
        for old_index, line in enumerate(old_lines):
            while matched and line != new_lines[matched]:
                matched = prefix[matched - 1]
            if line == new_lines[matched]:
                matched += 1
            if matched == len(new_lines) and old_index != last_old_index:
                matched = prefix[matched - 1]

        return _AppendLineMapping(len(old_lines) - matched, matched)

    @staticmethod
    def _matching_line_index(line_text, prior_index, new_lines, mapping):
        new_index = mapping.map_old_index(prior_index)
        if new_index is None or new_index >= len(new_lines):
            return None
        if new_lines[new_index] != line_text:
            return None
        return new_index

    def _restore_scroll_anchor(self, anchor, new_lines, mapping):
        new_index = self._matching_line_index(
            anchor.line_text,
            anchor.block_index,
            new_lines,
            mapping,
        )
        bar = self.log_view.verticalScrollBar()
        if new_index is None:
            bar.setValue(min(anchor.scroll_value, bar.maximum()))
            return False
        shifted_value = anchor.scroll_value + (new_index - anchor.block_index)
        bar.setValue(max(bar.minimum(), min(shifted_value, bar.maximum())))
        return True

    @staticmethod
    def _map_endpoint(endpoint, new_lines, document, mapping):
        block_index = InlineLogPanel._matching_line_index(
            endpoint.line_text,
            endpoint.block_index,
            new_lines,
            mapping,
        )
        if block_index is None:
            return None
        block = document.findBlockByNumber(block_index)
        if not block.isValid():
            return None
        return block.position() + min(endpoint.offset, len(new_lines[block_index]))

    def _restore_cursor_state(self, state, new_lines, mapping):
        document = self.log_view.document()
        anchor = self._map_endpoint(state.anchor, new_lines, document, mapping)
        position = self._map_endpoint(state.position, new_lines, document, mapping)
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
        old_display_lines = self._display_lines
        cursor_state = None
        scroll_anchor = None
        append_mapping = None
        compatible_append = change_kind == LogChangeKind.APPENDED
        if compatible_append:
            cursor_state = self._capture_cursor_state()
            append_mapping = self._append_line_mapping(old_display_lines, display_lines)
            if not at_bottom:
                scroll_anchor = self._capture_scroll_anchor()

        self.log_view.setPlainText("\n".join(display_lines))
        self._display_lines = display_lines
        self.highlighter.set_ranges(ranges)
        self._display_signature = signature

        if compatible_append and cursor_state is not None and append_mapping is not None:
            self._restore_cursor_state(cursor_state, display_lines, append_mapping)

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
        elif scroll_anchor is not None and append_mapping is not None:
            self._restore_scroll_anchor(scroll_anchor, display_lines, append_mapping)
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


__all__ = [
    "FILTER_SOFT_MIN_WIDTH",
    "InlineLogPanel",
    "LogRangeHighlighter",
    "PANEL_EMERGENCY_MIN_HEIGHT",
    "PANEL_MAX_HEIGHT",
    "PANEL_PREFERRED_HEIGHT",
]
