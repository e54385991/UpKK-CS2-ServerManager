"""Shared GitHub marketplace release and archive selection."""

from __future__ import annotations

import re
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from pydantic import BaseModel, Field

from modules.http_helper import http_helper
from modules.models import MarketPlugin
from services.linux_runtime_service import (
    RuntimeSelectionRequired,
    select_unique_runtime_asset,
    steam_runtime_asset_family,
    steam_runtime_for_asset,
)
from services.plugins.client_cvar_value import matches_legacy_linux_asset
from services.plugins.github_assets import GitHubPlanError, validate_download_url
from services.plugins.tracking import canonical_repo_url, derive_asset_glob, repo_api_url

ARCHIVE_EXTENSIONS = (".tar.gz", ".zip", ".tgz", ".tar", ".7z")
_INCOMPATIBLE = re.compile(
    r"(?:^|[-_. ])(?:windows|win|win32|win64|macos|osx|darwin|arm64|aarch64|i[3-6]86|"
    r"x86(?![-_]64)|source(?:[-_. ]code)?|src)(?=$|[-_. ])",
    re.IGNORECASE,
)
_AUXILIARY = re.compile(
    r"(?:^|[-_. ])(?:symbols?|debug(?:symbols)?|debuginfo|relwithdebinfo|pdb|"
    r"upgrade|update[-_. ]only)(?=$|[-_. ])",
    re.IGNORECASE,
)
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)
_PAYLOAD_SUFFIXES = {".so", ".dll", ".vdf", ".smx"}


class SyncedAsset(BaseModel):
    id: str
    name: str
    url: str
    sha256: str
    entrypoints: list[str] = Field(default_factory=list)


class MarketReleaseSync(BaseModel):
    revision: str
    repository: str
    release_id: str
    tag: str
    assets: list[SyncedAsset]


def market_release_sync(plugin: MarketPlugin) -> MarketReleaseSync | None:
    raw = getattr(plugin, "release_sync", None)
    if not raw:
        return None
    saved = MarketReleaseSync.model_validate(raw)
    return saved if saved.repository == canonical_repo_url(plugin.github_url).casefold() else None


def archive_kind(name: str) -> str:
    if not name.casefold().endswith(ARCHIVE_EXTENSIONS) or _INCOMPATIBLE.search(name):
        return "incompatible"
    return "auxiliary" if _AUXILIARY.search(name) else "ordinary"


def release_assets(release: dict[str, Any], *, manual: bool = False) -> list[dict[str, Any]]:
    """Normalize uploaded archives. GitHub-generated source archives are not assets."""
    if release.get("draft"):
        return []
    candidates = []
    for raw in release.get("assets") or []:
        if not isinstance(raw, dict) or raw.get("state", "uploaded") != "uploaded":
            continue
        name = str(raw.get("name") or "")
        url = str(raw.get("browser_download_url") or "")
        kind = archive_kind(name)
        if not url or kind == "incompatible" or (kind != "ordinary" and not manual):
            continue
        candidates.append(
            {
                "id": str(raw.get("id") or ""),
                "name": name,
                "url": url,
                "size": int(raw.get("size") or 0),
                "digest": raw.get("digest"),
                "content_type": raw.get("content_type"),
            }
        )
    return sorted(candidates, key=lambda asset: asset["name"].casefold())


def match_archives(
    repo: str, assets: list[dict[str, Any]], pattern: str | None
) -> list[dict[str, Any]]:
    if not pattern:
        return assets
    matches = [
        asset for asset in assets if fnmatchcase(asset["name"].casefold(), pattern.casefold())
    ]
    return matches or [
        asset for asset in assets if matches_legacy_linux_asset(repo, pattern, asset["name"])
    ]


def release_family(assets: list[dict[str, Any]]) -> bool:
    if len(assets) == 1:
        return True
    return (
        len(assets) == 2
        and {steam_runtime_for_asset(asset["name"]) for asset in assets} == {"steamrt3", "steamrt4"}
        and len({steam_runtime_asset_family(asset["name"]) for asset in assets}) == 1
    )


def select_release_asset(
    assets: list[dict[str, Any]], profile: dict[str, Any] | None
) -> dict[str, Any]:
    """Require a unique package or a single compatible Steam Runtime family."""
    if not release_family(assets):
        names = ", ".join(asset["name"] for asset in assets[:10])
        raise GitHubPlanError(
            f"Expected one compatible release package; found {len(assets)}: {names}"
        )
    try:
        selected = select_unique_runtime_asset(assets, profile)
    except RuntimeSelectionRequired as exc:
        raise GitHubPlanError(str(exc)) from exc
    assert selected is not None
    runtime = steam_runtime_for_asset(selected["name"])
    recommended = (profile or {}).get("recommended_steam_runtime")
    if runtime and recommended != runtime:
        reason = f"server uses {recommended}" if recommended else "server runtime is unknown"
        raise GitHubPlanError(f"{selected['name']} requires {runtime}; {reason}")
    return selected


