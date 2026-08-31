"""Background runner for versioned server operations.

Lives in the API layer so it can reuse the legacy ``server_action`` handler
without pulling HTTP routers into ``services/``.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid

from anyio import to_thread
from fastapi import HTTPException, Request
from sqlmodel import select

from api.dependencies import require_server_access
from api.routes.actions.deployment import server_action
from api.routes.servers.common import get_server_owner_user
from modules import ManagedPlugin, User
from modules.database import async_session_maker
from modules.schemas.common import ALLOWED_SERVER_ACTIONS
from modules.schemas.plugins import GitHubPluginInstallPlanRequest
from modules.schemas.servers import ServerAction
from services.ai_access import AgentAccessDenied
from services.game_mode_install_service import GameModePlanError, execute_game_mode_plan
from services.github_plugin_plan_service import (
    GitHubPlanError,
    execute_github_install_plan,
)
from services.host_initialization import SshManagerHostRunner, ensure_steamcmd_packages
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.plugin_conflict_service import PluginPlanError, execute_plugin_install_plan
from services.plugin_uninstall import uninstall_plugin_files
from services.s3_backup_service import s3_backup_service
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)
from services.ssh_manager import SSHManager
from services.system_dependencies import STEAMCMD_REQUIRED_PACKAGES

logger = logging.getLogger(__name__)


async def _dispatch(record: dict, factory) -> dict:
    """Start now if this job is current; otherwise the hub runs it later."""
    await server_operation_hub.schedule(str(record["operation_id"]), factory)
    return record


async def enqueue_server_operation(
    *,
    server_id: int,
    action: str,
    actor_user_id: int,
    request: Request | None = None,
) -> dict:
    """Create an operation record and run the existing SSH action in the background."""
    if action not in ALLOWED_SERVER_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Invalid action: {action}")

    record = await server_operation_hub.create(
        server_id=server_id,
        action=action,
        actor_user_id=actor_user_id,
        command=f"server {action}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_server_operation(operation_id=operation_id, request=request),
    )


async def run_server_operation(
    *,
    operation_id: str,
    request: Request | None = None,
) -> None:
    """Execute one queued operation using the legacy server_action implementation."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    action = str(record["action"])
    actor_user_id = int(record["actor_user_id"])

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
            async with maintenance_lock_service.get(
                server_id,
                operation="server_action",
                wait=False,
                ttl=7200,
            ):
                result = await server_action(
                    server_id,
                    ServerAction(action=action),
                    db,
                    user,
                    server,
                    request,
                )
            server_status = None
            if isinstance(result.data, dict):
                server_status = result.data.get("status")
            await server_operation_hub.finish(
                operation_id,
                success=bool(result.success),
                message=result.message,
                server_status=str(server_status) if server_status else None,
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background server operation %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="The operation failed unexpectedly",
        )


async def enqueue_plugin_install(
    *,
    server_id: int,
    plugin_id: int,
    actor_user_id: int,
    acknowledge_warning_rule_ids: list[int],
    plan_hash: str | None,
    download_url: str | None = None,
    upgrade_mode: bool = False,
    install_dependencies: bool = False,
    exclude_dirs: list[str] | None = None,
    exclude_files: list[str] | None = None,
) -> dict:
    """Create an operation record and install a market plugin in the background."""
    target = download_url or "latest"
    record = await server_operation_hub.create(
        server_id=server_id,
        action="install_plugin",
        actor_user_id=actor_user_id,
        command=f"plugin-market install {plugin_id} --from {target}",
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_plugin_install(
            operation_id=operation_id,
            plugin_id=plugin_id,
            acknowledge_warning_rule_ids=list(acknowledge_warning_rule_ids),
            plan_hash=plan_hash,
            download_url=download_url,
            upgrade_mode=upgrade_mode,
            install_dependencies=install_dependencies,
            exclude_dirs=list(exclude_dirs or []),
            exclude_files=list(exclude_files or []),
        ),
    )


