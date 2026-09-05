"""覆盖 AI 工具审批摘要的各类安全预览分支。"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import ai_tools


def _server():
    return SimpleNamespace(
        id=4,
        name="demo",
        host="host",
        ssh_port=22,
        game_port=27015,
        game_directory="/srv/cs2",
        user_id=1,
    )


def _ctx():
    return ai_tools.ToolContext(
        db=SimpleNamespace(),
        user=SimpleNamespace(id=1, is_admin=False, is_active=True),
        server=_server(),
        emit=AsyncMock(),
    )


def _plan_hash(plan):
    return ai_tools.canonical_arguments(plan)[1]


@pytest.mark.asyncio
async def test_approval_summary_simple_operation_branches(monkeypatch):
    monkeypatch.setattr(ai_tools, "_require_current_server", AsyncMock(return_value=_server()))
    ctx = _ctx()
    for operation in ("deploy", "update", "validate"):
        result = await ai_tools.build_approval_summary(
            "run_server_operation", {"operation": operation}, ctx
        )
        assert result["operation"] == operation
    for operation in ("install_metamod", "install_counterstrikesharp"):
        result = await ai_tools.build_approval_summary(
            "run_server_operation", {"operation": operation}, ctx
        )
        assert result["installation_method"] == "panel_native"
    for action in ("start", "stop", "restart"):
        result = await ai_tools.build_approval_summary("control_server", {"action": action}, ctx)
        assert result["operation"] == action
    command = await ai_tools.build_approval_summary(
        "send_game_console_command", {"command": "status"}, ctx
    )
    assert command["command_hash"] == hashlib.sha256(b"status").hexdigest()
    fallback = await ai_tools.build_approval_summary("unknown", {"x": 1}, ctx)
    assert fallback["arguments"] == {"x": 1}


@pytest.mark.asyncio
async def test_approval_summary_map_startup_and_diagnostic_branches(monkeypatch):
    ctx = _ctx()
    monkeypatch.setattr(ai_tools, "_require_current_server", AsyncMock(return_value=_server()))
    candidate = SimpleNamespace(command="map de_dust2", to_public_dict=lambda: {"name": "de_dust2"})
    monkeypatch.setattr("services.change_map_service.load_map_pool", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.change_map_service.resolve_unique_map", lambda *_a: candidate)
    result = await ai_tools.build_approval_summary("change_current_map", {"query": "dust"}, ctx)
    assert result["map"] == {"name": "de_dust2"}

    startup_plan = {
        "plan_hash": "a" * 64,
        "configuration_revision": "rev",
        "changes": [],
        "steps": [],
        "partial_failure_policy": "stop",
        "blocked": False,
        "blocking_reasons": [],
    }
    monkeypatch.setattr(
        "services.server_startup_service.build_server_startup_plan", lambda *_a: startup_plan
    )
    result = await ai_tools.build_approval_summary(
        "apply_server_startup_update",
        {"default_map": "de_dust2", "expected_plan_hash": "a" * 64},
        ctx,
    )
    assert result["configuration_revision"] == "rev"

    diagnostic_plan = {
        "plan_hash": "d" * 64,
        "candidates": [],
        "health_policy": {"max_start_attempts": 2, "max_duration_seconds": 30},
    }
    monkeypatch.setattr(
        "services.plugin_diagnostic_service.build_diagnostic_plan",
        AsyncMock(return_value=diagnostic_plan),
    )
    result = await ai_tools.build_approval_summary(
        "execute_plugin_crash_isolation", {"scope": "both", "expected_plan_hash": "d" * 64}, ctx
    )
    assert result["maximum_starts"] == 2
    monkeypatch.setattr(
        "services.plugin_diagnostic_service.get_diagnostic_run",
        AsyncMock(return_value={"quarantine": []}),
    )
    result = await ai_tools.build_approval_summary(
        "restore_plugin_quarantine", {"diagnostic_id": "x" * 36}, ctx
    )
    assert result["quarantine"] == []


@pytest.mark.asyncio
async def test_approval_summary_plugin_workshop_github_and_saved_command(monkeypatch):
    ctx = _ctx()
    monkeypatch.setattr(ai_tools, "_require_current_server", AsyncMock(return_value=_server()))
    workshop = {
        "plan_hash": "w" * 64,
        "workshop": {"id": "1"},
        "steps": [],
        "warnings": [],
        "download_behavior": "download",
    }
    monkeypatch.setattr(
        "services.workshop_map_service.build_workshop_map_plan", AsyncMock(return_value=workshop)
    )
    result = await ai_tools.build_approval_summary(
        "apply_workshop_map",
        {"workshop_id_or_url": "1", "expected_plan_hash": "w" * 64},
        ctx,
    )
    assert result["target"] == {"id": "1"}

    plugin_plan = {
        "plan_hash": "p" * 64,
        "plugin": {"id": 1},
        "steps": [],
        "hard_conflicts": [],
        "warnings": [],
        "already_installed": [],
        "installation_order": [],
    }
    monkeypatch.setattr(
        "services.plugin_conflict_service.build_plugin_install_plan",
        AsyncMock(return_value=plugin_plan),
    )
    monkeypatch.setattr(ai_tools.MarketPlugin, "get_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "services.linux_runtime_service.detect_linux_runtime_profile",
        AsyncMock(return_value={"reason": "default"}),
    )
    monkeypatch.setattr(ai_tools, "_market_release_selection_preview", AsyncMock(return_value=[]))
    result = await ai_tools.build_approval_summary(
        "apply_plugin_plan", {"plugin_id": 1, "expected_plan_hash": "p" * 64}, ctx
    )
    assert result["target"] == {"id": 1}

    github_plan = {
        "plan_hash": "g" * 64,
        "repo_url": "https://github.com/a/b",
        "release_tag": "v1",
        "asset": "a.zip",
        "archive_sha256": "a" * 64,
        "mapping": [],
        "config_policy": "preserve",
        "warnings": [],
        "hard_conflicts": [],
        "conflict_warnings": [],
        "compatibility_unknown": False,
    }
    monkeypatch.setattr(
        "services.github_plugin_plan_service.build_github_install_plan",
        AsyncMock(return_value=github_plan),
    )
    result = await ai_tools.build_approval_summary(
        "apply_github_plugin_install",
        {"repo_url": "https://github.com/a/b", "expected_plan_hash": "g" * 64},
        ctx,
    )
    assert result["repository"].endswith("a/b")

    command = SimpleNamespace(id=9, name="list", target="host", commands="ls", updated_at=None)
    monkeypatch.setattr(
        ai_tools.CustomCommand, "get_by_id_server_and_user", AsyncMock(return_value=command)
    )
    command_hash = ai_tools._saved_command_hash(command)
    result = await ai_tools.build_approval_summary(
        "execute_saved_host_command", {"command_id": 9, "expected_command_hash": command_hash}, ctx
    )
    assert result["command_name"] == "list"

    upgrade = {"no_op": True, "plugin_id": 2}
    monkeypatch.setattr(
        "services.plugin_auto_update_service.plugin_auto_update_service.build_plugin_upgrade_plan",
        AsyncMock(return_value=upgrade),
    )
    result = await ai_tools.build_approval_summary(
        "apply_managed_plugin_upgrade",
        {"plugin_id": 2, "expected_plan_hash": _plan_hash(upgrade)},
        ctx,
    )
    assert result["upgrade_plan"] == upgrade
