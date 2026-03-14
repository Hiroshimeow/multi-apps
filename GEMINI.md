# 

# Thiết kế & Hướng dẫn: Trình khởi chạy đa ứng dụng (Multi-App Launcher)

Tài liệu này tổng hợp lại quá trình phân tích, thiết kế và triển khai một công cụ khởi chạy nhiều ứng dụng Python một cách linh hoạt, mạnh mẽ và dễ bảo trì.

## 1. Mục tiêu

Xây dựng một công cụ cho phép khởi chạy nhiều ứng dụng Python (có thể nằm trong các môi trường ảo khác nhau) chỉ bằng một lệnh duy nhất. Việc ứng dụng nào được chạy sẽ được điều khiển thông qua một file cấu hình tập trung.

## 2. Phân tích & Thiết kế (Góc nhìn Senior Dev)

Đây là quá trình cải tiến từ ý tưởng ban đầu đến một thiết kế chuyên nghiệp và vững chắc.

### 2.1. File cấu hình `setting.yaml`

Việc tách cấu hình ra khỏi logic là một quyết định đúng đắn. Tuy nhiên, chúng ta cần một cấu trúc YAML có tổ chức để dễ đọc, dễ mở rộng và phân tích an toàn.

**Cấu trúc được đề xuất:**

Sử dụng một danh sách các "đối tượng" (object), mỗi đối tượng đại diện cho một ứng dụng với các thuộc tính rõ ràng.

```yaml
# setting.yaml
default_conda_env: "ana11"

apps:
  - name: "Tên ứng dụng 1"
    path: "Đường/dẫn/đến/app1.py"
    env: { ... }
    args: [ ... ]
    enabled: true
```

**Lợi ích:**
- **Tự diễn giải:** Các khóa `name`, `path`, `enabled` làm cho file tự nó giải thích ý nghĩa.
- **Dễ mở rộng:** Dễ dàng thêm các thuộc tính mới trong tương lai (ví dụ: `env_vars`, `working_dir`) mà không phá vỡ cấu trúc.
- **Phân tích an toàn:** Các thư viện YAML chuẩn xử lý cấu trúc này một cách đáng tin cậy.

### 2.2. Xử lý Tham số dòng lệnh (`args`)

Để truyền tham số cho các ứng dụng con một cách an toàn và không gặp lỗi với khoảng trắng hay ký tự đặc biệt, phương pháp tốt nhất là dùng một **danh sách (list)**.

- **Không nên (dễ lỗi):** `args: "--input 'data file.csv' --mode=fast"`
- **Nên (ổn định và rõ ràng):**
  ```yaml
  args:
    - "--input"
    - "data file.csv"
    - "--mode"
    - "fast"
  ```
Cách này ánh xạ trực tiếp đến cách `subprocess` của Python hoạt động và là tiêu chuẩn trong ngành.

### 2.3. Xử lý Môi trường ảo (`env`)

Đây là phần phức tạp nhưng quan trọng nhất. Việc cố gắng chạy các script kích hoạt môi trường (`activate.bat`, `.sh`) là không đáng tin cậy.

Giải pháp chuyên nghiệp là **khai báo** loại môi trường và để `multi.py` tự tìm đến file `python` thực thi.

- **Cấu trúc `env` được đề xuất:**
  ```yaml
  # Dùng môi trường Conda
  env:
    type: "conda"
    name: "ana11"
  
  # Dùng môi trường venv của Python
  env:
    type: "venv"
    path: "E:/project/my_app/venv"

  # Dùng môi trường của chính trình khởi chạy
  env: null 
  # hoặc
  env:
    type: "system"
  ```
**Lợi ích:**
- **Độc lập nền tảng:** Không cần lo về `bat` hay `sh`. `multi.py` sẽ tự xử lý logic cho Windows, Linux, macOS.
- **Rõ ràng:** File cấu hình chỉ định "cái gì" cần, không phải "cách làm".
- **Ổn định:** Trực tiếp gọi file `python` thực thi là phương pháp mạnh mẽ và đáng tin cậy nhất.

## 3. Triển khai

Dưới đây là nội dung hoàn chỉnh cho các file của project.

### File cấu hình: `setting.yaml` (Mẫu)

```yaml
# E:/python_project/multi-run-apps/setting.yaml

# Cấu hình môi trường mặc định nếu một app không chỉ định môi trường riêng.
# Để null nếu muốn dùng môi trường của chính multi.py.
default_conda_env: "ana11"

# Danh sách các ứng dụng để khởi chạy
apps:
  - name: "Screenshot Tool"
    path: "E:/python_project/app_screenshot.py"
    # Dùng chung môi trường mặc định (ana11) được khai báo ở trên.
    env: null
    # Không có tham số dòng lệnh.
    args: null
    enabled: true

  - name: "Auto Suggest"
    path: "E:/python_project/app_autosuggest.py"
    # Dùng môi trường venv riêng của nó.
    env:
      type: "venv"
      path: "E:/python_project/auto-suggest/venv"
    # Ví dụ về tham số dòng lệnh.
    args:
      - "--port"
      - "8080"
    enabled: true

  - name: "Data Processor"
    path: "E:/python_project/data_processor/main.py"
    # Ứng dụng này sẽ không được chạy.
    enabled: false
```

