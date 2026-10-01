"""Action stages with their original progress, status and cleanup ordering."""

from services.compat import LateBoundModule

from .action_context import ActionContext

host = LateBoundModule("api.routes.actions.deployment")


async def _action_install_metamod(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Installing Metamod:Source...")
    success, message = await ssh_manager.install_metamod(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "Metamod installed successfully")
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(
            server_id, "error", f"Metamod installation failed: {message}"
        )
    return success, message


async def _action_install_counterstrikesharp(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Installing CounterStrikeSharp...")
    success, message = await ssh_manager.install_counterstrikesharp(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(
            server_id, "complete", "CounterStrikeSharp installed successfully"
        )
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(
            server_id, "error", f"CounterStrikeSharp installation failed: {message}"
        )
    return success, message


async def _action_update_metamod(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Updating Metamod:Source...")
    success, message = await ssh_manager.update_metamod(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "Metamod updated successfully")
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"Metamod update failed: {message}")
    return success, message


async def _action_update_counterstrikesharp(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Updating CounterStrikeSharp...")
    success, message = await ssh_manager.update_counterstrikesharp(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(
            server_id, "complete", "CounterStrikeSharp updated successfully"
        )
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(
            server_id, "error", f"CounterStrikeSharp update failed: {message}"
        )
    return success, message


async def _action_install_cs2fixes(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Installing CS2Fixes...")
    success, message = await ssh_manager.install_cs2fixes(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "CS2Fixes installed successfully")
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(
            server_id, "error", f"CS2Fixes installation failed: {message}"
        )
    return success, message


async def _action_update_cs2fixes(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Updating CS2Fixes...")
    success, message = await ssh_manager.update_cs2fixes(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "CS2Fixes updated successfully")
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"CS2Fixes update failed: {message}")
    return success, message


async def _action_install_swiftly(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Installing SwiftlyS2...")
    success, message = await ssh_manager.install_swiftly(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "SwiftlyS2 installed successfully")
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(
            server_id, "error", f"SwiftlyS2 installation failed: {message}"
        )
    return success, message


async def _action_update_swiftly(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Updating SwiftlyS2...")
    success, message = await ssh_manager.update_swiftly(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "SwiftlyS2 updated successfully")
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"SwiftlyS2 update failed: {message}")
    return success, message
