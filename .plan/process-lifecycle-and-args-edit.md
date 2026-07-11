# Feature Plan: Safe Process Lifecycle, Remembered Close Policy, and Editable Args

## 1. Repository and branch contract

- Repository: `E:/python_project/multi-run-apps`
- Stable branch: `main`
- Integration branch: `develop`
- Working branch: `feat/process-lifecycle`
- Stable baseline commit: `6c40273 fix: launch advance clipboard with uv`
- Do not rewrite or force-update `main` or `develop` while implementing this plan.
- Implement only on `feat/process-lifecycle`.
- The working tree currently contains user-owned edits in `setting.yaml`. Preserve them exactly. Do not reset, overwrite, reformat, or include them in feature commits unless the user explicitly requests it.
- `tools/advance-clipboard` is a separate ignored Git repository. Do not modify or commit it as part of this feature.

Advance Clipboard fixes already completed separately:

- Plugin branch: `fix/atomic-clip-dedupe`
- Plugin commit: `596276c fix: make clipboard dedupe atomic`
- Launcher command fix: included in stable baseline `6c40273`

## 2. Read before coding

Read these files first:

1. `setting.yaml`
2. `lib/config.py`
3. `lib/core.py`
4. `lib/runners/command_runner.py`
5. `lib/session/subprocess_session.py`
6. `lib/utils.py`
7. `multi.py`
8. `tests/test_simple_launcher.py`
9. `README.md`

Preserve the current configuration philosophy:

- `path` is the working directory.
- `command` is the command typed in the terminal.
- `args` is appended verbatim.
- Do not reintroduce runner types, environment abstractions, or command inference.

## 3. User-visible requirements

### 3.1 Safe process cleanup

When a managed app is stopped, the launcher must:

1. request graceful termination;
2. wait for a configurable timeout;
3. force-close the complete process tree if anything remains.

When the launcher crashes or is force-killed, managed child processes must not remain orphaned unless the user explicitly selected **Keep running** during an intentional restart or exit.

Starting the launcher twice must never cause `auto_start` apps to run twice.

### 3.2 `close_ask`

New YAML option:

```yaml
close_ask: false
```

Rules:

- Default is `false`.
- `false`: the app is closed automatically on launcher Restart or Exit.
- `true`: if the app is running, show it in one consolidated confirmation dialog before Restart or Exit.
- Do not show one popup per app.
- Dialog close/X and Cancel must abort the entire Restart/Exit operation before any app is changed.
- Each listed app has a choice: `Close` or `Keep running`.
- Include a `Remember choices` checkbox.
- Remember Restart choices separately from Exit choices.
- Remembered choices are runtime preferences, not edits to `setting.yaml`.
- Provide `Close all`, `Keep all`, and `Restore defaults` controls.

### 3.3 `args_edit`

New YAML option:

```yaml
args_edit: false
```

Rules:

- Default is `false`.
- On a manual GUI Start with `args_edit: true`, show a small arguments dialog before launching.
- Prefill the dialog with YAML `args`.
- Show the exact final command preview.
- Preserve user text verbatim. Do not parse and reconstruct quoting with `shlex` on Windows.
- Enter runs the app. Shift+Enter inserts a newline if a multiline editor is used.
- Cancel performs no launch and creates no runtime record.
- If `multi_run: false` and the app is already running, do not show the arguments dialog.
- Auto-start never shows the dialog; it uses YAML `args` directly.
- CLI/TUI never opens a Qt dialog; it uses YAML `args` directly.
- Popup edits apply only to that run and are not written back to YAML.

Example entries:

```yaml
- id: "tampermonkey-main"
  name: "Tampermonkey Main"
  path: "E:/python_project/tampermonkey_auto/"
  command: "uv run python main.py"
  args_edit: true
  args:
    - "--role DEV,REVIEW,PLAN"
    - '--goal "Implement the requested change."'
  multi_run: true

- id: "tampermonkey-role"
  name: "Tampermonkey Role"
  path: "E:/python_project/tampermonkey_auto/"
  command: "uv run python role.py"
  args_edit: true
  args:
    - "--role DEV"
    - "--resp-from REVIEW"
    - '--prompt "Continue from the latest review."'
  multi_run: true
```

Do not infer `main.py` versus `role.py` from arguments. They must remain separate app entries.

## 4. Identity and runtime model

Use three identifiers:

- `app_id`: stable per YAML entry.
- `run_id`: unique for every Start.
- `launcher_id`: unique for every launcher instance.

