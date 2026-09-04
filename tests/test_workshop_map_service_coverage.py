"""覆盖 Workshop 地图工作流的远端预检、计划和原子写入路径。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import workshop_map_service as workshop
from services.map_management_service import (
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    append_map_to_config,
    content_revision,
)


class _Manager:
    def __init__(self, outputs=()):
        self.outputs = iter(outputs)
        self.commands = []
        self.write_file = AsyncMock(return_value=(True, ""))

    async def connect(self, _server):
        return True, "ok"

    async def disconnect(self):
        return None

    async def execute_command(self, command, **_kwargs):
        self.commands.append(command)
        try:
            return next(self.outputs)
        except StopIteration:
            return True, "", ""

    async def read_file(self, _path, _server, **_kwargs):
        return True, DEFAULT_MAPS_CONFIG, ""


def _server(**overrides):
    values = dict(
        id=3,
        user_id=7,
        game_directory="/srv/cs2",
        enable_a2s_monitoring=False,
        status="running",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_fetch_workshop_details_success_and_validation(monkeypatch):
    response = {
        "response": {
            "publishedfiledetails": [{"result": 1, "consumer_app_id": 730, "title": "de_dust 2"}]
        }
    }
    monkeypatch.setattr(workshop.http_helper, "post", AsyncMock(return_value=(True, response, "")))
    result = await workshop.fetch_workshop_details(
        "https://steamcommunity.com/sharedfiles/filedetails/?id=123456"
    )
    assert result == {"workshop_id": "123456", "title": "de_dust 2", "consumer_app_id": 730}
    bad_responses = [
        (False, None, "offline"),
        (True, {}, ""),
        (True, {"response": {"publishedfiledetails": [{"result": 9, "consumer_app_id": 730}]}}, ""),
        (True, {"response": {"publishedfiledetails": [{"result": 1, "consumer_app_id": 440}]}}, ""),
        (
            True,
            {
                "response": {
                    "publishedfiledetails": [
                        {"result": 1, "consumer_app_id": 730, "banned": "1", "title": "x"}
                    ]
                }
            },
            "",
        ),
    ]
    for value in bad_responses:
        monkeypatch.setattr(workshop.http_helper, "post", AsyncMock(return_value=value))
        with pytest.raises(workshop.WorkshopPlanError):
            await workshop.fetch_workshop_details("123456")


@pytest.mark.asyncio
async def test_inspect_read_configs_connect_and_pool(monkeypatch):
    server = _server()
    manager = _Manager([(True, "metamod=1\ncss=1\nmapchooser=0\nmaps=1\nconfig=0\n", "")])
    monkeypatch.setattr(workshop, "SSHManager", lambda: manager)
    connected = await workshop._connect(server)
    assert connected is manager
    state = await workshop._inspect(manager, server)
    assert state == {
        "metamod": True,
        "css": True,
        "mapchooser": False,
        "maps": True,
        "config": False,
    }
    maps, config = await workshop._read_configs(manager, server, state)
    assert maps == DEFAULT_MAPS_CONFIG and config == DEFAULT_PLUGIN_CONFIG_CONTENT
    manager.outputs = iter([(False, "", "inspection failed")])
    with pytest.raises(workshop.WorkshopPlanError, match="inspection failed"):
        await workshop._inspect(manager, server)
    manager.outputs = iter([(True, "", "")])
    with pytest.raises(workshop.WorkshopPlanError, match="SSH connection failed"):
        monkeypatch.setattr(
            workshop,
            "SSHManager",
            lambda: SimpleNamespace(connect=AsyncMock(return_value=(False, "no"))),
        )
        await workshop._connect(server)

    pool_manager = _Manager([(True, "metamod=1\ncss=1\nmapchooser=1\nmaps=1\nconfig=1\n", "")])
    pool_maps = append_map_to_config(DEFAULT_MAPS_CONFIG, name="de_dust2", workshop_id="123")
    pool_manager.read_file = AsyncMock(
        side_effect=[(True, pool_maps, ""), (True, DEFAULT_PLUGIN_CONFIG_CONTENT, "")]
    )
    monkeypatch.setattr(workshop, "SSHManager", lambda: pool_manager)
    assert (await workshop.read_map_pool(server))[0]["workshop_id"] == "123"


@pytest.mark.asyncio
async def test_build_plan_and_atomic_replace(monkeypatch):
    server = _server()
    db = SimpleNamespace()
    manager = _Manager()
    state = {"metamod": True, "css": True, "mapchooser": True, "maps": True, "config": True}
    monkeypatch.setattr(
        workshop,
        "fetch_workshop_details",
        AsyncMock(return_value={"workshop_id": "123", "title": "de_dust2", "consumer_app_id": 730}),
    )
    monkeypatch.setattr(workshop, "_connect", AsyncMock(return_value=manager))
    monkeypatch.setattr(workshop, "_inspect", AsyncMock(return_value=state))
    monkeypatch.setattr(
        workshop,
        "_read_configs",
        AsyncMock(return_value=(DEFAULT_MAPS_CONFIG, DEFAULT_PLUGIN_CONFIG_CONTENT)),
    )
    monkeypatch.setattr(workshop, "_find_mapchooser", AsyncMock(return_value=None))
    plan = await workshop.build_workshop_map_plan(
        db, server, {"workshop_id_or_url": "123", "name": "de_dust2", "restricted_times": ""}
    )
    assert plan["blocked"] is False and len(plan["plan_hash"]) == 64
    assert any(step["action"] == "patch_plugin_config" for step in plan["steps"])

    class _Atomic:
        execute_command = AsyncMock(return_value=(True, "", ""))
        write_file = AsyncMock(return_value=(True, ""))

    atomic = _Atomic()
    backup = await workshop._replace_with_backup(
        atomic, server, "/srv/cs2/config.json", "{}", existed=True
    )
    assert backup and atomic.write_file.await_count == 1
    atomic.execute_command = AsyncMock(side_effect=[(False, "", "mkdir failed")])
    with pytest.raises(workshop.WorkshopPlanError, match="mkdir failed"):
        await workshop._replace_with_backup(
            atomic, server, "/srv/cs2/config.json", "{}", existed=False
        )
    assert workshop._plan_hash({"x": 1}) == workshop._plan_hash({"x": 1})
    assert content_revision(DEFAULT_MAPS_CONFIG)


@pytest.mark.asyncio
async def test_configure_map_and_prerequisite_failures(monkeypatch):
    server = _server()
    maps = append_map_to_config(DEFAULT_MAPS_CONFIG, name="de_dust2", workshop_id="123")
    config = DEFAULT_PLUGIN_CONFIG_CONTENT
    state = {"metamod": True, "css": True, "mapchooser": True, "maps": True, "config": True}
    plan = {
        "current": state,
        "revisions": {
            "maps": content_revision(DEFAULT_MAPS_CONFIG),
            "plugin_config": content_revision(config),
        },
        "workshop": {"name": "de_dust2", "workshop_id": "123"},
        "settings": {
            "enabled": True,
            "min_players": 0,
            "only_nominate": False,
            "restricted_times": "",
        },
    }
    manager = _Manager()
    monkeypatch.setattr(workshop, "_inspect", AsyncMock(side_effect=[state, state]))
    monkeypatch.setattr(
        workshop,
        "_read_configs",
        AsyncMock(
            side_effect=[
                (DEFAULT_MAPS_CONFIG, config),
                (
                    maps,
                    config.replace(
                        '"ChangeMapUse_host_workshop_map": false',
                        '"ChangeMapUse_host_workshop_map": true',
                    ),
                ),
            ]
        ),
    )
    monkeypatch.setattr(workshop, "_replace_with_backup", AsyncMock(return_value=None))
    report = AsyncMock()
    completed = await workshop._configure_workshop_map(manager, server, plan, report)
    assert {item["action"] for item in completed} == {"patch_plugin_config", "append_map", "verify"}

    failed = _Manager()
    monkeypatch.setattr(
        workshop,
        "SSHManager",
        lambda: SimpleNamespace(install_metamod=AsyncMock(return_value=(False, "bad"))),
    )
    with pytest.raises(workshop.WorkshopPlanError, match="bad"):
        await workshop._install_workshop_prerequisites(
            SimpleNamespace(),
            server,
            SimpleNamespace(),
            {"current": {"metamod": False, "css": True, "mapchooser": True}, "steps": []},
            report,
            set(),
            None,
        )
    assert failed.commands == []
