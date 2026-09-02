# ruff: noqa: F401
"""
File manager routes for server file operations
"""

import asyncio
import ipaddress
import logging
import os
import posixpath
import re
import socket
import tempfile
import time
import uuid
from typing import Annotated, Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

import anyio
import httpx
import jwt
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from jwt import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select
from starlette.background import BackgroundTask

from api.dependencies import DatabaseSession, require_server_access
from modules import Server, User, get_current_active_user, get_db, settings
from services import SSHManager
from services.concurrency_limiter import KeyedConcurrencyLimiter
from services.github_credentials import get_effective_github_token
from services.task_registry import file_task_registry

logger = logging.getLogger(__name__)

EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS = 3600  # 1 hour

EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS = 7200  # 2 hours

STREAMING_DOWNLOAD_THRESHOLD_BYTES = 3 * 1024 * 1024  # 3MB

DOWNLOAD_TICKET_TTL_SECONDS = 60

REMOTE_NAME_MAX_BYTES = 255

DOWNLOAD_URL_MAX_LENGTH = 4096

MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024

GITHUB_API_VERSION = "2022-11-28"

GITHUB_ACTIONS_ARTIFACT_URL_RE = re.compile(
    r"^/([^/]+)/([^/]+)/actions/runs/[0-9]+/artifacts/([0-9]+)/?$"
)

extraction_tasks: Dict[str, Dict[str, Any]] = {}

_extraction_task_refs: Dict[str, asyncio.Task] = {}

extraction_tasks_lock = asyncio.Lock()

download_url_tasks: Dict[str, Dict[str, Any]] = {}

_download_url_task_refs: Dict[str, asyncio.Task] = {}

download_url_tasks_lock = asyncio.Lock()

download_tickets: Dict[str, Dict[str, Any]] = {}

download_tickets_lock = asyncio.Lock()

_file_task_limiter = KeyedConcurrencyLimiter[int](global_limit=4, per_key_limit=2)


async def _run_bounded_file_task(user_id: int, callback) -> None:
    async with _file_task_limiter.slot(user_id):
        await callback()


async def shutdown_background_tasks() -> None:
    """Compatibility wrapper for lifecycle-owned task cleanup."""
    await file_task_registry.shutdown()
    _download_url_task_refs.clear()
    _extraction_task_refs.clear()


class FileInfo(SQLModel):
    """File information model"""

    name: str
    path: str
    type: str
    size: int
    modified: float
    permissions: str
    is_symlink: bool


class DirectoryListResponse(SQLModel):
    """Directory listing response"""

    path: str
    files: List[FileInfo]


class FileContentRequest(SQLModel):
    """File content update request"""

    content: str


class CreateDirectoryRequest(SQLModel):
    """Create directory request"""

    name: str


class DownloadTicketRequest(SQLModel):
    """Create a short-lived browser download ticket"""

    path: str


class DeleteRequest(SQLModel):
    """Delete file/directory request"""

    path: str


class RenameRequest(SQLModel):
    """Rename file/directory request"""

    old_name: str
    new_name: str


class CopyPathsRequest(SQLModel):
    """Copy one or more remote paths into a destination directory."""

    sources: List[str]
    destination: str


class ExtractArchiveRequest(SQLModel):
    """Extract archive request"""

    archive_path: str
    destination_path: Optional[str] = None
    overwrite: bool = False
    source_folder: Optional[str] = None
    strip_source_folder: bool = False


class DownloadUrlRequest(SQLModel):
    """Download an archive URL to a remote server directory."""

    url: str
    destination_path: str
    filename: Optional[str] = None
    overwrite: bool = False


class InspectArchiveRequest(SQLModel):
    """Inspect folders contained in a remote archive."""

    archive_path: str


async def get_server_for_user(server_id: int, db: AsyncSession, current_user: User) -> Server:
    """Helper to get server and verify ownership - admins can access any server"""
    return await require_server_access(db, server_id, current_user)


