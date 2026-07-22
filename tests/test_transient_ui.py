import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import MagicMock

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFrame, QPushButton

from lib.ui.transient_ui import TransientUiController


_QT_APP = QApplication.instance() or QApplication([])


class TransientUiControllerTests(unittest.TestCase):
    def test_global_position_inside_protected_frame_handles_non_widget_receiver(self):
        tray = QFrame()
        tray.setGeometry(QRect(100, 100, 300, 300))
        tray.show()
        QApplication.processEvents()
        controller = TransientUiController(
            windows_provider=lambda: (tray,),
            dismiss_callback=lambda: None,
        )
        try:
            self.assertTrue(controller._inside_transient(object(), tray.frameGeometry().center()))
            self.assertFalse(controller._inside_transient(object(), QPoint(700, 700)))
        finally:
            controller.shutdown()
            tray.deleteLater()
            QApplication.processEvents()

    def test_global_button_transition_dismisses_only_outside_protected_frames(self):
        tray = QFrame()
        tray.setGeometry(QRect(100, 100, 300, 300))
        tray.show()
        QApplication.processEvents()
        buttons = [Qt.MouseButton.NoButton]
        cursor = [tray.frameGeometry().center()]
        dismiss = MagicMock()
        controller = TransientUiController(
            windows_provider=lambda: (tray,),
            dismiss_callback=dismiss,
            buttons_provider=lambda: buttons[0],
            cursor_provider=lambda: cursor[0],
            poll_interval_ms=10000,
        )
        try:
            buttons[0] = Qt.MouseButton.LeftButton
            controller._poll_global_pointer()
            dismiss.assert_not_called()
            buttons[0] = Qt.MouseButton.NoButton
            controller._poll_global_pointer()

            cursor[0] = QPoint(700, 700)
            buttons[0] = Qt.MouseButton.LeftButton
            controller._poll_global_pointer()
            dismiss.assert_called_once_with()
        finally:
            controller.shutdown()
            tray.deleteLater()
            QApplication.processEvents()

    def test_inside_click_is_kept_and_outside_click_dismisses_once(self):
        tray = QFrame()
        tray.setGeometry(QRect(100, 100, 300, 300))
        inside = QPushButton("inside", tray)
        inside.setGeometry(20, 20, 100, 30)
        outside = QPushButton("outside")
        outside.setGeometry(500, 100, 100, 30)
        tray.show()
        outside.show()
        QApplication.processEvents()
        dismiss = MagicMock()
        controller = TransientUiController(
            windows_provider=lambda: (tray,),
            dismiss_callback=dismiss,
        )
        try:
            QTest.mouseClick(inside, Qt.MouseButton.LeftButton)
            QApplication.processEvents()
            dismiss.assert_not_called()

            QTest.mouseClick(outside, Qt.MouseButton.LeftButton)
            QApplication.processEvents()
            dismiss.assert_called_once_with()
        finally:
            controller.shutdown()
            tray.deleteLater()
            outside.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
