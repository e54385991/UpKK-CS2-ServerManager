# ruff: noqa: F401
"""
File manager routes for server file operations
"""

import asyncio
import ipaddress
import json
import logging
import os
import posixpath
import re
import secrets
import socket
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

import anyio
import httpx
import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from jwt import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select
from starlette.background import BackgroundTask

from api.dependencies import (
    get_ssh_manager,
    require_server_access,
    resolve_maintenance_lock_service,
)
from api.http_resource import ApplicationHTTP as _ApplicationHTTP
from cs2_manager.core import ErrorResponse
from cs2_manager.infrastructure.credentials import hash_token
from modules import Server, User, get_current_active_user, get_db, settings
from modules.database import async_session_maker
from modules.http_helper import http_helper
from services import SSHManager
from services.github_credentials import get_effective_github_token
from services.maintenance_lock import MaintenanceLockService, maintenance_lock_service
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

_file_task_semaphore = asyncio.Semaphore(4)

_file_user_semaphores: dict[int, asyncio.Semaphore] = {}

SSHManagerFactory = Callable[[], SSHManager]


def _coerce_maintenance_lock_service(
    candidate: object,
) -> MaintenanceLockService:
    """Keep direct facade calls compatible while HTTP dependency lookup is strict."""
    if callable(getattr(candidate, "get", None)):
        return candidate  # type: ignore[return-value]
    return maintenance_lock_service


def _coerce_ssh_manager(
    candidate: object,
    constructor: Callable[[], SSHManager],
) -> SSHManager:
    """Keep direct facade calls compatible while HTTP dependency lookup is strict."""
    if callable(getattr(candidate, "disconnect", None)):
        return candidate  # type: ignore[return-value]
    return constructor()


def _bound_ssh_manager_factory(
    manager: SSHManager,
    constructor: Callable[..., SSHManager],
) -> SSHManagerFactory:
    """Create task-local managers bound to the request application's resources."""
    connection_pool = getattr(manager, "connection_pool", None)
    http_resource = getattr(manager, "http_resource", None)
    if connection_pool is None or http_resource is None:
        # Compatibility callers can continue patching the facade constructor.
        # Request dependencies always supply both explicit application resources.
        return constructor
    return lambda: constructor(
        connection_pool=connection_pool,
        http_resource=http_resource,
    )


async def _run_bounded_file_task(
    user_id: int,
    callback: Callable[..., Awaitable[None]],
    *args: Any,
) -> None:
    """Run file work with bounded fan-out without closing over request objects."""
    user_semaphore = _file_user_semaphores.setdefault(user_id, asyncio.Semaphore(2))
    async with user_semaphore:
        async with _file_task_semaphore:
            await callback(*args)


def _background_resources(request: Request):
    """Resolve lifespan-owned task and database resources before a request ends."""
    state = request.app.state
    container = getattr(state, "container", None)
    database = getattr(container, "database", None)
    session_factory = getattr(database, "session_factory", async_session_maker)
    return session_factory, getattr(state, "task_supervisor", None)


def _spawn_file_task(request: Request, coroutine, *, name: str) -> asyncio.Task:
    """Prefer the application supervisor and retain the legacy test fallback."""
    _session_factory, supervisor = _background_resources(request)
    if supervisor is not None:
        return supervisor.create(coroutine, name=name)
    return file_task_registry.add(asyncio.create_task(coroutine, name=name))


async def _load_server_snapshot(
    session_factory,
    server_id: int,
    user_id: int,
    user_is_admin: bool,
) -> Server:
    """Load and copy a server in a short session before any remote I/O."""
    async with session_factory() as session:
        server = await session.get(Server, server_id)
        if server is None or (not user_is_admin and server.user_id != user_id):
            raise RuntimeError("Server is no longer available")
        # SQLModel validation creates a transient instance. This guarantees the
        # slow SSH phase cannot lazy-load through, or retain, the request DB
        # session even when an injected factory uses expire_on_commit=True.
        snapshot = Server.model_validate(server, from_attributes=True)
        await session.commit()
    return snapshot


async def _disconnect_ssh_manager(ssh_manager: SSHManager, *, operation: str) -> None:
    """Best-effort release which never hides the operation's primary result."""
    try:
        await ssh_manager.disconnect()
    except Exception:
        logger.warning("Failed to release SSH connection after %s", operation, exc_info=True)


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


class FileContentResponse(SQLModel):
    """Editable text-file content response."""

    path: str
    content: str


