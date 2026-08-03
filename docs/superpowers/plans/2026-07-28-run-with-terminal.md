# Run With Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two app-name context actions: run the configured YAML command in a standalone terminal, or only open a terminal in the app directory.

**Architecture:** Reuse the existing app-name context popup and `AppToolService`. The standalone command path launches a terminal directly and never calls `AppController`, the session manager, process keeper, runtime registry, or launcher log pipeline.

**Tech Stack:** Python, PyQt6, subprocess, unittest.

## Global Constraints

- Use the configured YAML `command` plus YAML `args`; do not open the args editor.
- Keep the spawned command unmanaged by the launcher.
- Do not change managed Start/Stop behavior.
- Do not change or repair the separately reported `m365_copilot` environment failure.

---

### Task 1: Standalone terminal action

**Files:**
- Modify: `lib/ui/app_tools.py`
- Modify: `lib/ui/app_context_popup.py`
- Modify: `multi.py`
- Test: `tests/test_app_context_menu.py`
- Test: `tests/test_tray_ui_architecture.py`

**Interfaces:**
- Consumes: `CommandRunner.build_command_text(command, args)` and existing terminal discovery.
- Produces: `AppToolService.open_terminal(workdir, command=None)` and a two-action app context popup.

- [x] Write failing tests proving the popup has both actions, the command uses the exact workdir/configured command, and no managed start method is called.
- [x] Run the focused tests and confirm failure.
- [x] Implement the smallest direct terminal launch and two-button popup.
- [x] Run focused tests, full suite, compileall, and `git diff --check`.
- [x] Commit and push the branch used by PR #2.
