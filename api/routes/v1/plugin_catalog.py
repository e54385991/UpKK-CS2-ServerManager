"""Versioned portable plugin-market catalog import/export.

The console uses ``GET/POST /api/v1/plugin-catalog``. Plugins, dependencies,
and conflict rules are keyed by GitHub repository URL so catalogs can move
between panels without local numeric IDs. Import is admin-only (same as
creating a market plugin). Export is available to any authenticated user.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from api.dependencies import ActiveUser, AdminUser, DatabaseSession
from modules.schemas.plugins import (
    PluginCatalogExport,
    PluginCatalogImportRequest,
    PluginCatalogImportResponse,
)
from services.audit_log_service import record_audit_event
from services.plugin_catalog import collect_export_bundle, import_plugin_catalog

from .schemas import PluginCatalogImportRequest as PluginCatalogImportBody

router = APIRouter(prefix="/api/v1/plugin-catalog", tags=["v1-plugin-catalog"])


@router.get("", response_model=PluginCatalogExport)
async def export_plugin_catalog(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> PluginCatalogExport:
    """Return the full marketplace as a portable JSON catalog."""
    del current_user
    return await collect_export_bundle(db)


@router.post("", response_model=PluginCatalogImportResponse)
async def import_plugin_catalog_route(
    body: PluginCatalogImportBody,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> PluginCatalogImportResponse:
    """Import a catalog. Non-admins receive 403, matching market create."""
    summary = await import_plugin_catalog(
        db,
        PluginCatalogImportRequest.model_validate(body.model_dump()),
    )
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.import",
        status="success" if summary.failed == 0 else "partial",
        user=current_user,
        request=request,
        details={
            "imported": summary.imported,
            "updated": summary.updated,
            "skipped": summary.skipped,
            "failed": summary.failed,
            "total": summary.total,
            "conflict_strategy": body.conflict_strategy,
        },
    )
    return summary
