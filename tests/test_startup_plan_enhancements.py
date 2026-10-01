"""Security and planning coverage for the enhanced CS2 agent tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from modules.models import ManagedPluginFile
from modules.schemas.servers import ServerUpdate
from services import ai_orchestrator
from services.server_startup_arguments import normalize_additional_parameters
from services.server_startup_service import (
    StartupPlanError,
    build_server_startup_plan,
    execute_server_startup_plan,
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


def test_managed_plugin_file_unique_key_uses_fixed_size_path_digest():
    ddl = str(CreateTable(ManagedPluginFile.__table__).compile(dialect=postgresql.dialect()))

    assert "UNIQUE (managed_plugin_id, path_hash)" in ddl
    assert "UNIQUE (managed_plugin_id, relative_path)" not in ddl


def test_additional_startup_parameters_are_parsed_and_serialized_safely():
    assert (
        normalize_additional_parameters('+sv_hibernate_when_empty 0 +exec "practice cfg" -insecure')
        == "+sv_hibernate_when_empty 0 +exec 'practice cfg' -insecure"
    )
    assert normalize_additional_parameters("") is None


@pytest.mark.parametrize(
    "value",
    (
        "+sv_cheats 0; touch /tmp/pwned",
        "+exec $(whoami)",
        "+exec `whoami`",
        "+hostname duplicate",
        "-port 27016",
        "+game_mode 2",
        "bash -c whoami",
    ),
)
def test_additional_startup_parameters_reject_shell_and_managed_options(value):
    with pytest.raises(ValueError):
        normalize_additional_parameters(value)
    with pytest.raises(ValidationError):
        ServerUpdate(additional_parameters=value)


def test_startup_plan_updates_named_mode_pair_and_has_stable_steps():
    server = _startup_server()

    plan = build_server_startup_plan(
        server,
        {
            "default_map": "kz_variety_x",
            "max_players": 24,
            "game_mode": "deathmatch",
            "additional_parameters": "+sv_hibernate_when_empty 0 -insecure",
        },
    )

    assert plan["blocked"] is False
    assert plan["after"]["game_type"] == "2"
    assert plan["after"]["game_mode"] == "deathmatch"
    assert len(plan["plan_hash"]) == 64
    assert [step["action"] for step in plan["steps"]] == [
        "validate_startup_revision",
        "save_startup_settings",
        "restart_server",
        "verify_server",
    ]

    approval_plan, progress = ai_orchestrator._build_plan_snapshots(
        "apply_server_startup_update", plan
    )
    assert [step["id"] for step in approval_plan["steps"]] == [
        "validate_startup_revision",
        "save_startup_settings",
        "restart_server",
        "verify_server",
    ]
    assert progress["total"] == 4


@pytest.mark.asyncio
async def test_stale_startup_plan_is_rejected_before_save_or_restart(monkeypatch):
    from services import server_startup_service

    server = _startup_server()
    db = _StartupDB()
    events = []
    monkeypatch.setattr(
        server_startup_service.maintenance_lock_service,
        "get",
        lambda *args, **kwargs: _StartupLock(),
    )
    monkeypatch.setattr(
        server_startup_service,
        "_current_server",
        AsyncMock(return_value=server),
    )

    async def progress(message, message_type, metadata):
        events.append((message, message_type, metadata))

    with pytest.raises(StartupPlanError, match="changed before approval"):
        await execute_server_startup_plan(
            db,
            SimpleNamespace(id=8, is_admin=False),
            server.id,
            {"max_players": 24},
            "0" * 64,
            progress=progress,
        )

    assert db.commits == 0
    assert events[-1][2]["step_status"] == "failed"


@pytest.mark.asyncio
async def test_startup_save_failure_rolls_back_without_restart(monkeypatch):
    from services import server_startup_service

    server = _startup_server()
    request = {"max_players": 24}
    plan = build_server_startup_plan(server, request)
    db = _StartupDB(fail_first_commit=True)

    class Manager:
        async def check_session_manager_available(self, _server):
            return True, "ready"

        async def stop_server(self, _server):
            raise AssertionError("restart must not run after save failure")

    monkeypatch.setattr(
        server_startup_service.maintenance_lock_service,
        "get",
        lambda *args, **kwargs: _StartupLock(),
    )
    monkeypatch.setattr(
        server_startup_service,
        "_current_server",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(server_startup_service, "SSHManager", Manager)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await execute_server_startup_plan(
            db,
            SimpleNamespace(id=8, is_admin=False),
            server.id,
            request,
            plan["plan_hash"],
        )

    assert db.rollbacks == 1


@pytest.mark.asyncio
async def test_restart_failure_reports_saved_startup_configuration(monkeypatch):
    from services import server_startup_service

    server = _startup_server()
    request = {"max_players": 24, "default_map": "kz_variety_x"}
    plan = build_server_startup_plan(server, request)
    db = _StartupDB()

    class Manager:
        async def check_session_manager_available(self, _server):
            return True, "ready"

        async def stop_server(self, _server):
            return True, "stopped"

        async def start_server(self, _server, _progress=None):
            return False, "map failed to load"

    monkeypatch.setattr(
        server_startup_service.maintenance_lock_service,
        "get",
        lambda *args, **kwargs: _StartupLock(),
    )
    monkeypatch.setattr(
        server_startup_service,
        "_current_server",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(server_startup_service, "SSHManager", Manager)
    monkeypatch.setattr(
        server_startup_service.redis_manager,
        "clear_server_cache",
        AsyncMock(return_value=True),
    )

    result = await execute_server_startup_plan(
        db,
        SimpleNamespace(id=8, is_admin=False),
        server.id,
        request,
        plan["plan_hash"],
    )

    assert result["success"] is False
    assert result["partial_failure"] is True
    assert result["configuration_saved"] is True
    assert result["restart"]["success"] is False
    assert server.max_players == 24
    assert server.default_map == "kz_variety_x"


@pytest.mark.asyncio
async def test_successful_startup_update_verifies_process_and_a2s(monkeypatch):
    from services import server_startup_service

    server = _startup_server()
    request = {"game_mode": "deathmatch"}
    plan = build_server_startup_plan(server, request)
    db = _StartupDB()

    class Manager:
        async def check_session_manager_available(self, _server):
            return True, "ready"

        async def stop_server(self, _server):
            return True, "stopped"

        async def start_server(self, _server, _progress=None):
            return True, "started"

    monkeypatch.setattr(
        server_startup_service.maintenance_lock_service,
        "get",
        lambda *args, **kwargs: _StartupLock(),
    )
    monkeypatch.setattr(
        server_startup_service,
        "_current_server",
        AsyncMock(return_value=server),
    )
    monkeypatch.setattr(server_startup_service, "SSHManager", Manager)
    monkeypatch.setattr(
        server_startup_service,
        "_verify_process",
        AsyncMock(return_value=(True, "process running")),
    )
    monkeypatch.setattr(
        server_startup_service.a2s_service,
        "query_server_info",
        AsyncMock(return_value=(True, {"map_name": "de_dust2", "max_players": 32})),
    )
    monkeypatch.setattr(
        server_startup_service.redis_manager,
        "clear_server_cache",
        AsyncMock(return_value=True),
    )

    result = await execute_server_startup_plan(
        db,
        SimpleNamespace(id=8, is_admin=False),
        server.id,
        request,
        plan["plan_hash"],
    )

    assert result["success"] is True
    assert result["partial_failure"] is False
    assert result["verification"]["process"] is True
    assert result["verification"]["a2s"] is True
    assert server.game_mode == "deathmatch"
    assert server.game_type == "2"
