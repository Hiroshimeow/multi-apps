# Multi-Run Apps

`multi-run-apps` là control panel và process registry để chạy nhiều ứng dụng cục bộ từ System Tray trên Windows hoặc từ CLI/TUI trên Linux.

Launcher không quyết định vòng đời của ứng dụng chỉ vì cửa sổ launcher đang mở. Mỗi lần Start trong chế độ `subprocess` tạo một supervisor riêng cho run đó. Supervisor giữ process tree, IPC và log handles; launcher có thể Restart, Exit hoặc bị đóng mà ứng dụng vẫn tiếp tục chạy.

<img width="664" height="830" alt="{A2498988-CF13-4303-ACDB-C3D11863A281}" src="https://github.com/user-attachments/assets/4e8bd747-3387-4352-a6b5-25cb641d03ce" />


## Hành vi vòng đời

### Restart, Exit và launcher crash

- **Restart Launcher** chỉ thay launcher hiện tại bằng launcher mới.
- **Exit Launcher** chỉ thoát launcher.
- Launcher bị crash hoặc bị kết thúc từ Task Manager không tự dừng ứng dụng.
- Không thao tác nào ở trên gửi Stop tới các supervisor đang chạy.

Khi launcher mở lại, nó đọc runtime registry và reconnect tới các supervisor còn hợp lệ. Một run được khôi phục giữ nguyên:

- `run_id`;
- PID và creation-time identity của supervisor;
- process tree của ứng dụng;
- thời điểm bắt đầu dùng để tính uptime;
- đường dẫn stdout/stderr log.

Tray hiển thị trạng thái đã khôi phục ngay khi khởi động, không cần bấm Refresh Menu.

### Stop và Stop All Apps

- **Stop** là thao tác chủ động cho một app.
- **Stop All Apps** là thao tác chủ động cho toàn bộ app enabled trong config hiện tại.
- Stop thử graceful shutdown trước, chờ tối đa `close_timeout`, sau đó force-close complete process tree nếu cần.
- Supervisor xác minh PID cùng process creation time; không kill process chỉ dựa trên PID.
- Log cuối cùng vẫn được giữ lại sau khi run dừng.

### Trạng thái Orphaned

Một run có thể được đánh dấu **Orphaned** khi launcher không xác minh được supervisor nhưng vẫn có dấu hiệu process gốc còn sống hoặc identity không chắc chắn.

Ở trạng thái này launcher xử lý bảo thủ:

- không tự kill process không được xác minh;
- không tự tạo một run `auto_start` trùng;
- không cho non-`multi_run` mở thêm run;
- hiển thị rõ trạng thái để người dùng kiểm tra thủ công.

## Thứ tự startup và auto-start

Launcher khởi động theo thứ tự:

```text
acquire single-instance lock
-> load config
-> initialize registry-backed session manager
-> reconcile existing supervisors
-> build recovered UI
-> evaluate auto_start
```

`auto_start: true` chỉ tạo run khi app đang thực sự `Stopped` sau reconciliation. Một run đã khôi phục ở `Starting`, `Running`, `Stopping` hoặc `Orphaned` sẽ chặn duplicate auto-start, kể cả khi `multi_run: true`.

`multi_run` chỉ cho phép người dùng tạo thêm run bằng thao tác Start chủ động; nó không cho phép launcher tạo duplicate trong startup pass.

## Cấu hình

`setting.yaml.sample` trong repository khai báo **tường minh toàn bộ field được hỗ trợ** cho từng app, kể cả khi giá trị bằng default. `setting.yaml` là cấu hình riêng của từng máy và bị Git bỏ qua. Khi cài mới, copy sample thành `setting.yaml`; khi thêm app, copy một block hiện có và giữ đủ các field.

Mỗi app bắt buộc có `name` và `command`. `path` là working directory, tương đương việc `cd` vào thư mục trước khi chạy command.

Không có field `params`. Launcher không đọc `params`; mọi flag, option hoặc positional argument phải đặt trong `args`. Nên dùng dạng list. Mỗi phần tử được nối **nguyên văn** vào cuối `command` bằng một dấu cách. App không có tham số vẫn nên ghi rõ `args: []`.

```yaml
global:
  log_dir: "./logs"
  session: "subprocess"
  multi_run: false

apps:
  - id: "auto-suggest"
    name: "Auto Suggest"
    path: "E:/python_project/auto-suggest/"
    command: "uv run main.py"
    args:
      - "--repo E:/python_project"
      - "--port 8000"
    enabled: true
    auto_start: true
    multi_run: false
    args_edit: false
    close_timeout: 5.0
    os: "win11"
```

Lệnh thực tế:

```text
cd E:/python_project/auto-suggest/
uv run main.py --repo E:/python_project --port 8000
```

### Các trường app

