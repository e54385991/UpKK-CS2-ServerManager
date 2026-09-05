"""Cover deterministic route helpers without starting the application server."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import github_plugins, plugin_market
from api.routes.v1 import console
from api.routes.v1 import github_plugins as v1_github
from api.routes.v1.schemas import GitHubInstallPlanRequest


def test_market_and_legacy_github_parsers_cover_valid_and_invalid_inputs():
    assert plugin_market._requested_release(None) == (None, None, None)
    assert plugin_market._requested_release(
        "https://github.com/acme/demo/releases/download/v1/demo.zip"
    ) == ("tag:v1", "v1", "demo.zip")
    assert plugin_market._requested_release(
        "https://github.com/acme/demo/releases/download/v1"
    ) == (
        None,
        None,
        None,
    )
    with pytest.raises(HTTPException, match="Invalid download URL"):
        plugin_market._requested_release("https://example.com/demo.zip")

    assert plugin_market.parse_github_url("git@github.com:acme/demo.git") == ("acme", "demo")
    assert plugin_market.parse_github_url("https://github.com/acme/demo/anything") == (
        "acme",
        "demo",
    )
    with pytest.raises(ValueError):
        plugin_market.parse_github_url("https://gitlab.com/acme/demo")
    assert plugin_market.parse_dependency_ids(None) == []
    assert plugin_market.parse_dependency_ids(" 1, 2,,3 ") == [1, 2, 3]
    with pytest.raises(ValueError, match="Invalid dependency ID"):
        plugin_market.parse_dependency_ids("1,nope")

    assert github_plugins.parse_github_url("https://github.com/acme/demo/releases") == (
        "acme",
        "demo",
    )
    with pytest.raises(ValueError):
        github_plugins.parse_github_url("http://github.com/acme/demo")


def test_plugin_copy_command_covers_copy_strategies_and_shell_quoting():
    rsync = github_plugins._build_plugin_copy_command(
        "/tmp/source dir", "/tmp/target dir", ["*.json", "config/*"], use_rsync=True
    )
    assert "rsync -av" in rsync and "--exclude='*.json'" in rsync
    assert "gamedata" in rsync and "--remove-destination" in rsync

    tar = github_plugins._build_plugin_copy_command(
        "/tmp/source", "/tmp/target", ["*.cfg"], use_rsync=False
    )
    assert "tar --exclude='*.cfg'" in tar
    plain = github_plugins._build_plugin_copy_command(
        "/tmp/source", "/tmp/target", [], use_rsync=False
    )
    assert plain.startswith("cp -r")

    for exc, code, detail in (
        (github_plugins.AgentAccessDenied("hidden"), 404, "Server not found"),
        (PermissionError("denied"), 403, "denied"),
        (github_plugins.GitHubPlanError("conflict"), 409, "conflict"),
    ):
        error = github_plugins._safe_github_error(exc)
        assert (error.status_code, error.detail) == (code, detail)


def test_v1_github_plan_views_normalize_partial_payloads():
    body = GitHubInstallPlanRequest(
        repo_url=" https://github.com/acme/demo/ ",
        mode="upgrade",
        source_prefix="addons",
        target_prefix="addons/plugins",
        exclude_dirs=["cfg"],
    )
    legacy = v1_github._to_legacy_plan_request(body)
    assert legacy.repo_url == "https://github.com/acme/demo"
    assert legacy.mode == "upgrade"

    plan = v1_github.to_plan_view(
        {
            "server_id": 4,
            "repo_url": "https://github.com/acme/demo",
            "plan_hash": "h" * 64,
            "release": {"tag_name": "v1", "name": "Release"},
            "asset": {"name": "demo.zip"},
            "mapping": [{"source": "addons", "target": "addons"}, {"source": "x"}],
            "hard_conflicts": [{"rule_id": 1, "plugin_a_id": 2, "plugin_b_id": 3}],
            "conflict_warnings": ["skip", {"rule_id": 4, "reason": "warn"}],
            "dependencies": [{"id": 8, "title": "Dependency"}, {"id": 9}],
            "linux_runtime_profile": SimpleNamespace(
                model_dump=lambda: {"distro_id": "debian", "recommended_steam_runtime": "sniper"}
            ),
            "exclude_dirs": ["cfg"],
            "warnings": ["review"],
            "already_installed": [8],
        }
    )
    assert plan.release_tag == "v1"
    assert [item.model_dump() for item in plan.mapping] == [
        {"source": "addons", "target": "addons"}
    ]
    assert len(plan.hard_conflicts) == 1
    assert len(plan.conflict_warnings) == 1
    assert plan.dependencies[0].title == "Dependency"
    assert plan.linux_runtime_profile.recommended_steam_runtime == "sniper"
    assert v1_github._runtime_view(None) is None
    assert v1_github._runtime_view("invalid") is None


def _server(**overrides):
    values = {"id": 5, "host": "example.test", "session_manager": "tmux"}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_console_helpers_cover_origin_workspace_and_session_selection():
    assert console._strict_session_manager("screen") == "screen"
    assert console._strict_session_manager("other") == "tmux"
    assert console._optional_session_manager(None) is None
    assert console._optional_session_manager("screen") == "screen"
    assert console._optional_session_manager("invalid") is None
    assert console._session_name_for("game", 5)
    assert console._session_name_for("steamcmd", 5)

    allowed = SimpleNamespace(headers={"origin": "https://example.test", "host": "example.test"})
    denied = SimpleNamespace(headers={"origin": "https://evil.test", "host": "example.test"})
    absent = SimpleNamespace(headers={"host": "example.test"})
    assert console._origin_allowed(allowed)
    assert not console._origin_allowed(denied)
    assert console._origin_allowed(absent)

    view = console._workspace(
        _server(session_manager="screen"),
        ssh_ok=False,
        ssh_error="offline",
        game_running=True,
        steamcmd_running=True,
        message="notice",
    )
    assert view.session_manager == "screen"
    assert view.ssh_error == "offline"


@pytest.mark.asyncio
async def test_capture_console_pane_covers_missing_success_and_failure(monkeypatch):
    module = importlib.import_module("api.routes.v1.console")
    ssh = SimpleNamespace(
        execute_command=AsyncMock(side_effect=[(True, "", ""), (True, "tick 123", "")])
    )
    monkeypatch.setattr(module, "find_running_session_manager", AsyncMock(return_value=None))
    missing = await module._capture_session_pane(ssh, _server(), "game")
    assert missing.running is False

    ssh.execute_command = AsyncMock(return_value=(True, "tick 123", ""))
    monkeypatch.setattr(module, "find_running_session_manager", AsyncMock(return_value="screen"))
    success = await module._capture_session_pane(ssh, _server(), "game")
    assert success.running and success.session_manager == "screen"
    assert success.text == "tick 123"

    ssh.execute_command = AsyncMock(return_value=(False, "", "capture failed"))
    failed = await module._capture_session_pane(ssh, _server(), "steamcmd")
    assert failed.running and failed.message == "capture failed"
