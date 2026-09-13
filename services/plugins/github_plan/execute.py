"""Execute a previously built GitHub install plan."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import GitHubInstallRecipe, ManagedPlugin, ManagedPluginFile, User
from modules.schemas.plugins import GitHubPluginInstallPlanRequest, GitHubPluginInstallRequest
from services.compat import LateBoundModule
from services.plugins.github_assets import GitHubPlanError
from services.plugins.github_plan.common import (
    _post_install_restart_payload,
    normalize_public_repo_url,
)

ProgressCallback = Callable[..., Awaitable[None]]
host = LateBoundModule("services.github_plugin_plan_service")


async def execute_github_install_plan(
    db: AsyncSession,
    user: User,
    server_id: int,
    request: GitHubPluginInstallPlanRequest,
    expected_plan_hash: str,
    acknowledged_warning_rule_ids: set[int] | None = None,
    acknowledge_unknown_compatibility: bool = False,
    progress: ProgressCallback | None = None,
    lock_operation: str = "github_plugin_install_plan",
    operation_id: str | None = None,
) -> dict[str, Any]:
    async with host.maintenance_lock_service.get(
        server_id,
        operation=lock_operation,
        wait=False,
        ttl=3600,
    ):
        return await _execute_github_install_plan_locked(
            db,
            user,
            server_id,
            request,
            expected_plan_hash,
            acknowledged_warning_rule_ids,
            acknowledge_unknown_compatibility,
            progress,
            operation_id,
        )


def _validate_github_install_plan(
    plan: dict[str, Any],
    expected_plan_hash: str,
    acknowledged_warning_rule_ids: set[int] | None,
    acknowledge_unknown_compatibility: bool,
) -> set[int]:
    if plan["mapping_required"]:
        raise GitHubPlanError("Archive mapping requires an administrator-approved recipe")
    if plan["plan_hash"] != expected_plan_hash:
        raise GitHubPlanError("GitHub installation plan changed; inspect and approve it again")
    if plan["hard_conflicts"]:
        ids = ", ".join(str(item["rule_id"]) for item in plan["hard_conflicts"])
        raise GitHubPlanError(f"Installation blocked by hard conflict rule(s): {ids}")
    required_warning_ids = {int(item["rule_id"]) for item in plan["conflict_warnings"]}
    acknowledged = {int(item) for item in (acknowledged_warning_rule_ids or set())}
    missing_warning_ids = required_warning_ids - acknowledged
    if missing_warning_ids:
        missing = ", ".join(map(str, sorted(missing_warning_ids)))
        raise GitHubPlanError(f"Explicit acknowledgement required for warning rule(s): {missing}")
    if plan["compatibility_unknown"] and not acknowledge_unknown_compatibility:
        raise GitHubPlanError("Explicit acknowledgement is required for unknown compatibility")
    return acknowledged


async def _execute_github_install_plan_locked(
    db: AsyncSession,
    user: User,
    server_id: int,
    request: GitHubPluginInstallPlanRequest,
    expected_plan_hash: str,
    acknowledged_warning_rule_ids: set[int] | None = None,
    acknowledge_unknown_compatibility: bool = False,
    progress: ProgressCallback | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    server = await host.authorized_server(db, user, server_id)
    plan = await host.build_github_install_plan(db, user, server_id, request)
    acknowledged = _validate_github_install_plan(
        plan,
        expected_plan_hash,
        acknowledged_warning_rule_ids,
        acknowledge_unknown_compatibility,
    )
    completed_dependencies: list[dict[str, Any]] = []
    installed_ids = {int(item) for item in plan["already_installed"]}
    if plan["dependencies"]:
        for dependency in plan["dependencies"]:
            dependency_id = int(dependency["id"])
            if dependency_id in installed_ids:
                completed_dependencies.append(
                    {"plugin_id": dependency_id, "success": True, "skipped": True}
                )
                continue
            dependency_plan = await host.build_market_plan(
                db, server.id, dependency_id, server=server
            )
            dependency_result = await host.execute_market_plan(
                db,
                server,
                user,
                dependency_id,
                acknowledged,
                expected_plan_hash=dependency_plan["plan_hash"],
                acquire_lock=False,
                operation_id=operation_id,
            )
            completed_dependencies.append({"plugin_id": dependency_id, **dependency_result})
            if not dependency_result["success"]:
                dependency_restart_required = any(
                    bool(item.get("restart_required")) for item in completed_dependencies
                )
                return {
                    "success": False,
                    "message": f"Stopped after dependency {dependency_id} failed",
                    "completed_dependencies": completed_dependencies,
                    "plan_hash": plan["plan_hash"],
                    **_post_install_restart_payload(dependency_restart_required),
                }
        current_revisions = await host._target_revisions(server, plan["files"])
        if current_revisions != plan["target_revisions"]:
            dependency_restart_required = any(
                bool(item.get("restart_required")) for item in completed_dependencies
            )
            return {
                "success": False,
                "message": "Target files changed while installing dependencies; review a new plan",
                "completed_dependencies": completed_dependencies,
                "plan_hash": plan["plan_hash"],
                **_post_install_restart_payload(dependency_restart_required),
            }
    mapping = plan["mapping"]
    target_prefixes = sorted({item["target"].split("/", 1)[0] for item in mapping})
    custom_target = None
    if len(mapping) == 1 and (
        mapping[0]["target"] not in {"addons", "cfg"}
        or plan["recipe_id"] is not None
        or (mapping[0].get("source", ".") in {".", ""} and mapping[0]["target"] == "addons")
    ):
        custom_target = mapping[0]["target"]
    config_exclusions = []
    if request.mode == "upgrade" and request.config_policy == "preserve":
        config_exclusions = [
            item["install_relative"]
            for item in plan["files"]
            if item["file_role"] == "config" and item["target_revision"] != "missing"
        ]
    user_exclude_dirs = list(plan.get("exclude_dirs") or request.exclude_dirs or [])
    user_exclude_files = list(plan.get("exclude_files") or request.exclude_files or [])
    install_request = GitHubPluginInstallRequest(
        archive_mappings=plan["mapping"]
        if len(plan["mapping"]) > 1
        and any(item["target"] not in {"addons", "cfg"} for item in plan["mapping"])
        else [],
        download_url=plan["asset"]["url"],
        exclude_dirs=user_exclude_dirs,
        exclude_files=config_exclusions + user_exclude_files + user_exclude_dirs,
        custom_install_path=custom_target,
        repo_url=plan["repo_url"],
        release_id=plan["release_id"],
        release_tag=plan["release_tag"],
        asset_name=plan["asset"]["name"],
        display_name=plan["repo_url"].rsplit("/", 1)[-1],
        source_prefix=plan["source_prefix"],
        allowed_roots=(
            []
            if custom_target is not None
            else [root for root in target_prefixes if root in {"addons", "cfg"}]
        ),
        expected_archive_sha256=plan["archive_sha256"],
        installation_plan_hash=plan["plan_hash"],
        config_policy=request.config_policy,
    )
    result = await host.install_github_plugin_with_retry(
        server.id,
        install_request,
        db,
        user,
        ai_progress=progress,
        operation_id=operation_id,
    )
    if not result.success:
        dependency_restart_required = any(
            bool(item.get("restart_required")) for item in completed_dependencies
        )
        return {
            "success": False,
            "message": result.message,
            "completed_dependencies": completed_dependencies,
            "plan_hash": plan["plan_hash"],
            **_post_install_restart_payload(dependency_restart_required),
        }

    managed_result = await db.execute(
        select(ManagedPlugin).where(
            ManagedPlugin.server_id == server.id,
            ManagedPlugin.source_type == "github",
            ManagedPlugin.source_key == plan["repo_url"].lower(),
        )
    )
    managed = managed_result.scalar_one_or_none()
    if managed is not None:
        managed.install_recipe_id = plan["recipe_id"]
        managed.installed_asset_name = plan["asset"]["name"]
        managed.archive_sha256 = plan["archive_sha256"]
        managed.config_policy = request.config_policy
        db.add(managed)
        await db.commit()
        existing_result = await db.execute(
            select(ManagedPluginFile).where(ManagedPluginFile.managed_plugin_id == managed.id)
        )
        for item in existing_result.scalars().all():
            await db.delete(item)
        for item in plan["files"]:
            path = item["target_path"]
            role = item["file_role"]
            preserved = (
                role == "config"
                and request.config_policy == "preserve"
                and item["target_revision"] != "missing"
            )
            db.add(
                ManagedPluginFile(
                    managed_plugin_id=managed.id,
                    relative_path=path,
                    path_hash=hashlib.sha256(path.encode("utf-8")).hexdigest(),
                    sha256=(
                        item["target_revision"]
                        if preserved
                        else item.get("sha256") or plan["archive_sha256"]
                    ),
                    file_role=role,
                    preserved=preserved,
                )
            )
        await db.commit()
    from services.linux_runtime_service import steam_runtime_for_asset

    selected_runtime = steam_runtime_for_asset(plan["asset"]["name"])
    return {
        "success": True,
        "message": result.message,
        "installed_files": result.installed_files,
        "plan_hash": plan["plan_hash"],
        "archive_sha256": plan["archive_sha256"],
        "selected_asset_name": plan["asset"]["name"],
        "steam_runtime": selected_runtime,
        "runtime_selection_reason": (
            plan["linux_runtime_profile"]["reason"]
            if selected_runtime
            else "The selected release asset is not part of a paired Steam Runtime family"
        ),
        "linux_runtime_profile": plan["linux_runtime_profile"],
        "completed_dependencies": completed_dependencies,
        **_post_install_restart_payload(True),
    }


async def create_install_recipe(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any],
) -> GitHubInstallRecipe:
    if not user.is_admin:
        raise PermissionError("Only administrators can approve GitHub installation recipes")
    _owner, _repo, canonical = normalize_public_repo_url(payload["repo_url"])
    raw_source = str(payload.get("source_prefix") or "").replace("\\", "/")
    if (
        raw_source.startswith("/")
        or re.match(r"^[A-Za-z]:", raw_source) is not None
        or ".." in raw_source.split("/")
        or any(ord(character) < 32 for character in raw_source)
    ):
        raise GitHubPlanError("Recipe source prefix is unsafe")
    source = raw_source.strip("/")
    target = payload["target_prefix"]
    if target not in {"addons", "cfg"}:
        raise GitHubPlanError("Recipe target must be addons or cfg")
    revision_payload = {
        "repo_url": canonical,
        "source_prefix": source,
        "target_prefix": target,
        "framework": payload.get("framework"),
        "config_globs": payload.get("config_globs") or [],
        "required_repositories": payload.get("required_repositories") or [],
        "documentation_commit": payload.get("documentation_commit"),
    }
    revision = hashlib.sha256(
        json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    recipe = GitHubInstallRecipe(
        **revision_payload,
        display_name=payload["display_name"].strip(),
        revision=revision,
        created_by=user.id,
    )
    db.add(recipe)
    await db.commit()
    await db.refresh(recipe)
    return recipe
