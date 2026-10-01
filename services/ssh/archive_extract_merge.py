"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.file_download_extract")


async def _validate_extracted_entries(self: SSHMixinBase, safe_stage: str) -> str | None:
    special_command = (
        f"find {safe_stage} -xdev \\( -type l -o -type b -o -type c -o "
        "-type p -o -type s \\) -print -quit"
    )
    special_success, special_stdout, special_stderr = await self.execute_command(
        special_command,
        timeout=60,
    )
    if not special_success:
        return f"Failed to validate extracted files: {self._short_command_error(special_stdout, special_stderr)}"
    if special_stdout.strip():
        return "Archive extraction produced a link or special filesystem entry"

    hardlink_success, hardlink_stdout, hardlink_stderr = await self.execute_command(
        f"find {safe_stage} -xdev -type f -links +1 -print -quit",
        timeout=60,
    )
    if not hardlink_success:
        return f"Failed to validate extracted hardlinks: {self._short_command_error(hardlink_stdout, hardlink_stderr)}"
    if hardlink_stdout.strip():
        return "Archive extraction produced a hardlinked file"
    return None


async def _merge_extracted_entries(
    self: SSHMixinBase,
    server: Server,
    destination_path: str,
    stage_path: str,
    safe_destination: str,
    normalized_source: str | None,
    strip_source_folder: bool,
    overwrite: bool,
    send_progress,
) -> tuple[bool, str]:
    mkdir_success, mkdir_stdout, mkdir_stderr = await self.execute_command(
        f"mkdir -p -- {safe_destination}",
        timeout=30,
    )
    if not mkdir_success:
        return (
            False,
            f"Failed to create extraction destination: {self._short_command_error(mkdir_stdout, mkdir_stderr)}",
        )
    destination_valid, destination_error = await self.validate_path_within_base(
        server.game_directory,
        destination_path,
        server,
        allow_missing=False,
    )
    if not destination_valid:
        return False, destination_error

    cp_tool = await self._find_remote_tool(("cp",))
    if not cp_tool:
        return False, "Required merge tool is missing: install coreutils (cp)"
    copy_options = (
        "-a --no-dereference --remove-destination"
        if overwrite
        else "-a --no-dereference --no-clobber"
    )
    if normalized_source:
        selected_path = host.posixpath.join(stage_path, normalized_source)
        safe_selected = host.shlex.quote(selected_path)
        selected_success, _, _ = await self.execute_command(
            f"test -d {safe_selected} && test ! -L {safe_selected}",
            timeout=10,
        )
        if not selected_success:
            return False, "Selected source folder was not extracted as a directory"
        if strip_source_folder:
            copy_source = host.shlex.quote(host.posixpath.join(selected_path, "."))
        else:
            # Preserve the selected directory itself (its archive
            # parents are selection context and are not recreated).
            copy_source = safe_selected
    else:
        copy_source = host.shlex.quote(host.posixpath.join(stage_path, "."))

    merge_command = (
        f"{host.shlex.quote(cp_tool)} {copy_options} -- {copy_source} {safe_destination}/"
    )
    await send_progress("Merging extracted files")
    merge_success, merge_stdout, merge_stderr = await self.execute_command(
        merge_command,
        timeout=self.ARCHIVE_EXTRACT_TIMEOUT,
    )
    if not merge_success:
        return (
            False,
            f"Failed to merge extracted files: {self._short_command_error(merge_stdout, merge_stderr)}",
        )
    await send_progress("Extraction complete")
    return True, ""
