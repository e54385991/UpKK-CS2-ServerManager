"""Regression tests for session-manager preflight and console cleanup."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from api.routes import actions
from modules import database as database_module
from modules.models import AuthType, Server, ServerStatus
from modules.schemas import ServerAction
from services.game_session import attach_command, availability_command
from services.ssh_manager import SSHManager


def server_fixture(*, session_manager: str = "tmux") -> Server:
    return Server(
        id=73,
        user_id=1,
        name="Session manager regression",
        host="127.0.0.1",
        ssh_user="steam",
        auth_type=AuthType.PASSWORD,
        session_manager=session_manager,
        status=ServerStatus.RUNNING,
    )


class AvailabilityProbeManager(SSHManager):
    def __init__(self, *, available: bool):
        super().__init__()
        self.available = available
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.commands = []

    async def connect(self, server):
        self.connect_calls += 1
        return True, "connected"

    async def disconnect(self):
        self.disconnect_calls += 1

    async def execute_command(self, command, *args, **kwargs):
        self.commands.append((command, kwargs.get("timeout")))
        if command.startswith("test -f "):
            return True, "exists\n", ""
        return self.available, "", ""


@pytest.mark.asyncio
async def test_public_session_manager_preflight_checks_and_disconnects():
    server = server_fixture()
    manager = AvailabilityProbeManager(available=False)

    available, message = await manager.check_session_manager_available(
        server,
        timeout=19,
    )

    assert available is False
    assert "tmux" in message
    assert "not installed" in message
    assert manager.connect_calls == 1
    assert manager.disconnect_calls == 1
    assert len(manager.commands) == 2
    assert manager.commands[0][0].startswith("test -f ")
    assert manager.commands[0][1] == 19
    assert manager.commands[1] == (availability_command("tmux"), 19)


class StopBeforePreflightProbeManager(SSHManager):
    """Simulate a legacy screen session while the selected tmux is missing."""

    def __init__(self):
        super().__init__()
        self.disconnect_calls = 0
        self.stop_called = False
        self.stray_cleanup_called = False
        self.steamcmd_called = False

    async def connect(self, server):
        return True, "connected"

    async def disconnect(self):
        self.disconnect_calls += 1

    async def _kill_steamcmd_processes(self, *args, **kwargs):
        return None

    async def _running_server_session_managers(self, server, timeout=10):
        return ["screen"]

    async def execute_command(self, command, *args, **kwargs):
        assert command == availability_command("tmux")
        return False, "", "tmux not found"

    async def _stop_server_sessions_connected(self, *args, **kwargs):
        self.stop_called = True
        return True, ["screen"]

    async def _kill_stray_cs2_processes(self, *args, **kwargs):
        self.stray_cleanup_called = True

    async def _execute_steamcmd_with_retry(self, *args, **kwargs):
        self.steamcmd_called = True
        return True, "updated", ""


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update_server", "validate_server"])
async def test_running_server_is_not_stopped_when_selected_manager_is_missing(operation):
    server = server_fixture()
    manager = StopBeforePreflightProbeManager()

    success, message = await getattr(manager, operation)(server)

    assert success is False
    assert "aborted before stopping" in message
    assert "left running" in message
    assert manager.stop_called is False
    assert manager.stray_cleanup_called is False
    assert manager.steamcmd_called is False
    assert manager.disconnect_calls == 1


class FakeDB:
    def __init__(self):
        self.added = []
        self.commit_calls = 0
        self.refresh_calls = []

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, value):
        self.refresh_calls.append(value)


class RestartPreflightProbeManager:
    def __init__(self):
        self.preflight_calls = 0
        self.stop_called = False
        self.start_called = False

    async def check_session_manager_available(self, server):
        self.preflight_calls += 1
        return False, "Selected session manager 'tmux' is not installed"

    async def stop_server(self, server):
        self.stop_called = True
        return True, "stopped"

    async def start_server(self, server, progress_callback=None):
        self.start_called = True
        return True, "started"


@pytest.mark.asyncio
async def test_restart_route_aborts_before_stop_when_preflight_fails(monkeypatch):
    server = server_fixture()
    manager = RestartPreflightProbeManager()
    db = FakeDB()

    async def get_server(*args, **kwargs):
        return server

    async def no_deployment_lock(*args, **kwargs):
        return None

    async def no_op(*args, **kwargs):
        return None

    monkeypatch.setattr(actions, "get_server_and_verify_ownership", get_server)
    monkeypatch.setattr(actions, "SSHManager", lambda: manager)
    monkeypatch.setattr(actions.redis_manager, "get", no_deployment_lock)
    monkeypatch.setattr(actions.redis_manager, "clear_deployment_progress", no_op)
    monkeypatch.setattr(actions.redis_manager, "set_server_status", no_op)
    monkeypatch.setattr(actions, "send_deployment_update", no_op)
    monkeypatch.setattr(actions, "send_discord_action_notification", no_op)

    response = await actions.server_action(
        server.id,
        ServerAction(action="restart"),
        db,
        SimpleNamespace(id=server.user_id, is_admin=False),
        server,
    )

    assert response.success is False
    assert "aborted before stopping" in response.message
    assert response.data == {"status": ServerStatus.RUNNING.value}
    assert manager.preflight_calls == 1
    assert manager.stop_called is False
    assert manager.start_called is False
    assert server.status == ServerStatus.RUNNING


class BlockingStdout:
    def __init__(self, events):
        self.events = events

    async def read(self, size):
        self.events.append("reader_started")
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.events.append("reader_cancelled")
            raise


class ConsoleProcess:
    def __init__(self, events):
        self.events = events
        self.stdout = BlockingStdout(events)
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_closed_calls = 0

    def terminate(self):
        self.terminate_calls += 1
        self.events.append("process_terminated")

    def kill(self):
        self.kill_calls += 1
        self.events.append("process_killed")

    async def wait_closed(self):
        self.wait_closed_calls += 1
        self.events.append("process_waited")


class ConsoleConnection:
    def __init__(self, process):
        self.process = process
        self.commands = []

    async def create_process(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return self.process


class ConsoleSSHManager:
    def __init__(self, process, events):
        self.conn = ConsoleConnection(process)
        self.events = events
        self.disconnect_calls = 0

    async def connect(self, server):
        return True, "connected"

    async def execute_command(self, command, *args, **kwargs):
        return True, "", ""

    async def disconnect(self):
        self.disconnect_calls += 1
        self.events.append("ssh_disconnected")


class FakeSessionContext:
    def __init__(self, server):
        self.server = server

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, server_id):
        assert server_id == self.server.id
        return self.server


class DisconnectingWebSocket:
    def __init__(self, events, disconnect_mode):
        self.events = events
        self.disconnect_mode = disconnect_mode
        self.accepted = False
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        self.messages.append(message)

    async def receive_text(self):
        while "reader_started" not in self.events:
            await asyncio.sleep(0)
        if self.disconnect_mode == "exception":
            raise WebSocketDisconnect(code=1000)
        return json.dumps({"type": "disconnect"})


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_mode", ["message", "exception"])
async def test_game_console_disconnect_cleans_reader_process_and_ssh(
    monkeypatch,
    disconnect_mode,
):
    server = server_fixture()
    events = []
    process = ConsoleProcess(events)
    manager = ConsoleSSHManager(process, events)
    websocket = DisconnectingWebSocket(events, disconnect_mode)

    async def find_running(*args, **kwargs):
        return "tmux"

    async def authenticate(*args, **kwargs):
        return SimpleNamespace(id=server.user_id, is_admin=False), server

    monkeypatch.setattr(
        database_module,
        "async_session_maker",
        lambda: FakeSessionContext(server),
    )
    monkeypatch.setattr(actions, "SSHManager", lambda: manager)
    monkeypatch.setattr(actions, "find_running_session_manager", find_running)
    monkeypatch.setattr(actions, "authenticate_websocket", authenticate)

    await actions.game_console_websocket(websocket, server.id)

    assert websocket.accepted is True
    assert websocket.messages[0]["type"] == "connected"
    assert manager.conn.commands[0][0] == attach_command("tmux", f"cs2server_{server.id}")
    assert process.terminate_calls == 1
    assert process.wait_closed_calls == 1
    assert process.kill_calls == 0
    assert manager.disconnect_calls == 1
    assert events == [
        "reader_started",
        "reader_cancelled",
        "process_terminated",
        "process_waited",
        "ssh_disconnected",
    ]
