# Multi-Run Apps

Launcher đơn giản để chạy nhiều ứng dụng cục bộ từ System Tray trên Windows hoặc CLI/TUI trên Linux.

## Nguyên tắc cấu hình

Mỗi app chỉ cần hai trường để chạy:

- `path`: thư mục làm việc, tương đương với việc `cd` vào thư mục đó.
- `command`: câu lệnh giống như khi gõ trực tiếp trong terminal.

`args` là các đoạn văn bản được nối vào cuối `command`.

```yaml
apps:
  - name: "Auto Suggest"
    path: "E:/python_project/auto-suggest/"
    command: "uv run main.py"
    args:
      - "--repo E:/python_project"
      # - "--reload 3"
    auto_start: true
    enabled: true
    multi_run: false
    os: win11
```

Lệnh thực tế:

```text
cd E:/python_project/auto-suggest/
uv run main.py --repo E:/python_project
```

Không cần khai báo `type`, `workdir`, `env` hoặc loại runner.

## Dùng Conda hoặc chuỗi lệnh

Viết đúng câu lệnh bạn thường dùng trong terminal Windows:

```yaml
command: "conda activate ana11 && python main.py"
```

Cách ít phụ thuộc vào shell hơn:

```yaml
command: "conda run -n ana11 python main.py"
```

Có thể chạy nhiều lệnh liên tiếp:

```yaml
command: "python prepare.py && uv run main.py"
```

Trên Windows, command được chạy qua shell hệ thống (`cmd.exe`). Trên Linux, command được chạy qua shell mặc định của Python.

## Các trường hỗ trợ

```yaml
- name: "Tên hiển thị"       # Bắt buộc
  path: "E:/project/"        # Tùy chọn; mặc định là thư mục launcher
  command: "uv run main.py"  # Bắt buộc
  args:                       # Tùy chọn; nối nguyên văn vào command
    - "--port 8000"
  enabled: true               # Hiển thị và cho phép chạy
  auto_start: false           # Tự chạy khi mở launcher
  multi_run: false            # Cho phép chạy nhiều instance
  os: win11                   # win, windows, win10, win11, linux, ubuntu, debian
```

Cấu hình toàn cục:

```yaml
global:
  log_dir: "./logs"
  session: "subprocess"  # hoặc "tmux" trên Linux
  multi_run: false
```

## Cài đặt và chạy

```bash
uv sync
```

Windows:

```powershell
uv run multi.py
```

Hoặc chạy `multi_run.vbs` để mở ẩn.

Linux/CLI:

```bash
./run.sh
uv run python cli.py list
uv run python cli.py start "Tên ứng dụng"
uv run python cli.py stop "Tên ứng dụng"
```

## Log

Mỗi app ghi ra hai file trong `logs/`:

```text
<Tên app>.out.log
<Tên app>.err.log
```

## Plugin cục bộ

`tools/` bị Git của `multi-run-apps` bỏ qua. Mỗi plugin trong đó có thể dùng repository Git riêng.
