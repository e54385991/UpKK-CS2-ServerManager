"""Game operations for SSHManager."""

# ruff: noqa: F403,F405

from .common import *


class GameSelfCheckMixin(SSHMixinBase):
    """Focused game lifecycle capability."""

    async def _selfcheck_steamclient(
        self, server: Server, send_progress, issues_found, issues_fixed
    ) -> None:
        await send_progress("Checking steamclient.so symlink...")
        sdk_dir = f"/home/{server.ssh_user}/.steam/sdk64"
        target = f"{sdk_dir}/steamclient.so"
        _, output, _ = await self.execute_command(
            f"test -L {target} && test -e {target} && echo 'valid' || echo 'missing'"
        )
        if "missing" not in output:
            await send_progress("✓ steamclient.so symlink is valid")
            return
        issues_found.append("steamclient.so symlink missing or broken")
        await send_progress("✗ steamclient.so symlink missing or broken - attempting to fix...")
        await self.execute_command(f"mkdir -p {sdk_dir}")
        source = f"{server.game_directory}/steamcmd/linux64/steamclient.so"
        _, source_output, _ = await self.execute_command(
            f"test -f {source} && echo 'found' || echo 'notfound'"
        )
        if "found" not in source_output:
            await send_progress(f"✗ steamclient.so source not found at {source}")
            return
        linked, _, _ = await self.execute_command(f"ln -sf {source} {target}")
        if linked:
            issues_fixed.append("steamclient.so symlink")
            await send_progress("✓ steamclient.so symlink created successfully")
        else:
            await send_progress("✗ Failed to create steamclient.so symlink")

    async def _selfcheck_summary(
        self, send_progress, issues_found, issues_fixed
    ) -> Tuple[bool, str]:
        await send_progress("=" * 60)
        await send_progress("Self-check completed!")
        await send_progress("=" * 60)
        if issues_found:
            await send_progress(f"Issues found: {len(issues_found)}")
            for issue in issues_found:
                await send_progress(f"  - {issue}")
        if issues_fixed:
            await send_progress(f"Issues fixed: {len(issues_fixed)}")
            for fix in issues_fixed:
                await send_progress(f"  ✓ {fix}")
        if not issues_found:
            await send_progress("✓ No issues found - server is ready to start")
            return True, "Server self-check passed"
        if len(issues_fixed) == len(issues_found):
            await send_progress("✓ All issues were automatically fixed")
            return True, "Server self-check completed with auto-fixes"
        unfixed = len(issues_found) - len(issues_fixed)
        await send_progress(f"⚠ {unfixed} issue(s) could not be automatically fixed")
        return False, f"{unfixed} issues remain"

    async def perform_server_selfcheck(
        self, server: Server, progress_callback=None
    ) -> Tuple[bool, str]:
        """
        Perform universal self-checks on the CS2 server and automatically fix common issues.

        Checks performed:
        - CS2 executable exists and has proper permissions
        - steamclient.so symlink exists and is valid
        - gameinfo.gi is properly configured for Metamod (if installed)
        - Auto-restart script is deployed

        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates

        Returns:
            Tuple[bool, str]: (success, message)
        """

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        try:
            await send_progress("=" * 60)
            await send_progress("Performing server self-check and auto-fix...")
            await send_progress("=" * 60)

            issues_found = []
            issues_fixed = []

            # Check 1: CS2 executable exists and has proper permissions
            await send_progress("Checking CS2 executable...")
            cs2_executable = f"{server.game_directory}/cs2/game/bin/linuxsteamrt64/cs2"
            verify_cmd = f"test -f {cs2_executable} && echo 'exists'"
            verify_success, verify_stdout, _ = await self.execute_command(verify_cmd)

            if not verify_success or "exists" not in verify_stdout:
                issues_found.append("CS2 executable not found")
                await send_progress("✗ CS2 executable not found - server may not be deployed")
            else:
                # Ensure executable has proper permissions
                chmod_cmd = f"chmod +x {cs2_executable}"
                chmod_success, _, _ = await self.execute_command(chmod_cmd)
                if chmod_success:
                    await send_progress("✓ CS2 executable found and permissions set")
                else:
                    await send_progress("⚠ CS2 executable found but could not set permissions")

            await self._selfcheck_steamclient(server, send_progress, issues_found, issues_fixed)

            # Check 3: gameinfo.gi for Metamod
            await send_progress("Checking gameinfo.gi configuration...")
            cs2_dir = f"{server.game_directory}/cs2"
            gameinfo_path = f"{cs2_dir}/game/csgo/gameinfo.gi"
            metamod_dir = f"{cs2_dir}/game/csgo/addons/metamod"

            # Check if Metamod is installed
            check_mm_cmd = f"test -d {metamod_dir} && echo 'exists'"
            mm_exists_success, mm_exists_stdout, _ = await self.execute_command(check_mm_cmd)

            if mm_exists_success and "exists" in mm_exists_stdout:
                # Check if gameinfo.gi exists
                check_gi_cmd = f"test -f {gameinfo_path} && echo 'exists'"
                gi_exists_success, gi_exists_stdout, _ = await self.execute_command(check_gi_cmd)

                if gi_exists_success and "exists" in gi_exists_stdout:
                    # Check if Metamod is configured in gameinfo.gi
                    check_mm_line = f"grep -q 'addons/metamod' {gameinfo_path} && echo 'found' || echo 'notfound'"
                    check_line_success, check_line_stdout, _ = await self.execute_command(
                        check_mm_line
                    )

                    if "notfound" in check_line_stdout:
                        issues_found.append("Metamod not configured in gameinfo.gi")
                        await send_progress(
                            "✗ Metamod installed but not configured in gameinfo.gi - attempting to fix..."
                        )

                        # Backup gameinfo.gi
                        backup_cmd = (
                            f"cp {gameinfo_path} {gameinfo_path}.backup.$(date +%Y%m%d_%H%M%S)"
                        )
                        await self.execute_command(backup_cmd)

                        # Add Metamod to gameinfo.gi
                        sed_cmd = f"sed -i '/Game_LowViolence/a\\			Game\\tcsgo/addons/metamod' {gameinfo_path}"
                        sed_success, _, _ = await self.execute_command(sed_cmd)

                        if sed_success:
                            issues_fixed.append("gameinfo.gi Metamod configuration")
                            await send_progress("✓ Metamod added to gameinfo.gi successfully")
                        else:
                            await send_progress("✗ Failed to update gameinfo.gi automatically")
                    else:
                        await send_progress("✓ Metamod is properly configured in gameinfo.gi")
                else:
                    await send_progress(
                        "⚠ gameinfo.gi not found (Metamod installed but game not deployed)"
                    )
            else:
                await send_progress("✓ Metamod not installed - gameinfo.gi check skipped")

            # Check 4: Auto-restart script
            await send_progress("Checking auto-restart script...")
            autorestart_script_path = f"{server.game_directory}/cs2_autorestart.sh"

            check_script_cmd = f"test -f {autorestart_script_path} && test -x {autorestart_script_path} && echo 'exists'"
            script_success, script_stdout, _ = await self.execute_command(check_script_cmd)

            if not script_success or "exists" not in script_stdout:
                issues_found.append("Auto-restart script not found or not executable")
                await send_progress("✗ Auto-restart script missing - attempting to deploy...")

                # Deploy the script
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                local_script_path = os.path.join(script_dir, "scripts", "cs2_autorestart.sh")

                try:
                    async with await anyio.open_file(local_script_path, "r") as script_file:
                        script_content = await script_file.read()

                    create_script_cmd = f"cat > {autorestart_script_path} << 'EOFSCRIPT'\n{script_content}\nEOFSCRIPT"
                    deploy_success, _, _ = await self.execute_command(create_script_cmd, timeout=10)

                    if deploy_success:
                        chmod_script_cmd = f"chmod +x {autorestart_script_path}"
                        await self.execute_command(chmod_script_cmd)
                        issues_fixed.append("auto-restart script")
                        await send_progress("✓ Auto-restart script deployed successfully")
                    else:
                        await send_progress("✗ Failed to deploy auto-restart script")
                except Exception as e:
                    await send_progress(f"✗ Error deploying auto-restart script: {str(e)}")
            else:
                await send_progress("✓ Auto-restart script is deployed and executable")

            return await self._selfcheck_summary(send_progress, issues_found, issues_fixed)

        except Exception as e:
            await send_progress(f"Self-check error: {str(e)}")
            return False, f"Self-check error: {str(e)}"

    async def _running_server_session_managers(
        self,
        server: Server,
        timeout: int = 10,
    ) -> List[str]:
        """Find configured and legacy sessions for a server on this connection."""
        return await find_running_session_managers(
            self.execute_command,
            server.session_manager,
            session_name(server.id),
            timeout=timeout,
        )

    async def _configured_session_manager_available_connected(
        self,
        server: Server,
        timeout: int = 10,
    ) -> Tuple[bool, str]:
        """Check the selected manager without changing any existing session."""
        manager = normalize_session_manager(server.session_manager)
        available, _, _ = await self.execute_command(
            availability_command(manager),
            timeout=timeout,
        )
        if available:
            return True, f"{manager} is available"
        return False, (f"Selected session manager '{manager}' is not installed on the remote host")

    @classmethod
    def _cs2_executable_path(cls, server: Server) -> str:
        """Return the required Linux CS2 executable path for a server."""
        return posixpath.join(
            server.game_directory.rstrip("/"),
            cls.CS2_EXECUTABLE_RELATIVE_PATH,
        )

    async def _cs2_executable_exists_connected(
        self,
        server: Server,
        timeout: int = 10,
    ) -> Tuple[bool, str]:
        """Check the required executable using the current SSH connection."""
        executable_path = self._cs2_executable_path(server)
        success, stdout, _ = await self.execute_command(
            f"test -f {shlex.quote(executable_path)} && echo exists",
            timeout=timeout,
        )
        return success and stdout.strip() == "exists", executable_path

    @staticmethod
    def _missing_cs2_executable_message(executable_path: str) -> str:
        return f"CS2 可执行文件不存在：{executable_path}。请重新部署服务器或运行修复/验证。"

    async def check_session_manager_available(
        self,
        server: Server,
        timeout: int = 10,
    ) -> Tuple[bool, str]:
        """Check start/restart prerequisites before stopping a live server."""
        success, message = await self.connect(server)
        if not success:
            return False, f"Connection failed during start/restart preflight: {message}"

        try:
            executable_exists, executable_path = await self._cs2_executable_exists_connected(
                server, timeout=timeout
            )
            if not executable_exists:
                return False, self._missing_cs2_executable_message(executable_path)

            return await self._configured_session_manager_available_connected(
                server,
                timeout=timeout,
            )
        finally:
            await self.disconnect()