### 4.1 Config identity

Add optional YAML field:

```yaml
id: "advance-clipboard"
```

Rules:

- If absent, generate a slug from `name`.
- Reject or skip duplicate IDs with a clear warning.
- Keep `name` as display text only.
- Runtime preferences and run ownership are keyed by `app_id`, not display name.

Normalize these new fields in `lib/config.py`:

```text
id
close_ask=false
args_edit=false
close_timeout=5.0
```

Do not change the meaning of existing fields.

### 4.2 Runtime files

Use a local ignored directory:

```text
.runtime/
  launcher.lock
  preferences.json
  runs/
    <run_id>.json
```

Add `.runtime/` to `.gitignore`.

Use atomic JSON writes:

1. write `<file>.tmp`;
2. flush and `os.fsync` where practical;
3. replace with `os.replace`.

Do not use the Advance Clipboard SQLite database. A separate runtime SQLite database is not required for the first implementation; atomic per-run JSON files are sufficient and easier to inspect.

Minimum run record:

```json
{
  "app_id": "advance-clipboard",
  "run_id": "...",
  "launcher_id": "...",
  "keeper_pid": 0,
  "root_pid": 0,
  "root_created_at": 0.0,
  "path": "...",
  "command": "...",
  "args": ["..."],
  "stdout_path": "...",
  "stderr_path": "...",
  "state": "starting|running|keep_alive|stopping|stopped|orphaned|failed",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

Never trust PID alone. When reconnecting, validate at least PID plus process creation time. If identity cannot be verified, mark the run `orphaned` and do not force-kill it automatically.

## 5. Process ownership architecture

Implement process ownership through a small keeper process rather than making `multi.py` directly own only a `cmd.exe` PID.

Suggested new modules:

```text
lib/runtime/models.py
lib/runtime/registry.py
lib/runtime/preferences.py
lib/runtime/ipc.py
lib/runtime/process_keeper.py
lib/runtime/process_client.py
lib/runtime/single_instance.py
```

Keep UI code out of these modules.

### 5.1 Keeper lifecycle

For every launched run:

1. Launcher creates `run_id`, log paths, and a `starting` runtime record.
2. Launcher starts `process_keeper.py` with app/run/launcher metadata.
3. Keeper creates the managed OS process container.
4. Keeper starts the configured terminal command inside `path`.
5. Keeper writes verified PIDs and updates state to `running`.
6. Launcher talks to keeper for status, stop, keep, and claim operations.

### 5.2 Windows behavior

On Windows:

- Keeper creates a Windows Job Object.
- Set `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
- Keeper assigns itself to the Job Object before spawning the command so descendants inherit membership.
- Stopping a run first requests graceful termination, waits `close_timeout`, then terminates the Job Object.
- Do not rely only on `taskkill` or the shell PID.

Use `ctypes`; do not add a large Windows dependency solely for Job Objects.

### 5.3 Linux behavior

On Linux:

- Start the command in a new process session/process group.
- Gracefully terminate the process group, wait, then send SIGKILL.
- Keep tmux compatibility isolated; do not break existing `session: tmux` behavior without tests.

### 5.4 Detecting launcher death

Keeper must monitor the owning launcher process using PID plus creation time.

Default managed behavior:

- Unexpected launcher death: stop the complete managed app tree and exit.

Intentional keep behavior:

- Before launcher Restart/Exit, launcher sends `keep` to selected keepers.
- Keeper changes state to `keep_alive` before the launcher exits.
- On Restart, the new launcher discovers and `claim`s those keepers.
- Claim updates the owner launcher ID/PID/creation time and returns the run to managed mode.
- On intentional Exit with Keep, keeper may remain detached until a future launcher claims it or the user stops it.

If a keep or claim handshake fails, abort Restart/Exit and show a clear error. Do not silently abandon ownership.

### 5.5 IPC

Use standard-library local IPC:

- Windows: `multiprocessing.connection` with `AF_PIPE`.
- Linux: `multiprocessing.connection` with `AF_UNIX`.

Minimum commands:

```text
ping
status
stop
keep
claim
```

Use short timeouts. IPC failures must not freeze the Qt event loop; run blocking control work outside the UI thread or with bounded operations.

## 6. Single-instance launcher

Implement a launcher lock before auto-start executes.

Preferred implementation:

- `QLockFile` at `.runtime/launcher.lock`.
- Keep the lock object alive for the full application lifetime.
- Handle stale locks safely.
- If another launcher is active, show a concise message and exit without loading config or auto-starting apps.

