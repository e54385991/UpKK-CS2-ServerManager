"""Pure unit tests for detached game-session command construction."""

import asyncio
import shlex

import pytest
from pydantic import ValidationError

from api.routes import servers as server_routes
from modules.models import AuthType, Server
from modules.schemas import ServerCreate, ServerUpdate
from services.game_session import (
    TMUX_SOCKET_NAME,
    attach_command,
    availability_command,
    cleanup_command,
    find_running_session_manager,
    find_running_session_managers,
    force_stop_session_command,
    gslt_startup_parameter,
    send_keys_command,
    session_exists_command,
    session_manager_order,
    session_name,
    start_session_command,
    stop_session_command,
)


TMUX_PREFIX = f"tmux -L {TMUX_SOCKET_NAME} -f /dev/null"


def test_new_servers_default_to_tmux_while_explicit_screen_is_preserved():
    model = Server(
        id=1,
        user_id=1,
        name="New server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
    )
    request = ServerCreate(
        name="New server",
        host="127.0.0.1",
        ssh_user="steam",
        ssh_password="secret",
        captcha_token="token",
        captcha_code="1234",
    )

    migrated_server = Server(
        id=2,
        user_id=1,
        name="Migrated server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        session_manager="screen",
    )

    assert model.session_manager == "tmux"
    assert request.session_manager == "tmux"
    assert migrated_server.session_manager == "screen"


def test_schema_accepts_only_supported_session_managers():
    assert ServerUpdate(session_manager="tmux").session_manager == "tmux"
    assert ServerUpdate(session_manager="screen").session_manager == "screen"
    assert ServerUpdate().model_dump(exclude_unset=True) == {}

    with pytest.raises(ValidationError):
        ServerUpdate(session_manager="invalid")

    with pytest.raises(ValidationError):
        ServerUpdate(session_manager=None)


@pytest.mark.parametrize("token", [None, "", " ", "\t\r\n"])
def test_unconfigured_gslt_does_not_create_a_startup_parameter(token):
    assert gslt_startup_parameter(token) is None
    assert ServerUpdate(steam_account_token=token).steam_account_token is None
    create_request = ServerCreate(
        name="Server without GSLT",
        host="127.0.0.1",
        ssh_user="steam",
        ssh_password="secret",
        captcha_token="token",
        captcha_code="1234",
        steam_account_token=token,
    )
    assert create_request.steam_account_token is None


def test_configured_gslt_creates_the_expected_startup_parameter():
    assert (
        gslt_startup_parameter("  ABC123token  ")
        == '+sv_setsteamaccount "ABC123token"'
    )
    assert (
        gslt_startup_parameter("ABC123token", masked=True)
        == '+sv_setsteamaccount "***STEAM_TOKEN***"'
    )


@pytest.mark.parametrize("token", [None, "", " ", "\t\r\n"])
@pytest.mark.asyncio
async def test_startup_preview_omits_gslt_parameter_when_unconfigured(
    monkeypatch,
    token,
):
    server = Server(
        id=42,
        user_id=1,
        name="Preview without GSLT",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        steam_account_token=token,
    )

    async def get_server(*args, **kwargs):
        return server

    monkeypatch.setattr(server_routes, "get_server_with_permission", get_server)
    result = await server_routes.get_startup_command(42, db=None, current_user=None)

    assert "+sv_setsteamaccount" not in result["startup_command"]
    assert "+sv_setsteamaccount" not in result["cs2_command"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manager", "command_marker"),
    [
        ("screen", "screen -dmS cs2server_41"),
        ("tmux", f"{TMUX_PREFIX} new-session -d -s cs2server_41"),
    ],
)
async def test_startup_preview_uses_selected_manager_and_masks_secrets(
    monkeypatch,
    manager,
    command_marker,
):
    server = Server(
        id=41,
        user_id=1,
        name="Preview",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        session_manager=manager,
        api_key="preview-api-secret",
        server_password="preview-server-secret",
        rcon_password="preview-rcon-secret",
        steam_account_token="PREVIEWSTEAMTOKEN",
        cpu_affinity="0-3",
    )

    async def get_server(*args, **kwargs):
        return server

    monkeypatch.setattr(server_routes, "get_server_with_permission", get_server)
    result = await server_routes.get_startup_command(41, db=None, current_user=None)

    assert result["session_manager"] == manager
    assert command_marker in result["startup_command"]
    assert "taskset -c 0-3" in result["startup_command"]
    assert result["startup_command"].index(command_marker) < result["startup_command"].index("taskset")
    for secret in (
        server.api_key,
        server.server_password,
        server.rcon_password,
        server.steam_account_token,
    ):
        assert secret not in result["startup_command"]


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        (
            "screen",
            {
                "available": "command -v screen >/dev/null 2>&1",
                "cleanup": "screen -wipe >/dev/null 2>&1 || true",
                "start": "screen -dmS cs2server_7 bash /srv/run-server.sh",
                "stop": "screen -S cs2server_7 -X quit",
                "attach": "screen -x cs2server_7",
            },
        ),
        (
            "tmux",
            {
                "available": "command -v tmux >/dev/null 2>&1",
                "cleanup": None,
                "start": f"{TMUX_PREFIX} new-session -d -s cs2server_7 bash /srv/run-server.sh",
                "stop": f"{TMUX_PREFIX} kill-session -t =cs2server_7",
                "attach": f"{TMUX_PREFIX} attach-session -t =cs2server_7",
            },
        ),
    ],
)
def test_screen_and_tmux_lifecycle_commands(manager, expected):
    name = session_name(7)

    assert availability_command(manager) == expected["available"]
    assert cleanup_command(manager) == expected["cleanup"]
    assert start_session_command(manager, name, "bash /srv/run-server.sh") == expected["start"]
    assert stop_session_command(manager, name) == expected["stop"]
    assert attach_command(manager, name) == expected["attach"]


