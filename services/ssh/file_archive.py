"""Focused remote file operations."""

# ruff: noqa: F403,F405

from asyncssh.constants import FILEXFER_TYPE_DIRECTORY, FILEXFER_TYPE_REGULAR

from .common import *
from .connection import ConnectionMixin


class ArchiveOperationsMixin(SSHMixinBase):
    """Focused file-system capability."""

    @staticmethod
    def _canonical_path_is_within(base_path: str, target_path: str) -> bool:
        base = posixpath.normpath(base_path)
        target = posixpath.normpath(target_path)
        return target == base or target.startswith(base.rstrip("/") + "/")

    async def _probe_remote_path(self, sftp, target: str, allow_missing: bool):
        probe = target
        missing_parts: list[str] = []
        while True:
            try:
                attrs = await sftp.lstat(probe)
                canonical = posixpath.normpath(str(await sftp.realpath(probe)))
                return attrs, canonical, missing_parts
            except asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath:
                if not allow_missing:
                    raise ValueError("Remote path does not exist") from None
                parent = posixpath.dirname(probe)
                component = posixpath.basename(probe)
                if parent == probe:
                    raise ValueError("Cannot resolve an existing parent directory") from None
                if not component:
                    raise ValueError("Remote path contains an invalid component") from None
                missing_parts.append(component)
                probe = parent
            except asyncssh.SFTPError as exc:
                raise ValueError(f"Cannot resolve remote path: {exc}") from exc

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
                    if base_attrs.type != FILEXFER_TYPE_DIRECTORY:
                        return False, "Server directory is not a directory"
                    canonical_base = posixpath.normpath(str(await sftp.realpath(normalized_base)))
                except asyncssh.SFTPError as exc:
                    return False, f"Cannot resolve server directory: {exc}"

                try:
                    target_attrs, canonical_existing, missing_parts = await self._probe_remote_path(
                        sftp, normalized_target, allow_missing
                    )
                except ValueError as exc:
                    return False, str(exc)

                canonical_target = canonical_existing
                for component in reversed(missing_parts):
                    canonical_target = posixpath.join(canonical_target, component)
                canonical_target = posixpath.normpath(canonical_target)
                if not self._canonical_path_is_within(canonical_base, canonical_target):
                    return False, "Remote path resolves outside the server directory"

                if require_regular:
                    if missing_parts:
                        return False, "Archive file does not exist"
                    if target_attrs is None or target_attrs.type != FILEXFER_TYPE_REGULAR:
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

    async def _read_archive_stderr(self, stream) -> str:
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

    async def _consume_archive_stdout(
        self, stream, line_handler: Callable[[str], Optional[str]]
    ) -> Optional[str]:
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
        return await handle(bytes(buffer)) if buffer else None

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

        try:
            # Bytes mode lets us enforce limits before decoding potentially
            # attacker-controlled member names.
            process = await self.conn.create_process(command, encoding=None)
            stderr_task = asyncio.create_task(self._read_archive_stderr(process.stderr))

            async def run_listing() -> Tuple[Any, Optional[str], str]:
                nonlocal process_finished
                handler_error = await self._consume_archive_stdout(process.stdout, line_handler)
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
        program = compress_program or ArchiveOperationsMixin._tar_compress_program(archive_type)
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
        if archive_type == "zip":
            return await self._inspect_zip_archive(archive_path)
        if archive_type in ConnectionMixin.TAR_ARCHIVE_TYPES:
            return await self._inspect_tar_archive(archive_path, archive_type)
        if archive_type in ConnectionMixin.SEVEN_ZIP_ARCHIVE_TYPES:
            return await self._inspect_7z_archive(archive_path, archive_type)
        if archive_type in ConnectionMixin.SINGLE_FILE_ARCHIVE_TYPES:
            return await self._inspect_single_archive(archive_path, archive_type)
        return False, {}, "Unsupported archive format"

    async def _inspect_zip_archive(self, archive_path: str) -> Tuple[bool, Dict[str, Any], str]:
        safe_archive = shlex.quote(archive_path)
        raw_members: List[Tuple[str, bool]] = []
        unzip_tool = await self._find_remote_tool(("unzip",))
        if not unzip_tool:
            return False, {}, "Required archive tool is missing: install unzip"
        safe_tool = shlex.quote(unzip_tool)
        success, stdout, stderr = await self.execute_command(
            f"LC_ALL=C {safe_tool} -Z1 {safe_archive}", timeout=self.ARCHIVE_INSPECT_TIMEOUT
        )
        if not success:
            return False, {}, f"Invalid ZIP archive: {self._short_command_error(stdout, stderr)}"
        raw_members = [(line, line.endswith("/")) for line in stdout.splitlines() if line]

        verbose_success, verbose_stdout, verbose_stderr = await self.execute_command(
            f"LC_ALL=C {safe_tool} -Z -l {safe_archive}", timeout=self.ARCHIVE_INSPECT_TIMEOUT
        )
        if not verbose_success:
            return (
                False,
                {},
                f"Unable to inspect ZIP member types: {self._short_command_error(verbose_stdout, verbose_stderr)}",
            )
        for line in verbose_stdout.splitlines():
            stripped = line.lstrip()
            if (
                stripped
                and stripped[0] in "lhbcps"
                and re.match(r"^[lhbcps][rwxstST-]{9}", stripped)
            ):
                return False, {}, "Archive contains a link or special ZIP member"
        return self._build_archive_info("zip", raw_members)

    async def _inspect_tar_archive(
        self, archive_path: str, archive_type: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        raw_members: List[Tuple[str, bool]] = []
        tar_tool = await self._find_remote_tool(("tar",))
        if not tar_tool:
            return False, {}, "Required archive tool is missing: install tar"
        compress_name = self._tar_compress_program(archive_type)
        compress_program = None
        if compress_name:
            compress_program = await self._find_remote_tool((compress_name,))
            if not compress_program:
                return False, {}, f"Required archive tool is missing: install {compress_name}"

        def handle_tar_line(line: str) -> Optional[str]:
            parsed_member, parse_error = self._parse_tar_c_verbose_listing_line(line)
            if parse_error:
                return parse_error
            if parsed_member is not None:
                raw_members.append(parsed_member)
            return None

        list_success, list_error = await self._stream_archive_listing(
            self._tar_list_command(tar_tool, archive_type, archive_path, compress_program),
            handle_tar_line,
        )
        if not list_success:
            return False, {}, f"Invalid TAR archive: {list_error}"
        return self._build_archive_info(archive_type, raw_members)

    async def _inspect_7z_archive(
        self, archive_path: str, archive_type: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        safe_archive = shlex.quote(archive_path)
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
        return self._build_archive_info(archive_type, raw_members)

    async def _inspect_single_archive(
        self, archive_path: str, archive_type: str
    ) -> Tuple[bool, Dict[str, Any], str]:
        candidates = self._single_file_tool_candidates(archive_type)
        tool = await self._find_remote_tool(candidates)
        if not tool:
            return False, {}, f"Required archive tool is missing: install {' or '.join(candidates)}"
        success, stdout, stderr = await self.execute_command(
            self._single_file_command(tool, archive_type, archive_path, "t"),
            timeout=self.ARCHIVE_INSPECT_TIMEOUT,
        )
        if not success:
            return (
                False,
                {},
                f"Invalid {archive_type} archive: {self._short_command_error(stdout, stderr)}",
            )
        raw_members = [(self._single_file_output_name(archive_path, archive_type), False)]
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
