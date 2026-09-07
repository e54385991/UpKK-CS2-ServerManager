"""Versioned plugin market browse + managed-plugin list + async install."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, get_args

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlmodel import select

from api.dependencies import ActiveUser, AdminUser, DatabaseSession, require_server_access
from api.routes import plugin_market as legacy
from modules import ManagedPlugin, MarketPlugin, PluginCategory, PluginFramework
from modules.plugin_ai import PluginAIInfo
from modules.schemas.plugins import (
    MarketPluginCreate,
    MarketPluginResponse,
    MarketPluginUpdate,
)
from services.audit_log_service import record_audit_event
from services.github_credentials import get_effective_github_token
from services.plugin_catalog import delete_market_plugin as remove_catalog_plugin
from services.plugin_conflict_service import (
    PluginPlanError,
    build_plugin_install_plan,
    validate_plugin_plan_acknowledgements,
)
from services.plugins.common import framework_value, parse_dependency_ids, parse_framework
from services.plugins.description_sync import sync_market_plugin_descriptions
from services.plugins.tracking import forget_managed_plugin, forget_server_managed_plugins
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import enqueue_github_plugin_uninstall, enqueue_plugin_install
from .operations import to_view
from .schemas import (
    DEFAULT_PLUGIN_FRAMEWORK,
    ActionResult,
    GitHubRepoInfoRequest,
    GitHubRepoInfoView,
    GitHubUninstallRequest,
    ManagedPluginView,
    MarketPluginCreateRequest,
    MarketPluginDescriptionSyncItemView,
    MarketPluginDescriptionSyncRequest,
    MarketPluginDescriptionSyncView,
    MarketPluginUpdateRequest,
    MarketPluginView,
    MarketSort,
    Page,
    PluginAINoticeView,
    PluginCategoryList,
    PluginCategoryLiteral,
    PluginCategoryView,
    PluginConflictView,
    PluginDependencyOptionsView,
    PluginFrameworkCompatibilityView,
    PluginFrameworkLiteral,
    PluginInstallPlanView,
    PluginInstallRequest,
    PluginInstallStep,
    PluginRef,
    ProblemDetail,
    ServerOperationView,
)

market_router = APIRouter(prefix="/api/v1/plugins", tags=["v1-plugins"])
server_router = APIRouter(
    prefix="/api/v1/servers/{server_id}/plugins",
    tags=["v1-plugins"],
)


def _category_value(category: PluginCategory | str) -> str:
    return category.value if isinstance(category, PluginCategory) else str(category)


def _parse_framework_filter(framework: str | None) -> PluginFramework | None:
    """Resolve the marketplace section filter, rejecting unknown sections."""
    if not framework:
        return None
    try:
        return parse_framework(framework)
    except PluginPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _to_plugin_ref(plugin: MarketPlugin) -> PluginRef:
    return PluginRef(id=int(plugin.id), title=plugin.title)


async def _dependency_refs(
    db, plugins: Sequence[MarketPlugin | MarketPluginResponse]
) -> list[list[PluginRef]]:
    parsed: list[list[int] | None] = []
    all_ids: list[int] = []
    for plugin in plugins:
        if not plugin.dependencies:
            parsed.append([])
            continue
        try:
            dep_ids = parse_dependency_ids(plugin.dependencies)
        except ValueError, PluginPlanError:
            parsed.append(None)
            continue
        parsed.append(dep_ids)
        all_ids.extend(dep_ids)

    found = await MarketPlugin.get_by_ids(db, all_ids)
    by_id = {int(item.id): item for item in found if item.id is not None}
    refs: list[list[PluginRef]] = []
    for dep_ids in parsed:
        if not dep_ids:
            refs.append([])
            continue
        refs.append(
            [_to_plugin_ref(dep) for dep_id in dep_ids if (dep := by_id.get(dep_id)) is not None]
        )
    return refs


def to_market_view(
    plugin: MarketPlugin | MarketPluginResponse, dependencies: list[PluginRef]
) -> MarketPluginView:
    return MarketPluginView(
        id=int(plugin.id),
        title=plugin.title,
        description=plugin.description,
        author=plugin.author,
        version=plugin.version,
        category=_category_value(plugin.category),
        framework=framework_value(plugin.framework),
        tags=plugin.tags,
        is_recommended=bool(plugin.is_recommended),
        icon_url=plugin.icon_url,
        github_url=plugin.github_url,
        custom_install_path=plugin.custom_install_path,
        ai_metadata=PluginAIInfo.model_validate(plugin.ai_metadata) if plugin.ai_metadata else None,
        download_count=int(plugin.download_count or 0),
        install_count=int(plugin.install_count or 0),
        created_at=getattr(plugin, "created_at", None),
        dependencies=dependencies,
    )


def to_managed_view(plugin: ManagedPlugin) -> ManagedPluginView:
    return ManagedPluginView(
        id=int(plugin.id),
        server_id=int(plugin.server_id),
        source_type=plugin.source_type,
        source_key=plugin.source_key,
        display_name=plugin.display_name,
        repo_url=plugin.repo_url,
        market_plugin_id=plugin.market_plugin_id,
        framework_key=plugin.framework_key,
        installed_version=plugin.installed_version,
        latest_version=plugin.latest_version,
        auto_update_enabled=bool(plugin.auto_update_enabled),
        last_status=plugin.last_status,
        last_error=plugin.last_error,
        last_check_at=plugin.last_check_at,
        last_update_at=plugin.last_update_at,
    )


def _conflict_views(items: list[dict[str, Any]]) -> list[PluginConflictView]:
    return [
        PluginConflictView(
            rule_id=int(item["rule_id"]),
            plugin_a_id=int(item["plugin_a_id"]),
            plugin_b_id=int(item["plugin_b_id"]),
            severity=str(item["severity"]),
            reason=str(item["reason"]),
        )
        for item in items
    ]


def _framework_literal(value: str | None) -> PluginFrameworkLiteral | None:
    """Only pass through a runtime the contract actually declares."""
    for allowed in get_args(PluginFrameworkLiteral):
        if value == allowed:
            return allowed
    return None


def _category_literal(value: str | None) -> PluginCategoryLiteral | None:
    for allowed in get_args(PluginCategoryLiteral):
        if value == allowed:
            return allowed
    return None


def _framework_view(compatibility: dict[str, Any]) -> PluginFrameworkCompatibilityView:
    return PluginFrameworkCompatibilityView(
        plugin=str(compatibility.get("plugin") or DEFAULT_PLUGIN_FRAMEWORK),
        installed=[str(item) for item in compatibility.get("installed") or []],
        conflicting=[str(item) for item in compatibility.get("conflicting") or []],
        missing=bool(compatibility.get("missing")),
        mismatch=bool(compatibility.get("mismatch")),
    )


def _ai_notice_views(notices: list[dict[str, Any]]) -> list[PluginAINoticeView]:
    return [
        PluginAINoticeView(
            plugin_id=int(item["plugin_id"]),
            title=str(item["title"]),
            reviewed=bool(item.get("reviewed")),
            requirements=[str(value) for value in item.get("requirements") or []],
            notes=[str(value) for value in item.get("notes") or []],
        )
        for item in notices
    ]


def to_plan_view(plan: dict[str, Any]) -> PluginInstallPlanView:
    plugin = plan["plugin"]
    return PluginInstallPlanView(
        server_id=int(plan["server_id"]),
        plugin=PluginRef(id=int(plugin["id"]), title=str(plugin["title"])),
        dependencies=[
            PluginRef(id=int(item["id"]), title=str(item["title"]))
            for item in plan.get("dependencies") or []
        ],
        installation_order=[int(item) for item in plan.get("installation_order") or []],
        already_installed=[int(item) for item in plan.get("already_installed") or []],
        tracking_records_without_remote_evidence=list(
            plan.get("tracking_records_without_remote_evidence") or []
        ),
        compatibility_unknown=list(plan.get("compatibility_unknown") or []),
        hard_conflicts=_conflict_views(list(plan.get("hard_conflicts") or [])),
        warnings=_conflict_views(list(plan.get("warnings") or [])),
        framework=_framework_view(plan.get("framework") or {}),
        ai_unreviewed=plan.get("ai_unreviewed", []),
        ai_notices=_ai_notice_views(list(plan.get("ai_notices") or [])),
        steps=[
            PluginInstallStep(
                order=int(step["order"]),
                plugin_id=int(step["plugin_id"]),
                title=str(step["title"]),
                kind=str(step["kind"]),
                status=str(step["status"]),
                reason=str(step["reason"]),
            )
            for step in plan.get("steps") or []
        ],
        blocked=bool(plan.get("blocked")),
        plan_hash=str(plan["plan_hash"]),
    )


@market_router.get("/market", response_model=Page[MarketPluginView])
async def list_market_plugins(
    db: DatabaseSession,
    current_user: ActiveUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: str | None = Query(None),
    framework: str | None = Query(
        None,
        description="Marketplace section: counterstrikesharp, swiftly or other",
    ),
    sort: MarketSort = Query(
        "recommended",
        description="Browse order: recommended, newest or oldest by creation time",
    ),
    q: str | None = Query(None, max_length=200),
) -> Page[MarketPluginView]:
    """Browse the plugin marketplace with offset pagination."""
    del current_user
    framework_enum = _parse_framework_filter(framework)
    category_enum = None
    if category:
        try:
            category_enum = PluginCategory(category)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Invalid category. Valid categories: "
                    + ", ".join(item.value for item in PluginCategory)
                ),
            ) from None

    plugins, total = await MarketPlugin.search_plugins(
        db,
        category=category_enum,
        search_query=q.strip() if q and q.strip() else None,
        skip=offset,
        limit=limit,
        framework=framework_enum,
        sort=sort,
    )
    dependency_lists = await _dependency_refs(db, plugins)
    return Page(
        items=[
            to_market_view(plugin, deps)
            for plugin, deps in zip(plugins, dependency_lists, strict=True)
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@market_router.get("/market/categories", response_model=PluginCategoryList)
async def list_market_categories(current_user: ActiveUser) -> PluginCategoryList:
    """Return the stable marketplace category vocabulary."""
    del current_user
    return PluginCategoryList(
        items=[
            PluginCategoryView(value=item.value, name=item.value.replace("_", " ").title())
            for item in PluginCategory
        ]
    )


@market_router.get(
    "/market/dependency-options",
    response_model=PluginDependencyOptionsView,
    responses={403: {"model": ProblemDetail}},
)
async def list_market_dependency_options(
    db: DatabaseSession,
    current_user: AdminUser,
    search: str | None = Query(default=None, max_length=200),
    exclude_id: int | None = Query(default=None, ge=1),
) -> PluginDependencyOptionsView:
    """Return the minimal admin-only dependency picker options."""
    plugins, _ = await MarketPlugin.search_plugins(
        db,
        search_query=search.strip() if search and search.strip() else None,
        skip=0,
        limit=100,
    )
    return PluginDependencyOptionsView(
        items=[
            _to_plugin_ref(plugin)
            for plugin in plugins
            if exclude_id is None or plugin.id != exclude_id
        ]
    )


@market_router.post(
    "/market/repo-info",
    response_model=GitHubRepoInfoView,
    responses={403: {"model": ProblemDetail}},
)
async def fetch_market_repo_info(
    body: GitHubRepoInfoRequest,
    db: DatabaseSession,
    current_user: AdminUser,
) -> GitHubRepoInfoView:
    """Fetch non-secret GitHub metadata for the admin create form."""
    github_token = await get_effective_github_token(db, current_user)
    await db.commit()
    result = await legacy.fetch_github_repo_info(body.github_url, github_token=github_token)
    return GitHubRepoInfoView(
        success=bool(result.success),
        repo_name=result.repo_name,
        description=result.description,
        readme=result.readme,
        author=result.author,
        topics=list(result.topics or [])[:50],
        framework=_framework_literal(result.framework),
        category=_category_literal(result.category),
        error=result.error,
    )


@market_router.post(
    "/market",
    response_model=MarketPluginView,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"model": ProblemDetail},
        409: {"model": ProblemDetail},
    },
)
async def create_market_plugin(
    body: MarketPluginCreateRequest,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> MarketPluginView:
    """Create a marketplace listing through the existing validated workflow."""
    created = await legacy.create_plugin(
        MarketPluginCreate(**body.model_dump()),
        db,
        current_user,
    )
    dependencies = (await _dependency_refs(db, [created]))[0]
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.create",
        status="success",
        user=current_user,
        request=request,
        details={
            "plugin_id": created.id,
            "title": created.title,
            "github_url": created.github_url,
        },
    )
    return to_market_view(created, dependencies)


@market_router.post(
    "/market/descriptions/sync",
    response_model=MarketPluginDescriptionSyncView,
    responses={403: {"model": ProblemDetail}},
)
async def sync_market_descriptions(
    body: MarketPluginDescriptionSyncRequest,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> MarketPluginDescriptionSyncView:
    """Refresh marketplace descriptions in bulk from the upstream READMEs."""
    github_token = await get_effective_github_token(db, current_user)
    result = await sync_market_plugin_descriptions(
        db,
        github_token=github_token,
        plugin_ids=list(body.plugin_ids) or None,
        framework=_parse_framework_filter(body.framework),
        overwrite=body.overwrite,
    )
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.sync_descriptions",
        status="success" if result.failed == 0 else "partial",
        user=current_user,
        request=request,
        details={
            "total": result.total,
            "updated": result.updated,
            "failed": result.failed,
            "framework": body.framework,
            "overwrite": body.overwrite,
        },
    )
    return MarketPluginDescriptionSyncView(
        total=result.total,
        updated=result.updated,
        unchanged=result.unchanged,
        skipped=result.skipped,
        failed=result.failed,
        remaining=result.remaining,
        items=[
            MarketPluginDescriptionSyncItemView(
                plugin_id=item.plugin_id,
                title=item.title,
                github_url=item.github_url,
                action=item.action,
                message=item.message,
            )
            for item in result.items
        ],
    )


@market_router.get("/market/{plugin_id}", response_model=MarketPluginView)
async def get_market_plugin(
    plugin_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> MarketPluginView:
    """Return one marketplace plugin, including resolved dependency titles."""
    del current_user
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    dependencies = (await _dependency_refs(db, [plugin]))[0]
    return to_market_view(plugin, dependencies)


@market_router.patch(
    "/market/{plugin_id}",
    response_model=MarketPluginView,
    responses={
        400: {"model": ProblemDetail},
        403: {"model": ProblemDetail},
        404: {"model": ProblemDetail},
    },
)
async def update_market_plugin(
    plugin_id: int,
    body: MarketPluginUpdateRequest,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> MarketPluginView:
    """Edit an existing marketplace listing. Only submitted fields change."""
    submitted = body.model_dump(include=body.model_fields_set)
    changed = {key: value for key, value in submitted.items() if value is not None}
    if not changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one field to update",
        )
    updated = await legacy.update_plugin(
        plugin_id,
        MarketPluginUpdate(**changed),
        db,
        current_user,
    )
    dependencies = (await _dependency_refs(db, [updated]))[0]
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.update",
        status="success",
        user=current_user,
        request=request,
        details={
            "plugin_id": plugin_id,
            "title": updated.title,
            "fields": sorted(changed),
        },
    )
    return to_market_view(updated, dependencies)


@market_router.delete("/market/{plugin_id}", response_model=ActionResult)
async def delete_market_plugin(
    plugin_id: int,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> ActionResult:
    """Remove a marketplace listing. Members receive 403. Files on servers stay."""
    plugin = await remove_catalog_plugin(db, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    await record_audit_event(
        category="plugin",
        action="plugin.catalog.delete",
        status="success",
        user=current_user,
        request=request,
        details={
            "plugin_id": plugin_id,
            "title": plugin.title,
            "github_url": plugin.github_url,
        },
    )
    return ActionResult(success=True, message=f"Plugin '{plugin.title}' deleted successfully")


@server_router.get("", response_model=list[ManagedPluginView])
async def list_server_plugins(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> list[ManagedPluginView]:
    """List plugins already tracked on a server the caller can access."""
    await require_server_access(db, server_id, current_user)
    result = await db.execute(
        select(ManagedPlugin)
        .where(ManagedPlugin.server_id == server_id)
        .order_by(ManagedPlugin.display_name)
    )
    return [to_managed_view(item) for item in result.scalars().all()]


@server_router.delete("", response_model=ActionResult)
async def forget_all_server_plugins(
    server_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> ActionResult:
    """Clear every tracking record for a server. Files on the host are kept."""
    await require_server_access(db, server_id, current_user)
    removed = await forget_server_managed_plugins(db, server_id)
    await record_audit_event(
        category="plugin",
        action="plugin.tracking.clear_all",
        status="success",
        user=current_user,
        request=request,
        server_id=server_id,
        details={"removed": removed},
    )
    return ActionResult(
        success=True,
        message=f"Cleared {removed} plugin record(s). Files on the game server were not deleted.",
    )


@server_router.delete("/{managed_plugin_id}", response_model=ActionResult)
async def forget_server_plugin(
    server_id: int,
    managed_plugin_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    request: Request,
) -> ActionResult:
    """Clear one tracking record. Files on the host are kept."""
    await require_server_access(db, server_id, current_user)
    removed = await forget_managed_plugin(db, server_id, managed_plugin_id)
    if removed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin record not found")
    await record_audit_event(
        category="plugin",
        action="plugin.tracking.clear",
        status="success",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "managed_plugin_id": managed_plugin_id,
            "display_name": removed.display_name,
            "source_type": removed.source_type,
        },
    )
    return ActionResult(
        success=True,
        message=(
            f"Cleared the record for '{removed.display_name}'. "
            "Files on the game server were not deleted."
        ),
    )


@server_router.get(
    "/market/{plugin_id}/preflight",
    response_model=PluginInstallPlanView,
)
async def plugin_install_preflight(
    server_id: int,
    plugin_id: int,
    db: DatabaseSession,
    current_user: ActiveUser,
    install_dependencies: bool = Query(
        False,
        description="Whether to include declared dependencies in the plan (opt-in, matches the web installer)",
    ),
) -> PluginInstallPlanView:
    """Resolve dependencies and conflicts without changing the server."""
    server = await require_server_access(db, server_id, current_user)
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    try:
        plan = await build_plugin_install_plan(
            db,
            server_id,
            plugin_id,
            include_dependencies=install_dependencies,
            server=server,
        )
    except PluginPlanError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return to_plan_view(plan)


@server_router.post(
    "/market/{plugin_id}/install",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_market_plugin(
    server_id: int,
    plugin_id: int,
    body: PluginInstallRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Accept a market install and return immediately with an operation_id."""
    server = await require_server_access(db, server_id, current_user)
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    await reject_stuck_lock_unless_active(server_id)

    try:
        plan = await build_plugin_install_plan(
            db,
            server_id,
            plugin_id,
            include_dependencies=body.install_dependencies,
            server=server,
        )
        validate_plugin_plan_acknowledgements(
            plan,
            body.acknowledge_warning_rule_ids,
            acknowledge_framework_mismatch=body.acknowledge_framework_mismatch,
            acknowledge_ai_unreviewed=body.acknowledge_ai_unreviewed,
        )
    except PluginPlanError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if plan.get("blocked"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Installation blocked by a hard conflict rule",
        )
    if body.plan_hash and plan.get("plan_hash") != body.plan_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin plan changed; review and approve the new plan",
        )

    try:
        record = await enqueue_plugin_install(
            server_id=server_id,
            plugin_id=plugin_id,
            actor_user_id=current_user.id,
            acknowledge_warning_rule_ids=body.acknowledge_warning_rule_ids,
            acknowledge_framework_mismatch=body.acknowledge_framework_mismatch,
            acknowledge_ai_unreviewed=body.acknowledge_ai_unreviewed,
            plan_hash=body.plan_hash or plan["plan_hash"],
            download_url=body.download_url,
            upgrade_mode=body.upgrade_mode,
            install_dependencies=body.install_dependencies,
            exclude_dirs=list(body.exclude_dirs),
            exclude_files=list(body.exclude_files),
        )
    except ServerOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.install",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "operation_id": record["operation_id"],
            "plugin_id": plugin_id,
            "upgrade_mode": body.upgrade_mode,
            "framework_mismatch_acknowledged": body.acknowledge_framework_mismatch,
        },
    )
    return to_view(record)


@server_router.post(
    "/market/{plugin_id}/uninstall",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def uninstall_market_plugin(
    server_id: int,
    plugin_id: int,
    body: GitHubUninstallRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Uninstall a market plugin by deleting selected files, then drop tracking."""
    await require_server_access(db, server_id, current_user)
    plugin = await MarketPlugin.get_by_id(db, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    await reject_stuck_lock_unless_active(server_id)
    try:
        record = await enqueue_github_plugin_uninstall(
            server_id=server_id,
            actor_user_id=current_user.id,
            files_to_delete=list(body.files_to_delete),
            market_plugin_id=plugin_id,
        )
    except ServerOperationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await record_audit_event(
        category="plugin",
        action="plugin.uninstall",
        status="requested",
        user=current_user,
        request=request,
        server_id=server_id,
        details={
            "operation_id": record["operation_id"],
            "plugin_id": plugin_id,
            "file_count": len(body.files_to_delete),
        },
    )
    return to_view(record)
