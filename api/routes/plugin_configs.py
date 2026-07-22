"""Authenticated APIs for generic per-server plugin configuration editing."""

from __future__ import annotations

import json
import posixpath
from typing import Any, Literal, Optional

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.routes.servers import get_server_with_permission
from modules import (
    DEFAULT_PLUGIN_CONFIG_SOURCE_PATH,
    PluginConfigSource,
    User,
    get_current_active_user,
    get_db,
)
from services.maintenance_lock import maintenance_lock_service
from services.plugin_config_service import (
    MAX_CONFIG_BYTES,
    SUPPORTED_DIRECTORY_EXTENSIONS,
    PluginConfigError,
    apply_visual_changes,
    atomic_write_text_file,
    browse_directory,
    content_revision,
    inspect_source,
    iter_source_scan,
    normalize_relative_path,
    parse_config,
    path_hash,
    read_text_file,
    validate_raw_content,
)
from services.ssh_manager import SSHManager

router = APIRouter(
    prefix="/servers/{server_id}/plugin-configs",
    tags=["plugin-configs"],
)


class SourceCreateRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1500)


class ConfigChange(BaseModel):
    id: str = Field(min_length=1, max_length=1500)
    value: Any


class ConfigSaveRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1500)
    expected_revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    mode: Literal["visual", "raw"]
    changes: list[ConfigChange] = Field(default_factory=list, max_length=5000)
    content: Optional[str] = Field(default=None, max_length=MAX_CONFIG_BYTES)


def _source_payload(source: PluginConfigSource, game_directory: str) -> dict[str, Any]:
    return {
        "id": source.id,
        "path": source.relative_path,
        "absolute_path": absolute_path_for_payload(game_directory, source.relative_path),
        "name": posixpath.basename(source.relative_path.rstrip("/")) or game_directory,
        "type": source.source_type,
        "is_default": source.is_default,
        "persisted": source.id is not None,
    }


def absolute_path_for_payload(game_directory: str, relative_path: str) -> str:
    return posixpath.normpath(posixpath.join(game_directory, relative_path))


async def _source_for_server(
    db: AsyncSession,
    server_id: int,
    source_id: int,
) -> PluginConfigSource:
    result = await db.execute(
        select(PluginConfigSource).where(
            PluginConfigSource.id == source_id,
            PluginConfigSource.server_id == server_id,
            PluginConfigSource.is_enabled.is_(True),
        )
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="Configuration source not found")
    return source


def _file_for_source(server, source: PluginConfigSource, requested_path: str) -> str:
    try:
        relative = normalize_relative_path(server.game_directory, requested_path)
    except PluginConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source_path = posixpath.normpath(source.relative_path)
    if source.source_type == "file":
        allowed = relative == source_path
    else:
        allowed = source_path == "." or relative.startswith(source_path.rstrip("/") + "/")
    if not allowed:
        raise HTTPException(
            status_code=403, detail="File is outside the selected configuration source"
        )
    if (
        source.source_type == "directory"
        and posixpath.splitext(relative)[1].lower() not in SUPPORTED_DIRECTORY_EXTENSIONS
    ):
        raise HTTPException(
            status_code=415, detail="File extension is not enabled for directory scanning"
        )
    return relative


async def _connect(server) -> SSHManager:
    manager = SSHManager()
    success, message = await manager.connect(server)
    if not success:
        raise HTTPException(status_code=502, detail=f"SSH connection failed: {message}")
    return manager


def _remote_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PluginConfigError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (asyncssh.Error, OSError)):
        return HTTPException(
            status_code=502, detail=f"Remote configuration operation failed: {exc}"
        )
    return HTTPException(status_code=500, detail="Plugin configuration operation failed")


def _file_payload(relative_path: str, content: str) -> dict[str, Any]:
    parsed = parse_config(content, posixpath.basename(relative_path))
    return {
        "path": relative_path,
        "name": posixpath.basename(relative_path),
        "format": parsed.format,
        "revision": content_revision(content),
        "content": content,
        "visual_supported": parsed.visual_supported,
        "parse_error": parsed.parse_error,
        "fields": parsed.public_fields(),
    }


