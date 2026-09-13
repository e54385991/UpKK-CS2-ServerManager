"""Shared GitHub plan primitives."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from services.compat import LateBoundModule
from services.plugins.github_assets import GitHubPlanError

host = LateBoundModule("services.github_plugin_plan_service")

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ARCHIVE_EXTENSIONS = (".tar.gz", ".tgz", ".tar", ".zip", ".7z")
MAX_AUTOMATIC_FILES = 5_000
README_LIMIT = 8_000
RELEASE_NOTES_LIMIT = 4_000
PLAN_CACHE_SECONDS = 30 * 60
PANEL_MANAGED_FRAMEWORK_REPOSITORIES = {
    ("alliedmodders", "metamod-source"): "Metamod:Source",
    ("roflmuffin", "counterstrikesharp"): "CounterStrikeSharp",
}


def _panel_managed_framework(owner: str, repository: str) -> str | None:
    normalized_repository = repository.casefold()
    if normalized_repository == "counterstrikesharp":
        return "CounterStrikeSharp"
    if normalized_repository in {"metamod", "metamod-source"}:
        return "Metamod:Source"
    return PANEL_MANAGED_FRAMEWORK_REPOSITORIES.get((owner.casefold(), normalized_repository))


def _post_install_restart_payload(required: bool) -> dict[str, Any]:
    if not required:
        return {"restart_required": False}
    return {
        "restart_required": True,
        "next_step": (
            "Restart (or start) the server and wait for startup before searching for, reading, "
            "or patching generated configuration files."
        ),
    }


def _github_plan_confirmation_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """Exclude live inventory evidence while retaining approval-critical state."""
    return {key: value for key, value in plan.items() if key != "already_installed"}


def normalize_public_repo_url(value: str) -> tuple[str, str, str]:
    parsed = urlsplit(value.strip())
    try:
        has_port = parsed.port is not None
    except ValueError as exc:
        raise GitHubPlanError("Invalid GitHub repository port") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or has_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GitHubPlanError("Only canonical public HTTPS GitHub repository URLs are supported")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise GitHubPlanError("GitHub URL must identify exactly one owner and repository")
    owner, repo = parts
    repo = repo.removesuffix(".git")
    if not REPO_RE.fullmatch(owner) or not REPO_RE.fullmatch(repo):
        raise GitHubPlanError("Invalid GitHub owner or repository name")
    return owner, repo, f"https://github.com/{owner}/{repo}"


def _headers(token: str | None, *, raw: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "User-Agent": "UpKK-CS2-ServerManager",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _github_request(
    path: str,
    token: str | None,
    *,
    params: dict[str, Any] | None = None,
    raw: bool = False,
) -> Any:
    if not path.startswith("/") or "//" in path:
        raise GitHubPlanError("Invalid GitHub API path")
    url = f"https://api.github.com{path}"
    async with host.httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.get(url, headers=_headers(token, raw=raw), params=params)
    if response.status_code == 404:
        raise GitHubPlanError("Public GitHub repository or release was not found")
    if response.status_code >= 400:
        raise GitHubPlanError(f"GitHub API request failed with HTTP {response.status_code}")
    return response.text if raw else response.json()


def _is_linux_archive(name: str) -> bool:
    lowered = name.casefold()
    if not lowered.endswith(ARCHIVE_EXTENSIONS):
        return False
    blocked = ("windows", "win32", "win64", "symbols", "debug", "source code")
    return not any(marker in lowered for marker in blocked) and (
        "linux" in lowered or not any(marker in lowered for marker in ("win", "macos", "osx"))
    )


def _release_payload(release: dict[str, Any]) -> dict[str, Any]:
    if release.get("draft") or release.get("prerelease"):
        raise GitHubPlanError("Only stable, published GitHub releases are supported")
    assets = []
    for item in release.get("assets") or []:
        name = str(item.get("name") or "")
        if _is_linux_archive(name):
            assets.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": name,
                    "url": str(item.get("browser_download_url") or ""),
                    "size": int(item.get("size") or 0),
                    "digest": item.get("digest"),
                    "content_type": item.get("content_type"),
                }
            )
    return {
        "id": str(release.get("id") or ""),
        "tag": str(release.get("tag_name") or ""),
        "name": release.get("name"),
        "published_at": release.get("published_at"),
        "target_commitish": release.get("target_commitish"),
        "assets": assets,
    }