async def _create_download_ticket(user_id: int, server_id: int, path: str) -> str:
    """Create a short-lived one-time ticket bound to a user, server and path."""
    now = time.monotonic()
    async with download_tickets_lock:
        expired_tickets = [
            ticket for ticket, info in download_tickets.items() if info["expires_at"] <= now
        ]
        for ticket in expired_tickets:
            download_tickets.pop(ticket, None)

        ticket = uuid.uuid4().hex
        download_tickets[ticket] = {
            "user_id": user_id,
            "server_id": server_id,
            "path": path,
            "expires_at": now + DOWNLOAD_TICKET_TTL_SECONDS,
        }
        return ticket


async def _consume_download_ticket(ticket: str, server_id: int, path: str) -> Optional[int]:
    """Consume and validate a one-time download ticket."""
    now = time.monotonic()
    async with download_tickets_lock:
        ticket_info = download_tickets.pop(ticket, None)

    if not ticket_info:
        return None
    if ticket_info["expires_at"] <= now:
        return None
    if ticket_info["server_id"] != server_id or ticket_info["path"] != path:
        return None
    return int(ticket_info["user_id"])


async def get_current_active_user_for_download(
    server_id: int,
    path: str,
    ticket: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    *,
    db: DatabaseSession,
) -> User:
    """Authenticate downloads with a one-time ticket or a normal bearer token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_id: Optional[int] = None

    if ticket:
        user_id = await _consume_download_ticket(ticket, server_id, path)
        if user_id is None:
            raise credentials_exception
    elif authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise credentials_exception

        try:
            payload = jwt.decode(
                token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
            )
            user_id_str = payload.get("sub")
            if user_id_str is None:
                raise credentials_exception
            user_id = int(user_id_str)
        except InvalidTokenError, ValueError:
            raise credentials_exception from None
    else:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    return user


DownloadUser = Annotated[User, Depends(get_current_active_user_for_download)]


def is_path_safe(base_path: str, requested_path: str) -> bool:
    """
    Verify that the requested path is within the server's directory
    Prevents path traversal attacks
    """
    # Remote game servers use POSIX paths even when the panel runs on Windows.
    base = posixpath.normpath(base_path)
    requested = posixpath.normpath(requested_path)

    # Check if requested path is the base path or a child of it
    return requested == base or requested.startswith(base.rstrip("/") + "/")


def remote_join(*parts: str) -> str:
    """Join remote server paths using POSIX separators."""
    return posixpath.normpath(posixpath.join(*parts))


def safe_relative_upload_path(relative_path: str | None, filename: str | None) -> str:
    """Accept ``a/b/c.cfg`` for folder uploads. Reject ``..`` and absolute paths."""

    raw = (relative_path or "").strip().replace("\\", "/")
    fallback = _validate_direct_child_name(filename or "upload", "filename")
    if not raw:
        return fallback
    if raw.startswith("/") or raw.startswith("./../") or raw.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="relative_path must stay inside the destination folder",
        )
    parts = [part for part in raw.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="relative_path must stay inside the destination folder",
        )
    return "/".join(_validate_direct_child_name(part, "relative_path") for part in parts)


def _validate_direct_child_name(name: str, label: str = "name") -> str:
    """Validate a single remote path component without changing its value."""
    if not isinstance(name, str) or not name or not name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{label} cannot be empty"
        )
    if name in (".", "..") or posixpath.basename(name) != name or "/" in name or "\\" in name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label} must be a direct child name and cannot contain path separators",
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label} cannot contain control characters",
        )
    if len(name.encode("utf-8")) > REMOTE_NAME_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label} is too long (maximum {REMOTE_NAME_MAX_BYTES} UTF-8 bytes)",
        )
    return name


def _normalize_source_folder(source_folder: Optional[str]) -> Optional[str]:
    """Normalize and validate a directory name stored inside an archive."""
    if source_folder is None or not source_folder.strip():
        return None

    value = source_folder.strip().rstrip("/")
    while value.startswith("./"):
        value = value[2:]

    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source_folder must be a safe relative POSIX directory path",
        )

    normalized = posixpath.normpath(value)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source_folder cannot escape the archive root",
        )
    return normalized


_HUB_FILE_STATUS = {
    "queued": "pending",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
}


def resolve_extract_paths(server, archive_path: str, destination_path: str | None):
    """Normalize extract paths and reject anything outside the game directory."""
    archive = posixpath.normpath(archive_path)
    if not destination_path or not str(destination_path).strip():
        destination = posixpath.dirname(archive)
    else:
        destination = posixpath.normpath(destination_path)
    if not is_path_safe(server.game_directory, archive):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: archive path is outside server directory",
        )
    if not is_path_safe(server.game_directory, destination):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: destination path is outside server directory",
        )
    return archive, destination


def file_task_payload_from_hub(record: dict[str, Any]) -> dict[str, Any]:
    """Map a hub operation record onto the legacy FileTask poll shape."""
    status = _HUB_FILE_STATUS.get(str(record.get("status") or ""), "pending")
    message = str(record["message"]) if record.get("message") else None
    destination = record.get("destination") or record.get("destination_path")
    target = record.get("target_path")
    return {
        "task_id": str(record["operation_id"]),
        "status": status,
        "message": None if status == "failed" else message,
        "error": message if status == "failed" else None,
        "target_path": str(target) if target else None,
        "destination": str(destination) if destination else None,
        "destination_path": str(destination) if destination else None,
        "archive_path": str(record["archive_path"]) if record.get("archive_path") else None,
        "elapsed_seconds": None,
    }


def _validate_download_url(url: str) -> str:
    """Apply transport-level validation before passing a URL to remote curl."""
    if not isinstance(url, str) or not url or len(url) > DOWNLOAD_URL_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"URL is required and must not exceed {DOWNLOAD_URL_MAX_LENGTH} characters",
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="URL cannot contain control characters",
        )

    try:
        parsed = urlsplit(url)
        port = parsed.port  # Accessing this validates malformed ports.
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Invalid URL: {exc}"
        ) from exc

    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only absolute HTTP and HTTPS URLs are supported",
        )
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="URLs containing embedded credentials are not supported",
        )
    if parsed.fragment:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="URL fragments are not supported",
        )
    if port is not None and not 1 <= port <= 65535:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="URL port is outside the valid range",
        )

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Localhost download URLs are not allowed",
        )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
        # curl and the platform resolver accept historical inet_aton forms
        # such as 2130706433, 127.1, 0177.0.0.1, and 0x7f000001.  They are
        # ambiguous to urllib/ipaddress and can otherwise disguise a private
        # IPv4 literal as a hostname.  Canonical IPv4 text was handled above;
        # reject every other numeric form instead of resolving it as DNS.
        try:
            socket.inet_aton(hostname)
        except OSError:
            pass
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Non-canonical numeric IPv4 download URLs are not allowed",
            ) from None
    if address is not None and not address.is_global:
        raise HTTPException(
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
        raw_filename = posixpath.basename(urlsplit(url).path)
        filename = unquote(raw_filename)
        if not filename:
            if allow_unresolved:
                return None
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="filename is required when the URL path has no filename",
            )

    filename = _validate_direct_child_name(filename, "filename")
    if SSHManager.archive_type_from_path(filename) is None:
        if allow_unresolved and not (requested_filename and requested_filename.strip()):
            return None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Unsupported archive filename. Supported formats: "
                f"{SSHManager.SUPPORTED_ARCHIVE_FORMATS_LABEL}"
            ),
        )
    return filename


def _parse_github_actions_artifact_url(url: str) -> Optional[Tuple[str, str, int]]:
    """Parse a GitHub Actions artifact web URL without accepting lookalike hosts."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != "github.com":
        return None
    match = GITHUB_ACTIONS_ARTIFACT_URL_RE.fullmatch(parsed.path)
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
    artifact = _parse_github_actions_artifact_url(url)
    if artifact is None:
        raise RuntimeError("Invalid GitHub Actions artifact URL")
    owner, repository, artifact_id = artifact
    api_base = (
        "https://api.github.com/repos/"
        f"{quote(owner, safe='')}/{quote(repository, safe='')}/actions/artifacts/{artifact_id}"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "UpKK-CS2-ServerManager",
    }
    token = github_token.strip() if github_token and github_token.strip() else None
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
        ) as client:
            metadata_response = await client.get(api_base, headers=headers)
            if metadata_response.status_code != 200:
                raise _github_artifact_http_error(
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
                filename = _validate_direct_child_name(filename, "artifact filename")
            except HTTPException as exc:
                raise RuntimeError(f"GitHub artifact name is unsafe: {exc.detail}") from exc
            if SSHManager.archive_type_from_path(filename) != "zip":
                raise RuntimeError("GitHub artifact filename is not a ZIP archive")

            download_response = await client.get(f"{api_base}/zip", headers=headers)
            if download_response.status_code != 302:
                raise _github_artifact_http_error(
                    download_response.status_code, token, metadata=False
                )
            location = download_response.headers.get("location")
            if not location:
                raise RuntimeError("GitHub artifact response did not include a download redirect")
            signed_url = str(download_response.url.join(location))
    except httpx.TimeoutException as exc:
        raise RuntimeError("GitHub artifact request timed out") from exc
    except httpx.RequestError as exc:
        raise RuntimeError("Could not connect to GitHub to resolve the artifact") from exc

    try:
        signed_url = _validate_download_url(signed_url)
    except HTTPException as exc:
        raise RuntimeError(
            f"GitHub returned an invalid artifact download URL: {exc.detail}"
        ) from exc
    return signed_url, filename


def _cleanup_temp_file(path: str) -> None:
    """Remove a temporary file after FileResponse finishes sending it."""
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        logger.warning("Failed to clean up temporary download file: %s", path, exc_info=True)


def _download_headers(filename: str, file_size: Optional[int] = None) -> Dict[str, str]:
    """Build attachment headers with UTF-8 filename support."""
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii") or "download"
    ascii_filename = ascii_filename.replace("\\", "_").replace('"', "_")
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename)}"
        )
    }
    if file_size is not None:
        headers["Content-Length"] = str(file_size)
    return headers


