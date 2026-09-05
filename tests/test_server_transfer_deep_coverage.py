"""覆盖服务器配置导入导出的冲突、安全和清理分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes.servers import transfer
from modules import AuthType, Server, ServerAgentPolicy
from modules.schemas.servers import ServerConfigExport, ServerConfigImportRequest
from services.server_config_transfer import server_to_config_entry


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Db:
    def __init__(self, query_values=()):
        self.query_values = list(query_values)
        self.added = []
        self.commits = 0
        self.flushed = 0

    async def execute(self, _statement):
        value = self.query_values.pop(0) if self.query_values else None
        return _Result(value)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushed += 1
        for value in self.added:
            if isinstance(value, Server) and value.id is None:
                value.id = 100

    async def commit(self):
        self.commits += 1


def _server(**overrides):
    values = dict(
        id=1,
        user_id=7,
        name="Alpha",
        host="host-a",
        ssh_port=22,
        ssh_user="cs2",
        auth_type=AuthType.PASSWORD,
        ssh_password="ssh-secret",
        game_port=27015,
        game_directory="/srv/cs2",
        server_name="Alpha server",
        default_map="de_dust2",
        max_players=16,
        game_mode="competitive",
        game_type="0",
        api_key="old-key",
    )
    values.update(overrides)
    return Server(**values)


def _entry(**overrides):
    server = _server(**overrides)
    return server_to_config_entry(server, include_secrets=True)


@pytest.mark.asyncio
async def test_conflict_and_export_resolution_helpers(monkeypatch):
    name_hit = _server(id=2, name="same")
    path_hit = _server(id=3, name="other")
    db = _Db([name_hit, path_hit])
    assert await transfer._find_conflicts(
        db, 7, name="same", host="host-a", game_directory="/srv/cs2"
    ) == (name_hit, path_hit)

    calls = []

    async def find_by_name(_db, candidate, user_id):
        calls.append((candidate, user_id))
        return candidate == "Alpha (2)"

    monkeypatch.setattr(transfer.Server, "get_by_name_and_user", find_by_name)
    assert await transfer._next_available_name(_Db(), 7, "Alpha") == "Alpha (3)"
    assert calls == [("Alpha (2)", 7), ("Alpha (3)", 7)]

    user = SimpleNamespace(id=7)
    selected = _server(id=4)
    monkeypatch.setattr(transfer.Server, "get_all_by_user", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException, match="No servers"):
        await transfer.collect_export_bundle(_Db(), user, None, False)
    with pytest.raises(HTTPException, match="At least one"):
        await transfer._resolve_export_servers(_Db(), user, [])
    monkeypatch.setattr(transfer, "get_server_with_permission", AsyncMock(return_value=selected))
    resolved = await transfer._resolve_export_servers(_Db(), user, [4, 4])
    assert resolved == [selected]
    bundle = await transfer.collect_export_bundle(_Db(), user, [4], True)
    assert isinstance(bundle, ServerConfigExport)
    assert bundle.servers[0].name == "Alpha"


@pytest.mark.asyncio
async def test_export_endpoint_serializes_attachment(monkeypatch):
    server = _server()
    monkeypatch.setattr(
        transfer,
        "collect_export_bundle",
        AsyncMock(
            return_value=ServerConfigExport(
                servers=[server_to_config_entry(server, include_secrets=False)]
            )
        ),
    )
    response = await transfer.export_server_configs(
        server_ids=[1], include_secrets=False, db=_Db(), current_user=SimpleNamespace(id=7)
    )
    assert response.media_type == "application/json"
    assert "attachment; filename=cs2-server-config-" in response.headers["content-disposition"]
    assert response.body.endswith(b"\n")


@pytest.mark.asyncio
async def test_import_handles_conflicts_updates_renames_and_creates(monkeypatch):
    user = SimpleNamespace(id=7)
    db = _Db()
    different_name = _server(id=10, name="different")
    same_name = _server(id=11, name="skip")
    update_server = _server(id=12, name="update")
    path_server = _server(id=13, name="path-owner")
    rename_server = _server(id=14, name="rename")
    entries = [
        _entry(name="conflict"),
        _entry(name="skip"),
        _entry(name="update"),
        _entry(name="path"),
        _entry(name="rename"),
        _entry(name="fresh"),
    ]
    conflict_map = {
        "conflict": (different_name, _server(id=15, name="other")),
        "skip": (same_name, None),
        "update": (update_server, update_server),
        "path": (None, path_server),
        "rename": (rename_server, None),
        "fresh": (None, None),
    }

    async def find_conflict(_db, _user_id, *, name, host, game_directory):
        return conflict_map[name]

    monkeypatch.setattr(transfer, "_find_conflicts", find_conflict)
    monkeypatch.setattr(transfer, "_next_available_name", AsyncMock(return_value="rename (2)"))
    monkeypatch.setattr(transfer, "generate_api_key", lambda: "new-key")
    monkeypatch.setattr(transfer, "inherit_global_discord_binding", AsyncMock())
    monkeypatch.setattr(transfer.redis_manager, "clear_server_cache", AsyncMock())
    monkeypatch.setattr(transfer, "record_audit_event", AsyncMock())

    request = ServerConfigImportRequest(
        servers=entries,
        include_secrets=True,
        conflict_strategy="rename",
    )
    # Exercise skip and update in separate calls so the strategy itself remains
    # explicit while the combined request covers all per-entry outcomes.
    request.conflict_strategy = "rename"
    result = await transfer.import_server_configs(request, db, user, SimpleNamespace())
    assert result.total == 6
    assert result.failed == 1
    assert result.skipped == 2
    assert result.imported == 3
    assert result.updated == 0
    assert [item.action for item in result.results] == [
        "failed",
        "imported",
        "skipped",
        "skipped",
        "imported",
        "imported",
    ]
    assert db.commits == 1
    assert db.flushed == 3
    assert any(isinstance(value, ServerAgentPolicy) for value in db.added)

    request.conflict_strategy = "update"
    update_result = await transfer.import_server_configs(
        ServerConfigImportRequest(servers=[_entry(name="update")], conflict_strategy="update"),
        db,
        user,
        SimpleNamespace(),
    )
    assert update_result.updated == 1
    transfer.redis_manager.clear_server_cache.assert_awaited_with(12)


@pytest.mark.asyncio
async def test_import_skip_strategy_and_empty_server_validation(monkeypatch):
    user = SimpleNamespace(id=7)
    existing = _server(id=21, name="same")
    monkeypatch.setattr(transfer, "_find_conflicts", AsyncMock(return_value=(existing, None)))
    monkeypatch.setattr(transfer, "record_audit_event", AsyncMock())
    db = _Db()
    result = await transfer.import_server_configs(
        ServerConfigImportRequest(servers=[_entry(name="same")], conflict_strategy="skip"),
        db,
        user,
        SimpleNamespace(),
    )
    assert result.skipped == 1 and result.imported == 0
    assert db.commits == 1