@router.get("/sources")
async def list_sources(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    server = await get_server_with_permission(server_id, current_user, db)
    result = await db.execute(
        select(PluginConfigSource)
        .where(
            PluginConfigSource.server_id == server_id,
            PluginConfigSource.is_enabled.is_(True),
        )
        .order_by(PluginConfigSource.is_default.desc(), PluginConfigSource.relative_path)
    )
    return {
        "game_directory": server.game_directory,
        "sources": [
            _source_payload(source, server.game_directory) for source in result.scalars().all()
        ],
    }


@router.post("/sources", status_code=status.HTTP_201_CREATED)
async def create_source(
    server_id: int,
    request: SourceCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    server = await get_server_with_permission(server_id, current_user, db)
    try:
        relative = normalize_relative_path(server.game_directory, request.path)
    except PluginConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    manager = await _connect(server)
    try:
        source_type = await inspect_source(manager, server, relative)
    except Exception as exc:
        raise _remote_error(exc) from exc
    finally:
        await manager.disconnect()

    digest = path_hash(relative)
    existing_result = await db.execute(
        select(PluginConfigSource).where(
            PluginConfigSource.server_id == server_id,
            PluginConfigSource.path_hash == digest,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        if existing.is_enabled:
            raise HTTPException(status_code=409, detail="Configuration source already exists")
        existing.is_enabled = True
        existing.source_type = source_type
        source = existing
    else:
        source = PluginConfigSource(
            server_id=server_id,
            relative_path=relative,
            path_hash=digest,
            source_type=source_type,
            is_default=False,
            is_enabled=True,
        )
        db.add(source)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Configuration source already exists") from exc
    await db.refresh(source)
    return _source_payload(source, server.game_directory)


@router.delete("/sources/{source_id}")
async def delete_source(
    server_id: int,
    source_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, bool]:
    await get_server_with_permission(server_id, current_user, db)
    source = await _source_for_server(db, server_id, source_id)
    if source.is_default:
        source.is_enabled = False
        db.add(source)
    else:
        await db.delete(source)
    await db.commit()
    return {"success": True}


@router.post("/sources/restore-default")
async def restore_default_source(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    server = await get_server_with_permission(server_id, current_user, db)
    digest = path_hash(DEFAULT_PLUGIN_CONFIG_SOURCE_PATH)
    result = await db.execute(
        select(PluginConfigSource).where(
            PluginConfigSource.server_id == server_id,
            PluginConfigSource.path_hash == digest,
        )
    )
    source = result.scalar_one_or_none()
    if source is None:
        source = PluginConfigSource(
            server_id=server_id,
            relative_path=DEFAULT_PLUGIN_CONFIG_SOURCE_PATH,
            path_hash=digest,
            source_type="directory",
            is_default=True,
            is_enabled=True,
        )
    else:
        source.is_default = True
        source.is_enabled = True
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return _source_payload(source, server.game_directory)


@router.get("/browse")
async def browse_source_path(
    server_id: int,
    path: str = Query("."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    server = await get_server_with_permission(server_id, current_user, db)
    try:
        relative = normalize_relative_path(server.game_directory, path)
    except PluginConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    manager = await _connect(server)
    try:
        items = await browse_directory(manager, server, relative)
        return {"path": relative, "items": items}
    except Exception as exc:
        raise _remote_error(exc) from exc
    finally:
        await manager.disconnect()


@router.post("/sources/{source_id}/scan")
async def load_source_files(
    server_id: int,
    source_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    server = await get_server_with_permission(server_id, current_user, db)
    source = await _source_for_server(db, server_id, source_id)

    async def stream_events():
        manager: Optional[SSHManager] = None
        try:
            yield json.dumps({"type": "start"}) + "\n"
            manager = await _connect(server)
            async for event in iter_source_scan(
                manager, server, source.relative_path, source.source_type
            ):
                yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        except HTTPException as exc:
            yield json.dumps({"type": "error", "detail": exc.detail}, ensure_ascii=False) + "\n"
        except PluginConfigError as exc:
            yield json.dumps({"type": "error", "detail": str(exc)}, ensure_ascii=False) + "\n"
        except (asyncssh.Error, OSError) as exc:
            yield (
                json.dumps(
                    {
                        "type": "error",
                        "detail": f"Remote configuration scan failed: {exc}",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception:
            yield json.dumps({"type": "error", "detail": "Plugin configuration scan failed"}) + "\n"
        finally:
            if manager is not None:
                await manager.disconnect()

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sources/{source_id}/file")
async def get_config_file(
    server_id: int,
    source_id: int,
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    server = await get_server_with_permission(server_id, current_user, db)
    source = await _source_for_server(db, server_id, source_id)
    relative = _file_for_source(server, source, path)
    manager = await _connect(server)
    try:
        content = await read_text_file(manager, server, relative)
        return _file_payload(relative, content)
    except Exception as exc:
        raise _remote_error(exc) from exc
    finally:
        await manager.disconnect()


@router.put("/sources/{source_id}/file")
async def save_config_file(
    server_id: int,
    source_id: int,
    request: ConfigSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    server = await get_server_with_permission(server_id, current_user, db)
    source = await _source_for_server(db, server_id, source_id)
    relative = _file_for_source(server, source, request.path)
    async with maintenance_lock_service.get(server_id, operation="plugin_config_save", wait=False):
        manager = await _connect(server)
        try:
            current_content = await read_text_file(manager, server, relative)
            if content_revision(current_content) != request.expected_revision:
                raise HTTPException(
                    status_code=409,
                    detail="Configuration changed on the server. Reload it before saving.",
                )
            try:
                if request.mode == "visual":
                    updated = apply_visual_changes(
                        current_content,
                        posixpath.basename(relative),
                        [change.model_dump() for change in request.changes],
                    )
                else:
                    if request.content is None:
                        raise PluginConfigError("Raw configuration content is required")
                    validate_raw_content(request.content, posixpath.basename(relative))
                    updated = request.content
                await atomic_write_text_file(manager, server, relative, updated)
            except PluginConfigError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {
                **_file_payload(relative, updated),
                "message": "Configuration saved. Reload the plugin or restart the server if required.",
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise _remote_error(exc) from exc
        finally:
            await manager.disconnect()
