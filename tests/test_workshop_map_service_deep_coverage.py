"""补充 Workshop 地图服务的计划、回滚和执行异常分支。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules import ServerStatus
from services import workshop_map_service as workshop
from services.map_management_service import (
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    append_map_to_config,
    content_revision,
)


class _Manager:
    def __init__(self, *, inspect=None, reads=None, commands=None):
        self.inspect = inspect
        self.reads = iter(reads or [])
        self.commands = iter(commands or [])
        self.calls = []
        self.writes = []

    async def connect(self, _server):
        return True, "connected"

    async def disconnect(self):
        self.calls.append(("disconnect",))

    async def execute_command(self, command, **_kwargs):
        self.calls.append(("command", command))
        try:
            return next(self.commands)
        except StopIteration:
            if self.inspect is not None:
                return True, self.inspect, ""
            return True, "", ""

    async def read_file(self, path, _server, **_kwargs):
        self.calls.append(("read", path))
        try:
            return next(self.reads)
        except StopIteration:
            return True, DEFAULT_MAPS_CONFIG, ""

    async def write_file(self, path, content, _server):
        self.writes.append((path, content))
        return True, ""


class _Db:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _server(**overrides):
    values = {
        "id": 3,
        "user_id": 7,
        "game_directory": "/srv/cs2",
        "status": "running",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _state(**overrides):
    values = {"metamod": True, "css": True, "mapchooser": True, "maps": True, "config": True}
    values.update(overrides)
    return values


def _plan(state=None, *, warnings=None, blocked=False):
    current = state or _state()
    maps = DEFAULT_MAPS_CONFIG
    config = DEFAULT_PLUGIN_CONFIG_CONTENT
    return {
        "current": current,
        "revisions": {
            "maps": content_revision(maps),
            "plugin_config": content_revision(config),
        },
        "workshop": {"name": "de_dust2", "workshop_id": "123"},
        "settings": {
            "enabled": True,
            "min_players": 0,
            "only_nominate": False,
            "restricted_times": "",
        },
        "steps": [],
        "warnings": warnings or [],
        "blocked": blocked,
        "blocking_reasons": ["blocked"] if blocked else [],
        "plan_hash": "expected",
    }


@pytest.mark.asyncio
async def test_fetch_details_metadata_and_pool_failures(monkeypatch):
    values = [
        {"result": "x", "consumer_app_id": 730, "title": "x"},
        {"result": 1, "consumer_app_id": 730, "title": ""},
        {"result": 1, "consumer_app_id": 730, "banned": "yes", "title": "x"},
    ]
    for item in values:
        monkeypatch.setattr(
            workshop.http_helper,
            "post",
            AsyncMock(
                return_value=(
                    True,
                    {"response": {"publishedfiledetails": [item]}},
                    "",
                )
            ),
        )
        with pytest.raises(workshop.WorkshopPlanError):
            await workshop.fetch_workshop_details("123456")

    server = _server()
    manager = _Manager(inspect="metamod=1\ncss=0\nmapchooser=0\nmaps=0\nconfig=0\n")
    monkeypatch.setattr(workshop, "SSHManager", lambda: manager)
    with pytest.raises(workshop.WorkshopPlanError, match="Install CounterStrikeSharp"):
        await workshop.read_map_pool(server)

    manager = _Manager(inspect="metamod=1\ncss=1\nmapchooser=1\nmaps=1\nconfig=1\n")
    manager.reads = iter([(True, "not a maps file", ""), (True, "{}", "")])
    monkeypatch.setattr(workshop, "SSHManager", lambda: manager)
    with pytest.raises(workshop.WorkshopPlanError, match="Invalid maps.txt"):
        await workshop.read_map_pool(server)


@pytest.mark.asyncio
async def test_read_configs_and_find_market_plugin(monkeypatch):
    server = _server()
    state = _state(maps=True, config=True)
    manager = _Manager(reads=[(False, "", "maps unreadable"), (False, "", "config unreadable")])
    with pytest.raises(workshop.WorkshopPlanError, match="maps.txt"):
        await workshop._read_configs(manager, server, state)
    manager.reads = iter([(True, "maps", ""), (False, "", "config unreadable")])
    with pytest.raises(workshop.WorkshopPlanError, match="MapChooser config"):
        await workshop._read_configs(manager, server, state)

    item = SimpleNamespace(id=4, title="CS2-Upkk-PanelPLG-Mapchooser")
    monkeypatch.setattr(
        workshop.MarketPlugin,
        "search_plugins",
        AsyncMock(return_value=([SimpleNamespace(title="other"), item], 2)),
    )
    assert await workshop._find_mapchooser(SimpleNamespace()) is item
    monkeypatch.setattr(
        workshop.MarketPlugin,
        "search_plugins",
        AsyncMock(return_value=([], 0)),
    )
    assert await workshop._find_mapchooser(SimpleNamespace()) is None


@pytest.mark.asyncio
async def test_build_plan_install_steps_and_conflicts(monkeypatch):
    server = _server()
    db = SimpleNamespace()
    state = _state(metamod=False, css=False, mapchooser=False, maps=False, config=False)
    maps = DEFAULT_MAPS_CONFIG
    config = DEFAULT_PLUGIN_CONFIG_CONTENT.replace(
        '"ChangeMapUse_host_workshop_map": false',
        '"ChangeMapUse_host_workshop_map": true',
    )
    manager = _Manager(reads=[(True, maps, ""), (True, config, "")])
    monkeypatch.setattr(
        workshop,
        "fetch_workshop_details",
        AsyncMock(return_value={"workshop_id": "123", "title": "de_dust2", "consumer_app_id": 730}),
    )
    monkeypatch.setattr(workshop, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(workshop, "_inspect", AsyncMock(return_value=state))
    monkeypatch.setattr(workshop, "_read_configs", AsyncMock(return_value=(maps, config)))
    market = SimpleNamespace(id=8, title=workshop.MAPCHOOSER_MARKET_TITLE)
    monkeypatch.setattr(workshop, "_find_mapchooser", AsyncMock(return_value=market))
    plugin_plan = {
        "plugin": {"id": 8},
        "hard_conflicts": ["conflict"],
        "warnings": [{"rule_id": 2}],
    }
    monkeypatch.setattr(workshop, "build_plugin_install_plan", AsyncMock(return_value=plugin_plan))
    plan = await workshop.build_workshop_map_plan(
        db,
        server,
        {
            "workshop_id_or_url": "123",
            "name": "de_dust2",
            "restricted_times": "",
            "min_players": 3,
        },
    )
    actions = [step["action"] for step in plan["steps"]]
    assert actions.count("install_framework") == 2
    assert "install_market_plugin" in actions
    assert plan["blocked"] is True
    assert plan["hard_conflicts"] == ["conflict"]
    assert len(plan["plan_hash"]) == 64

    monkeypatch.setattr(workshop, "_find_mapchooser", AsyncMock(return_value=None))
    plan = await workshop.build_workshop_map_plan(db, server, {"workshop_id_or_url": "123"})
    assert plan["blocking_reasons"]


@pytest.mark.asyncio
async def test_replace_with_backup_all_failure_paths(monkeypatch):
    server = _server()
    manager = _Manager(commands=[(True, "", ""), (False, "", "backup failed")])
    with pytest.raises(workshop.WorkshopPlanError, match="backup failed"):
        await workshop._replace_with_backup(manager, server, "/srv/config", "{}", existed=True)

    manager = _Manager(commands=[(True, "", "")])
    manager.write_file = AsyncMock(return_value=(False, "write failed"))
    with pytest.raises(workshop.WorkshopPlanError, match="write failed"):
        await workshop._replace_with_backup(manager, server, "/srv/config", "{}", existed=False)

    manager = _Manager(commands=[(True, "", ""), (False, "", "move failed"), (True, "", "")])
    with pytest.raises(workshop.WorkshopPlanError, match="move failed"):
        await workshop._replace_with_backup(manager, server, "/srv/config", "{}", existed=False)
    assert len([call for call in manager.calls if call[0] == "command"]) == 3


@pytest.mark.asyncio
async def test_install_prerequisites_success_and_restart_failures(monkeypatch):
    server = _server()
    user = SimpleNamespace(id=7)
    db = _Db()
    report = AsyncMock()
    installs = []

    class _Installer:
        async def install_metamod(self, _server):
            installs.append("metamod")
            return True, "ok"

        async def install_counterstrikesharp(self, _server):
            installs.append("css")
            return True, "ok"

        async def stop_server(self, _server):
            installs.append("stop")
            return True, "stopped"

        async def start_server(self, _server, _progress):
            installs.append("start")
            return True, "started"

    monkeypatch.setattr(workshop, "SSHManager", _Installer)
    monkeypatch.setattr(workshop, "record_framework_installation", AsyncMock())
    plugin_result = {"success": True, "message": "installed"}
    monkeypatch.setattr(
        workshop, "execute_plugin_install_plan", AsyncMock(return_value=plugin_result)
    )
    plan = _plan(_state(metamod=False, css=False, mapchooser=False))
    plan["plugin_plan"] = {"plugin": {"id": 8}, "plan_hash": "hash"}
    plan["steps"] = [{"action": "restart_server"}]
    completed = await workshop._install_workshop_prerequisites(
        db, server, user, plan, report, {2}, "op-1"
    )
    assert installs == ["metamod", "css", "stop", "start"]
    assert {item["action"] for item in completed} == {
        "install_metamod",
        "install_counterstrikesharp",
        "install_mapchooser",
        "restart_server",
    }
    assert server.status == ServerStatus.RUNNING and db.commits == 1

    class _BadInstaller(_Installer):
        async def stop_server(self, _server):
            return False, "cannot stop"

    monkeypatch.setattr(workshop, "SSHManager", _BadInstaller)
    with pytest.raises(workshop.WorkshopPlanError, match="cannot stop"):
        await workshop._install_workshop_prerequisites(
            db,
            server,
            user,
            {
                "current": {"metamod": True, "css": True, "mapchooser": True},
                "steps": [{"action": "restart_server"}],
            },
            report,
            set(),
            None,
        )

    class _BadStart(_Installer):
        async def start_server(self, _server, _progress):
            return False, "cannot start"

    monkeypatch.setattr(workshop, "SSHManager", _BadStart)
    with pytest.raises(workshop.WorkshopPlanError, match="cannot start"):
        await workshop._install_workshop_prerequisites(
            db,
            server,
            user,
            {
                "current": {"metamod": True, "css": True, "mapchooser": True},
                "steps": [{"action": "restart_server"}],
            },
            report,
            set(),
            None,
        )


@pytest.mark.asyncio
async def test_install_prerequisites_plugin_failure_and_configure_guards(monkeypatch):
    server = _server()
    report = AsyncMock()
    monkeypatch.setattr(
        workshop,
        "execute_plugin_install_plan",
        AsyncMock(return_value={"success": False, "message": "plugin failed"}),
    )
    monkeypatch.setattr(workshop, "SSHManager", lambda: SimpleNamespace())
    with pytest.raises(workshop.WorkshopPlanError, match="plugin failed"):
        await workshop._install_workshop_prerequisites(
            _Db(),
            server,
            SimpleNamespace(),
            {
                "current": {"metamod": True, "css": True, "mapchooser": False},
                "plugin_plan": {"plugin": {"id": 8}, "plan_hash": "hash"},
                "steps": [],
            },
            report,
            set(),
            None,
        )

    manager = _Manager()
    base = _plan()
    monkeypatch.setattr(workshop, "_inspect", AsyncMock(return_value=_state(css=False)))
    with pytest.raises(workshop.WorkshopPlanError, match="Prerequisite verification"):
        await workshop._configure_workshop_map(manager, server, base, report)

    monkeypatch.setattr(workshop, "_inspect", AsyncMock(return_value=_state()))
    changed = append_map_to_config(DEFAULT_MAPS_CONFIG, name="other", workshop_id="999")
    monkeypatch.setattr(
        workshop,
        "_read_configs",
        AsyncMock(return_value=(changed, DEFAULT_PLUGIN_CONFIG_CONTENT)),
    )
    with pytest.raises(workshop.WorkshopPlanError, match="maps.txt changed"):
        await workshop._configure_workshop_map(manager, server, base, report)

    config_changed = DEFAULT_PLUGIN_CONFIG_CONTENT.replace(
        '"ChangeMapUse_host_workshop_map": false',
        '"ChangeMapUse_host_workshop_map": true',
    )
    monkeypatch.setattr(
        workshop,
        "_read_configs",
        AsyncMock(return_value=(DEFAULT_MAPS_CONFIG, config_changed)),
    )
    with pytest.raises(workshop.WorkshopPlanError, match="config changed"):
        await workshop._configure_workshop_map(manager, server, base, report)


@pytest.mark.asyncio
async def test_configure_final_verification_failure_and_execute_guards(monkeypatch):
    server = _server()
    manager = _Manager()
    plan = _plan()
    report = AsyncMock()
    monkeypatch.setattr(workshop, "_inspect", AsyncMock(side_effect=[_state(), _state()]))
    monkeypatch.setattr(
        workshop,
        "_read_configs",
        AsyncMock(side_effect=[(DEFAULT_MAPS_CONFIG, DEFAULT_PLUGIN_CONFIG_CONTENT)] * 2),
    )
    monkeypatch.setattr(workshop, "_replace_with_backup", AsyncMock(return_value=None))
    with pytest.raises(workshop.WorkshopPlanError, match="Final Workshop"):
        await workshop._configure_workshop_map(manager, server, plan, report)

    monkeypatch.setattr(workshop.maintenance_lock_service, "get", lambda *_a, **_k: _Lock())
    db = SimpleNamespace()
    user = SimpleNamespace(id=7, is_admin=False)
    monkeypatch.setattr(workshop.Server, "get_by_id_and_user", AsyncMock(return_value=None))
    with pytest.raises(workshop.WorkshopPlanError, match="permission"):
        await workshop.execute_workshop_map_plan(db, server, user, {"workshop_id_or_url": "123"})

    current = _server()
    monkeypatch.setattr(workshop.Server, "get_by_id_and_user", AsyncMock(return_value=current))
    monkeypatch.setattr(
        workshop, "build_workshop_map_plan", AsyncMock(return_value=_plan(blocked=True))
    )
    with pytest.raises(workshop.WorkshopPlanError, match="blocked"):
        await workshop.execute_workshop_map_plan(db, server, user, {"workshop_id_or_url": "123"})

    valid = _plan(warnings=[{"rule_id": 4}])
    monkeypatch.setattr(workshop, "build_workshop_map_plan", AsyncMock(return_value=valid))
    with pytest.raises(workshop.WorkshopPlanError, match="changed"):
        await workshop.execute_workshop_map_plan(
            db,
            server,
            user,
            {"workshop_id_or_url": "123"},
            expected_plan_hash="different",
        )
    with pytest.raises(workshop.WorkshopPlanError, match="acknowledgement"):
        await workshop.execute_workshop_map_plan(
            db, server, user, {"workshop_id_or_url": "123"}, expected_plan_hash="expected"
        )
