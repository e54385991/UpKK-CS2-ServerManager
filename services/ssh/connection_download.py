"""Connection operations for SSHManager."""

# ruff: noqa: F403,F405

from .common import *


def _legacy_connection_pool():
    from . import connection as legacy_connection

    return legacy_connection.ssh_connection_pool


class DownloadConnectionMixin(SSHMixinBase):
    """Focused SSH connection capability."""

    STREAMING_OUTPUT_MAX_BYTES = 2 * 1024 * 1024
    SUPPORTED_ARCHIVE_FORMATS_LABEL = (
        ".zip, .7z, .rar, .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .tbz, "
        ".tar.xz, .txz, .tar.zst, .tzst, .tar.lzma, .tlz, .gz, .bz2, .xz, "
        ".zst, .zstd, .lzma"
    )
    TAR_ARCHIVE_TYPES = frozenset({"tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lzma"})
    SEVEN_ZIP_ARCHIVE_TYPES = frozenset({"7z", "rar"})
    SINGLE_FILE_ARCHIVE_TYPES = frozenset({"gz", "bz2", "xz", "zst", "lzma"})
    ARCHIVE_TYPES_ALLOW_BACKSLASH = TAR_ARCHIVE_TYPES | SEVEN_ZIP_ARCHIVE_TYPES
    REMOTE_DOWNLOAD_FILENAME_MAX_BYTES = 255
    REMOTE_DOWNLOAD_URL_MAX_LENGTH = 4096
    REMOTE_DOWNLOAD_REDIRECT_CODES = frozenset((301, 302, 303, 307, 308))
    METAMOD_DOWNLOADS_URL = "https://www.sourcemm.net/downloads.php?branch=dev"
    METAMOD_GITHUB_RELEASES_API = (
        "https://api.github.com/repos/alliedmodders/metamod-source/releases?per_page=50"
    )
    METAMOD_LINUX_DOWNLOAD_PATTERN = (
        r"https://github\.com/alliedmodders/metamod-source/releases/download/"
        r"2\.0\.0\.[0-9]+/mmsource-2\.0\.0-git[0-9]+-linux\.tar\.gz"
    )

    @staticmethod
    def archive_type_from_path(path: str) -> Optional[str]:
        """Return a normalized archive type from a local or remote filename."""
        lower_path = path.lower()
        for suffix, archive_type in (
            (".tar.zstd", "tar.zst"),
            (".tar.lzma", "tar.lzma"),
            (".tar.zst", "tar.zst"),
            (".tar.bz2", "tar.bz2"),
            (".tar.gz", "tar.gz"),
            (".tar.xz", "tar.xz"),
            (".tzst", "tar.zst"),
            (".tbz2", "tar.bz2"),
            (".tgz", "tar.gz"),
            (".txz", "tar.xz"),
            (".tlz", "tar.lzma"),
            (".tbz", "tar.bz2"),
            (".zip", "zip"),
            (".7z", "7z"),
            (".rar", "rar"),
            (".tar", "tar"),
            (".zstd", "zst"),
            (".lzma", "lzma"),
            (".zst", "zst"),
            (".gz", "gz"),
            (".bz2", "bz2"),
            (".xz", "xz"),
        ):
            if lower_path.endswith(suffix):
                return archive_type
        return None

    @staticmethod
    def _filename_from_content_disposition(value: str) -> Optional[str]:
        """Decode a Content-Disposition filename, preferring RFC 5987 filename*."""
        if not value:
            return None
        message = Message()
        try:
            message["Content-Disposition"] = value
            params = (
                message.get_params(
                    header="content-disposition",
                    unquote=True,
                )
                or []
            )
        except TypeError, ValueError:
            return None

        regular_names: List[str] = []
        extended_names: List[str] = []
        for key, raw_value in params[1:]:
            if key.lower() != "filename" or raw_value is None:
                continue
            if isinstance(raw_value, tuple):
                try:
                    decoded = collapse_rfc2231_value(raw_value, errors="strict")
                except LookupError, UnicodeError, TypeError, ValueError:
                    continue
                extended_names.append(decoded)
            elif isinstance(raw_value, str):
                regular_names.append(raw_value)

        candidates = extended_names or regular_names
        return candidates[0] if candidates else None

    @classmethod
    def _validate_download_filename(cls, filename: str) -> Tuple[Optional[str], str]:
        """Return a safe direct-child archive filename or a public error."""
        if not isinstance(filename, str):
            return None, "Download response did not provide a valid filename"
        filename = filename.strip()
        if (
            not filename
            or filename in (".", "..")
            or "/" in filename
            or "\\" in filename
            or posixpath.basename(filename) != filename
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)
        ):
            return None, "Download response filename is unsafe"
        if len(filename.encode("utf-8")) > cls.REMOTE_DOWNLOAD_FILENAME_MAX_BYTES:
            return None, "Download response filename is too long"
        if cls.archive_type_from_path(filename) is None:
            return None, "Download response filename does not use a supported archive extension"
        return filename, ""

    @classmethod
    def _filename_from_download_response(
        cls,
        raw_headers: str,
        effective_url: str,
    ) -> Tuple[Optional[str], str]:
        """Resolve a safe filename from the final headers, then effective URL."""
        normalized_headers = raw_headers.replace("\r\n", "\n").replace("\r", "\n")
        content_disposition = None
        for block in reversed(re.split(r"\n\s*\n", normalized_headers)):
            lines = block.splitlines()
            if not lines or not lines[0].lstrip().upper().startswith("HTTP/"):
                continue

            unfolded: List[str] = []
            for line in lines[1:]:
                if line[:1] in (" ", "\t") and unfolded:
                    unfolded[-1] += " " + line.strip()
                else:
                    unfolded.append(line)
            for line in unfolded:
                name, separator, value = line.partition(":")
                if separator and name.strip().lower() == "content-disposition":
                    content_disposition = value.strip()
            break

        candidates: List[str] = []
        if content_disposition:
            header_filename = cls._filename_from_content_disposition(content_disposition)
            if header_filename:
                candidates.append(header_filename)

        try:
            raw_url_filename = posixpath.basename(urlsplit(effective_url.strip()).path)
            if raw_url_filename:
                candidates.append(unquote(raw_url_filename))
        except ValueError:
            pass

        last_error = "Download response did not provide an archive filename"
        for candidate in candidates:
            filename, error = cls._validate_download_filename(candidate)
            if filename is not None:
                return filename, ""
            last_error = error
        return None, last_error

    @classmethod
    def _validate_download_hostname(cls, hostname: str) -> str | None:
        if not hostname or "%" in hostname or len(hostname) > 253:
            return "Download URL hostname is invalid"
        labels = hostname.split(".")
        if any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
        ):
            return "Download URL hostname is invalid"
        return None

    @classmethod
    def _validate_remote_download_url(cls, url: str) -> Tuple[Optional[Any], str]:
        """Validate one URL hop before it is resolved on the SSH host."""
        if not isinstance(url, str) or not url or len(url) > cls.REMOTE_DOWNLOAD_URL_MAX_LENGTH:
            return None, "Download URL is missing or too long"
        if any(ord(char) < 32 or ord(char) == 127 for char in url):
            return None, "Download URL contains control characters"

        try:
            parsed = urlsplit(url)
            port = parsed.port
        except TypeError, ValueError:
            return None, "Download URL is malformed"

        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return None, "Only absolute HTTP and HTTPS download URLs are supported"
        if parsed.username is not None or parsed.password is not None:
            return None, "Download URLs containing credentials are not supported"
        if parsed.fragment:
            return None, "Download URL fragments are not supported"
        if port is not None and not 1 <= port <= 65535:
            return None, "Download URL port is outside the valid range"

        hostname = parsed.hostname.rstrip(".").lower()
        hostname_error = cls._validate_download_hostname(hostname)
        if not hostname or "%" in hostname:
            return None, "Download URL hostname is invalid"

        try:
            literal_address = ipaddress.ip_address(hostname)
        except ValueError:
            literal_address = None
            try:
                socket.inet_aton(hostname)
            except OSError:
                pass
            else:
                return None, "Non-canonical numeric IPv4 download URLs are not allowed"

            if hostname_error:
                return None, hostname_error
        else:
            mapped_address = getattr(literal_address, "ipv4_mapped", None)
            if not literal_address.is_global or (
                mapped_address is not None and not mapped_address.is_global
            ):
                return None, "Non-public IP address download URLs are not allowed"

        return parsed, ""

    async def _resolve_public_download_address(
        self,
        hostname: str,
        getent_tool: str,
    ) -> Tuple[Optional[str], str]:
        """Resolve a host remotely and choose one address after validating all."""
        literal_hostname = hostname.rstrip(".").lower()
        try:
            literal_address = ipaddress.ip_address(literal_hostname)
        except ValueError:
            literal_address = None

        if literal_address is not None:
            mapped_address = getattr(literal_address, "ipv4_mapped", None)
            if not literal_address.is_global or (
                mapped_address is not None and not mapped_address.is_global
            ):
                return None, "Download URL resolves to a non-public IP address"
            return literal_address.compressed, ""

        success, stdout, _ = await self.execute_command(
            f"LC_ALL=C {shlex.quote(getent_tool)} ahosts {shlex.quote(hostname)} 2>/dev/null",
            timeout=15,
        )
        addresses = []
        for line in stdout.splitlines():
            fields = line.split()
            if not fields:
                continue
            try:
                address = ipaddress.ip_address(fields[0])
            except ValueError:
                continue
            mapped_address = getattr(address, "ipv4_mapped", None)
            if not address.is_global or (
                mapped_address is not None and not mapped_address.is_global
            ):
                return None, "Download URL resolves to a non-public IP address"
            if "%" in fields[0]:
                return None, "Download URL resolves to an unsupported scoped IP address"
            if address not in addresses:
                addresses.append(address)

        if not success or not addresses:
            return None, "Download host could not be resolved from the managed server"

        # Prefer IPv4 when both families are returned.  getent applies the SSH
        # host's address-family policy, and this avoids choosing an unusable
        # IPv6 route while curl remains pinned to the validated result.
        selected_address = min(addresses, key=lambda item: item.version != 4)
        return selected_address.compressed, ""

    @staticmethod
    def _curl_resolve_entry(hostname: str, port: int, address: str) -> str:
        """Build curl's host:port:address pin, including IPv6 brackets."""
        resolve_host = f"[{hostname}]" if ":" in hostname else hostname
        resolve_address = f"[{address}]" if ":" in address else address
        return f"{resolve_host}:{port}:{resolve_address}"

    @staticmethod
    def _download_response_metadata(
        raw_headers: str,
    ) -> Tuple[Optional[int], Dict[str, str], str]:
        """Return the last HTTP response status and unfolded headers."""
        normalized_headers = raw_headers.replace("\r\n", "\n").replace("\r", "\n")
        for block in reversed(re.split(r"\n\s*\n", normalized_headers)):
            lines = block.splitlines()
            if not lines:
                continue
            status_match = re.match(r"^\s*HTTP/\S+\s+([0-9]{3})(?:\s|$)", lines[0], re.I)
            if status_match is None:
                continue

            unfolded: List[str] = []
            for line in lines[1:]:
                if line[:1] in (" ", "\t") and unfolded:
                    unfolded[-1] += " " + line.strip()
                else:
                    unfolded.append(line)
            headers: Dict[str, str] = {}
            for line in unfolded:
                name, separator, value = line.partition(":")
                if separator:
                    headers[name.strip().lower()] = value.strip()
            return int(status_match.group(1)), headers, ""
        return None, {}, "Download response did not include valid HTTP headers"

    @classmethod
    def _redirect_url_from_response(
        cls,
        raw_headers: str,
        current_url: str,
    ) -> Tuple[Optional[str], bool, str]:
        """Resolve and validate a redirect Location from one curl response."""
        status_code, headers, metadata_error = cls._download_response_metadata(raw_headers)
        if status_code is None:
            return None, False, metadata_error
        if status_code in cls.REMOTE_DOWNLOAD_REDIRECT_CODES:
            location = headers.get("location")
            if not location:
                return None, True, "Download redirect did not include a Location header"
            if any(ord(char) < 32 or ord(char) == 127 for char in location):
                return None, True, "Download redirect Location is invalid"
            try:
                redirect_url = urljoin(current_url, location.strip(" "))
            except TypeError, ValueError:
                return None, True, "Download redirect Location is invalid"
            _, validation_error = cls._validate_remote_download_url(redirect_url)
            if validation_error:
                return None, True, f"Download redirect target is not allowed: {validation_error}"
            return redirect_url, True, ""
        if 300 <= status_code < 400:
            return None, False, f"Download returned unsupported redirect status HTTP {status_code}"
        if not 200 <= status_code < 300:
            return None, False, f"Download failed with HTTP status {status_code}"
        return None, False, ""

    @staticmethod
    def _redact_download_error(message: str) -> str:
        """Remove complete HTTP(S) URLs, including signed query strings."""
        return re.sub(
            r'(?i)https?://[^\s<>"\']+',
            "[redacted URL]",
            message or "Remote command failed",
        )

    async def _reconnect_current_pooled_connection(
        self, server: Server
    ) -> Tuple[bool, Optional[asyncssh.SSHClientConnection], str]:
        """Replace this manager's lease without affecting another generation."""
        previous_connection = self.conn
        pool = _legacy_connection_pool()
        success, connection, message = await pool.reconnect_for_connection(
            server,
            previous_connection,
        )
        if success:
            self.conn = connection
            if previous_connection is not None and previous_connection is not connection:
                await pool.release_connection(
                    server,
                    previous_connection,
                )
        return success, connection, message

    async def _handle_sftp_error_with_reconnect(
        self, error: Exception, server: Server, operation_name: str, retry_func
    ):
        """
        Handle SFTP errors with automatic reconnection and retry

        Args:
            error: The exception that occurred
            server: Server instance
            operation_name: Name of the operation for logging
            retry_func: Async function to retry after reconnection

        Returns:
            Result from retry_func if successful, or raises the error
        """
        error_msg = str(error)
        # Check if this is a connection error that might be fixed by reconnection
        if any(
            keyword in error_msg.lower()
            for keyword in ["open failed", "connection", "broken pipe", "reset"]
        ):
            logger.warning(
                f"[SSH Manager] SFTP error detected in {operation_name}, attempting reconnection: {error_msg}"
            )
            # Try to reconnect - use server parameter for consistency
            if self.use_pool:
                success, conn, reconnect_msg = await self._reconnect_current_pooled_connection(
                    server
                )
                if success:
                    logger.info(f"[SSH Manager] Reconnection successful, retrying {operation_name}")
                    # Retry the operation once after reconnection
                    try:
                        return await retry_func()
                    except Exception as retry_e:
                        logger.error(
                            f"[SSH Manager] Retry {operation_name} after reconnection failed: {str(retry_e)}"
                        )
                        raise Exception(
                            "操作失败（重连后重试仍失败）| "
                            f"Operation failed after reconnection: {str(retry_e)}"
                        ) from retry_e
                else:
                    logger.error(f"[SSH Manager] Reconnection failed: {reconnect_msg}")
                    raise Exception(f"连接失败 | Connection failed: {reconnect_msg}")
        raise error

    @staticmethod
    def _apply_github_download_proxy(download_url: str, github_proxy: Optional[str]) -> str:
        """Apply a configured GitHub download proxy to release asset URLs."""
        if not github_proxy or not github_proxy.strip():
            return download_url

        if not download_url.startswith("https://github.com/"):
            return download_url

        proxy_base = github_proxy.strip().rstrip("/")
        github_prefix = "https://github.com"
        if proxy_base.endswith(github_prefix):
            return f"{proxy_base}{download_url[len(github_prefix) :]}"

        return f"{proxy_base}/{download_url}"

    async def _fetch_latest_metamod_url(self, progress_callback=None) -> Tuple[bool, str]:
        """
        Fetch the latest Metamod:Source 2.0 Linux dev build URL.

        GitHub marks stable 1.12 releases as latest, so this intentionally
        reads the sourcemm dev page first and falls back to the GitHub releases
        list filtered to 2.0.0 dev assets.
        """

        async def send_progress(message: str):
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        pattern = self.METAMOD_LINUX_DOWNLOAD_PATTERN
        downloads_url = shlex.quote(self.METAMOD_DOWNLOADS_URL)
        releases_api = shlex.quote(self.METAMOD_GITHUB_RELEASES_API)

        sourcemm_cmd = (
            f"page=$(curl -fsSL {downloads_url} 2>/dev/null); "
            f"latest=$(printf '%s' \"$page\" | grep -Eo '{pattern}' | sort -V | tail -n 1); "
            'if [ -n "$latest" ]; then printf \'%s\\n\' "$latest"; exit 0; fi; '
            "build=$(printf '%s' \"$page\" | sed -n 's/.*Latest downloads for version 2\\.0 - build \\([0-9][0-9]*\\).*/\\1/p' | head -n 1); "
            'if [ -n "$build" ]; then '
            'printf \'https://github.com/alliedmodders/metamod-source/releases/download/2.0.0.%s/mmsource-2.0.0-git%s-linux.tar.gz\\n\' "$build" "$build"; '
            "exit 0; fi; "
            "exit 1"
        )

        github_cmd = (
            f"latest=$(curl -fsSL {releases_api} 2>/dev/null | grep -Eo '{pattern}' | sort -V | tail -n 1); "
            'if [ -n "$latest" ]; then printf \'%s\\n\' "$latest"; exit 0; fi; '
            "exit 1"
        )

        for source_name, command in (
            ("sourcemm dev downloads page", sourcemm_cmd),
            ("GitHub releases API", github_cmd),
        ):
            success, url, _ = await self.execute_command(command, timeout=30)
            metamod_url = url.strip().splitlines()[-1].strip() if url.strip() else ""

            if success and metamod_url and re.fullmatch(pattern, metamod_url):
                await send_progress(
                    f"Found latest Metamod dev build from {source_name}: {metamod_url}"
                )
                return True, metamod_url

        return False, (
            "Failed to determine the latest Metamod:Source 2.0 dev build. "
            "Please check access to sourcemm.net and api.github.com."
        )

    async def _fetch_github_release_url(
        self, repo: str, pattern: str, progress_callback=None, github_proxy: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Helper function to fetch the latest release URL from GitHub

        Args:
            repo: Repository in format "owner/repo" (e.g., "Source2ZE/CS2Fixes")
                  Supports alphanumeric, hyphens, underscores, and periods
            pattern: Grep pattern to match the browser_download_url (basic grep pattern, not regex)
                    Example: '\"browser_download_url\": \"[^\"]*CS2Fixes-[^\"]*-linux\\.tar\\.gz\"'
                    NOTE: This pattern is used in shell command - ensure it comes from trusted source
            progress_callback: Optional callback for progress messages
            github_proxy: Optional GitHub proxy URL (e.g., https://ghfast.top/https://github.com)

        Returns:
            Tuple[bool, str]: (success, download_url or error_message)
        """

        async def send_progress(message: str):
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        # Validate repo parameter to prevent command injection
        # Repository should only contain alphanumeric, hyphens, underscores, periods, and one slash
        # Supports names like "jquery/jquery.ui"
        if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", repo):
            return False, f"Invalid repository format: {repo}. Expected format: owner/repo"

        # Note: pattern parameter is used in shell command and should be from trusted source only
        # In this codebase, it's called with hardcoded patterns from install_cs2fixes method

        # GitHub API URL - DO NOT use proxy for API requests
        # Proxy services like ghfast.top only work for file downloads, not API
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        # Note: github_proxy parameter exists but is NOT used for API requests
        # because proxy services don't support GitHub API endpoints

        # Primary method: Use grep pattern
        # Use basic grep without -P flag for better portability
        get_url_cmd = (
            f"curl -sL {api_url} | grep -o '{pattern}' | grep -o 'https://[^\"]*' | head -1"
        )
        success, url, stderr = await self.execute_command(get_url_cmd, timeout=30)

        if success and url.strip():
            return True, url.strip()

        # Fallback 1: Try to find any download URL from browser_download_url
        # This is a broader search when specific pattern fails
        await send_progress("⚠ Trying alternative API query...")
        alt_cmd = f"curl -sL {api_url} | grep '\"browser_download_url\"' | grep -o 'https://[^\"]*' | head -1"
        success, url, _ = await self.execute_command(alt_cmd, timeout=30)

        if success and url.strip():
            # Verify the URL is a valid GitHub release URL
            # Must start with https://github.com/owner/repo/releases/download/
            url_stripped = url.strip()
            expected_prefix = f"https://github.com/{repo}/releases/download/"
            if url_stripped.startswith(expected_prefix):
                return True, url_stripped

        # Fallback 2: Get tag with proper semantic version matching
        await send_progress("⚠ Could not fetch from GitHub API, trying tag-based approach...")
        # Match semantic versioning: v1.2.3 or v1.2 format
        tag_cmd = f"curl -sL {api_url} | grep '\"tag_name\"' | grep -o 'v[0-9]\\+\\(\\.[0-9]\\+\\)*' | head -1"
        tag_success, tag, _ = await self.execute_command(tag_cmd, timeout=30)

        if tag_success and tag.strip():
            return (
                False,
                f"Found tag {tag.strip()} but could not construct download URL automatically. Please check the repository.",
            )

        return (
            False,
            f"Failed to fetch latest release from GitHub repository {repo}. Please check your internet connection.",
        )
