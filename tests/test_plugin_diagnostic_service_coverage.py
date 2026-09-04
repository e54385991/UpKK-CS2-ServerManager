"""隔离插件诊断服务的策略、健康探测和生命周期分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import plugin_diagnostic_service as module


class _Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)

    def scalar_one_or_none(self):
        return self.scalar


class _Db:
    def __init__(self, *, rows=(), scalar=None, user=None):
        self.rows = list(rows)
        self.scalar = scalar
        self.user = user
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _query):
        return _Result(self.rows, self.scalar)

    async def get(self, model, _key):
        return self.user if getattr(model, "__name__", "") == "User" else None

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        if getattr(value, "id", None) is None:
            value.id = "run-1"


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Manager:
    def __init__(self, *, commands=(), start=(True, "started"), status=(True, "running")):
        self.commands = list(commands)
        self.start_result = start
        self.status_result = status
        self.executed = []
        self.stopped = 0
        self.disconnected = 0

    async def connect(self, _server):
        return True, "connected"

    async def disconnect(self):
        self.disconnected += 1

    async def execute_command(self, command, timeout):
        self.executed.append((command, timeout))
        return self.commands.pop(0)

    async def validate_path_within_base(self, *_args, **_kwargs):
        return True, ""

    async def start_server(self, _server):
        return self.start_result

    async def stop_server(self, _server):
        self.stopped += 1
        return True, "stopped"

    async def get_server_status(self, _server):
        return self.status_result


def _server(**overrides):
    values = {
        "id": 7,
        "user_id": 3,
        "game_directory": "/srv/cs2",
        "enable_a2s_monitoring": True,
        "a2s_query_host": "",
        "a2s_query_port": 0,
        "host": "game.example",
        "game_port": 27015,
        "last_update_time": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _entry(key="metamod:one", *, quarantined=False):
    return SimpleNamespace(
        candidate_key=key,
        source_relative_path="addons/metamod/one.vdf",
        quarantine_relative_path=f".upkk/quarantine/run/{key.replace(':', '-')}",
        is_quarantined=quarantined,
        is_culprit=False,
        restored_at=None,
    )


@pytest.mark.asyncio
async def test_inventory_aliases_groups_and_build_plan(monkeypatch):
    server = _server(enable_a2s_monitoring=False)
    manager = _Manager(
        commands=[
            (
                True,
                "metamod\t/srv/cs2/cs2/game/csgo/addons/metamod/one.vdf\tstat\n"
                "counterstrikesharp\t/srv/cs2/cs2/game/csgo/addons/counterstrikesharp/plugins/Two\tstat\n",
                "",
            ),
            (True, "a" * 64 + "\n", ""),
            (True, "b" * 64 + "\n", ""),
        ]
    )
    monkeypatch.setattr(module, "SSHManager", lambda: manager)
    candidates = await module._inventory(server)
    assert [item["name"] for item in candidates] == ["Two", "one.vdf"]
    assert manager.disconnected == 1

    managed = [
        SimpleNamespace(
            id=1,
            market_plugin_id=11,
            display_name="One",
            repo_url="https://github.com/acme/one",
            custom_install_path=None,
        ),
        SimpleNamespace(
            id=2,
            market_plugin_id=None,
            display_name="ignored",
            repo_url=None,
            custom_install_path=None,
        ),
    ]
    links = module._link_managed_plugins(managed, {"metamod:one.vdf": {"one"}})
    assert links == {11: "metamod:one.vdf"}
    db = _Db(rows=managed)
    market = SimpleNamespace(id=11, dependencies="12")
    dependent = SimpleNamespace(id=12, dependencies=None)
    monkeypatch.setattr(module.MarketPlugin, "get_by_ids", AsyncMock(return_value=[market, dependent]))
    groups = await module._group_candidates(
        db,
        7,
        [
            {"key": "metamod:one.vdf", "name": "one.vdf"},
            {"key": "counterstrikesharp:Two", "name": "Two"},
        ],
    )
    assert len(groups) == 2

    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(module, "_inventory", AsyncMock(return_value=candidates))
    monkeypatch.setattr(module, "_group_candidates", AsyncMock(return_value=[]))
    plan = await module.build_diagnostic_plan(db, SimpleNamespace(id=3), 7, "metamod")
    assert plan["candidates"] and plan["warnings"] == []
    monkeypatch.setattr(module, "_inventory", AsyncMock(return_value=[candidates[1]]))
    empty = await module.build_diagnostic_plan(db, SimpleNamespace(id=3), 7, "counterstrikesharp")
    assert empty["warnings"]


@pytest.mark.asyncio
async def test_inventory_connection_and_command_failures(monkeypatch):
    failed_connect = _Manager()
    failed_connect.connect = AsyncMock(return_value=(False, "no route"))
    monkeypatch.setattr(module, "SSHManager", lambda: failed_connect)
    with pytest.raises(RuntimeError, match="SSH connection failed"):
        await module._inventory(_server())
    assert failed_connect.disconnected == 0

    failed_command = _Manager(commands=[(False, "", "permission denied")])
    monkeypatch.setattr(module, "SSHManager", lambda: failed_command)
    with pytest.raises(RuntimeError, match="permission denied"):
        await module._inventory(_server())


@pytest.mark.asyncio
async def test_recommendation_and_path_move_console_and_step(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=3)
    db = _Db()
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    monitor = SimpleNamespace(get_restart_info=lambda _id: {"restart_count": 2, "can_restart": False, "max_restarts": 3})
    monitor_module = SimpleNamespace(server_monitor=monitor)
    monkeypatch.setitem(__import__("sys").modules, "services.server_monitor", monitor_module)
    result = await module.get_diagnostic_recommendation(db, user, 7)
    assert result["recommended"] and result["reason"] == "restart_loop_protection"

    manager = _Manager(commands=[(True, "", "")])
    entry = _entry()
    await module._move_entry(manager, server, entry, quarantine=True)
    assert entry.is_quarantined
    manager.commands = [(False, "", "move failed")]
    with pytest.raises(RuntimeError, match="move failed"):
        await module._move_entry(manager, server, entry, quarantine=False)

    entries = {entry.candidate_key: entry}
    await module._set_candidates(db, manager, server, user, entries, [entry.candidate_key], quarantine=True)
    assert not db.added
    entry.is_quarantined = False
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    manager.commands = [(True, "", "")]
    await module._set_candidates(db, manager, server, user, entries, [entry.candidate_key], quarantine=True)
    assert db.commits == 1

    manager.commands = [(True, "12\n", ""), (True, "fatal error secret\nnormal\n", "")]
    assert await module._console_size(manager, server) == 12
    assert "fatal" in await module._console_delta(manager, server, 12)
    manager.commands = [(False, "not numeric", "")]
    assert await module._console_size(manager, server) == 0

    run = SimpleNamespace(id="run", start_attempts=0)
    await module._record_step(db, run, "phase", ["one"], True, {"ok": True})
    assert db.added[-1].sequence == 1


@pytest.mark.asyncio
async def test_health_attempt_covers_a2s_and_limits(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=3)
    db = _Db(user=user)
    manager = _Manager(
        commands=[(True, "0\n", ""), (True, "fatal error: token\n", "")]
    )
    run = SimpleNamespace(
        id="run",
        requested_by=3,
        server_id=7,
        start_attempts=0,
        health_policy={"a2s_required": True},
    )
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(module, "_record_step", AsyncMock())
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        module.a2s_service,
        "query_server_info",
        AsyncMock(side_effect=[(True, {}), (False, "offline")]),
    )
    progress = AsyncMock()
    assert not await module._health_attempt(db, run, server, manager, "phase", ["one"], progress)
    assert run.start_attempts == 1
    assert progress.await_count >= 2

    run.start_attempts = module.MAX_START_ATTEMPTS
    with pytest.raises(RuntimeError, match="limit"):
        await module._health_attempt(db, run, server, manager, "phase", [], None)
    db.user = None
    run.start_attempts = 0
    with pytest.raises(module.AgentAccessDenied):
        await module._health_attempt(db, run, server, manager, "phase", [], None)


@pytest.mark.asyncio
async def test_group_suspect_and_strict_fallback_paths(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=3)
    db = _Db()
    manager = _Manager()
    entries = {key: _entry(key) for key in ("a", "b", "c")}
    monkeypatch.setattr(module, "authorized_server", AsyncMock())
    monkeypatch.setattr(module, "_set_candidates", AsyncMock())
    health = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(module, "_health_attempt", health)
    monkeypatch.setattr(module.time, "monotonic", lambda: 0.0)
    suspect = await module._run_group_isolation(
        db, user, server, manager, SimpleNamespace(start_attempts=0), entries,
        {"ga": ["a"], "gb": ["b"], "gc": ["c"]}, 0.0, None
    )
    assert suspect == "gb"

    run = SimpleNamespace(start_attempts=0, culprit_keys=[], status="running", error=None)
    health.side_effect = [False, True]
    await module._run_strict_fallback(
        db, user, server, manager, run, entries,
        {"ga": ["a"], "gb": ["b"]}, "ga", ["a", "b"], 0.0, None
    )
    assert run.status == "completed_with_quarantine"
    assert run.culprit_keys == ["b"]

    run = SimpleNamespace(start_attempts=0, culprit_keys=[], status="running", error=None)
    health.side_effect = [False, True]
    await module._run_suspect_analysis(
        db, user, server, manager, run, entries,
        {"ga": ["a"], "gb": ["b"]}, "ga", ["a", "b"], 0.0, None
    )
    assert run.status == "completed_with_quarantine" and run.culprit_keys == ["a"]


@pytest.mark.asyncio
async def test_payload_latest_run_restore_and_interrupt(monkeypatch):
    run = SimpleNamespace(
        id="run", server_id=7, requested_by=3, scope="both", status="completed",
        plan_hash="hash", culprit_keys=None, start_attempts=2, error=None,
        created_at=None, completed_at=None, original_server_running=True,
    )
    step = SimpleNamespace(sequence=1, phase="p", candidate_keys=["a"], healthy=True, evidence={})
    entry = SimpleNamespace(candidate_key="a", source_relative_path="a", is_quarantined=True, is_culprit=True)
    class _PayloadDb(_Db):
        def __init__(self):
            super().__init__(scalar=run)
            self.calls = 0

        async def execute(self, _query):
            self.calls += 1
            return _Result([step] if self.calls % 2 else [entry], self.scalar)

    db = _PayloadDb()
    payload = await module.diagnostic_run_payload(db, run)
    assert payload["steps"][0]["phase"] == "p" and payload["quarantine"][0]["is_culprit"]
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=_server()))
    class _LatestDb(_Db):
        def __init__(self, scalar):
            super().__init__(scalar=scalar)
            self.calls = 0

        async def execute(self, _query):
            self.calls += 1
            if self.calls == 1:
                return _Result(scalar=self.scalar)
            return _Result([step] if self.calls == 2 else [entry])

    latest_db = _LatestDb(run)
    latest = await module.get_latest_diagnostic_run(latest_db, SimpleNamespace(id=3), 7)
    assert latest["id"] == "run"
    with pytest.raises(LookupError):
        latest_db = _LatestDb(None)
        latest_db.scalar = None
        await module.get_latest_diagnostic_run(latest_db, SimpleNamespace(id=3), 7)

    class _RestoreDb(_Db):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def execute(self, _query):
            self.calls += 1
            return _Result(scalar=run) if self.calls == 1 else _Result([entry])

    restore_db = _RestoreDb()
    restore_manager = _Manager()
    monkeypatch.setattr(module, "SSHManager", lambda: restore_manager)
    monkeypatch.setattr(module, "_move_entry", AsyncMock())
    monkeypatch.setattr(module, "diagnostic_run_payload", AsyncMock(return_value={"status": "restored"}))
    monkeypatch.setattr(module.maintenance_lock_service, "get", lambda *_args, **_kwargs: _Lock())
    restored = await module.restore_diagnostic_run(restore_db, SimpleNamespace(id=3), 7, "run")
    assert restored["status"] == "restored" and run.status == "restored"

    active = [SimpleNamespace(server_id=7, status="running"), SimpleNamespace(server_id=8, status="completed")]
    interrupt_db = _Db(rows=active)
    database_module = __import__("modules.database", fromlist=["async_session_maker"])
    monkeypatch.setattr(database_module, "async_session_maker", lambda: interrupt_db)
    assert await module.interrupt_active_plugin_diagnostics() == 1
    assert active[0].status == "interrupted"


@pytest.mark.asyncio
async def test_execute_plan_rejects_stale_and_empty_plan(monkeypatch):
    db = _Db()
    user = SimpleNamespace(id=3)
    monkeypatch.setattr(module.maintenance_lock_service, "get", lambda *_args, **_kwargs: _Lock())
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=_server()))
    monkeypatch.setattr(module, "build_diagnostic_plan", AsyncMock(return_value={"plan_hash": "new", "candidates": []}))
    with pytest.raises(ValueError, match="changed"):
        await module.execute_diagnostic_plan(db, user, 7, "both", "old")
    monkeypatch.setattr(module, "build_diagnostic_plan", AsyncMock(return_value={"plan_hash": "same", "candidates": []}))
    with pytest.raises(ValueError, match="No plugin"):
        await module.execute_diagnostic_plan(db, user, 7, "both", "same")


@pytest.mark.asyncio
async def test_execute_plan_success_baseline_failure_and_connect_failure(monkeypatch):
    user = SimpleNamespace(id=3)
    server = _server(enable_a2s_monitoring=True)
    plan = {
        "plan_hash": "same",
        "candidates": [{"key": "metamod:one", "relative_path": "addons/one", "revision": "a" * 64}],
        "candidate_groups": [{"key": "group:one", "candidate_keys": ["metamod:one"]}],
        "health_policy": {"a2s_required": True},
    }

    class _PlanDb(_Db):
        async def refresh(self, value):
            if getattr(value, "id", None) is None:
                value.id = "run"

    class _PlanManager(_Manager):
        def __init__(self, connected=True):
            super().__init__()
            self.connected_result = connected

        async def get_server_status(self, _server):
            return True, "running"

        async def connect(self, _server):
            return self.connected_result, "cannot connect" if not self.connected_result else "ok"

    monkeypatch.setattr(module.maintenance_lock_service, "get", lambda *_args, **_kwargs: _Lock())
    monkeypatch.setattr(module, "authorized_server", AsyncMock(return_value=server))
    monkeypatch.setattr(module, "build_diagnostic_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(module.a2s_service, "query_server_info", AsyncMock(return_value=(True, {})))
    monkeypatch.setattr(module, "_record_step", AsyncMock())
    monkeypatch.setattr(module, "_set_candidates", AsyncMock())
    monkeypatch.setattr(module, "_run_group_isolation", AsyncMock(return_value="group:one"))
    monkeypatch.setattr(module, "_health_attempt", AsyncMock(return_value=True))

    async def finish_suspect(_db, _user, _server, _manager, run, *_args):
        run.status = "completed_with_quarantine"

    monkeypatch.setattr(module, "_run_suspect_analysis", finish_suspect)
    monkeypatch.setattr(module, "diagnostic_run_payload", AsyncMock(return_value={"status": "done"}))
    manager = _PlanManager()
    monkeypatch.setattr(module, "SSHManager", lambda: manager)
    progress = AsyncMock()
    result = await module.execute_diagnostic_plan(
        _PlanDb(), user, 7, "both", "same", ai_run_id="ai-1", progress=progress
    )
    assert result["status"] == "done"
    assert manager.disconnected == 1

    unhealthy = _PlanManager()
    monkeypatch.setattr(module, "SSHManager", lambda: unhealthy)
    monkeypatch.setattr(module, "_health_attempt", AsyncMock(return_value=False))
    unhealthy_result = await module.execute_diagnostic_plan(_PlanDb(), user, 7, "both", "same")
    assert unhealthy_result["status"] == "done"

    disconnected = _PlanManager(connected=False)
    monkeypatch.setattr(module, "SSHManager", lambda: disconnected)
    with pytest.raises(RuntimeError, match="SSH connection failed"):
        await module.execute_diagnostic_plan(_PlanDb(), user, 7, "both", "same")
