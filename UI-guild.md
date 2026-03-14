# UI & Keyboard Interaction Guild (Best Practices)

Tài liệu này hướng dẫn các tiêu chuẩn thiết kế UI và xử lý bàn phím cho các công cụ nhỏ (micro-tools) trong project, nhằm đảm bảo trải nghiệm mượt mà, không giật lag và đặc biệt là **không bị kẹt phím (sticky keys)**.

## 1. Xử lý Hotkey & Bàn phím (Keyboard Handling)

### Thư viện ưu tiên: `pynput`
Sử dụng `pynput` thay vì thư viện `keyboard`. 
- **Ưu điểm:** Chạy trên luồng riêng, ổn định hơn khi kết hợp với PyQt6, không can thiệp sâu vào driver hệ thống gây kẹt phím.

### Cơ chế Chặn Input (Input Locking) - QUAN TRỌNG
Khi một công cụ được mở bằng Hotkey (ví dụ: `Ctrl+Alt+V`), người dùng có thể chưa thả phím ngay lập tức.
- **Giải pháp:** Sử dụng một cờ `self.input_locked = True` trong khoảng **150ms** ngay khi box hiện lên.
- **Tác dụng:** Lờ đi các tín hiệu phím "thừa" hoặc "rác" lọt vào ô nhập liệu hoặc gây nhiễu cho hệ thống.

### Giả lập Paste (Safe Pasting)
Để paste dữ liệu một cách an toàn mà không làm kẹt phím `Ctrl`:
1. **Giải phóng phím cũ:** Thực hiện `kb_controller.release(Key.ctrl)` và `Key.alt` một cách tường minh trước khi gửi lệnh mới.
2. **Delay OS:** Chờ khoảng **150ms** sau khi ẩn UI để hệ thống trả lại focus cho ứng dụng cũ (Word, Excel, Explorer...) rồi mới thực hiện lệnh Paste.

```python
def _perform_keyboard_paste(self):
    # Đảm bảo các phím bổ trợ được nhả ra
    self.kb_controller.release(Key.ctrl)
    self.kb_controller.release(Key.alt)
    
    # Thực hiện tổ hợp Ctrl+V
    with self.kb_controller.pressed(Key.ctrl):
        self.kb_controller.press('v')
        self.kb_controller.release('v')
```

---

## 2. Quản lý Giao diện (UI Management)

### Chiếm quyền Focus (Focus Hack cho Windows)
Windows thường chặn các ứng dụng tự ý nhảy lên phía trước (`SetForegroundWindow`). 
- **Giải pháp:** Sử dụng `AttachThreadInput` để gắn luồng của ứng dụng vào luồng của cửa sổ đang active trước khi gọi `SetForegroundWindow`.

```python
if sys.platform == "win32":
    try:
        our_hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        foreground_hwnd = user32.GetForegroundWindow()
        f_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None)
        a_thread = user32.GetWindowThreadProcessId(our_hwnd, None)
        user32.AttachThreadInput(f_thread, a_thread, True)
        user32.SetForegroundWindow(our_hwnd)
        user32.SetFocus(our_hwnd)
        user32.AttachThreadInput(f_thread, a_thread, False)
    except: pass
```

### Tự động ẩn khi mất Focus (Hide on Blur)
Thay vì dùng `eventFilter` phức tạp, hãy sử dụng `changeEvent` để bắt sự kiện `ActivationChange`. Đây là cách mượt nhất để ẩn UI khi người dùng click ra ngoài.

```python
def changeEvent(self, event):
    if event.type() == QEvent.Type.ActivationChange:
        if not self.isActiveWindow():
            self.hide()
    super().changeEvent(event)
```

---

## 3. Hiệu năng (Performance)

### Lazy UI Update
Không refresh lại toàn bộ danh sách (ListWidget) mỗi khi có thay đổi dữ liệu nếu cửa sổ đang ẩn.
- **Giải pháp:** Đánh dấu một cờ `self.is_ui_dirty = True`. Chỉ thực hiện render lại UI ngay trước khi gọi `self.show()`.

### Giảm Flickering (Nháy màn hình)
Sử dụng `self.setUpdatesEnabled(False)` trước khi thực hiện các thay đổi hàng loạt trên ListWidget (clear, add items) và bật lại sau khi xong.

---

## 4. Checklist cho công cụ mới
- [ ] Sử dụng `pynput` cho hotkey.
- [ ] Có `input_locked` (150ms) khi show UI.
- [ ] Có `Focus Hack` cho Windows.
- [ ] Sử dụng `changeEvent` để ẩn khi mất focus.
---

## 5. Hỗ trợ Scripts & Commands (Windows)

### Chạy .bat và .vbs
Để đảm bảo log được bắt chính xác và không hiện cửa sổ console thừa:
1. **.bat/.cmd:** Chạy thông qua `cmd /c`.
2. **.vbs:** Chạy thông qua `cscript //nologo`.
3. **Shell=True:** Luôn bật trên Windows cho các loại runner này để nhận diện đúng lệnh hệ thống.

### Hỗ trợ UV (uv run)
Runner `uv` được thiết kế để tận dụng tốc độ của công cụ `uv`:
- Tự động gọi `uv run`.
- Nếu `path` là một file tồn tại, nó sẽ chạy file đó. Nếu không, nó coi đó là một lệnh được định nghĩa trong `pyproject.toml`.

### Auto-detection Logic
Khi thiết kế runner mới, hệ thống tự động phân loại ứng dụng:
- Nếu có `type: uv`: Dùng `UvRunner`.
- Nếu có `command`: Dùng `CommandRunner`.
- Nếu có `path`:
    - Đuôi `.py`: `PythonRunner`.
    - Đuôi `.bat`, `.cmd`, `.vbs`, `.sh`: `ShellRunner`.
    - Các đuôi khác: Mặc định `PythonRunner`.