async def _run_download_url_task(
    task_id: str,
    url: str,
    destination_path: str,
    target_path: Optional[str],
    server: Server,
    overwrite: bool,
    github_token: Optional[str],
):
    """Download an archive on the SSH host without retaining its URL in status."""
    ssh_manager: Optional[SSHManager] = None

    async def update_target_path(resolved_target_path: str) -> None:
        async with download_url_tasks_lock:
            task_info = download_url_tasks.get(task_id)
            if task_info is not None:
                task_info["target_path"] = resolved_target_path

    try:
        async with download_url_tasks_lock:
            download_url_tasks[task_id]["status"] = "running"
            download_url_tasks[task_id]["started_at"] = time.time()

        ssh_manager = SSHManager()
        connected, connection_error = await ssh_manager.connect(server)
        if not connected:
            raise RuntimeError(f"Connection failed: {connection_error}")

        is_github_artifact = _parse_github_actions_artifact_url(url) is not None
        download_url = url
        if is_github_artifact:
            download_url, artifact_filename = await _resolve_github_actions_artifact(
                url,
                github_token,
            )
            if target_path is None:
                target_path = remote_join(destination_path, artifact_filename)
                await update_target_path(target_path)

        logger.info(
            "[URL Download] Starting task %s -> %s",
            task_id,
            target_path or destination_path,
        )
        success, error = await ssh_manager.download_url_to_file(
            download_url,
            target_path,
            server,
            overwrite=overwrite,
            destination_path=destination_path,
            resolved_target_callback=update_target_path,
        )

        # GitHub's signed object-storage redirect expires after one minute. If
        # curl reached it too late, fetch a fresh redirect and retry exactly
        # once. Non-transfer failures (unsafe path, conflict, missing curl) do
        # not benefit from another authenticated API request.
        if is_github_artifact and not success and error.startswith("Download failed:"):
            download_url, _ = await _resolve_github_actions_artifact(url, github_token)
            success, error = await ssh_manager.download_url_to_file(
                download_url,
                target_path,
                server,
                overwrite=overwrite,
                destination_path=destination_path,
                resolved_target_callback=update_target_path,
            )

        # The SSH host only ever receives an expiring signed URL, never this
        # credential. Drop the coroutine's local token reference after use.
        github_token = None

        async with download_url_tasks_lock:
            if success:
                download_url_tasks[task_id]["status"] = "completed"
                download_url_tasks[task_id]["message"] = "Archive downloaded successfully"
            else:
                download_url_tasks[task_id]["status"] = "failed"
                download_url_tasks[task_id]["error"] = error
            download_url_tasks[task_id]["completed_at"] = time.time()
    except Exception as exc:
        logger.exception("[URL Download] Task %s failed", task_id)
        async with download_url_tasks_lock:
            if task_id in download_url_tasks:
                download_url_tasks[task_id]["status"] = "failed"
                download_url_tasks[task_id]["error"] = str(exc)
                download_url_tasks[task_id]["completed_at"] = time.time()
    finally:
        if ssh_manager is not None:
            try:
                await ssh_manager.disconnect()
            except Exception:
                logger.warning(
                    "[URL Download] Failed to release SSH connection for task %s",
                    task_id,
                    exc_info=True,
                )
        async with download_url_tasks_lock:
            _download_url_task_refs.pop(task_id, None)


