"""Focused UI components used by the launcher."""

from .app_context_popup import AppContextPopup, compute_context_popup_rect
from .app_tools import AppToolActionResult, AppToolService
from .app_row import AppControlWidget, AppNameLabel, HoverLogButton
from .inline_log_panel import (
    FILTER_SOFT_MIN_WIDTH,
    InlineLogPanel,
    LogRangeHighlighter,
    PANEL_EMERGENCY_MIN_HEIGHT,
    PANEL_MAX_HEIGHT,
    PANEL_PREFERRED_HEIGHT,
)
from .log_filter import (
    FILTER_WARNING_STANDALONE_BANG,
    FilteredLine,
    HighlightRange,
    LogFilterResult,
    LogFilterResultState,
    LogFilterSpec,
    apply_log_filter,
    highlight_ranges,
    parse_filter_expression,
)
from .lifecycle_commands import BackgroundCommandExecutor, LifecycleCommandController
from .log_hover import LatestLogReader, LogHoverController, LogTarget
from .log_preference_owner import LogPreferenceOwner
from .log_popup import LogPopupWindow, PinnedLogWindow, compute_log_popup_rect
from .pinned_logs import (
    MultiLogReader,
    PinnedLogManager,
    compute_initial_pinned_log_rect,
    compute_pinned_log_rects,
    recover_pinned_log_rect,
)
from .log_preferences import (
    LogPanelPreference,
    LogPanelPreferenceStore,
    default_log_preferences_path,
)
from .log_reader import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TAIL_LINES,
    MAX_READ_BYTES,
    MAX_TAIL_LINES,
    MIN_READ_BYTES,
    MIN_TAIL_LINES,
    BoundedLogReader,
    LogChangeKind,
    LogReader,
    LogSnapshot,
    LogSnapshotState,
    classify_log_change,
)
from .tray_panel import TrayActionButton, TrayPanelWindow, compute_tray_panel_rect
from .transient_ui import TransientUiController

__all__ = [
    "compute_initial_pinned_log_rect",
    "compute_pinned_log_rects",
    "compute_context_popup_rect",
    "TransientUiController",
    "PinnedLogManager",
    "MultiLogReader",
    "LifecycleCommandController",
    "BackgroundCommandExecutor",
    "AppContextPopup",
    "AppControlWidget",
    "AppNameLabel",
    "AppToolActionResult",
    "AppToolService",
    "BoundedLogReader",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TAIL_LINES",
    "FILTER_SOFT_MIN_WIDTH",
    "FILTER_WARNING_STANDALONE_BANG",
    "FilteredLine",
    "HighlightRange",
    "HoverLogButton",
    "InlineLogPanel",
    "LatestLogReader",
    "LogChangeKind",
    "LogFilterResult",
    "LogFilterResultState",
    "LogFilterSpec",
    "LogHoverController",
    "LogPanelPreference",
    "LogPreferenceOwner",
    "LogPanelPreferenceStore",
    "LogPopupWindow",
    "PinnedLogWindow",
    "LogRangeHighlighter",
    "LogReader",
    "LogSnapshot",
    "LogSnapshotState",
    "LogTarget",
    "MAX_READ_BYTES",
    "MAX_TAIL_LINES",
    "MIN_READ_BYTES",
    "MIN_TAIL_LINES",
    "PANEL_EMERGENCY_MIN_HEIGHT",
    "PANEL_MAX_HEIGHT",
    "PANEL_PREFERRED_HEIGHT",
    "TrayActionButton",
    "TrayPanelWindow",
    "apply_log_filter",
    "classify_log_change",
    "compute_log_popup_rect",
    "compute_tray_panel_rect",
    "default_log_preferences_path",
    "highlight_ranges",
    "parse_filter_expression",
    "recover_pinned_log_rect",
]
