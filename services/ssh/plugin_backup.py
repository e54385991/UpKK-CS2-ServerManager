"""PluginBackupMixin implementation."""

# ruff: noqa: F403,F405

from .common import *


class PluginBackupMixin(SSHMixinBase):
    async def _find_backup_items(self, csgo_dir: str, send_progress) -> list[str]:
        items: list[str] = []
        checks = (
            ("addons", "addons/", "folder"),
            ("cfg", "cfg/", "folder"),
            ("gameinfo.gi", "gameinfo.gi", "file"),
        )
        for item, label, _kind in checks:
            flag = "-d" if item != "gameinfo.gi" else "-f"
            success, stdout, _ = await self.execute_command(
                f"test {flag} {csgo_dir}/{item} && echo 'exists'"
            )
            if success and "exists" in stdout:
                items.append(item)
                await send_progress(f"✓ Found: {label}")
            else:
                await send_progress(f"⚠ Warning: {label} not found, skipping")
        return items

    async def _prepare_plugin_backup(self, server: Server, send_progress):
        game_dir = server.game_directory.rstrip("/")
        csgo_dir = f"{game_dir}/cs2/game/csgo"
        check_success, check_stdout, _ = await self.execute_command(
            f"test -d {csgo_dir} && echo 'exists'"
        )
        if not check_success or "exists" not in check_stdout:
            return None, "CS2 server not found. Please deploy the server first."
        await send_progress(f"✓ CS2 server directory found: {csgo_dir}")

        backups_dir = f"{game_dir}/backups"
        await send_progress(f"Creating backups directory: {backups_dir}")
        mkdir_success, _, mkdir_stderr = await self.execute_command(
            f"mkdir -p {shlex.quote(backups_dir)}"
        )
        if not mkdir_success:
            error_msg = (
                mkdir_stderr.strip()
                if mkdir_stderr and mkdir_stderr.strip()
                else "Failed to create backups directory"
            )
            await send_progress(f"✗ {error_msg}")
            return None, f"Failed to create backups directory: {error_msg}"
        await send_progress(f"✓ Backups directory ready: {backups_dir}")

        ts_success, timestamp, _ = await self.execute_command("date '+%Y-%m-%d-%H%M%S'")
        timestamp = (
            timestamp.strip()
            if ts_success and timestamp.strip()
            else datetime.now().strftime("%Y-%m-%d-%H%M%S")
        )
        backup_filename = f"{timestamp}.tar.gz"
        backup_path = f"{backups_dir}/{backup_filename}"
        await send_progress(f"Backup will be saved to: {backup_path}")
        return (game_dir, csgo_dir, backups_dir, backup_filename, backup_path, timestamp), ""

    async def _create_plugin_backup(
        self, csgo_dir: str, backups_dir: str, backup_path: str, send_progress
    ):
        items = await self._find_backup_items(csgo_dir, send_progress)
        if not items:
            return (
                False,
                "No items found to backup. Please ensure the server is deployed and has plugins installed.",
                None,
            )
        await send_progress(f"Creating backup archive with {len(items)} item(s)...")
        tar_cmd = (
            f"cd {shlex.quote(csgo_dir)} && tar -czf {shlex.quote(backup_path)} {' '.join(items)}"
        )
        await send_progress(f"Creating compressed backup: {backup_path}")
        await send_progress(f"[DEBUG] Executing command: {tar_cmd}")
        tar_success, tar_stdout, tar_stderr = await self.execute_command_streaming(
            tar_cmd, output_callback=send_progress, timeout=600
        )
        exists_success, exists_out, _ = await self.execute_command(
            f"test -f {shlex.quote(backup_path)} && echo 'exists'"
        )
        created = exists_success and "exists" in exists_out
        await send_progress(f"[DEBUG] Backup file created: {created}")
        await send_progress(f"[DEBUG] Tar exit code successful: {tar_success}")
        if not created:
            error_detail = (
                f"Command: {tar_cmd}\nExit successful: {tar_success}\nFile created: {created}\n"
                f"Stderr: {tar_stderr.strip() if tar_stderr and tar_stderr.strip() else '(empty)'}\n"
                f"Stdout: {tar_stdout.strip() if tar_stdout and tar_stdout.strip() else '(empty)'}"
            )
            await send_progress("✗ Backup creation failed - file not created")
            for label, value in (
                ("Command", tar_cmd),
                ("Exit successful", tar_success),
                ("File created", created),
            ):
                await send_progress(f"{label}: {value}")
            await send_progress(
                f"Stderr: {tar_stderr.strip() if tar_stderr and tar_stderr.strip() else '(empty)'}"
            )
            await send_progress(
                f"Stdout: {tar_stdout.strip() if tar_stdout and tar_stdout.strip() else '(empty)'}"
            )
            check_success, check_out, _ = await self.execute_command(
                "which tar && tar --version | head -1"
            )
            if check_success:
                await send_progress(f"[INFO] Tar location and version: {check_out.strip()}")
            dir_success, dir_out, _ = await self.execute_command(
                f"ls -ld {shlex.quote(backups_dir)}"
            )
            if dir_success:
                await send_progress(f"[INFO] Backup directory permissions: {dir_out.strip()}")
            return False, f"Backup creation failed:\n{error_detail}", None
        if not tar_success:
            suffix = f" (stderr: {tar_stderr.strip()})" if tar_stderr and tar_stderr.strip() else ""
            await send_progress(
                f"[WARN] Tar returned non-zero exit code but file was created successfully{suffix}"
            )
        await send_progress("✓ Backup archive created successfully")
        return True, "", tar_stderr

    async def _finish_plugin_backup(self, context, send_progress):
        game_dir, _csgo_dir, backups_dir, backup_filename, backup_path, timestamp = context
        file_size = None
        size_success, size_out, _ = await self.execute_command(
            f"stat -f%z {shlex.quote(backup_path)} 2>/dev/null || stat -c%s {shlex.quote(backup_path)} 2>/dev/null"
        )
        if size_success and size_out.strip():
            file_size = int(size_out.strip())
            units = ((1024**3, "GB"), (1024**2, "MB"), (1024, "KB"))
            for divisor, label in units:
                if file_size >= divisor:
                    await send_progress(f"✓ Backup file size: {file_size / divisor:.2f} {label}")
                    break
            else:
                await send_progress(f"✓ Backup file size: {file_size} bytes")
        await send_progress("=" * 60)
        await send_progress("✓ Plugin backup completed successfully!")
        await send_progress(f"Backup saved to: {backup_path}")
        await send_progress("=" * 60)
        self.last_plugin_backup = {
            "path": backup_path,
            "filename": backup_filename,
            "size": file_size,
            "backups_dir": backups_dir,
            "created_at": timestamp,
        }
        return f"Plugin backup completed successfully. Saved to: {backup_path}"

    async def backup_plugins(self, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Backup plugins (addons, cfg folders and gameinfo.gi file) to a timestamped tar.gz archive

        Creates backup at: {game_directory}/backups/YYYY-MM-DD-HHMMSS.tar.gz
        Backs up from: {game_directory}/cs2/game/csgo/
        - addons/ folder
        - cfg/ folder
        - gameinfo.gi file

        Args:
            server: Server instance
            progress_callback: Optional async callback for progress updates
        Returns: (success: bool, message: str)
        """

        async def send_progress(message: str):
            """Helper to send progress updates"""
            if progress_callback:
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        self.last_plugin_backup = None

        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

        try:
            await send_progress("=" * 60)
            await send_progress("Starting plugin backup...")
            await send_progress("=" * 60)
            context, error = await self._prepare_plugin_backup(server, send_progress)
            if context is None:
                return False, error
            created, error, _ = await self._create_plugin_backup(
                context[1], context[2], context[4], send_progress
            )
            if not created:
                return False, error
            return True, await self._finish_plugin_backup(context, send_progress)

        except Exception as e:
            await send_progress(f"Backup error: {str(e)}")
            return False, f"Backup error: {str(e)}"
        finally:
            await self.disconnect()
