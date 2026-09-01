"""Versioned plugin-configuration workspace for the Next.js console."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.routes import plugin_configs as legacy
from services.audit_log_service import record_audit_event

from .schemas import (
    PluginConfigBrowseItemView,
    PluginConfigBrowseView,
    PluginConfigFieldView,
    PluginConfigFileView,
    PluginConfigSaveRequest,
    PluginConfigSourceCreateRequest,
    PluginConfigSourceDeleteResult,
    PluginConfigSourcesView,
    PluginConfigSourceView,
)

router = APIRouter(
    prefix="/api/v1/servers/{server_id}/plugin-configs",
    tags=["v1-plugin-configs"],
)


def _source(raw: dict[str, Any]) -> PluginConfigSourceView:
    kind = raw.get("type")
    source_id = raw.get("id")
    return PluginConfigSourceView(
        id=int(source_id) if source_id is not None else None,
        path=str(raw.get("path") or ""),
        absolute_path=str(raw.get("absolute_path") or ""),
        name=str(raw.get("name") or ""),
        type="directory" if kind == "directory" else "file",
        is_default=bool(raw.get("is_default", False)),
        persisted=bool(raw.get("persisted", False)),
    )


def _field(raw: dict[str, Any]) -> PluginConfigFieldView:
    value = raw.get("value")
    field_value: bool | int | float | str | None
    if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
        field_value = value
    else:
        field_value = str(value)
    return PluginConfigFieldView(
        id=str(raw.get("id") or ""),
        key=str(raw.get("key") or ""),
        group=str(raw.get("group") or ""),
        kind=str(raw.get("kind") or "string"),
        value=field_value,
        line=int(raw.get("line") or 0),
        comment=str(raw.get("comment") or ""),
    )


def _file(raw: dict[str, Any]) -> PluginConfigFileView:
    fields = [_field(item) for item in raw.get("fields") or [] if isinstance(item, dict)]
    return PluginConfigFileView(
        path=str(raw.get("path") or ""),
        name=str(raw.get("name") or ""),
        format=str(raw.get("format") or "raw"),
        revision=str(raw.get("revision") or ""),
        content=str(raw.get("content") or ""),
        visual_supported=bool(raw.get("visual_supported", False)),
        parse_error=str(raw["parse_error"]) if raw.get("parse_error") else None,
        fields=fields,
        message=str(raw["message"]) if raw.get("message") else None,
    )


def _browse_item(raw: dict[str, Any]) -> PluginConfigBrowseItemView:
    kind = raw.get("type")
    item_type: Literal["file", "directory", "symlink"]
    if kind == "directory":
        item_type = "directory"
    elif kind == "symlink":
        item_type = "symlink"
    else:
        item_type = "file"
    return PluginConfigBrowseItemView(
        name=str(raw.get("name") or ""),
        path=str(raw["path"]) if raw.get("path") else None,
        type=item_type,
        selectable=bool(raw.get("selectable", False)),
        size=int(raw.get("size") or 0),
    )


def _sources_view(
    server_id: int,
    game_directory: str,
    sources: list[dict[str, Any]],
) -> PluginConfigSourcesView:
    return PluginConfigSourcesView(
        server_id=server_id,
        game_directory=game_directory,
        sources=[_source(item) for item in sources],
    )


@router.get("/sources", response_model=PluginConfigSourcesView)
async def list_sources(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginConfigSourcesView:
    """List persisted sources. Does not scan the remote host."""
    payload = await legacy.list_sources(server_id, db, current_user)
    return _sources_view(
        server_id,
        str(payload.get("game_directory") or ""),
        [item for item in payload.get("sources") or [] if isinstance(item, dict)],
    )


@router.post(
    "/sources",
    response_model=PluginConfigSourceView,
    status_code=status.HTTP_201_CREATED,
)
async def create_source(
    server_id: int,
    body: PluginConfigSourceCreateRequest,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginConfigSourceView:
    payload = await legacy.create_source(
        server_id,
        legacy.SourceCreateRequest(path=body.path),
        db,
        current_user,
    )
    return _source(payload)


@router.delete(
    "/sources/{source_id}",
    response_model=PluginConfigSourceDeleteResult,
)
async def delete_source(
    server_id: int,
    source_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginConfigSourceDeleteResult:
    payload = await legacy.delete_source(server_id, source_id, db, current_user)
    return PluginConfigSourceDeleteResult(success=bool(payload.get("success", True)))


@router.post("/sources/restore-default", response_model=PluginConfigSourcesView)
async def restore_default_sources(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginConfigSourcesView:
    server = await require_server_access(db, server_id, current_user)
    payload = await legacy.restore_default_source(server_id, db, current_user)
    raw_sources = payload.get("sources")
    sources = (
        [item for item in raw_sources if isinstance(item, dict)]
        if isinstance(raw_sources, list)
        else [payload]
    )
    return _sources_view(server_id, server.game_directory, sources)


@router.get("/browse", response_model=PluginConfigBrowseView)
async def browse_source_path(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    path: str = Query("."),
) -> PluginConfigBrowseView:
    payload = await legacy.browse_source_path(server_id, path, db, current_user)
    return PluginConfigBrowseView(
        path=str(payload.get("path") or "."),
        items=[_browse_item(item) for item in payload.get("items") or [] if isinstance(item, dict)],
    )


@router.post("/sources/{source_id}/scan", response_model=None)
async def scan_source_files(
    server_id: int,
    source_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> StreamingResponse:
    """NDJSON scan stream. Same events as the legacy tab (start/progress/file/complete)."""
    return await legacy.load_source_files(server_id, source_id, db, current_user)


@router.get("/sources/{source_id}/file", response_model=PluginConfigFileView)
async def get_config_file(
    server_id: int,
    source_id: int,
    path: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginConfigFileView:
    payload = await legacy.get_config_file(server_id, source_id, path, db, current_user)
    return _file(payload)


@router.put("/sources/{source_id}/file", response_model=PluginConfigFileView)
async def save_config_file(
    server_id: int,
    source_id: int,
    body: PluginConfigSaveRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginConfigFileView:
    payload = await legacy.save_config_file(
        server_id,
        source_id,
        legacy.ConfigSaveRequest(
            path=body.path,
            expected_revision=body.expected_revision,
            mode=body.mode,
            changes=[legacy.ConfigChange(id=item.id, value=item.value) for item in body.changes],
            content=body.content,
        ),
        db,
        current_user,
    )
    await record_audit_event(
        category="config",
        action="config.plugin_file.update",
        status="success",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"path": body.path, "source_id": source_id, "mode": body.mode},
    )
    return _file(payload)
