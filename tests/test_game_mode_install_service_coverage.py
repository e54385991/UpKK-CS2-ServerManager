"""Cover game-mode execution state transitions with fully isolated fakes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.models import AuthType, Server, ServerStatus
from services import game_mode_install_service as service
from services.game_mode_recipes import GameModeRecipe


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _DB:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.commits = 0
        self.added = []
        self.executed = []

    async def execute(self, statement):
        self.executed.append(statement)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self.rows)))

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


def _server(**overrides) -> Server:
    values = {
        "id": 401,
        "user_id": 9,
        "name": "game-mode",
        "host": "127.0.0.1",
        "ssh_user": "steam",
        "auth_type": AuthType.PASSWORD,
        "status": ServerStatus.STOPPED,
        "game_directory": "/srv/cs2",
    }
    values.update(overrides)
    return Server(**values)


def _recipe(*, frameworks=(), wait_files=(), plugin_config=None):
    return GameModeRecipe(
        id="test-mode",
        launch_upsert={"-tickrate": "128"},
        frameworks=tuple(frameworks),
        market_plugin_titles=(),
        wait_files=tuple(wait_files),
        plugin_config=plugin_config or {},
        maps_append=(),
        startup_workshop_map="123",
    )


def _market_recipe(*, title="CS2-Upkk-PanelPLG-Mapchooser", maps=()):
    return GameModeRecipe(
        id="test-mode",
        launch_upsert={},
        frameworks=(),
        market_plugin_titles=(title,),
        wait_files=("config.json",),
        plugin_config={},
        maps_append=tuple(maps),
        startup_workshop_map="123",
    )


def _patch_lock_and_lookup(monkeypatch, server, *, admin=False):
    monkeypatch.setattr(
        service.maintenance_lock_service,
        "get",
        lambda *_args, **_kwargs: _Lock(),
    )
    lookup = AsyncMock(return_value=server)
    monkeypatch.setattr(Server, "get_by_id", lookup)
    monkeypatch.setattr(Server, "get_by_id_and_user", lookup)
    user = SimpleNamespace(id=server.user_id, is_admin=admin)
    return user


@pytest.mark.asyncio
async def test_catalog_and_plan_reject_unknown_mode(monkeypatch):
    with pytest.raises(service.GameModePlanError, match="Unknown game mode"):
        await service.build_game_mode_plan(_DB(), _server(), "missing")

    monkeypatch.setattr(service, "_catalog_for_server", AsyncMock(return_value={"modes": []}))
    assert await service.catalog_for_server(_DB(), _server()) == {"modes": []}


@pytest.mark.asyncio
async def test_managed_plugin_cleanup_and_launch_save_cover_empty_and_rows(monkeypatch):
    empty = _DB()
    assert await service._clear_managed_plugins(empty, 401) == 0
    assert empty.commits == 1

    row_db = _DB([SimpleNamespace(id=1), SimpleNamespace(id=2)])
    assert await service._clear_managed_plugins(row_db, 401) == 2
    assert row_db.commits == 1

    server = _server(additional_parameters="-insecure")
    cache_clear = AsyncMock()
    monkeypatch.setattr(service.redis_manager, "clear_server_cache", cache_clear)
    db = _DB()
    await service._save_launch_args(db, server, "-tickrate 128")
    assert server.additional_parameters == "-tickrate 128"
    assert db.added == [server]
    cache_clear.assert_awaited_once_with(server.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan, expected",
    [
        ({"blocked": True, "blocking_reasons": ["conflict"], "plan_hash": "h"}, "conflict"),
        ({"blocked": False, "blocking_reasons": [], "plan_hash": "new"}, "changed"),
        (
            {
                "blocked": False,
                "blocking_reasons": [],
                "plan_hash": "h",
                "warnings": [{"rule_id": 3}],
            },
            "acknowledgement",
        ),
    ],
)
async def test_execute_plan_rejects_blocked_stale_and_unacknowledged(monkeypatch, plan, expected):
    server = _server()
    user = _patch_lock_and_lookup(monkeypatch, server)
    monkeypatch.setattr(service, "build_game_mode_plan", AsyncMock(return_value=plan))
    with pytest.raises(service.GameModePlanError, match=expected):
        await service.execute_game_mode_plan(
            _DB(),
            server,
            user,
            "test-mode",
            wipe_addons=False,
            expected_plan_hash="h",
        )


@pytest.mark.asyncio
async def test_execute_plan_success_covers_startup_framework_restart_and_verify(monkeypatch):
    server = _server(additional_parameters=None)
    user = _patch_lock_and_lookup(monkeypatch, server)
    recipe = _recipe(frameworks=("counterstrikesharp",), wait_files=("config.json",))
    plan = {
        "blocked": False,
        "blocking_reasons": [],
        "plan_hash": "h",
        "addons_path": "/srv/cs2/addons",
        "startup": {"changed": True, "after": "-tickrate 128"},
        "current": {"css": False, "mapchooser": True, "maps": True, "config": True},
        "plugin_plans": {},
        "warnings": [],
        "steps": [{"id": "restart_and_wait", "status": "pending"}],
    }
    monkeypatch.setattr(service, "build_game_mode_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "get_recipe", lambda _mode: recipe)
    monkeypatch.setattr(service, "record_framework_installation", AsyncMock())
    monkeypatch.setattr(service, "_emit_plan_progress", AsyncMock())
    monkeypatch.setattr(service, "_read_text", AsyncMock(side_effect=["maps", "{}", "maps", "{}"]))
    monkeypatch.setattr(
        service,
        "inspect_game_mode_state",
        AsyncMock(return_value={"css": True, "mapchooser": True, "maps": True, "config": True}),
    )
    monkeypatch.setattr(service, "wait_for_remote_files", AsyncMock())
    manager = SimpleNamespace(disconnect=AsyncMock())
    monkeypatch.setattr(service, "connect", AsyncMock(return_value=manager))

    class _SSH:
        async def install_counterstrikesharp(self, _server, callback):
            await callback("framework progress")
            return True, "installed"

        async def stop_server(self, _server):
            return True, "stopped"

        async def start_server(self, _server, callback):
            await callback("restart progress")
            return True, "started"

    monkeypatch.setattr(service, "SSHManager", _SSH)
    result = await service.execute_game_mode_plan(
        _DB(), server, user, "test-mode", wipe_addons=False, expected_plan_hash="h"
    )
    assert result["success"] is True
    assert result["restart_performed"] is True
    assert server.additional_parameters == "-tickrate 128"


@pytest.mark.asyncio
async def test_execute_kz_plan_installs_legacy_libssl_before_plugins(monkeypatch):
    server = _server()
    user = _patch_lock_and_lookup(monkeypatch, server)
    recipe = _recipe()
    plan = {
        "blocked": False,
        "blocking_reasons": [],
        "plan_hash": "kz-libssl",
        "addons_path": "/srv/cs2/addons",
        "startup": {"changed": False, "after": None},
        "current": {
            "libssl11": False,
            "css": True,
            "mapchooser": True,
            "maps": True,
            "config": True,
        },
        "plugin_plans": {},
        "warnings": [],
        "steps": [{"id": "restart_and_wait", "status": "unchanged"}],
    }
    monkeypatch.setattr(service, "build_game_mode_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "get_recipe", lambda _mode: recipe)
    monkeypatch.setattr(service, "_emit_plan_progress", AsyncMock())
    monkeypatch.setattr(
        service,
        "inspect_game_mode_state",
        AsyncMock(return_value={"css": True, "mapchooser": True, "maps": True, "config": True}),
    )
    monkeypatch.setattr(service, "_read_text", AsyncMock(side_effect=["maps", "{}"]))
    manager = SimpleNamespace(
        execute_sudo_command=AsyncMock(return_value=(True, "installed", "")),
        execute_command=AsyncMock(return_value=(True, "", "")),
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(service, "connect", AsyncMock(return_value=manager))
    result = await service.execute_game_mode_plan(
        _DB(), server, user, "kz", wipe_addons=False, expected_plan_hash="kz-libssl"
    )
    assert result["success"] is True
    install_command = manager.execute_sudo_command.await_args_list[0].args[0]
    assert "libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb" in install_command
    assert "dpkg -i" in install_command
    assert manager.disconnect.await_count == 2


@pytest.mark.asyncio
async def test_execute_plan_wipe_and_failure_return_partial_result(monkeypatch):
    server = _server()
    user = _patch_lock_and_lookup(monkeypatch, server)
    recipe = _recipe(frameworks=(), wait_files=())
    plan = {
        "blocked": False,
        "blocking_reasons": [],
        "plan_hash": "wipe-hash",
        "addons_path": "/srv/cs2/addons",
        "startup": {"changed": False, "after": None},
        "current": {"css": True, "mapchooser": True, "maps": True, "config": True},
        "plugin_plans": {},
        "warnings": [],
        "steps": [{"id": "restart_and_wait", "status": "unchanged"}],
    }
    monkeypatch.setattr(service, "build_game_mode_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "get_recipe", lambda _mode: recipe)
    monkeypatch.setattr(service, "_emit_plan_progress", AsyncMock())
    monkeypatch.setattr(service, "wipe_addons_directory", AsyncMock())
    monkeypatch.setattr(
        service, "connect", AsyncMock(return_value=SimpleNamespace(disconnect=AsyncMock()))
    )
    monkeypatch.setattr(
        service,
        "SSHManager",
        lambda: SimpleNamespace(stop_server=AsyncMock(return_value=(False, "still running"))),
    )
    result = await service.execute_game_mode_plan(
        _DB(), server, user, "test-mode", wipe_addons=True, expected_plan_hash="wipe-hash"
    )
    assert result["success"] is False
    assert "Unable to stop" in result["message"]
    assert result["partial_completion"] is False


@pytest.mark.asyncio
async def test_execute_plan_market_plugin_install_restart_and_final_verify(monkeypatch):
    server = _server()
    user = _patch_lock_and_lookup(monkeypatch, server)
    recipe = _market_recipe()
    plan = {
        "blocked": False,
        "blocking_reasons": [],
        "plan_hash": "market-hash",
        "addons_path": "/srv/cs2/addons",
        "startup": {"changed": False, "after": None},
        "current": {"css": True, "mapchooser": False, "maps": True, "config": True},
        "plugin_plans": {
            "CS2-Upkk-PanelPLG-Mapchooser": {
                "plugin": {"id": 17},
                "plan_hash": "plugin-hash",
            }
        },
        "warnings": [],
        "steps": [{"id": "restart_and_wait", "status": "pending"}],
    }
    monkeypatch.setattr(service, "build_game_mode_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "get_recipe", lambda _mode: recipe)
    monkeypatch.setattr(service, "_emit_plan_progress", AsyncMock())
    validate = Mock()
    monkeypatch.setattr(service, "validate_plugin_plan_acknowledgements", validate)

    async def install(_db, _server, _user, _plugin_id, _acknowledged, **kwargs):
        await kwargs["progress"]("plugin progress")
        return {"success": True, "message": "installed"}

    monkeypatch.setattr(service, "execute_plugin_install_plan", install)
    monkeypatch.setattr(service, "wait_for_remote_files", AsyncMock())
    monkeypatch.setattr(service, "record_framework_installation", AsyncMock())
    states = iter(
        [
            {"css": True, "mapchooser": True, "maps": True, "config": True},
        ]
    )
    monkeypatch.setattr(
        service, "inspect_game_mode_state", AsyncMock(side_effect=lambda *_: next(states))
    )
    monkeypatch.setattr(service, "_read_text", AsyncMock(side_effect=["maps", "{}"]))
    manager = SimpleNamespace(
        disconnect=AsyncMock(),
    )
    monkeypatch.setattr(service, "connect", AsyncMock(return_value=manager))

    class _SSH:
        async def stop_server(self, _server):
            return True, "stopped"

        async def start_server(self, _server, callback):
            await callback("started")
            return True, "started"

    monkeypatch.setattr(service, "SSHManager", _SSH)
    result = await service.execute_game_mode_plan(
        _DB(), server, user, "test-mode", wipe_addons=False, expected_plan_hash="market-hash"
    )
    assert result["success"] is True
    assert any(item["action"].startswith("install:") for item in result["completed"])


@pytest.mark.asyncio
async def test_execute_plan_wipe_restart_and_verification_errors(monkeypatch):
    server = _server()
    user = _patch_lock_and_lookup(monkeypatch, server)
    recipe = _recipe()
    plan = {
        "blocked": False,
        "blocking_reasons": [],
        "plan_hash": "wipe-ok",
        "addons_path": "/srv/cs2/addons",
        "startup": {"changed": False, "after": None},
        "current": {"css": True, "mapchooser": True, "maps": True, "config": True},
        "plugin_plans": {},
        "warnings": [],
        "steps": [{"id": "restart_and_wait", "status": "unchanged"}],
    }
    monkeypatch.setattr(service, "build_game_mode_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "get_recipe", lambda _mode: recipe)
    monkeypatch.setattr(service, "_emit_plan_progress", AsyncMock())
    monkeypatch.setattr(service, "wipe_addons_directory", AsyncMock())
    monkeypatch.setattr(
        service,
        "inspect_game_mode_state",
        AsyncMock(return_value={"css": False, "mapchooser": True}),
    )
    monkeypatch.setattr(
        service, "connect", AsyncMock(return_value=SimpleNamespace(disconnect=AsyncMock()))
    )
    monkeypatch.setattr(service, "_clear_managed_plugins", AsyncMock(return_value=2))

    class _SSH:
        async def stop_server(self, _server):
            return True, "stopped"

        async def start_server(self, _server, _callback):
            return True, "started"

    monkeypatch.setattr(service, "SSHManager", _SSH)
    result = await service.execute_game_mode_plan(
        _DB(), server, user, "test-mode", wipe_addons=True, expected_plan_hash="wipe-ok"
    )
    assert result["success"] is False
    assert "Prerequisite verification failed" in result["message"]
    assert result["partial_completion"] is True
    assert result["restart_required"] is False


@pytest.mark.asyncio
async def test_execute_plan_restart_start_failure_is_persisted(monkeypatch):
    server = _server()
    user = _patch_lock_and_lookup(monkeypatch, server)
    recipe = _recipe()
    plan = {
        "blocked": False,
        "blocking_reasons": [],
        "plan_hash": "restart-fail",
        "addons_path": "/srv/cs2/addons",
        "startup": {"changed": False, "after": None},
        "current": {"css": True, "mapchooser": True, "maps": True, "config": True},
        "plugin_plans": {},
        "warnings": [],
        "steps": [{"id": "restart_and_wait", "status": "pending"}],
    }
    monkeypatch.setattr(service, "build_game_mode_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(service, "get_recipe", lambda _mode: recipe)
    monkeypatch.setattr(service, "_emit_plan_progress", AsyncMock())
    monkeypatch.setattr(
        service, "connect", AsyncMock(return_value=SimpleNamespace(disconnect=AsyncMock()))
    )

    class _SSH:
        async def stop_server(self, _server):
            return True, "stopped"

        async def start_server(self, _server, _callback):
            return False, "start failed"

    monkeypatch.setattr(service, "SSHManager", _SSH)
    db = _DB()
    result = await service.execute_game_mode_plan(
        db, server, user, "test-mode", wipe_addons=False, expected_plan_hash="restart-fail"
    )
    assert result["success"] is False
    assert result["message"] == "Unable to start server after plugin installation: start failed"
    assert server.status == ServerStatus.ERROR
    assert db.commits == 1