def test_session_exists_commands_match_the_complete_session_name():
    screen_command = session_exists_command("screen", "cs2server_1")
    tmux_command = session_exists_command("tmux", "cs2server_1")

    assert screen_command == (
        "screen -list 2>/dev/null | "
        "grep -E '[.]cs2server_1([[:space:]]|$)' >/dev/null"
    )
    assert "grep -F cs2server_1" not in screen_command
    assert tmux_command == (
        f"{TMUX_PREFIX} has-session -t =cs2server_1 2>/dev/null"
    )
    assert "=cs2server_1" in tmux_command


@pytest.mark.parametrize("manager", ["screen", "tmux"])
def test_cpu_affinity_is_applied_to_the_payload(manager):
    payload = "bash /srv/cs2_autorestart.sh --server 12"
    command = start_session_command(
        manager,
        "cs2server_12",
        payload,
        cpu_affinity="0-3, 6",
    )
    affinity_payload = f"taskset -c {shlex.quote('0-3, 6')} {payload}"

    assert affinity_payload in command
    assert not command.startswith("taskset ")
    if manager == "tmux":
        assert command.index("new-session") < command.index("taskset")
    else:
        assert command.index("screen -dmS") < command.index("taskset")


@pytest.mark.parametrize("manager", ["screen", "tmux"])
def test_invalid_session_names_and_cpu_affinity_are_rejected(manager):
    with pytest.raises(ValueError, match="Invalid session name"):
        session_exists_command(manager, "cs2server_1; touch /tmp/owned")

    with pytest.raises(ValueError, match="Invalid CPU affinity"):
        start_session_command(
            manager,
            "cs2server_1",
            "./cs2 -dedicated",
            cpu_affinity="0-3; touch /tmp/owned",
        )


def test_screen_send_keys_shell_quotes_the_complete_literal_input():
    command_text = "say \"hello\"; echo '$HOME'; quit"

    command = send_keys_command("screen", "cs2server_3", command_text)

    assert command == (
        "screen -S cs2server_3 -X stuff "
        f"{shlex.quote(command_text + chr(10))}"
    )


def test_tmux_send_keys_uses_literal_mode_and_a_separate_enter_key():
    command_text = "say \"hello\"; echo '$HOME'; quit"
    quoted_text = shlex.quote(command_text)

    command = send_keys_command("tmux", "cs2server_3", command_text)

    assert command == (
        f"{TMUX_PREFIX} send-keys -t =cs2server_3: -l -- "
        f"{quoted_text} && "
        f"{TMUX_PREFIX} send-keys -t =cs2server_3: Enter"
    )
    assert command.count(quoted_text) == 1


def test_tmux_stop_commands_never_kill_the_shared_tmux_server():
    commands = (
        stop_session_command("tmux", "cs2server_4"),
        force_stop_session_command("tmux", "cs2server_4"),
    )

    for command in commands:
        assert "kill-session" in command
        assert "kill-server" not in command
        assert "pkill" not in command


def test_screen_force_stop_does_not_match_its_own_pkill_command():
    command = force_stop_session_command("screen", "cs2server_4")

    assert "[S]CREEN" in command
    assert "pkill -9 -f 'SCREEN" not in command


@pytest.mark.parametrize(
    ("preferred", "expected_order"),
    [
        ("screen", ("screen", "tmux")),
        ("tmux", ("tmux", "screen")),
    ],
)
def test_fallback_detection_checks_preferred_manager_first(preferred, expected_order):
    name = "cs2server_21"
    calls = []
    fallback = expected_order[1]

    async def execute_command(command, *, timeout):
        calls.append((command, timeout))
        return command == session_exists_command(fallback, name), "", ""

    detected = asyncio.run(
        find_running_session_manager(
            execute_command,
            preferred,
            name,
            timeout=17,
        )
    )

    assert session_manager_order(preferred) == expected_order
    assert [command for command, _ in calls] == [
        session_exists_command(manager, name) for manager in expected_order
    ]
    assert [timeout for _, timeout in calls] == [17, 17]
    assert detected == fallback


@pytest.mark.parametrize("preferred", ["screen", "tmux"])
def test_all_running_managers_are_returned_in_detection_order(preferred):
    name = "cs2server_22"
    expected = list(session_manager_order(preferred))

    async def execute_command(command, *, timeout):
        assert timeout == 10
        return True, "", ""

    detected = asyncio.run(
        find_running_session_managers(execute_command, preferred, name)
    )

    assert detected == expected
