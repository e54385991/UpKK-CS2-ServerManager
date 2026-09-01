"""Host operation workers."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid

from anyio import to_thread
from fastapi import HTTPException

from api.dependencies import require_server_access
from api.routes.servers.common import get_server_owner_user
from modules import User
from modules.database import async_session_maker
from services.host_initialization import SshManagerHostRunner, ensure_steamcmd_packages
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.s3_backup_service import s3_backup_service
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)
from services.ssh_manager import SSHManager
from services.system_dependencies import STEAMCMD_REQUIRED_PACKAGES

from .shared import _dispatch, logger


async def enqueue_apply_apt_mirror(
    *,
    server_id: int,
    mirror: str,
    actor_user_id: int,
) -> dict:
    """Create an operation that switches apt sources and retries host packages."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="apply_apt_mirror",
        actor_user_id=actor_user_id,
        command=f"apt-mirror apply {mirror}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_apply_apt_mirror(operation_id=operation_id, mirror=mirror),
    )


async def run_apply_apt_mirror(*, operation_id: str, mirror: str) -> None:
    """Apply one catalog mirror over SSH, then re-run host package install."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])

    async def progress(message: str) -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

    manager = SSHManager()
    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                await server_operation_hub.finish(
                    operation_id,
                    success=False,
                    message="The operator account is no longer available",
                )
                return

            server = await require_server_access(db, server_id, user)
            server.apt_mirror = mirror
            await db.commit()
            await db.refresh(server)

            async with maintenance_lock_service.get(
                server_id,
                operation="apply_apt_mirror",
                wait=False,
                ttl=7200,
            ):
                connected, connect_message = await manager.connect(server)
                if not connected:
                    await server_operation_hub.finish(
                        operation_id,
                        success=False,
                        message=connect_message,
                    )
                    return
                try:
                    result = await ensure_steamcmd_packages(
                        SshManagerHostRunner(manager, server),
                        STEAMCMD_REQUIRED_PACKAGES,
                        progress=progress,
                        preferred_mirror=mirror,
                        apply_preferred_first=True,
                    )
                finally:
                    await manager.disconnect()

            if result.apt_mirror and server.apt_mirror != result.apt_mirror:
                server.apt_mirror = result.apt_mirror
                await db.commit()
            await server_operation_hub.finish(
                operation_id,
                success=bool(result.success),
                message=result.message,
                server_status=str(server.status.value) if getattr(server, "status", None) else None,
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background apt-mirror switch %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="Switching the apt mirror failed unexpectedly",
        )


async def enqueue_s3_restore(
    *,
    server_id: int,
    object_key: str,
    actor_user_id: int,
) -> dict:
    """Create an operation that restores one S3 plugin backup over SSH."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="s3_restore",
        actor_user_id=actor_user_id,
        command=f"s3-restore {object_key}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_s3_restore(operation_id=operation_id, object_key=object_key),
    )


async def run_s3_restore(*, operation_id: str, object_key: str) -> None:
    """Download, safety-backup, upload, and extract one S3 plugin archive."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])

    async def progress(message: str) -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

    temp_dir = tempfile.mkdtemp(prefix="cs2_s3_restore_")
    local_path = os.path.join(temp_dir, s3_backup_service.safe_object_filename(object_key))
    manager = SSHManager()
    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                await server_operation_hub.finish(
                    operation_id,
                    success=False,
                    message="The operator account is no longer available",
                )
                return

            server = await require_server_access(db, server_id, user)
            owner = await get_server_owner_user(db, server, user)
            if not s3_backup_service.validate_object_key(owner, server, object_key):
                await server_operation_hub.finish(
                    operation_id,
                    success=False,
                    message="Selected S3 backup does not belong to this server",
                )
                return

            async with maintenance_lock_service.get(
                server_id,
                operation="s3_restore",
                wait=False,
                ttl=7200,
            ):
                await progress(f"Downloading S3 backup {object_key}")
                download_success, download_error = await s3_backup_service.download_backup(
                    owner,
                    server,
                    object_key,
                    local_path,
                )
                if not download_success:
                    await server_operation_hub.finish(
                        operation_id,
                        success=False,
                        message=download_error,
                    )
                    return

                await progress("Creating a local safety backup of plugin files")
                safety_success, safety_message = await manager.backup_plugins(server)
                if not safety_success:
                    await server_operation_hub.finish(
                        operation_id,
                        success=False,
                        message=f"Failed to create safety backup before restore: {safety_message}",
                    )
                    return

                game_dir = server.game_directory.rstrip("/")
                filename = s3_backup_service.safe_object_filename(object_key)
                remote_restore_path = (
                    f"{game_dir}/backups/s3-restore-{uuid.uuid4().hex[:8]}-{filename}"
                )
                await progress(f"Uploading restore archive to {remote_restore_path}")
                upload_success, upload_error = await manager.upload_file(
                    local_path, remote_restore_path, server
                )
                if not upload_success:
                    await server_operation_hub.finish(
                        operation_id,
                        success=False,
                        message=f"Failed to upload restore archive to server: {upload_error}",
                    )
                    return

                csgo_dir = f"{game_dir}/cs2/game/csgo"
                await progress(f"Extracting restore archive into {csgo_dir}")
                extract_success, extract_error = await manager.extract_archive(
                    remote_restore_path,
                    csgo_dir,
                    server,
                    overwrite=True,
                )
                if not extract_success:
                    await server_operation_hub.finish(
                        operation_id,
                        success=False,
                        message=f"Failed to extract restore archive: {extract_error}",
                    )
                    return

            safety_backup = getattr(manager, "last_plugin_backup", None)
            message = "S3 plugin backup restored successfully"
            if safety_backup:
                message = f"{message}. Safety backup: {safety_backup}"
            await server_operation_hub.finish(
                operation_id,
                success=True,
                message=message,
                server_status=str(server.status.value) if getattr(server, "status", None) else None,
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background S3 restore %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="Restoring the S3 backup failed unexpectedly",
        )
    finally:
        try:
            await manager.disconnect()
        except Exception:
            pass
        await to_thread.run_sync(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