class FileActionResponse(SQLModel):
    """Success envelope for file operations without additional data."""

    success: bool
    message: str


class FileUploadResponse(FileActionResponse):
    """Successful upload response."""

    path: str
    filename: str


class DownloadTicketResponse(SQLModel):
    """Short-lived one-time download credential."""

    ticket: str
    expires_in: int


class DirectoryCreatedResponse(FileActionResponse):
    """Successful directory creation response."""

    path: str


class FileRenamedResponse(FileActionResponse):
    """Successful rename response."""

    new_path: str


class ArchiveInspectionResponse(SQLModel):
    """Safe, selectable metadata discovered in a remote archive."""

    archive_type: str
    folders: List[str]
    entry_count: int


class ExtractionStartedResponse(FileActionResponse):
    """Accepted extraction task response."""

    task_id: str
    status: str
    destination: str


class ExtractionStatusResponse(SQLModel):
    """Current state of an extraction task."""

    task_id: str
    status: str
    archive_path: str
    destination_path: str
    source_folder: Optional[str] = None
    strip_source_folder: bool
    message: Optional[str] = None
    error: Optional[str] = None
    elapsed_seconds: Optional[float] = None


class DownloadUrlStartedResponse(SQLModel):
    """Accepted remote URL download task response."""

    success: bool
    task_id: str
    status: str
    target_path: Optional[str] = None


class DownloadUrlStatusResponse(SQLModel):
    """Current state of a remote URL download task."""

    task_id: str
    status: str
    target_path: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    elapsed_seconds: Optional[float] = None


def file_error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build explicit legacy ``{"detail": ...}`` error declarations."""
    return {status_code: {"model": ErrorResponse} for status_code in status_codes}


class FileContentRequest(SQLModel):
    """File content update request"""

    content: str


class CreateDirectoryRequest(SQLModel):
    """Create directory request"""

    name: str


class DownloadTicketRequest(SQLModel):
    """Create a short-lived browser download ticket"""

    path: str


class DownloadTicketStoreUnavailable(RuntimeError):
    """Raised when Redis cannot safely issue or consume a one-time ticket."""


class DeleteRequest(SQLModel):
    """Delete file/directory request"""

    path: str


class RenameRequest(SQLModel):
    """Rename file/directory request"""

    old_name: str
    new_name: str


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
    """Authorize and detach a server before any slow remote operation."""
    server = await require_server_access(
        db,
        server_id,
        current_user,
        commit=False,
    )
    snapshot = Server.model_validate(server, from_attributes=True)
    await db.commit()
    return snapshot


def _download_ticket_resources(request: Request, operation: str) -> tuple[Any, str]:
    """Resolve only the Redis and hash key owned by the request's application."""
    try:
        container = request.app.state.container
        redis_resource = container.redis
        redis_client = redis_resource.client
        ticket_settings = container.settings
        token_key = ticket_settings.TOKEN_HASH_KEY or ticket_settings.SECRET_KEY
        redis_operation = getattr(redis_client, operation)
    except Exception as exc:
        logger.error("Download ticket application resources are unavailable: %s", exc)
        raise DownloadTicketStoreUnavailable(
            "Download ticket service is temporarily unavailable"
        ) from exc

    if not token_key or not callable(redis_operation):
        logger.error("Download ticket application resources are incomplete")
        raise DownloadTicketStoreUnavailable("Download ticket service is temporarily unavailable")
    return redis_client, token_key


async def _create_download_ticket(
    request: Request,
    user_id: int,
    server_id: int,
    path: str,
) -> str:
    """Create a Redis-backed, hashed, one-time ticket bound to exact inputs."""
    payload = json.dumps(
        {"user_id": user_id, "server_id": server_id, "path": path},
        separators=(",", ":"),
    )
    redis_client, token_key = _download_ticket_resources(request, "set")
    try:
        async with asyncio.timeout(1.0):
            for _attempt in range(3):
                ticket = secrets.token_urlsafe(32)
                digest = hash_token(ticket, token_key)
                created = await redis_client.set(
                    f"download_ticket:{digest}",
                    payload,
                    ex=DOWNLOAD_TICKET_TTL_SECONDS,
                    nx=True,
                )
                if created:
                    return ticket
    except Exception as exc:
        logger.error("Unable to create download ticket in coordination storage: %s", exc)
        raise DownloadTicketStoreUnavailable(
            "Download ticket service is temporarily unavailable"
        ) from exc
    raise DownloadTicketStoreUnavailable("Unable to allocate a unique download ticket")


