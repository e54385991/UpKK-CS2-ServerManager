"""Game-console response capture for custom and AI commands."""

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from modules.models import AuthType, Server
from services import ai_tools, custom_command_service
from services.custom_command_service import execute_custom_commands, read_game_console
from services.game_session import capture_console_command, send_keys_command, session_exists_command


def _server() -> Server:
    return Server(
        id=31,
        user_id=1,
        name="Server",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        session_manager="tmux",
    )


class _ConsoleManager:
    def __init__(self, snapshots: list[tuple[bool, str, str]]) -> None:
        self.snapshots = iter(snapshots)
        self.commands: list[str] = []
        self.disconnected = False

    async def connect(self, _server):
        return True, "connected"

    async def execute_command(self, command, *, timeout=30):
        self.commands.append(command)
        name = "cs2server_31"
        if command == session_exists_command("tmux", name):
            return True, "", ""
        if command == session_exists_command("screen", name):
            return False, "", ""
        if command == send_keys_command("tmux", name, "status"):
            return True, "", ""
        if command in {
            capture_console_command("tmux", name, lines=80),
            capture_console_command("tmux", name, lines=200),
        }:
            return next(self.snapshots)
        raise AssertionError(f"Unexpected command: {command}")

    async def disconnect(self):
        self.disconnected = True


class _Lock(AbstractAsyncContextManager):
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


async def test_game_command_returns_only_new_console_output(monkeypatch):
    before = "Server is running\nplayers : 0 humans"
    after = before + "\nstatus\nhostname: Test Server\nplayers : 2 humans"
    manager = _ConsoleManager(
        [
            (True, before, ""),
            (True, after, ""),
            (True, after, ""),
        ]
    )
    monkeypatch.setattr(custom_command_service, "SSHManager", lambda: manager)
    monkeypatch.setattr(custom_command_service, "GAME_CONSOLE_CAPTURE_POLL_SECONDS", 0)

    result = await execute_custom_commands(
        _server(),
        "game_process",
        "status",
        capture_game_output=True,
    )

    assert result["success"] is True
    assert result["results"] == [
        {
            "index": 1,
            "command": "status",
            "success": True,
            "stdout": "",
            "stderr": "",
            "console_output": "status\nhostname: Test Server\nplayers : 2 humans",
            "console_output_scope": "new_since_command",
        }
    ]
    assert manager.disconnected is True


async def test_game_command_returns_recent_snapshot_when_baseline_capture_fails(monkeypatch):
    recent = "status\nhostname: Test Server"
    manager = _ConsoleManager(
        [
            (False, "", "initial capture failed"),
            (True, recent, ""),
            (True, recent, ""),
        ]
    )
    monkeypatch.setattr(custom_command_service, "SSHManager", lambda: manager)
    monkeypatch.setattr(custom_command_service, "GAME_CONSOLE_CAPTURE_POLL_SECONDS", 0)

    result = await execute_custom_commands(
        _server(),
        "game_process",
        "status",
        capture_game_output=True,
    )

    command_result = result["results"][0]
    assert command_result["success"] is True
    assert command_result["console_output"] == recent
    assert command_result["console_output_scope"] == "recent_snapshot"
    assert "exact delta" in command_result["console_capture_note"]


async def test_agent_can_read_current_game_console_without_sending_input(monkeypatch):
    manager = _ConsoleManager([(True, "\x1b[32mhostname: Test Server\x1b[0m\r\nplayers: 2", "")])
    monkeypatch.setattr(custom_command_service, "SSHManager", lambda: manager)

    result = await read_game_console(_server(), lines=80)

    assert result == {
        "success": True,
        "session_manager": "tmux",
        "lines_requested": 80,
        "content": "hostname: Test Server\nplayers: 2",
    }
    assert send_keys_command("tmux", "cs2server_31", "status") not in manager.commands
    assert manager.disconnected is True


async def test_ai_send_tool_requests_console_output_and_redacts_it(monkeypatch):
    server = _server()
    execute = AsyncMock(
        return_value={
            "success": True,
            "results": [
                {
                    "success": True,
                    "console_output": 'sv_password "do-not-show"',
                }
            ],
        }
    )
    monkeypatch.setattr(ai_tools, "_require_current_server", AsyncMock(return_value=server))
    monkeypatch.setattr(ai_tools, "_require_active_user", AsyncMock())
    monkeypatch.setattr(custom_command_service, "execute_custom_commands", execute)
    monkeypatch.setattr(ai_tools.maintenance_lock_service, "get", lambda *args, **kwargs: _Lock())
    context = ai_tools.ToolContext(
        db=SimpleNamespace(),
        user=SimpleNamespace(id=1),
        server=server,
        emit=AsyncMock(),
    )

    result = await ai_tools.send_game_console_command(
        context,
        ai_tools.GameConsoleCommandInput(command="status"),
    )

    execute.assert_awaited_once_with(
        server,
        "game_process",
        "status",
        capture_game_output=True,
    )
    assert result["results"][0]["console_output"] == "sv_password [REDACTED]"
