"""Versioned GitHub URL plugin inspect + 202 install for the Next console."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from api.dependencies import ActiveUser, DatabaseSession, require_server_access
from api.routes.github_plugins import _safe_github_error
from api.routes.github_plugins import analyze_archive as analyze_legacy
from api.routes.github_plugins import get_github_releases as list_legacy_releases
from modules import GitHubPluginInstallPlanRequest
from services.ai_access import AgentAccessDenied, enforce_agent_rate_limit
from services.audit_log_service import record_audit_event
from services.github_plugin_plan_service import GitHubPlanError, build_github_install_plan
from services.server_operation_hub import ServerOperationConflict

from .operation_locks import reject_stuck_lock_unless_active
from .operation_runner import enqueue_github_plugin_install, enqueue_github_plugin_uninstall
from .operations import to_view
from .schemas import (
    ArchiveFileView,
    ArchiveMappingView,
    GitHubArchiveView,
    GitHubInstallPlanView,
    GitHubInstallRequest,
    GitHubReleaseAssetView,
    GitHubReleasesView,
    GitHubReleaseView,
    GitHubUninstallRequest,
    LinuxRuntimeProfileView,
    PluginConflictView,
    PluginRef,
    ServerOperationView,
)
from .schemas import (
    GitHubInstallPlanRequest as GitHubInstallPlanBody,
)

market_router = APIRouter(prefix="/api/v1/plugins/github", tags=["v1-github-plugins"])
server_router = APIRouter(
    prefix="/api/v1/servers/{server_id}/plugins/github",
    tags=["v1-github-plugins"],
)


def _runtime_view(raw) -> LinuxRuntimeProfileView | None:
    if raw is None:
        return None
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        return None
    return LinuxRuntimeProfileView(
        distro_id=raw.get("distro_id"),
        distro_version=raw.get("distro_version"),
        pretty_name=raw.get("pretty_name"),
        glibc_version=raw.get("glibc_version"),
        recommended_steam_runtime=raw.get("recommended_steam_runtime"),
        detection_source=str(raw.get("detection_source") or "unknown"),
        reason=str(raw.get("reason") or ""),
    )


def _conflict_views(items: list) -> list[PluginConflictView]:
    views: list[PluginConflictView] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        views.append(
            PluginConflictView(
                rule_id=int(item.get("rule_id") or 0),
                plugin_a_id=int(item.get("plugin_a_id") or 0),
                plugin_b_id=int(item.get("plugin_b_id") or 0),
                severity=str(item.get("severity") or "warning"),
                reason=str(item.get("reason") or ""),
            )
        )
    return views


def _dependency_refs(items: list) -> list[PluginRef]:
    refs: list[PluginRef] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        plugin_id = item.get("id")
        title = item.get("title")
        if plugin_id is None or not title:
            continue
        refs.append(PluginRef(id=int(plugin_id), title=str(title)))
    return refs


def to_plan_view(plan: dict) -> GitHubInstallPlanView:
    release = plan.get("release") if isinstance(plan.get("release"), dict) else {}
    asset = plan.get("asset") if isinstance(plan.get("asset"), dict) else {}
    return GitHubInstallPlanView(
        server_id=int(plan["server_id"]),
        repo_url=str(plan.get("repo_url") or ""),
        mode=str(plan.get("mode") or "install"),
        config_policy=str(plan.get("config_policy") or "preserve"),
        plan_hash=str(plan["plan_hash"]),
        release_tag=str(release.get("tag") or release.get("tag_name") or "") or None,
        release_name=str(release.get("name") or "") or None,
        asset_name=str(asset.get("name") or "") or None,
        archive_sha256=str(plan.get("archive_sha256") or "") or None,
        mapping_required=bool(plan.get("mapping_required")),
        source_prefix=str(plan.get("source_prefix") or "") or None,
        mapping=[
            ArchiveMappingView(
                source=str(item.get("source") or "."),
                target=str(item.get("target") or ""),
            )
            for item in (plan.get("mapping") or [])
            if isinstance(item, dict) and item.get("target")
        ],
        recipe_id=int(plan["recipe_id"]) if plan.get("recipe_id") else None,
        exclude_dirs=[str(item) for item in (plan.get("exclude_dirs") or [])],
        exclude_files=[str(item) for item in (plan.get("exclude_files") or [])],
        warnings=[str(item) for item in (plan.get("warnings") or [])],
        hard_conflicts=_conflict_views(list(plan.get("hard_conflicts") or [])),
        conflict_warnings=_conflict_views(list(plan.get("conflict_warnings") or [])),
        compatibility_unknown=bool(plan.get("compatibility_unknown")),
        already_installed=[int(item) for item in (plan.get("already_installed") or [])],
        dependencies=_dependency_refs(list(plan.get("dependencies") or [])),
        linux_runtime_profile=_runtime_view(plan.get("linux_runtime_profile")),
    )


def _to_legacy_plan_request(body: GitHubInstallPlanBody) -> GitHubPluginInstallPlanRequest:
    return GitHubPluginInstallPlanRequest(
        repo_url=body.repo_url,
        mode=body.mode,
        asset_name=body.asset_name,
        config_policy=body.config_policy,
        recipe_id=body.recipe_id,
        source_prefix=body.source_prefix,
        target_prefix=body.target_prefix,
        exclude_dirs=list(body.exclude_dirs),
        exclude_files=list(body.exclude_files),
    )


@market_router.get("/releases", response_model=GitHubReleasesView)
async def list_github_releases(
    repo_url: str = Query(min_length=1, max_length=500),
    count: int = Query(default=5, ge=1, le=10),
    server_id: int | None = Query(default=None),
    db: DatabaseSession = None,
    current_user: ActiveUser = None,
) -> GitHubReleasesView:
    """Fetch public GitHub release archives. Optional server_id adds runtime hints."""
    if server_id is not None:
        await require_server_access(db, server_id, current_user)
    raw = await list_legacy_releases(
        repo_url=repo_url,
        count=count,
        server_id=server_id,
        db=db,
        current_user=current_user,
    )
    if not raw.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(raw.error or "Failed to fetch GitHub releases"),
        )
    releases: list[GitHubReleaseView] = []
    for release in raw.releases or []:
        assets = [
            GitHubReleaseAssetView(
                name=asset.name,
                browser_download_url=asset.browser_download_url,
                size=int(asset.size or 0),
                content_type=asset.content_type,
                steam_runtime=asset.steam_runtime,
                runtime_compatibility=asset.runtime_compatibility,
            )
            for asset in release.assets or []
        ]
        releases.append(
            GitHubReleaseView(
                id=release.id,
                tag_name=release.tag_name,
                name=release.name,
                published_at=release.published_at,
                prerelease=bool(release.prerelease),
                assets=assets,
            )
        )
    return GitHubReleasesView(
        repo_owner=raw.repo_owner,
        repo_name=raw.repo_name,
        releases=releases,
        linux_runtime_profile=_runtime_view(raw.linux_runtime_profile),
    )


@server_router.get("/analyze-archive", response_model=GitHubArchiveView)
async def analyze_github_archive(
    server_id: int,
    download_url: str,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> GitHubArchiveView:
    """Inspect a GitHub release asset locally. Does not install anything."""
    raw = await analyze_legacy(server_id, download_url, db, current_user)
    if not raw.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(raw.error or "Archive analysis failed"),
        )
    return GitHubArchiveView(
        has_addons_dir=bool(raw.has_addons_dir),
        root_dirs=list(raw.root_dirs or []),
        all_dirs=list(raw.all_dirs or []),
        all_files=[
            ArchiveFileView(path=item.path, is_dir=bool(item.is_dir), size=int(item.size or 0))
            for item in (raw.all_files or [])
        ],
        archive_type=raw.archive_type,
    )


@server_router.post("/plan", response_model=GitHubInstallPlanView)
async def plan_github_plugin_install(
    server_id: int,
    body: GitHubInstallPlanBody,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> GitHubInstallPlanView:
    """Build a GitHub install plan without changing the server."""
    await require_server_access(db, server_id, current_user)
    try:
        await enforce_agent_rate_limit(current_user.id, "github_plan", limit=5)
        plan = await build_github_install_plan(
            db, current_user, server_id, _to_legacy_plan_request(body)
        )
    except (AgentAccessDenied, GitHubPlanError) as exc:
        raise _safe_github_error(exc) from exc
    return to_plan_view(plan)


@server_router.post(
    "/install",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_github_plugin(
    server_id: int,
    body: GitHubInstallRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Accept a GitHub install and return immediately with an operation_id."""
    await require_server_access(db, server_id, current_user)
    await reject_stuck_lock_unless_active(server_id)

    try:
        await enforce_agent_rate_limit(
            current_user.id, "github_install", limit=2, window_seconds=300
        )
        plan = await build_github_install_plan(
            db, current_user, server_id, _to_legacy_plan_request(body)
        )
    except (AgentAccessDenied, GitHubPlanError) as exc:
        raise _safe_github_error(exc) from exc
    if plan.get("mapping_required"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Archive mapping requires an administrator-approved recipe",
        )
    if plan.get("hard_conflicts"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Installation blocked by a hard conflict rule",
        )
    if plan.get("plan_hash") != body.expected_plan_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="GitHub installation plan changed; inspect and approve it again",
        )

    try:
        record = await enqueue_github_plugin_install(
            server_id=server_id,
            actor_user_id=current_user.id,
            repo_url=body.repo_url,
            mode=body.mode,
            asset_name=body.asset_name,
            config_policy=body.config_policy,
            recipe_id=body.recipe_id,
            source_prefix=body.source_prefix,
            target_prefix=body.target_prefix,
            exclude_dirs=list(body.exclude_dirs),
            exclude_files=list(body.exclude_files),
            expected_plan_hash=body.expected_plan_hash,
            acknowledge_warning_rule_ids=body.acknowledge_warning_rule_ids,
            acknowledge_unknown_compatibility=body.acknowledge_unknown_compatibility,
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
            "source": "github",
            "mode": body.mode,
        },
    )
    return to_view(record)


@server_router.post(
    "/uninstall",
    response_model=ServerOperationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def uninstall_github_plugin(
    server_id: int,
    body: GitHubUninstallRequest,
    request: Request,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> ServerOperationView:
    """Accept a selected-file uninstall and return immediately with an operation_id."""
    await require_server_access(db, server_id, current_user)
    await reject_stuck_lock_unless_active(server_id)

    try:
        record = await enqueue_github_plugin_uninstall(
            server_id=server_id,
            actor_user_id=current_user.id,
            files_to_delete=list(body.files_to_delete),
            market_plugin_id=body.market_plugin_id,
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
            "source": "github",
            "file_count": len(body.files_to_delete),
            "market_plugin_id": body.market_plugin_id,
        },
    )
    return to_view(record)
