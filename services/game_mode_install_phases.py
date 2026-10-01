"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules import Server, User
from services.compat import LateBoundModule
from services.game_mode_recipes import (
    GameModeRecipe,
)
from services.game_mode_types import PlanReport

host = LateBoundModule("services.game_mode_install_service")


async def _wipe_mode_addons(
    db: AsyncSession,
    current_server: Server,
    user: User,
    plan: dict[str, Any],
    completed: list[dict[str, Any]],
    report: PlanReport,
    server: Server,
) -> Server:
    await report("wipe_addons", "running", f"Wiping {plan['addons_path']}")
    restart_manager = host.SSHManager()
    stopped, stop_message = await restart_manager.stop_server(current_server)
    if not stopped:
        raise host.GameModePlanError(
            f"Unable to stop the server before wiping addons: {stop_message}"
        )
    manager = await host.connect(current_server)
    try:
        await host.wipe_addons_directory(manager, plan["addons_path"])
    finally:
        await manager.disconnect()
    cleared = await host._clear_managed_plugins(db, int(current_server.id))
    completed.append({"action": "wipe_addons", "success": True, "cleared_tracking": cleared})
    await report("wipe_addons", "completed", "Addons directory wiped")
    current_server = (
        await host.Server.get_by_id(db, server.id)
        if user.is_admin
        else await host.Server.get_by_id_and_user(db, server.id, user.id)
    )
    if current_server is None:
        raise host.GameModePlanError("Server disappeared after addons wipe")

    return current_server


async def _install_mode_framework(
    db: AsyncSession,
    current_server: Server,
    user: User,
    plan: dict[str, Any],
    completed: list[dict[str, Any]],
    report: PlanReport,
) -> None:
    async def css_progress(
        message: str,
        _kind: str = "status",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await report(
            "install_counterstrikesharp",
            "running",
            message,
            metadata,
        )

    await report(
        "install_counterstrikesharp",
        "running",
        "Installing CounterStrikeSharp (includes Metamod)",
    )
    success, message = await host.SSHManager().install_counterstrikesharp(
        current_server, css_progress
    )
    if not success:
        raise host.GameModePlanError(message)
    await host.record_framework_installation(current_server, user, "counterstrikesharp")
    completed.append({"action": "install_counterstrikesharp", "success": True})
    await report(
        "install_counterstrikesharp",
        "completed",
        "Installed CounterStrikeSharp",
    )


async def _install_mode_plugins(
    db: AsyncSession,
    current_server: Server,
    user: User,
    plan: dict[str, Any],
    completed: list[dict[str, Any]],
    report: PlanReport,
    recipe: GameModeRecipe,
    wipe_addons: bool,
    acknowledged: set[int],
    operation_id: str | None,
) -> None:
    for title in recipe.market_plugin_titles:
        plugin_plan = plan["plugin_plans"].get(title)
        if plugin_plan is None:
            raise host.GameModePlanError(f"{title} is missing from the plugin market")
        present = False
        if not wipe_addons:
            if title == "cs2kz-metamod":
                present = bool(plan["current"].get("cs2kz"))
            elif title == "CS2-Upkk-PanelPLG-Mapchooser":
                present = bool(plan["current"].get("mapchooser"))
        if present:
            completed.append({"action": f"install:{title}", "success": True, "skipped": True})
            continue

        async def plugin_progress(
            message: str,
            _kind: str = "status",
            _metadata: dict[str, Any] | None = None,
            *,
            step_title: str = title,
        ) -> None:
            await report(f"install:{step_title}", "running", message, _metadata)

        await report(f"install:{title}", "running", f"Installing {title}")
        try:
            host.validate_plugin_plan_acknowledgements(plugin_plan, acknowledged)
        except host.PluginPlanError as exc:
            raise host.GameModePlanError(str(exc)) from exc
        result = await host.execute_plugin_install_plan(
            db,
            current_server,
            user,
            int(plugin_plan["plugin"]["id"]),
            acknowledged,
            expected_plan_hash=None if wipe_addons else plugin_plan.get("plan_hash"),
            progress=plugin_progress,
            acquire_lock=False,
            operation_id=operation_id,
            include_dependencies=True,
        )
        completed.append({"action": f"install:{title}", "result": result})
        if not result.get("success"):
            raise host.GameModePlanError(str(result.get("message") or f"Failed to install {title}"))
        await report(f"install:{title}", "completed", f"Installed {title}")


async def _restart_mode_server(
    db: AsyncSession,
    current_server: Server,
    user: User,
    plan: dict[str, Any],
    completed: list[dict[str, Any]],
    report: PlanReport,
    recipe: GameModeRecipe,
) -> None:
    await report(
        "restart_and_wait",
        "running",
        "Restarting the server and waiting for generated configs",
    )

    async def restart_progress(message: str) -> None:
        await report("restart_and_wait", "running", message)

    restart_manager = host.SSHManager()
    stopped, stop_message = await restart_manager.stop_server(current_server)
    if not stopped:
        current_server.status = host.ServerStatus.ERROR
        db.add(current_server)
        await db.commit()
        raise host.GameModePlanError(
            f"Unable to stop server before plugin initialization: {stop_message}"
        )
    # The plan promises this between the stop and the start: the
    # freshly installed libraries are not mapped by a running
    # process, so patchelf can rewrite them in place.
    await host.run_planned_execstack_step(plan, current_server, report)
    started, start_message = await restart_manager.start_server(current_server, restart_progress)
    if not started:
        current_server.status = host.ServerStatus.ERROR
        db.add(current_server)
        await db.commit()
        raise host.GameModePlanError(
            f"Unable to start server after plugin installation: {start_message}"
        )
    current_server.status = host.ServerStatus.RUNNING
    db.add(current_server)
    await db.commit()

    manager = await host.connect(current_server)
    try:
        await host.wait_for_remote_files(
            manager,
            host.wait_file_paths(current_server, recipe.wait_files),
            progress=restart_progress,
        )
    finally:
        await manager.disconnect()
    completed.append({"action": "restart_and_wait", "success": True})
    await report(
        "restart_and_wait",
        "completed",
        "Restarted and found generated MapChooser configs",
    )
