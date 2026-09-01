"""Versioned portable server-configuration import/export.

The console uses ``/api/v1/server-configs`` instead of the legacy
``GET/POST /servers/export|/import`` HTML-adjacent routes. The bundle format
is unchanged so existing backups remain importable. Secrets are omitted unless
the caller explicitly sets ``include_secrets``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from api.dependencies import ActiveUser, DatabaseSession
from api.routes.servers import transfer as legacy
from modules.schemas.servers import (
    ServerConfigExport,
    ServerConfigImportRequest,
    ServerConfigImportResponse,
)

from .schemas import ServerConfigImportRequest as ServerConfigImportBody

router = APIRouter(prefix="/api/v1/server-configs", tags=["v1-server-configs"])


@router.get("", response_model=ServerConfigExport)
async def export_server_configs(
    db: DatabaseSession,
    current_user: ActiveUser,
    server_ids: Optional[list[int]] = Query(default=None, max_length=100),
    include_secrets: bool = Query(
        default=False,
        description=(
            "Include SSH, game, Steam, and Discord credentials. Default is a redacted copy."
        ),
    ),
) -> ServerConfigExport:
    """Return a portable JSON bundle for the selected (or all) servers."""
    return await legacy.collect_export_bundle(db, current_user, server_ids, include_secrets)


@router.post("", response_model=ServerConfigImportResponse)
async def import_server_configs(
    body: ServerConfigImportBody,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> ServerConfigImportResponse:
    """Import a configuration bundle into the current user's server list."""
    return await legacy.import_server_configs(
        ServerConfigImportRequest.model_validate(body.model_dump()),
        db,
        current_user,
        request,
    )
