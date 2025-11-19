"""
Server actions routes with WebSocket support for real-time deployment status
"""
from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from datetime import datetime, timezone, timedelta
import asyncio
import json

from modules import (
    Server, DeploymentLog, ServerStatus,
    ServerAction, ActionResponse, DeploymentLogResponse,
    get_db, User, get_current_active_user
)
from services import SSHManager, redis_manager

router = APIRouter(tags=["actions"])


class DeploymentWebSocket:
    """WebSocket manager for deployment status updates"""
    
    def __init__(self):
        self.active_connections: dict[int, list[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, server_id: int):
        """Connect a WebSocket client"""
        await websocket.accept()
        if server_id not in self.active_connections:
            self.active_connections[server_id] = []
        self.active_connections[server_id].append(websocket)
    
    def disconnect(self, websocket: WebSocket, server_id: int):
        """Disconnect a WebSocket client"""
        if server_id in self.active_connections:
            if websocket in self.active_connections[server_id]:
                self.active_connections[server_id].remove(websocket)
            if not self.active_connections[server_id]:
                del self.active_connections[server_id]
    
    async def send_message(self, server_id: int, message: dict):
        """Send message to all connected clients for a server"""
        if server_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[server_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    disconnected.append(connection)
            
            # Remove disconnected clients
            for connection in disconnected:
                self.disconnect(connection, server_id)


deployment_ws = DeploymentWebSocket()


@router.websocket("/servers/{server_id}/deployment-status")
async def deployment_status_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for real-time deployment status updates
    
    Sends messages in format:
    {
        "type": "status|output|error|complete",
        "message": "...",
        "timestamp": "2024-01-01T00:00:00"
    }
    """
    await deployment_ws.connect(websocket, server_id)
    try:
        while True:
            # Keep connection alive and receive any client messages
            data = await websocket.receive_text()
            # Echo back or handle client messages if needed
            await websocket.send_json({
                "type": "ack",
                "message": "Connected to deployment status stream",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
    except WebSocketDisconnect:
        deployment_ws.disconnect(websocket, server_id)


async def send_deployment_update(server_id: int, msg_type: str, message: str):
    """Helper to send deployment updates via WebSocket"""
    await deployment_ws.send_message(server_id, {
        "type": msg_type,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })


@router.post("/servers/{server_id}/actions", response_model=ActionResponse)
async def server_action(
    server_id: int,
    action_data: ServerAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Execute action on server (deploy, start, stop, restart, status)"""
    result = await db.execute(select(Server).filter(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with ID {server_id} not found"
        )
    
    # Check ownership
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform actions on this server"
        )
    
    ssh_manager = SSHManager()
    action = action_data.action
    
    # Create deployment log
    log = DeploymentLog(
        server_id=server_id,
        action=action,
        status="in_progress"
    )
    db.add(log)
    await db.commit()
    
    # Send WebSocket notification
    await send_deployment_update(server_id, "status", f"Starting action: {action}")
    
    try:
        if action == "deploy":
            server.status = ServerStatus.DEPLOYING
            await db.commit()
            
            await send_deployment_update(server_id, "status", "Connecting to server via SSH...")
            success, message = await ssh_manager.deploy_cs2_server(server, 
                                                                   lambda msg: asyncio.create_task(
                                                                       send_deployment_update(server_id, "output", msg)
                                                                   ))
            
            if success:
                server.status = ServerStatus.STOPPED
                server.last_deployed = datetime.now(timezone.utc)
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Deployment completed successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Deployment failed: {message}")
            
        elif action == "start":
            await send_deployment_update(server_id, "status", "Starting server...")
            success, message = await ssh_manager.start_server(server,
                                                             lambda msg: asyncio.create_task(
                                                                 send_deployment_update(server_id, "output", msg)
                                                             ))
            
            if success:
                server.status = ServerStatus.RUNNING
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Server started successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Start failed: {message}")
        
        elif action == "stop":
            await send_deployment_update(server_id, "status", "Stopping server...")
            success, message = await ssh_manager.stop_server(server)
            
            if success:
                server.status = ServerStatus.STOPPED
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Server stopped successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Stop failed: {message}")
        
        elif action == "restart":
            await send_deployment_update(server_id, "status", "Restarting server...")
            
            # Auto-cleanup crash history based on offline duration
            should_clear_crash_history = False
            
            # Check if auto-clear is configured and server was offline long enough
            if server.auto_clear_crash_hours and server.auto_clear_crash_hours > 0:
                if server.last_status_check:
                    offline_duration = datetime.now(timezone.utc) - server.last_status_check
                    offline_hours = offline_duration.total_seconds() / 3600
                    
                    if offline_hours >= server.auto_clear_crash_hours:
                        should_clear_crash_history = True
                        await send_deployment_update(
                            server_id, 
                            "output", 
                            f"⏰ Server offline for {offline_hours:.1f} hours (threshold: {server.auto_clear_crash_hours}h)"
                        )
                else:
                    # No last status check recorded, assume manual restart should clear
                    should_clear_crash_history = True
            else:
                # Always clear on manual restart if auto-clear is not configured
                should_clear_crash_history = True
            
            # Clean up crash history log if needed
            if should_clear_crash_history:
                try:
                    crash_log_path = f"{server.game_directory}/crash_history.log"
                    cleanup_cmd = f"rm -f {crash_log_path}"
                    await ssh_manager.connect(server)
                    await ssh_manager.execute_command(cleanup_cmd)
                    await ssh_manager.disconnect()
                    await send_deployment_update(server_id, "output", "✓ Crash history cleared for fresh start")
                except Exception as e:
                    # Non-critical, continue with restart
                    await send_deployment_update(server_id, "output", f"Note: Could not clear crash history: {str(e)}")
            else:
                await send_deployment_update(
                    server_id, 
                    "output", 
                    f"ℹ Crash history retained (offline duration below {server.auto_clear_crash_hours}h threshold)"
                )
            
            # Stop then start with additional verification
            success, message = await ssh_manager.stop_server(server)
            
            # Always proceed to start, even if stop reports failure
            # The start_server method has its own defensive checks to kill existing sessions
            if not success:
                await send_deployment_update(server_id, "output", f"Stop returned: {message}")
                await send_deployment_update(server_id, "output", "Proceeding with start (defensive checks will ensure cleanup)...")
            else:
                await send_deployment_update(server_id, "output", "Server stopped successfully, starting again...")
            
            # Add small delay to ensure cleanup
            await asyncio.sleep(0.5)
            
            success, message = await ssh_manager.start_server(server,
                                                             lambda msg: asyncio.create_task(
                                                                 send_deployment_update(server_id, "output", msg)
                                                             ))
            if success:
                server.status = ServerStatus.RUNNING
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Server restarted successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Restart failed: {message}")
        
        elif action == "status":
            await send_deployment_update(server_id, "status", "Checking server status...")
            success, status_msg = await ssh_manager.get_server_status(server)
            
            # Update last status check time
            server.last_status_check = datetime.now(timezone.utc)
            
            if success:
                if status_msg == "running":
                    server.status = ServerStatus.RUNNING
                elif status_msg == "stopped":
                    server.status = ServerStatus.STOPPED
                else:
                    server.status = ServerStatus.UNKNOWN
                
                log.status = "success"
                log.output = status_msg
                message = f"Server is {status_msg}"
                await send_deployment_update(server_id, "complete", message)
            else:
                server.status = ServerStatus.UNKNOWN
                log.status = "failed"
                log.error_message = status_msg
                message = f"Failed to get status: {status_msg}"
                success = False
                await send_deployment_update(server_id, "error", message)
        
        elif action == "update":
            await send_deployment_update(server_id, "status", "Updating server...")
            # Store current status to restore after update
            current_status = server.status
            success, message = await ssh_manager.update_server(server,
                                                              lambda msg: asyncio.create_task(
                                                                  send_deployment_update(server_id, "output", msg)
                                                              ))
            
            if success:
                # Keep the same status as before update (or set to STOPPED if it was running)
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Server updated successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Update failed: {message}")
        
        elif action == "validate":
            await send_deployment_update(server_id, "status", "Updating and validating server...")
            # Store current status to restore after validate
            current_status = server.status
            success, message = await ssh_manager.validate_server(server,
                                                                lambda msg: asyncio.create_task(
                                                                    send_deployment_update(server_id, "output", msg)
                                                                ))
            
            if success:
                # Keep the same status as before validate
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Server validated successfully")
            else:
                server.status = ServerStatus.ERROR
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Validation failed: {message}")
        
        elif action == "install_metamod":
            await send_deployment_update(server_id, "status", "Installing Metamod:Source...")
            success, message = await ssh_manager.install_metamod(server,
                                                                lambda msg: asyncio.create_task(
                                                                    send_deployment_update(server_id, "output", msg)
                                                                ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Metamod installed successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Metamod installation failed: {message}")
        
        elif action == "install_counterstrikesharp":
            await send_deployment_update(server_id, "status", "Installing CounterStrikeSharp...")
            success, message = await ssh_manager.install_counterstrikesharp(server,
                                                                           lambda msg: asyncio.create_task(
                                                                               send_deployment_update(server_id, "output", msg)
                                                                           ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "CounterStrikeSharp installed successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"CounterStrikeSharp installation failed: {message}")
        
        elif action == "update_metamod":
            await send_deployment_update(server_id, "status", "Updating Metamod:Source...")
            success, message = await ssh_manager.update_metamod(server,
                                                               lambda msg: asyncio.create_task(
                                                                   send_deployment_update(server_id, "output", msg)
                                                               ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "Metamod updated successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"Metamod update failed: {message}")
        
        elif action == "update_counterstrikesharp":
            await send_deployment_update(server_id, "status", "Updating CounterStrikeSharp...")
            success, message = await ssh_manager.update_counterstrikesharp(server,
                                                                          lambda msg: asyncio.create_task(
                                                                              send_deployment_update(server_id, "output", msg)
                                                                          ))
            
            if success:
                log.status = "success"
                log.output = message
                await send_deployment_update(server_id, "complete", "CounterStrikeSharp updated successfully")
            else:
                log.status = "failed"
                log.error_message = message
                await send_deployment_update(server_id, "error", f"CounterStrikeSharp update failed: {message}")
        
        await db.commit()
        await db.refresh(server)
        await db.refresh(log)
        
        # Update cache
        await redis_manager.set_server_status(server_id, server.status.value)
        
        return ActionResponse(
            success=success,
            message=message,
            data={"status": server.status.value}
        )
    
    except Exception as e:
        log.status = "failed"
        log.error_message = str(e)
        server.status = ServerStatus.ERROR
        await db.commit()
        
        await send_deployment_update(server_id, "error", f"Action failed: {str(e)}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Action failed: {str(e)}"
        )


@router.get("/servers/{server_id}/logs", response_model=List[DeploymentLogResponse])
async def get_server_logs(
    server_id: int,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get deployment logs for a server"""
    result = await db.execute(select(Server).filter(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server with ID {server_id} not found"
        )
    
    # Check ownership
    if server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view logs for this server"
        )
    
    result = await db.execute(
        select(DeploymentLog)
        .filter(DeploymentLog.server_id == server_id)
        .order_by(DeploymentLog.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    logs = result.scalars().all()
    
    return logs


@router.post("/servers/batch-install-plugins")
async def batch_install_plugins(
    server_ids: List[int],
    plugins: List[str],  # List of plugins: ["metamod", "counterstrikesharp"]
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Install plugins on multiple servers in batch
    
    Args:
        server_ids: List of server IDs to install plugins on
        plugins: List of plugins to install (e.g., ["metamod", "counterstrikesharp"])
    
    Returns:
        Results for each server
    """
    results = {}
    
    for server_id in server_ids:
        # Get server
        result = await db.execute(select(Server).filter(Server.id == server_id))
        server = result.scalar_one_or_none()
        
        if not server:
            results[server_id] = {
                "success": False,
                "message": f"Server with ID {server_id} not found"
            }
            continue
        
        # Check ownership
        if server.user_id != current_user.id:
            results[server_id] = {
                "success": False,
                "message": "Not authorized to perform actions on this server"
            }
            continue
        
        # Install each plugin
        server_results = []
        ssh_manager = SSHManager()
        
        for plugin in plugins:
            # Create deployment log
            log = DeploymentLog(
                server_id=server_id,
                action=f"install_{plugin}",
                status="in_progress"
            )
            db.add(log)
            await db.commit()
            
            try:
                if plugin == "metamod":
                    success, message = await ssh_manager.install_metamod(server)
                elif plugin == "counterstrikesharp":
                    success, message = await ssh_manager.install_counterstrikesharp(server)
                else:
                    success = False
                    message = f"Unknown plugin: {plugin}"
                
                log.status = "success" if success else "failed"
                if success:
                    log.output = message
                else:
                    log.error_message = message
                
                await db.commit()
                
                server_results.append({
                    "plugin": plugin,
                    "success": success,
                    "message": message
                })
            except Exception as e:
                log.status = "failed"
                log.error_message = str(e)
                await db.commit()
                
                server_results.append({
                    "plugin": plugin,
                    "success": False,
                    "message": str(e)
                })
        
        results[server_id] = {
            "success": all(r["success"] for r in server_results),
            "results": server_results
        }
    
    return {
        "success": all(r["success"] for r in results.values()),
        "servers": results
    }


@router.websocket("/servers/{server_id}/ssh-console")
async def ssh_console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for SSH console access
    Provides interactive SSH terminal access to the server
    """
    await websocket.accept()
    
    try:
        # Get server details from database
        from modules.database import async_session_maker
        async with async_session_maker() as db:
            result = await db.execute(select(Server).filter(Server.id == server_id))
            server = result.scalar_one_or_none()
            
            if not server:
                await websocket.send_json({
                    "type": "error",
                    "message": "Server not found"
                })
                await websocket.close()
                return
        
        # Create SSH connection
        ssh_manager = SSHManager()
        success, msg = await ssh_manager.connect(server)
        
        if not success:
            await websocket.send_json({
                "type": "error",
                "message": f"SSH connection failed: {msg}"
            })
            await websocket.close()
            return
        
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to {server.host}"
        })
        
        # Handle interactive shell
        try:
            # Create interactive process
            process = await ssh_manager.conn.create_process()
            
            async def read_output():
                """Read output from SSH and send to WebSocket"""
                try:
                    while True:
                        output = await process.stdout.read(1024)
                        if output:
                            await websocket.send_json({
                                "type": "output",
                                "data": output
                            })
                        else:
                            break
                except Exception as e:
                    pass
            
            # Start reading output
            output_task = asyncio.create_task(read_output())
            
            # Handle input from WebSocket
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message.get("type") == "input":
                    # Send input to SSH
                    input_data = message.get("data", "")
                    process.stdin.write(input_data)
                    await process.stdin.drain()
                elif message.get("type") == "resize":
                    # Handle terminal resize
                    width = message.get("width", 80)
                    height = message.get("height", 24)
                    process.change_terminal_size(width, height)
                elif message.get("type") == "disconnect":
                    break
        
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"Console error: {str(e)}"
            })
        finally:
            await ssh_manager.disconnect()
    
    except WebSocketDisconnect:
        pass


@router.websocket("/servers/{server_id}/game-console")
async def game_console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for game console access
    Uses interactive PTY attachment to screen session for real-time console access (like screen -x)
    
    This implementation creates a true interactive session with the screen process,
    providing real-time bidirectional streaming without polling delays.
    """
    await websocket.accept()
    
    ssh_conn = None
    process = None
    reader_task = None
    session_log = None
    
    try:
        # Get server details from database
        from modules.database import async_session_maker
        async with async_session_maker() as db:
            result = await db.execute(select(Server).filter(Server.id == server_id))
            server = result.scalar_one_or_none()
            
            if not server:
                await websocket.send_json({
                    "type": "error",
                    "message": "Server not found"
                })
                await websocket.close()
                return
        
        # Import asyncssh for direct connection
        import asyncssh
        
        # Create SSH connection directly with asyncssh
        # Add keepalive settings to prevent connection timeouts
        try:
            from modules.models import AuthType
            
            # Common connection options with keepalive
            connect_options = {
                'known_hosts': None,
                'keepalive_interval': 30,  # Send keepalive every 30 seconds
                'keepalive_count_max': 3,  # Allow 3 missed keepalives before disconnect
                'login_timeout': 60,  # Increase login timeout
                'connect_timeout': 30,  # Connection timeout
            }
            
            if server.auth_type == AuthType.PASSWORD:
                ssh_conn = await asyncssh.connect(
                    host=server.host,
                    port=server.ssh_port,
                    username=server.ssh_user,
                    password=server.ssh_password,
                    **connect_options
                )
            elif server.auth_type == AuthType.KEY_FILE:
                ssh_conn = await asyncssh.connect(
                    host=server.host,
                    port=server.ssh_port,
                    username=server.ssh_user,
                    client_keys=[server.ssh_key_path],
                    **connect_options
                )
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unsupported auth type: {server.auth_type}"
                })
                await websocket.close()
                return
                
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"SSH connection failed: {str(e)}"
            })
            await websocket.close()
            return
        
        # Check if server is running
        screen_name = f"cs2server_{server_id}"
        try:
            result = await ssh_conn.run(f"screen -list | grep {screen_name} || true", check=False)
            if not result.stdout or not result.stdout.strip() or screen_name not in result.stdout:
                await websocket.send_json({
                    "type": "error",
                    "message": "Game server is not running. Please start the server first."
                })
                await websocket.close()
                return
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"Failed to check server status: {str(e)}"
            })
            await websocket.close()
            return
        
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to CS2 server console on {server.host}"
        })
        
        # Get initial console history using hardcopy
        try:
            hardcopy_file = f"/tmp/cs2_console_init_{server_id}.txt"
            await ssh_conn.run(f"screen -S {screen_name} -X hardcopy -h {hardcopy_file}", check=False)
            await asyncio.sleep(0.1)
            
            result = await ssh_conn.run(f"test -f {hardcopy_file} && tail -n 100 {hardcopy_file} 2>/dev/null || echo ''", check=False)
            if result.stdout and result.stdout.strip():
                await websocket.send_json({
                    "type": "output",
                    "data": "--- Console History (last 100 lines) ---\n" + result.stdout + "\n"
                })
            
            await ssh_conn.run(f"rm -f {hardcopy_file}", check=False)
        except Exception:
            pass
        
        await websocket.send_json({
            "type": "output",
            "data": "--- Console Connected ---\n"
        })
        
        # Use screen's logging feature to capture output in real-time
        # This is more reliable than trying to tail console.log which may not exist
        # We'll use screen -X hardcopy to periodically get screen buffer
        # and use script command to capture real-time output
        
        # Method: Use script command with screen to capture all output
        # The script command records terminal sessions
        try:
            # Create a unique log file for this session
            session_log = f"/tmp/cs2_console_session_{server_id}_{int(asyncio.get_event_loop().time())}.log"
            
            # Use script to capture screen session output in real-time
            # -f forces flush of output, -c runs the command
            # Use -x flag to attach to screen even if already attached (multi-display mode)
            # -q for quiet mode to suppress screen messages
            process = await ssh_conn.create_process(
                f"script -f -q -c 'screen -x {screen_name} -q' {session_log}",
                term_type='xterm-256color',
                encoding='utf-8',
                errors='replace'
            )
            
            # Give the process a moment to start
            await asyncio.sleep(0.3)
            
            # Check if process started successfully
            if process.exit_status is not None:
                # Process already exited
                await websocket.send_json({
                    "type": "error",
                    "message": f"Console monitoring process exited with status {process.exit_status}"
                })
                await websocket.close()
                return
                
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": f"Failed to attach to console: {str(e)}"
            })
            await websocket.close()
            return
        
        # Task to read output from the console and send to WebSocket
        async def read_console_output():
            try:
                # Read from stdout in real-time
                async for line in process.stdout:
                    try:
                        # Send each line as it comes in real-time
                        await websocket.send_json({
                            "type": "output",
                            "data": line
                        })
                    except Exception as e:
                        # WebSocket closed or error sending
                        break
                        
                # If we get here, the stream ended
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Console stream ended unexpectedly"
                    })
                except Exception:
                    pass
                    
            except asyncio.CancelledError:
                # Task was cancelled, normal shutdown
                pass
            except Exception as e:
                # Connection error or other issue
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Console read error: {str(e)}"
                    })
                except Exception:
                    pass
        
        # Start reading output in the background
        reader_task = asyncio.create_task(read_console_output())
        
        # Handle input from WebSocket
        try:
            while True:
                # Receive data from WebSocket
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message.get("type") == "input":
                    # Send command to screen session using screen -X stuff
                    command = message.get("data", "")
                    if not command:
                        continue
                    
                    # Add newline if not present
                    if not command.endswith('\n'):
                        command += '\n'
                    
                    # Escape command for shell (handle quotes and backslashes)
                    escaped_cmd = command.replace("\\", "\\\\").replace("'", "'\\''")
                    send_cmd = f"screen -S {screen_name} -X stuff '{escaped_cmd}'"
                    
                    try:
                        # Execute command to send input to screen
                        result = await asyncio.wait_for(
                            ssh_conn.run(send_cmd, check=False),
                            timeout=5.0
                        )
                        
                        # Check if command failed
                        if result.exit_status != 0 and result.stderr:
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Failed to send command: {result.stderr}"
                            })
                    except asyncio.TimeoutError:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Command send timeout"
                        })
                    except Exception as e:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Failed to send command: {str(e)}"
                        })
                
                elif message.get("type") == "ping":
                    # Respond to ping to keep connection alive
                    try:
                        await websocket.send_json({
                            "type": "pong"
                        })
                    except Exception:
                        break
                
                elif message.get("type") == "disconnect":
                    break
        
        except WebSocketDisconnect:
            # Client disconnected
            pass
        except Exception as e:
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Console error: {str(e)}"
                })
            except Exception:
                pass
    
    except WebSocketDisconnect:
        # Client disconnected during setup
        pass
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Unexpected error: {str(e)}"
            })
        except Exception:
            pass
    finally:
        # Clean up resources
        if reader_task and not reader_task.done():
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
        
        if process:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        
        if ssh_conn:
            # Clean up session log file if it exists
            if session_log:
                try:
                    await ssh_conn.run(f"rm -f {session_log}", check=False)
                except Exception:
                    pass
            
            ssh_conn.close()
            try:
                await ssh_conn.wait_closed()
            except Exception:
                pass