async def _consume_download_ticket(
    request: Request,
    ticket: str,
    server_id: int,
    path: str,
) -> Optional[int]:
    """Atomically consume and validate a Redis-backed one-time ticket."""
    if not ticket or len(ticket) > 256:
        return None
    redis_client, token_key = _download_ticket_resources(request, "eval")
    digest = hash_token(ticket, token_key)
    consume_script = """
    local value = redis.call('GET', KEYS[1])
    if value then redis.call('DEL', KEYS[1]) end
    return value
    """
    try:
        async with asyncio.timeout(1.0):
            encoded = await redis_client.eval(
                consume_script,
                1,
                f"download_ticket:{digest}",
            )
    except Exception as exc:
        logger.error("Unable to consume download ticket from coordination storage: %s", exc)
        raise DownloadTicketStoreUnavailable(
            "Download ticket service is temporarily unavailable"
        ) from exc
    if not encoded:
        return None
    if isinstance(encoded, bytes):
        encoded = encoded.decode("utf-8")
    try:
        ticket_info = json.loads(encoded)
    except TypeError, json.JSONDecodeError:
        logger.error("Discarded malformed download ticket payload")
        return None
    if ticket_info.get("server_id") != server_id or ticket_info.get("path") != path:
        return None
    try:
        return int(ticket_info["user_id"])
    except KeyError, TypeError, ValueError:
        return None


