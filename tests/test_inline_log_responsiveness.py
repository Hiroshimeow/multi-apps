import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame

from lib.ui.inline_log_panel import InlineLogPanel
from lib.ui.log_filter import apply_log_filter
from lib.ui.log_reader import LogSnapshot, LogSnapshotState


_QT_APP = QApplication.instance() or QApplication([])


def snapshot(lines, *, size, identity=(1, 1)):
    return LogSnapshot(
        path="C:/logs/demo.log",
        state=LogSnapshotState.READY,
        lines=tuple(lines),
        size_bytes=size,
        file_identity=identity,
    )


class InlineLogResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.host = QFrame()
        self.host.resize(900, 280)
        self.panel = InlineLogPanel(self.host)
        self.panel.resize(860, 220)
        self.panel.show()
        self.host.show()
        QApplication.processEvents()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        QApplication.processEvents()

    def test_compatible_5000_line_append_avoids_full_document_replacement(self):
        old_lines = tuple(f"line-{index:04d}" for index in range(5000))
        new_lines = old_lines[2:] + ("new-5000", "new-5001")
        self.panel.update_snapshot(snapshot(old_lines, size=60000))
        original = self.panel.log_view.setPlainText
        self.panel.log_view.setPlainText = MagicMock(wraps=original)

        self.panel.update_snapshot(snapshot(new_lines, size=60020))

        self.panel.log_view.setPlainText.assert_not_called()
        self.assertEqual(tuple(self.panel.log_view.toPlainText().splitlines()), new_lines)

    def test_append_changes_only_evicted_prefix_and_new_suffix_text(self):
        old_lines = tuple(f"line-{index:04d}" for index in range(5000))
        new_lines = old_lines[2:] + ("new-a", "new-b")
        self.panel.update_snapshot(snapshot(old_lines, size=60000))
        changes = []
        self.panel.log_view.document().contentsChange.connect(
            lambda position, removed, added: changes.append((position, removed, added))
        )

        self.panel.update_snapshot(snapshot(new_lines, size=60020))

        self.assertTrue(changes)
        self.assertLessEqual(sum(item[1] for item in changes), 24)
        self.assertLessEqual(sum(item[2] for item in changes), 16)
        self.assertEqual(tuple(self.panel.log_view.toPlainText().splitlines()), new_lines)

    def test_filtered_append_only_filters_new_source_suffix(self):
        old_lines = tuple(
            f"{'keep' if index % 2 == 0 else 'drop'}-{index:04d}"
            for index in range(5000)
        )
        new_lines = old_lines[1:] + ("keep-new",)
        self.panel.filter_edit.setText("keep")
        self.panel.update_snapshot(snapshot(old_lines, size=60000))
        original_set_text = self.panel.log_view.setPlainText
        self.panel.log_view.setPlainText = MagicMock(wraps=original_set_text)

        with patch(
            "lib.ui.inline_log_panel.apply_log_filter",
            wraps=apply_log_filter,
        ) as filter_call:
            self.panel.update_snapshot(snapshot(new_lines, size=60020))

        self.panel.log_view.setPlainText.assert_not_called()
        filter_call.assert_called_once()
        self.assertEqual(len(filter_call.call_args.args[0]), 1)
        self.assertTrue(self.panel.log_view.toPlainText().endswith("keep-new"))

    def test_structural_change_still_uses_full_render(self):
        self.panel.update_snapshot(snapshot(("before",), size=7, identity=(1, 1)))
        original = self.panel.log_view.setPlainText
        self.panel.log_view.setPlainText = MagicMock(wraps=original)

        self.panel.update_snapshot(snapshot(("rotated",), size=8, identity=(2, 1)))

        self.panel.log_view.setPlainText.assert_called_once_with("rotated")

    def test_queued_snapshots_collapse_to_latest_payload(self):
        calls = []
        original = self.panel.update_snapshot

        def capture(current, *, reset_bottom=False, force=False):
            calls.append((current.lines, reset_bottom, force))
            return original(current, reset_bottom=reset_bottom, force=force)

        self.panel.update_snapshot = capture
        self.panel.queue_snapshot(snapshot(("one",), size=4), reset_bottom=True)
        self.panel.queue_snapshot(snapshot(("two",), size=8))
        self.panel.queue_snapshot(snapshot(("latest",), size=12))
        QApplication.processEvents()

        self.assertEqual(calls, [(('latest',), False, False)])
        self.assertEqual(self.panel.log_view.toPlainText(), "latest")

    def test_stream_switch_discards_stale_queued_snapshot(self):
        self.panel.queue_snapshot(snapshot(("stale stdout",), size=12))
        self.panel.prepare_stream("stderr")
        QApplication.processEvents()

        self.assertEqual(self.panel.stream_label.text(), "stderr")
        self.assertNotIn("stale stdout", self.panel.log_view.toPlainText())

    def test_scheduler_yields_between_multiple_slow_panels(self):
        panels = [self.panel]
        extra_hosts = []
        events = []
        try:
            for _ in range(3):
                host = QFrame()
                panel = InlineLogPanel(host)
                panel.show()
                host.show()
                extra_hosts.append(host)
                panels.append(panel)
            QApplication.processEvents()

            for index, panel in enumerate(panels):
                def slow_render(
                    _snapshot,
                    *,
                    reset_bottom=False,
                    force=False,
                    selected=index,
                ):
                    events.append(("render", selected))
                    QTimer.singleShot(0, lambda current=selected: events.append(("heartbeat", current)))

                panel.update_snapshot = slow_render

            for panel in panels:
                panel.queue_snapshot(snapshot(("new",), size=4))

            deadline = time.monotonic() + 2
            while (
                sum(kind == "render" for kind, _value in events) < len(panels)
                and time.monotonic() < deadline
            ):
                QApplication.processEvents()
                QTest.qWait(1)
            QApplication.processEvents()

            render_positions = [
                index for index, event in enumerate(events) if event[0] == "render"
            ]
            self.assertEqual(sorted(value for kind, value in events if kind == "render"), list(range(4)))
            self.assertEqual(len(render_positions), len(panels))
            for left, right in zip(render_positions, render_positions[1:]):
                self.assertIn("heartbeat", [kind for kind, _value in events[left + 1 : right]])
        finally:
            for host in extra_hosts:
                host.close()
                host.deleteLater()
            QApplication.processEvents()

    def test_four_filtered_panels_append_callbacks_use_incremental_updates(self):
        panels = [self.panel]
        extra_hosts = []
        try:
            for _ in range(3):
                host = QFrame()
                panel = InlineLogPanel(host)
                panel.resize(860, 220)
                panel.show()
                host.show()
                extra_hosts.append(host)
                panels.append(panel)
            QApplication.processEvents()

            base = tuple(f"line-{index:04d}" for index in range(5000))
            full_repaints = []
            for panel in panels:
                panel.filter_edit.setText("line-")
                panel.update_snapshot(snapshot(base, size=60000))
                repaint = MagicMock(wraps=panel.log_view.setPlainText)
                panel.log_view.setPlainText = repaint
                full_repaints.append(repaint)

            lines = base
            for iteration in range(8):
                lines = lines[1:] + (f"line-new-{iteration}",)
                for panel in panels:
                    panel.queue_snapshot(snapshot(lines, size=60020 + iteration))
                for panel in panels:
                    panel._render_pending_snapshot()
                QApplication.processEvents()

            self.assertTrue(all(repaint.call_count == 0 for repaint in full_repaints))
            self.assertTrue(
                all(panel.log_view.toPlainText().endswith("line-new-7") for panel in panels)
            )
        finally:
            for host in extra_hosts:
                host.close()
                host.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
