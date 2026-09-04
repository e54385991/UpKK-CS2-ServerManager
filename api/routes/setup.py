"""
Server setup automation routes
"""

import asyncio
from typing import List, Optional

import asyncssh
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from api.dependencies import ActiveUser, DatabaseSession
from modules import (
    authenticate_websocket,
    get_current_time,
)
from services.captcha_policy import require_captcha

# Kept as a compatibility alias for integrations that patch the legacy service directly.
from services.captcha_service import captcha_service  # noqa: F401
from services.redis_manager import redis_manager

from .setup_workflow import (
    ServerSetupRequest,
    ServerSetupResponse,
    _configure_setup_user,
    _detect_setup_host,
    _install_legacy_libssl,
    _install_setup_dependencies,
    _persist_setup_configuration,
    _SetupContext,
    generate_secure_password,
)

router = APIRouter(prefix="/api/setup", tags=["setup"])


class SetupWebSocket:
    """WebSocket manager for setup progress updates"""

    def __init__(self):
        self.active_connections: dict[str, tuple[int, WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str, user_id: int):
        """Connect a WebSocket client"""
        existing = self.active_connections.get(session_id)
        if existing is not None and existing[0] != user_id:
            await websocket.close(code=4409, reason="Setup session is already in use")
            return False
        await websocket.accept()
        self.active_connections[session_id] = (user_id, websocket)
        return True

    def disconnect(self, session_id: str, websocket: WebSocket | None = None):
        """Disconnect a WebSocket client"""
        current = self.active_connections.get(session_id)
        if current is not None and (websocket is None or current[1] is websocket):
            del self.active_connections[session_id]

    async def send_message(self, session_id: str, user_id: int, message: dict):
        """Send message to connected client for a session"""
        connection = self.active_connections.get(session_id)
        if connection is not None and connection[0] == user_id:
            try:
                await asyncio.wait_for(connection[1].send_json(message), timeout=2.0)
            except Exception:
                # Connection closed, remove it silently
                self.disconnect(session_id, connection[1])


setup_ws = SetupWebSocket()


# Redis-based schemas
class RedisServerListItem(BaseModel):
    """Schema for Redis-stored server in list (without password)"""

    key: str = Field(..., description="Redis key for this server")
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    game_directory: str
    created_at: float = Field(..., description="Unix timestamp")


class RedisServerDetail(BaseModel):
    """Schema for Redis-stored server detail (with password)"""

    user_id: int
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    ssh_password: str
    game_directory: str
    created_at: float = Field(..., description="Unix timestamp")


async def send_setup_progress(session_id: Optional[str], user_id: int, log_message: str):
    """
    Helper to send setup progress via WebSocket if session_id is provided
    Silently fails if WebSocket connection is not available or fails
    """
    if session_id:
        try:
            await setup_ws.send_message(
                session_id,
                user_id,
                {
                    "type": "log",
                    "message": log_message,
                    "timestamp": get_current_time().isoformat(),
                },
            )
        except Exception:
            # WebSocket failures should not break the main setup flow
            # Silently ignore WebSocket errors
            pass


@router.websocket("/setup-progress/{session_id}")
async def setup_progress_websocket(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time setup progress updates

    Connect to this endpoint before starting auto-setup to receive real-time logs.
    The session_id should be passed to the /auto-setup endpoint.

    Messages format:
    {
        "type": "log",
        "message": "...",
        "timestamp": "2024-01-01T00:00:00"
    }
    """
    user, _ = await authenticate_websocket(websocket)
    if user is None:
        return
    if not await setup_ws.connect(websocket, session_id, user.id):
        return
    try:
        # Send initial connection message
        await websocket.send_json(
            {
                "type": "info",
                "message": "WebSocket 连接已建立，等待设置开始...",
                "timestamp": get_current_time().isoformat(),
            }
        )

        while True:
            # Keep connection alive and receive any client messages
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    except Exception:
        pass
    finally:
        setup_ws.disconnect(session_id, websocket)


@router.post("/auto-setup", response_model=ServerSetupResponse)
async def auto_setup_server(
    setup_req: ServerSetupRequest,
    current_user: ActiveUser,
    db: DatabaseSession,
):
    """Validate the request, run the setup workflow, and return safe credentials."""
    await require_captcha(db, setup_req.captcha_token, setup_req.captcha_code)

    await db.commit()
    logs: list[str] = []
    conn: asyncssh.SSHClientConnection | None = None

    async def add_log(message: str) -> None:
        logs.append(message)
        await send_setup_progress(setup_req.session_id, current_user.id, message)

    async def add_command_output(output: str) -> None:
        for line in output.strip().splitlines():
            if line.strip():
                await add_log(f"  {line}")

    try:
        cs2_password = setup_req.cs2_password or generate_secure_password()
        await add_log(
            f"正在连接到 {setup_req.host}:{setup_req.ssh_port} (用户: {setup_req.ssh_user})..."
        )
        conn = await asyncssh.connect(
            host=setup_req.host,
            port=setup_req.ssh_port,
            username=setup_req.ssh_user,
            password=setup_req.ssh_password,
            known_hosts=None,
            connect_timeout=15,
        )
        await add_log("✓ SSH 连接成功")
        context = _SetupContext(
            request=setup_req,
            conn=conn,
            add_log=add_log,
            add_command_output=add_command_output,
            cs2_password=cs2_password,
        )
        await _detect_setup_host(context)
        await _install_setup_dependencies(context)
        await _install_legacy_libssl(context)
        await _configure_setup_user(context)
        await add_log("=" * 50)
        await add_log("✓ 服务器环境设置完成！")
        await add_log("=" * 50)
        initialized_server_id = await _persist_setup_configuration(
            context, current_user=current_user, db=db
        )
        return ServerSetupResponse(
            success=True,
            message="服务器环境设置成功",
            cs2_username=setup_req.cs2_username,
            cs2_password=cs2_password,
            game_directory=context.game_directory,
            logs=logs,
            initialized_server_id=initialized_server_id,
            session_id=setup_req.session_id,
        )
    except asyncssh.PermissionDenied:
        await add_log("✗ SSH 认证失败")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="SSH 认证失败，请检查用户名和密码/密钥"
        ) from None
    except asyncio.TimeoutError:
        await add_log("✗ SSH 连接超时")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="SSH 连接超时 - 服务器可能无法访问或响应过慢，请检查网络连接和服务器状态",
        ) from None
    except asyncssh.Error as exc:
        await add_log(f"✗ SSH 错误: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"SSH 连接错误: {exc}"
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        await add_log(f"✗ 未知错误: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"设置失败: {exc}"
        ) from exc
    finally:
        if conn is not None:
            conn.close()
            await conn.wait_closed()


@router.get("/initialized-servers", response_model=List[RedisServerListItem])
async def list_initialized_servers(current_user: ActiveUser):
    """
    List all initialized servers for the current user from Redis (without sensitive credentials)

    **Authentication Required**: User must be logged in.
    Note: Data stored in Redis with 24-hour expiration.
    """
    servers = await redis_manager.get_initialized_servers(current_user.id)

    # Remove sensitive data from list response
    safe_servers = []
    for server in servers:
        safe_server = RedisServerListItem(
            key=server.get("key"),
            name=server.get("name"),
            host=server.get("host"),
            ssh_port=server.get("ssh_port"),
            ssh_user=server.get("ssh_user"),
            game_directory=server.get("game_directory"),
            created_at=server.get("created_at"),
        )
        safe_servers.append(safe_server)

    return safe_servers


@router.delete("/initialized-servers/{server_key:path}")
async def delete_initialized_server(server_key: str, current_user: ActiveUser):
    """
    Delete an initialized server configuration from Redis

    **Authentication Required**: User must be logged in and own the server.
    """
    # Verify ownership by checking if server belongs to user
    server_data = await redis_manager.get_initialized_server(server_key)

    if not server_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Initialized server not found or already expired",
        )

    if server_data.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this server configuration",
        )

    success = await redis_manager.delete_initialized_server(current_user.id, server_key)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete server configuration",
        )

    return {"success": True, "message": "Initialized server deleted successfully"}


@router.get("/initialized-servers/{server_key:path}", response_model=RedisServerDetail)
async def get_initialized_server(server_key: str, current_user: ActiveUser):
    """
    Get a specific initialized server configuration from Redis (including credentials)

    **Authentication Required**: User must be logged in and own the server.
    """
    server_data = await redis_manager.get_initialized_server(server_key)

    if not server_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Initialized server not found or expired (24-hour limit)",
        )

    if server_data.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this server configuration",
        )

    return RedisServerDetail(**server_data)
