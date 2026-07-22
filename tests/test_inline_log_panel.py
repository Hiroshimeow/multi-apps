import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import MagicMock

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame, QMenu, QStyle

from lib.ui.inline_log_panel import (
    DEFAULT_HIDE_DELAY_MS,
    DEFAULT_REFRESH_INTERVAL_MS,
    HIDE_DELAY_MS,
    InlineLogPanel,
    InlineLogPanelCoordinator,
    LogRangeHighlighter,
    NativeScrollableMenuStyle,
    PANEL_EMERGENCY_MIN_HEIGHT,
    PANEL_PREFERRED_HEIGHT,
    REFRESH_INTERVAL_MS,
    compute_inline_menu_geometry,
)
from lib.ui.log_reader import DEFAULT_MAX_BYTES, LogSnapshot, LogSnapshotState


_QT_APP = QApplication.instance() or QApplication([])


def ready_snapshot(*lines, size=None, identity=(1, 1), truncated=False):
    return LogSnapshot(
        path="C:/logs/demo.log",
        state=LogSnapshotState.READY,
        lines=tuple(lines),
        size_bytes=size if size is not None else sum(len(line) + 1 for line in lines),
        file_identity=identity,
        truncated=truncated,
    )


class InlineLogPanelWidgetTests(unittest.TestCase):
    def setUp(self):
        self.host = QFrame()
        self.host.resize(800, 260)
        self.panel = InlineLogPanel(self.host)
        self.panel.resize(760, 220)
        self.host.show()
        QApplication.processEvents()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        QApplication.processEvents()

    def test_hierarchy_defaults_and_plain_text_contract(self):
        self.assertIsInstance(self.panel, QFrame)
        self.assertFalse(self.panel.isWindow())
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(self.panel.stream_label.text(), "stdout")
        self.assertEqual(self.panel.line_count.value(), 100)
        self.assertEqual(self.panel.line_count.minimum(), 10)
        self.assertEqual(self.panel.line_count.maximum(), 5000)
        self.assertTrue(self.panel.log_view.isReadOnly())
        self.assertEqual(
            self.panel.log_view.lineWrapMode(),
            self.panel.log_view.LineWrapMode.NoWrap,
        )
        self.assertEqual(self.panel.filter_label.buddy(), self.panel.filter_edit)
        self.assertEqual(self.panel.lines_label.buddy(), self.panel.line_count)
        self.assertIn("!term excludes", self.panel.filter_edit.toolTip())
        self.assertEqual(REFRESH_INTERVAL_MS, 500)
        self.assertEqual(HIDE_DELAY_MS, 300)
        self.assertEqual(DEFAULT_REFRESH_INTERVAL_MS, 500)
        self.assertEqual(DEFAULT_HIDE_DELAY_MS, 300)

    def test_ready_filter_highlights_and_copy_plain_text(self):
        self.panel.show()
        self.panel.set_stream("stdout")
        self.panel.filter_edit.setText("[alpha,beta,!drop-me]")
        self.panel.update_snapshot(
            ready_snapshot("alpha one", "drop-me alpha", "beta two")
        )
        QApplication.processEvents()

        self.assertEqual(self.panel.log_view.toPlainText(), "alpha one\nbeta two")
        self.assertEqual(self.panel.state_label.text(), "Live")
        self.assertIsInstance(self.panel.highlighter, LogRangeHighlighter)
        self.assertEqual(len(self.panel.highlighter.ranges_by_block), 2)
        cursor = self.panel.log_view.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.panel.log_view.setTextCursor(cursor)
        self.assertEqual(cursor.selectedText(), "alpha one\u2029beta two")
        self.assertNotIn("<", self.panel.log_view.toPlainText())

    def test_exact_empty_missing_unreadable_and_fully_filtered_states(self):
        cases = (
            (
                LogSnapshot("x", LogSnapshotState.MISSING),
                "Log file missing",
            ),
            (
                LogSnapshot("x", LogSnapshotState.EMPTY, file_identity=(1, 1)),
                "Log is empty",
            ),
            (
                LogSnapshot(
                    "x",
                    LogSnapshotState.UNREADABLE,
                    size_bytes=4,
                    file_identity=(1, 1),
                    error="OSError: late io",
                ),
                "Cannot read log",
            ),
        )
        for snapshot, expected in cases:
            with self.subTest(expected=expected):
                self.panel.update_snapshot(snapshot)
                self.assertEqual(self.panel.state_label.text(), expected)
                self.assertEqual(self.panel.log_view.toPlainText(), "")
        self.panel.filter_edit.setText("!alpha")
        self.panel.update_snapshot(ready_snapshot("alpha"))
        self.assertEqual(self.panel.state_label.text(), "All lines filtered")
        self.assertEqual(self.panel.log_view.toPlainText(), "")

    def test_standalone_bang_warning_is_nonfatal(self):
        self.panel.filter_edit.setText("!")
        self.panel.update_snapshot(ready_snapshot("line"))
        self.assertEqual(self.panel.log_view.toPlainText(), "line")
        self.assertEqual(self.panel.state_label.text(), "Live")
        self.assertIn("Standalone ! ignored", self.panel.state_label.toolTip())

    def test_unchanged_snapshot_does_not_replace_document_or_selection(self):
        snapshot = ready_snapshot("alpha", "beta")
        self.panel.update_snapshot(snapshot)
        document = self.panel.log_view.document()
        cursor = self.panel.log_view.textCursor()
        cursor.setPosition(1)
        cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
        self.panel.log_view.setTextCursor(cursor)

        changed = self.panel.update_snapshot(snapshot)

        self.assertFalse(changed)
        self.assertIs(self.panel.log_view.document(), document)
        current = self.panel.log_view.textCursor()
        self.assertEqual((current.anchor(), current.position()), (1, 4))
        self.assertEqual(current.selectedText(), "lph")

    def test_appended_refresh_preserves_selection_caret_direction_and_focus(self):
        first = ready_snapshot("zero", "alpha target", "tail", size=24)
        second = ready_snapshot("zero", "alpha target", "tail", "new", size=28)
        self.panel.show()
        self.panel.update_snapshot(first)
        self.panel.log_view.setFocus()
        text = self.panel.log_view.toPlainText()
        start = text.index("alpha")
        cursor = self.panel.log_view.textCursor()
        cursor.setPosition(start + len("alpha"))
        cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
        self.panel.log_view.setTextCursor(cursor)
        before = self.panel.log_view.textCursor()
        self.assertGreater(before.anchor(), before.position())

        self.panel.update_snapshot(second)
        QApplication.processEvents()

        after = self.panel.log_view.textCursor()
        self.assertEqual(after.selectedText(), "alpha")
        self.assertGreater(after.anchor(), after.position())
        self.assertTrue(self.panel.log_view.hasFocus())

    def test_duplicate_selection_survives_bounded_tail_prefix_eviction(self):
        clipboard = QApplication.clipboard()
        previous_clipboard = clipboard.text()
        try:
            old_lines = ("same", "X", "same", "Y", "same", "Z")
            new_lines = ("X", "same", "Y", "same", "Z", "N")
            self.panel.show()
            self.panel.update_snapshot(ready_snapshot(*old_lines, size=100))
            self.panel.log_view.setFocus()

            old_text = self.panel.log_view.toPlainText()
            start = old_text.index("same", old_text.index("same") + 1)
            end = old_text.index("Y") + 1
            cursor = self.panel.log_view.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            self.panel.log_view.setTextCursor(cursor)
            before = self.panel.log_view.textCursor()
            before_direction = before.anchor() < before.position()
            self.panel.log_view.copy()
            self.assertEqual(clipboard.text(), "same\nY")

            self.panel.update_snapshot(ready_snapshot(*new_lines, size=110))
            QApplication.processEvents()
            self.panel.log_view.copy()

            after = self.panel.log_view.textCursor()
            new_text = self.panel.log_view.toPlainText()
            expected_start = new_text.index("same")
            expected_end = new_text.index("Y") + 1
            self.assertEqual(
                (after.anchor(), after.position()),
                (expected_start, expected_end),
            )
            self.assertEqual(after.selectedText(), "same\u2029Y")
            self.assertEqual(after.anchor() < after.position(), before_direction)
            self.assertEqual(clipboard.text(), "same\nY")
            self.assertTrue(self.panel.log_view.hasFocus())
        finally:
            clipboard.setText(previous_clipboard)

    def test_scroll_status_pauses_and_resumes(self):
        lines = tuple(f"line-{index:03d}" for index in range(200))
        self.panel.resize(500, 180)
        self.panel.show()
        self.panel.update_snapshot(ready_snapshot(*lines))
        QApplication.processEvents()
        bar = self.panel.log_view.verticalScrollBar()
        bar.setValue(max(0, bar.maximum() // 2))
        QApplication.processEvents()
        self.assertEqual(self.panel.state_label.text(), "Live paused while scrolled")
        bar.setValue(bar.maximum())
        QApplication.processEvents()
        self.assertEqual(self.panel.state_label.text(), "Live")

    def test_bottom_follow_stays_at_bottom_during_append(self):
        lines = tuple(f"line-{index:03d}" for index in range(120))
        self.panel.resize(500, 180)
        self.panel.show()
        self.panel.update_snapshot(ready_snapshot(*lines, size=1800))
        QApplication.processEvents()
        bar = self.panel.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

        self.panel.update_snapshot(
            ready_snapshot(*lines, "appended", size=1810)
        )
        QApplication.processEvents()

        self.assertEqual(bar.value(), bar.maximum())
        self.assertEqual(self.panel.state_label.text(), "Live")

    def test_copy_result_stays_stable_across_append(self):
        clipboard = QApplication.clipboard()
        previous = clipboard.text()
        try:
            self.panel.show()
            self.panel.update_snapshot(
                ready_snapshot("zero", "copy target", "tail", size=24)
            )
            text = self.panel.log_view.toPlainText()
            start = text.index("copy target")
            cursor = self.panel.log_view.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(start + len("copy target"), QTextCursor.MoveMode.KeepAnchor)
            self.panel.log_view.setTextCursor(cursor)
            self.panel.log_view.setFocus()
            self.panel.log_view.copy()
            self.assertEqual(clipboard.text(), "copy target")

            self.panel.update_snapshot(
                ready_snapshot("zero", "copy target", "tail", "new", size=28)
            )
            self.panel.log_view.copy()

            self.assertEqual(clipboard.text(), "copy target")
            self.assertEqual(
                self.panel.log_view.textCursor().selectedText(),
                "copy target",
            )
            self.assertTrue(self.panel.log_view.hasFocus())
        finally:
            clipboard.setText(previous)

    def test_paused_append_preserves_first_visible_line_when_tail_shifts(self):
        first_lines = tuple(f"line-{index:03d}" for index in range(120))
        second_lines = first_lines[5:] + tuple(f"new-{index:03d}" for index in range(5))
        self.panel.resize(500, 180)
        self.panel.show()
        self.panel.update_snapshot(ready_snapshot(*first_lines, size=2000))
        QApplication.processEvents()
        bar = self.panel.log_view.verticalScrollBar()
        bar.setValue(40)
        QApplication.processEvents()
        before = self.panel.log_view.firstVisibleBlock().text()

        self.panel.update_snapshot(ready_snapshot(*second_lines, size=2100))
        QApplication.processEvents()

        self.assertEqual(self.panel.log_view.firstVisibleBlock().text(), before)
        self.assertEqual(self.panel.state_label.text(), "Live paused while scrolled")

    def test_focused_caret_without_selection_survives_append(self):
        self.panel.show()
        self.panel.update_snapshot(ready_snapshot("zero", "target", "tail", size=20))
        text = self.panel.log_view.toPlainText()
        position = text.index("target") + 3
        cursor = self.panel.log_view.textCursor()
        cursor.setPosition(position)
        self.panel.log_view.setTextCursor(cursor)
        self.panel.log_view.setFocus()

        self.panel.update_snapshot(
            ready_snapshot("zero", "target", "tail", "new", size=24)
        )
        QApplication.processEvents()

        current = self.panel.log_view.textCursor()
        self.assertFalse(current.hasSelection())
        self.assertEqual(current.position(), position)
        self.assertTrue(self.panel.log_view.hasFocus())

    def test_repeated_line_caret_survives_bounded_tail_prefix_eviction(self):
        old_lines = ("same", "X", "same", "Y", "same", "Z")
        new_lines = ("X", "same", "Y", "same", "Z", "N")
        self.panel.show()
        self.panel.update_snapshot(ready_snapshot(*old_lines, size=100))
        old_text = self.panel.log_view.toPlainText()
        repeated_start = old_text.index("same", old_text.index("same") + 1)
        cursor = self.panel.log_view.textCursor()
        cursor.setPosition(repeated_start + 2)
        self.panel.log_view.setTextCursor(cursor)
        self.panel.log_view.setFocus()

        self.panel.update_snapshot(ready_snapshot(*new_lines, size=110))
        QApplication.processEvents()

        current = self.panel.log_view.textCursor()
        expected = self.panel.log_view.toPlainText().index("same") + 2
        self.assertFalse(current.hasSelection())
        self.assertEqual((current.anchor(), current.position()), (expected, expected))
        self.assertTrue(self.panel.log_view.hasFocus())

    def test_structural_transition_may_reset_selection(self):
        self.panel.update_snapshot(ready_snapshot("alpha", "beta", size=12))
        cursor = self.panel.log_view.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
        self.panel.log_view.setTextCursor(cursor)

        rotated = ready_snapshot("replacement", size=11, identity=(2, 2))
        self.panel.update_snapshot(rotated)

        self.assertEqual(self.panel.log_view.toPlainText(), "replacement")
        self.assertFalse(self.panel.log_view.textCursor().hasSelection())
        self.assertEqual(
            self.panel.log_view.verticalScrollBar().value(),
            self.panel.log_view.verticalScrollBar().maximum(),
        )

    def test_filter_and_spin_focus_survive_append_refresh(self):
        self.panel.show()
        self.panel.update_snapshot(ready_snapshot("alpha", size=6))
        for control in (self.panel.filter_edit, self.panel.line_count):
            with self.subTest(control=control.objectName() or type(control).__name__):
                control.setFocus()
                QApplication.processEvents()
                self.panel.update_snapshot(ready_snapshot("alpha", "beta", size=11))
                QApplication.processEvents()
                self.assertTrue(control.hasFocus())


class InlineLogPanelCoordinatorTests(unittest.TestCase):
    def make_row(self, name, snapshots):
        manager = MagicMock()
        manager.name = name
        manager.get_log_snapshot.side_effect = list(snapshots)
        row = MagicMock()
        row.manager = manager
        row.persist_log_preferences = MagicMock()
        row.inline_log_panel = InlineLogPanel()
        row.inline_log_panel.setParent(None)
        return row

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, InlineLogPanel):
                widget.close()
                widget.deleteLater()
        QApplication.processEvents()

    def test_open_switch_one_panel_and_one_refresh_timer(self):
        first = self.make_row("one", [ready_snapshot("out"), ready_snapshot("err")])
        second = self.make_row("two", [ready_snapshot("two")])
        coordinator = InlineLogPanelCoordinator(refresh_interval_ms=20, hide_delay_ms=15)

        coordinator.request_open(first, "stdout")
        self.assertIs(coordinator.current_row, first)
        self.assertTrue(first.inline_log_panel.isVisible())
        self.assertTrue(coordinator.refresh_timer.isActive())
        self.assertEqual(first.inline_log_panel.stream_label.text(), "stdout")

        coordinator.request_open(first, "stderr")
        self.assertEqual(first.inline_log_panel.stream_label.text(), "stderr")
        self.assertEqual(first.manager.get_log_snapshot.call_count, 2)

        coordinator.request_open(second, "stdout")
        self.assertFalse(first.inline_log_panel.isVisible())
        self.assertTrue(second.inline_log_panel.isVisible())
        self.assertIs(coordinator.current_row, second)
        self.assertTrue(coordinator.refresh_timer.isActive())
        coordinator.shutdown()
        self.assertFalse(coordinator.refresh_timer.isActive())
        self.assertFalse(coordinator.hide_timer.isActive())
        self.assertFalse(second.inline_log_panel.isVisible())

    def test_same_stream_reentry_does_not_reemit_visibility_or_reread_log(self):
        row = self.make_row(
            "one",
            [ready_snapshot("first"), ready_snapshot("unexpected second read")],
        )
        coordinator = InlineLogPanelCoordinator(refresh_interval_ms=1000, hide_delay_ms=30)
        visibility_changed = MagicMock()
        coordinator.panel_visibility_changed.connect(visibility_changed)

        coordinator.request_open(row, "stdout")
        visibility_changed.reset_mock()
        initial_reads = row.manager.get_log_snapshot.call_count

        coordinator.request_open(row, "stdout")

        visibility_changed.assert_not_called()
        self.assertEqual(row.manager.get_log_snapshot.call_count, initial_reads)
        self.assertIs(coordinator.current_row, row)
        self.assertEqual(coordinator.current_stream, "stdout")
        self.assertTrue(coordinator.refresh_timer.isActive())
        coordinator.shutdown()

    def test_switching_rows_and_streams_does_not_reemit_visibility(self):
        first = self.make_row(
            "one",
            [ready_snapshot("one-out"), ready_snapshot("one-err")],
        )
        second = self.make_row(
            "two",
            [ready_snapshot("two-out"), ready_snapshot("two-err")],
        )
        coordinator = InlineLogPanelCoordinator(
            refresh_interval_ms=1000,
            hide_delay_ms=30,
        )
        visibility_changed = MagicMock()
        coordinator.panel_visibility_changed.connect(visibility_changed)

        coordinator.request_open(first, "stdout")
        coordinator.request_open(second, "stdout")
        coordinator.request_open(second, "stderr")
        coordinator.request_open(first, "stderr")

        self.assertEqual(
            [call.args for call in visibility_changed.call_args_list],
            [(True,)],
        )
        self.assertIs(coordinator.current_row, first)
        self.assertEqual(coordinator.current_stream, "stderr")
        coordinator.shutdown()

    def test_hover_debounces_to_latest_log_target(self):
        first = self.make_row("one", [ready_snapshot("first")])
        second = self.make_row("two", [ready_snapshot("second")])
        coordinator = InlineLogPanelCoordinator(
            refresh_interval_ms=1000,
            hide_delay_ms=30,
            open_delay_ms=20,
        )

        coordinator.button_entered(first, "stdout")
        self.assertIsNone(coordinator.current_row)
        first.manager.get_log_snapshot.assert_not_called()
        coordinator.button_left(first, "stdout")
        coordinator.button_entered(second, "stderr")

        QTest.qWait(35)
        QApplication.processEvents()

        first.manager.get_log_snapshot.assert_not_called()
        second.manager.get_log_snapshot.assert_called_once_with(
            "err",
            max_lines=100,
            max_bytes=DEFAULT_MAX_BYTES,
        )
        self.assertIs(coordinator.current_row, second)
        self.assertEqual(coordinator.current_stream, "stderr")
        coordinator.shutdown()

    def test_hover_leave_before_open_delay_cancels_pending_open(self):
        row = self.make_row("one", [ready_snapshot("unexpected")])
        coordinator = InlineLogPanelCoordinator(
            refresh_interval_ms=1000,
            hide_delay_ms=30,
            open_delay_ms=20,
        )

        coordinator.button_entered(row, "stdout")
        coordinator.button_left(row, "stdout")
        QTest.qWait(35)
        QApplication.processEvents()

        self.assertIsNone(coordinator.current_row)
        row.manager.get_log_snapshot.assert_not_called()
        coordinator.shutdown()

    def test_delayed_hide_cancel_and_focus_keep_open(self):
        row = self.make_row("one", [ready_snapshot("one")])
        coordinator = InlineLogPanelCoordinator(refresh_interval_ms=1000, hide_delay_ms=30)
        coordinator.request_open(row, "stdout")
        coordinator.button_left(row, "stdout")
        self.assertTrue(coordinator.hide_timer.isActive())
        coordinator.panel_entered(row)
        self.assertFalse(coordinator.hide_timer.isActive())
        coordinator.panel_left(row)
        row.inline_log_panel.filter_edit.setFocus()
        QTest.qWait(45)
        QApplication.processEvents()
        self.assertTrue(row.inline_log_panel.isVisible())
        row.inline_log_panel.clearFocus()
        row.inline_log_panel.filter_edit.clearFocus()
        coordinator.panel_left(row)
        QTest.qWait(45)
        QApplication.processEvents()
        self.assertFalse(row.inline_log_panel.isVisible())
        coordinator.shutdown()
        coordinator.deleteLater()

    def test_filter_change_uses_cached_snapshot_without_disk_read(self):
        row = self.make_row("one", [ready_snapshot("alpha", "drop-me")])
        coordinator = InlineLogPanelCoordinator(refresh_interval_ms=1000, hide_delay_ms=30)
        coordinator.request_open(row, "stdout")
        self.assertEqual(row.manager.get_log_snapshot.call_count, 1)
        row.inline_log_panel.filter_edit.setText("!drop-me")
        QApplication.processEvents()
        self.assertEqual(row.manager.get_log_snapshot.call_count, 1)
        self.assertEqual(row.inline_log_panel.log_view.toPlainText(), "alpha")
        coordinator.shutdown()

    def test_line_count_change_requests_exact_limit(self):
        row = self.make_row("one", [ready_snapshot("one"), ready_snapshot("two")])
        coordinator = InlineLogPanelCoordinator(refresh_interval_ms=1000, hide_delay_ms=30)
        coordinator.request_open(row, "stdout")
        row.inline_log_panel.line_count.setValue(5000)
        QApplication.processEvents()
        self.assertEqual(row.manager.get_log_snapshot.call_args.kwargs["max_lines"], 5000)
        coordinator.shutdown()

    def test_filter_line_count_and_stream_changes_persist_but_same_stream_reentry_does_not(self):
        row = self.make_row(
            "one",
            [
                ready_snapshot("out"),
                ready_snapshot("filtered"),
                ready_snapshot("stderr"),
            ],
        )
        coordinator = InlineLogPanelCoordinator(refresh_interval_ms=1000, hide_delay_ms=30)
        coordinator.request_open(row, "stdout")
        row.persist_log_preferences.reset_mock()

        row.inline_log_panel.filter_edit.setText("alpha")
        QApplication.processEvents()
        self.assertEqual(row.persist_log_preferences.call_count, 1)

        row.inline_log_panel.line_count.setValue(5000)
        QApplication.processEvents()
        self.assertEqual(row.persist_log_preferences.call_count, 2)

        coordinator.request_open(row, "stderr")
        self.assertEqual(row.persist_log_preferences.call_count, 3)
        self.assertEqual(row.inline_log_panel.stream_label.text(), "stderr")

        coordinator.request_open(row, "stderr")
        self.assertEqual(row.persist_log_preferences.call_count, 3)
        coordinator.shutdown()

    def test_shutdown_disconnects_all_old_panel_callbacks_and_is_idempotent(self):
        row = self.make_row("one", [ready_snapshot("one"), ready_snapshot("unexpected")])
        coordinator = InlineLogPanelCoordinator(refresh_interval_ms=20, hide_delay_ms=15)
        visibility_changed = MagicMock()
        coordinator.panel_visibility_changed.connect(visibility_changed)
        coordinator.request_open(row, "stdout")

        coordinator.shutdown()
        coordinator.shutdown()
        visibility_changed.reset_mock()
        row.manager.get_log_snapshot.reset_mock()
        row.persist_log_preferences.reset_mock()

        row.inline_log_panel.pointer_entered.emit()
        row.inline_log_panel.pointer_left.emit()
        row.inline_log_panel.filter_edit.setText("after-shutdown")
        row.inline_log_panel.line_count.setValue(5000)
        QApplication.processEvents()
        QTest.qWait(30)

        self.assertFalse(coordinator.refresh_timer.isActive())
        self.assertFalse(coordinator.hide_timer.isActive())
        self.assertIsNone(coordinator.current_row)
        self.assertEqual(coordinator._registered_rows, set())
        self.assertEqual(coordinator._row_callbacks, {})
        row.manager.get_log_snapshot.assert_not_called()
        row.persist_log_preferences.assert_not_called()
        visibility_changed.assert_not_called()


class InlineMenuGeometryTests(unittest.TestCase):
    def test_native_menu_style_forces_scrollable_hint(self):
        style = NativeScrollableMenuStyle()
        try:
            self.assertEqual(
                style.styleHint(QStyle.StyleHint.SH_Menu_Scrollable),
                1,
            )
        finally:
            style.deleteLater()

    def test_normal_bottom_and_emergency_geometry(self):
        available = QRect(0, 0, 1200, 900)
        normal = compute_inline_menu_geometry(
            base_menu_size=QSize(700, 400),
            content_width=760,
            desired_panel_height=PANEL_PREFERRED_HEIGHT,
            available_geometry=available,
            anchor_rect=QRect(1100, 820, 20, 20),
        )
        self.assertEqual(normal.panel_height, PANEL_PREFERRED_HEIGHT)
        self.assertLessEqual(normal.menu_rect.right(), available.right() - 12)
        self.assertLessEqual(normal.menu_rect.bottom(), available.bottom() - 12)

        emergency = compute_inline_menu_geometry(
            base_menu_size=QSize(700, 850),
            content_width=900,
            desired_panel_height=PANEL_PREFERRED_HEIGHT,
            available_geometry=available,
            anchor_rect=QRect(20, 880, 10, 10),
        )
        self.assertEqual(emergency.panel_height, PANEL_EMERGENCY_MIN_HEIGHT)
        self.assertTrue(emergency.native_menu_overflow)
        self.assertLessEqual(emergency.menu_rect.height(), available.height() - 24)
        self.assertLessEqual(emergency.menu_rect.width(), available.width() - 24)


if __name__ == "__main__":
    unittest.main()
