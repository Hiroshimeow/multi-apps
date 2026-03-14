import time
import sqlite3
import os
from win32_monitor import Win32ClipboardMonitor
from PyQt6.QtWidgets import QApplication
import sys

def on_change():
    print(f"[{time.strftime('%H:%M:%S')}] SIGNAL: clipboard_changed received from Win32!")

def on_hotkey():
    print(f"[{time.strftime('%H:%M:%S')}] SIGNAL: hotkey_toggle received from Win32!")

def check_db():
    db_path = os.path.join(os.path.dirname(__file__), "clipboard.db")
    if not os.path.exists(db_path):
        return 0
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM clips")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        print(f"DB Error: {e}")
        return -1

def main():
    app = QApplication(sys.argv)
    
    print("Starting Win32 Monitor test...")
    monitor = Win32ClipboardMonitor()
    monitor.clipboard_changed.connect(on_change)
    monitor.hotkey_toggle.connect(on_hotkey)
    monitor.start()
    
    last_count = check_db()
    print(f"Current DB items: {last_count}")
    print("Please press Ctrl+C to copy some text, or Ctrl+Alt+V to test hotkey...")
    
    def loop():
        nonlocal last_count
        current_count = check_db()
        if current_count != last_count and current_count != -1:
            print(f"[{time.strftime('%H:%M:%S')}] DB UPDATED: Item count changed from {last_count} to {current_count}")
            last_count = current_count
            
    # Check DB every second
    from PyQt6.QtCore import QTimer
    timer = QTimer()
    timer.timeout.connect(loop)
    timer.start(1000)
    
    # Run for 30 seconds then exit
    QTimer.singleShot(30000, app.quit)
    
    app.exec()
    monitor.stop()
    print("Test finished.")

if __name__ == '__main__':
    main()