Acceptance condition: launching `multi_run.vbs` twice results in one launcher process and one set of auto-start apps.

## 7. Logs and reconnect behavior

Use per-run logs:

```text
logs/<app_id>/<run_id>.out.log
logs/<app_id>/<run_id>.err.log
```

Add markers:

```text
===== RUN START <run_id> <timestamp> =====
===== LAUNCHER CLAIM <launcher_id> <timestamp> =====
===== RUN STOP <state/exit code> <timestamp> =====
```

UI behavior:

- For one active run, log buttons open that run.
- For multiple active runs, default to the newest active run and show instance count in status.
- Reconnected runs continue using the same log paths.
- If the log file is missing, truncated, or rotated, log preview must fail gracefully.

Do not append a new run to an unrelated old `run_id` log.

## 8. Close dialog and remembered preferences

Create UI classes outside `SystemTrayApp`, for example:

```text
lib/ui/close_apps_dialog.py
lib/ui/args_dialog.py
```

If moving all UI into `lib/ui` is too broad, keep only the new dialogs there and leave existing tray widgets in `multi.py`.

Preference schema example:

```json
{
  "advance-clipboard": {
    "restart": "keep",
    "exit": "close"
  }
}
```

Allowed values:

```text
ask
close
keep
```

Resolution rules:

1. `close_ask: false` always resolves to `close`.
2. `close_ask: true` with remembered `close` or `keep` applies that value.
3. `close_ask: true` with no preference or `ask` appears in the dialog.
4. `Restore defaults` clears remembered choices for displayed apps.

Collect all decisions first. Only after the user confirms should the launcher send stop/keep actions.

## 9. Arguments dialog implementation

Keep command construction testable outside Qt.

Refactor `CommandRunner` to accept optional run-time argument overrides without mutating the normalized app config. Example API:

```python
runner = CommandRunner(app_config, global_config, args_override=[...])
```

Or add a pure helper:

```python
build_command(command: str, args: list[str]) -> str
```

Dialog requirements:

- Prefill from normalized YAML `args`.
- Use one argument fragment per line.
- Preview joins non-empty lines with one space after the base command.
- Preserve quotes and shell operators exactly.
- Enter accepts and launches.
- Shift+Enter adds a line.
- Escape/Cancel rejects.
- Display a warning that Windows `cmd.exe` groups spaces with double quotes, not single quotes.

Do not write popup values back into `setting.yaml`.

## 10. Controller and API changes

Avoid putting policy in `multi.py`.

Suggested controller/session APIs:

```python
start_app(app_name, args_override=None, launch_source="manual")
stop_app(app_name, run_id=None)
list_runs(app_name=None)
prepare_shutdown(action)  # action: restart|exit
apply_shutdown_decisions(decisions)
claim_keep_alive_runs()
```

Return structured results rather than only booleans where useful:

```python
{
  "ok": true,
  "message": "...",
  "run_id": "...",
  "status": "running"
}
```

Maintain compatibility with CLI/TUI callers where practical.

## 11. Required implementation order

Do not implement everything in one large commit.

### Milestone 1: config and pure models

- Add config defaults and stable IDs.
- Add runtime models, atomic registry, preferences.
- Add `.runtime/` ignore.
- No process behavior change yet.
- Commit separately.

### Milestone 2: single-instance launcher

- Add lock before config load and auto-start.
- Add tests for active and stale lock behavior where possible.
- Commit separately.

### Milestone 3: keeper and process-tree stop

- Add keeper, IPC, Job Object/process group.
- Route subprocess start/stop/status through keeper.
- Add graceful timeout then force-close.
- Preserve current terminal-command semantics.
- Commit separately.

### Milestone 4: reconnect and keep-alive

- Discover runtime records on startup.
- Validate keeper PID plus creation time.
- Claim valid keep-alive runs.
- Mark unverifiable records orphaned; never kill uncertain processes.
- Commit separately.

### Milestone 5: close dialog and remembered choices

- Add consolidated dialog.
- Persist Restart and Exit preferences separately.
- Abort safely on Cancel or failed handoff.
- Commit separately.

### Milestone 6: `args_edit`

- Add arguments dialog and command preview.
- Manual GUI start only.
- Add override-aware command building.
- Commit separately.

### Milestone 7: docs and integration cleanup

- Update README and sample YAML.
- Run full tests and Windows manual scenarios.
- Do not merge to `develop` or `main`; leave review-ready commits on the feature branch.

