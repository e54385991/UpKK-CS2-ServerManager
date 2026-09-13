# ruff: noqa: F401
"""
File manager routes for server file operations
"""

from __future__ import annotations

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
from api.routes._compat import install_patch_compatibility
from modules import Server, User, get_current_active_user, get_db, settings
from services import SSHManager
from services.concurrency_limiter import KeyedConcurrencyLimiter
from services.github_credentials import get_effective_github_token
from services.task_registry import file_task_registry

from . import access as _access
from . import github as _github
from . import models as _models
from . import paths as _paths
from . import tasks as _tasks
from .access import (
    _consume_download_ticket,
    _create_download_ticket,
    get_current_active_user_for_download,
    get_server_for_user,
)
from .github import (
    _download_archive_filename,
    _github_artifact_http_error,
    _parse_github_actions_artifact_url,
    _resolve_github_actions_artifact,
    _validate_download_url,
)
from .models import (
    CopyPathsRequest,
    CreateDirectoryRequest,
    DeleteRequest,
    DirectoryListResponse,
    DownloadTicketRequest,
    DownloadUrlRequest,
    ExtractArchiveRequest,
    FileContentRequest,
    FileInfo,
    InspectArchiveRequest,
    RenameRequest,
)
from .paths import (
    _HUB_FILE_STATUS,
    _normalize_source_folder,
    _validate_direct_child_name,
    file_task_payload_from_hub,
    is_path_safe,
    remote_join,
    resolve_extract_paths,
    safe_relative_upload_path,
)
from .tasks import (
    _cleanup_old_download_url_tasks,
    _cleanup_old_extraction_tasks,
    _cleanup_temp_file,
    _download_headers,
    _run_bounded_file_task,
    _run_download_url_task,
    _run_extraction_task,
    shutdown_background_tasks,
)

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

DownloadUser = Annotated[User, Depends(get_current_active_user_for_download)]

install_patch_compatibility(__name__, (_access, _github, _models, _paths, _tasks))

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

_PATCHABLE = (
    SSHManager,
    time,
    jwt,
    httpx,
    uuid,
    settings,
    logger,
    _consume_download_ticket,
    _run_download_url_task,
    _parse_github_actions_artifact_url,
    _resolve_github_actions_artifact,
    _github_artifact_http_error,
    get_server_for_user,
    file_task_registry,
    get_effective_github_token,
)
