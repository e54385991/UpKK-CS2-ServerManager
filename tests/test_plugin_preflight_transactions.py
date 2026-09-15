"""Plugin preflight owns short read sessions and batches dependency loads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import plugin_conflict_service as module
from services.plugins.conflict import plan as plan_module


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)


class _Db:
    def __init__(self, *, rows=(), rules=()):
        self.rows = list(rows)
        self.rules = list(rules)
        self.calls = 0
        self.commit = AsyncMock()

    async def execute(self, _query):
        self.calls += 1
        return _Result(self.rows if self.calls == 1 else self.rules)


class _BoundDb(_Db):
    bind = object()


class _SessionFactory:
    def __init__(self, db: _Db) -> None:
        self._db = db
        self.open = 0
        self.closed = 0
        self.max_open = 0

    def __call__(self) -> _SessionFactory:
        return self

    async def __aenter__(self) -> _Db:
        self.open += 1
        self.max_open = max(self.max_open, self.open)
        return self._db

    async def __aexit__(self, *_args) -> bool:
        self.open -= 1
        self.closed += 1
        return False


def _plugin(plugin_id=1, *, deps=None, title=None):
    return SimpleNamespace(
        id=plugin_id,
        title=title or f"Plugin {plugin_id}",
        dependencies=deps,
        github_url=f"https://github.com/acme/plugin{plugin_id}",
        download_count=0,
        install_count=0,
        custom_install_path=None,
        ai_metadata=None,
        framework_key=None,
        framework="counterstrikesharp",
        version="v1",
    )


def _server():
    return SimpleNamespace(
        id=7,
        user_id=3,
        host="10.0.0.7",
        ssh_port=22,
        ssh_user="cs2",
        auth_type="password",
        ssh_password="secret",
        ssh_key_path=None,
        sudo_password=None,
        game_directory="/srv/cs2",
        github_proxy=None,
    )


def _empty_inventory() -> dict:
    return {"plugins": [], "frameworks": {}, "truncated": False}


@pytest.mark.asyncio
async def test_plan_plugin_install_releases_read_session_before_ssh(monkeypatch):
    plugins = {1: _plugin(1)}
    db = _Db()
    factory = _SessionFactory(db)
    ssh_started = asyncio.Event()
    release_ssh = asyncio.Event()

    async def blocked_inventory(_server):
        assert factory.open == 0
        ssh_started.set()
        await release_ssh.wait()
        return _empty_inventory()

    monkeypatch.setattr(
        module.MarketPlugin, "get_by_id", AsyncMock(side_effect=lambda _db, key: plugins.get(key))
    )
    monkeypatch.setattr(module, "inspect_remote_plugin_inventory", blocked_inventory)
    monkeypatch.setattr(module, "verified_market_plugin_ids", lambda *_args: set())
    monkeypatch.setattr(module, "installation_evidence", lambda *_args: [])

    task = asyncio.create_task(
        module.plan_plugin_install(
            7,
            1,
            include_dependencies=True,
            server=_server(),
            session_factory=factory,
        )
    )
    await asyncio.wait_for(ssh_started.wait(), timeout=1)
    assert factory.open == 0
    assert factory.closed == 1
    release_ssh.set()
    plan = await asyncio.wait_for(task, timeout=1)
    assert plan["plugin"]["id"] == 1
    assert factory.closed == 2
    assert factory.max_open == 1
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_compat_builder_does_not_commit_caller_session(monkeypatch):
    plugins = {1: _plugin(1)}
    db = _Db()
    monkeypatch.setattr(
        module.MarketPlugin, "get_by_id", AsyncMock(side_effect=lambda _db, key: plugins.get(key))
    )
    monkeypatch.setattr(
        module, "inspect_remote_plugin_inventory", AsyncMock(return_value=_empty_inventory())
    )
    monkeypatch.setattr(module, "verified_market_plugin_ids", lambda *_args: set())
    monkeypatch.setattr(module, "installation_evidence", lambda *_args: [])

    plan = await module.build_plugin_install_plan(db, 7, 1, server=_server())
    assert plan["installation_order"] == [1]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_dependency_graph_batches_when_session_has_bind(monkeypatch):
    plugins = {1: _plugin(1, deps="2"), 2: _plugin(2), 3: _plugin(3, deps="1")}
    loaded_batches: list[list[int]] = []

    async def get_by_ids(_db, plugin_ids):
        loaded_batches.append(list(plugin_ids))
        return [plugins[plugin_id] for plugin_id in plugin_ids if plugin_id in plugins]

    get_by_id = AsyncMock(side_effect=lambda _db, key: plugins.get(key))
    monkeypatch.setattr(module.MarketPlugin, "get_by_ids", get_by_ids)
    monkeypatch.setattr(module.MarketPlugin, "get_by_id", get_by_id)

    dependencies, target = await module._resolve_dependency_order(_BoundDb(), 3)
    assert [item.id for item in dependencies] == [2, 1]
    assert target.id == 3
    assert loaded_batches
    assert get_by_id.await_count == 0


@pytest.mark.asyncio
async def test_owned_and_compat_plans_share_hash(monkeypatch):
    plugins = {1: _plugin(1, deps="2"), 2: _plugin(2)}
    warning = SimpleNamespace(id=9, plugin_a_id=1, plugin_b_id=2, severity="warning", reason="warn")
    monkeypatch.setattr(
        module.MarketPlugin, "get_by_id", AsyncMock(side_effect=lambda _db, key: plugins.get(key))
    )
    monkeypatch.setattr(
        module, "inspect_remote_plugin_inventory", AsyncMock(return_value=_empty_inventory())
    )
    monkeypatch.setattr(module, "verified_market_plugin_ids", lambda *_args: set())
    monkeypatch.setattr(module, "installation_evidence", lambda *_args: [])

    compat = await module.build_plugin_install_plan(
        _Db(rules=[warning]), 7, 1, server=_server()
    )
    owned = await module.plan_plugin_install(
        7,
        1,
        server=_server(),
        session_factory=_SessionFactory(_Db(rules=[warning])),
    )
    assert owned["plan_hash"] == compat["plan_hash"]
    assert owned["installation_order"] == compat["installation_order"] == [2, 1]
    assert plan_module._plugin_plan_confirmation_payload(owned) == (
        plan_module._plugin_plan_confirmation_payload(compat)
    )
