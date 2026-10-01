"""Security and planning coverage for the enhanced CS2 agent tools."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.models import MarketPlugin
from modules.utils import get_current_time
from services import ai_orchestrator
from services.ai_tools import (
    TOOLS_BY_NAME,
    _safe_css_log_name,
)
from services.plugin_diagnostic_service import (
    ACTIVE_DIAGNOSTIC_STATUSES,
    _group_candidates,
    get_diagnostic_recommendation,
)
from tests.ai_agent_enhancements_fakes import (
    _startup_server as _startup_server,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupDB as _StartupDB,
)
from tests.ai_agent_enhancements_fakes import (
    _StartupLock as _StartupLock,
)


@pytest.mark.asyncio
async def test_stale_ai_server_lock_reconciliation_keeps_active_runs(monkeypatch):
    class DB:
        def __init__(self, active_run_id=None):
            self.active_run_id = active_run_id

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: self.active_run_id)

    clear = AsyncMock(return_value=True)
    monkeypatch.setattr(ai_orchestrator.maintenance_lock_service, "clear_stale_server_lock", clear)

    assert await ai_orchestrator.reconcile_stale_ai_server_lock(DB(), 32) is True
    clear.assert_awaited_once()

    clear.reset_mock()
    assert await ai_orchestrator.reconcile_stale_ai_server_lock(DB("run-32"), 32) is False
    clear.assert_not_awaited()


def test_new_agent_tool_schemas_cannot_select_identity_server_or_paths():
    tool_names = {
        "list_css_error_logs",
        "read_css_error_log",
        "plan_plugin_crash_isolation",
        "execute_plugin_crash_isolation",
        "get_plugin_crash_isolation",
        "restore_plugin_quarantine",
        "search_github_cs2_plugins",
        "inspect_github_plugin",
        "plan_github_plugin_install",
        "apply_github_plugin_install",
    }
    for name in tool_names:
        properties = TOOLS_BY_NAME[name].input_model.model_json_schema().get("properties", {})
        assert "user_id" not in properties
        assert "server_id" not in properties
        assert "ssh_host" not in properties
        assert "game_directory" not in properties
        assert "relative_path" not in properties


def test_css_log_reader_accepts_only_fixed_directory_basenames():
    assert _safe_css_log_name("errors_2026-08-01.log") == "errors_2026-08-01.log"
    for value in ("../console.log", "subdir/error.log", ".hidden.log", "plugin.dll"):
        with pytest.raises(ValueError):
            _safe_css_log_name(value)


@pytest.mark.asyncio
async def test_declared_market_dependencies_form_one_diagnostic_group(monkeypatch):
    managed = [
        SimpleNamespace(
            id=1,
            display_name="RootPlugin",
            repo_url="https://github.com/example/RootPlugin",
            custom_install_path=None,
            market_plugin_id=10,
        ),
        SimpleNamespace(
            id=2,
            display_name="SharedDependency",
            repo_url="https://github.com/example/SharedDependency",
            custom_install_path=None,
            market_plugin_id=20,
        ),
    ]

    class Result:
        def scalars(self):
            return self

        def all(self):
            return managed

    class DB:
        async def execute(self, _statement):
            return Result()

    market = [
        SimpleNamespace(id=10, dependencies="20"),
        SimpleNamespace(id=20, dependencies=None),
    ]
    monkeypatch.setattr(MarketPlugin, "get_by_ids", AsyncMock(return_value=market))
    candidates = [
        {"key": "metamod:rootplugin", "name": "RootPlugin.vdf"},
        {"key": "metamod:shareddependency", "name": "SharedDependency.vdf"},
    ]

    groups = await _group_candidates(DB(), 7, candidates)

    assert len(groups) == 1
    assert groups[0]["reason"] == "declared_dependency"
    assert groups[0]["candidate_keys"] == [
        "metamod:rootplugin",
        "metamod:shareddependency",
    ]


def test_quarantine_states_block_automation_until_explicit_restore():
    assert "interrupted" in ACTIVE_DIAGNOSTIC_STATUSES
    assert "inconclusive" in ACTIVE_DIAGNOSTIC_STATUSES
    assert "completed_with_quarantine" in ACTIVE_DIAGNOSTIC_STATUSES


@pytest.mark.asyncio
async def test_post_update_restart_failures_only_recommend_diagnosis(monkeypatch):
    from services import plugin_diagnostic_service
    from services.server_monitor import server_monitor

    now = get_current_time()
    server = SimpleNamespace(id=817, last_update_time=now - timedelta(minutes=10))
    monkeypatch.setattr(
        plugin_diagnostic_service,
        "authorized_server",
        AsyncMock(return_value=server),
    )
    server_monitor.restart_history[server.id] = [
        now - timedelta(minutes=3),
        now - timedelta(minutes=1),
    ]
    try:
        result = await get_diagnostic_recommendation(
            SimpleNamespace(), SimpleNamespace(id=12), server.id
        )
    finally:
        server_monitor.restart_history.pop(server.id, None)

    assert result["recommended"] is True
    assert result["reason"] == "post_update_start_failures"
    assert result["restart_count"] == 2
