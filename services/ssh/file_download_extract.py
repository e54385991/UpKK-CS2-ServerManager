"""Focused remote file operations."""

# ruff: noqa: F403,F405

from asyncssh.constants import FILEXFER_TYPE_DIRECTORY as FILEXFER_TYPE_DIRECTORY
from asyncssh.constants import FILEXFER_TYPE_REGULAR as FILEXFER_TYPE_REGULAR

from .archive_extract_merge import _merge_extracted_entries as _merge_extracted_entries
from .archive_extract_merge import _validate_extracted_entries as _validate_extracted_entries
from .archive_extract_staging import _archive_extract_command as _archive_extract_command
from .archive_extract_staging import _archive_preflight as _archive_preflight
from .archive_extract_staging import _archive_source_folder as _archive_source_folder
from .archive_extract_staging import _extraction_progress as _extraction_progress
from .archive_extract_staging import _prepare_extraction_root as _prepare_extraction_root
from .archive_extract_staging import _validate_extraction_stage as _validate_extraction_stage
from .common import *
from .connection import ConnectionMixin as ConnectionMixin
from .remote_download_protocol import _check_download_target as _check_download_target
from .remote_download_protocol import _download_destination as _download_destination
from .remote_download_protocol import _download_pinned_hop as _download_pinned_hop
from .remote_download_protocol import _download_pinned_hops as _download_pinned_hops
from .remote_download_protocol import _publish_downloaded_target as _publish_downloaded_target
from .remote_download_protocol import _resolve_downloaded_target as _resolve_downloaded_target


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
        destination_ok, parent_dir, curl_tool, getent_tool = await _download_destination(
            self, server, target_path, destination_path
        )
        if not destination_ok:
            return False, parent_dir

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

            target_error = await _check_download_target(self, target_path, overwrite)
            if target_error is not None:
                return False, target_error

            download_ok, download_error, raw_headers, final_url = await _download_pinned_hops(
                self, url, curl_tool, getent_tool, headers_path, safe_headers, safe_part
            )
            if not download_ok:
                return False, download_error

            target_path, filename_error = await _resolve_downloaded_target(
                self, part_path, parent_dir, target_path, raw_headers, final_url
            )
            if target_path is None:
                return False, filename_error

            return await _publish_downloaded_target(
                self, server, target_path, safe_part, overwrite, resolved_target_callback
            )
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
            await _extraction_progress(progress_callback, message)

        archive_type, archive_folders, archive_backslash, inspect_error = await _archive_preflight(
            self, server, archive_path, destination_path, send_progress
        )
        if archive_type is None:
            return False, inspect_error

        normalized_source, source_error = _archive_source_folder(
            self, source_folder, archive_type, archive_folders
        )
        if source_error:
            return False, source_error

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
            root_error = await _prepare_extraction_root(self, server, temp_root, safe_temp_root)
            if root_error is not None:
                return False, root_error
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

            stage_error = await _validate_extraction_stage(self, server, stage_path)
            if stage_error is not None:
                return False, stage_error

            extract_command, command_error = await _archive_extract_command(
                self,
                archive_type,
                archive_path,
                stage_path,
                safe_archive,
                safe_stage,
                archive_backslash,
            )
            if extract_command is None:
                return False, command_error

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
            entries_error = await _validate_extracted_entries(self, safe_stage)
            if entries_error is not None:
                return False, entries_error

            return await _merge_extracted_entries(
                self,
                server,
                destination_path,
                stage_path,
                safe_destination,
                normalized_source,
                strip_source_folder,
                overwrite,
                send_progress,
            )
        except Exception as exc:
            return False, f"Error extracting archive: {exc}"
        finally:
            if stage_created and temp_root_validated:
                await self.execute_command(f"rm -rf -- {safe_stage}", timeout=60)
                await self.execute_command(
                    f"rmdir -- {safe_temp_root} 2>/dev/null || true", timeout=10
                )
