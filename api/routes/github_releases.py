"""Focused route implementation; the original module retains patchable dependencies."""

from __future__ import annotations

from typing import Optional

from api.dependencies import (
    ActiveUser,
    DatabaseSession,
)
from modules import (
    GitHubRelease,
    GitHubReleasesResponse,
)
from services.compat import LateBoundModule

host = LateBoundModule("api.routes.github_plugins")
router = host.router


def _release_asset_payloads(release_data: dict[str, object]) -> list[dict[str, object]]:
    asset_payloads = []
    raw_assets = release_data.get("assets", [])
    assets_data = raw_assets if isinstance(raw_assets, list) else []
    for raw_asset in assets_data:
        if not isinstance(raw_asset, dict):
            continue
        asset_data: dict[str, object] = raw_asset
        asset_name = str(asset_data.get("name") or "")
        asset_name_lower = asset_name.lower()

        # Skip Windows-specific archives (filename contains 'windows' or 'win')
        if (
            "windows" in asset_name_lower
            or "-win-" in asset_name_lower
            or "_win_" in asset_name_lower
            or asset_name_lower.endswith("-win.zip")
        ):
            continue

        # Only include archive files that could be plugins (including 7z)
        if any(
            asset_name_lower.endswith(ext) for ext in [".zip", ".tar.gz", ".tgz", ".tar", ".7z"]
        ):
            asset_payloads.append(
                {
                    "name": asset_name,
                    "browser_download_url": asset_data.get("browser_download_url", ""),
                    "size": asset_data.get("size", 0),
                    "content_type": asset_data.get("content_type"),
                }
            )

    return asset_payloads


@router.get("/releases")
async def get_github_releases(
    repo_url: str,
    count: int = 5,
    server_id: Optional[int] = None,
    *,
    db: DatabaseSession,
    current_user: ActiveUser,
) -> GitHubReleasesResponse:
    """
    Fetch recent releases from a GitHub repository.

    Args:
        repo_url: GitHub repository URL (e.g., https://github.com/Source2ZE/CS2Fixes)
        count: Number of releases to fetch (default: 5, max: 10)
        server_id: Optional server ID to use server's GitHub proxy configuration

    Returns:
        List of releases with their assets
    """
    try:
        owner, repo = host.parse_github_url(repo_url)
    except ValueError as e:
        return host.GitHubReleasesResponse(success=False, error=str(e), releases=[])

    # A server context is authorization-only. GitHub credentials are never sent
    # through a user-configured proxy.
    github_proxy = None
    server = None
    if server_id:
        from services.ai_access import authorized_server

        server = await authorized_server(db, current_user, server_id)

    linux_runtime_profile: dict[str, object] | None = None
    if server is not None:
        from services.linux_runtime_service import detect_linux_runtime_profile

        linux_runtime_profile = await detect_linux_runtime_profile(server)

    # Limit count to prevent abuse
    count = min(count, 10)

    # Fetch releases from GitHub API
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"}

    # Prefer the user's token and use the system credential only as a fallback.
    github_token = await host.get_effective_github_token(db, current_user)

    success, data, error = await host.http_helper.get(
        api_url,
        headers=headers,
        params={"per_page": count},
        timeout=30,
        proxy=github_proxy,
        github_token=github_token,
    )

    if not success:
        return host.GitHubReleasesResponse(
            success=False,
            error=f"Failed to fetch releases: {error}",
            releases=[],
            repo_owner=owner,
            repo_name=repo,
        )

    if not isinstance(data, list):
        return host.GitHubReleasesResponse(
            success=False,
            error="Unexpected response format from GitHub API",
            releases=[],
            repo_owner=owner,
            repo_name=repo,
        )

    # Parse releases
    releases: list[GitHubRelease] = []
    for raw_release in data:
        if not isinstance(raw_release, dict):
            continue
        release_data: dict[str, object] = raw_release
        if release_data.get("draft") or release_data.get("prerelease"):
            continue
        asset_payloads = _release_asset_payloads(release_data)

        from services.linux_runtime_service import annotate_runtime_assets

        assets = [
            host.GitHubReleaseAsset.model_validate(item)
            for item in annotate_runtime_assets(asset_payloads, linux_runtime_profile)
        ]

        # Only include releases that have downloadable assets
        if assets:
            releases.append(
                host.GitHubRelease(
                    id=str(release_data.get("id") or ""),
                    tag_name=release_data.get("tag_name", ""),
                    name=release_data.get("name"),
                    published_at=release_data.get("published_at"),
                    prerelease=release_data.get("prerelease", False),
                    assets=assets,
                )
            )
            if len(releases) >= count:
                break

    from modules import LinuxRuntimeProfile

    return host.GitHubReleasesResponse(
        success=True,
        releases=releases,
        repo_owner=owner,
        repo_name=repo,
        linux_runtime_profile=(
            LinuxRuntimeProfile.model_validate(linux_runtime_profile)
            if linux_runtime_profile is not None
            else None
        ),
    )
