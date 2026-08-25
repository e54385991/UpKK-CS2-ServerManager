"""Portable server configuration import and export endpoints."""

from __future__ import annotations

import hashlib
import json
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.dependencies import ActiveUser, DatabaseSession
from modules import (
    DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS,
    AuthType,
    PluginConfigSource,
    Server,
    ServerAgentPolicy,
    User,
    generate_api_key,
    get_current_time,
)
from modules.schemas.servers import (
    ServerConfigExport,
    ServerConfigImportRequest,
    ServerConfigImportResponse,
    ServerConfigImportResult,
)
from services import redis_manager
from services.server_config_transfer import config_entry_values, server_to_config_entry

from .common import get_server_with_permission

router = APIRouter(prefix="/servers", tags=["servers"])


async def _find_conflicts(
    db: AsyncSession,
    user_id: int,
    *,
    name: str,
    host: str,
    game_directory: str,
) -> tuple[Optional[Server], Optional[Server]]:
    """Find name and host/directory conflicts without crossing user boundaries."""
    name_result = await db.execute(
        select(Server).where(Server.user_id == user_id, Server.name == name)
    )
    path_result = await db.execute(
        select(Server).where(
            Server.user_id == user_id,
            Server.host == host,
            Server.game_directory == game_directory,
        )
    )
    return name_result.scalar_one_or_none(), path_result.scalar_one_or_none()


async def _next_available_name(
    db: AsyncSession,
    user_id: int,
    name: str,
) -> str:
    """Generate a deterministic name for a rename import."""
    suffix = 2
    candidate = f"{name} ({suffix})"
    while await Server.get_by_name_and_user(db, candidate, user_id):
        suffix += 1
        candidate = f"{name} ({suffix})"
    return candidate


async def _resolve_export_servers(
    db: AsyncSession,
    current_user: User,
    server_ids: Optional[list[int]],
) -> list[Server]:
    """Resolve selected servers through the normal ownership checks."""
    if server_ids is None:
        return await Server.get_all_by_user(db, current_user.id)

    unique_ids = list(dict.fromkeys(server_ids))
    if not unique_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one server must be selected for export",
        )
    servers = []
    for server_id in unique_ids:
        servers.append(await get_server_with_permission(server_id, current_user, db))
    return servers


@router.get("/export", name="export_server_configs")
async def export_server_configs(
    server_ids: Optional[list[int]] = Query(default=None, max_length=100),
    include_secrets: bool = Query(
        default=False,
        description="Include SSH, game, Steam, and Discord credentials in the downloaded bundle",
    ),
    db: DatabaseSession = None,
    current_user: ActiveUser = None,
):
    """Download one or more portable server configuration entries."""
    servers = await _resolve_export_servers(db, current_user, server_ids)
    if not servers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No servers to export")

    bundle = ServerConfigExport(
        exported_at=get_current_time(),
        include_secrets=include_secrets,
        servers=[
            server_to_config_entry(server, include_secrets=include_secrets) for server in servers
        ],
    )
    content = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    timestamp = get_current_time().strftime("%Y%m%d-%H%M%S")
    filename = f"cs2-server-config-{timestamp}.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={quote(filename)}",
        },
    )


@router.post(
    "/import",
    response_model=ServerConfigImportResponse,
    status_code=status.HTTP_200_OK,
    name="import_server_configs",
)
async def import_server_configs(
    request: ServerConfigImportRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
):
    """Import a configuration bundle into the current user's server list."""
    results: list[ServerConfigImportResult] = []
    imported = updated = skipped = failed = 0
    changed_server_ids: list[int] = []

    for index, entry in enumerate(request.servers, start=1):
        name_conflict, path_conflict = await _find_conflicts(
            db,
            current_user.id,
            name=entry.name,
            host=entry.host,
            game_directory=entry.game_directory,
        )
        conflicts = [server for server in (name_conflict, path_conflict) if server is not None]
        conflict_ids = {server.id for server in conflicts}

        if len(conflict_ids) > 1:
            failed += 1
            results.append(
                ServerConfigImportResult(
                    index=index,
                    name=entry.name,
                    action="failed",
                    message="The name and host/directory conflicts refer to different servers",
                )
            )
            continue

        existing = conflicts[0] if conflicts else None
        if existing and request.conflict_strategy == "skip":
            skipped += 1
            results.append(
                ServerConfigImportResult(
                    index=index,
                    name=entry.name,
                    action="skipped",
                    server_id=existing.id,
                    message="A server with the same name or host/directory already exists",
                )
            )
            continue

        if existing and request.conflict_strategy == "update":
            values = config_entry_values(entry, preserve_redacted=True)
            existing.sqlmodel_update(values)
            changed_server_ids.append(existing.id)
            updated += 1
            results.append(
                ServerConfigImportResult(
                    index=index,
                    name=entry.name,
                    action="updated",
                    server_id=existing.id,
                )
            )
            continue

        if existing and request.conflict_strategy == "rename":
            if path_conflict is not None:
                skipped += 1
                results.append(
                    ServerConfigImportResult(
                        index=index,
                        name=entry.name,
                        action="skipped",
                        server_id=path_conflict.id,
                        message="The host and game directory already belong to another server",
                    )
                )
                continue
            entry_values = config_entry_values(entry)
            entry_values["name"] = await _next_available_name(db, current_user.id, entry.name)
        else:
            entry_values = config_entry_values(entry)

        entry_values["user_id"] = current_user.id
        entry_values["api_key"] = generate_api_key()
        entry_values["auth_type"] = entry_values.get("auth_type", AuthType.PASSWORD)
        server = Server(**entry_values)
        db.add(server)
        await db.flush()
        db.add(ServerAgentPolicy(server_id=server.id))

        for default_path in DEFAULT_PLUGIN_CONFIG_SOURCE_PATHS:
            db.add(
                PluginConfigSource(
                    server_id=server.id,
                    relative_path=default_path,
                    path_hash=hashlib.sha256(default_path.encode("utf-8")).hexdigest(),
                    source_type="directory",
                    is_default=True,
                    is_enabled=True,
                )
            )

        imported += 1
        results.append(
            ServerConfigImportResult(
                index=index,
                name=server.name,
                action="imported",
                server_id=server.id,
            )
        )

    await db.commit()
    for server_id in changed_server_ids:
        await redis_manager.clear_server_cache(server_id)

    return ServerConfigImportResponse(
        total=len(request.servers),
        imported=imported,
        updated=updated,
        skipped=skipped,
        failed=failed,
        results=results,
    )
