"""Inspect and search public GitHub plugin repositories."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import User
from services.compat import LateBoundModule
from services.plugins.github_assets import GitHubPlanError
from services.plugins.github_plan.common import (
    README_LIMIT,
    RELEASE_NOTES_LIMIT,
    _release_payload,
    normalize_public_repo_url,
)

host = LateBoundModule("services.github_plugin_plan_service")


async def inspect_github_plugin(
    db: AsyncSession,
    user: User,
    repo_url: str,
    mode: Literal["install", "upgrade"] = "install",
    linux_runtime_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    owner, repo, canonical = normalize_public_repo_url(repo_url)
    token = await host.get_effective_github_token(db, user)
    metadata = await host._github_request(f"/repos/{owner}/{repo}", token)
    if metadata.get("private"):
        raise GitHubPlanError("Private repositories are not supported in the first release")
    if metadata.get("archived"):
        raise GitHubPlanError("Archived repositories cannot be installed automatically")
    raw_release = await host._github_request(f"/repos/{owner}/{repo}/releases/latest", token)
    release = _release_payload(raw_release)
    warnings: list[str] = []
    from services.linux_runtime_service import (
        RuntimeSelectionRequired,
        annotate_runtime_assets,
        select_unique_runtime_asset,
    )

    assets = annotate_runtime_assets(release["assets"], linux_runtime_profile)
    release["assets"] = assets
    selected: dict[str, Any] | None = None
    if assets:
        preferred = [
            item for item in assets if ("upgrade" in item["name"].casefold()) == (mode == "upgrade")
        ]
        candidates = preferred or assets
        if len(candidates) > 1:
            linux_named = [item for item in candidates if "linux" in item["name"].casefold()]
            if linux_named:
                candidates = linux_named
        try:
            selected = select_unique_runtime_asset(candidates, linux_runtime_profile)
        except RuntimeSelectionRequired as exc:
            warnings.append(str(exc))
        if selected is None and not any("select an asset explicitly" in item for item in warnings):
            warnings.append("Multiple Linux release assets require an explicit selection")
    else:
        warnings.append("No stable Linux release archive is available")
    try:
        readme = await host._github_request(
            f"/repos/{owner}/{repo}/readme",
            token,
            params={"ref": release["tag"]},
            raw=True,
        )
    except GitHubPlanError:
        readme = ""
        warnings.append("README was not available at the selected release")
    documentation = {
        "ref": release["tag"],
        "untrusted": True,
        "readme": str(readme)[:README_LIMIT],
        "release_notes": str(raw_release.get("body") or "")[:RELEASE_NOTES_LIMIT],
    }
    return {
        "repo_url": canonical,
        "repository": {
            "full_name": metadata.get("full_name"),
            "description": metadata.get("description"),
            "archived": False,
            "fork": bool(metadata.get("fork")),
            "stars": int(metadata.get("stargazers_count") or 0),
            "pushed_at": metadata.get("pushed_at"),
            "updated_at": metadata.get("updated_at"),
            "default_branch": metadata.get("default_branch"),
            "topics": metadata.get("topics") or [],
            "license": (metadata.get("license") or {}).get("spdx_id"),
        },
        "release": release,
        "selected_asset": selected,
        "documentation": documentation,
        "warnings": warnings,
        "linux_runtime_profile": linux_runtime_profile,
    }


async def search_github_plugins(
    db: AsyncSession,
    user: User,
    query: str,
    *,
    limit: int = 3,
    linux_runtime_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query = query.strip()
    if not query or len(query) > 120:
        raise GitHubPlanError("Search query must contain 1 to 120 characters")
    token = await host.get_effective_github_token(db, user)
    data = await host._github_request(
        "/search/repositories",
        token,
        params={
            "q": f"{query} cs2 in:name,description,readme archived:false is:public",
            "sort": "updated",
            "order": "desc",
            "per_page": 10,
        },
    )
    candidates: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        if len(candidates) >= 8:
            break
        url = str(item.get("html_url") or "")
        try:
            inspected = await host.inspect_github_plugin(
                db,
                user,
                url,
                linux_runtime_profile=linux_runtime_profile,
            )
        except GitHubPlanError:
            continue
        if not inspected["release"]["assets"]:
            continue
        candidates.append(
            {
                "repo_url": inspected["repo_url"],
                "full_name": inspected["repository"]["full_name"],
                "description": inspected["repository"]["description"],
                "release_tag": inspected["release"]["tag"],
                "release_published_at": inspected["release"]["published_at"],
                "pushed_at": inspected["repository"]["pushed_at"],
                "stars": inspected["repository"]["stars"],
                "linux_assets": inspected["release"]["assets"],
                "installable": inspected["selected_asset"] is not None,
            }
        )
    candidates.sort(
        key=lambda item: (
            item["installable"],
            item["release_published_at"] or "",
            item["pushed_at"] or "",
            item["stars"],
        ),
        reverse=True,
    )
    selected = candidates[: max(1, min(limit, 3))]
    return {
        "query": query,
        "candidates": selected,
        "recommended_repo_url": selected[0]["repo_url"] if selected else None,
        "linux_runtime_profile": linux_runtime_profile,
    }
