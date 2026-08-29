"""Versioned plugin market browse + managed-plugin list + async install."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import select

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from modules import ManagedPlugin, MarketPlugin, PluginCategory
from services.plugin_conflict_service import (
    PluginPlanError,
    build_plugin_install_plan,
    validate_plugin_plan_acknowledgements,
)
from services.plugins.common import parse_dependency_ids
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import enqueue_github_plugin_uninstall, enqueue_plugin_install
from .operations import to_view
from .schemas import (
    GitHubUninstallRequest,
    ManagedPluginView,
    MarketPluginView,
    Page,
    PluginCategoryList,
    PluginCategoryView,
    PluginConflictView,
    PluginInstallPlanView,
    PluginInstallRequest,
    PluginInstallStep,
    PluginRef,
    ServerOperationView,
)

market_router = APIRouter(prefix="/api/v1/plugins", tags=["v1-plugins"])
server_router = APIRouter(
    prefix="/api/v1/servers/{server_id}/plugins",
    tags=["v1-plugins"],
)


def _category_value(category: PluginCategory | str) -> str:
    return category.value if isinstance(category, PluginCategory) else str(category)


def _to_plugin_ref(plugin: MarketPlugin) -> PluginRef:
    return PluginRef(id=int(plugin.id), title=plugin.title)


async def _dependency_refs(db, plugins: list[MarketPlugin]) -> list[list[PluginRef]]:
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


def to_market_view(plugin: MarketPlugin, dependencies: list[PluginRef]) -> MarketPluginView:
    return MarketPluginView(
        id=int(plugin.id),
        title=plugin.title,
        description=plugin.description,
        author=plugin.author,
        version=plugin.version,
        category=_category_value(plugin.category),
        tags=plugin.tags,
        is_recommended=bool(plugin.is_recommended),
        icon_url=plugin.icon_url,
        github_url=plugin.github_url,
        download_count=int(plugin.download_count or 0),
        install_count=int(plugin.install_count or 0),
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
    q: str | None = Query(None, max_length=200),
) -> Page[MarketPluginView]:
    """Browse the plugin marketplace with offset pagination."""
    del current_user
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
        validate_plugin_plan_acknowledgements(plan, body.acknowledge_warning_rule_ids)
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
    return to_view(record)
