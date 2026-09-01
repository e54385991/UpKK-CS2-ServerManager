"""Market operation workers."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

from api.dependencies import require_server_access
from modules import User
from modules.database import async_session_maker
from services.ai_access import AgentAccessDenied
from services.github_plugin_plan_service import (
    GitHubPlanError,
    execute_github_install_plan,
)
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.plugin_conflict_service import PluginPlanError, execute_plugin_install_plan
from services.plugin_uninstall import uninstall_plugin_files
from services.server_operation_hub import (
    ServerOperationConflict,
    server_operation_hub,
)

from .shared import _dispatch, logger


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


async def enqueue_github_plugin_install(
    *,
    server_id: int,
    actor_user_id: int,
    repo_url: str,
    mode: Literal["install", "upgrade"],
    asset_name: str | None,
    config_policy: Literal["preserve", "overwrite"],
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
    mode: Literal["install", "upgrade"],
    asset_name: str | None,
    config_policy: Literal["preserve", "overwrite"],
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
    from modules.schemas.plugins import GitHubPluginInstallPlanRequest

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
    from sqlmodel import select

    from modules import ManagedPlugin

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
