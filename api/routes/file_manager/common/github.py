"""Download-URL validation and GitHub Actions artifact resolution."""

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import status

from services.compat import LateBoundModule

host = LateBoundModule("api.routes.file_manager.common")


def _validate_download_url(url: str) -> str:
    """Apply transport-level validation before passing a URL to remote curl."""
    if not isinstance(url, str) or not url or len(url) > host.DOWNLOAD_URL_MAX_LENGTH:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"URL is required and must not exceed {host.DOWNLOAD_URL_MAX_LENGTH} characters",
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="URL cannot contain control characters",
        )

    try:
        parsed = host.urlsplit(url)
        port = parsed.port  # Accessing this validates malformed ports.
    except ValueError as exc:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Invalid URL: {exc}"
        ) from exc

    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only absolute HTTP and HTTPS URLs are supported",
        )
    if parsed.username is not None or parsed.password is not None:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="URLs containing embedded credentials are not supported",
        )
    if parsed.fragment:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="URL fragments are not supported",
        )
    if port is not None and not 1 <= port <= 65535:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="URL port is outside the valid range",
        )

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Localhost download URLs are not allowed",
        )
    try:
        address = host.ipaddress.ip_address(hostname)
    except ValueError:
        address = None
        # curl and the platform resolver accept historical inet_aton forms
        # such as 2130706433, 127.1, 0177.0.0.1, and 0x7f000001.  They are
        # ambiguous to urllib/ipaddress and can otherwise disguise a private
        # IPv4 literal as a hostname.  Canonical IPv4 text was handled above;
        # reject every other numeric form instead of resolving it as DNS.
        try:
            host.socket.inet_aton(hostname)
        except OSError:
            pass
        else:
            raise host.HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Non-canonical numeric IPv4 download URLs are not allowed",
            ) from None
    if address is not None and not address.is_global:
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Non-public IP address download URLs are not allowed",
        )

    return url


def _download_archive_filename(
    url: str,
    requested_filename: Optional[str],
    *,
    allow_unresolved: bool = False,
) -> Optional[str]:
    """Choose a safe archive filename from an explicit value or URL path.

    A URL endpoint is allowed to omit an archive suffix when the caller will
    resolve the final response filename asynchronously. The basename is taken
    before percent-decoding so an encoded slash cannot hide path traversal.
    """
    if requested_filename is not None and requested_filename.strip():
        filename = requested_filename
    else:
        raw_filename = host.posixpath.basename(host.urlsplit(url).path)
        filename = host.unquote(raw_filename)
        if not filename:
            if allow_unresolved:
                return None
            raise host.HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="filename is required when the URL path has no filename",
            )

    filename = host._validate_direct_child_name(filename, "filename")
    if host.SSHManager.archive_type_from_path(filename) is None:
        if allow_unresolved and not (requested_filename and requested_filename.strip()):
            return None
        raise host.HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Unsupported archive filename. Supported formats: "
                f"{host.SSHManager.SUPPORTED_ARCHIVE_FORMATS_LABEL}"
            ),
        )
    return filename


def _parse_github_actions_artifact_url(url: str) -> Optional[Tuple[str, str, int]]:
    """Parse a GitHub Actions artifact web URL without accepting lookalike hosts."""
    try:
        parsed = host.urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != "github.com":
        return None
    match = host.GITHUB_ACTIONS_ARTIFACT_URL_RE.fullmatch(parsed.path)
    if not match:
        return None
    owner, repository, artifact_id = match.groups()
    return owner, repository, int(artifact_id)


def _github_artifact_http_error(
    status_code: int, token: str | None, *, metadata: bool
) -> RuntimeError:
    if status_code in (401, 403):
        if token:
            return RuntimeError("GitHub token is invalid or lacks Actions artifact read access")
        return RuntimeError(
            "GitHub Actions artifact download requires a GitHub token with Actions read access; configure it in your profile"
        )
    if status_code == 404 and metadata:
        return RuntimeError(
            "GitHub Actions artifact was not found or is not accessible with the configured token"
        )
    if status_code in (404, 410):
        return RuntimeError("GitHub Actions artifact is unavailable or has expired")
    return RuntimeError(f"GitHub artifact request failed (HTTP {status_code})")


async def _resolve_github_actions_artifact(
    url: str,
    github_token: Optional[str],
) -> Tuple[str, str]:
    """Resolve GitHub artifact metadata and its short-lived signed URL locally.

    Redirects are deliberately handled manually. This keeps the user's GitHub
    token on the panel host and prevents it from being forwarded to GitHub's
    object-storage redirect target or to the managed SSH server.
    """
    artifact = host._parse_github_actions_artifact_url(url)
    if artifact is None:
        raise RuntimeError("Invalid GitHub Actions artifact URL")
    owner, repository, artifact_id = artifact
    api_base = (
        "https://api.github.com/repos/"
        f"{host.quote(owner, safe='')}/{host.quote(repository, safe='')}/actions/artifacts/{artifact_id}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": host.GITHUB_API_VERSION,
        "User-Agent": "UpKK-CS2-ServerManager",
    }
    token = github_token.strip() if github_token and github_token.strip() else None
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with host.httpx.AsyncClient(
            timeout=host.httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
        ) as client:
            metadata_response = await client.get(api_base, headers=headers)
            if metadata_response.status_code != 200:
                raise host._github_artifact_http_error(
                    metadata_response.status_code, token, metadata=True
                )

            try:
                metadata = metadata_response.json()
            except ValueError as exc:
                raise RuntimeError("GitHub returned invalid artifact metadata") from exc
            if metadata.get("expired") is True:
                raise RuntimeError("GitHub Actions artifact has expired")
            artifact_name = metadata.get("name")
            if not isinstance(artifact_name, str) or not artifact_name.strip():
                raise RuntimeError("GitHub artifact metadata does not contain a valid name")

            filename = (
                artifact_name if artifact_name.lower().endswith(".zip") else f"{artifact_name}.zip"
            )
            try:
                filename = host._validate_direct_child_name(filename, "artifact filename")
            except host.HTTPException as exc:
                raise RuntimeError(f"GitHub artifact name is unsafe: {exc.detail}") from exc
            if host.SSHManager.archive_type_from_path(filename) != "zip":
                raise RuntimeError("GitHub artifact filename is not a ZIP archive")

            download_response = await client.get(f"{api_base}/zip", headers=headers)
            if download_response.status_code != 302:
                raise host._github_artifact_http_error(
                    download_response.status_code, token, metadata=False
                )
            location = download_response.headers.get("location")
            if not location:
                raise RuntimeError("GitHub artifact response did not include a download redirect")
            signed_url = str(download_response.url.join(location))
    except host.httpx.TimeoutException as exc:
        raise RuntimeError("GitHub artifact request timed out") from exc
    except host.httpx.RequestError as exc:
        raise RuntimeError("Could not connect to GitHub to resolve the artifact") from exc

    try:
        signed_url = host._validate_download_url(signed_url)
    except host.HTTPException as exc:
        raise RuntimeError(
            f"GitHub returned an invalid artifact download URL: {exc.detail}"
        ) from exc
    return signed_url, filename
