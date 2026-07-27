# Idle-First Launcher Implementation Plan

> **For agentic workers:** Use the appropriate Superpower skill before acting. Phase 2 is the final implementation phase; complete all remaining work before final review.

**Repository:** `E:\python_project\multi-run-apps`

**Branch:** `feat/idle-first-launcher`

**Accepted baseline:** `f8fc0f9bef2414f9e7fad7ec2574f30e0ea3cb18`

## Goal

Keep this personal launcher light, smooth, stable, and easy to extend without adding infrastructure that the application does not need.

Preserve the functions that already work:

- Start, Stop, Stop All;
- Restart Launcher, Exit Launcher, crash survival and reconnect;
- auto-start and duplicate prevention;
- `multi_run`;
- inline and pinned logs;
- editable arguments;
- CLI and TUI;
- verified process-tree cleanup.

## Simple architecture

Reuse the current code:

- `RuntimeRegistry` and full `.runtime/runs/<run_id>.json` records;
- `atomic_write_json()` and `_exclusive_file_lock()`;
- `SubprocessSessionManager` as the shared tray/CLI/TUI lifecycle implementation;
- current supervisor, IPC and process identity checks;
- current Qt panel, rows and log components.

Add only:

- one rebuildable `.runtime/hot-index-v1.json`;
- minimal app-level locking for duplicate Start races;
- one visible-only Qt refresh coordinator.

Do not add a database, service, daemon, plugin framework, vendor adapter, or second runtime registry.

## Command and parameters

The launcher already supports generic shell commands. Keep one model:

```yaml
command: "codex"
args:
  - "--model gpt-5"
  - "--sandbox workspace-write"
  - "exec \"Fix the failing tests\""
```

```yaml
command: "gemini"
args:
  - "--model gemini-2.5-pro"
  - "--prompt \"Review this repository\""
```

`command` is the executable or shell command. `args` are its parameters. Preview and runtime must continue using `CommandRunner.build_command_text()`.

Do not create a separate `params` field. Codex, Gemini, Python, Node, `uv`, Conda and other tools use the same `command + args` path.

---

# Phase 0 - Measurement baseline

**Status:** Complete.

Implemented:

- isolated idle benchmark;
- real Qt launcher PID measurement;
- active fixture validation;
- CPU, runtime I/O, worker, timer, watcher and child-process checks.

Relevant commits:

```text
c78a7f4  benchmark harness
fce8f7d  trustworthy idle verification
```

---

# Phase 1 - Idle-first UI

**Status:** Complete and accepted.

**Final commit:**

```text
f8fc0f9bef2414f9e7fad7ec2574f30e0ea3cb18
```

Implemented:

- one batch status snapshot instead of per-row polling;
- no recurring status, watcher, log reader or preference worker while hidden;
- preference load/save outside the Qt thread;
- viewer close and unpin do not join or wait for disk I/O;
- final application shutdown remains bounded;
- reopen/drain/shutdown races keep one preference writer and preserve pending data.

Phase 1 is only changed again for a reproduced regression.

---

# Phase 2 - Final implementation

**Status:** Next and final phase.

Implement all remaining work in this phase. Internal tasks may use small commits, but there is one final review after the whole phase rather than a new product phase for each task.

## Task 1 - Add one hot runtime index

**Primary files:**

- `lib/runtime/registry.py`
- `tests/test_runtime_registry.py`
- optional focused test: `tests/test_hot_index.py`

Add:

```text
.runtime/hot-index-v1.json
```

Shape:

```json
{
  "version": 1,
  "active": {},
  "latest": {}
}
```

Rules:

- full run records remain authoritative;
- `active` contains only `starting`, `running`, `stopping` and `orphaned` runs;
- `latest` points to the newest run by `(created_at, run_id)`;
- save/update/delete keeps the index synchronized;
- missing or invalid index rebuilds once from full records;
- normal reads use the index and do not scan historical files;
- reuse current atomic JSON and file-lock helpers.

Tests:

- active and latest update correctly;
- terminal state removes active but keeps latest;
- concurrent updates do not lose entries;
- invalid/missing index rebuilds;
- 10,000 historical records are not opened during a normal index read.

## Task 2 - Move normal lifecycle paths to the index

**Primary files:**

- `lib/session/subprocess_session.py`
- `lib/core.py` only where needed
- existing runtime, lifecycle, reconnect and snapshot tests

Use hot-index data for:

