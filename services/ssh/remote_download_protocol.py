"""Focused lifecycle stages with explicit session inputs."""

# ruff: noqa: F403,F405

from services.compat import LateBoundModule

from .common import *

host = LateBoundModule("services.ssh.file_download_extract")


async def _check_download_target(
    self: SSHMixinBase, target_path: str | None, overwrite: bool
) -> str | None:
    if target_path is None:
        return None
    async with self.conn.start_sftp_client() as sftp:
        try:
            existing_attrs = await sftp.lstat(target_path)
        except host.asyncssh.SFTPNoSuchFile, host.asyncssh.SFTPNoSuchPath:
            existing_attrs = None
        if existing_attrs is not None:
            if not overwrite:
                return "Target file already exists. Enable overwrite to replace it."
            if existing_attrs.type != host.FILEXFER_TYPE_REGULAR:
                return "Existing target must be a regular file and cannot be a symlink"
    return None


async def _download_pinned_hop(
    self: SSHMixinBase,
    current_url: str,
    parsed_url,
    curl_tool: str,
    getent_tool: str,
    headers_path: str,
    safe_headers: str,
    safe_part: str,
) -> tuple[str | None, str]:
    resolved_address, resolve_error = await self._resolve_public_download_address(
        parsed_url.hostname,
        getent_tool,
    )
    if resolved_address is None:
        return None, resolve_error
    request_port = parsed_url.port or (443 if parsed_url.scheme.lower() == "https" else 80)
    resolve_entry = self._curl_resolve_entry(
        parsed_url.hostname,
        request_port,
        resolved_address,
    )

    # Do not use --location: the response is inspected before the
    # next hop is allowed.  --noproxy ensures an HTTP proxy cannot
    # bypass the validated/pinned origin address.
    curl_command = (
        f"umask 077; {host.shlex.quote(curl_tool)} --fail --silent --show-error "
        f"--request GET --noproxy {host.shlex.quote('*')} "
        f"--proto {host.shlex.quote('=http,https')} "
        f"--connect-timeout 20 --max-time {self.REMOTE_DOWNLOAD_TIMEOUT} "
        f"--retry 2 --retry-delay 2 --max-filesize {self.REMOTE_DOWNLOAD_MAX_BYTES} "
        f"--resolve {host.shlex.quote(resolve_entry)} "
        f"--dump-header {safe_headers} --output {safe_part} "
        f"--url {host.shlex.quote(current_url)}"
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
        return None, f"Download failed: {error_detail}"

    async with self.conn.start_sftp_client() as sftp:
        headers_attrs = await sftp.lstat(headers_path)
        if headers_attrs.type != host.FILEXFER_TYPE_REGULAR:
            return None, "Download response metadata is not a regular file"
        if headers_attrs.size > self.REMOTE_DOWNLOAD_METADATA_MAX_BYTES:
            return None, "Download response metadata is too large"
        async with sftp.open(headers_path, "rb") as header_file:
            raw_header_bytes = await header_file.read()
    if isinstance(raw_header_bytes, str):
        raw_headers = raw_header_bytes
    else:
        raw_headers = raw_header_bytes.decode("iso-8859-1", errors="replace")
    return raw_headers, ""


async def _download_pinned_hops(
    self: SSHMixinBase,
    url: str,
    curl_tool: str,
    getent_tool: str,
    headers_path: str,
    safe_headers: str,
    safe_part: str,
) -> tuple[bool, str, str, str]:
    current_url = url
    final_url = url
    raw_headers = ""
    seen_urls = set()
    for redirect_count in range(self.REMOTE_DOWNLOAD_MAX_REDIRECTS + 1):
        parsed_url, url_error = self._validate_remote_download_url(current_url)
        if parsed_url is None:
            return False, url_error, "", ""
        if current_url in seen_urls:
            return False, "Download redirect loop detected", "", ""
        seen_urls.add(current_url)

        raw_headers, hop_error = await host._download_pinned_hop(
            self,
            current_url,
            parsed_url,
            curl_tool,
            getent_tool,
            headers_path,
            safe_headers,
            safe_part,
        )
        if raw_headers is None:
            return False, hop_error, "", ""

        redirect_url, is_redirect, redirect_error = self._redirect_url_from_response(
            raw_headers,
            current_url,
        )
        if redirect_error:
            return False, redirect_error, "", ""
        if is_redirect:
            if redirect_count >= self.REMOTE_DOWNLOAD_MAX_REDIRECTS:
                return False, "Download exceeded the redirect limit", "", ""
            if redirect_url is None:
                return False, "Download redirect target could not be resolved", "", ""
            current_url = redirect_url
            continue

        final_url = current_url
        break
    return True, "", raw_headers, final_url


async def _resolve_downloaded_target(
    self: SSHMixinBase,
    part_path: str,
    parent_dir: str,
    target_path: str | None,
    raw_headers: str,
    final_url: str,
) -> tuple[str | None, str]:
    async with self.conn.start_sftp_client() as sftp:
        attrs = await sftp.lstat(part_path)
        if attrs.type != host.FILEXFER_TYPE_REGULAR or not attrs.size:
            return None, "Downloaded archive is empty or is not a regular file"
        if attrs.size > self.REMOTE_DOWNLOAD_MAX_BYTES:
            return None, "Downloaded archive exceeds the configured size limit"

        if target_path is None:
            resolved_filename, filename_error = self._filename_from_download_response(
                raw_headers,
                final_url,
            )
            if resolved_filename is None:
                return None, filename_error
            target_path = host.posixpath.join(parent_dir, resolved_filename)

    if target_path is None:
        return None, "Download response filename could not be resolved"
    return target_path, ""


async def _publish_downloaded_target(
    self: SSHMixinBase,
    server: Server,
    target_path: str,
    safe_part: str,
    overwrite: bool,
    resolved_target_callback,
) -> tuple[bool, str]:
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
        if host.inspect.isawaitable(callback_result):
            await callback_result

    target_error = await host._check_download_target(self, target_path, overwrite)
    if target_error is not None:
        return False, target_error

    safe_target = host.shlex.quote(target_path)
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


async def _download_destination(
    self: SSHMixinBase, server: Server, target_path: str | None, destination_path: str | None
) -> tuple[bool, str, str, str]:
    if target_path is not None and self.archive_type_from_path(target_path) is None:
        return False, "Target filename does not use a supported archive extension", "", ""

    if target_path is not None:
        parent_dir = host.posixpath.dirname(target_path)
        validation_path = target_path
    elif destination_path:
        parent_dir = host.posixpath.normpath(destination_path)
        validation_path = parent_dir
    else:
        return False, "Download destination path is required", "", ""

    valid, validation_error = await self.validate_path_within_base(
        server.game_directory,
        validation_path,
        server,
        allow_missing=True,
    )
    if not valid:
        return False, validation_error, "", ""

    curl_tool = await self._find_remote_tool(("curl",))
    if not curl_tool:
        return False, "Required download tool is missing: install curl", "", ""
    getent_tool = await self._find_remote_tool(("getent",))
    if not getent_tool:
        return False, "Required DNS resolver is missing: install getent", "", ""
    return True, parent_dir, curl_tool, getent_tool
