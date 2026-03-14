---
name: Multi-Run Apps Developer
description: Core philosophy, architectural guidelines, and standard practices for developing tools and applications within the Multi-Run Apps ecosystem.
---

# Multi-Run Apps: Development Philosophy & Guidelines

Bạn đang phát triển và bảo trì các ứng dụng hoặc công cụ (tools) thuộc hệ sinh thái **Multi-Run Apps**. Khi thực hiện bất kỳ thay đổi, cải tiến hoặc thêm mới chức năng nào, bạn TẮT BUỘC phải tuân thủ nghiêm ngặt các triết lý và quy chuẩn kỹ thuật sau đây.

## 1. Triết lý hệ thống (System Philosophy)

*   **Tập trung nhưng Độc lập (Centralized but Independent):** Multi-Run Apps hoạt động như một trình khởi chạy trung tâm (`multi.py`), được cấu hình thông qua một file duy nhất (`setting.yaml`). Tuy nhiên, các ứng dụng/tools con phải hoạt động độc lập, không phụ thuộc cứng vào nhau, có thể cấu hình môi trường chạy (venv, conda, system) riêng biệt.
*   **Hiệu suất cao - Tiêu thụ tài nguyên thấp (High Performance & Low Footprint):** Các tool thường chạy ngầm liên tục (background/tray apps). Do đó, việc tiết kiệm CPU, RAM và tránh gây ảnh hưởng đến hiệu ứng hệ thống (desktop lag) là ưu tiên số một.
*   **Không tạo rác (No Runtime Artifacts in Repo):** Các file sinh ra trong quá trình chạy (database SQLite, log file, thư mục backup, ảnh cache...) phải luôn được cấu hình để nằm ngoài git (thông qua `.gitignore`).

## 2. Quy chuẩn kỹ thuật lõi (Core Technical Standards)

### 2.1. Quản lý Hotkey và Global Event (Đặc biệt quan trọng)

Như đã thực hiện ở `tools/advance-clipboard/win32_monitor.py`, hệ thống không chấp nhận các giải pháp "easy nhưng heavy" khi bắt các sự kiện bàn phím toàn cục. 

**QUY TẮC BẮT BUỘC KHI LÀM GLOBAL HOTKEY / KEYBOARD LISTENER:**
*   **KHÔNG SỬ DỤNG** các thư viện hooking cấp thấp như `pynput` hay `keyboard`. Lý do: `SetWindowsHookEx(WH_KEYBOARD_LL)` bắt mọi phím bấm của người dùng trên toàn hệ thống, gấy tốn CPU, dễ bị phần mềm Antivirus quét nhầm thành Keylogger, và đặc biệt gây lag (input lag) nghiêm trọng khi người dùng chơi game hoặc dùng các phần mềm đồ họa nặng.
*   **PHẢI SỬ DỤNG Win32 API (RegisterHotKey):** 
    *   Sử dụng thủ thuật tạo một **Hidden Message-only Window** (`HWND_MESSAGE`) để nhận message vòng lặp `GetMessageW`.
    *   Đăng ký phím tắt bằng `ctypes.windll.user32.RegisterHotKey`. Cách này hệ điều hành Windows chỉ "đánh thức" ứng dụng của bạn khi đúng tổ hợp phím đó được ấn. Chi phí CPU = 0% trong thời gian chờ.

**QUY TẮC BẮT BUỘC KHI GIẢ LẬP NHẤN PHÍM (Simulate Keystrokes):**
*   **PHẢI SỬ DỤNG Win32 API (keybd_event/SendInput):** Dùng `ctypes.windll.user32.keybd_event` để chèn tín hiệu phím vào hệ thống. Giải pháp này trực tiếp, nhẹ, tốc độ tức thì và tương thích hoàn toàn kể cả trong game hay ứng dụng full-screen (tránh lỗi focus).

### 2.2. Kiến trúc Data & Tích hợp liên tục
*   **Database:** Khuyến khích sử dụng SQLite với chế độ **WAL (Write-Ahead Logging)** cho các luồng đọc/ghi đồng thời nhanh, ổn định. (e.g. `PRAGMA journal_mode=WAL;`).
*   **Backup:** Các dữ liệu quan trọng nên có cơ chế tự động backup (ví dụ: JSON backup chạy ngầm cách nhau 30s với cơ chế debounce - xem `backup_manager.py`).

## 3. Kiến trúc Tool UI (Nếu có giao diện)

*   Sử dụng **PyQt6** là tiêu chuẩn.
*   Giao diện công cụ nên được thiết kế theo hướng "Frameless" (không viền) hoặc dạng Tool Window (`Qt.WindowType.Tool`), tông màu tối (Dark Mode), mượt mà ("Smooth scrolling" hoặc có tiểu xảo animation nhẹ) để mang lại cảm giác premium.
*   Tránh để main window của PyQt bị tắt ngang khi ứng dụng đang thu nhỏ. Cửa sổ giám sát Win32 API (`Win32ClipboardMonitor`) và UI của PyQt phải hoạt động luồng (threads) phối hợp nhịp nhàng (dùng `pyqtSignal` để giao tiếp giữa Background Thread và Main GUI Thread).

## 4. Checklist khi triển khai Code

Khi thực hiện task, hãy luôn hỏi:
- [ ] Tính năng này có làm tăng CPU usage khi rảnh (idle) không?
- [ ] Các tổ hợp phím có được bind qua Win32 `RegisterHotKey` chưa?
- [ ] Có đảm bảo sử dụng đường dẫn tuyệt đối (hoặc tính toán từ gốc script `__file__`) hay chưa? (Quá trình khởi chạy từ file `multi.py` ở root có thể làm thay đổi Current Working Directory (CWD)).
- [ ] File config có tương thích tốt với luồng đọc qua `yaml` trong Multi-Run không?

Việc ghi nhớ và tự động áp dụng các quy chuẩn này đảm bảo toàn bộ hệ sinh thái của anh vững chắc, có độ tin cậy và đạt hiệu năng của một ứng dụng pro cấp hệ thống (system-level application).