| Trường | Mặc định | Ý nghĩa |
|---|---:|---|
| `id` | slug tạo từ `name` | Stable app ID dùng cho registry và thư mục log. Nên khai báo rõ và phải duy nhất. |
| `name` | bắt buộc | Tên hiển thị và tên dùng bởi CLI. |
| `path` | thư mục chứa file config | Working directory của command. |
| `command` | bắt buộc | Câu lệnh chạy qua shell hệ thống. |
| `args` | `[]` | Chuỗi hoặc danh sách fragment nối nguyên văn vào command. Dùng field này cho flag/option/positional argument; không có field `params`. |
| `enabled` | `true` | Cho phép app xuất hiện và được điều khiển. |
| `auto_start` | `false` | Tự Start một lần sau reconciliation nếu không có run active/orphaned. |
| `multi_run` | giá trị global | Cho phép các thao tác Start chủ động tạo nhiều run. |
| `args_edit` | `false` | Mở argument editor chỉ khi Start thủ công từ tray GUI. |
| `close_timeout` | `5.0` giây | Thời gian graceful Stop trước khi force-close complete tree. Phải lớn hơn 0. |
| `os` | mọi OS hỗ trợ | Lọc theo `win`, `windows`, `win10`, `win11`, `linux`, `ubuntu` hoặc `debian`. |

### Các trường global

| Trường | Giá trị khuyến nghị | Ý nghĩa |
|---|---:|---|
| `log_dir` | `"./logs"` | Thư mục log theo từng run; path tương đối tính từ thư mục chứa file YAML. |
| `session` | `"subprocess"` | Backend chạy app. Dùng `"subprocess"` trên Windows/Linux; `"tmux"` chỉ có hiệu lực trên Linux. |
| `multi_run` | `false` | Giá trị fallback cho app cũ không khai báo `multi_run`; file mẫu hiện khai báo lại field này ở từng app. |

Các path tương đối được resolve từ thư mục chứa file config.

### Cách viết `args`

```yaml
# Không có tham số
args: []

# Có nhiều flag/option
args:
  - "--port 8000"
  - "--host 127.0.0.1"
  - "input.txt"
```

Các phần tử không bị parse thành token lần nữa. Quote, pipe và shell operator được giữ nguyên, ví dụ `"--title \"Two words\""` hoặc `"&& echo done"`. Vì vậy cần viết đúng cú pháp mà shell đích sẽ nhận.

## Chỉnh args cho một run

Khi `args_edit: true`, nút **Start** trong tray GUI mở argument editor:

- editor được prefill từ YAML `args`;
- preview hiển thị final command dùng cùng formatter với runtime;
- dấu quote, pipe và shell operator được giữ nguyên, không bị parse lại;
- Enter chấp nhận; Cancel không tạo run record;
- args đã sửa chỉ áp dụng cho run mới đó;
- file YAML và config đã normalize không bị sửa.

`args_edit` không xuất hiện trong auto-start, CLI hoặc TUI. Các đường chạy đó dùng trực tiếp YAML `args`.

Với `multi_run: false`, launcher kiểm tra trạng thái trước khi mở editor. App đang `Starting`, `Running`, `Stopping` hoặc `Orphaned` sẽ không mở dialog. Với `multi_run: true`, mỗi Start thủ công có thể tạo run riêng với args và log riêng.

## Command và shell

Viết command như khi dùng terminal:

```yaml
command: "conda activate ana11 && python main.py"
```

Cách ít phụ thuộc shell hơn:

```yaml
command: "conda run -n ana11 python main.py"
```

Có thể nối nhiều lệnh:

```yaml
command: "python prepare.py && uv run main.py"
```

Trên Windows, command chạy qua `cmd.exe`. Trên Linux, command chạy qua shell do Python cung cấp.

## Phạm vi tương thích

Launcher phù hợp với phần lớn ứng dụng có thể khởi động bằng command shell: CLI, local server, Python/Node/UV/Conda command và executable GUI thông thường. Không thể cam kết cho mọi app: ứng dụng UWP/Store, service cần quyền đặc biệt, chương trình tự tách khỏi process tree, ứng dụng bắt buộc terminal tương tác hoặc launcher chống cheat có thể cần cấu hình và kiểm tra riêng.

## Runtime registry và log

Runtime records nằm trong `.runtime/` và được ghi atomic. Chúng lưu `run_id`, supervisor/root identity, state, command, args, timestamps và log paths để launcher mới reconnect an toàn.

Mỗi run có hai file log riêng:

```text
logs/<app_id>/<run_id>.out.log
logs/<app_id>/<run_id>.err.log
```

Supervisor giữ log handles, vì vậy log tiếp tục được ghi khi launcher Restart, Exit hoặc crash. Tray dùng run active mới nhất làm log target; khi không còn run active, nó dùng historical run mới nhất. Hover trên **O.Logs** hoặc **E.Logs** hiển thị tail đang cập nhật của cùng file.

## Cài đặt và chạy

Tạo cấu hình local một lần:

```powershell
Copy-Item setting.yaml.sample setting.yaml
```

Linux/macOS shell:

```bash
cp setting.yaml.sample setting.yaml
```

```bash
uv sync
```

Windows tray:

```powershell
uv run multi.py
```

Hoặc chạy `multi_run.vbs` để mở launcher ẩn.

Linux/CLI:

```bash
./run.sh
uv run python cli.py list
uv run python cli.py start "Tên ứng dụng"
uv run python cli.py stop "Tên ứng dụng"
```

Launcher dùng `.runtime/launcher.lock` để ngăn hai launcher hoạt động đồng thời.

## Plugin cục bộ

`tools/` bị Git của `multi-run-apps` bỏ qua. Mỗi plugin trong đó có thể dùng repository Git riêng.
