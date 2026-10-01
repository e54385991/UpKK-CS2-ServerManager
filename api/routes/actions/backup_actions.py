"""Action stages with their original progress, status and cleanup ordering."""

from services.compat import LateBoundModule

from .action_context import ActionContext

host = LateBoundModule("api.routes.actions.deployment")


async def _action_backup_plugins(ctx: ActionContext) -> tuple[bool, str]:
    server_id = ctx.server_id
    server = ctx.server
    db = ctx.db
    current_user = ctx.current_user
    ssh_manager = ctx.ssh_manager
    log = ctx.log
    progress_callback = ctx.progress_callback
    await host.send_deployment_update(server_id, "status", "Backing up plugins...")
    success, message = await ssh_manager.backup_plugins(server, progress_callback)

    if success:
        s3_success, s3_message = await host.upload_latest_plugin_backup_to_s3(
            db,
            server,
            current_user,
            ssh_manager,
            progress_callback=progress_callback,
        )
        if s3_success:
            if s3_message:
                message = f"{message}\n{s3_message}"
            log.status = "success"
            log.output = message
            await host.send_deployment_update(
                server_id, "complete", "Plugins backed up successfully"
            )
        else:
            success = False
            message = f"{message}\n{s3_message}"
            log.status = "failed"
            log.error_message = message
            await host.send_deployment_update(
                server_id, "error", f"Plugin backup S3 upload failed: {s3_message}"
            )
    else:
        log.status = "failed"
        log.error_message = message
        await host.send_deployment_update(server_id, "error", f"Plugin backup failed: {message}")
    return success, message