async def run_plugin_install(
    *,
    operation_id: str,
    plugin_id: int,
    acknowledge_warning_rule_ids: list[int],
    plan_hash: str | None,
    download_url: str | None = None,
    upgrade_mode: bool = False,
    install_dependencies: bool = False,
    exclude_dirs: list[str] | None = None,
    exclude_files: list[str] | None = None,
) -> None:
    """Execute one queued market-plugin install through the existing planner."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])

    async def progress(message: str, _kind: str = "status") -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

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
            result = await execute_plugin_install_plan(
                db,
                server,
                user,
                plugin_id,
                acknowledge_warning_rule_ids,
                expected_plan_hash=plan_hash,
                progress=progress,
                acquire_lock=True,
                lock_operation="plugin_install",
                operation_id=operation_id,
                include_dependencies=install_dependencies,
                download_url=download_url,
                upgrade_mode=upgrade_mode,
                exclude_dirs=list(exclude_dirs or []),
                exclude_files=list(exclude_files or []),
            )
            await server_operation_hub.finish(
                operation_id,
                success=bool(result.get("success")),
                message=str(result.get("message") or "Plugin installation finished"),
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except PluginPlanError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background plugin install %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="The plugin install failed unexpectedly",
        )


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


async def enqueue_github_plugin_install(
    *,
    server_id: int,
    actor_user_id: int,
    repo_url: str,
    mode: str,
    asset_name: str | None,
    config_policy: str,
    recipe_id: int | None,
    source_prefix: str | None,
    target_prefix: str | None,
    exclude_dirs: list[str],
    exclude_files: list[str],
    expected_plan_hash: str,
    acknowledge_warning_rule_ids: list[int],
    acknowledge_unknown_compatibility: bool,
) -> dict:
    """Create an operation and install a GitHub release asset in the background."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="install_github_plugin",
        actor_user_id=actor_user_id,
        command=f"plugin-github install {repo_url}"
        + (f" --asset {asset_name}" if asset_name else ""),
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_github_plugin_install(
            operation_id=operation_id,
            repo_url=repo_url,
            mode=mode,
            asset_name=asset_name,
            config_policy=config_policy,
            recipe_id=recipe_id,
            source_prefix=source_prefix,
            target_prefix=target_prefix,
            exclude_dirs=list(exclude_dirs),
            exclude_files=list(exclude_files),
            expected_plan_hash=expected_plan_hash,
            acknowledge_warning_rule_ids=list(acknowledge_warning_rule_ids),
            acknowledge_unknown_compatibility=acknowledge_unknown_compatibility,
        ),
    )


async def run_github_plugin_install(
    *,
    operation_id: str,
    repo_url: str,
    mode: str,
    asset_name: str | None,
    config_policy: str,
    recipe_id: int | None,
    source_prefix: str | None,
    target_prefix: str | None,
    exclude_dirs: list[str],
    exclude_files: list[str],
    expected_plan_hash: str,
    acknowledge_warning_rule_ids: list[int],
    acknowledge_unknown_compatibility: bool,
) -> None:
    """Execute one queued GitHub-plugin install through the existing planner."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])

    async def progress(message: str, _kind: str = "status") -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

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

            await require_server_access(db, server_id, user)
            plan_request = GitHubPluginInstallPlanRequest(
                repo_url=repo_url,
                mode=mode,
                asset_name=asset_name,
                config_policy=config_policy,
                recipe_id=recipe_id,
                source_prefix=source_prefix,
                target_prefix=target_prefix,
                exclude_dirs=list(exclude_dirs),
                exclude_files=list(exclude_files),
            )
            result = await execute_github_install_plan(
                db,
                user,
                server_id,
                plan_request,
                expected_plan_hash,
                set(acknowledge_warning_rule_ids),
                acknowledge_unknown_compatibility,
                progress=progress,
                lock_operation="github_plugin_install",
                operation_id=operation_id,
            )
            await server_operation_hub.finish(
                operation_id,
                success=bool(result.get("success")),
                message=str(result.get("message") or "GitHub plugin installation finished"),
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except GitHubPlanError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except AgentAccessDenied:
        await server_operation_hub.finish(operation_id, success=False, message="Server not found")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background GitHub plugin install %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="The GitHub plugin install failed unexpectedly",
        )


async def enqueue_github_plugin_uninstall(
    *,
    server_id: int,
    actor_user_id: int,
    files_to_delete: list[str],
    market_plugin_id: int | None = None,
) -> dict:
    """Create an operation and delete selected plugin files in the background."""
    record = await server_operation_hub.create(
        server_id=server_id,
        action="uninstall_github_plugin",
        actor_user_id=actor_user_id,
        command=f"plugin uninstall --files {len(files_to_delete)}"
        + (f" --market {market_plugin_id}" if market_plugin_id else ""),
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_github_plugin_uninstall(
            operation_id=operation_id,
            files_to_delete=list(files_to_delete),
            market_plugin_id=market_plugin_id,
        ),
    )


async def run_github_plugin_uninstall(
    *,
    operation_id: str,
    files_to_delete: list[str],
    market_plugin_id: int | None,
) -> None:
    """Execute one queued plugin-file uninstall over SSH."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])

    async def progress(message: str, _kind: str = "status") -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

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
            async with maintenance_lock_service.get(
                server_id,
                operation="github_plugin_uninstall",
                wait=False,
            ):
                result = await uninstall_plugin_files(
                    server=server,
                    files_to_delete=list(files_to_delete),
                    progress=progress,
                )
            if result.get("success") and market_plugin_id is not None:
                tracked = await db.execute(
                    select(ManagedPlugin).where(
                        ManagedPlugin.server_id == server_id,
                        ManagedPlugin.source_type == "market",
                        ManagedPlugin.source_key == str(market_plugin_id),
                    )
                )
                managed = tracked.scalar_one_or_none()
                if managed is not None:
                    await db.delete(managed)
                    await db.commit()
            await server_operation_hub.finish(
                operation_id,
                success=bool(result.get("success")),
                message=str(result.get("message") or "Plugin uninstallation finished"),
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except ValueError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background GitHub plugin uninstall %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="The GitHub plugin uninstall failed unexpectedly",
        )


