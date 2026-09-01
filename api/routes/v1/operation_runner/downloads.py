"""Downloads operation workers."""

from __future__ import annotations

from fastapi import HTTPException

from api.dependencies import require_server_access
from modules import User
from modules.database import async_session_maker
from services.github_credentials import get_effective_github_token
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)
from services.ssh_manager import SSHManager

from .shared import _audit_terminal, _dispatch, _progress_emitter, logger


async def enqueue_extract_archive(
    *,
    server_id: int,
    actor_user_id: int,
    archive_path: str,
    destination_path: str,
    overwrite: bool,
    source_folder: str | None,
    strip_source_folder: bool,
) -> dict:
    """Queue a remote archive extract on the per-server FIFO."""
    command = f"extract {archive_path} -> {destination_path}"
    if source_folder:
        command += f" --folder {source_folder}"
    record = await server_operation_hub.create(
        server_id=server_id,
        action="extract_archive",
        actor_user_id=actor_user_id,
        command=command,
        extra={"archive_path": archive_path, "destination": destination_path},
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_extract_archive(
            operation_id=operation_id,
            archive_path=archive_path,
            destination_path=destination_path,
            overwrite=overwrite,
            source_folder=source_folder,
            strip_source_folder=strip_source_folder,
        ),
    )


async def run_extract_archive(
    *,
    operation_id: str,
    archive_path: str,
    destination_path: str,
    overwrite: bool,
    source_folder: str | None,
    strip_source_folder: bool,
) -> None:
    """Extract one queued archive over SSH."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])
    progress = _progress_emitter(operation_id)
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
                await _audit_terminal(
                    record,
                    category="files",
                    action="files.extract",
                    success=False,
                    message="The operator account is no longer available",
                    extra={"path": archive_path, "destination": destination_path},
                )
                return

            server = await require_server_access(db, server_id, user)
            async with maintenance_lock_service.get(
                server_id,
                operation="extract_archive",
                wait=False,
                ttl=10800,
            ):
                connected, connect_message = await manager.connect(server)
                if not connected:
                    await server_operation_hub.finish(
                        operation_id, success=False, message=connect_message
                    )
                    await _audit_terminal(
                        record,
                        category="files",
                        action="files.extract",
                        success=False,
                        message=connect_message,
                        extra={"path": archive_path, "destination": destination_path},
                    )
                    return
                success, error = await manager.extract_archive(
                    archive_path,
                    destination_path,
                    server,
                    overwrite=overwrite,
                    source_folder=source_folder,
                    strip_source_folder=strip_source_folder,
                    progress_callback=progress,
                )
            message = "Archive extracted successfully" if success else error
            await server_operation_hub.finish(operation_id, success=success, message=message)
            await _audit_terminal(
                record,
                category="files",
                action="files.extract",
                success=success,
                message=message,
                extra={"path": archive_path, "destination": destination_path},
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="files",
            action="files.extract",
            success=False,
            message=str(exc),
            extra={"path": archive_path},
        )
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
        await _audit_terminal(
            record,
            category="files",
            action="files.extract",
            success=False,
            message=str(exc),
            extra={"path": archive_path},
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
        await _audit_terminal(
            record,
            category="files",
            action="files.extract",
            success=False,
            message=detail,
            extra={"path": archive_path},
        )
    except Exception:
        logger.exception("Background archive extract %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="Archive extraction failed unexpectedly",
        )
        await _audit_terminal(
            record,
            category="files",
            action="files.extract",
            success=False,
            message="Archive extraction failed unexpectedly",
            extra={"path": archive_path},
        )
    finally:
        try:
            await manager.disconnect()
        except Exception:
            pass


async def enqueue_url_download(
    *,
    server_id: int,
    actor_user_id: int,
    url: str,
    destination_path: str,
    target_path: str | None,
    overwrite: bool,
) -> dict:
    """Queue a remote HTTP(S) archive download on the per-server FIFO."""
    shown = target_path or destination_path
    record = await server_operation_hub.create(
        server_id=server_id,
        action="download_url",
        actor_user_id=actor_user_id,
        command=f"download-url -> {shown}",
        extra={"destination": destination_path, "target_path": target_path},
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_url_download(
            operation_id=operation_id,
            url=url,
            destination_path=destination_path,
            target_path=target_path,
            overwrite=overwrite,
        ),
    )


async def run_url_download(
    *,
    operation_id: str,
    url: str,
    destination_path: str,
    target_path: str | None,
    overwrite: bool,
) -> None:
    """Download one queued HTTP(S) archive onto the SSH host."""
    from api.routes.file_manager.common import (
        _parse_github_actions_artifact_url,
        _resolve_github_actions_artifact,
        remote_join,
    )

    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])
    progress = _progress_emitter(operation_id)
    manager = SSHManager()
    resolved_target = target_path

    async def update_target(path: str) -> None:
        nonlocal resolved_target
        resolved_target = path
        await server_operation_hub.patch(operation_id, target_path=path)

    async def fail(message: str) -> None:
        await server_operation_hub.finish(operation_id, success=False, message=message)
        await _audit_terminal(
            record,
            category="files",
            action="files.download_url",
            success=False,
            message=message,
            extra={"destination": destination_path, "target_path": resolved_target},
        )

    try:
        async with async_session_maker() as db:
            user = await db.get(User, actor_user_id)
            if user is None or not user.is_active:
                await fail("The operator account is no longer available")
                return

            server = await require_server_access(db, server_id, user)
            github_token = await get_effective_github_token(db, user)
            async with maintenance_lock_service.get(
                server_id,
                operation="download_url",
                wait=False,
                ttl=10800,
            ):
                connected, connect_message = await manager.connect(server)
                if not connected:
                    await fail(connect_message)
                    return
                await progress(f"Downloading to {resolved_target or destination_path}")
                download_url = url
                is_github_artifact = _parse_github_actions_artifact_url(url) is not None
                if is_github_artifact:
                    download_url, artifact_filename = await _resolve_github_actions_artifact(
                        url,
                        github_token,
                    )
                    if resolved_target is None:
                        resolved_target = remote_join(destination_path, artifact_filename)
                        await update_target(resolved_target)

                success, error = await manager.download_url_to_file(
                    download_url,
                    resolved_target,
                    server,
                    overwrite=overwrite,
                    destination_path=destination_path,
                    resolved_target_callback=update_target,
                )
                if is_github_artifact and not success and str(error).startswith("Download failed:"):
                    download_url, _ = await _resolve_github_actions_artifact(url, github_token)
                    success, error = await manager.download_url_to_file(
                        download_url,
                        resolved_target,
                        server,
                        overwrite=overwrite,
                        destination_path=destination_path,
                        resolved_target_callback=update_target,
                    )
                github_token = None
            message = "Archive downloaded successfully" if success else error
            await server_operation_hub.finish(operation_id, success=success, message=message)
            await _audit_terminal(
                record,
                category="files",
                action="files.download_url",
                success=success,
                message=message,
                extra={"destination": destination_path, "target_path": resolved_target},
            )
    except ServerOperationConflict as exc:
        await fail(str(exc))
    except OperationBusyError as exc:
        await fail(str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await fail(detail)
    except Exception:
        logger.exception("Background URL download %s failed", operation_id)
        await fail("URL download failed unexpectedly")
    finally:
        try:
            await manager.disconnect()
        except Exception:
            pass