### File thực thi: `multi.py`

```python
# E:/python_project/multi-run-apps/multi.py

import yaml
import subprocess
import sys
import os
import time
import platform

def find_python_interpreter(env_config, default_conda_env):
    """
    Tìm đường dẫn đến file python thực thi dựa trên cấu hình env.
    """
    # 1. Nếu app không có `env` (null), thì dùng default_conda_env
    if not env_config:
        if not default_conda_env:
            # Nếu không có default, dùng python hiện tại (nhanh nhất)
            return sys.executable
        # Nếu có default, coi như đó là env_config
        env_config = {'type': 'conda', 'name': default_conda_env}

    env_type = env_config.get('type', 'system')

    # 2. Xử lý dựa trên loại môi trường
    try:
        if env_type == "system":
            return sys.executable
        
        elif env_type == "conda":
            env_name = env_config.get('name')
            if not env_name: return sys.executable
            
            conda_base = subprocess.check_output(['conda', 'info', '--base'], shell=True, text=True, encoding='utf-8').strip()
            if platform.system() == "Windows":
                exe_path = os.path.join(conda_base, 'envs', env_name, 'python.exe')
            else:
                exe_path = os.path.join(conda_base, 'envs', env_name, 'bin', 'python')
            
            return exe_path if os.path.exists(exe_path) else None

        elif env_type == "venv":
            venv_path = env_config.get('path')
            if not venv_path: return sys.executable

            if platform.system() == "Windows":
                exe_path = os.path.join(venv_path, 'Scripts', 'python.exe')
            else:
                exe_path = os.path.join(venv_path, 'bin', 'python')
            
            return exe_path if os.path.exists(exe_path) else None

        else:
            print(f"WARNING: Unknown env type '{env_type}'. Using current interpreter.")
            return sys.executable
            
    except Exception as e:
        print(f"ERROR: Could not find python for env {env_config}. Reason: {e}")
        return None


def main():
    try:
        with open('setting.yaml', 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"FATAL: Could not read 'setting.yaml'. Error: {e}")
        sys.exit(1)

    default_env = config.get('default_conda_env')
    processes = []
    
    print("--- LAUNCHER: Starting enabled applications... ---\\n")
    for app_cfg in config.get('apps', []):
        if not app_cfg.get('enabled', False):
            continue

        app_name = app_cfg.get('name', 'Unnamed App')
        app_path = app_cfg.get('path')
        env_cfg = app_cfg.get('env')
        args_list = app_cfg.get('args') or []

        print(f"--- Processing '{app_name}' ---")

        interpreter = find_python_interpreter(env_cfg, default_env)
        
        if not interpreter or not os.path.exists(interpreter):
             print(f"ERROR: Interpreter not found for this configuration. Skipping.")
             print("-" * (len(app_name) + 20) + "\\n")
             continue
        if not os.path.exists(app_path):
            print(f"ERROR: Script path '{app_path}' not found. Skipping.")
            print("-" * (len(app_name) + 20) + "\\n")
            continue
            
        command = [interpreter, app_path] + args_list
        
        print(f"  Interpreter: {interpreter}")
        print(f"  Command: {' '.join(command)}")

        try:
            process = subprocess.Popen(command)
            processes.append((app_name, process))
            print(f"-> SUCCESS: Started with PID: {process.pid}")
        except Exception as e:
            print(f"-> ERROR: Failed to start. Reason: {e}")
        
        print("-" * (len(app_name) + 20) + "\\n")
        time.sleep(1)

    if not processes:
        print("No applications were started. Exiting.")
        sys.exit(0)

    print("--- All applications launched. Launcher is monitoring. ---")
    print("Press Ctrl+C in this terminal to terminate all applications.")

    try:
        for name, process in processes:
            process.wait()
    except KeyboardInterrupt:
        print("\\n--- LAUNCHER: Interruption! Terminating all... ---\\n")
        for name, process in reversed(processes):
            if process.poll() is None:
                print(f"Terminating '{name}' (PID: {process.pid})...")
                process.terminate()
    finally:
        print("\\n--- LAUNCHER: Finished. ---\\n")


if __name__ == '__main__':
    main()

```

## 4. Hướng dẫn sử dụng

1. **Cài đặt thư viện:**
    ```bash
    # Kích hoạt môi trường bạn muốn dùng để chạy multi.py
    conda activate your_base_env 
    pip install pyyaml
    ```
2. **Tạo cấu trúc project:**
Tạo thư mục `E:\python_project\multi-run-apps` và đặt 2 file `setting.yaml` và `multi.py` (với nội dung như trên) vào trong.

3. **Chỉnh sửa `setting.yaml`:**
Mở file `setting.yaml` và thay đổi các đường dẫn `path` cũng như cấu hình `env`, `args` cho đúng với các project thực tế trên máy của bạn.

4. **Chạy trình khởi chạy:**
Mở terminal, di chuyển đến thư mục project và chạy lệnh:
    ```bash
    cd E:\python_project\multi-run-apps
    python multi.py
    ```

Trình khởi chạy sẽ đọc file cấu hình và tự động khởi động các ứng dụng được cho phép.