## 12. Automated tests

Keep existing tests passing and add focused test files.

Suggested tests:

```text
tests/test_config_lifecycle_fields.py
tests/test_runtime_registry.py
tests/test_runtime_preferences.py
tests/test_command_args_override.py
tests/test_single_instance.py
tests/test_keeper_protocol.py
tests/test_process_tree_cleanup.py
tests/test_shutdown_decisions.py
tests/test_args_dialog_model.py
```

Required cases:

1. Missing `id` generates deterministic slug.
2. Duplicate IDs are rejected/skipped with warning.
3. New booleans default to false.
4. Atomic registry write survives interrupted temporary file.
5. Stale PID with mismatched creation time is not adopted or killed.
6. Two launcher starts do not duplicate auto-start.
7. Normal Stop closes parent and descendant processes.
8. Force timeout kills a descendant that ignores graceful termination.
9. Unexpected launcher death closes managed descendants.
10. Intentional Keep survives launcher restart and is claimed by the next launcher.
11. Cancel shutdown changes no run state.
12. Restart and Exit preferences are stored separately.
13. `args_edit` override produces the exact expected command.
14. Cancel arguments dialog starts nothing.
15. `multi_run: false` blocks the dialog when already running.
16. Auto-start never requests interactive args.
17. Multi-run instances receive distinct run IDs and log paths.

Use helper scripts under `tests/fixtures/` for process tests. Helpers should write PID files, spawn one descendant, optionally ignore termination, and exit deterministically.

Skip Windows Job Object tests on non-Windows. Do not mark core pure-model tests as Windows-only.

## 13. Manual Windows acceptance checklist

Run these before declaring the feature complete:

1. Start launcher once; verify auto-start apps start once.
2. Start launcher a second time; verify no second tray process and no duplicate apps.
3. Start a helper app that spawns children; press Stop; verify every PID is gone.
4. Start helper that ignores graceful stop; verify force-close after timeout.
5. Force-kill launcher from Task Manager; verify managed helper tree closes.
6. Restart launcher and choose Keep for one `close_ask` app; verify app remains and new launcher shows it Running.
7. Restart again and choose Close; verify it closes.
8. Exit launcher and choose Keep; reopen launcher later; verify it reconnects safely.
9. Corrupt a runtime record PID; verify status becomes Orphaned and no unrelated process is killed.
10. Start an `args_edit` app; edit role/goal values; verify exact command preview and execution.
11. Cancel arguments dialog; verify no new run/log/runtime record.
12. Test at least two simultaneous `multi_run: true` instances with different arguments.

Record commands and results in the final implementation report.

## 14. Non-goals for this feature

Do not add these unless required to satisfy a tested requirement:

- General task scheduler.
- Automatic restart/health monitoring.
- Remote process control.
- Editing YAML from the GUI.
- Guessing app entrypoints.
- Reintroducing Python/UV/Conda runner types.
- Adopting arbitrary external processes based only on matching command text.
- Modifying child application repositories.

## 15. Failure policy

- Never force-kill a process whose identity cannot be verified.
- Never proceed with Restart/Exit after a failed keep/stop handshake without informing the user.
- Never perform some shutdown decisions before the confirmation dialog is accepted.
- Never silently discard a remembered preference file parse error; rename the corrupt file and use defaults.
- UI operations must have bounded waits and must not freeze indefinitely.

## 16. Definition of done

The branch is ready for review when:

- all milestones are committed separately;
- existing and new tests pass;
- `git diff --check` passes;
- no user-owned `setting.yaml` edits are included accidentally;
- the manual Windows checklist is documented;
- `main` and `develop` remain at stable commit `6c40273`;
- the feature branch contains no modifications under `tools/`;
- the implementation report identifies remaining risks and any skipped platform tests.

## 17. Suggested Tampermonkey DEV dispatch

From `E:/python_project/tampermonkey_auto`:

```powershell
uv run python main.py --role DEV --goal "Work in E:/python_project/multi-run-apps on branch feat/process-lifecycle. Read E:/python_project/multi-run-apps/.plan/process-lifecycle-and-args-edit.md only as the implementation contract. Implement milestones in order with separate commits. Preserve all existing unstaged setting.yaml changes and do not modify tools/. Stop and report exact blockers instead of weakening safety requirements."
```

For a first pass with a bounded scope, ask DEV to implement Milestone 1 only, review it, then continue milestone by milestone.
