"""Execute a previously built market plugin install plan."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from modules import GitHubPluginInstallRequest, MarketPlugin, Server, User
from services.compat import LateBoundModule
from services.plugins.ai_install_policy import metadata, selected_asset_rules
from services.plugins.common import PluginPlanError
from services.plugins.tracking import derive_asset_glob
from services.plugins.upgrade_exclusions import apply_upgrade_mode_exclusions

ProgressCallback = Callable[..., Awaitable[None]]
host = LateBoundModule("services.plugin_conflict_service")


async def _install_one(
    db: AsyncSession,
    plugin: MarketPlugin,
    server: Server,
    user: User,
    progress: ProgressCallback | None = None,
    operation_id: str | None = None,
    linux_runtime_profile: dict[str, Any] | None = None,
    resolved_asset: dict[str, Any] | None = None,
    exclude_dirs: list[str] | None = None,
    exclude_files: list[str] | None = None,
    upgrade_mode: bool = False,
) -> dict[str, Any]:
    framework_key = host._panel_framework_key(plugin)
    if framework_key is not None:
        return await host._install_panel_framework(
            db,
            plugin,
            server,
            user,
            framework_key,
            progress,
        )

    total_attempts = host.PLUGIN_INSTALL_MAX_RETRIES + 1
    failures: list[str] = []
    asset: dict[str, Any] | None = None
    result = None
    for attempt in range(1, total_attempts + 1):
        if attempt > 1:
            await host._emit_plan_progress(
                progress,
                f"Retrying {plugin.title} installation (attempt {attempt}/{total_attempts})",
                step_id=f"plugin:{plugin.id}",
                step_status="running",
            )
        try:
            asset = (
                resolved_asset
                if attempt == 1 and resolved_asset is not None
                else await host._latest_release_asset(
                    db,
                    plugin,
                    server,
                    user,
                    linux_runtime_profile,
                )
            )
            assert asset is not None
            final_exclude_files = list(exclude_files or [])
            if upgrade_mode:
                final_exclude_files = apply_upgrade_mode_exclusions(final_exclude_files)
            request = GitHubPluginInstallRequest(
                download_url=asset["download_url"],
                exclude_dirs=list(exclude_dirs or []),
                exclude_files=final_exclude_files,
                custom_install_path=asset["custom_install_path"],
                record_installation=False,
                suppress_notification=False,
                archive_mappings=asset.get("archive_mappings", []),
                source_prefix=asset["source_prefix"],
                allowed_roots=asset["allowed_roots"],
                expected_archive_sha256=asset["archive_sha256"],
            )
            result = await host.install_github_plugin(
                server.id,
                request,
                db,
                user,
                ai_progress=progress,
                operation_id=(f"{operation_id}-market-{plugin.id}" if operation_id else None),
            )
            if result.success:
                break
            failure = result.message
        except LookupError, PermissionError:
            raise
        except Exception as exc:
            failure = str(exc) or exc.__class__.__name__
        failures.append(failure)
        deterministic = failure.casefold().startswith("invalid github url")
        if (
            attempt >= total_attempts
            or deterministic
            or not host._is_retryable_install_failure(failure)
        ):
            break
        await host._emit_plan_progress(
            progress,
            f"{plugin.title} installation attempt {attempt}/{total_attempts} failed: "
            f"{failure}. Retrying automatically.",
            step_id=f"plugin:{plugin.id}",
            step_status="running",
        )

    if result is None or not result.success:
        failure = failures[-1] if failures else "Plugin installation failed"
        if len(failures) > 1:
            failure = (
                f"{failure} (installation failed after {len(failures)} attempts, "
                f"including {len(failures) - 1} automatic retries)"
            )
        return {"success": False, "plugin_id": plugin.id, "message": failure}

    assert asset is not None
    tracked_exclude_files = list(exclude_files or [])
    if upgrade_mode:
        tracked_exclude_files = apply_upgrade_mode_exclusions(tracked_exclude_files)
    await host.upsert_managed_plugin(
        server_id=server.id,
        source_type="market",
        source_key=str(plugin.id),
        display_name=plugin.title,
        repo_url=plugin.github_url,
        market_plugin_id=plugin.id,
        installed_release_id=asset["release_id"],
        installed_version=asset["release_tag"] or plugin.version or "unknown",
        installed_asset_name=asset["asset_name"],
        asset_glob=derive_asset_glob(asset["asset_name"], asset["release_tag"]),
        custom_install_path=asset["custom_install_path"],
        exclude_dirs=list(exclude_dirs or []),
        exclude_files=tracked_exclude_files,
    )
    plugin.download_count += 1
    plugin.install_count += 1
    db.add(plugin)
    await db.commit()
    return {
        "success": True,
        "plugin_id": plugin.id,
        "message": result.message,
        "selected_asset_name": asset["asset_name"],
        "steam_runtime": asset.get("steam_runtime"),
        "linux_runtime_profile": linux_runtime_profile,
        "restart_required": True,
        "next_step": (
            "Restart (or start) the server and wait for startup before locating generated configs."
        ),
    }


@asynccontextmanager
async def _optional_server_lock(
    server_id: int,
    acquire_lock: bool,
    operation: str = "plugin_install_plan",
) -> AsyncIterator[None]:
    if not acquire_lock:
        yield
        return
    async with host.maintenance_lock_service.get(
        server_id, operation=operation, wait=False, ttl=1800
    ):
        yield


def _restart_payload(completed: Iterable[dict[str, Any]]) -> dict[str, Any]:
    if not any(bool(item.get("restart_required")) for item in completed):
        return {"restart_required": False}
    return {
        "restart_required": True,
        "next_step": (
            "Restart (or start) the server and wait for startup before searching for, reading, "
            "or patching generated configuration files."
        ),
    }


async def _prepare_plugin_execution(
    db: AsyncSession,
    refreshed_server: Server,
    user: User,
    plugin_id: int,
    acknowledged_warning_rule_ids: Iterable[int],
    expected_plan_hash: str | None,
    include_dependencies: bool,
    download_url: str | None,
    acknowledge_framework_mismatch: bool = False,
    acknowledge_ai_unreviewed: bool = False,
    force_reinstall: bool = False,
) -> tuple[
    dict[str, Any], set[int], dict[int, MarketPlugin], dict[int, dict[str, Any]], dict | None
]:
    refreshed_plan = await host.build_plugin_install_plan(
        db,
        refreshed_server.id,
        plugin_id,
        include_dependencies=include_dependencies,
        server=refreshed_server,
    )
    if expected_plan_hash and refreshed_plan["plan_hash"] != expected_plan_hash:
        raise PluginPlanError("Plugin plan changed; review and approve the new plan")
    host.validate_plugin_plan_acknowledgements(
        refreshed_plan,
        acknowledged_warning_rule_ids,
        acknowledge_framework_mismatch=acknowledge_framework_mismatch,
        acknowledge_ai_unreviewed=acknowledge_ai_unreviewed,
    )
    installed = set(refreshed_plan["already_installed"])
    if force_reinstall:
        installed.clear()
    plugins = await host.MarketPlugin.get_by_ids(db, refreshed_plan["installation_order"])
    by_id = {plugin.id: plugin for plugin in plugins}
    ordinary_plugin_ids = [
        current_id
        for current_id in refreshed_plan["installation_order"]
        if current_id not in installed
        and (plugin := by_id.get(current_id)) is not None
        and host._panel_framework_key(plugin) is None
    ]
    linux_runtime_profile = None
    if ordinary_plugin_ids:
        from services.linux_runtime_service import detect_linux_runtime_profile

        linux_runtime_profile = await detect_linux_runtime_profile(refreshed_server)
    resolved_assets: dict[int, dict[str, Any]] = {}
    for current_id in refreshed_plan["installation_order"]:
        if current_id in installed:
            continue
        plugin = by_id.get(current_id)
        if plugin is None:
            raise PluginPlanError(f"Plugin {current_id} disappeared before execution")
        if current_id == plugin_id and download_url:
            resolved_assets[current_id] = host._asset_from_download_url(plugin, download_url)
            if metadata(plugin):
                resolved_assets[current_id].update(await selected_asset_rules(plugin, download_url))
        elif current_id in ordinary_plugin_ids:
            resolved_assets[current_id] = await host._latest_release_asset(
                db, plugin, refreshed_server, user, linux_runtime_profile
            )
    return refreshed_plan, installed, by_id, resolved_assets, linux_runtime_profile


async def execute_plugin_install_plan(
    db: AsyncSession,
    server: Server,
    user: User,
    plugin_id: int,
    acknowledged_warning_rule_ids: Iterable[int] = (),
    *,
    expected_plan_hash: str | None = None,
    progress: ProgressCallback | None = None,
    acquire_lock: bool = True,
    lock_operation: str = "plugin_install_plan",
    operation_id: str | None = None,
    include_dependencies: bool = True,
    download_url: str | None = None,
    upgrade_mode: bool = False,
    exclude_dirs: list[str] | None = None,
    exclude_files: list[str] | None = None,
    acknowledge_framework_mismatch: bool = False,
    acknowledge_ai_unreviewed: bool = False,
    force_reinstall: bool = False,
) -> dict[str, Any]:
    """Recompute and execute a plan, stopping immediately after any failure."""
    plan = await host.build_plugin_install_plan(
        db,
        server.id,
        plugin_id,
        include_dependencies=include_dependencies,
        server=server,
    )
    if expected_plan_hash and plan["plan_hash"] != expected_plan_hash:
        raise PluginPlanError("Plugin plan changed; review and approve the new plan")
    host.validate_plugin_plan_acknowledgements(
        plan,
        acknowledged_warning_rule_ids,
        acknowledge_framework_mismatch=acknowledge_framework_mismatch,
        acknowledge_ai_unreviewed=acknowledge_ai_unreviewed,
    )
    completed: list[dict[str, Any]] = []

    async with host._optional_server_lock(server.id, acquire_lock, lock_operation):
        # Permission and plan state are intentionally checked again immediately
        # before any remote mutation.
        refreshed_server = (
            await host.Server.get_by_id(db, server.id)
            if user.is_admin
            else await host.Server.get_by_id_and_user(db, server.id, user.id)
        )
        if refreshed_server is None:
            raise PluginPlanError("Server permission changed before execution")
        (
            refreshed_plan,
            installed,
            by_id,
            resolved_assets,
            linux_runtime_profile,
        ) = await host._prepare_plugin_execution(
            db,
            refreshed_server,
            user,
            plugin_id,
            acknowledged_warning_rule_ids,
            expected_plan_hash,
            include_dependencies,
            download_url,
            acknowledge_framework_mismatch,
            acknowledge_ai_unreviewed,
            force_reinstall,
        )

        for current_id in refreshed_plan["installation_order"]:
            step_id = f"plugin:{current_id}"
            latest_plan = await host.build_plugin_install_plan(
                db,
                server.id,
                plugin_id,
                include_dependencies=include_dependencies,
                server=refreshed_server,
            )
            host.validate_plugin_plan_acknowledgements(
                latest_plan,
                acknowledged_warning_rule_ids,
                acknowledge_framework_mismatch=acknowledge_framework_mismatch,
                acknowledge_ai_unreviewed=acknowledge_ai_unreviewed,
            )
            if latest_plan["installation_order"] != refreshed_plan["installation_order"]:
                raise PluginPlanError(
                    "Plugin dependency graph changed during execution; review a new plan"
                )
            if current_id in installed:
                completed.append({"plugin_id": current_id, "success": True, "skipped": True})
                await host._emit_plan_progress(
                    progress,
                    f"Plugin {current_id} is already installed",
                    step_id=step_id,
                    step_status="skipped",
                )
                continue
            plugin = by_id.get(current_id)
            if plugin is None:
                raise PluginPlanError(f"Plugin {current_id} disappeared before execution")
            await host._emit_plan_progress(
                progress,
                f"Installing {plugin.title}",
                step_id=step_id,
                step_status="running",
            )
            try:
                result = await host._install_one(
                    db,
                    plugin,
                    refreshed_server,
                    user,
                    progress,
                    operation_id=operation_id,
                    linux_runtime_profile=linux_runtime_profile,
                    resolved_asset=resolved_assets.get(current_id),
                    exclude_dirs=list(exclude_dirs or []),
                    exclude_files=list(exclude_files or []),
                    upgrade_mode=upgrade_mode,
                )
            except Exception as exc:
                result = {
                    "success": False,
                    "plugin_id": current_id,
                    "message": str(exc),
                }
            completed.append(result)
            if not result["success"]:
                await host._emit_plan_progress(
                    progress,
                    str(result.get("message") or f"Failed to install {plugin.title}"),
                    step_id=step_id,
                    step_status="failed",
                )
                completed_ids = {entry["plugin_id"] for entry in completed}
                for remaining_id in refreshed_plan["installation_order"]:
                    if remaining_id in completed_ids:
                        continue
                    await host._emit_plan_progress(
                        progress,
                        "Not started because an earlier plugin failed",
                        step_id=f"plugin:{remaining_id}",
                        step_status="interrupted",
                    )
                return {
                    "success": False,
                    "message": f"Stopped after {plugin.title} failed",
                    "completed": completed,
                    "remaining": [
                        item
                        for item in refreshed_plan["installation_order"]
                        if item not in {entry["plugin_id"] for entry in completed}
                    ],
                    **_restart_payload(completed),
                }
            await host._emit_plan_progress(
                progress,
                f"Installed {plugin.title}",
                step_id=step_id,
                step_status="completed",
            )

    return {
        "success": True,
        "message": "Plugin installation plan completed",
        "completed": completed,
        "remaining": [],
        **_restart_payload(completed),
    }
