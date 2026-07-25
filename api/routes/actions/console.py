"""Actions console endpoints."""

# ruff: noqa: F403,F405

from .common import *

router = APIRouter(tags=["actions"])


@router.websocket("/servers/{server_id}/ssh-console")
async def ssh_console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for SSH console access
    Provides interactive SSH terminal access to the server
    """
    user, server = await authenticate_websocket(websocket, server_id)
    if user is None or server is None:
        return

    try:
        ssh_manager = get_ssh_manager(websocket)
    except HTTPException as exc:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason=str(exc.detail))
        return
    await websocket.accept()

    try:
        # Create SSH connection
        success, msg = await ssh_manager.connect(server)

        if not success:
            await websocket.send_json({"type": "error", "message": f"SSH connection failed: {msg}"})
            await websocket.close()
            return
        if ssh_manager.conn is None:
            await websocket.send_json(
                {"type": "error", "message": "SSH connection did not provide a session"}
            )
            await websocket.close()
            return

        await websocket.send_json({"type": "connected", "message": f"Connected to {server.host}"})

        # Handle interactive shell
        process = None
        output_task = None
        try:
            # Create interactive process with PTY for interactive shell
            # Request a PTY to enable interactive terminal features
            process = await ssh_manager.conn.create_process(
                term_type="xterm-256color", encoding="utf-8", errors="replace"
            )

            async def read_output():
                """Read output from SSH and send to WebSocket"""
                try:
                    while True:
                        output = await process.stdout.read(1024)
                        if output:
                            await websocket.send_json({"type": "output", "data": output})
                        else:
                            break
                except Exception:
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
                    cols = message.get("cols", 80)
                    rows = message.get("rows", 24)
                    process.change_terminal_size(cols, rows)
                elif message.get("type") == "disconnect":
                    break

        except Exception as e:
            await websocket.send_json({"type": "error", "message": f"Console error: {str(e)}"})
        finally:
            if output_task is not None:
                output_task.cancel()
                with suppress(asyncio.CancelledError):
                    await output_task
            if process is not None:
                with suppress(Exception):
                    process.terminate()
                    await asyncio.wait_for(process.wait_closed(), timeout=2)

    except WebSocketDisconnect:
        pass
    finally:
        await ssh_manager.disconnect()


@router.websocket("/servers/{server_id}/game-console")
async def game_console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for game console access
    Uses an interactive PTY attachment to the configured screen/tmux session.
    """
    user, server = await authenticate_websocket(websocket, server_id)
    if user is None or server is None:
        return

    try:
        ssh_manager = get_ssh_manager(websocket)
    except HTTPException as exc:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason=str(exc.detail))
        return
    await websocket.accept()

    try:
        # Create SSH connection using SSHManager (same as SSH console)
        success, msg = await ssh_manager.connect(server)

        if not success:
            await websocket.send_json({"type": "error", "message": f"SSH connection failed: {msg}"})
            await websocket.close()
            return
        if ssh_manager.conn is None:
            await websocket.send_json(
                {"type": "error", "message": "SSH connection did not provide a session"}
            )
            await websocket.close()
            return

        # Check both the configured and legacy manager to support safe switches.
        name = session_name(server_id)
        try:
            active_manager = await find_running_session_manager(
                ssh_manager.execute_command,
                server.session_manager,
                name,
            )
            if not active_manager:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Game server is not running. Please start the server first.",
                    }
                )
                await websocket.close()
                return
        except Exception as e:
            await websocket.send_json(
                {"type": "error", "message": f"Failed to check server status: {str(e)}"}
            )
            await websocket.close()
            return

        await websocket.send_json(
            {"type": "connected", "message": f"Connected to CS2 server console on {server.host}"}
        )

        # Attach to the live session with a PTY (screen and tmux both support
        # multiple clients without disconnecting the game process).
        process = None
        output_task = None
        try:
            process = await ssh_manager.conn.create_process(
                attach_command(active_manager, name),
                term_type="xterm-256color",
                encoding="utf-8",
                errors="replace",
            )

            async def read_output():
                """Read output from the game session and send it to WebSocket."""
                try:
                    while True:
                        output = await process.stdout.read(1024)
                        if output:
                            await websocket.send_json({"type": "output", "data": output})
                        else:
                            break
                except Exception:
                    pass

            # Start reading output
            output_task = asyncio.create_task(read_output())

            # Handle input from WebSocket
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)

                if message.get("type") == "input":
                    # Send input directly to the attached session via stdin.
                    input_data = message.get("data", "")
                    process.stdin.write(input_data)
                    await process.stdin.drain()
                elif message.get("type") == "resize":
                    # Handle terminal resize
                    cols = message.get("cols", 80)
                    rows = message.get("rows", 24)
                    process.change_terminal_size(cols, rows)
                elif message.get("type") == "ping":
                    # Respond to ping to keep connection alive
                    try:
                        await websocket.send_json({"type": "pong"})
                    except Exception:
                        break
                elif message.get("type") == "disconnect":
                    break

        except WebSocketDisconnect:
            pass
        except Exception as e:
            await websocket.send_json({"type": "error", "message": f"Console error: {str(e)}"})
        finally:
            # A pooled SSH connection remains open after disconnect(), so the
            # attached screen/tmux client must be closed explicitly.  This
            # detaches only the console client; it does not stop the session.
            if output_task is not None:
                output_task.cancel()
                with suppress(asyncio.CancelledError):
                    await output_task

            if process is not None:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait_closed(), timeout=2)
                except Exception:
                    with suppress(Exception):
                        process.kill()
                        await asyncio.wait_for(process.wait_closed(), timeout=2)

    except WebSocketDisconnect:
        pass
    finally:
        await ssh_manager.disconnect()
