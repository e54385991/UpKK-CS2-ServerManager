"""Focused remote file operations."""

# ruff: noqa: F403,F405

from asyncssh.constants import FILEXFER_TYPE_DIRECTORY, FILEXFER_TYPE_SYMLINK

from .common import *


class BasicFileOperationsMixin(SSHMixinBase):
    """Focused file-system capability."""

    async def list_directory(
        self, path: str, server: Server
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        """
        List directory contents with file metadata

        Args:
            path: Directory path to list
            server: Server instance

        Returns:
            Tuple[bool, List[Dict], str]: (success, files_list, error_message)
            Each file dict contains: name, path, type, size, modified, permissions
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, [], f"Connection failed: {msg}"

        async def _do_list():
            async with self.conn.start_sftp_client() as sftp:
                files = []
                async for entry in sftp.scandir(path):
                    attrs = entry.attrs
                    file_info = {
                        "name": entry.filename,
                        "path": posixpath.join(path, entry.filename),
                        "type": "directory" if attrs.type == FILEXFER_TYPE_DIRECTORY else "file",
                        "size": attrs.size or 0,
                        "modified": attrs.mtime or 0,
                        "permissions": oct(attrs.permissions)[-3:] if attrs.permissions else "000",
                        "is_symlink": attrs.type == FILEXFER_TYPE_SYMLINK,
                    }
                    files.append(file_info)
                files.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))
                return files

        try:
            files = await _do_list()
            return True, files, ""
        except asyncssh.SFTPError as e:
            try:
                files = await self._handle_sftp_error_with_reconnect(
                    e, server, "list_directory", _do_list
                )
                return True, files, ""
            except Exception as final_e:
                return False, [], str(final_e)
        except Exception as e:
            return False, [], f"Error listing directory: {str(e)}"

    async def read_file(
        self, file_path: str, server: Server, max_size: int = 10 * 1024 * 1024
    ) -> Tuple[bool, str, str]:
        """
        Read file contents

        Args:
            file_path: Path to file
            server: Server instance
            max_size: Maximum file size to read (default 10MB)

        Returns:
            Tuple[bool, str, str]: (success, file_content, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, "", f"Connection failed: {msg}"

        async def _do_read():
            async with self.conn.start_sftp_client() as sftp:
                attrs = await sftp.stat(file_path)
                if attrs.size > max_size:
                    raise Exception(
                        f"File too large ({attrs.size} bytes). Maximum size is {max_size} bytes."
                    )
                async with sftp.open(file_path, "r") as f:
                    content = await f.read()
                    try:
                        if isinstance(content, bytes):
                            text_content = content.decode("utf-8")
                        else:
                            text_content = content
                    except UnicodeDecodeError:
                        if isinstance(content, bytes):
                            text_content = content.decode("latin-1")
                        else:
                            text_content = content
                    return text_content

        try:
            content = await _do_read()
            return True, content, ""
        except asyncssh.SFTPError as e:
            try:
                content = await self._handle_sftp_error_with_reconnect(
                    e, server, "read_file", _do_read
                )
                return True, content, ""
            except Exception as final_e:
                return False, "", str(final_e)
        except Exception as e:
            return False, "", f"Error reading file: {str(e)}"

    async def write_file(self, file_path: str, content: str, server: Server) -> Tuple[bool, str]:
        """
        Write content to file

        Args:
            file_path: Path to file
            content: Content to write (string)
            server: Server instance

        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"

        async def _do_write():
            async with self.conn.start_sftp_client() as sftp:
                parent_dir = posixpath.dirname(file_path)
                if parent_dir:
                    try:
                        await sftp.stat(parent_dir)
                    except asyncssh.SFTPNoSuchFile:
                        await sftp.makedirs(parent_dir)
                async with sftp.open(file_path, "w", encoding="utf-8") as f:
                    await f.write(content)

        try:
            await _do_write()
            return True, ""
        except asyncssh.SFTPError as e:
            try:
                await self._handle_sftp_error_with_reconnect(e, server, "write_file", _do_write)
                return True, ""
            except Exception as final_e:
                return False, str(final_e)
        except Exception as e:
            return False, f"Error writing file: {str(e)}"

    async def delete_path(self, path: str, server: Server) -> Tuple[bool, str]:
        """
        Delete file or directory

        Args:
            path: Path to delete
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
                attrs = await sftp.stat(path)

                if attrs.type == FILEXFER_TYPE_DIRECTORY:
                    # Remove directory recursively
                    await sftp.rmtree(path)
                else:
                    # Remove file
                    await sftp.remove(path)

                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error deleting: {str(e)}"

    async def create_directory(self, path: str, server: Server) -> Tuple[bool, str]:
        """
        Create directory

        Args:
            path: Directory path to create
            server: Server instance

        Returns:
            Tuple[bool, str]: (success, error_message)
        """
        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"

        valid, validation_error = await self.validate_path_within_base(
            server.game_directory,
            path,
            server,
            allow_missing=True,
        )
        if not valid:
            return False, validation_error

        try:
            async with self.conn.start_sftp_client() as sftp:
                await sftp.makedirs(path)
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error creating directory: {str(e)}"

    async def _remote_exists(self, path: str) -> bool:
        if not self.conn:
            return False
        try:
            async with self.conn.start_sftp_client() as sftp:
                await sftp.lstat(path)
            return True
        except asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath:
            return False
        except asyncssh.SFTPError:
            return False

    @staticmethod
    def copy_collision_name(basename: str, index: int) -> str:
        if index <= 0:
            return basename
        stem, ext = posixpath.splitext(basename)
        suffix = " copy" if index == 1 else f" copy {index}"
        return f"{stem}{suffix}{ext}"

    async def copy_into_directory(
        self, source: str, dest_dir: str, server: Server
    ) -> Tuple[bool, str, str]:
        """Copy a remote file or directory into ``dest_dir``. Returns the new path."""

        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, "", f"Connection failed: {msg}"

        source_n = posixpath.normpath(source)
        dest_n = posixpath.normpath(dest_dir)
        basename = posixpath.basename(source_n)
        if not basename or basename in {".", ".."}:
            return False, "", "Invalid source path"
        if dest_n == source_n or dest_n.startswith(source_n.rstrip("/") + "/"):
            return False, "", "Cannot copy a folder into itself"

        valid_source, source_error = await self.validate_path_within_base(
            server.game_directory, source_n, server
        )
        if not valid_source:
            return False, "", source_error
        valid_dest, dest_error = await self.validate_path_within_base(
            server.game_directory, dest_n, server
        )
        if not valid_dest:
            return False, "", dest_error

        candidate = posixpath.join(dest_n, basename)
        for index in range(0, 100):
            name = self.copy_collision_name(basename, index)
            candidate = posixpath.join(dest_n, name)
            if not await self._remote_exists(candidate):
                break
        else:
            return False, "", "Too many name collisions at the destination"

        valid_target, target_error = await self.validate_path_within_base(
            server.game_directory, candidate, server, allow_missing=True
        )
        if not valid_target:
            return False, "", target_error

        success, stdout, stderr = await self.execute_command(
            f"cp -a -- {shlex.quote(source_n)} {shlex.quote(candidate)}",
            timeout=120,
        )
        if not success:
            return False, "", self._short_command_error(stdout, stderr)
        return True, candidate, ""

    async def rename_path(self, old_path: str, new_path: str, server: Server) -> Tuple[bool, str]:
        """
        Rename or move file/directory

        Args:
            old_path: Current path
            new_path: New path
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
                await sftp.rename(old_path, new_path)
                return True, ""
        except asyncssh.SFTPError as e:
            return False, f"SFTP error: {str(e)}"
        except Exception as e:
            return False, f"Error renaming: {str(e)}"
