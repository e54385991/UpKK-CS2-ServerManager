"""Build an immutable GitHub install plan."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import MarketPlugin, User
from modules.schemas.plugins import GitHubPluginInstallPlanRequest
from services.compat import LateBoundModule
from services.plugins.github_assets import GitHubPlanError
from services.plugins.github_plan.common import (
    _github_plan_confirmation_payload,
    _panel_managed_framework,
    normalize_public_repo_url,
)
from services.plugins.github_plan.mapping import (
    _apply_user_mapping,
    _infer_plugin_metadata,
    _mapped_files,
)

host = LateBoundModule("services.github_plugin_plan_service")


async def build_github_install_plan(
    db: AsyncSession,
    user: User,
    server_id: int,
    request: GitHubPluginInstallPlanRequest,
) -> dict[str, Any]:
    server = await host.authorized_server(db, user, server_id)
    requested_owner, requested_repo, _requested_canonical = normalize_public_repo_url(
        request.repo_url
    )
    framework_name = _panel_managed_framework(requested_owner, requested_repo)
    if framework_name is not None:
        operation = (
            "install_metamod"
            if framework_name == "Metamod:Source"
            else "install_counterstrikesharp"
        )
        raise GitHubPlanError(
            f"{framework_name} is managed by the panel. Use run_server_operation with "
            f"operation={operation}; generic GitHub installation is disabled for this framework."
        )
    from services.linux_runtime_service import detect_linux_runtime_profile

    linux_runtime_profile = await detect_linux_runtime_profile(server)
    inspected = await host.inspect_github_plugin(
        db,
        user,
        request.repo_url,
        request.mode,
        linux_runtime_profile,
    )
    assets = inspected["release"]["assets"]
    if request.asset_name:
        selected = next((item for item in assets if item["name"] == request.asset_name), None)
        if selected is None:
            raise GitHubPlanError("Selected asset is not part of the latest stable Linux release")
    else:
        selected = inspected["selected_asset"]
    if selected is None:
        raise GitHubPlanError("Select exactly one stable Linux release archive")
    _owner, repo_name, canonical = normalize_public_repo_url(inspected["repo_url"])
    layout = await host.inspect_release_asset_layout(selected, repo_name)
    archive_sha256 = layout["archive_sha256"]
    entries = layout["entries"]
    source_prefix = layout["source_prefix"]
    mapping = layout["mapping"]
    mapping_required = layout["mapping_required"]
    plugin_metadata = _infer_plugin_metadata(entries, inspected["documentation"])
    recipe = await host._recipe_for_plan(db, user, canonical, request.recipe_id)
    recipe_revision = None
    if recipe is not None:
        source_prefix = recipe.source_prefix or None
        mapping = [{"source": recipe.source_prefix or ".", "target": recipe.target_prefix}]
        mapping_required = False
        recipe_revision = recipe.revision
    elif request.source_prefix is not None or request.target_prefix is not None:
        source_prefix, mapping = _apply_user_mapping(
            entries, request.source_prefix, request.target_prefix
        )
        mapping_required = False
    mapped_files = _mapped_files(entries, mapping) if not mapping_required else []
    target_revisions = await host._target_revisions(server, mapped_files) if mapped_files else {}
    for item in mapped_files:
        item["target_revision"] = target_revisions.get(item["target_path"], "missing")
    warnings = list(inspected["warnings"])
    if request.asset_name:
        warnings = [
            warning
            for warning in warnings
            if "select an asset explicitly" not in warning
            and "Multiple Linux release assets" not in warning
        ]
    if request.asset_name and selected.get("runtime_compatibility") == "alternative":
        warnings.append(
            "The explicitly selected Steam Runtime asset overrides the detected recommendation"
        )
    market_result = await db.execute(
        select(MarketPlugin).where(
            func.lower(MarketPlugin.github_url).in_(
                (canonical.casefold(), f"{canonical.casefold()}/")
            )
        )
    )
    market_plugin = market_result.scalar_one_or_none()
    market_plan: dict[str, Any] | None = None
    if market_plugin is None:
        warnings.append(
            "Compatibility is unknown because this repository is not in the plugin market"
        )
    else:
        market_plan = await host.build_market_plan(db, server.id, market_plugin.id, server=server)
    if mapping_required:
        warnings.append("Archive layout is ambiguous; an administrator-approved recipe is required")
    plan_core = {
        "server_id": server.id,
        "server_owner_id": server.user_id,
        "repo_url": canonical,
        "mode": request.mode,
        "config_policy": request.config_policy,
        "release_id": inspected["release"]["id"],
        "release_tag": inspected["release"]["tag"],
        "asset": selected,
        "archive_sha256": archive_sha256,
        "source_prefix": source_prefix,
        "mapping": mapping,
        "recipe_id": recipe.id if recipe else None,
        "recipe_revision": recipe_revision,
        "mapping_required": mapping_required,
        "exclude_dirs": list(request.exclude_dirs or []),
        "exclude_files": list(request.exclude_files or []),
        "plugin_metadata": plugin_metadata,
        "market_plugin_id": market_plugin.id if market_plugin else None,
        "dependencies": market_plan["dependencies"] if market_plan else [],
        "already_installed": market_plan["already_installed"] if market_plan else [],
        "hard_conflicts": market_plan["hard_conflicts"] if market_plan else [],
        "conflict_warnings": market_plan["warnings"] if market_plan else [],
        "compatibility_unknown": market_plugin is None,
        "target_revisions": target_revisions,
        "linux_runtime_profile": linux_runtime_profile,
    }
    plan_hash = hashlib.sha256(
        json.dumps(
            _github_plan_confirmation_payload(plan_core),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        **plan_core,
        "plan_hash": plan_hash,
        "release": inspected["release"],
        "files": mapped_files,
        "warnings": warnings,
    }