async def enqueue_game_mode_install(
    *,
    server_id: int,
    mode_id: str,
    actor_user_id: int,
    wipe_addons: bool,
    wipe_addons_acknowledged: bool,
    plan_hash: str,
    acknowledge_warning_rule_ids: list[int],
) -> dict:
    """Create an operation record and install a game-mode recipe in the background."""
    del wipe_addons_acknowledged
    command = f"game-mode install {mode_id}"
    if wipe_addons:
        command += " --wipe-addons"
    record = await server_operation_hub.create(
        server_id=server_id,
        action="install_game_mode",
        actor_user_id=actor_user_id,
        command=command,
    )
    operation_id = str(record["operation_id"])
    return await _dispatch(
        record,
        lambda: run_game_mode_install(
            operation_id=operation_id,
            mode_id=mode_id,
            wipe_addons=wipe_addons,
            plan_hash=plan_hash,
            acknowledge_warning_rule_ids=list(acknowledge_warning_rule_ids),
        ),
    )


async def run_game_mode_install(
    *,
    operation_id: str,
    mode_id: str,
    wipe_addons: bool,
    plan_hash: str,
    acknowledge_warning_rule_ids: list[int],
) -> None:
    """Execute one queued game-mode install through the recipe planner."""
    record = await server_operation_hub.get(operation_id)
    if record is None:
        return

    await server_operation_hub.mark_running(operation_id)
    server_id = int(record["server_id"])
    actor_user_id = int(record["actor_user_id"])

    async def progress(message: str, _kind: str = "status") -> None:
        await server_operation_hub.emit(
            operation_id,
            "progress",
            kind="output",
            message=message,
        )

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
            result = await execute_game_mode_plan(
                db,
                server,
                user,
                mode_id,
                wipe_addons=wipe_addons,
                expected_plan_hash=plan_hash,
                acknowledged_warning_rule_ids=acknowledge_warning_rule_ids,
                progress=progress,
                operation_id=operation_id,
            )
            await server_operation_hub.finish(
                operation_id,
                success=bool(result.get("success")),
                message=str(result.get("message") or "Game-mode installation finished"),
            )
    except ServerOperationConflict as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except OperationBusyError as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except (GameModePlanError, PluginPlanError) as exc:
        await server_operation_hub.finish(operation_id, success=False, message=str(exc))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        await server_operation_hub.finish(operation_id, success=False, message=detail)
    except Exception:
        logger.exception("Background game-mode install %s failed", operation_id)
        await server_operation_hub.finish(
            operation_id,
            success=False,
            message="The game-mode install failed unexpectedly",
        )
