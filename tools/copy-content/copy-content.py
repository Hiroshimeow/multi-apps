# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "PyQt6",
#     "pynput",
#     "pywin32; sys_platform == 'win32'",
# ]
# ///
import sys
import os
import time
import re
import json
import fnmatch
import ctypes
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QSystemTrayIcon,
    QMenu,
    QStyle,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QEvent
from PyQt6.QtGui import QAction, QCursor, QGuiApplication

try:
    from pynput import keyboard
    from pynput.keyboard import Key

    HAS_PYNPUT = True
except ImportError:
    keyboard = None
    Key = None
    HAS_PYNPUT = False

# --- Cố gắng import thư viện lấy Path Windows ---
try:
    import win32gui
    import win32com.client
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


# --- STYLE ---
STYLESHEET = """
QWidget {
    background-color: #202020;
    color: #cccccc;
    font-family: 'Segoe UI', sans-serif;
    font-size: 10pt;
    border-radius: 8px;
}
QCheckBox {
    spacing: 5px; color: #9cdcfe; font-weight: bold;
}
QCheckBox::indicator {
    width: 14px; height: 14px; background: #2d2d2d; border: 1px solid #555; border-radius: 3px;
}
QCheckBox::indicator:checked {
    background: #007acc; border: 1px solid #007acc;
}
QLineEdit {
    background-color: #2d2d2d; border: 1px solid #3e3e42;
    border-radius: 3px; padding: 4px 8px; color: #ce9178;
}
QLineEdit:focus { border: 1px solid #007acc; }
QPushButton {
    background-color: #0e639c; color: white; border: none;
    border-radius: 3px; padding: 5px 12px; font-weight: bold;
}
QPushButton:hover { background-color: #1177bb; }
QPushButton#closeBtn {
    background-color: transparent; color: #666; font-size: 12px; padding: 0; margin: 0;
}
QPushButton#closeBtn:hover { color: #ff5555; }
"""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "copy-content-config.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "copy-content.txt")

# --- Worker xử lý Hotkey (Theo phong cách auto-suggest) ---
class HotkeyWorker(QObject):
    activated = pyqtSignal()
    escape_pressed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.hotkeys = None
        self.listener = None

    def start(self):
        if not HAS_PYNPUT:
            return False
        # Lắng nghe tổ hợp phím Ctrl+Alt+C
        self.hotkeys = keyboard.GlobalHotKeys({
            '<ctrl>+<alt>+c': self.on_activate
        })
        
        # Lắng nghe phím Esc để ẩn
        self.listener = keyboard.Listener(on_press=self.on_press)
        
        self.hotkeys.start()
        self.listener.start()
        return True

    def on_activate(self):
        self.activated.emit()

    def on_press(self, key):
        if key == Key.esc:
            self.escape_pressed.emit()

    def stop(self):
        if self.hotkeys: self.hotkeys.stop()
        if self.listener: self.listener.stop()


