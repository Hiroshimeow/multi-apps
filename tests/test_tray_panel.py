import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from lib.ui.tray_panel import TrayActionButton, TrayPanelWindow, compute_tray_panel_rect


_QT_APP = QApplication.instance() or QApplication([])


class TrayPanelGeometryTests(unittest.TestCase):
    def test_panel_is_clamped_above_bottom_right_tray_anchor(self):
        available = QRect(0, 0, 1920, 1032)
        anchor = QRect(1880, 1000, 32, 32)

        rect = compute_tray_panel_rect(
            QSize(620, 560),
            anchor,
            available,
        )

        self.assertEqual(rect.size(), QSize(620, 560))
        self.assertLessEqual(rect.right(), available.right() - 12)
        self.assertLessEqual(rect.bottom(), anchor.top() - 8)
        self.assertGreaterEqual(rect.left(), available.left() + 12)
        self.assertGreaterEqual(rect.top(), available.top() + 12)


class TrayPanelInteractionTests(unittest.TestCase):
    def test_internal_left_and_right_clicks_do_not_hide_panel(self):
        panel = TrayPanelWindow()
        row = QWidget()
        row.resize(400, 44)
        label = QLabel("Demo", row)
        label.setGeometry(10, 8, 120, 24)
        panel.set_rows([row])
        panel.show_at(QRect(700, 700, 24, 24), QRect(0, 0, 900, 760))
        QApplication.processEvents()
        try:
            self.assertTrue(panel.isVisible())

            QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(300, 20))
            QApplication.processEvents()
            self.assertTrue(panel.isVisible())

            QTest.mouseClick(label, Qt.MouseButton.RightButton)
            QApplication.processEvents()
            self.assertTrue(panel.isVisible())
        finally:
            panel.hide()
            panel.deleteLater()
            QApplication.processEvents()

    def test_reserved_popup_space_caps_panel_before_first_hover(self):
        panel = TrayPanelWindow()
        tall_row = QWidget()
        tall_row.setMinimumHeight(1600)
        panel.set_rows([tall_row])
        geometry = panel.show_at(
            QRect(1880, 1000, 32, 32),
            QRect(0, 0, 1920, 1032),
            reserved_top_height=228,
        )
        QApplication.processEvents()
        try:
            self.assertLessEqual(geometry.height(), 780)
            self.assertGreaterEqual(geometry.top(), 240)
        finally:
            panel.hide()
            panel.deleteLater()
            QApplication.processEvents()

    def test_clear_actions_deletes_old_buttons(self):
        panel = TrayPanelWindow()
        for _ in range(20):
            panel.clear_actions()
            for index in range(4):
                panel.add_action(f"Action {index}", lambda: None)
            QApplication.processEvents()
        try:
            buttons = panel.actions_container.findChildren(TrayActionButton)
            self.assertEqual(len(buttons), 4)
        finally:
            panel.clear_actions()
            panel.deleteLater()
            QApplication.processEvents()

    def test_escape_hides_panel(self):
        panel = TrayPanelWindow()
        panel.show_at(QRect(700, 700, 24, 24), QRect(0, 0, 900, 760))
        QApplication.processEvents()
        try:
            QTest.keyClick(panel, Qt.Key.Key_Escape)
            QApplication.processEvents()
            self.assertFalse(panel.isVisible())
        finally:
            panel.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
