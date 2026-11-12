"""Account isolation, bounded argument rules, and execution-time revocation."""

import copy
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_operation_runner_coverage import _install, _record

from api.routes.v1.operation_runner import server
from modules import create_access_token
from services.automation.commands import AutomationDenied
from services.automation.registry import (
    ArgumentSetting,
    CommandSetting,
    commands_for,
    integer_pattern,
    settings_for,
)

pytest_plugins = ["test_automation_auth"]


def custom(**changes):
    value = dict(
        name="css_custom",
        description="Choose a game mode.",
        enabled=True,
        requires_empty=True,
        requires_online=True,
        arguments=[
            dict(
                name="mode",
                description="Allowed mode.",
                kind="choice",
                choices=["kz", "ze"],
                max_length=8,
            )
        ],
    )
    value.update(changes)
    return value


def test_registry_preserves_defaults_and_explicit_empty_or_disabled_registries():
    user = SimpleNamespace(automation_commands=None)
    assert set(commands_for(user)) == {"css_wsmap", "css_ext_maptime"}
    assert len(settings_for(user)) == 2
    defaults = {item.name: item for item in settings_for(user)}
    assert defaults["css_wsmap"].max_human_players == 1
    assert defaults["css_wsmap"].requires_empty is False
    assert defaults["css_ext_maptime"].max_human_players == 255
    user.automation_commands = []
    assert commands_for(user) == {}
    user.automation_commands = [custom(enabled=False)]
    assert not commands_for(user)
    assert settings_for(user)[0].enabled is False


def test_map_changes_accept_a_human_occupancy_limit_and_keep_saved_empty_rules():
    spec = CommandSetting.model_validate(
        custom(
            name="css_wsmap",
            requires_empty=False,
            max_human_players=1,
            arguments=[
                dict(
                    name="map_name",
                    description="Use 1-128 ASCII letters, digits, or underscores.",
                    kind="token",
                    max_length=128,
                )
            ],
        )
    )
    assert spec.max_human_players == 1
    assert spec.requires_empty is False
    saved = custom(
        name="css_wsmap",
        requires_empty=True,
        arguments=[
            dict(
                name="map_name",
                description="Use 1-128 ASCII letters, digits, or underscores.",
                kind="token",
                max_length=128,
            )
        ],
    )
    saved.pop("max_human_players", None)
    empty = CommandSetting.model_validate(saved)
    assert empty.max_human_players == 0
    assert empty.requires_empty is True


@pytest.mark.parametrize(
    "saved", ["bad", [None], [custom(), custom()], [custom(name="bad;quit")], [custom()] * 101]
)
def test_corrupt_stored_registries_fail_closed(saved):
    with pytest.raises(AutomationDenied, match="registry is invalid"):
        commands_for(SimpleNamespace(automation_commands=saved))


@pytest.mark.parametrize(
    "minimum,maximum",
    [
        (0, 0),
        (1, 9),
        (8, 21),
        (0, 999),
        (132, 713),
        (1, 86400),
        (0, 999999999),
        (1827356, 685843746),
    ],
)
def test_generated_integer_patterns_match_only_the_requested_canonical_range(minimum, maximum):
    pattern = integer_pattern(minimum, maximum)
    assert len(pattern) <= 512
    samples = set(range(0, 1000)) | {minimum, maximum, minimum - 1, maximum + 1}
    for value in samples:
        assert (re.fullmatch(pattern, str(value)) is not None) == (minimum <= value <= maximum)
    for text in ("01", "-1", "+1", "１", "1.0", "1;quit", "1\nquit"):
        assert re.fullmatch(pattern, text) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"minimum": 9, "maximum": 2},
        {"minimum": True},
        {"max_length": 129},
        {"kind": "regex"},
        {"kind": "integer", "max_length": 2, "maximum": 300},
        {"kind": "choice", "choices": []},
        {"kind": "choice", "choices": ["x", "x"]},
        {"kind": "choice", "choices": ["x;quit"]},
        {"kind": "token", "choices": ["x" * 129]},
        {"kind": "integer", "choices": ["x;quit"]},
        {"kind": "token", "choices": [""]},
        {"kind": "choice", "choices": ["x" * 128 + str(i) for i in range(32)]},
        {"kind": "choice", "choices": ["a" * 100 + str(i) for i in range(8)]},
    ],
)
def test_argument_definition_rejects_invalid_and_unbounded_rules(changes):
    with pytest.raises(ValidationError):
        ArgumentSetting.model_validate({"name": "value", "description": "Safe value.", **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "css_x;quit"},
        {"requires_online": False},
        {"enabled": "true"},
        {"arguments": [custom()["arguments"][0]] * 2},
        {"name": "css_wsmap", "max_human_players": 256},
        {"name": "css_ext_maptime", "arguments": []},
    ],
)
def test_command_definition_cannot_weaken_fixed_contracts(changes):
    with pytest.raises(ValidationError):
        CommandSetting.model_validate(custom(**changes))


