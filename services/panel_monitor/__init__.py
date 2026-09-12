"""Panel process monitoring: collection, history, and query helpers."""

from .history import history
from .queries import get_errors, get_monitor
from .runtime import set_monitoring_enabled, start_panel_monitor, stop_panel_monitor
from .types import ErrorQuery, MonitorQuery

__all__ = [
    "ErrorQuery",
    "MonitorQuery",
    "get_errors",
    "get_monitor",
    "history",
    "set_monitoring_enabled",
    "start_panel_monitor",
    "stop_panel_monitor",
]
