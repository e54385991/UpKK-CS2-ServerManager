"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.game_deployment")


async def _prepare_cs2_home(
    self: SSHMixinBase, server: Server, send_progress
) -> tuple[bool, str] | None:
    check_user_cmd = "id cs2server > /dev/null 2>&1 && echo 'exists' || echo 'missing'"
    user_success, user_stdout, _ = await self.execute_command(check_user_cmd)

    if "missing" in user_stdout or not user_success:
        await send_progress("✗ Environment not initialized: cs2server user does not exist")
        return False, (
            "Environment not initialized. Please create cs2server user first:\n"
            "sudo useradd -m -s /bin/bash cs2server\n"
            "sudo passwd cs2server\n"
            "sudo usermod -aG sudo cs2server  # Optional: for installing dependencies\n\n"
            "Or use a different game_directory path that the current user can access."
        )

    await send_progress("✓ cs2server user exists")

    # Verify cs2server home directory has correct permissions
    check_perms_cmd = "test -w /home/cs2server && echo 'writable' || echo 'not_writable'"
    perm_success, perm_stdout, _ = await self.execute_command(check_perms_cmd)

    if "not_writable" in perm_stdout or not perm_success:
        await send_progress("✗ /home/cs2server directory is not writable")

        # Try to fix permissions if we have sudo password
        privileged_password = server.sudo_password or server.ssh_password
        if privileged_password:
            await send_progress("Attempting to fix permissions...")
            fix_perms_cmd = f"echo '{privileged_password}' | sudo -S chown -R cs2server:cs2server /home/cs2server && echo '{privileged_password}' | sudo -S chmod 755 /home/cs2server"
            fix_success, _, fix_stderr = await self.execute_command(fix_perms_cmd)

            if fix_success:
                await send_progress("✓ Permissions fixed for /home/cs2server")
            else:
                return False, (
                    "Cannot create directory in /home/cs2server: Permission denied.\n"
                    "Please ensure the directory has correct permissions:\n"
                    "sudo chown -R cs2server:cs2server /home/cs2server\n"
                    "sudo chmod 755 /home/cs2server"
                )
        else:
            return False, (
                "Cannot create directory in /home/cs2server: Permission denied.\n"
                "Please ensure the directory has correct permissions:\n"
                "sudo chown -R cs2server:cs2server /home/cs2server\n"
                "sudo chmod 755 /home/cs2server"
            )
    else:
        await send_progress("✓ /home/cs2server is writable")
    return None


async def _install_deployment_tools(
    self: SSHMixinBase, server: Server, missing_tools: list[str], send_progress
) -> None:
    check_apt = "command -v apt-get > /dev/null && echo 'apt' || echo 'none'"
    _, pkg_mgr, _ = await self.execute_command(check_apt)

    if "apt" in pkg_mgr:
        install_cmd = (
            f"{host.apt_get_command('update')} && {host.apt_get_command('install', missing_tools)}"
        )
        success, stdout, stderr = await self.execute_command(install_cmd, timeout=600)

        if not success:
            await send_progress("Trying to install with sudo and automatic retries...")
            for attempt in range(1, host.APT_RETRY_ATTEMPTS + 1):
                success, stdout, stderr = await self.execute_sudo_command(
                    install_cmd,
                    server.sudo_password or server.ssh_password,
                    timeout=600,
                )
                if success:
                    break
                await send_progress(
                    f"⚠ Dependency installation attempt {attempt}/"
                    f"{host.APT_RETRY_ATTEMPTS} failed: {stderr.strip() or stdout.strip()}"
                )
                if attempt < host.APT_RETRY_ATTEMPTS:
                    delay = host.APT_RETRY_DELAYS_SECONDS[
                        min(attempt - 1, len(host.APT_RETRY_DELAYS_SECONDS) - 1)
                    ]
                    await send_progress(f"Retrying in {delay} seconds...")
                    await host.asyncio.sleep(delay)

            if success:
                await send_progress(f"✓ Successfully installed: {', '.join(missing_tools)}")
            else:
                await send_progress(
                    f"⚠ Could not install tools. Please run: "
                    f"sudo apt-get install {' '.join(missing_tools)}"
                )
        else:
            await send_progress(f"✓ Successfully installed: {', '.join(missing_tools)}")


async def _prepare_deployment_tools(
    self: SSHMixinBase, server: Server, send_progress
) -> tuple[bool, str] | None:
    session_manager = host.normalize_session_manager(server.session_manager)
    required_tools = ["wget", "tar", session_manager, "unzip"]
    missing_tools = []
    for tool in required_tools:
        success, stdout, stderr = await self.execute_command(f"command -v {tool}")
        if not success:
            await send_progress(f"⚠ Warning: {tool} not found")
            missing_tools.append(tool)
        else:
            await send_progress(f"✓ Found {tool}: {stdout.strip()}")

    # Try to install missing tools
    if missing_tools:
        await send_progress(f"Attempting to install missing tools: {', '.join(missing_tools)}")
        # Check package manager
        await host._install_deployment_tools(self, server, missing_tools, send_progress)

        unresolved_tools = []
        for tool in missing_tools:
            tool_success, _, _ = await self.execute_command(f"command -v {tool}")
            if not tool_success:
                unresolved_tools.append(tool)
        if unresolved_tools:
            return False, (
                "Required tools are still missing after automatic installation: "
                f"{', '.join(unresolved_tools)}"
            )
    return None
