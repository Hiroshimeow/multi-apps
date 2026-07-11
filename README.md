# Multi-Run Apps

Trình khởi chạy tập trung cho các ứng dụng và công cụ cục bộ. Dự án cung cấp System Tray trên Windows, CLI/TUI trên Linux, quản lý process, thư mục làm việc và log từ một file `setting.yaml`.

## Kiến trúc repository

Repository này chỉ quản lý launcher, cấu hình và mã điều phối.

- `tools/` chứa các plugin/app cục bộ và bị loại khỏi Git của `multi-run-apps`.
- Mỗi plugin trong `tools/` có thể là một Git repository độc lập.
- Thay đổi source của plugin chỉ cần commit trong repository của plugin; không cần commit lại `multi-run-apps`.
- `logs/`, cache Python, file nén và artifact phân tích đều không được đưa vào Git.

## Yêu cầu

- Python 3.13 trở lên
- [uv](https://docs.astral.sh/uv/)
- Windows: System Tray dùng PyQt6
- Linux: cần `tmux` cho session chạy nền

## Cài đặt

```bash
uv sync
```

## Chạy

### Windows

```powershell
uv run multi.py
```

Có thể chạy `multi_run.vbs` để mở launcher ẩn, không giữ cửa sổ terminal.

### Linux CLI/TUI

```bash
./run.sh
```

Hoặc chạy lệnh trực tiếp:

```bash
uv run python cli.py list
uv run python cli.py status
uv run python cli.py start "Tên ứng dụng"
uv run python cli.py stop "Tên ứng dụng"
uv run python cli.py restart "Tên ứng dụng"
```

Dùng `--all` để áp dụng cho toàn bộ app đang bật.

## Cấu hình ứng dụng

Các app được khai báo trong `setting.yaml`.

```yaml
global:
  log:
    dir: "./logs"
  session:
    type: "subprocess"

apps:
  - name: "Local plugin"
    type: "uv"
    path: "./tools/local-plugin/main.py"
    enabled: true

  - name: "External project"
    type: "uv"
    command: "uv run --directory E:/projects/example main.py"
    enabled: true

  - name: "Custom command"
    type: "command"
    command: "some-command --serve"
    enabled: true
```

Các trường thường dùng:

- `name`: tên hiển thị và định danh app.
- `type`: `python`, `uv`, `gunicorn`, `shell`, `cmd` hoặc `command`.
- `path`: script hoặc executable cần chạy.
- `command`: câu lệnh đầy đủ, dùng thay cho `path`.
- `workdir`: thư mục làm việc; launcher tự suy luận khi có thể.
- `env`: Conda hoặc virtual environment riêng của app.
- `args`, `env_vars`: tham số và biến môi trường.
- `enabled`, `auto_start`, `multi_run`, `os`: điều khiển khả dụng và cách chạy.

## Log và điều khiển

Log mặc định nằm trong `logs/` với hai stream:

```text
<App name>.out.log
<App name>.err.log
```

Trên Windows, menu System Tray cho phép start/stop app, mở thư mục làm việc, mở log và xem nhanh các dòng log cuối khi rê chuột lên nút log.

## Cấu trúc chính

```text
multi-run-apps/
├── multi.py              # Windows System Tray
├── cli.py                # CLI/TUI entry point
├── setting.yaml          # Danh sách app cục bộ
├── lib/                  # Controller, runner và session manager
├── tools/                # Plugin cục bộ, không thuộc Git repo này
├── run.sh                # Linux launcher
├── run.bat               # Windows console launcher
└── multi_run.vbs         # Windows hidden launcher
```
