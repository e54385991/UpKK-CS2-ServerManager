"""Administrator-only bulk actions for the marketplace catalogue."""

from fastapi import APIRouter, Request

from api.dependencies import AdminUser, DatabaseSession
from services.audit_log_service import record_audit_event
from services.plugin_catalog import delete_market_plugins

from .schemas import ActionResult, MarketPluginBulkDeleteRequest

router = APIRouter(prefix="/api/v1/plugins", tags=["v1-plugins"])


@router.post("/market/bulk-delete", response_model=ActionResult)
async def bulk_delete_market_plugins(
    body: MarketPluginBulkDeleteRequest,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> ActionResult:
    """Delete selected listings or clear the whole marketplace catalogue."""
    deleted = await delete_market_plugins(db, None if body.clear_all else list(body.plugin_ids))
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.bulk_delete",
        status="success",
        user=current_user,
        request=request,
        details={
            "clear_all": body.clear_all,
            "requested_plugin_ids": list(body.plugin_ids),
            "deleted_plugin_ids": [int(plugin.id) for plugin in deleted if plugin.id is not None],
        },
    )
    scope = "catalogue" if body.clear_all else "selected listings"
    return ActionResult(
        success=True,
        message=f"Deleted {len(deleted)} plugin listing(s) from the {scope}.",
    )
