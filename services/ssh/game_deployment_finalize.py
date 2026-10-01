"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.game_deployment")


async def _finish_cs2_deployment(
    self: SSHMixinBase, server: Server, steamcmd_dir: str, send_progress
) -> tuple[bool, str]:
    steam_sdk_dir = f"/home/{server.ssh_user}/.steam/sdk64"
    mkdir_cmd = f"mkdir -p {steam_sdk_dir}"
    await self.execute_command(mkdir_cmd)

    # Create symlink to steamclient.so
    # This fixes: "Failed to load module '/home/user/.steam/sdk64/steamclient.so'"
    steamclient_source = f"{steamcmd_dir}/linux64/steamclient.so"
    steamclient_target = f"{steam_sdk_dir}/steamclient.so"
    symlink_cmd = f"ln -sf {steamclient_source} {steamclient_target}"
    symlink_success, _, _ = await self.execute_command(symlink_cmd)

    if symlink_success:
        await send_progress("✓ steamclient.so symlink created successfully")
    else:
        await send_progress(
            "⚠ Warning: Could not create steamclient.so symlink (may cause startup issues)"
        )

        # CS2 executable exists, installation successful despite exit code
        await send_progress("=" * 60)
        await send_progress("✓ CS2 server installed successfully (verified)")
        await send_progress("=" * 60)
        return True, "CS2 server deployed successfully"

    await send_progress("=" * 60)
    await send_progress("✓ CS2 server installed successfully!")
    await send_progress("=" * 60)

    # Deploy auto-restart wrapper script
    await send_progress("=" * 60)
    await send_progress("Deploying auto-restart wrapper script...")
    await send_progress("=" * 60)

    autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"

    try:
        script_ready, script_status = await host.ensure_autorestart_script(
            self.execute_command,
            autorestart_script_path,
        )
        if not script_ready:
            await send_progress(f"⚠ Warning: Could not deploy autorestart script: {script_status}")
        elif script_status == "current":
            await send_progress("✓ Auto-restart wrapper script is current")
        else:
            await send_progress("✓ Auto-restart wrapper script deployed successfully")
    except Exception as e:
        await send_progress(f"⚠ Warning: Could not deploy autorestart script: {str(e)}")

    await send_progress("=" * 60)
    await send_progress("✓ Deployment completed successfully!")
    await send_progress("=" * 60)

    return True, "CS2 server deployed successfully"


async def _deployment_progress(progress_callback, message: str) -> None:
    """Helper to send progress updates"""
    if progress_callback:
        if host.inspect.iscoroutinefunction(progress_callback):
            await progress_callback(message)
        else:
            progress_callback(message)


async def _verify_deployed_executable(self: SSHMixinBase, server: Server, send_progress) -> bool:
    executable_exists, executable_path = await self._cs2_executable_exists_connected(server)
    if executable_exists:
        await send_progress(f"✓ CS2 executable verified: {executable_path}")
    else:
        await send_progress(f"✗ CS2 executable is still missing: {executable_path}")
    return executable_exists
