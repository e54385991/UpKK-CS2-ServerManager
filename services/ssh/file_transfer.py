"""Focused remote file operations."""

# ruff: noqa: F403,F405

from asyncssh.constants import FILEXFER_TYPE_DIRECTORY

from .common import *

DOWNLOAD_CHUNK_SIZE = 262144


class FileTransferMixin(SSHMixinBase):
    """Focused file-system capability."""

    async def _ensure_remote_parent(self, remote_path: str, sftp) -> Tuple[bool, str]:
        """Create every missing parent of ``remote_path`` before a put/open."""
        parent_dir = posixpath.dirname(remote_path)
        if not parent_dir or parent_dir in (".", "/"):
            return True, ""
        try:
            await sftp.makedirs(parent_dir, exist_ok=True)
            return True, ""
        except asyncssh.SFTPError, OSError:
            success, stdout, stderr = await self.execute_command(
                f"mkdir -p -- {shlex.quote(parent_dir)}",
                timeout=30,
            )
            if success:
                return True, ""
            return (
                False,
                f"Failed to create upload directory: {self._short_command_error(stdout, stderr)}",
            )

    async def upload_file(
        self, local_path: str, remote_path: str, server: Server
    ) -> Tuple[bool, str]:
        """
        Upload file from local to remote

        Args:
            local_path: Local file path
            remote_path: Remote file path
            server: Server instance

        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"

        try:
            async with self.conn.start_sftp_client() as sftp:
                created, create_error = await self._ensure_remote_parent(remote_path, sftp)
                if not created:
                    return False, create_error

                # Upload file
                await sftp.put(local_path, remote_path)
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error uploading file: {str(e)}"

    async def download_file(
        self, remote_path: str, local_path: str, server: Server
    ) -> Tuple[bool, str]:
        """
        Download file from remote to local

        Args:
            remote_path: Remote file path
            local_path: Local file path
            server: Server instance

        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"

        try:
            async with self.conn.start_sftp_client() as sftp:
                # Ensure local parent directory exists
                os.makedirs(os.path.dirname(local_path), exist_ok=True)

                # Download file
                await sftp.get(remote_path, local_path)
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error downloading file: {str(e)}"

    async def get_file_size(
        self, remote_path: str, server: Server
    ) -> Tuple[bool, Optional[int], str]:
        """
        Get remote file size without downloading the file.

        Args:
            remote_path: Remote file path
            server: Server instance

        Returns:
            Tuple[bool, Optional[int], str]: (success, size_bytes, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, None, f"Connection failed: {msg}"

        try:
            async with self.conn.start_sftp_client() as sftp:
                attrs = await sftp.stat(remote_path)
                if attrs.type == FILEXFER_TYPE_DIRECTORY:
                    return False, None, "Cannot download a directory"
                return True, attrs.size or 0, ""
        except asyncssh.SFTPError as e:
            return False, None, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, None, f"Error getting file size: {str(e)}"

    async def stream_file(
        self,
        remote_path: str,
        server: Server,
        chunk_size: int = DOWNLOAD_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """
        Stream a remote file over SFTP in chunks.

        The caller is responsible for calling disconnect() after iteration
        completes or is cancelled.
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                raise RuntimeError(f"Connection failed: {msg}")

        async with self.conn.start_sftp_client() as sftp:
            async with sftp.open(remote_path, "rb") as remote_file:
                while True:
                    chunk = await remote_file.read(chunk_size)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    yield chunk

    async def upload_file_with_progress(
        self, local_path: str, remote_path: str, server: Server, progress_callback=None
    ) -> Tuple[bool, str]:
        """
        Upload file from local to remote with progress tracking

        Args:
            local_path: Local file path
            remote_path: Remote file path
            server: Server instance
            progress_callback: Optional async callback function for progress updates
                             Called with (bytes_uploaded, total_bytes)

        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"

        try:
            # Get file size
            total_bytes = os.path.getsize(local_path)
            bytes_uploaded = 0

            async with self.conn.start_sftp_client() as sftp:
                created, create_error = await self._ensure_remote_parent(remote_path, sftp)
                if not created:
                    return False, create_error

                # Upload file with progress tracking
                # Read file in chunks and upload
                chunk_size = self.UPLOAD_CHUNK_SIZE

                async with await sftp.open(remote_path, "wb") as remote_file:
                    async with await anyio.open_file(local_path, "rb") as local_file:
                        while True:
                            chunk = await local_file.read(chunk_size)
                            if not chunk:
                                break

                            await remote_file.write(chunk)
                            bytes_uploaded += len(chunk)

                            # Send progress update
                            if progress_callback:
                                if asyncio.iscoroutinefunction(progress_callback):
                                    await progress_callback(bytes_uploaded, total_bytes)
                                else:
                                    progress_callback(bytes_uploaded, total_bytes)

                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error uploading file: {str(e)}"
