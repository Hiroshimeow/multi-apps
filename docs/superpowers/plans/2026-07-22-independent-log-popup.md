# Independent Tray Panel and Log Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Replace the interactive `QMenu`/`QWidgetAction` implementation with a stable tray panel and one independent fixed-position live-log popup.

**Architecture:** `SystemTrayApp` owns two sibling top-level UI components: `TrayPanelWindow` for app controls and `LogPopupWindow` for the single live-log view. `LogHoverController` owns hover timing, latest-wins asynchronous reads, preference state, and popup lifecycle. Neither component changes the other's layout or geometry.

**Tech Stack:** Python 3.13+, PyQt6, standard-library threading, unittest.

## Global Constraints

- No new dependency.
- No `QMenu` or `QWidgetAction` for app rows or live logs.
- Exactly one `InlineLogPanel` instance regardless of app count.
- Log file reads must not run on the Qt GUI thread.
- Rapid hover is latest-wins; stale read results are ignored.
- Tray panel geometry never changes when live logs open, switch, refresh, or close.
- Internal clicks on labels, blank row areas, controls, filter, log text, and scrollbar must not hide the tray panel.
- Right-click on an app name has no action. Left-click still opens its working directory.
- Keep existing Start, Stop, O.Logs/E.Logs click behavior, Open Config, Stop All Apps, Restart Launcher, and Exit Launcher.
- Preserve per-app line count, filter expression, and stream preferences.
- Do not modify machine-local `setting.yaml`.

---

### Task 1: Stable tray panel window

**Files:**
- Create: `lib/ui/tray_panel.py`
- Modify: `multi.py`
- Test: `tests/test_tray_panel.py`

**Produces:** `TrayPanelWindow`, `TrayActionButton`, and `compute_tray_panel_rect()`.

- [x] Write failing tests proving internal right/left clicks do not hide the panel and placement is stable inside screen bounds.
- [x] Verify tests fail because `TrayPanelWindow` does not exist.
- [x] Implement a frameless `Qt.Tool` window with header, app-row layout, global-action layout, Escape-to-hide, and deterministic bottom-right placement.
- [x] Replace `QSystemTrayIcon.setContextMenu()` with `ActivationReason.Context` toggling the custom panel.
- [x] Verify focused tray-panel tests pass.

### Task 2: One independent fixed log popup

**Files:**
- Create: `lib/ui/log_popup.py`
- Modify: `lib/ui/inline_log_panel.py`
- Test: `tests/test_log_popup.py`

**Produces:** `LogPopupWindow` and `compute_log_popup_rect()`.

- [x] Write failing tests proving one top-level popup is independent from tray layout, has deterministic geometry, and remains interactive.
- [x] Verify tests fail because `LogPopupWindow` does not exist.
- [x] Implement one frameless `Qt.Tool` popup containing one `InlineLogPanel`.
- [x] Position popup above the tray panel, clamped to the active screen, without touching tray-panel geometry.
- [x] Verify focused popup tests pass.

### Task 3: Latest-wins asynchronous log flow

**Files:**
- Create: `lib/ui/log_hover.py`
- Modify: `lib/ui/inline_log_panel.py`
- Test: `tests/test_log_hover.py`

**Produces:** `LatestLogReader` and `LogHoverController`.

- [x] Write failing tests proving GUI requests return immediately, rapid requests collapse to the latest target, stale results are ignored, and shutdown leaves no active worker/timer.
- [x] Verify tests fail because the new controller does not exist.
- [x] Implement one standard-library worker thread with a replaceable pending request slot.
- [x] Implement hover open/hide timers, popup ownership, periodic refresh, preference load/save, and generation checks.
- [x] Keep disk read/filter source state separate from QWidget geometry.
- [x] Verify focused controller tests pass.

### Task 4: Row and lifecycle integration

**Files:**
- Modify: `multi.py`
- Modify: `lib/ui/__init__.py`
- Test: `tests/test_app_context_menu.py`
- Test: `tests/test_launcher_reconnect_ui.py`
- Test: `tests/test_inline_log_stress.py`

- [x] Write failing integration tests proving rows own no log panel, one shared popup exists, panel geometry does not change on first/sustained hover, blank/right-clicks do not close UI, and menu-era geometry methods are absent.
- [x] Verify tests fail against current code.
- [x] Connect row hover signals to `LogHoverController`; keep log button clicks opening files.
- [x] Rebuild tray rows in `TrayPanelWindow`; stop/shutdown old row timers and controller exactly once.
- [x] Update restart/exit timer capture for tray panel and log controller.
- [x] Verify integration and stress tests pass.

### Task 5: Delete menu-era code and update contracts

**Files:**
- Modify: `lib/ui/inline_log_panel.py`
- Modify: `multi.py`
- Modify: `README.md`
- Modify: affected tests under `tests/`

- [x] Delete `InlineMenuGeometry`, `compute_inline_menu_geometry`, `NativeScrollableMenuStyle`, `InlineLogPanelCoordinator`, top-log `QWidgetAction`, menu resize state, and geometry repair tests.
- [x] Update README to describe a persistent tray panel plus independent fixed log popup.
- [x] Run dead-reference scans for removed menu-era symbols.
- [x] Run focused tests, full suite, compileall, YAML parse, and diff check.
- [x] Run Windows physical smoke: first hover, 100 rapid hover transitions, filter/scroll interaction, blank/right-click interaction, restart/rebuild, and screenshot inspection.
- [x] Commit and push only after exact-SHA verification.