async def _cleanup_old_download_url_tasks():
    """Remove expired URL download task records and completed task references."""
    current_time = time.time()
    tasks_to_remove = []
    async with download_url_tasks_lock:
        for task_id, task_info in download_url_tasks.items():
            completed_at = task_info.get("completed_at")
            created_at = task_info.get("created_at")
            if (
                completed_at
                and current_time - completed_at > EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS
            ):
                tasks_to_remove.append(task_id)
            elif (
                not completed_at
                and created_at
                and current_time - created_at > EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS
            ):
                tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            download_url_tasks.pop(task_id, None)
            _download_url_task_refs.pop(task_id, None)


async def _run_extraction_task(
    task_id: str,
    archive_path: str,
    destination_path: str,
    server: Server,
    overwrite: bool,
    source_folder: Optional[str],
    strip_source_folder: bool,
):
    """Background task to perform archive extraction"""
    ssh_manager: Optional[SSHManager] = None
    try:
        async with extraction_tasks_lock:
            extraction_tasks[task_id]["status"] = "running"
            extraction_tasks[task_id]["started_at"] = time.time()

        logger.info(
            f"[Extraction] Starting extraction task {task_id}: {archive_path} -> {destination_path}"
        )

        # Extract using SSH
        ssh_manager = SSHManager()
        success, error = await ssh_manager.extract_archive(
            archive_path,
            destination_path,
            server,
            overwrite,
            source_folder=source_folder,
            strip_source_folder=strip_source_folder,
        )

        async with extraction_tasks_lock:
            if success:
                extraction_tasks[task_id]["status"] = "completed"
                extraction_tasks[task_id]["message"] = "Archive extracted successfully"
                logger.info(f"[Extraction] Task {task_id} completed successfully")
            else:
                extraction_tasks[task_id]["status"] = "failed"
                extraction_tasks[task_id]["error"] = error
                logger.error(f"[Extraction] Task {task_id} failed: {error}")

            extraction_tasks[task_id]["completed_at"] = time.time()

    except Exception as e:
        logger.exception(f"[Extraction] Task {task_id} encountered an exception")
        async with extraction_tasks_lock:
            extraction_tasks[task_id]["status"] = "failed"
            extraction_tasks[task_id]["error"] = str(e)
            extraction_tasks[task_id]["completed_at"] = time.time()
    finally:
        if ssh_manager is not None:
            try:
                await ssh_manager.disconnect()
            except Exception:
                logger.warning(
                    "[Extraction] Failed to release SSH connection for task %s",
                    task_id,
                    exc_info=True,
                )
        # Clean up task reference
        async with extraction_tasks_lock:
            if task_id in _extraction_task_refs:
                del _extraction_task_refs[task_id]