class ConfigManager:
    """Quản lý Load/Save config ra file JSON"""
    def __init__(self, filepath):
        self.filepath = filepath
        self.data = self._load_file()

    def _load_file(self):
        if not os.path.exists(self.filepath): return {}
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}

    def save_file(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e: print(f"Save config failed: {e}")

    def get_config(self, path):
        norm_key = os.path.normcase(os.path.abspath(path))
        return self.data.get(norm_key, None)

    def set_config(self, path, extra, ignore):
        if not path or not os.path.isdir(path): return
        norm_key = os.path.normcase(os.path.abspath(path))
        if not extra and not ignore:
            if norm_key in self.data: del self.data[norm_key]
        else:
            self.data[norm_key] = {"extra": extra, "ignore": ignore}
        self.save_file()


class MiniCopier(QWidget):
    def __init__(self):
        super().__init__()
        self.check_boxes = {}
        self.config_manager = ConfigManager(CONFIG_FILE)
        self.loading_config = False
        self.input_locked = False

        self.initUI()
        self.load_data_initial()

        # Hotkey Worker
        self.hotkey_worker = HotkeyWorker()
        self.hotkey_worker.activated.connect(self.toggle_window)
        self.hotkey_worker.escape_pressed.connect(self.hide_if_visible)
        self.hotkey_available = self.hotkey_worker.start()
        if not self.hotkey_available:
            QTimer.singleShot(0, self.show_window)

    def initUI(self):
        self.setWindowTitle("Mini Copier")
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLESHEET)
        self.setFixedSize(420, 160)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # --- DÒNG 1: Checkbox + Close ---
        row1 = QHBoxLayout()
        extensions = [".py", ".md", ".txt", ".json", "All"]
        for ext in extensions:
            cb = QCheckBox(ext)
            cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if ext in [".py", ".md"]:
                cb.setChecked(True)
            self.check_boxes[ext] = cb
            row1.addWidget(cb)

        row1.addStretch()
        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("closeBtn")
        self.btn_close.setFixedSize(20, 20)
        self.btn_close.clicked.connect(self.hide)
        row1.addWidget(self.btn_close)
        main_layout.addLayout(row1)

        # --- DÒNG 2: Path + GO ---
        row2 = QHBoxLayout()
        row2.setSpacing(5)
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Root Path...")
        self.path_input.returnPressed.connect(self.process_copy)
        self.path_input.textChanged.connect(self.on_path_changed)
        row2.addWidget(self.path_input)

        self.btn_run = QPushButton("GO")
        self.btn_run.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_run.setFixedWidth(50)
        self.btn_run.clicked.connect(self.process_copy)
        row2.addWidget(self.btn_run)
        main_layout.addLayout(row2)

        # --- DÒNG 3: Extra ---
        row3 = QHBoxLayout()
        self.extra_ext_input = QLineEdit()
        self.extra_ext_input.setPlaceholderText("Include (Ưu tiên): a/g, .html ...")
        self.extra_ext_input.editingFinished.connect(self.save_current_config)
        row3.addWidget(self.extra_ext_input)
        main_layout.addLayout(row3)

        # --- DÒNG 4: Ignore ---
        row4 = QHBoxLayout()
        self.ignore_input = QLineEdit()
        self.ignore_input.setPlaceholderText("Ignore: a, node_modules...")
        self.ignore_input.editingFinished.connect(self.save_current_config)
        row4.addWidget(self.ignore_input)
        main_layout.addLayout(row4)

        self.setLayout(main_layout)

    def load_data_initial(self):
        # Placeholder for any initial data loading if needed
        pass

    # --- Xử lý Ẩn/Hiện mượt (Logic từ auto-suggest) ---
    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                self.hide()
        super().changeEvent(event)

    def hide_if_visible(self):
        if self.isVisible(): self.hide()

    def toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_window()

    def show_window(self):
        # 1. Khóa input 150ms để reset trạng thái bàn phím
        self.input_locked = True
        QTimer.singleShot(150, lambda: setattr(self, 'input_locked', False))

        # 2. Phát hiện path từ Explorer
        detected = self.get_active_explorer_path()
        if detected:
            current = self.path_input.text()
            if os.path.normcase(detected) != os.path.normcase(current):
                self.path_input.setText(detected)

        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        geo = screen.geometry()
        
        w, h = self.width(), self.height()
        x, y = pos.x() - (w // 2), pos.y() - (h // 2)
        x = max(geo.x(), min(x, geo.x() + geo.width() - w))
        y = max(geo.y(), min(y, geo.y() + geo.height() - h))
        
        self.move(x, y)
        
        # 3. Hiện cửa sổ & Force Focus
        self.show()
        if sys.platform == "win32":
            try:
                our_hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                foreground_hwnd = user32.GetForegroundWindow()
                if foreground_hwnd != our_hwnd:
                    f_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None)
                    a_thread = user32.GetWindowThreadProcessId(our_hwnd, None)
                    user32.AttachThreadInput(f_thread, a_thread, True)
                    user32.SetForegroundWindow(our_hwnd)
                    user32.SetFocus(our_hwnd)
                    user32.AttachThreadInput(f_thread, a_thread, False)
            except: pass

        self.raise_()
        self.activateWindow()
        self.path_input.setFocus()
        self.path_input.selectAll()
        self.btn_run.setText("GO")
        self.btn_run.setStyleSheet("")

    def get_active_explorer_path(self):
        if not HAS_WIN32: return ""
        try:
            point = win32gui.GetCursorPos()
            hwnd_under_mouse = win32gui.WindowFromPoint(point)
            hwnd_root = win32gui.GetAncestor(hwnd_under_mouse, 2)
            target_hwnd = hwnd_root if win32gui.GetClassName(hwnd_root) == "CabinetWClass" else None
            if not target_hwnd:
                hwnd_active = win32gui.GetForegroundWindow()
                if win32gui.GetClassName(hwnd_active) == "CabinetWClass":
                    target_hwnd = hwnd_active
            if not target_hwnd: return ""
            shell = win32com.client.Dispatch("Shell.Application")
            for w in shell.Windows():
                if int(w.HWND) == int(target_hwnd):
                    return w.Document.Folder.Self.Path
        except: pass
        return ""

    # --- Config Logic ---
    def on_path_changed(self, text):
        if self.input_locked: return
        path = text.strip().replace('"', "").replace("'", "")
        if os.path.isdir(path):
            self.load_config_for_path(path)

    def load_config_for_path(self, path):
        self.loading_config = True
        conf = self.config_manager.get_config(path)
        if conf:
            self.extra_ext_input.setText(conf.get("extra", ""))
            self.ignore_input.setText(conf.get("ignore", ""))
        else:
            self.extra_ext_input.clear()
            self.ignore_input.clear()
        self.loading_config = False

    def save_current_config(self):
        if self.loading_config: return
        path = self.path_input.text().strip().replace('"', "").replace("'", "")
        if os.path.isdir(path):
            extra = self.extra_ext_input.text().strip()
            ignore = self.ignore_input.text().strip()
            self.config_manager.set_config(path, extra, ignore)

    # --- Logic Xử lý Copy ---
    def process_copy(self):
        if self.input_locked: return
        self.save_current_config()
        root_path = self.path_input.text().strip().replace('"', "").replace("'", "")
        if not os.path.isdir(root_path):
            self.path_input.setText("Invalid Path!")
            return

        def split_trim(s): return [x.strip() for x in re.split(r"[,\n]+", s or "") if x.strip()]
        def norm_path(p): return os.path.normcase(os.path.abspath(p))
        def is_same_or_child(path, base):
            try:
                return os.path.commonpath([path, base]) == base
            except ValueError:
                return False

        target_exts = set()
        is_all_ext = self.check_boxes["All"].isChecked()
        if not is_all_ext:
            for k, v in self.check_boxes.items():
                if k != "All" and v.isChecked(): target_exts.add(k)

        include_dirs = []
        extra_exts = set()
        for t in split_trim(self.extra_ext_input.text()):
            if t.startswith("."): extra_exts.add(t if t.startswith("*") else f"*{t}")
            elif "/" in t or "\\" in t: include_dirs.append(norm_path(os.path.join(root_path, t)))
            else:
                p = os.path.join(root_path, t)
                if os.path.isdir(p): include_dirs.append(norm_path(p))
                else: extra_exts.add(f"*{t}" if t.startswith("*") else f"*.{t}")

        ignore_paths = set(); ignore_all_mode = False
        for t in split_trim(self.ignore_input.text()):
            if t.lower() in ["all", "*"]: ignore_all_mode = True
            else: ignore_paths.add(norm_path(os.path.join(root_path, t)))

        root_abs = norm_path(root_path)
        content = [f"Root: {root_path}", f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"]
        count = 0

        for current_root, dirs, files in os.walk(root_path, topdown=True):
            current_abs = norm_path(current_root)
            allowed_dirs = []
            for d in dirs:
                d_abs = norm_path(os.path.join(current_abs, d))
                if d.startswith(".") or d in ["__pycache__", "node_modules", "venv"]: continue
                # Basic check for inclusion/exclusion
                is_inc = any(is_same_or_child(d_abs, w) or is_same_or_child(w, d_abs) for w in include_dirs)
                is_ig = ignore_all_mode or d_abs in ignore_paths or any(is_same_or_child(d_abs, ig) for ig in ignore_paths)
                if is_inc or not is_ig: allowed_dirs.append(d)
            dirs[:] = allowed_dirs

            ignored_current = ignore_all_mode or any(
                is_same_or_child(current_abs, ig) for ig in ignore_paths
            )
            forced_include = any(
                is_same_or_child(current_abs, w) for w in include_dirs
            )
            if not ignored_current or forced_include:
                for file in files:
                    valid = is_all_ext or any(file.endswith(e) for e in target_exts) or any(fnmatch.fnmatch(file, pat) for pat in extra_exts)
                    if valid:
                        try:
                            f_abs = os.path.join(current_root, file)
                            with open(f_abs, "r", encoding="utf-8", errors="ignore") as f:
                                text = f.read()
                                rel = os.path.relpath(f_abs, root_abs)
                                content.append(f"{'-' * 40}\nFile: {rel}\n{'-' * 40}\n{text}\n")
                                count += 1
                        except: pass

        if count > 0:
            final_text = "\n".join(content)
            QApplication.clipboard().setText(final_text)
            try:
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f: f.write(final_text)
            except: pass
            self.btn_run.setText("OK")
            self.btn_run.setStyleSheet("background-color: #4ec9b0; color: #1e1e1e;")
            QTimer.singleShot(600, self.hide)
        else:
            self.btn_run.setText("0")
            self.btn_run.setStyleSheet("background-color: #f44747;")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape: self.hide()
        super().keyPressEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ex = MiniCopier()
    sys.exit(app.exec())
