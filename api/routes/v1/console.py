"""Versioned game and SSH consoles for the Next.js workspace."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from modules import Server, async_session_maker
from modules.auth import WEB_SESSION_COOKIE, _get_active_user_for_token
from services.game_session import (
    attach_command,
    find_running_session_manager,
    normalize_session_manager,
    session_name,
)
from services.ssh_manager import SSHManager

from .schemas import ConsoleWorkspaceView

router = APIRouter(prefix="/api/v1/servers/{server_id}/console", tags=["v1-console"])


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

    token = websocket.cookies.get(WEB_SESSION_COOKIE)
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
    message: str | None = None,
) -> ConsoleWorkspaceView:
    return ConsoleWorkspaceView(
        server_id=server.id,
        host=str(server.host or ""),
        session_manager=normalize_session_manager(getattr(server, "session_manager", None)),
        ssh_ok=ssh_ok,
        ssh_error=ssh_error,
        game_running=game_running,
        message=message,
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
            active = await find_running_session_manager(
                ssh.execute_command,
                getattr(server, "session_manager", None),
                session_name(server.id),
            )
        except Exception as exc:
            return _workspace(
                server,
                ssh_ok=True,
                game_running=False,
                message=f"Failed to check game session: {exc}",
            )
        return _workspace(server, ssh_ok=True, game_running=bool(active))
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


async def _run_console(
    websocket: WebSocket,
    server: Server,
    *,
    kind: Literal["ssh", "game"],
) -> None:
    ssh = SSHManager()
    process = None
    output_task = None
    try:
        success, error = await ssh.connect(server)
        if not success:
            await websocket.send_json(
                {"type": "error", "message": f"SSH connection failed: {error}"}
            )
            await websocket.close()
            return

        if ssh.conn is None:
            await websocket.send_json(
                {"type": "error", "message": "SSH connection failed: no session"}
            )
            await websocket.close()
            return

        if kind == "game":
            name = session_name(server.id)
            try:
                active_manager = await find_running_session_manager(
                    ssh.execute_command,
                    getattr(server, "session_manager", None),
                    name,
                )
            except Exception as exc:
                await websocket.send_json(
                    {"type": "error", "message": f"Failed to check server status: {exc}"}
                )
                await websocket.close()
                return
            if not active_manager:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Game server is not running. Please start the server first.",
                    }
                )
                await websocket.close()
                return
            process = await ssh.conn.create_process(
                attach_command(active_manager, name),
                term_type="xterm-256color",
                encoding="utf-8",
                errors="replace",
            )
            await websocket.send_json(
                {
                    "type": "connected",
                    "message": f"Connected to CS2 server console on {server.host}",
                }
            )
        else:
            process = await ssh.conn.create_process(
                term_type="xterm-256color",
                encoding="utf-8",
                errors="replace",
            )
            await websocket.send_json(
                {"type": "connected", "message": f"Connected to {server.host}"}
            )

        async def read_output() -> None:
            try:
                while True:
                    output = await process.stdout.read(1024)
                    if output:
                        await websocket.send_json({"type": "output", "data": output})
                    else:
                        break
            except Exception:
                pass

        output_task = asyncio.create_task(read_output())
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = message.get("type")
            if msg_type == "input":
                process.stdin.write(str(message.get("data") or ""))
                await process.stdin.drain()
            elif msg_type == "resize":
                cols = int(message.get("cols") or 80)
                rows = int(message.get("rows") or 24)
                process.change_terminal_size(cols, rows)
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        with suppress(Exception):
            await websocket.send_json({"type": "error", "message": f"Console error: {exc}"})
    finally:
        if output_task is not None:
            output_task.cancel()
            with suppress(asyncio.CancelledError):
                await output_task
        if process is not None:
            with suppress(Exception):
                process.terminate()
                await asyncio.wait_for(process.wait_closed(), timeout=2)
        await ssh.disconnect()