async def get_current_active_user_for_download(
    request: Request,
    server_id: int,
    path: str,
    ticket: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate downloads with a one-time ticket or a normal bearer token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_id: Optional[int] = None

    if ticket:
        try:
            user_id = await _consume_download_ticket(request, ticket, server_id, path)
        except DownloadTicketStoreUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
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
                "Unsupported archive filename. Supported formats: .zip, .7z, .tar, "
                ".tar.gz, .tgz, .tar.bz2, .tbz2, .tar.xz, .txz, .gz, .bz2"
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


async def _resolve_github_actions_artifact(
    url: str,
    github_token: Optional[str],
    *,
    http_resource: _ApplicationHTTP | None = None,
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

    # Direct Python users of the compatibility facade retain the legacy
    # process-global default. FastAPI request paths always pass the adapter
    # owned by their application container explicitly.
    outbound_http = http_resource or http_helper
    try:
        async with outbound_http.borrow_client() as client:
            request_timeout = httpx.Timeout(20.0, connect=10.0)
            metadata_response = await client.get(
                api_base,
                headers=headers,
                timeout=request_timeout,
                follow_redirects=False,
            )
            if metadata_response.status_code != 200:
                if metadata_response.status_code in (401, 403):
                    if token:
                        raise RuntimeError(
                            "GitHub token is invalid or lacks Actions artifact read access"
                        )
                    raise RuntimeError(
                        "GitHub Actions artifact download requires a GitHub token with Actions read access; configure it in your profile"
                    )
                if metadata_response.status_code == 404:
                    raise RuntimeError(
                        "GitHub Actions artifact was not found or is not accessible with the configured token"
                    )
                raise RuntimeError(
                    f"GitHub artifact metadata request failed (HTTP {metadata_response.status_code})"
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

            download_response = await client.get(
                f"{api_base}/zip",
                headers=headers,
                timeout=request_timeout,
                follow_redirects=False,
            )
            if download_response.status_code != 302:
                if download_response.status_code in (401, 403):
                    if token:
                        raise RuntimeError(
                            "GitHub token is invalid or lacks Actions artifact read access"
                        )
                    raise RuntimeError(
                        "GitHub Actions artifact download requires a GitHub token with Actions read access; configure it in your profile"
                    )
                if download_response.status_code in (404, 410):
                    raise RuntimeError("GitHub Actions artifact is unavailable or has expired")
                raise RuntimeError(
                    f"GitHub artifact download request failed (HTTP {download_response.status_code})"
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
    server_id: int,
    user_id: int,
    user_is_admin: bool,
    overwrite: bool,
    github_token: Optional[str],
    session_factory=async_session_maker,
    lock_service: MaintenanceLockService | None = None,
    http_resource: _ApplicationHTTP | None = None,
    ssh_manager_factory: SSHManagerFactory | None = None,
):
    """Download on the SSH host after loading a detached server snapshot."""
    lock_service = _coerce_maintenance_lock_service(lock_service)
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

        async with lock_service.get(
            server_id,
            operation="file_download_url",
            wait=False,
            ttl=7200,
        ):
            server = await _load_server_snapshot(
                session_factory,
                server_id,
                user_id,
                user_is_admin,
            )
            manager_factory = ssh_manager_factory or SSHManager
            ssh_manager = manager_factory()
            try:
                connected, connection_error = await ssh_manager.connect(server)
                if not connected:
                    raise RuntimeError(f"Connection failed: {connection_error}")

                is_github_artifact = _parse_github_actions_artifact_url(url) is not None
                download_url = url
                if is_github_artifact:
                    download_url, artifact_filename = await _resolve_github_actions_artifact(
                        url,
                        github_token,
                        http_resource=http_resource,
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
                    download_url, _ = await _resolve_github_actions_artifact(
                        url,
                        github_token,
                        http_resource=http_resource,
                    )
                    success, error = await ssh_manager.download_url_to_file(
                        download_url,
                        target_path,
                        server,
                        overwrite=overwrite,
                        destination_path=destination_path,
                        resolved_target_callback=update_target_path,
                    )
            finally:
                await _disconnect_ssh_manager(
                    ssh_manager,
                    operation=f"URL download task {task_id}",
                )
                ssh_manager = None

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
    except asyncio.CancelledError:
        async with download_url_tasks_lock:
            if task_id in download_url_tasks:
                download_url_tasks[task_id]["status"] = "failed"
                download_url_tasks[task_id]["error"] = "Download task was cancelled"
                download_url_tasks[task_id]["completed_at"] = time.time()
        raise
    except Exception as exc:
        logger.exception("[URL Download] Task %s failed", task_id)
        async with download_url_tasks_lock:
            if task_id in download_url_tasks:
                download_url_tasks[task_id]["status"] = "failed"
                download_url_tasks[task_id]["error"] = str(exc)
                download_url_tasks[task_id]["completed_at"] = time.time()
    finally:
        if ssh_manager is not None:
            await _disconnect_ssh_manager(ssh_manager, operation=f"URL download task {task_id}")
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
    server_id: int,
    user_id: int,
    user_is_admin: bool,
    overwrite: bool,
    source_folder: Optional[str],
    strip_source_folder: bool,
    session_factory=async_session_maker,
    lock_service: MaintenanceLockService | None = None,
    ssh_manager_factory: SSHManagerFactory | None = None,
):
    """Extract an archive using a detached server loaded inside the task."""
    lock_service = _coerce_maintenance_lock_service(lock_service)
    ssh_manager: Optional[SSHManager] = None
    try:
        async with extraction_tasks_lock:
            extraction_tasks[task_id]["status"] = "running"
            extraction_tasks[task_id]["started_at"] = time.time()

        logger.info(
            f"[Extraction] Starting extraction task {task_id}: {archive_path} -> {destination_path}"
        )

        async with lock_service.get(
            server_id,
            operation="file_extract",
            wait=False,
            ttl=7200,
        ):
            server = await _load_server_snapshot(
                session_factory,
                server_id,
                user_id,
                user_is_admin,
            )
            manager_factory = ssh_manager_factory or SSHManager
            ssh_manager = manager_factory()
            try:
                success, error = await ssh_manager.extract_archive(
                    archive_path,
                    destination_path,
                    server,
                    overwrite,
                    source_folder=source_folder,
                    strip_source_folder=strip_source_folder,
                )
            finally:
                await _disconnect_ssh_manager(
                    ssh_manager,
                    operation=f"extraction task {task_id}",
                )
                ssh_manager = None

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

    except asyncio.CancelledError:
        async with extraction_tasks_lock:
            if task_id in extraction_tasks:
                extraction_tasks[task_id]["status"] = "failed"
                extraction_tasks[task_id]["error"] = "Extraction task was cancelled"
                extraction_tasks[task_id]["completed_at"] = time.time()
        raise
    except Exception as e:
        logger.exception(f"[Extraction] Task {task_id} encountered an exception")
        async with extraction_tasks_lock:
            if task_id in extraction_tasks:
                extraction_tasks[task_id]["status"] = "failed"
                extraction_tasks[task_id]["error"] = str(e)
                extraction_tasks[task_id]["completed_at"] = time.time()
    finally:
        if ssh_manager is not None:
            await _disconnect_ssh_manager(ssh_manager, operation=f"extraction task {task_id}")
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
__all__ = [name for name in globals() if not name.startswith("__")]
