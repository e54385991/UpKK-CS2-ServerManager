"""Actions console endpoints."""

# ruff: noqa: F403,F405

from services.ssh.text import decode_remote_text, encode_console_input

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
    await websocket.accept()

    try:
        # Create SSH connection
        ssh_manager = SSHManager()
        success, msg = await ssh_manager.connect(server)

        if not success:
            await websocket.send_json({"type": "error", "message": f"SSH connection failed: {msg}"})
            await websocket.close()
            return

        await websocket.send_json({"type": "connected", "message": f"Connected to {server.host}"})

        # Handle interactive shell
        process = None
        output_task = None
        try:
            # Create interactive process with PTY for interactive shell
            # Request a PTY to enable interactive terminal features
            process = await ssh_manager.create_interactive_process()

            async def read_output():
                """Read output from SSH and send to WebSocket"""
                try:
                    while True:
                        output = await process.stdout.read(1024)
                        if output:
                            await websocket.send_json(
                                {"type": "output", "data": decode_remote_text(output)}
                            )
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
                    process.stdin.write(encode_console_input(str(input_data)))
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
            await ssh_manager.disconnect()

    except WebSocketDisconnect:
        pass


@router.websocket("/servers/{server_id}/game-console")
async def game_console_websocket(websocket: WebSocket, server_id: int):
    """
    WebSocket endpoint for game console access
    Uses an interactive PTY attachment to the configured screen/tmux session.
    """
    user, server = await authenticate_websocket(websocket, server_id)
    if user is None or server is None:
        return
    await websocket.accept()

    try:
        # Create SSH connection using SSHManager (same as SSH console)
        ssh_manager = SSHManager()
        success, msg = await ssh_manager.connect(server)

        if not success:
            await websocket.send_json({"type": "error", "message": f"SSH connection failed: {msg}"})
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
                await ssh_manager.disconnect()
                return
        except Exception as e:
            await websocket.send_json(
                {"type": "error", "message": f"Failed to check server status: {str(e)}"}
            )
            await websocket.close()
            await ssh_manager.disconnect()
            return

        await websocket.send_json(
            {"type": "connected", "message": f"Connected to CS2 server console on {server.host}"}
        )

        # Attach to the live session with a PTY (screen and tmux both support
        # multiple clients without disconnecting the game process).
        process = None
        output_task = None
        try:
            process = await ssh_manager.create_interactive_process(
                attach_command(active_manager, name)
            )

            async def read_output():
                """Read output from the game session and send it to WebSocket."""
                try:
                    while True:
                        output = await process.stdout.read(1024)
                        if output:
                            await websocket.send_json(
                                {"type": "output", "data": decode_remote_text(output)}
                            )
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
                    process.stdin.write(encode_console_input(str(input_data)))
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

            await ssh_manager.disconnect()

    except WebSocketDisconnect:
        pass
