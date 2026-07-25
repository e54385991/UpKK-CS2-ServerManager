"""Deployment progress fan-out shared by HTTP, WebSocket and services."""

from __future__ import annotations

import asyncio

from fastapi import WebSocket

from modules.utils import get_current_time
from services.redis_manager import redis_manager


class DeploymentWebSocket:
    """Track active deployment WebSockets by server."""

    def __init__(self) -> None:
        self.active_connections: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, server_id: int) -> None:
        await websocket.accept()
        self.active_connections.setdefault(server_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, server_id: int) -> None:
        connections = self.active_connections.get(server_id)
        if not connections:
            return
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self.active_connections.pop(server_id, None)

    async def send_message(self, server_id: int, message: dict) -> None:
        connections = list(self.active_connections.get(server_id, ()))
        if not connections:
            return

        async def send_with_timeout(connection: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(connection.send_json(message), timeout=2.0)
                return None
            except Exception:
                return connection

        disconnected = await asyncio.gather(
            *(send_with_timeout(connection) for connection in connections)
        )
        for connection in disconnected:
            if connection is not None:
                self.disconnect(connection, server_id)


deployment_ws = DeploymentWebSocket()


async def send_deployment_update(
    server_id: int,
    msg_type: str,
    message: str,
) -> None:
    """Send a live update and persist it for reconnect recovery."""
    timestamp = get_current_time().isoformat()
    await deployment_ws.send_message(
        server_id,
        {
            "type": msg_type,
            "message": message,
            "timestamp": timestamp,
        },
    )
    await redis_manager.append_deployment_progress(
        server_id,
        msg_type,
        message,
        timestamp,
    )