- `get_status_snapshot()` and `get_info()`;
- duplicate Start checks;
- auto-start eligibility;
- startup reconcile candidates;
- Stop target selection;
- reconnect and active log target selection.

Keep `list_records()` only for explicit history, migration and diagnostics.

All Start paths already converge on `SubprocessSessionManager.start()`. Keep that design.

For non-`multi_run` Start:

1. take a short app lock;
2. check active state;
3. save a durable `starting` record and index reservation;
4. release the lock;
5. launch the keeper;
6. update the record and index normally.

Do not hold locks while waiting for process startup, IPC or Stop.

Tests:

- two non-`multi_run` starts create one run;
- two `multi_run` starts create two runs;
- auto-start racing manual/CLI start creates no duplicate;
- stale updates do not reactivate terminal runs;
- failed launch leaves a visible recoverable record;
- restart, exit, crash survival, reconnect and Stop remain unchanged.

## Task 3 - Refresh only while visible

**Primary files:**

- `multi.py`
- `lib/ui/tray_panel.py` only where needed
- `tests/test_idle_launcher.py`
- `tests/test_tray_ui_architecture.py`

`SystemTrayApp` owns one refresh coordinator.

When the panel opens:

```text
show -> one hot-index snapshot -> apply rows -> start visible refresh
```

Use:

- one `QFileSystemWatcher` while visible;
- one short debounce around 100 ms;
- one fallback refresh around 3 seconds while visible;
- direct refresh after Start, Stop, lifecycle completion and config reload.

When hidden:

- remove watcher paths;
- stop refresh timers;
- perform no status polling or runtime reads.

Pinned logs may keep their own log reader, but must not restart global status polling.

Tests:

- visible state refreshes automatically;
- rapid changes coalesce;
- hide stops watcher and timers;
- config reload does not duplicate rows, timers or watchers;
- hidden idle benchmark stays at zero runtime activity.

## Task 4 - Verify command parameters, migrate and finish

**Files:**

- `tests/test_args_edit.py`
- `tests/test_simple_launcher.py`
- `README.md`
- `setting.yaml.sample`
- `lib/config.py` or `lib/runners/command_runner.py` only if a real test exposes a gap

Add regression examples for Codex- and Gemini-style commands. Prove:

- argument order is preserved;
- spaces and quotes remain intact;
- preview equals runtime command;
- manual argument editing affects only the new run;
- YAML stays unchanged;
- CLI, TUI and auto-start use YAML args;
- no provider-specific implementation is required.

Migration must:

- rebuild a missing or invalid index automatically;
- preserve existing running children and run IDs;
- read old full records without conversion;
- reconcile stale `starting` records with current identity rules.

## Final verification

Focused set:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run python -m unittest -v `
  tests.test_runtime_registry `
  tests.test_status_snapshot `
  tests.test_simple_launcher `
  tests.test_launcher_reconnect_ui `
  tests.test_persistent_supervisors `
  tests.test_process_tree_cleanup `
  tests.test_idle_launcher `
  tests.test_tray_ui_architecture `
  tests.test_args_edit `
  tests.test_lifecycle_commands
```

Full suite:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run python -m unittest discover -s tests -p "test_*.py"
```

Static checks:

```powershell
uv run python -m compileall -q multi.py cli.py lib tools\idle_first_benchmark.py
git diff --check
```

Final acceptance:

```text
hidden status timers: 0
hidden runtime watchers: 0
hidden status snapshots: 0
hidden runtime/log reads: 0
hidden named workers: 0
launcher-owned child processes while idle: 0
normal status/start paths scan historical run files: 0
CPU one-core mean while hidden: <1.0%
CPU one-core p95 while hidden: <2.0%
full test failures: 0
```

Also verify:

- Restart/Exit/crash do not stop managed apps;
- reconnect keeps run identity, uptime and logs;
- Stop still performs verified process-tree cleanup;
- auto-start does not duplicate runs;
- `multi_run` behavior is unchanged;
- Codex/Gemini-style commands with parameters launch through the generic path.

After all evidence passes:

1. update README and sample config;
2. run one final review over the complete Phase 2 diff;
3. commit and push `feat/idle-first-launcher`;
4. mark the project complete.

## Working rules

- Follow current code patterns.
- Use the smallest complete change.
- Start non-trivial tasks with deterministic failing tests.
- Do not weaken identity, durability or process-tree safety.
- Do not restyle the UI or refactor unrelated code.
- Keep one lifecycle path for tray, auto-start, CLI and TUI.
