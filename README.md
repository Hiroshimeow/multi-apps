# 🚀 Multi-Run Apps Manager

Một công cụ quản lý và khởi chạy đa ứng dụng (Python, Gunicorn, Shell, Custom Commands) hỗ trợ cả Linux (CLI + tmux) và Windows (GUI).

## ✨ Tính năng chính

*   **Đa nền tảng**: 
    *   **Linux**: CLI tương tác (Interactive Menu), quản lý session bằng `tmux`.
    *   **Windows**: GUI System Tray (PyQt6).
*   **Hỗ trợ nhiều loại App**:
    *   Script Python (tự động activate Conda/Venv).
    *   Gunicorn Server (Flask/Django/FastAPI).
    *   Shell scripts (`.sh`, `.bat`, `.cmd`, `.vbs` trên Windows).
    *   Custom commands (Lệnh trực tiếp trong PATH, Node.js, binary, v.v.).
*   **Quản lý tập trung**: Cấu hình tất cả trong một file `setting.yaml`.
*   **Logging**: Tự động redirect output ra file log riêng biệt, dễ dàng xem log nhanh từ System Tray.
*   **An toàn**: Kiểm tra trạng thái running, tránh chạy trùng lặp, graceful shutdown.

## 🛠️ Yêu cầu hệ thống

*   **Python**: 3.8 trở lên
*   **Linux**: `tmux` (để quản lý session chạy ngầm)
*   **Thư viện Python**:
    ```bash
    pip install pyyaml psutil rich
    ```
    *   `pyyaml`: Đọc file cấu hình.
    *   `psutil`: Quản lý process.
    *   `rich`: Giao diện dòng lệnh đẹp mắt.

## 🚀 Hướng dẫn sử dụng (Linux)

### 1. Cấu hình (`setting.yaml`)

Chỉnh sửa file `setting.yaml` để định nghĩa các ứng dụng của bạn:

```yaml
global:
  default_conda_env: "ana11"  # Môi trường Conda mặc định
  log:
    dir: "./logs"             # Nơi lưu log

apps:
  # 1. Python Script truyền thống (Sử dụng Conda/Venv)
  - name: "My Python Tool"
    type: "python"
    path: "E:/projects/tool.py"
    env: { type: "conda", name: "ana11" }
    enabled: true

  # 2. Sử dụng UV (Hiện đại, cực nhanh)
  - name: "UV App"
    type: "uv"
    path: "main.py"                 # File script
    workdir: "E:/projects/my-uv"   # Thư mục chứa pyproject.toml
    args: ["--port", "8000"]
    enabled: true

  # 3. Chạy lệnh trực tiếp (Command)
  - name: "OpenClaw Gateway"
    command: "openclaw gateway run"
    enabled: true

  # 4. Windows Scripts (.bat, .vbs)
  - name: "Backup Task"
    path: "C:/scripts/backup.bat"   # Tự động chạy qua cmd /c
    enabled: true

  - name: "Popup Notifier"
    path: "C:/scripts/alert.vbs"    # Tự động chạy qua cscript //nologo
    enabled: true
```

### 2. Chạy Interactive Menu

Cách dễ nhất để sử dụng là chạy script `run.sh` không tham số:

```bash
./run.sh
```

Giao diện menu sẽ hiện ra:
*   **Phím Lên/Xuống**: Di chuyển để chọn ứng dụng.
*   **Phím Space**: Chọn/Bỏ chọn ứng dụng (Toggle `[x]`).
*   **Phím S**: Start các app đã chọn.
*   **Phím T**: Stop các app đã chọn.
*   **Phím R**: Restart các app đã chọn.
*   **Phím A**: Start tất cả (All).
*   **Phím X**: Stop tất cả.
*   **Phím Q**: Thoát.

### 3. Chạy lệnh trực tiếp (CLI)

Bạn có thể dùng lệnh tắt để tích hợp vào script khác:

```bash
# Start một app cụ thể
./run.sh start "Amen API"

# Start tất cả app đang enabled
./run.sh start --all

# Stop app
./run.sh stop "Amen API"

# Restart app
./run.sh restart "Amen API"

# Xem trạng thái
./run.sh status

# Liệt kê danh sách app
./run.sh list
```

### 4. Xem Logs

Log được lưu tự động trong thư mục `logs/` với format `tên-app_ngày.log`.

```bash
# Xem log file mới nhất của app
cat logs/amen-api_2026-01-29.log

# Theo dõi log realtime (tail -f)
tail -f logs/amen-api_*.log
```

### 5. Debugging (tmux)

Mỗi app chạy trong một `tmux session` riêng biệt (định dạng `app-<tên-slug>`).

```bash
# Liệt kê các session đang chạy
tmux list-sessions

# Attach vào session để debug trực tiếp
tmux attach -t app-amen-api

# Detach khỏi session (quay lại terminal chính)
# Nhấn tổ hợp phím: Ctrl+B, sau đó nhấn D
```

## 🖥️ Hướng dẫn sử dụng (Windows)

1.  Cài đặt thư viện: `pip install pyyaml psutil PyQt6`.
2.  Chạy file `multi.py` hoặc `multi_run.vbs` (để chạy ẩn).
3.  Icon sẽ hiện dưới System Tray. Chuột phải để bật menu quản lý.
4.  **Tính năng mới**:
    *   **Nút Logs**: Mở nhanh thư mục chứa log của từng ứng dụng.
    *   **Refresh Menu**: Cập nhật lại danh sách ứng dụng khi thay đổi `setting.yaml` mà không cần khởi động lại launcher.
    *   **Auto-detect**: Tự động nhận diện loại ứng dụng (Python/Shell/Command) dựa trên đuôi file hoặc câu lệnh.
    *   **Smart Start**: Tự động vô hiệu hóa nút Start nếu ứng dụng đang chạy (trừ khi bật `multi_run: true`).

## 📂 Cấu trúc Project

```
multi-run-apps/
├── run.sh                  # Entry point (Linux)
├── cli.py                  # Giao diện dòng lệnh chính
├── setting.yaml            # File cấu hình
├── lib/                    # Thư viện lõi
│   ├── config.py           # Xử lý cấu hình
│   ├── core.py             # Điều khiển luồng chính
│   ├── runners/            # Các loại runner (python, gunicorn, shell...)
│   └── session/            # Quản lý session (tmux)
└── logs/                   # Thư mục chứa log
```

## ❓ Troubleshooting

*   **Lỗi `tmux: command not found`**: Cài đặt tmux (`sudo apt install tmux`).
*   **Lỗi `ModuleNotFoundError: No module named 'rich'`**: Cài đặt rich (`pip install rich`).
*   **App báo RUNNING nhưng không thấy process**:
    *   Check log trong `logs/`.
    *   Attach vào tmux session để xem lỗi hiển thị trên màn hình console ảo.
*   **Gunicorn không stop hẳn**: Script đã được tối ưu để kill cả process con, nhưng nếu vẫn bị kẹt, hãy chạy `./run.sh stop "App Name"` một lần nữa hoặc `pkill -f gunicorn`.