async def _cleanup_old_extraction_tasks():
    """Clean up extraction tasks older than configured thresholds"""
    current_time = time.time()
    tasks_to_remove = []

    async with extraction_tasks_lock:
        for task_id, task_info in extraction_tasks.items():
            # Remove completed/failed tasks older than threshold
            if task_info.get("completed_at"):
                if (
                    current_time - task_info["completed_at"]
                    > EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS
                ):
                    tasks_to_remove.append(task_id)
            # Remove pending tasks older than threshold (likely abandoned)
            elif task_info.get("created_at"):
                if (
                    current_time - task_info["created_at"]
                    > EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS
                ):
                    tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            del extraction_tasks[task_id]
            # Also clean up task reference if it exists
            if task_id in _extraction_task_refs:
                del _extraction_task_refs[task_id]


# Export private helpers too: endpoint modules are mechanical domain slices.
__all__ = [
    "asyncio",
    "ipaddress",
    "logging",
    "os",
    "posixpath",
    "re",
    "socket",
    "tempfile",
    "time",
    "uuid",
    "Annotated",
    "Any",
    "Dict",
    "List",
    "Optional",
    "Tuple",
    "quote",
    "unquote",
    "urlsplit",
    "anyio",
    "httpx",
    "jwt",
    "APIRouter",
    "Depends",
    "File",
    "Header",
    "HTTPException",
    "Query",
    "UploadFile",
    "status",
    "FileResponse",
    "StreamingResponse",
    "InvalidTokenError",
    "AsyncSession",
    "SQLModel",
    "select",
    "BackgroundTask",
    "DatabaseSession",
    "require_server_access",
    "Server",
    "User",
    "get_current_active_user",
    "get_db",
    "settings",
    "SSHManager",
    "KeyedConcurrencyLimiter",
    "get_effective_github_token",
    "file_task_registry",
    "logger",
    "EXTRACTION_TASK_COMPLETED_CLEANUP_SECONDS",
    "EXTRACTION_TASK_ABANDONED_CLEANUP_SECONDS",
    "STREAMING_DOWNLOAD_THRESHOLD_BYTES",
    "DOWNLOAD_TICKET_TTL_SECONDS",
    "REMOTE_NAME_MAX_BYTES",
    "DOWNLOAD_URL_MAX_LENGTH",
    "MAX_UPLOAD_BYTES",
    "GITHUB_API_VERSION",
    "GITHUB_ACTIONS_ARTIFACT_URL_RE",
    "extraction_tasks",
    "_extraction_task_refs",
    "extraction_tasks_lock",
    "download_url_tasks",
    "_download_url_task_refs",
    "download_url_tasks_lock",
    "download_tickets",
    "download_tickets_lock",
    "_file_task_limiter",
    "_run_bounded_file_task",
    "shutdown_background_tasks",
    "FileInfo",
    "DirectoryListResponse",
    "FileContentRequest",
    "CreateDirectoryRequest",
    "DownloadTicketRequest",
    "DeleteRequest",
    "RenameRequest",
    "CopyPathsRequest",
    "ExtractArchiveRequest",
    "DownloadUrlRequest",
    "InspectArchiveRequest",
    "get_server_for_user",
    "_create_download_ticket",
    "_consume_download_ticket",
    "get_current_active_user_for_download",
    "DownloadUser",
    "is_path_safe",
    "remote_join",
    "safe_relative_upload_path",
    "_validate_direct_child_name",
    "_normalize_source_folder",
    "_HUB_FILE_STATUS",
    "resolve_extract_paths",
    "file_task_payload_from_hub",
    "_validate_download_url",
    "_download_archive_filename",
    "_parse_github_actions_artifact_url",
    "_resolve_github_actions_artifact",
    "_cleanup_temp_file",
    "_download_headers",
    "_run_download_url_task",
    "_cleanup_old_download_url_tasks",
    "_run_extraction_task",
    "_cleanup_old_extraction_tasks",
]
