"""Versioned game and SSH consoles for the Next.js workspace."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from api.dependencies import ActiveUser, DatabaseSession, StreamUser, require_server_access
from modules import Server, async_session_maker
from modules.auth import _get_active_user_for_token, web_session_cookie_name
from services.game_session import (
    attach_command,
    capture_console_command,
    find_running_session_manager,
    normalize_session_manager,
    session_name,
    steamcmd_session_name,
)
from services.ssh.text import decode_remote_text, encode_console_input
from services.ssh_manager import SSHManager
from services.steamcmd_session import latest_console_heartbeat

from .schemas import ConsolePaneView, ConsoleWorkspaceView

router = APIRouter(prefix="/api/v1/servers/{server_id}/console", tags=["v1-console"])


def _strict_session_manager(value: str) -> Literal["screen", "tmux"]:
    return "screen" if value == "screen" else "tmux"


def _optional_session_manager(value: str | None) -> Literal["screen", "tmux"] | None:
    if value == "screen":
        return "screen"
    if value == "tmux":
        return "tmux"
    return None


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    origin_host = urlsplit(origin).netloc.lower()
    candidates = [
        websocket.headers.get("host"),
        websocket.headers.get("x-forwarded-host"),
    ]
    allowed = {item.split(",")[0].strip().lower() for item in candidates if item}
    return origin_host in allowed


async def _authenticate_console(
    websocket: WebSocket,
    server_id: int,
) -> Server | None:
    """Cookie-auth a console socket. Origin must match Host or X-Forwarded-Host."""
    if not _origin_allowed(websocket):
        await websocket.close(code=4403, reason="Invalid WebSocket origin")
        return None

    token = websocket.cookies.get(web_session_cookie_name())
    if not token:
        await websocket.close(code=4401, reason="Authentication required")
        return None

    async with async_session_maker() as db:
        user = await _get_active_user_for_token(token, db)
        if user is None:
            await websocket.close(code=4401, reason="Invalid or expired session")
            return None
        try:
            server = await require_server_access(db, server_id, user)
        except HTTPException:
            await websocket.close(code=4404, reason="Server not found")
            return None
        return server


def _workspace(
    server: Server,
    *,
    ssh_ok: bool,
    ssh_error: str | None = None,
    game_running: bool = False,
    steamcmd_running: bool = False,
    message: str | None = None,
) -> ConsoleWorkspaceView:
    preferred_manager = _strict_session_manager(
        normalize_session_manager(getattr(server, "session_manager", None))
    )
    return ConsoleWorkspaceView(
        server_id=server.id,
        host=str(server.host or ""),
        session_manager=preferred_manager,
        ssh_ok=ssh_ok,
        ssh_error=ssh_error,
        game_running=game_running,
        steamcmd_running=steamcmd_running,
        message=message,
    )


def _session_name_for(kind: Literal["game", "steamcmd"], server_id: int) -> str:
    return session_name(server_id) if kind == "game" else steamcmd_session_name(server_id)


async def _capture_session_pane(
    ssh: SSHManager,
    server: Server,
    kind: Literal["game", "steamcmd"],
) -> ConsolePaneView:
    name = _session_name_for(kind, int(server.id))
    preferred_value = normalize_session_manager(getattr(server, "session_manager", None))
    preferred = _optional_session_manager(preferred_value)
    manager = await find_running_session_manager(ssh.execute_command, preferred, name)
    if not manager:
        return ConsolePaneView(
            server_id=int(server.id),
            kind=kind,
            session_name=name,
            session_manager=None,
            ssh_ok=True,
            running=False,
            text="",
            heartbeat=None,
        )
    resolved = _strict_session_manager(normalize_session_manager(manager))
    success, stdout, stderr = await ssh.execute_command(
        capture_console_command(resolved, name, lines=200),
        timeout=15,
    )
    text = stdout or ""
    heartbeat = latest_console_heartbeat(text)
    if not success:
        return ConsolePaneView(
            server_id=int(server.id),
            kind=kind,
            session_name=name,
            session_manager=resolved,
            ssh_ok=True,
            running=True,
            text="",
            heartbeat=heartbeat,
            message=stderr or "Failed to capture session pane",
        )
    return ConsolePaneView(
        server_id=int(server.id),
        kind=kind,
        session_name=name,
        session_manager=resolved,
        ssh_ok=True,
        running=True,
        text=text,
        heartbeat=heartbeat,
    )


@router.get("", response_model=ConsoleWorkspaceView)
async def get_console_workspace(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ConsoleWorkspaceView:
    """Report SSH and game-session reachability. SSH failures stay 200."""
    server = await require_server_access(db, server_id, current_user)
    ssh = SSHManager()
    try:
        success, error = await ssh.connect(server)
        if not success:
            return _workspace(
                server,
                ssh_ok=False,
                ssh_error=error or "SSH connection failed",
            )
        try:
            preferred = getattr(server, "session_manager", None)
            active = await find_running_session_manager(
                ssh.execute_command,
                preferred,
                session_name(server.id),
            )
            steamcmd = await find_running_session_manager(
                ssh.execute_command,
                preferred,
                steamcmd_session_name(int(server.id)),
            )
        except Exception as exc:
            return _workspace(
                server,
                ssh_ok=True,
                game_running=False,
                steamcmd_running=False,
                message=f"Failed to check game session: {exc}",
            )
        return _workspace(
            server,
            ssh_ok=True,
            game_running=bool(active),
            steamcmd_running=bool(steamcmd),
        )
    finally:
        await ssh.disconnect()


@router.get("/pane", response_model=ConsolePaneView)
async def get_console_pane(
    server_id: int,
    db: DatabaseSession,
    current_user: StreamUser,
    kind: Literal["game", "steamcmd"] = Query(default="game"),
) -> ConsolePaneView:
    """Snapshot the live game or SteamCMD tmux/screen pane."""
    server = await require_server_access(db, server_id, current_user)
    ssh = SSHManager()
    name = _session_name_for(kind, server_id)
    try:
        success, error = await ssh.connect(server)
        if not success:
            return ConsolePaneView(
                server_id=server_id,
                kind=kind,
                session_name=name,
                ssh_ok=False,
                running=False,
                message=error or "SSH connection failed",
            )
        return await _capture_session_pane(ssh, server, kind)
    except Exception as exc:
        return ConsolePaneView(
            server_id=server_id,
            kind=kind,
            session_name=name,
            ssh_ok=True,
            running=False,
            message=f"Failed to capture session pane: {exc}",
        )
    finally:
        await ssh.disconnect()


@router.websocket("/ssh")
async def ssh_console_websocket(websocket: WebSocket, server_id: int) -> None:
    server = await _authenticate_console(websocket, server_id)
    if server is None:
        return
    await websocket.accept()
    await _run_console(websocket, server, kind="ssh")


@router.websocket("/game")
async def game_console_websocket(websocket: WebSocket, server_id: int) -> None:
    server = await _authenticate_console(websocket, server_id)
    if server is None:
        return
    await websocket.accept()
    await _run_console(websocket, server, kind="game")


async def _start_console_session(websocket: WebSocket, ssh: SSHManager, server: Server, kind: str):
    success, error = await ssh.connect(server)
    if not success:
        await websocket.send_json({"type": "error", "message": f"SSH connection failed: {error}"})
        await websocket.close()
        return None
    if ssh.conn is None:
        await websocket.send_json({"type": "error", "message": "SSH connection failed: no session"})
        await websocket.close()
        return None
    if kind == "game":
        name = session_name(server.id)
        try:
            active_manager = await find_running_session_manager(
                ssh.execute_command, getattr(server, "session_manager", None), name
            )
        except Exception as exc:
            await websocket.send_json(
                {"type": "error", "message": f"Failed to check server status: {exc}"}
            )
            await websocket.close()
            return None
        if not active_manager:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Game server is not running. Please start the server first.",
                }
            )
            await websocket.close()
            return None
        process = await ssh.create_interactive_process(attach_command(active_manager, name))
        message = f"Connected to CS2 server console on {server.host}"
    else:
        process = await ssh.create_interactive_process()
        message = f"Connected to {server.host}"
    await websocket.send_json({"type": "connected", "message": message})
    return process


async def _relay_console_input(websocket: WebSocket, process) -> None:
    async def read_output() -> None:
        try:
            while True:
                output = await process.stdout.read(1024)
                if not output:
                    return
                await websocket.send_json({"type": "output", "data": decode_remote_text(output)})
        except Exception:
            return

    output_task = asyncio.create_task(read_output())
    try:
        while True:
            try:
                message = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                continue
            msg_type = message.get("type")
            if msg_type == "input":
                process.stdin.write(encode_console_input(str(message.get("data") or "")))
                await process.stdin.drain()
            elif msg_type == "resize":
                process.change_terminal_size(
                    int(message.get("cols") or 80), int(message.get("rows") or 24)
                )
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "disconnect":
                return
    finally:
        output_task.cancel()
        with suppress(asyncio.CancelledError):
            await output_task


async def _run_console(
    websocket: WebSocket,
    server: Server,
    *,
    kind: Literal["ssh", "game"],
) -> None:
    ssh = SSHManager()
    process = None
    try:
        process = await _start_console_session(websocket, ssh, server, kind)
        if process is None:
            return
        await _relay_console_input(websocket, process)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        with suppress(Exception):
            await websocket.send_json({"type": "error", "message": f"Console error: {exc}"})
    finally:
        if process is not None:
            with suppress(Exception):
                process.terminate()
                await asyncio.wait_for(process.wait_closed(), timeout=2)
        await ssh.disconnect()
