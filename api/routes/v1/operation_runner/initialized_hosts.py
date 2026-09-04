"""Queued operations for saved initialized hosts that are not game servers yet."""

from __future__ import annotations

import asyncio

import asyncssh

from modules import User
from modules.database import async_session_maker
from services.initialized_server_service import resolve_initialized_server
from services.server_operation_hub import ServerOperationConflict, server_operation_hub

from .shared import _dispatch, logger


def _queue_id(initialized_server_id: int) -> int:
    """Use negative IDs so host jobs cannot collide with game-server IDs."""
    return -initialized_server_id


async def enqueue_initialized_host_ssh_test(
    *, initialized_server_id: int, actor_user_id: int
) -> dict:
    """Queue an SSH connectivity check and return without opening SSH inline."""
    record = await server_operation_hub.create(
        server_id=_queue_id(initialized_server_id),
        action="test_initialized_ssh",
        actor_user_id=actor_user_id,
        command=f"ssh-test initialized-host:{initialized_server_id}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_initialized_host_ssh_test(
            operation_id=operation_id,
            initialized_server_id=initialized_server_id,
        ),
    )


async def run_initialized_host_ssh_test(*, operation_id: str, initialized_server_id: int) -> None:
    """Check SSH authentication and command execution in the queue worker."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return
    await server_operation_hub.mark_running(operation_id)
    connection: asyncssh.SSHClientConnection | None = None

    async def progress(message: str) -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

    try:
        async with async_session_maker() as db:
            user = await db.get(User, int(record["actor_user_id"]))
            if user is None or not user.is_active:
                await server_operation_hub.finish(
                    operation_id,
                    success=False,
                    message="The operator account is no longer available",
                )
                return
            resolved = await resolve_initialized_server(db, str(initialized_server_id), user.id)
            if resolved is None or resolved.database_record is None:
                await server_operation_hub.finish(
                    operation_id,
                    success=False,
                    message="Saved initialized host was deleted or is no longer available",
                )
                return
            host = resolved.record
        await progress(f"Connecting to {host.host}:{host.ssh_port} over SSH")
        connection = await asyncssh.connect(
            host=host.host,
            port=host.ssh_port,
            username=host.ssh_user,
            password=host.ssh_password,
            known_hosts=None,
            connect_timeout=15,
        )
        result = await connection.run("printf 'ssh-ok'", check=False)
        if int(result.exit_status or 0) != 0:
            await server_operation_hub.finish(
                operation_id,
                success=False,
                message="SSH authentication succeeded, but the remote command failed",
            )
            return
        await progress("SSH authentication and remote command execution succeeded")
        await server_operation_hub.finish(
            operation_id,
            success=True,
            message="SSH is available",
        )
    except asyncssh.PermissionDenied:
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="SSH authentication failed",
        )
    except asyncio.TimeoutError:
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="SSH connection timed out",
        )
    except asyncssh.Error as exc:
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message=f"SSH connection failed: {exc}",
        )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except Exception:
        logger.exception("Initialized host SSH test %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="SSH connectivity test failed unexpectedly",
        )
    finally:
        if connection is not None:
            connection.close()
            await connection.wait_closed()


__all__ = [
    "enqueue_initialized_host_ssh_test",
    "run_initialized_host_ssh_test",
]
