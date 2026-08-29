"""Files operations for SSHManager."""

# ruff: noqa: F403,F405

from .common import *
from .connection import ConnectionMixin

DOWNLOAD_CHUNK_SIZE = 262144


class RemoteFileMixin:
    """Internal files behavior; instantiate through SSHManager."""

    @staticmethod
    def _canonical_path_is_within(base_path: str, target_path: str) -> bool:
        base = posixpath.normpath(base_path)
        target = posixpath.normpath(target_path)
        return target == base or target.startswith(base.rstrip("/") + "/")

    async def validate_path_within_base(
        self,
        base_path: str,
        target_path: str,
        server: Server,
        allow_missing: bool = False,
        require_regular: bool = False,
    ) -> Tuple[bool, str]:
        """Validate a remote path against the canonical server directory.

        Lexical checks alone do not catch a symlink beneath ``base_path`` which
        points outside it. This resolves the nearest existing ancestor and then
        applies any missing suffix components to that canonical path.
        """
        normalized_base = posixpath.normpath(base_path)
        normalized_target = posixpath.normpath(target_path)
        if not self._canonical_path_is_within(normalized_base, normalized_target):
            return False, "Path is outside the server directory"

        if not self.conn:
            success, msg = await self.connect(server)
            if not success:
                return False, f"Connection failed: {msg}"

        try:
            async with self.conn.start_sftp_client() as sftp:
                try:
                    base_attrs = await sftp.stat(normalized_base)
                    if base_attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
                        return False, "Server directory is not a directory"
                    canonical_base = posixpath.normpath(str(await sftp.realpath(normalized_base)))
                except asyncssh.SFTPError as exc:
                    return False, f"Cannot resolve server directory: {exc}"

                probe = normalized_target
                missing_parts: List[str] = []
                target_attrs = None
                while True:
                    try:
                        target_attrs = await sftp.lstat(probe)
                        canonical_existing = posixpath.normpath(str(await sftp.realpath(probe)))
                        break
                    except asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath:
                        if not allow_missing:
                            return False, "Remote path does not exist"
                        parent = posixpath.dirname(probe)
                        if parent == probe:
                            return False, "Cannot resolve an existing parent directory"
                        component = posixpath.basename(probe)
                        if not component:
                            return False, "Remote path contains an invalid component"
                        missing_parts.append(component)
                        probe = parent
                    except asyncssh.SFTPError as exc:
                        return False, f"Cannot resolve remote path: {exc}"

                canonical_target = canonical_existing
                for component in reversed(missing_parts):
                    canonical_target = posixpath.join(canonical_target, component)
                canonical_target = posixpath.normpath(canonical_target)
                if not self._canonical_path_is_within(canonical_base, canonical_target):
                    return False, "Remote path resolves outside the server directory"

                if require_regular:
                    if missing_parts:
                        return False, "Archive file does not exist"
                    if target_attrs is None or target_attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
                        return False, "Archive path must be a regular file and cannot be a symlink"
                return True, ""
        except asyncssh.SFTPError as exc:
            return False, f"SFTP path validation failed: {exc}"
        except Exception as exc:
            return False, f"Path validation failed: {exc}"

    async def _find_remote_tool(self, candidates: Tuple[str, ...]) -> Optional[str]:
        """Return the first available hard-coded executable name."""
        for candidate in candidates:
            success, stdout, _ = await self.execute_command(
                f"command -v {shlex.quote(candidate)}",
                timeout=5,
            )
            if success and stdout.strip():
                return stdout.strip().splitlines()[0]
        return None

    @staticmethod
    def _short_command_error(stdout: str, stderr: str, limit: int = 2000) -> str:
        message = (stderr or stdout or "Remote command failed").strip()
        if len(message) > limit:
            return message[:limit] + "..."
        return message

    async def _stream_archive_listing(
        self,
        command: str,
        line_handler: Callable[[str], Optional[str]],
    ) -> Tuple[bool, str]:
        """Stream a bounded archive listing and handle each line immediately.

        Archive listings can be surprisingly large even when the compressed
        file itself is modest. ``SSHClientConnection.run()`` retains the whole
        stdout value, so use a byte stream here and cap both line length and
        entry count before any output is retained by the panel process.
        """
        if not self.conn:
            return False, "Not connected"

        process = None
        stderr_task = None
        local_error: Optional[str] = None
        process_finished = False

        async def read_stderr(stream) -> str:
            retained = bytearray()
            while True:
                chunk = await stream.read(self.ARCHIVE_LISTING_READ_BYTES)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                remaining = self.ARCHIVE_LISTING_ERROR_BYTES - len(retained)
                if remaining > 0:
                    retained.extend(chunk[:remaining])
            return retained.decode("utf-8", errors="replace")

        async def consume_stdout(stream) -> Optional[str]:
            buffer = bytearray()
            line_count = 0

            async def handle(raw_line: bytes) -> Optional[str]:
                nonlocal line_count
                raw_line = raw_line.rstrip(b"\r")
                if not raw_line:
                    return None
                line_count += 1
                if line_count > self.ARCHIVE_MAX_ENTRIES:
                    return f"Archive contains too many entries (maximum {self.ARCHIVE_MAX_ENTRIES})"
                if len(raw_line) > self.ARCHIVE_LISTING_MAX_LINE_BYTES:
                    return "Archive contains an excessively long member path"
                try:
                    line = raw_line.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    return "Archive contains a member name which is not valid UTF-8"
                return line_handler(line)

            while True:
                chunk = await stream.read(self.ARCHIVE_LISTING_READ_BYTES)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                buffer.extend(chunk)

                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    raw_line = bytes(buffer[:newline])
                    del buffer[: newline + 1]
                    error = await handle(raw_line)
                    if error:
                        return error

                if len(buffer) > self.ARCHIVE_LISTING_MAX_LINE_BYTES:
                    return "Archive contains an excessively long member path"

            if buffer:
                return await handle(bytes(buffer))
            return None

        try:
            # Bytes mode lets us enforce limits before decoding potentially
            # attacker-controlled member names.
            process = await self.conn.create_process(command, encoding=None)
            stderr_task = asyncio.create_task(read_stderr(process.stderr))

            async def run_listing() -> Tuple[Any, Optional[str], str]:
                nonlocal process_finished
                handler_error = await consume_stdout(process.stdout)
                if handler_error:
                    process_finished = await self._stop_archive_listing_process(process)
                    return None, handler_error, ""
                result = await process.wait()
                process_finished = True
                stderr = await stderr_task
                return result, handler_error, stderr

            result, local_error, stderr = await asyncio.wait_for(
                run_listing(),
                timeout=self.ARCHIVE_INSPECT_TIMEOUT,
            )
            if local_error:
                return False, local_error
            if result.exit_status != 0:
                return False, self._short_command_error("", stderr)
            return True, ""
        except asyncio.TimeoutError:
            if process is not None:
                process_finished = await self._stop_archive_listing_process(process)
            return False, "Archive inspection timed out"
        except (
            asyncssh.ConnectionLost,
            asyncssh.DisconnectError,
            asyncssh.ChannelOpenError,
        ) as exc:
            return False, f"SSH connection lost while inspecting archive: {exc}"
        except Exception as exc:
            return False, f"Unable to inspect archive: {exc}"
        finally:
            if process is not None and not process_finished:
                await self._stop_archive_listing_process(process)
            if stderr_task is not None:
                if not stderr_task.done():
                    stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await stderr_task

    @classmethod
    async def _stop_archive_listing_process(cls, process: Any) -> bool:
        """Reap a listing process, escalating from TERM to KILL quickly."""
        with contextlib.suppress(Exception):
            process.terminate()
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=cls.ARCHIVE_LISTING_STOP_TIMEOUT,
            )
            return True
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                process.kill()
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=cls.ARCHIVE_LISTING_STOP_TIMEOUT,
                )
                return True
            except Exception:
                logger.warning("Archive listing process could not be reaped after SIGKILL")
                return False
        except Exception:
            # A closed SSH channel or already-reaped child also means there is
            # no live listing process left for this connection to retain.
            return True

    @classmethod
    def _normalize_archive_member(
        cls,
        member_name: str,
        *,
        allow_backslash_separators: bool = False,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Normalize one archive member, returning (path, error)."""
        if not isinstance(member_name, str):
            return None, "Archive contains a non-text member name"
        if any(ord(char) < 32 or ord(char) == 127 for char in member_name):
            return None, "Archive contains a member name with control characters"
        original_member_name = member_name
        if "\\" in member_name:
            if not allow_backslash_separators:
                return None, "Archive contains a member name with backslash separators"
            member_name = member_name.replace("\\", "/")
        if len(member_name.encode("utf-8")) > cls.ARCHIVE_MAX_MEMBER_PATH_BYTES:
            return None, "Archive contains an excessively long member path"

        # GNU tar emits the two POSIX root markers; Windows-created TARs can
        # also contain the exact equivalent '.\\'. Do not infer a root marker
        # after rewriting separators or trimming slashes: doing so would
        # silently accept absolute '/', '//' or Windows '\\' members.
        if original_member_name in (".", "./") or (
            allow_backslash_separators and original_member_name == ".\\"
        ):
            return None, None
        if member_name.startswith("/") or re.match(r"^[A-Za-z]:", member_name):
            return None, f"Archive member uses an absolute path: {original_member_name!r}"

        value = member_name.rstrip("/")
        while value.startswith("./"):
            value = value[2:]
        if value in ("", "."):
            return None, f"Archive member contains an unsafe empty path: {original_member_name!r}"
        if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
            return None, f"Archive member uses an absolute path: {original_member_name!r}"

        components = value.split("/")
        if any(component in ("", ".", "..") for component in components):
            return None, f"Archive member contains an unsafe path component: {member_name!r}"
        normalized = posixpath.normpath(value)
        if normalized == ".." or normalized.startswith("../"):
            return None, f"Archive member escapes the archive root: {member_name!r}"
        return normalized, None

    @staticmethod
    def _decode_tar_listing_name(value: str) -> Tuple[Optional[str], Optional[str]]:
        """Decode the escaped body of one GNU tar C-quoted member name."""
        decoded = bytearray()
        index = 0
        escapes = {
            "\\": ord("\\"),
            '"': ord('"'),
            "a": 7,
            "b": 8,
            "f": 12,
            "n": 10,
            "r": 13,
            "t": 9,
            "v": 11,
        }
        while index < len(value):
            char = value[index]
            if char != "\\":
                decoded.extend(char.encode("utf-8"))
                index += 1
                continue

            index += 1
            if index >= len(value):
                return None, "TAR returned a malformed escaped member name"
            escaped = value[index]
            if escaped in escapes:
                decoded.append(escapes[escaped])
                index += 1
                continue
            if escaped in "01234567":
                end = index + 1
                while end < min(index + 3, len(value)) and value[end] in "01234567":
                    end += 1
                decoded_byte = int(value[index:end], 8)
                if decoded_byte > 255:
                    return None, "TAR returned an invalid octal escape in a member name"
                decoded.append(decoded_byte)
                index = end
                continue
            return None, "TAR returned an unsupported escaped member name"
        try:
            return decoded.decode("utf-8", errors="strict"), None
        except UnicodeDecodeError:
            return None, "Archive contains a member name which is not valid UTF-8"

    @classmethod
    def _parse_tar_c_verbose_listing_line(
        cls,
        line: str,
    ) -> Tuple[Optional[Tuple[str, bool]], Optional[str]]:
        """Parse GNU tar's stable numeric, UTC, C-quoted verbose format."""
        fields = line.split(None, 5)
        if len(fields) != 6 or not fields[0]:
            return None, "TAR returned a malformed member listing"

        member_type = fields[0][0]
        if member_type not in ("-", "d"):
            # Reject links before parsing their ``name -> target`` suffix.
            return None, "Archive contains a link or special TAR member"

        quoted_name = fields[5]
        if len(quoted_name) < 2 or not quoted_name.startswith('"') or not quoted_name.endswith('"'):
            return None, "TAR returned an unquoted or malformed member name"
        decoded_name, decode_error = cls._decode_tar_listing_name(quoted_name[1:-1])
        if decode_error:
            return None, decode_error
        if decoded_name is None:
            return None, "TAR returned a malformed member name"
        return (decoded_name, member_type == "d"), None

    @classmethod
    def _build_archive_info(
        cls,
        archive_type: str,
        raw_members: List[Tuple[str, bool]],
    ) -> Tuple[bool, Dict[str, Any], str]:
        if len(raw_members) > cls.ARCHIVE_MAX_ENTRIES:
            return (
                False,
                {},
                (f"Archive contains too many entries (maximum {cls.ARCHIVE_MAX_ENTRIES})"),
            )

        normalize_backslashes = archive_type in ConnectionMixin.ARCHIVE_TYPES_ALLOW_BACKSLASH
        has_backslash_separators = False
        member_types: Dict[str, bool] = {}
        members: List[Dict[str, Any]] = []
        for raw_name, is_directory in raw_members:
            if "\\" in raw_name:
                has_backslash_separators = True
            normalized, error = cls._normalize_archive_member(
                raw_name,
                allow_backslash_separators=normalize_backslashes,
            )
            if error:
                return False, {}, error
            if normalized is None:
                continue
            if normalized in member_types:
                return False, {}, f"Archive contains a duplicate member path: {normalized}"
            member_types[normalized] = is_directory
            members.append({"path": normalized, "is_dir": is_directory})

        file_paths = {path for path, is_directory in member_types.items() if not is_directory}
        for path in member_types:
            parts = path.split("/")
            for index in range(1, len(parts)):
                ancestor = "/".join(parts[:index])
                if ancestor in file_paths:
                    return False, {}, (f"Archive member path conflicts with file ancestor: {path}")

        folders = set()
        for member in members:
            parts = member["path"].split("/")
            folder_depth = len(parts) if member["is_dir"] else len(parts) - 1
            for index in range(1, folder_depth + 1):
                folders.add("/".join(parts[:index]))
                if len(folders) > cls.ARCHIVE_MAX_FOLDERS:
                    return (
                        False,
                        {},
                        (f"Archive contains too many folders (maximum {cls.ARCHIVE_MAX_FOLDERS})"),
                    )

        return (
            True,
            {
                "archive_type": archive_type,
                "folders": sorted(folders, key=lambda value: (value.count("/"), value.lower())),
                "entry_count": len(members),
                "members": members,
                "has_backslash_separators": has_backslash_separators,
            },
            "",
        )

    @staticmethod
    def _tar_compress_program(archive_type: str) -> Optional[str]:
        return {"tar.zst": "zstd", "tar.lzma": "lzma"}.get(archive_type)

    @classmethod
    def _tar_list_command(
        cls,
        tool: str,
        archive_type: str,
        archive_path: str,
        compress_program: Optional[str] = None,
    ) -> str:
        list_flag = {
            "tar": "-tvf",
            "tar.gz": "-tvzf",
            "tar.bz2": "-tvjf",
            "tar.xz": "-tvJf",
            "tar.zst": "-tvf",
            "tar.lzma": "-tvf",
        }[archive_type]
        program = compress_program or cls._tar_compress_program(archive_type)
        compress_option = f"-I {shlex.quote(program)} " if program else ""
        return (
            f"LC_ALL=C TAR_OPTIONS= {shlex.quote(tool)} {compress_option}{list_flag} "
            f"{shlex.quote(archive_path)} --numeric-owner --full-time --utc --quoting-style=c"
        )

    @staticmethod
    def _tar_extract_command(
        tool: str,
        archive_type: str,
        archive_path: str,
        stage_path: str,
        normalize_backslashes: bool,
        compress_program: Optional[str] = None,
    ) -> str:
        extract_flag = {
            "tar": "-xf",
            "tar.gz": "-xzf",
            "tar.bz2": "-xjf",
            "tar.xz": "-xJf",
            "tar.zst": "-xf",
            "tar.lzma": "-xf",
        }[archive_type]
        program = compress_program or RemoteFileMixin._tar_compress_program(archive_type)
        compress_option = f"-I {shlex.quote(program)} " if program else ""
        transform_option = ""
        if normalize_backslashes:
            # Limit the transform to member names. Uppercase S/H explicitly
            # exclude symbolic/hard-link targets as defence in depth if an
            # archive changes between inspection and extraction.
            transform_expression = shlex.quote(r"flags=rSH;s|\\|/|g")
            transform_option = f" --transform={transform_expression}"
        return (
            f"LC_ALL=C TAR_OPTIONS= {shlex.quote(tool)} {compress_option}{extract_flag} "
            f"{shlex.quote(archive_path)} "
            f"-C {shlex.quote(stage_path)} --no-same-owner --no-same-permissions"
            f"{transform_option}"
        )

    @staticmethod
    def _single_file_output_name(archive_path: str, archive_type: str) -> str:
        name = posixpath.basename(archive_path)
        lower = name.lower()
        suffixes = {
            "gz": (".gz",),
            "bz2": (".bz2",),
            "xz": (".xz",),
            "zst": (".zstd", ".zst"),
            "lzma": (".lzma",),
        }[archive_type]
        for suffix in suffixes:
            if lower.endswith(suffix):
                stripped = name[: -len(suffix)]
                return stripped or name
        return name

    @staticmethod
    def _single_file_tool_candidates(archive_type: str) -> Tuple[str, ...]:
        return {
            "gz": ("gzip",),
            "bz2": ("bzip2",),
            "xz": ("xz",),
            "zst": ("zstd",),
            "lzma": ("lzma", "xz"),
        }[archive_type]

    @staticmethod
    def _single_file_command(tool: str, archive_type: str, archive_path: str, mode: str) -> str:
        flag = "-t" if mode == "t" else "-dc"
        tool_name = posixpath.basename(tool).lower()
        if archive_type == "lzma" and tool_name in {"xz", "xz.exe"}:
            return f"{shlex.quote(tool)} --format=lzma {flag} {shlex.quote(archive_path)}"
        return f"{shlex.quote(tool)} {flag} {shlex.quote(archive_path)}"

    @staticmethod
    def _parse_7z_listing(output: str) -> Tuple[Optional[List[Tuple[str, bool]]], Optional[str]]:
        """Parse technical 7-Zip listing output after its entry separator."""
        normalized_output = output.replace("\r\n", "\n").replace("\r", "\n")
        separator_match = re.search(r"^-{10,}\s*$", normalized_output, flags=re.MULTILINE)
        if separator_match:
            normalized_output = normalized_output[separator_match.end() :]

        raw_members: List[Tuple[str, bool]] = []
        for block in re.split(r"\n\s*\n", normalized_output.strip()):
            if not block.strip():
                continue
            properties: Dict[str, str] = {}
            malformed = False
            for line in block.splitlines():
                if " = " not in line:
                    malformed = True
                    continue
                key, value = line.split(" = ", 1)
                properties[key] = value
            path = properties.get("Path")
            if not path:
                # Archive metadata and banner blocks are not members.
                continue
            if malformed:
                return None, "7-Zip returned a malformed technical listing"

            attributes = properties.get("Attributes", "")
            lower_attributes = attributes.lower()
            if (
                properties.get("Symbolic Link")
                or properties.get("Hard Link")
                or properties.get("Anti") == "+"
                or re.search(r"(^|\s)l[rwx-]{9}", lower_attributes)
                or re.search(r"(^|\s)[bcps][rwx-]{9}", lower_attributes)
            ):
                return None, f"Archive contains an unsupported link or special entry: {path}"
            is_directory = properties.get("Folder") == "+" or attributes.startswith("D")
            raw_members.append((path, is_directory))
        return raw_members, None

    async def _inspect_archive_connected(
        self,
        archive_path: str,
        archive_type: str,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """Inspect archive members using only quoted, fixed remote commands."""
        safe_archive = shlex.quote(archive_path)
        raw_members: List[Tuple[str, bool]]

        if archive_type == "zip":
            unzip_tool = await self._find_remote_tool(("unzip",))
            if not unzip_tool:
                return False, {}, "Required archive tool is missing: install unzip"
            safe_tool = shlex.quote(unzip_tool)
            success, stdout, stderr = await self.execute_command(
                f"LC_ALL=C {safe_tool} -Z1 {safe_archive}",
                timeout=self.ARCHIVE_INSPECT_TIMEOUT,
            )
            if not success:
                return (
                    False,
                    {},
                    f"Invalid ZIP archive: {self._short_command_error(stdout, stderr)}",
                )
            raw_members = [(line, line.endswith("/")) for line in stdout.splitlines() if line]

            verbose_success, verbose_stdout, verbose_stderr = await self.execute_command(
                f"LC_ALL=C {safe_tool} -Z -l {safe_archive}",
                timeout=self.ARCHIVE_INSPECT_TIMEOUT,
            )
            if not verbose_success:
                return (
                    False,
                    {},
                    (
                        "Unable to inspect ZIP member types: "
                        f"{self._short_command_error(verbose_stdout, verbose_stderr)}"
                    ),
                )
            for line in verbose_stdout.splitlines():
                stripped = line.lstrip()
                if (
                    stripped
                    and stripped[0] in "lhbcps"
                    and re.match(r"^[lhbcps][rwxstST-]{9}", stripped)
                ):
                    return False, {}, "Archive contains a link or special ZIP member"

        elif archive_type in ConnectionMixin.TAR_ARCHIVE_TYPES:
            tar_tool = await self._find_remote_tool(("tar",))
            if not tar_tool:
                return False, {}, "Required archive tool is missing: install tar"
            compress_name = self._tar_compress_program(archive_type)
            compress_program = None
            if compress_name:
                compress_program = await self._find_remote_tool((compress_name,))
                if not compress_program:
                    return (
                        False,
                        {},
                        f"Required archive tool is missing: install {compress_name}",
                    )
            raw_members: List[Tuple[str, bool]] = []

            def handle_tar_line(line: str) -> Optional[str]:
                parsed_member, parse_error = self._parse_tar_c_verbose_listing_line(line)
                if parse_error:
                    return parse_error
                if parsed_member is not None:
                    raw_members.append(parsed_member)
                return None

            list_success, list_error = await self._stream_archive_listing(
                self._tar_list_command(
                    tar_tool,
                    archive_type,
                    archive_path,
                    compress_program,
                ),
                handle_tar_line,
            )
            if not list_success:
                return False, {}, f"Invalid TAR archive: {list_error}"

        elif archive_type in ConnectionMixin.SEVEN_ZIP_ARCHIVE_TYPES:
            seven_zip_tool = await self._find_remote_tool(("7zz", "7z", "7za"))
            if not seven_zip_tool:
                return False, {}, "Required archive tool is missing: install 7zz, 7z, or 7za"
            success, stdout, stderr = await self.execute_command(
                f"LC_ALL=C {shlex.quote(seven_zip_tool)} l -slt -sccUTF-8 {safe_archive}",
                timeout=self.ARCHIVE_INSPECT_TIMEOUT,
            )
            if not success:
                label = "RAR" if archive_type == "rar" else "7z"
                return (
                    False,
                    {},
                    f"Invalid {label} archive: {self._short_command_error(stdout, stderr)}",
                )
            parsed_members, parse_error = self._parse_7z_listing(stdout)
            if parse_error:
                return False, {}, parse_error
            raw_members = parsed_members or []

        elif archive_type in ConnectionMixin.SINGLE_FILE_ARCHIVE_TYPES:
            candidates = self._single_file_tool_candidates(archive_type)
            tool = await self._find_remote_tool(candidates)
            if not tool:
                return (
                    False,
                    {},
                    f"Required archive tool is missing: install {' or '.join(candidates)}",
                )
            success, stdout, stderr = await self.execute_command(
                self._single_file_command(tool, archive_type, archive_path, "t"),
                timeout=self.ARCHIVE_INSPECT_TIMEOUT,
            )
            if not success:
                return (
                    False,
                    {},
                    (
                        f"Invalid {archive_type} archive: {self._short_command_error(stdout, stderr)}"
                    ),
                )
            raw_members = [(self._single_file_output_name(archive_path, archive_type), False)]
        else:
            return False, {}, "Unsupported archive format"

        return self._build_archive_info(archive_type, raw_members)

    async def inspect_archive(
        self,
        archive_path: str,
        server: Server,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """Validate and list a remote archive for the file-manager UI."""
        archive_type = self.archive_type_from_path(archive_path)
        if archive_type is None:
            return (
                False,
                {},
                (
                    "Unsupported archive format. Supported formats: "
                    f"{ConnectionMixin.SUPPORTED_ARCHIVE_FORMATS_LABEL}"
                ),
            )
        valid, validation_error = await self.validate_path_within_base(
            server.game_directory,
            archive_path,
            server,
            allow_missing=False,
            require_regular=True,
        )
        if not valid:
            return False, {}, validation_error
        return await self._inspect_archive_connected(archive_path, archive_type)

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
                        "type": "directory"
                        if attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY
                        else "file",
                        "size": attrs.size or 0,
                        "modified": attrs.mtime or 0,
                        "permissions": oct(attrs.permissions)[-3:] if attrs.permissions else "000",
                        "is_symlink": attrs.type == asyncssh.FILEXFER_TYPE_SYMLINK,
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

                if attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY:
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

    async def download_url_to_file(
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
                        if existing_attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
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
                    if headers_attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
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
                if attrs.type != asyncssh.FILEXFER_TYPE_REGULAR or not attrs.size:
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
                    if existing_attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
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

    async def extract_archive(
        self,
        archive_path: str,
        destination_path: str,
        server: Server,
        overwrite: bool = False,
        source_folder: Optional[str] = None,
        strip_source_folder: bool = False,
    ) -> Tuple[bool, str]:
        """Inspect, stage, and merge a supported archive on the SSH host."""
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
                    and existing_root_attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY
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
                if root_attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
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
                if stage_attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
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
            merge_success, merge_stdout, merge_stderr = await self.execute_command(
                merge_command,
                timeout=self.ARCHIVE_EXTRACT_TIMEOUT,
            )
            if not merge_success:
                return (
                    False,
                    f"Failed to merge extracted files: {self._short_command_error(merge_stdout, merge_stderr)}",
                )
            return True, ""
        except Exception as exc:
            return False, f"Error extracting archive: {exc}"
        finally:
            if stage_created and temp_root_validated:
                await self.execute_command(f"rm -rf -- {safe_stage}", timeout=60)
                await self.execute_command(
                    f"rmdir -- {safe_temp_root} 2>/dev/null || true", timeout=10
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
                # Ensure parent directory exists
                parent_dir = posixpath.dirname(remote_path)
                if parent_dir:
                    try:
                        await sftp.stat(parent_dir)
                    except asyncssh.SFTPNoSuchFile:
                        await sftp.makedirs(parent_dir)

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
                if attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY:
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
                # Ensure parent directory exists
                parent_dir = posixpath.dirname(remote_path)
                if parent_dir:
                    try:
                        await sftp.stat(parent_dir)
                    except asyncssh.SFTPNoSuchFile:
                        await sftp.makedirs(parent_dir)

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
