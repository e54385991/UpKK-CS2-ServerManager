"""Bounded, reversible crash isolation for Metamod and CounterStrikeSharp plugins."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Awaitable, Callable, Literal

from modules.models import MarketPlugin, Server
from modules.utils import get_current_time
from services.a2s_query import a2s_service
from services.ai_access import AgentAccessDenied, authorized_server
from services.maintenance_lock import maintenance_lock_service
from services.plugins.diagnostic_policy import (
    ACTIVE_DIAGNOSTIC_STATUSES,
)
from services.plugins.diagnostic_policy import (
    blocked_servers as _blocked_servers,
)
from services.plugins.diagnostic_policy import (
    has_diagnostic_blocker as _has_diagnostic_blocker,
)
from services.plugins.diagnostics.execute import (
    _expand_groups,
    _health_attempt,
    _record_step,
    _run_group_isolation,
    _run_strict_fallback,
    _run_suspect_analysis,
    diagnostic_run_payload,
    execute_diagnostic_plan,
    get_diagnostic_run,
    interrupt_active_plugin_diagnostics,
    restore_diagnostic_run,
)
from services.plugins.diagnostics.plan import (
    _group_candidates,
    _health_policy,
    _inventory,
    _link_managed_plugins,
    _plan_hash,
    _plugin_alias,
    build_diagnostic_plan,
    get_diagnostic_recommendation,
    get_latest_diagnostic_run,
    has_diagnostic_blocker,
)
from services.plugins.diagnostics.ssh import (
    _console_delta,
    _console_size,
    _move_entry,
    _set_candidates,
    _validate_remote_path,
)
from services.ssh_manager import SSHManager

DiagnosticScope = Literal["metamod", "counterstrikesharp", "both"]
Progress = Callable[[str, dict[str, Any]], Awaitable[None]]
MAX_START_ATTEMPTS = 12
MAX_DURATION_SECONDS = 20 * 60
HEALTH_OBSERVE_SECONDS = 60
A2S_RECHECK_SECONDS = 2
FATAL_LOG_PATTERN = re.compile(
    r"segmentation fault|sigsegv|core dumped|fatal error|failed to load.*(?:\.so|plugin)|"
    r"unhandled exception.*terminat|server crashed",
    re.IGNORECASE,
)

_DIAGNOSTIC_PHASE_MESSAGES = {
    "preflight_versions_and_health": "Checking server health and plugin versions",
    "baseline_without_third_party": "Testing baseline health without third-party plugins",
    "group_isolation": "Isolating plugin groups to narrow down the crash",
    "individual_confirmation": "Confirming the suspected crash-causing plugin",
    "final_restored_state": "Verifying server stability after restoring safe plugins",
    "strict_individual_fallback": "Testing remaining plugins individually as fallback",
    "multi_fault_final_state": "Verifying final state after multi-fault isolation",
    "safe_all_plugins_quarantined": "Checking stability with all candidates quarantined",
}


async def _emit_readable_progress(progress, phase, enabled_keys):
    if progress is None:
        return
    message = str(_DIAGNOSTIC_PHASE_MESSAGES.get(phase, phase))
    if enabled_keys:
        message += ": " + ", ".join(enabled_keys)
    await progress(
        "diagnostic_progress",
        {"phase": phase, "enabled": enabled_keys, "message": message},
    )


_PATCHABLE = (
    ACTIVE_DIAGNOSTIC_STATUSES,
    MarketPlugin,
    SSHManager,
    Server,
    _blocked_servers,
    _console_delta,
    _console_size,
    _expand_groups,
    _group_candidates,
    _has_diagnostic_blocker,
    _health_attempt,
    _health_policy,
    _inventory,
    _link_managed_plugins,
    _move_entry,
    _plan_hash,
    _plugin_alias,
    _record_step,
    _run_group_isolation,
    _run_strict_fallback,
    _run_suspect_analysis,
    _set_candidates,
    _validate_remote_path,
    a2s_service,
    asyncio,
    authorized_server,
    build_diagnostic_plan,
    diagnostic_run_payload,
    execute_diagnostic_plan,
    get_current_time,
    get_diagnostic_recommendation,
    get_diagnostic_run,
    get_latest_diagnostic_run,
    has_diagnostic_blocker,
    interrupt_active_plugin_diagnostics,
    maintenance_lock_service,
    restore_diagnostic_run,
    time,
    AgentAccessDenied,
)
