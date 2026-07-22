import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from lib.ui.inline_log_panel import InlineLogPanel
from lib.ui.log_popup import LogPopupWindow, compute_log_popup_rect
from lib.ui.tray_panel import TrayPanelWindow


_QT_APP = QApplication.instance() or QApplication([])


class LogPopupGeometryTests(unittest.TestCase):
    def test_popup_is_fixed_above_panel_and_clamped_to_screen(self):
        available = QRect(0, 0, 1920, 1032)
        panel_rect = QRect(1299, 459, 609, 561)

        popup_rect = compute_log_popup_rect(
            QSize(609, 220),
            panel_rect,
            available,
        )

        self.assertEqual(popup_rect.width(), panel_rect.width())
        self.assertEqual(popup_rect.bottom(), panel_rect.top() - 9)
        self.assertGreaterEqual(popup_rect.left(), available.left() + 12)
        self.assertGreaterEqual(popup_rect.top(), available.top() + 12)
        self.assertLessEqual(popup_rect.right(), available.right() - 12)


class LogPopupWindowTests(unittest.TestCase):
    def test_popup_owns_exactly_one_panel_and_does_not_move_tray_panel(self):
        tray_panel = TrayPanelWindow()
        tray_panel.show_at(QRect(1880, 1000, 32, 32), QRect(0, 0, 1920, 1032))
        popup = LogPopupWindow()
        QApplication.processEvents()
        try:
            tray_geometry = QRect(tray_panel.frameGeometry())
            popup.show_for(tray_geometry, QRect(0, 0, 1920, 1032))
            QApplication.processEvents()

            self.assertTrue(popup.isWindow())
            self.assertIsNone(popup.parentWidget())
            self.assertEqual(len(popup.findChildren(InlineLogPanel)), 1)
            self.assertIs(popup.panel, popup.findChild(InlineLogPanel))
            self.assertEqual(tray_panel.frameGeometry(), tray_geometry)
            self.assertTrue(popup.panel.isVisible())
        finally:
            popup.hide()
            popup.deleteLater()
            tray_panel.hide()
            tray_panel.deleteLater()
            QApplication.processEvents()

    def test_clicking_log_content_does_not_hide_popup(self):
        popup = LogPopupWindow()
        popup.show_for(QRect(300, 400, 620, 300), QRect(0, 0, 1200, 900))
        QApplication.processEvents()
        try:
            QTest.mouseClick(popup.panel.log_view.viewport(), Qt.MouseButton.LeftButton)
            QApplication.processEvents()
            self.assertTrue(popup.isVisible())
        finally:
            popup.hide()
            popup.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
