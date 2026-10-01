"""Action stages with their original progress, status and cleanup ordering."""

from services.compat import LateBoundModule

from .action_context import ActionContext

host = LateBoundModule("api.routes.actions.deployment")


async def _action_deploy(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    db = ctx.db
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    deployment_lock_key = ctx.deployment_lock_key
    progress_callback = ctx.progress_callback
    server.status = host.ServerStatus.DEPLOYING
    await db.commit()

    try:
        await host.send_deployment_update(server_id, "status", "Connecting to server via SSH...")
        success, message = await ssh_manager.deploy_cs2_server(server, progress_callback)
        if success:
            server.status = host.ServerStatus.STOPPED
            server.last_deployed = host.get_current_time()
            log.status = "success"
            log.output = message
            await host.send_deployment_update(
                server_id, "complete", "Deployment completed successfully"
            )
        else:
            server.status = host.ServerStatus.ERROR
            log.status = "failed"
            log.error_message = message
            await host.send_deployment_update(server_id, "error", f"Deployment failed: {message}")
    finally:
        await host.redis_manager.delete(deployment_lock_key)
        host._store_task(
            host.asyncio.create_task(host.clear_deployment_progress_after_delay(server_id))
        )
    return success, message


async def _action_start(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    clear_crash_protection = ctx.clear_crash_protection
    await host.send_deployment_update(server_id, "status", "Starting server...")
    await clear_crash_protection()
    success, message = await ssh_manager.start_server(server, progress_callback)
    if success:
        server.status = host.ServerStatus.RUNNING
        log.status = "success"
        log.output = message
        host.server_monitor.reset_restart_history(server_id)
        await host.send_deployment_update(server_id, "complete", "Server started successfully")
    else:
        server.status = host.ServerStatus.ERROR
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"Start failed: {message}")
    return success, message


async def _action_stop(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    clear_crash_protection = ctx.clear_crash_protection
    await host.send_deployment_update(server_id, "status", "Stopping server...")
    await clear_crash_protection()
    success, message = await ssh_manager.stop_server(server)

    if success:
        server.status = host.ServerStatus.STOPPED
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "Server stopped successfully")
    else:
        server.status = host.ServerStatus.ERROR
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"Stop failed: {message}")
    return success, message


async def _action_restart(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    clear_crash_protection = ctx.clear_crash_protection
    clear_execstack = ctx.clear_execstack
    clear_execstack_targets = ctx.clear_execstack_targets
    await host.send_deployment_update(server_id, "status", "Restarting server...")

    await clear_crash_protection()

    success, message = await ssh_manager.stop_server(server)

    if not success:
        await host.send_deployment_update(server_id, "output", f"Stop returned: {message}")
        await host.send_deployment_update(
            server_id,
            "output",
            "Proceeding with start (defensive checks will ensure cleanup)...",
        )
    else:
        await host.send_deployment_update(
            server_id, "output", "Server stopped successfully, starting again..."
        )

    if clear_execstack:
        if not success:
            await host.send_deployment_update(
                server_id,
                "output",
                "⚠ Plugin execstack cleanup skipped because the server did not confirm it stopped; continuing restart.",
            )
        else:
            await host.send_deployment_update(
                server_id,
                "output",
                "Clearing executable-stack flags from configured plugin targets...",
            )
            fixed, detail = await host.run_clear_execstack(
                ssh_manager, server, clear_execstack_targets
            )
            message = (
                "✓ Plugin execstack cleanup completed"
                if fixed
                else "⚠ Plugin execstack cleanup failed; continuing restart"
            )
            await host.send_deployment_update(server_id, "output", f"{message}: {detail}")

    await host.asyncio.sleep(0.5)

    success, message = await ssh_manager.start_server(server, progress_callback)
    if success:
        server.status = host.ServerStatus.RUNNING
        log.status = "success"
        log.output = message
        host.server_monitor.reset_restart_history(server_id)
        await host.send_deployment_update(server_id, "complete", "Server restarted successfully")
    else:
        server.status = host.ServerStatus.ERROR
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"Restart failed: {message}")
    return success, message


async def _action_status(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    await host.send_deployment_update(server_id, "status", "Checking server status...")
    success, status_msg = await ssh_manager.get_server_status(server)

    server.last_status_check = host.get_current_time()

    if success:
        if status_msg == "running":
            server.status = host.ServerStatus.RUNNING
        elif status_msg == "stopped":
            server.status = host.ServerStatus.STOPPED
        else:
            server.status = host.ServerStatus.UNKNOWN

        log.status = "success"
        log.output = status_msg
        message = f"Server is {status_msg}"
        await host.send_deployment_update(server_id, "complete", message)
    else:
        server.status = host.ServerStatus.UNKNOWN
        log.status = "failed"
        log.error_message = status_msg
        message = f"Failed to get status: {status_msg}"
        success = False
        await host.send_deployment_update(server_id, "error", message)
    return success, message


async def _action_update(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    clear_crash_protection = ctx.clear_crash_protection
    clear_execstack = ctx.clear_execstack
    clear_execstack_targets = ctx.clear_execstack_targets
    await host.send_deployment_update(server_id, "status", "Updating server...")
    await clear_crash_protection()
    if clear_execstack:
        success, message = await ssh_manager.update_server(
            server,
            progress_callback,
            clear_execstack=True,
            clear_execstack_targets=clear_execstack_targets,
        )
    else:
        success, message = await ssh_manager.update_server(server, progress_callback)

    if success:
        server.last_update_time = host.get_current_time()
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "Server updated successfully")
    else:
        server.status = host.ServerStatus.ERROR
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"Update failed: {message}")
    return success, message


async def _action_validate(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    clear_execstack = ctx.clear_execstack
    clear_execstack_targets = ctx.clear_execstack_targets
    await host.send_deployment_update(server_id, "status", "Updating and validating server...")
    if clear_execstack:
        success, message = await ssh_manager.validate_server(
            server,
            progress_callback,
            clear_execstack=True,
            clear_execstack_targets=clear_execstack_targets,
        )
    else:
        success, message = await ssh_manager.validate_server(server, progress_callback)

    if success:
        log.status = "success"
        log.output = message
        await host.send_deployment_update(server_id, "complete", "Server validated successfully")
    else:
        server.status = host.ServerStatus.ERROR
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"Validation failed: {message}")
    return success, message
