"""Focused remote file operations."""

# ruff: noqa: F403,F405

from asyncssh.constants import FILEXFER_TYPE_DIRECTORY, FILEXFER_TYPE_REGULAR

from .common import *
from .connection import ConnectionMixin


class DownloadExtractMixin(SSHMixinBase):
    """Focused file-system capability."""

    async def download_url_to_file(  # noqa: C901
        self,
        url: str,
        target_path: Optional[str],
        server: Server,
        overwrite: bool = False,
        *,
        destination_path: Optional[str] = None,
        resolved_target_callback=None,
    ) -> Tuple[bool, str]:
        """Download an HTTP(S) URL to a part file and publish atomically.

        Redirects are followed manually.  Every hop is resolved on the SSH
        host, all returned addresses are required to be public, and curl is
        pinned to one validated address to prevent DNS rebinding.  When
        ``target_path`` is unknown, the final Content-Disposition (including
        RFC 5987 filename*) takes precedence over the final URL.
        """
        if target_path is not None and self.archive_type_from_path(target_path) is None:
            return False, "Target filename does not use a supported archive extension"

        if target_path is not None:
            parent_dir = posixpath.dirname(target_path)
            validation_path = target_path
        elif destination_path:
            parent_dir = posixpath.normpath(destination_path)
            validation_path = parent_dir
        else:
            return False, "Download destination path is required"

        valid, validation_error = await self.validate_path_within_base(
            server.game_directory,
            validation_path,
            server,
            allow_missing=True,
        )
        if not valid:
            return False, validation_error

        curl_tool = await self._find_remote_tool(("curl",))
        if not curl_tool:
            return False, "Required download tool is missing: install curl"
        getent_tool = await self._find_remote_tool(("getent",))
        if not getent_tool:
            return False, "Required DNS resolver is missing: install getent"

        download_id = uuid.uuid4().hex
        part_path = posixpath.join(parent_dir, f".upkk-download-{download_id}.part")
        headers_path = posixpath.join(parent_dir, f".upkk-download-{download_id}.headers")
        safe_parent = shlex.quote(parent_dir)
        safe_part = shlex.quote(part_path)
        safe_headers = shlex.quote(headers_path)

        try:
            success, stdout, stderr = await self.execute_command(
                f"mkdir -p -- {safe_parent}",
                timeout=30,
            )
            if not success:
                return (
                    False,
                    f"Failed to create download directory: {self._short_command_error(stdout, stderr)}",
                )

            parent_valid, parent_error = await self.validate_path_within_base(
                server.game_directory,
                parent_dir,
                server,
                allow_missing=False,
            )
            if not parent_valid:
                return False, parent_error

            if target_path is not None:
                async with self.conn.start_sftp_client() as sftp:
                    try:
                        existing_attrs = await sftp.lstat(target_path)
                    except asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath:
                        existing_attrs = None
                    if existing_attrs is not None:
                        if not overwrite:
                            return (
                                False,
                                "Target file already exists. Enable overwrite to replace it.",
                            )
                        if existing_attrs.type != FILEXFER_TYPE_REGULAR:
                            return (
                                False,
                                "Existing target must be a regular file and cannot be a symlink",
                            )

            current_url = url
            final_url = url
            raw_headers = ""
            seen_urls = set()
            for redirect_count in range(self.REMOTE_DOWNLOAD_MAX_REDIRECTS + 1):
                parsed_url, url_error = self._validate_remote_download_url(current_url)
                if parsed_url is None:
                    return False, url_error
                if current_url in seen_urls:
                    return False, "Download redirect loop detected"
                seen_urls.add(current_url)

                resolved_address, resolve_error = await self._resolve_public_download_address(
                    parsed_url.hostname,
                    getent_tool,
                )
                if resolved_address is None:
                    return False, resolve_error
                request_port = parsed_url.port or (
                    443 if parsed_url.scheme.lower() == "https" else 80
                )
                resolve_entry = self._curl_resolve_entry(
                    parsed_url.hostname,
                    request_port,
                    resolved_address,
                )

                # Do not use --location: the response is inspected before the
                # next hop is allowed.  --noproxy ensures an HTTP proxy cannot
                # bypass the validated/pinned origin address.
                curl_command = (
                    f"umask 077; {shlex.quote(curl_tool)} --fail --silent --show-error "
                    f"--request GET --noproxy {shlex.quote('*')} "
                    f"--proto {shlex.quote('=http,https')} "
                    f"--connect-timeout 20 --max-time {self.REMOTE_DOWNLOAD_TIMEOUT} "
                    f"--retry 2 --retry-delay 2 --max-filesize {self.REMOTE_DOWNLOAD_MAX_BYTES} "
                    f"--resolve {shlex.quote(resolve_entry)} "
                    f"--dump-header {safe_headers} --output {safe_part} "
                    f"--url {shlex.quote(current_url)}"
                )
                success, stdout, stderr = await self.execute_command(
                    curl_command,
                    timeout=self.REMOTE_DOWNLOAD_TIMEOUT + 30,
                )
                if not success:
                    error_detail = self._redact_download_error(
                        self._short_command_error(stdout, stderr).replace(
                            current_url,
                            "[redacted URL]",
                        )
                    )
                    return False, f"Download failed: {error_detail}"

                async with self.conn.start_sftp_client() as sftp:
                    headers_attrs = await sftp.lstat(headers_path)
                    if headers_attrs.type != FILEXFER_TYPE_REGULAR:
                        return False, "Download response metadata is not a regular file"
                    if headers_attrs.size > self.REMOTE_DOWNLOAD_METADATA_MAX_BYTES:
                        return False, "Download response metadata is too large"
                    async with sftp.open(headers_path, "rb") as header_file:
                        raw_header_bytes = await header_file.read()
                if isinstance(raw_header_bytes, str):
                    raw_headers = raw_header_bytes
                else:
                    raw_headers = raw_header_bytes.decode("iso-8859-1", errors="replace")

                redirect_url, is_redirect, redirect_error = self._redirect_url_from_response(
                    raw_headers,
                    current_url,
                )
                if redirect_error:
                    return False, redirect_error
                if is_redirect:
                    if redirect_count >= self.REMOTE_DOWNLOAD_MAX_REDIRECTS:
                        return False, "Download exceeded the redirect limit"
                    if redirect_url is None:
                        return False, "Download redirect target could not be resolved"
                    current_url = redirect_url
                    continue

                final_url = current_url
                break

            async with self.conn.start_sftp_client() as sftp:
                attrs = await sftp.lstat(part_path)
                if attrs.type != FILEXFER_TYPE_REGULAR or not attrs.size:
                    return False, "Downloaded archive is empty or is not a regular file"
                if attrs.size > self.REMOTE_DOWNLOAD_MAX_BYTES:
                    return False, "Downloaded archive exceeds the configured size limit"

                if target_path is None:
                    resolved_filename, filename_error = self._filename_from_download_response(
                        raw_headers,
                        final_url,
                    )
                    if resolved_filename is None:
                        return False, filename_error
                    target_path = posixpath.join(parent_dir, resolved_filename)

            if target_path is None:
                return False, "Download response filename could not be resolved"

            target_valid, target_error = await self.validate_path_within_base(
                server.game_directory,
                target_path,
                server,
                allow_missing=True,
            )
            if not target_valid:
                return False, target_error

            if resolved_target_callback is not None:
                callback_result = resolved_target_callback(target_path)
                if inspect.isawaitable(callback_result):
                    await callback_result

            async with self.conn.start_sftp_client() as sftp:
                try:
                    existing_attrs = await sftp.lstat(target_path)
                except asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath:
                    existing_attrs = None
                if existing_attrs is not None:
                    if not overwrite:
                        return False, "Target file already exists. Enable overwrite to replace it."
                    if existing_attrs.type != FILEXFER_TYPE_REGULAR:
                        return (
                            False,
                            "Existing target must be a regular file and cannot be a symlink",
                        )

            safe_target = shlex.quote(target_path)
            if overwrite:
                publish_command = f"mv -f -- {safe_part} {safe_target}"
            else:
                # A hard-link publish is an atomic no-clobber operation because
                # the part file is created in the destination directory.
                publish_command = f"ln -- {safe_part} {safe_target} && rm -- {safe_part}"
            success, stdout, stderr = await self.execute_command(publish_command, timeout=30)
            if not success:
                return (
                    False,
                    f"Failed to publish downloaded archive: {self._short_command_error(stdout, stderr)}",
                )
            return True, ""
        except asyncssh.SFTPError as exc:
            return False, f"SFTP error while downloading archive: {exc}"
        except Exception as exc:
            return False, f"Error downloading archive: {exc}"
        finally:
            # These paths contain only a server-controlled UUID and are quoted.
            await self.execute_command(
                f"rm -f -- {safe_part} {safe_headers}",
                timeout=10,
            )

    async def extract_archive(  # noqa: C901
        self,
        archive_path: str,
        destination_path: str,
        server: Server,
        overwrite: bool = False,
        source_folder: Optional[str] = None,
        strip_source_folder: bool = False,
        progress_callback=None,
    ) -> Tuple[bool, str]:
        """Inspect, stage, and merge a supported archive on the SSH host."""

        async def send_progress(message: str) -> None:
            if progress_callback is None:
                return
            if inspect.iscoroutinefunction(progress_callback):
                await progress_callback(message)
            else:
                progress_callback(message)

        archive_type = self.archive_type_from_path(archive_path)
        if archive_type is None:
            return False, (
                "Unsupported archive format. Supported formats: "
                f"{ConnectionMixin.SUPPORTED_ARCHIVE_FORMATS_LABEL}"
            )

        archive_valid, archive_error = await self.validate_path_within_base(
            server.game_directory,
            archive_path,
            server,
            allow_missing=False,
            require_regular=True,
        )
        if not archive_valid:
            return False, archive_error
        destination_valid, destination_error = await self.validate_path_within_base(
            server.game_directory,
            destination_path,
            server,
            allow_missing=True,
        )
        if not destination_valid:
            return False, destination_error

        await send_progress("Inspecting archive")
        inspect_success, archive_info, inspect_error = await self._inspect_archive_connected(
            archive_path,
            archive_type,
        )
        if not inspect_success:
            return False, inspect_error

        normalized_source = None
        if source_folder:
            normalized_source, source_error = self._normalize_archive_member(
                source_folder.rstrip("/"),
                allow_backslash_separators=archive_type
                in ConnectionMixin.ARCHIVE_TYPES_ALLOW_BACKSLASH,
            )
            if source_error or normalized_source is None:
                return False, source_error or "Invalid source_folder"
            if normalized_source not in set(archive_info["folders"]):
                return (
                    False,
                    f"Selected source folder was not found in archive: {normalized_source}",
                )

        temp_root = posixpath.join(posixpath.normpath(server.game_directory), ".upkk-file-tasks")
        stage_path = posixpath.join(temp_root, f"extract-{uuid.uuid4().hex}")
        safe_archive = shlex.quote(archive_path)
        safe_destination = shlex.quote(destination_path)
        safe_temp_root = shlex.quote(temp_root)
        safe_stage = shlex.quote(stage_path)
        stage_created = False
        temp_root_validated = False

        try:
            await send_progress("Preparing staging directory")
            temp_root_safe, temp_root_error = await self.validate_path_within_base(
                server.game_directory,
                temp_root,
                server,
                allow_missing=True,
            )
            if not temp_root_safe:
                return False, temp_root_error

            async with self.conn.start_sftp_client() as sftp:
                try:
                    existing_root_attrs = await sftp.lstat(temp_root)
                except asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath:
                    existing_root_attrs = None
                if (
                    existing_root_attrs is not None
                    and existing_root_attrs.type != FILEXFER_TYPE_DIRECTORY
                ):
                    return False, "Extraction task directory cannot be a symlink"
                if existing_root_attrs is not None:
                    canonical_game_dir = posixpath.normpath(
                        str(await sftp.realpath(server.game_directory))
                    )
                    canonical_temp_root = posixpath.normpath(str(await sftp.realpath(temp_root)))
                    expected_temp_root = posixpath.normpath(
                        posixpath.join(canonical_game_dir, ".upkk-file-tasks")
                    )
                    if canonical_temp_root != expected_temp_root:
                        return False, "Extraction task directory resolves to an unexpected path"

            root_success, root_stdout, root_stderr = await self.execute_command(
                f"umask 077; mkdir -p -- {safe_temp_root} && chmod 700 -- {safe_temp_root}",
                timeout=30,
            )
            if not root_success:
                return (
                    False,
                    f"Failed to create extraction task directory: {self._short_command_error(root_stdout, root_stderr)}",
                )

            async with self.conn.start_sftp_client() as sftp:
                root_attrs = await sftp.lstat(temp_root)
                if root_attrs.type != FILEXFER_TYPE_DIRECTORY:
                    return False, "Extraction task directory cannot be a symlink"
                canonical_game_dir = posixpath.normpath(
                    str(await sftp.realpath(server.game_directory))
                )
                canonical_temp_root = posixpath.normpath(str(await sftp.realpath(temp_root)))
                expected_temp_root = posixpath.normpath(
                    posixpath.join(canonical_game_dir, ".upkk-file-tasks")
                )
                if canonical_temp_root != expected_temp_root:
                    return False, "Extraction task directory resolves to an unexpected path"
            temp_root_safe, temp_root_error = await self.validate_path_within_base(
                server.game_directory,
                temp_root,
                server,
                allow_missing=False,
            )
            if not temp_root_safe:
                return False, temp_root_error
            temp_root_validated = True

            create_success, create_stdout, create_stderr = await self.execute_command(
                f"umask 077; mkdir -- {safe_stage} && chmod 700 -- {safe_stage}",
                timeout=30,
            )
            if not create_success:
                return (
                    False,
                    f"Failed to create extraction staging directory: {self._short_command_error(create_stdout, create_stderr)}",
                )
            stage_created = True

            async with self.conn.start_sftp_client() as sftp:
                stage_attrs = await sftp.lstat(stage_path)
                if stage_attrs.type != FILEXFER_TYPE_DIRECTORY:
                    return False, "Extraction staging path cannot be a symlink"

            stage_valid, stage_error = await self.validate_path_within_base(
                server.game_directory,
                stage_path,
                server,
                allow_missing=False,
            )
            if not stage_valid:
                return False, stage_error

            if archive_type == "zip":
                tool = await self._find_remote_tool(("unzip",))
                if not tool:
                    return False, "Required archive tool is missing: install unzip"
                extract_command = (
                    f"LC_ALL=C {shlex.quote(tool)} -qq -o {safe_archive} -d {safe_stage}"
                )
            elif archive_type in ConnectionMixin.TAR_ARCHIVE_TYPES:
                tool = await self._find_remote_tool(("tar",))
                if not tool:
                    return False, "Required archive tool is missing: install tar"
                compress_name = self._tar_compress_program(archive_type)
                compress_program = None
                if compress_name:
                    compress_program = await self._find_remote_tool((compress_name,))
                    if not compress_program:
                        return False, f"Required archive tool is missing: install {compress_name}"
                extract_command = self._tar_extract_command(
                    tool,
                    archive_type,
                    archive_path,
                    stage_path,
                    archive_info["has_backslash_separators"],
                    compress_program,
                )
            elif archive_type in ConnectionMixin.SEVEN_ZIP_ARCHIVE_TYPES:
                tool = await self._find_remote_tool(("7zz", "7z", "7za"))
                if not tool:
                    return False, "Required archive tool is missing: install 7zz, 7z, or 7za"
                output_argument = shlex.quote(f"-o{stage_path}")
                extract_command = (
                    f"LC_ALL=C {shlex.quote(tool)} x -y -aoa -bd -bso0 -bsp0 "
                    f"{output_argument} -- {safe_archive}"
                )
            elif archive_type in ConnectionMixin.SINGLE_FILE_ARCHIVE_TYPES:
                candidates = self._single_file_tool_candidates(archive_type)
                tool = await self._find_remote_tool(candidates)
                if not tool:
                    return (
                        False,
                        f"Required archive tool is missing: install {' or '.join(candidates)}",
                    )
                output_name = self._single_file_output_name(archive_path, archive_type)
                safe_output = shlex.quote(posixpath.join(stage_path, output_name))
                extract_command = (
                    f"{self._single_file_command(tool, archive_type, archive_path, 'dc')} "
                    f"> {safe_output}"
                )
            else:
                return False, "Unsupported archive format"

            await send_progress("Extracting archive")
            extract_success, extract_stdout, extract_stderr = await self.execute_command(
                extract_command,
                timeout=self.ARCHIVE_EXTRACT_TIMEOUT,
            )
            if not extract_success:
                return (
                    False,
                    f"Extraction failed: {self._short_command_error(extract_stdout, extract_stderr)}",
                )

            # Fail closed if the extractor produced any link, hardlink, device,
            # FIFO, or socket despite the preflight member listing.
            special_command = (
                f"find {safe_stage} -xdev \\( -type l -o -type b -o -type c -o "
                "-type p -o -type s \\) -print -quit"
            )
            special_success, special_stdout, special_stderr = await self.execute_command(
                special_command,
                timeout=60,
            )
            if not special_success:
                return (
                    False,
                    f"Failed to validate extracted files: {self._short_command_error(special_stdout, special_stderr)}",
                )
            if special_stdout.strip():
                return False, "Archive extraction produced a link or special filesystem entry"

            hardlink_success, hardlink_stdout, hardlink_stderr = await self.execute_command(
                f"find {safe_stage} -xdev -type f -links +1 -print -quit",
                timeout=60,
            )
            if not hardlink_success:
                return (
                    False,
                    f"Failed to validate extracted hardlinks: {self._short_command_error(hardlink_stdout, hardlink_stderr)}",
                )
            if hardlink_stdout.strip():
                return False, "Archive extraction produced a hardlinked file"

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
                selected_path = posixpath.join(stage_path, normalized_source)
                safe_selected = shlex.quote(selected_path)
                selected_success, _, _ = await self.execute_command(
                    f"test -d {safe_selected} && test ! -L {safe_selected}",
                    timeout=10,
                )
                if not selected_success:
                    return False, "Selected source folder was not extracted as a directory"
                if strip_source_folder:
                    copy_source = shlex.quote(posixpath.join(selected_path, "."))
                else:
                    # Preserve the selected directory itself (its archive
                    # parents are selection context and are not recreated).
                    copy_source = safe_selected
            else:
                copy_source = shlex.quote(posixpath.join(stage_path, "."))

            merge_command = (
                f"{shlex.quote(cp_tool)} {copy_options} -- {copy_source} {safe_destination}/"
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
        except Exception as exc:
            return False, f"Error extracting archive: {exc}"
        finally:
            if stage_created and temp_root_validated:
                await self.execute_command(f"rm -rf -- {safe_stage}", timeout=60)
                await self.execute_command(
                    f"rmdir -- {safe_temp_root} 2>/dev/null || true", timeout=10
                )