def suggest_asset_glob(assets: list[dict[str, Any]], tag: str) -> str | None:
    if not release_family(assets):
        return None
    pattern = derive_asset_glob(assets[0]["name"], tag)
    if pattern and len(assets) == 2:
        pattern = re.sub(r"(steam[-_.]?rt[-_.]?)[34]", r"\g<1>[34]", pattern, flags=re.I)
    return pattern


def validate_repository_asset(repo: str, url: str) -> None:
    validate_download_url(url)
    expected = urlsplit(repo).path.rstrip("/").removesuffix(".git").casefold()
    actual = urlsplit(url).path.split("/releases/download/", 1)[0].casefold()
    if actual != expected:
        raise GitHubPlanError("Release archive belongs to a different repository")


async def fetch_release(
    repo: str, token: str | None, *, release_id: str | None = None, tag: str | None = None
) -> dict[str, Any]:
    url = repo_api_url(repo)
    if release_id:
        if not release_id.isascii() or not release_id.isdecimal():
            raise GitHubPlanError("Invalid GitHub release ID")
        url = url.removesuffix("latest") + release_id
    if tag:
        url = url.removesuffix("latest") + "tags/" + quote(tag, safe="")
    success, data, error = await http_helper.get(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"},
        timeout=30,
        github_token=token,
    )
    if not success or not isinstance(data, dict):
        raise GitHubPlanError(f"Failed to fetch GitHub release: {error or 'invalid response'}")
    return data


def mapped_entrypoints(layout: dict[str, Any], *, target: str | None = None) -> list[str]:
    mapping = layout.get("mapping") or []
    if target:
        mapping = [{"source": layout.get("source_prefix") or ".", "target": target}]
    paths: set[str] = set()
    for entry in layout.get("entries") or []:
        path = PurePosixPath(entry["path"])
        if entry.get("is_dir") or path.suffix.casefold() not in _PAYLOAD_SUFFIXES:
            continue
        for rule in mapping:
            source = PurePosixPath(rule["source"])
            if path.is_relative_to(source):
                paths.add(str(PurePosixPath(rule["target"]) / path.relative_to(source)))
                break
    return sorted(paths)


def compatible_entrypoints(previous: list[str], current: list[str]) -> bool:
    # Compare mapped runtime files so an unrelated package with a shared
    # dependency cannot inherit the prior installation's identity.
    return bool(previous) and set(previous) == set(current)


async def newer_market_release(
    repo: str, token: str | None, installed_tag: str, release: dict[str, Any]
) -> bool:
    tag = str(release.get("tag_name") or "")
    installed_match = _VERSION.fullmatch(installed_tag)
    offered_match = _VERSION.fullmatch(tag)
    if installed_match and offered_match:
        return tuple(map(int, offered_match.groups())) > tuple(map(int, installed_match.groups()))
    if installed_tag in {"", "unknown"}:
        raise GitHubPlanError("Installed version is unknown; confirm it with a manual installation")
    previous = await fetch_release(repo, token, tag=installed_tag)
    try:
        installed_at = datetime.fromisoformat(previous["published_at"])
        offered_at = datetime.fromisoformat(release["published_at"])
        return offered_at > installed_at
    except (KeyError, TypeError, ValueError) as exc:
        raise GitHubPlanError("Unable to confirm which release is newer") from exc


def url_tag(url: str) -> str:
    value = urlsplit(url).path.split("/releases/download/", 1)[-1]
    tag, _, _asset = value.rpartition("/")
    return unquote(tag)


def verified_snapshot_assets(
    snapshot: MarketReleaseSync, release: dict[str, Any]
) -> list[dict[str, Any]]:
    if (
        release.get("draft")
        or release.get("prerelease")
        or str(release.get("id") or "") != snapshot.release_id
        or release.get("tag_name") != snapshot.tag
    ):
        raise GitHubPlanError("The synchronized GitHub release changed; preview it again")
    by_id = {asset["id"]: asset for asset in release_assets(release)}
    verified = []
    for saved in snapshot.assets:
        asset = by_id.get(saved.id)
        if (
            asset is None
            or asset["name"] != saved.name
            or asset["url"] != saved.url
            or (asset.get("digest") and asset["digest"] != f"sha256:{saved.sha256}")
        ):
            raise GitHubPlanError("A synchronized release asset changed; preview it again")
        verified.append(asset)
    return verified
