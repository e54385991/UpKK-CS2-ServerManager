"""Extract and copy a staged GitHub plugin archive onto the game tree."""

from __future__ import annotations

import logging
import shlex
import uuid

from modules import GitHubPluginInstallResponse
from services.plugins.github_install.context import GithubInstallContext, host
from services.plugins.install_mapping import stage_mapping

logger = logging.getLogger(__name__)


async def deploy_plugin_archive(  # noqa: C901
    ctx: GithubInstallContext,
    archive_file: str,
    archive_type: str,
) -> GitHubPluginInstallResponse:
    request = ctx.request
    server = ctx.server
    ssh_manager = ctx.ssh_manager
    remote_temp_dir = ctx.remote_temp_dir
    progress = ctx.progress
    notify_install_result = ctx.notify_install_result
    record_installation = ctx.record_installation
    csgo_dir = ctx.csgo_dir

    # Create extraction directory
    extract_dir = f"{remote_temp_dir}/extracted"
    await ssh_manager.execute_command(f"mkdir -p {extract_dir}")

    # Extract archive (support zip, tar.gz, tar, 7z)
    await progress(f"Extracting {archive_type} archive...")
    if archive_type == "zip":
        extract_cmd = f"unzip -o {archive_file} -d {extract_dir}"
    elif archive_type == "7z":
        # Check if 7z is available
        check_7z = "command -v 7z || command -v 7za"
        success, seven_zip_path, _ = await ssh_manager.execute_command(check_7z)
        if not seven_zip_path.strip():
            extract_cmd = f"7za x -y -o{extract_dir} {archive_file} 2>/dev/null || 7zr x -y -o{extract_dir} {archive_file}"
        else:
            extract_cmd = f"7z x -y -o{extract_dir} {archive_file}"
    else:
        extract_cmd = f"tar -xzf {archive_file} -C {extract_dir} 2>/dev/null || tar -xf {archive_file} -C {extract_dir}"

    success, _, stderr = await ssh_manager.execute_command(extract_cmd, timeout=120)

    if not success:
        await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
        await progress(f"Failed to extract archive: {stderr}", "error")
        await notify_install_result(False, f"Failed to extract archive: {stderr}")
        return GitHubPluginInstallResponse(
            success=False, message=f"Failed to extract archive: {stderr}"
        )

    await progress("Extraction complete, analyzing archive structure...")

    if request.archive_mappings:
        extract_dir = await stage_mapping(
            ssh_manager, extract_dir, f"{remote_temp_dir}/mapped-tree", request.archive_mappings
        )
        request = request.model_copy(
            update={"source_prefix": None, "custom_install_path": None, "allowed_roots": []}
        )

    source_prefix = request.source_prefix or ""
    requested_source_dir = f"{extract_dir}/{source_prefix}" if source_prefix else extract_dir
    source_check = f"test -d {shlex.quote(requested_source_dir)}"
    source_ok, _, _ = await ssh_manager.execute_command(source_check)
    if not source_ok:
        await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
        error_msg = "Approved archive source prefix was not found"
        await progress(error_msg, "error")
        await notify_install_result(False, error_msg)
        return GitHubPluginInstallResponse(success=False, message=error_msg)

    if request.allowed_roots:
        install_tree = f"{remote_temp_dir}/install-tree"
        await ssh_manager.execute_command(
            f"rm -rf -- {shlex.quote(install_tree)} && mkdir -p -- {shlex.quote(install_tree)}"
        )
        copied_roots: list[str] = []
        for root in request.allowed_roots:
            approved_root = f"{requested_source_dir}/{root}"
            exists, _, _ = await ssh_manager.execute_command(
                f"test -d {shlex.quote(approved_root)}"
            )
            if not exists:
                continue
            copied, _, copy_error = await ssh_manager.execute_command(
                f"cp -a --no-dereference -- {shlex.quote(approved_root)} "
                f"{shlex.quote(install_tree)}/"
            )
            if not copied:
                await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
                error_msg = f"Failed to stage approved {root}/ tree: {copy_error}"
                await progress(error_msg, "error")
                await notify_install_result(False, error_msg)
                return GitHubPluginInstallResponse(success=False, message=error_msg)
            copied_roots.append(root)
        if "addons" not in copied_roots:
            await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
            error_msg = "Approved archive mapping did not contain addons/"
            await progress(error_msg, "error")
            await notify_install_result(False, error_msg)
            return GitHubPluginInstallResponse(success=False, message=error_msg)
        requested_source_dir = install_tree

    backup_root: str | None = None

    async def prepare_rollback(source: str, target: str) -> None:
        nonlocal backup_root
        if not request.installation_plan_hash:
            return
        backup_root = posix_backup = (
            f"{server.game_directory.rstrip('/')}/.upkk/backups/github/"
            f"{request.installation_plan_hash[:16]}-{uuid.uuid4().hex[:12]}"
        )
        await progress("Backing up files affected by the approved plan...")
        backed_up, backup_output, backup_error = await ssh_manager.execute_command(
            host._build_backup_command(source, target, posix_backup), timeout=120
        )
        if not backed_up:
            raise RuntimeError(
                backup_error or backup_output or "Unable to create the installation backup"
            )

    async def rollback_install(target: str) -> str | None:
        if backup_root is None:
            return None
        rolled_back, rollback_output, rollback_error = await ssh_manager.execute_command(
            host._build_rollback_command(target, backup_root), timeout=120
        )
        if rolled_back:
            return "The affected files were restored from backup"
        return f"Rollback failed: {rollback_error or rollback_output}"

    # Check if addons directory exists in extracted content
    addons_check = f"test -d {shlex.quote(f'{requested_source_dir}/addons')} && echo 'addons_found'"
    success, addons_output, _ = await ssh_manager.execute_command(addons_check)
    has_addons = "addons_found" in addons_output and (
        not request.custom_install_path or not request.source_prefix
    )

    # Determine source directory for copy
    if has_addons:
        # Archive has proper structure (addons/, cfg/, etc.)
        source_dir = requested_source_dir
        await progress("Found addons/ directory at root level")
    else:
        # Check if there's a single subdirectory that contains addons
        find_cmd = (
            f"find {shlex.quote(requested_source_dir)} -maxdepth 2 -type d -name 'addons' | head -1"
        )
        success, find_output, _ = await ssh_manager.execute_command(find_cmd)

        if find_output.strip() and (not request.custom_install_path or not request.source_prefix):
            # Found addons in subdirectory
            addons_path = find_output.strip()
            source_dir = addons_path.rsplit("/addons", 1)[0]
            await progress("Found addons/ directory in subdirectory")
        elif request.custom_install_path:
            # No addons directory found, but custom install path is specified
            # Extract to the custom path (e.g., 'addons')
            safe_custom_path = request.custom_install_path.strip().strip("/")

            # Validate custom path to prevent path traversal
            if ".." in safe_custom_path or safe_custom_path.startswith("/"):
                await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
                error_msg = "Invalid custom install path specified"
                await progress(error_msg, "error")
                await notify_install_result(False, error_msg)
                return GitHubPluginInstallResponse(success=False, message=error_msg)

            # Build exclusion patterns for files and directories
            exclude_raw_patterns = []

            # Exclude specified files (new preferred method)
            for exclude_file in request.exclude_files:
                # Sanitize file path
                safe_file = exclude_file.strip().strip("/")
                if safe_file and ".." not in safe_file:
                    exclude_raw_patterns.append(safe_file)

            # Also support excluding directories for backward compatibility
            for exclude_dir in request.exclude_dirs:
                # Sanitize directory name
                safe_dir = exclude_dir.strip().strip("/")
                if safe_dir and ".." not in safe_dir:
                    exclude_raw_patterns.append(safe_dir)
                    exclude_raw_patterns.append(f"{safe_dir}/")
                    exclude_raw_patterns.append(f"{safe_dir}/*")

            if exclude_raw_patterns:
                exclude_count = len(request.exclude_files) + len(request.exclude_dirs)
                await progress(f"Excluding {exclude_count} item(s) from installation")

            # Create the target directory structure
            target_custom_dir = f"{csgo_dir}/{safe_custom_path}"
            mkdir_cmd = f"mkdir -p {target_custom_dir}"
            await ssh_manager.execute_command(mkdir_cmd)
            await prepare_rollback(requested_source_dir, target_custom_dir)

            # Copy with exclusions
            rsync_check = "command -v rsync"
            success_check, rsync_path, _ = await ssh_manager.execute_command(rsync_check)

            if rsync_path.strip():
                # Use rsync for better control
                if exclude_raw_patterns:
                    await progress(f"Applying {len(exclude_raw_patterns)} exclusion pattern(s)")
                copy_cmd = host._build_plugin_copy_command(
                    requested_source_dir,
                    target_custom_dir,
                    exclude_raw_patterns,
                    use_rsync=True,
                )
            else:
                # Fallback to cp with tar for exclusions
                if exclude_raw_patterns:
                    await progress(
                        f"Using tar with {len(exclude_raw_patterns)} exclusion pattern(s)"
                    )
                copy_cmd = host._build_plugin_copy_command(
                    requested_source_dir,
                    target_custom_dir,
                    exclude_raw_patterns,
                    use_rsync=False,
                )

            logger.info(f"Custom path copy command: {copy_cmd}")
            success, _, stderr = await ssh_manager.execute_command(copy_cmd)

            if not success:
                rollback_message = await rollback_install(target_custom_dir)
                await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
                error_msg = f"Failed to copy files to custom path: {stderr}"
                if rollback_message:
                    error_msg = f"{error_msg}. {rollback_message}"
                await progress(error_msg, "error")
                await notify_install_result(False, error_msg)
                return GitHubPluginInstallResponse(success=False, message=error_msg)

            await progress(f"Extracted to custom path: {safe_custom_path}")

            # Cleanup and return success
            await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")

            # Count files after installation
            count_after_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
            _, count_after, _ = await ssh_manager.execute_command(count_after_cmd)
            count_after = int(count_after.strip()) if count_after.strip().isdigit() else 0

            await progress(
                f"Installation complete! Custom path used: {safe_custom_path}", "success"
            )
            await notify_install_result(
                True,
                f"Plugin installed successfully to custom path: {safe_custom_path}",
                count_after,
            )
            await record_installation()

            return GitHubPluginInstallResponse(
                success=True,
                message=f"Plugin installed successfully to custom path: {safe_custom_path}",
                installed_files=count_after,
            )
        else:
            # No addons directory found - reject installation
            await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
            error_msg = "No addons/ directory found in archive. This does not appear to be a valid CS2 plugin package."
            await progress(error_msg, "error")
            await notify_install_result(False, error_msg)
            return GitHubPluginInstallResponse(success=False, message=error_msg)

    # Build exclusion patterns for files and directories
    exclude_raw_patterns = []

    # Exclude specified files (new preferred method)
    for exclude_file in request.exclude_files:
        # Sanitize file path
        safe_file = exclude_file.strip().strip("/")
        if safe_file and ".." not in safe_file:
            exclude_raw_patterns.append(safe_file)

    # Also support excluding directories for backward compatibility
    for exclude_dir in request.exclude_dirs:
        # Sanitize directory name
        safe_dir = exclude_dir.strip().strip("/")
        if safe_dir and ".." not in safe_dir:
            exclude_raw_patterns.append(safe_dir)
            exclude_raw_patterns.append(f"{safe_dir}/")
            exclude_raw_patterns.append(f"{safe_dir}/*")

    if exclude_raw_patterns:
        exclude_count = len(request.exclude_files) + len(request.exclude_dirs)
        await progress(f"Excluding {exclude_count} item(s) from installation")

    # Count files before copy
    count_before_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
    _, count_before, _ = await ssh_manager.execute_command(count_before_cmd)
    count_before = int(count_before.strip()) if count_before.strip().isdigit() else 0

    await progress("Installing plugin files...")
    await prepare_rollback(source_dir, csgo_dir)

    # Copy files using rsync for better control
    rsync_check = "command -v rsync"
    success, rsync_path, _ = await ssh_manager.execute_command(rsync_check)

    if rsync_path.strip():
        # Use rsync for better control
        if exclude_raw_patterns:
            await progress(f"Applying {len(exclude_raw_patterns)} exclusion pattern(s)")
        copy_cmd = host._build_plugin_copy_command(
            source_dir,
            csgo_dir,
            exclude_raw_patterns,
            use_rsync=True,
        )
    else:
        # Fallback to cp with tar for exclusions
        if exclude_raw_patterns:
            await progress(f"Using tar with {len(exclude_raw_patterns)} exclusion pattern(s)")
        copy_cmd = host._build_plugin_copy_command(
            source_dir,
            csgo_dir,
            exclude_raw_patterns,
            use_rsync=False,
        )

    logger.info(f"Copy command: {copy_cmd}")
    success, copy_output, stderr = await ssh_manager.execute_command(copy_cmd, timeout=120)

    # Count files after copy
    count_after_cmd = f"find {csgo_dir}/addons -type f 2>/dev/null | wc -l"
    _, count_after, _ = await ssh_manager.execute_command(count_after_cmd)
    count_after = int(count_after.strip()) if count_after.strip().isdigit() else 0

    installed_files = count_after - count_before if count_after > count_before else 0

    if not success:
        rollback_message = await rollback_install(csgo_dir)
        failure_message = f"Failed to copy files: {stderr}"
        if rollback_message:
            failure_message = f"{failure_message}. {rollback_message}"
        await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
        await progress(failure_message, "error")
        await notify_install_result(False, failure_message, installed_files)
        return GitHubPluginInstallResponse(
            success=False,
            message=failure_message,
            installed_files=installed_files,
        )

    # Installing CounterStrikeSharp itself from the marketplace or a GitHub
    # release must land on the same managed core.json as the framework action.
    await host.maybe_apply_counterstrikesharp_core_defaults(
        ssh_manager.execute_command,
        csgo_dir=csgo_dir,
        source_dir=source_dir,
        repo_url=request.repo_url,
        download_url=request.download_url,
        report=progress,
    )

    # Cleanup - use the correct temp directory. Persistent backups are kept under .upkk.
    await ssh_manager.execute_command(f"rm -rf -- {shlex.quote(remote_temp_dir)}")
    await progress("Cleanup complete")

    success_msg = f"Plugin installed successfully! {installed_files} files installed. Restart server to apply changes."
    await progress(success_msg, "complete")
    await notify_install_result(True, success_msg, installed_files)
    await record_installation()

    return GitHubPluginInstallResponse(
        success=True, message=success_msg, installed_files=installed_files
    )