def test_custom_definitions_render_structured_tokens_and_reject_injection():
    spec = CommandSetting.model_validate(custom())
    assert spec.contract().render(["kz"]) == "css_custom kz"
    with pytest.raises(AutomationDenied):
        spec.contract().render(["kz;quit"])
    token = ArgumentSetting(name="map", description="Map token.")
    assert token.contract().validate("ze_test") == "ze_test"
    with pytest.raises(AutomationDenied):
        token.contract().validate("map space")


def test_jwt_profile_settings_round_trip_discovery_scope_and_api_key_cannot_edit(
    automation, monkeypatch
):
    state = automation
    monkeypatch.setattr("api.routes.v1.profile_game_commands.record_audit_event", AsyncMock())
    jwt = {"Authorization": "Bearer " + create_access_token(data={"sub": "7"})}
    path = "/api/v1/profile/game-commands"
    defaults = state.client.get(path, headers=jwt)
    assert defaults.status_code == 200
    assert len(defaults.json()["commands"]) == 2
    for method in ("GET", "PUT"):
        assert (
            state.client.request(
                method,
                path,
                headers=state.key,
                json={"commands": [custom()]} if method == "PUT" else None,
            ).status_code
            == 401
        )
    body = {"commands": [custom()]}
    response = state.client.put(path, headers=jwt, json=body)
    assert response.status_code == 200
    assert response.json()["commands"][0]["arguments"][0]["choices"] == ["kz", "ze"]
    assert state.client.get(path, headers=jwt).json() == response.json()
    entries = state.client.get("/api/v1/automation/game-commands", headers=state.key).json()
    assert [entry["name"] for entry in entries] == ["css_custom"]
    request = {"action": "send_game_command", "command": "css_custom", "args": ["kz"]}
    assert (
        state.client.post(
            "/api/v1/servers/12/automation-operations", headers=state.key, json=request
        ).status_code
        == 202
    )
    assert state.game_enqueue.await_args.kwargs["command"] == "css_custom kz"
    assert (
        state.client.post(
            "/api/v1/servers/13/automation-operations", headers=state.key, json=request
        ).status_code
        == 404
    )
    state.user.automation_commands = [custom(enabled=False)]
    assert state.client.get("/api/v1/automation/game-commands", headers=state.key).json() == []
    state.game_enqueue.reset_mock()
    assert (
        state.client.post(
            "/api/v1/servers/12/automation-operations", headers=state.key, json=request
        ).status_code
        == 422
    )
    state.game_enqueue.assert_not_awaited()
    # A different account never inherits the first account's definitions.
    assert "css_custom" not in commands_for(SimpleNamespace(automation_commands=None))


@pytest.mark.parametrize(
    "body",
    [
        {"commands": [custom(), custom()]},
        {"commands": [custom(requires_online=False)]},
        {"commands": [custom()], "user_id": 99},
        {"commands": [custom(arguments=[])] * 101},
        {
            "commands": [
                custom(
                    arguments=[{"name": "value", "description": "Token.", "choices": ["x" * 129]}]
                )
            ]
        },
        {
            "commands": [
                custom(
                    arguments=[{"name": "value", "description": "Token.", "choices": ["x;quit"]}]
                )
            ]
        },
    ],
)
def test_profile_rejects_invalid_definitions_without_replacing_saved_values(automation, body):
    state = automation
    state.user.automation_commands = [custom()]
    previous = copy.deepcopy(state.user.automation_commands)
    jwt = {"Authorization": "Bearer " + create_access_token(data={"sub": "7"})}
    response = state.client.put("/api/v1/profile/game-commands", headers=jwt, json=body)
    assert response.status_code == 422
    assert state.user.automation_commands == previous


@pytest.mark.asyncio
async def test_worker_reloads_personal_registry_after_lock_and_rejects_revocation(monkeypatch):
    user = SimpleNamespace(id=9, is_active=True, automation_commands=[custom()])
    record = _record(
        action="send_game_command",
        automation_guard="registered_command",
        automation_command={"name": "css_custom", "args": ["kz"]},
    )
    hub, session = _install(monkeypatch, server, record=record, user=user)
    execute = AsyncMock(return_value={"success": True})
    eligible = AsyncMock()
    monkeypatch.setattr(server, "execute_custom_commands", execute)
    monkeypatch.setattr(server, "ensure_eligible", eligible)
    await server.run_game_console_command(operation_id="op-1", command="unsafe;ignored")
    assert execute.await_args.args[1:] == ("game_process", "css_custom kz")
    eligible.assert_awaited_once()
    for updated in (
        SimpleNamespace(id=9, is_active=True, automation_commands=[]),
        SimpleNamespace(id=9, is_active=False),
    ):
        execute.reset_mock()
        calls = 0

        async def get_user(*args, updated=updated):
            nonlocal calls
            calls += 1
            return user if calls == 1 else updated

        session.get = get_user
        await server.run_game_console_command(operation_id="op-1", command="unsafe;ignored")
        execute.assert_not_awaited()
        assert hub.finished[-1][1]["success"] is False
