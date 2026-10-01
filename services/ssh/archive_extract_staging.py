"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.file_download_extract")


def _archive_source_folder(
    self: SSHMixinBase, source_folder: str | None, archive_type: str, folders: list[str]
) -> tuple[str | None, str]:
    normalized_source = None
    if source_folder:
        normalized_source, source_error = self._normalize_archive_member(
            source_folder.rstrip("/"),
            allow_backslash_separators=archive_type
            in host.ConnectionMixin.ARCHIVE_TYPES_ALLOW_BACKSLASH,
        )
        if source_error or normalized_source is None:
            return None, source_error or "Invalid source_folder"
        if normalized_source not in set(folders):
            return None, f"Selected source folder was not found in archive: {normalized_source}"
    return normalized_source, ""


async def _prepare_extraction_root(
    self: SSHMixinBase, server: Server, temp_root: str, safe_temp_root: str
) -> str | None:
    temp_root_safe, temp_root_error = await self.validate_path_within_base(
        server.game_directory,
        temp_root,
        server,
        allow_missing=True,
    )
    if not temp_root_safe:
        return temp_root_error

    async with self.conn.start_sftp_client() as sftp:
        try:
            existing_root_attrs = await sftp.lstat(temp_root)
        except host.asyncssh.SFTPNoSuchFile, host.asyncssh.SFTPNoSuchPath:
            existing_root_attrs = None
        if (
            existing_root_attrs is not None
            and existing_root_attrs.type != host.FILEXFER_TYPE_DIRECTORY
        ):
            return "Extraction task directory cannot be a symlink"
        if existing_root_attrs is not None:
            canonical_game_dir = host.posixpath.normpath(
                str(await sftp.realpath(server.game_directory))
            )
            canonical_temp_root = host.posixpath.normpath(str(await sftp.realpath(temp_root)))
            expected_temp_root = host.posixpath.normpath(
                host.posixpath.join(canonical_game_dir, ".upkk-file-tasks")
            )
            if canonical_temp_root != expected_temp_root:
                return "Extraction task directory resolves to an unexpected path"

    root_success, root_stdout, root_stderr = await self.execute_command(
        f"umask 077; mkdir -p -- {safe_temp_root} && chmod 700 -- {safe_temp_root}",
        timeout=30,
    )
    if not root_success:
        return f"Failed to create extraction task directory: {self._short_command_error(root_stdout, root_stderr)}"

    async with self.conn.start_sftp_client() as sftp:
        root_attrs = await sftp.lstat(temp_root)
        if root_attrs.type != host.FILEXFER_TYPE_DIRECTORY:
            return "Extraction task directory cannot be a symlink"
        canonical_game_dir = host.posixpath.normpath(
            str(await sftp.realpath(server.game_directory))
        )
        canonical_temp_root = host.posixpath.normpath(str(await sftp.realpath(temp_root)))
        expected_temp_root = host.posixpath.normpath(
            host.posixpath.join(canonical_game_dir, ".upkk-file-tasks")
        )
        if canonical_temp_root != expected_temp_root:
            return "Extraction task directory resolves to an unexpected path"
    temp_root_safe, temp_root_error = await self.validate_path_within_base(
        server.game_directory,
        temp_root,
        server,
        allow_missing=False,
    )
    if not temp_root_safe:
        return temp_root_error
    return None


async def _archive_extract_command(
    self: SSHMixinBase,
    archive_type: str,
    archive_path: str,
    stage_path: str,
    safe_archive: str,
    safe_stage: str,
    has_backslash_separators: bool,
) -> tuple[str | None, str]:
    if archive_type == "zip":
        tool = await self._find_remote_tool(("unzip",))
        if not tool:
            return None, "Required archive tool is missing: install unzip"
        extract_command = f"LC_ALL=C {host.shlex.quote(tool)} -qq -o {safe_archive} -d {safe_stage}"
    elif archive_type in host.ConnectionMixin.TAR_ARCHIVE_TYPES:
        tool = await self._find_remote_tool(("tar",))
        if not tool:
            return None, "Required archive tool is missing: install tar"
        compress_name = self._tar_compress_program(archive_type)
        compress_program = None
        if compress_name:
            compress_program = await self._find_remote_tool((compress_name,))
            if not compress_program:
                return None, f"Required archive tool is missing: install {compress_name}"
        extract_command = self._tar_extract_command(
            tool,
            archive_type,
            archive_path,
            stage_path,
            has_backslash_separators,
            compress_program,
        )
    elif archive_type in host.ConnectionMixin.SEVEN_ZIP_ARCHIVE_TYPES:
        tool = await self._find_remote_tool(("7zz", "7z", "7za"))
        if not tool:
            return None, "Required archive tool is missing: install 7zz, 7z, or 7za"
        output_argument = host.shlex.quote(f"-o{stage_path}")
        extract_command = (
            f"LC_ALL=C {host.shlex.quote(tool)} x -y -aoa -bd -bso0 -bsp0 "
            f"{output_argument} -- {safe_archive}"
        )
    elif archive_type in host.ConnectionMixin.SINGLE_FILE_ARCHIVE_TYPES:
        candidates = self._single_file_tool_candidates(archive_type)
        tool = await self._find_remote_tool(candidates)
        if not tool:
            return None, f"Required archive tool is missing: install {' or '.join(candidates)}"
        output_name = self._single_file_output_name(archive_path, archive_type)
        safe_output = host.shlex.quote(host.posixpath.join(stage_path, output_name))
        extract_command = (
            f"{self._single_file_command(tool, archive_type, archive_path, 'dc')} > {safe_output}"
        )
    else:
        return None, "Unsupported archive format"
    return extract_command, ""


async def _archive_preflight(
    self: SSHMixinBase, server: Server, archive_path: str, destination_path: str, send_progress
) -> tuple[str | None, list[str], bool, str]:
    archive_type = self.archive_type_from_path(archive_path)
    if archive_type is None:
        return (
            None,
            [],
            False,
            f"Unsupported archive format. Supported formats: {host.ConnectionMixin.SUPPORTED_ARCHIVE_FORMATS_LABEL}",
        )

    archive_valid, archive_error = await self.validate_path_within_base(
        server.game_directory,
        archive_path,
        server,
        allow_missing=False,
        require_regular=True,
    )
    if not archive_valid:
        return None, [], False, archive_error
    destination_valid, destination_error = await self.validate_path_within_base(
        server.game_directory,
        destination_path,
        server,
        allow_missing=True,
    )
    if not destination_valid:
        return None, [], False, destination_error

    await send_progress("Inspecting archive")
    inspect_success, archive_info, inspect_error = await self._inspect_archive_connected(
        archive_path,
        archive_type,
    )
    if not inspect_success:
        return None, [], False, inspect_error
    return archive_type, archive_info["folders"], archive_info["has_backslash_separators"], ""


async def _extraction_progress(progress_callback, message: str) -> None:
    if progress_callback is None:
        return
    if host.inspect.iscoroutinefunction(progress_callback):
        await progress_callback(message)
    else:
        progress_callback(message)


async def _validate_extraction_stage(
    self: SSHMixinBase, server: Server, stage_path: str
) -> str | None:
    async with self.conn.start_sftp_client() as sftp:
        stage_attrs = await sftp.lstat(stage_path)
        if stage_attrs.type != host.FILEXFER_TYPE_DIRECTORY:
            return "Extraction staging path cannot be a symlink"

    stage_valid, stage_error = await self.validate_path_within_base(
        server.game_directory,
        stage_path,
        server,
        allow_missing=False,
    )
    if not stage_valid:
        return stage_error
    return None
