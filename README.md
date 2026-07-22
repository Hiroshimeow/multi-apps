# Multi-Run Apps

`multi-run-apps` là control panel và process registry để chạy nhiều ứng dụng cục bộ từ System Tray trên Windows hoặc từ CLI/TUI trên Linux.
<img width="648" height="520" alt="{726DBEEE-F798-435E-B871-A29D4FB4F918}" src="https://github.com/user-attachments/assets/ae9943b5-945a-4caf-9155-ad5fce6b9c9b" />

Launcher không quyết định vòng đời của ứng dụng chỉ vì cửa sổ launcher đang mở. Mỗi lần Start trong chế độ `subprocess` tạo một supervisor riêng cho run đó. Supervisor giữ process tree, IPC và log handles; launcher có thể Restart, Exit hoặc bị đóng mà ứng dụng vẫn tiếp tục chạy.

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

Tray hiển thị trạng thái đã khôi phục ngay khi khởi động, không cần thao tác reload thủ công.

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

## Thao tác trong tray

- nhấp trái tên app để mở working directory của app;
- **Open Config** mở đúng file YAML mà launcher hiện tại đang dùng;
- **Stop All Apps**, **Restart Launcher** và **Exit Launcher** giữ nguyên chức năng tương ứng.

Tên app không có menu chuột phải riêng. Cấu hình dùng chung được mở từ menu tray thay vì khai báo lặp lại theo từng app.

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

Supervisor giữ log handles, vì vậy log tiếp tục được ghi khi launcher Restart, Exit hoặc crash. Tray dùng run active mới nhất làm log target; khi không còn run active, nó dùng historical run mới nhất.

## Inline live logs

Hover trên **O.Logs** hoặc **E.Logs** mở một panel log cố định phía trên danh sách app; tại một thời điểm chỉ có một panel. Panel đổi sang app và stream đang hover, còn chiều cao từng app row không đổi. Menu mở rộng lên trên và giữ các action trong vùng làm việc của màn hình. Nhấp nút vẫn mở file log như trước. Panel cập nhật theo chu kỳ 500 ms và có các điều khiển:

- stream `stdout` hoặc `stderr` theo nút đang hover;
- **Lines** mặc định **100 dòng**, cho phép **10–5000**;
- **Filter** dùng các term literal, không phải regex và không render HTML.

Filter có thể viết `alpha,beta,!drop-me` hoặc `[alpha,beta,!drop-me]`. Dòng được giữ khi chứa ít nhất một positive term; term có tiền tố `!` loại dòng. Positive term được highlight trên nguyên văn log. Dấu phẩy bên trong term không được hỗ trợ.

Khi viewport đang ở cuối, log mới tự follow và trạng thái là `Live`. Cuộn lên sẽ giữ vị trí đọc và đổi trạng thái thành `Live paused while scrolled`; cuộn về cuối tự tiếp tục follow. Selection, caret và kết quả copy được giữ qua append tương thích.

Mỗi app lưu riêng line count, filter và stream gần nhất trong `.runtime/ui-log-preferences.json` nằm cạnh file config. File này được ghi atomic. Config thiếu/hỏng dùng default; lỗi đọc I/O không được phép ghi đè preference hợp lệ đang có.

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
