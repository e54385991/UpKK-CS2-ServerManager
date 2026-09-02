"""Safe GitHub discovery and immutable release-install planning."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import posixpath
import re
import shlex
import stat
import tarfile
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import (
    GitHubInstallRecipe,
    ManagedPlugin,
    ManagedPluginFile,
    MarketPlugin,
    User,
)
from modules.schemas.plugins import GitHubPluginInstallPlanRequest, GitHubPluginInstallRequest
from services.ai_access import authorized_server
from services.github_credentials import get_effective_github_token
from services.maintenance_lock import maintenance_lock_service
from services.plugin_installation import install_github_plugin_with_retry
from services.plugins.github_assets import (
    GitHubPlanError,
    download_release_asset,
    validate_download_url,
)
from services.plugins.market_integration import (
    build_market_plan,
    execute_market_plan,
)
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)
ProgressCallback = Callable[..., Awaitable[None]]
_download_release_asset = download_release_asset
_validate_download_url = validate_download_url

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ARCHIVE_EXTENSIONS = (".tar.gz", ".tgz", ".tar", ".zip", ".7z")
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
MAX_AUTOMATIC_FILES = 5_000
MAX_COMPRESSION_RATIO = 200
README_LIMIT = 8_000
RELEASE_NOTES_LIMIT = 4_000
PLAN_CACHE_SECONDS = 30 * 60
PANEL_MANAGED_FRAMEWORK_REPOSITORIES = {
    ("alliedmodders", "metamod-source"): "Metamod:Source",
    ("roflmuffin", "counterstrikesharp"): "CounterStrikeSharp",
}
BLOCKED_RELEASE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".exe",
    ".exp",
    ".ilk",
    ".lib",
    ".ps1",
    ".sh",
    ".sln",
    ".csproj",
    ".vcxproj",
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
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
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


async def inspect_github_plugin(
    db: AsyncSession,
    user: User,
    repo_url: str,
    mode: Literal["install", "upgrade"] = "install",
    linux_runtime_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    owner, repo, canonical = normalize_public_repo_url(repo_url)
    token = await get_effective_github_token(db, user)
    metadata = await _github_request(f"/repos/{owner}/{repo}", token)
    if metadata.get("private"):
        raise GitHubPlanError("Private repositories are not supported in the first release")
    if metadata.get("archived"):
        raise GitHubPlanError("Archived repositories cannot be installed automatically")
    raw_release = await _github_request(f"/repos/{owner}/{repo}/releases/latest", token)
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
        readme = await _github_request(
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
    token = await get_effective_github_token(db, user)
    data = await _github_request(
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
            inspected = await inspect_github_plugin(
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


def _safe_entry_name(name: str) -> str:
    value = name.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value) is not None
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise GitHubPlanError(f"Unsafe archive path: {name[:200]}")
    return path.as_posix().rstrip("/")


def _stream_sha256(handle: Any) -> str:
    digest = hashlib.sha256()
    try:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    finally:
        handle.close()
    return digest.hexdigest()


def _zip_entries(path: str) -> list[dict[str, Any]]:
    entries = []
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            mode = item.external_attr >> 16
            if any(
                predicate(mode)
                for predicate in (
                    stat.S_ISLNK,
                    stat.S_ISCHR,
                    stat.S_ISBLK,
                    stat.S_ISFIFO,
                    stat.S_ISSOCK,
                )
            ):
                raise GitHubPlanError("Archive links and special files are not allowed")
            entries.append(
                {
                    "path": _safe_entry_name(item.filename),
                    "size": int(item.file_size),
                    "is_dir": item.is_dir(),
                    "sha256": None if item.is_dir() else _stream_sha256(archive.open(item, "r")),
                }
            )
    return entries


def _tar_entries(path: str) -> list[dict[str, Any]]:
    entries = []
    with tarfile.open(path, "r:*") as archive:
        for item in archive.getmembers():
            if item.issym() or item.islnk() or item.isdev():
                raise GitHubPlanError("Archive links and device entries are not allowed")
            if not (item.isfile() or item.isdir()):
                raise GitHubPlanError("Unsupported archive entry type")
            extracted = archive.extractfile(item) if item.isfile() else None
            entries.append(
                {
                    "path": _safe_entry_name(item.name),
                    "size": int(item.size),
                    "is_dir": item.isdir(),
                    "sha256": _stream_sha256(extracted) if extracted is not None else None,
                }
            )
    return entries


def _seven_entries(path: str) -> list[dict[str, Any]]:
    try:
        import py7zr
    except ImportError as exc:  # pragma: no cover - dependency is explicit
        raise GitHubPlanError("7z inspection support is unavailable") from exc
    entries = []
    with py7zr.SevenZipFile(path, mode="r") as archive:
        for item in archive.list():
            if any(
                bool(getattr(item, attribute, False))
                for attribute in ("is_symlink", "is_junction", "is_hardlink")
            ):
                raise GitHubPlanError("Archive links are not allowed")
            is_dir = bool(getattr(item, "is_directory", False))
            entries.append(
                {
                    "path": _safe_entry_name(item.filename),
                    "size": 0 if is_dir else int(getattr(item, "uncompressed", 0) or 0),
                    "is_dir": is_dir,
                    "sha256": None,
                }
            )
    return entries


def _validate_archive_entries(entries: list[dict[str, Any]], compressed_size: int) -> None:
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise GitHubPlanError("Archive contains too many entries")
    total = sum(item["size"] for item in entries if not item["is_dir"])
    if total > MAX_EXPANDED_BYTES:
        raise GitHubPlanError("Archive expanded size exceeds 2 GiB")
    if compressed_size and total > compressed_size * MAX_COMPRESSION_RATIO:
        raise GitHubPlanError("Archive compression ratio is unsafe")
    folded: set[str] = set()
    for item in entries:
        key = item["path"].casefold()
        if key in folded:
            raise GitHubPlanError("Archive contains case-colliding paths")
        folded.add(key)


def _archive_entries(path: str, asset_name: str, compressed_size: int) -> list[dict[str, Any]]:
    lowered = asset_name.casefold()
    if lowered.endswith(".zip"):
        entries = _zip_entries(path)
    elif lowered.endswith((".tar.gz", ".tgz", ".tar")):
        entries = _tar_entries(path)
    elif lowered.endswith(".7z"):
        entries = _seven_entries(path)
    else:
        raise GitHubPlanError("Unsupported release archive type")
    _validate_archive_entries(entries, compressed_size)
    return entries


def _validate_release_contents(entries: list[dict[str, Any]]) -> None:
    """Reject build/script payloads; release plans only deploy runtime files."""
    for item in entries:
        if item["is_dir"]:
            continue
        path = item["path"]
        lowered = path.casefold()
        suffix = posixpath.splitext(lowered)[1]
        basename = posixpath.basename(lowered)
        if (
            suffix in BLOCKED_RELEASE_SUFFIXES
            or basename in {"cmakelists.txt", "makefile"}
            or "/.git/" in f"/{lowered}/"
        ):
            raise GitHubPlanError(
                "Release contains source-build, script, Windows, or debug artifacts"
            )


def _detect_mapping(
    entries: list[dict[str, Any]], repo_name: str
) -> tuple[str | None, list[dict[str, str]], bool]:
    paths = [item["path"] for item in entries]
    parts = [path.split("/") for path in paths]
    prefixes = ["", "game/csgo", "csgo"]
    first_segments = sorted({item[0] for item in parts if item})
    for first in first_segments:
        prefixes.extend([first, f"{first}/game/csgo", f"{first}/csgo"])
    seen: set[str] = set()
    for prefix in prefixes:
        prefix = prefix.strip("/")
        if prefix in seen:
            continue
        seen.add(prefix)
        base = f"{prefix}/" if prefix else ""
        roots = [
            root
            for root in ("addons", "cfg")
            if any(path == f"{base}{root}" or path.startswith(f"{base}{root}/") for path in paths)
        ]
        if "addons" in roots:
            return (
                prefix or None,
                [{"source": f"{base}{root}", "target": root} for root in roots],
                False,
            )

    # Some CounterStrikeSharp releases intentionally omit the outer addons/
    # directory. Preserve the whole framework subtree because it may include
    # shared libraries and multiple companion plugin modules.
    framework_prefixes = ["counterstrikesharp"]
    framework_prefixes.extend(f"{first}/counterstrikesharp" for first in first_segments)
    for prefix in framework_prefixes:
        base = f"{prefix.strip('/')}/"
        if any(
            path.casefold().startswith(f"{base.casefold()}plugins/")
            and path.casefold().endswith(".dll")
            for path in paths
        ):
            return (
                prefix,
                [{"source": prefix, "target": "addons/counterstrikesharp"}],
                False,
            )

    # A release may contain plugins/<name>/ without the CounterStrikeSharp
    # wrapper. Map the entire plugins tree so companion modules are retained.
    plugin_prefixes = ["plugins"]
    plugin_prefixes.extend(f"{first}/plugins" for first in first_segments)
    for prefix in plugin_prefixes:
        base = f"{prefix.strip('/')}/"
        if any(
            path.casefold().startswith(base.casefold()) and path.casefold().endswith(".dll")
            for path in paths
        ):
            return (
                prefix,
                [{"source": prefix, "target": "addons/counterstrikesharp/plugins"}],
                False,
            )

    files = [item for item in entries if not item["is_dir"]]
    flat_prefixes = [""]
    flat_prefixes.extend(first_segments)
    for prefix in flat_prefixes:
        base = f"{prefix}/" if prefix else ""
        direct_files = [
            item["path"]
            for item in files
            if item["path"].startswith(base) and "/" not in item["path"][len(base) :]
        ]
        if any(path.casefold().endswith(".dll") for path in direct_files) and any(
            path.casefold().endswith(".deps.json") for path in direct_files
        ):
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo_name).strip(".-")
            target = f"addons/counterstrikesharp/plugins/{safe_name}"
            source = prefix or "."
            return prefix or None, [{"source": source, "target": target}], False

    root_dirs = {
        item["path"].strip("/").split("/")[0]
        for item in entries
        if item["path"].strip("/") and "/" not in item["path"].strip("/") and item["is_dir"]
    }
    if "metamod" in root_dirs and any(
        path.casefold().startswith("metamod/") and path.casefold().endswith(".vdf")
        for path in paths
    ):
        return None, [{"source": ".", "target": "addons"}], False
    return None, [], True


def _apply_user_mapping(
    entries: list[dict[str, Any]],
    source_prefix: str | None,
    target_prefix: str | None,
) -> tuple[str | None, list[dict[str, str]]]:
    """Resolve an operator-chosen archive prefix onto addons/ or cfg/."""
    source = (source_prefix or "").replace("\\", "/").strip().strip("/")
    target = (target_prefix or "").replace("\\", "/").strip().strip("/")
    if not target:
        raise GitHubPlanError("target_prefix is required when mapping an archive")
    if (
        source.startswith("/")
        or target.startswith("/")
        or ".." in source.split("/")
        or ".." in target.split("/")
        or any(ord(character) < 32 for character in f"{source}{target}")
    ):
        raise GitHubPlanError("Archive mapping prefixes are unsafe")
    root = target.split("/", 1)[0]
    if root not in {"addons", "cfg"}:
        raise GitHubPlanError("target_prefix must start with addons or cfg")
    paths = [str(item.get("path") or "").replace("\\", "/").strip("/") for item in entries]
    if source:
        exists = any(path == source or path.startswith(f"{source}/") for path in paths)
        if not exists:
            raise GitHubPlanError("source_prefix was not found in the archive")
    return source or None, [{"source": source or ".", "target": target}]


def _infer_plugin_metadata(
    entries: list[dict[str, Any]], documentation: dict[str, Any]
) -> dict[str, Any]:
    paths = [item["path"].casefold() for item in entries if not item["is_dir"]]
    readme = str(documentation.get("readme") or "").casefold()
    is_css = any(
        path.endswith(".dll")
        and (
            "addons/counterstrikesharp/plugins/" in path
            or "/counterstrikesharp/plugins/" in f"/{path}"
            or "/plugins/" in f"/{path}"
        )
        for path in paths
    )
    is_metamod = any("addons/metamod/" in path or path.endswith(".vdf") for path in paths)
    framework = "counterstrikesharp" if is_css else ("metamod" if is_metamod else None)
    dependencies: list[dict[str, str]] = []
    if framework == "counterstrikesharp":
        dependencies.append(
            {"key": "counterstrikesharp", "basis": "release archive path structure"}
        )
    elif framework == "metamod":
        dependencies.append({"key": "metamod", "basis": "release archive path structure"})
    documentation_hints = []
    if "metamod" in readme:
        documentation_hints.append("README mentions Metamod")
    if "counterstrikesharp" in readme:
        documentation_hints.append("README mentions CounterStrikeSharp")
    return {
        "framework": framework,
        "dependencies": dependencies,
        "documentation_hints": documentation_hints,
        "untrusted_documentation": True,
    }


def _mapped_files(
    entries: list[dict[str, Any]], mapping: list[dict[str, str]]
) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    folded_targets: set[str] = set()
    for entry in entries:
        if entry["is_dir"]:
            continue
        archive_path = entry["path"]
        for rule in mapping:
            source = rule["source"].strip("/")
            if source in {"", "."}:
                remainder = archive_path
            elif archive_path == source:
                remainder = posixpath.basename(archive_path)
            elif archive_path.startswith(f"{source}/"):
                remainder = archive_path[len(source) + 1 :]
            else:
                continue
            target = posixpath.normpath(posixpath.join(rule["target"], remainder))
            if target.startswith("../") or target.split("/", 1)[0] not in {"addons", "cfg"}:
                raise GitHubPlanError("Archive mapping escaped the approved CS2 target roots")
            folded = target.casefold()
            if folded in folded_targets:
                raise GitHubPlanError("Archive mapping produced duplicate target paths")
            folded_targets.add(folded)
            parts = target.casefold().split("/")
            extension = posixpath.splitext(target)[1].casefold()
            is_config = (
                target.casefold().startswith("cfg/")
                or extension
                in {".cfg", ".conf", ".ini", ".json", ".jsonc", ".toml", ".yaml", ".yml"}
            ) and "gamedata" not in parts
            source_relative = (
                archive_path if source in {"", "."} else archive_path[len(source) :].lstrip("/")
            )
            mapped.append(
                {
                    "archive_path": archive_path,
                    "source_relative": source_relative,
                    "target_path": target,
                    "install_relative": (
                        target
                        if rule["target"].split("/", 1)[0] in {"addons", "cfg"}
                        else source_relative
                    ),
                    "size": entry["size"],
                    "sha256": entry.get("sha256"),
                    "file_role": "config"
                    if is_config
                    else ("gamedata" if "gamedata" in parts else "data"),
                }
            )
            break
    if len(mapped) > MAX_AUTOMATIC_FILES:
        raise GitHubPlanError(
            "Archive maps more than 5,000 files and cannot be installed automatically"
        )
    return mapped


async def _target_revisions(server: Any, files: list[dict[str, Any]]) -> dict[str, str]:
    manager = SSHManager()
    connected, message = await manager.connect(server)
    if not connected:
        raise GitHubPlanError(f"SSH connection failed while revisioning targets: {message}")
    csgo = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo")
    revisions: dict[str, str] = {}
    try:
        for offset in range(0, len(files), 100):
            commands = []
            for item in files[offset : offset + 100]:
                relative = item["target_path"]
                absolute = posixpath.join(csgo, relative)
                label = shlex.quote(relative)
                path = shlex.quote(absolute)
                commands.append(
                    f"if test -L {path}; then printf '%s\\tsymlink\\n' {label}; "
                    f"elif test -f {path}; then printf '%s\\t' {label}; sha256sum -- {path} | awk '{{print $1}}'; "
                    f"elif test -e {path}; then printf '%s\\tspecial\\n' {label}; "
                    f"else printf '%s\\tmissing\\n' {label}; fi"
                )
            success, stdout, stderr = await manager.execute_command("; ".join(commands), timeout=60)
            if not success:
                raise GitHubPlanError(stderr or stdout or "Unable to revision install targets")
            for line in stdout.splitlines():
                relative, separator, revision = line.partition("\t")
                if separator and relative:
                    if revision in {"symlink", "special"}:
                        raise GitHubPlanError(
                            f"Install target is not a regular file: {relative[:300]}"
                        )
                    revisions[relative] = revision
    finally:
        await manager.disconnect()
    return revisions


async def _recipe_for_plan(
    db: AsyncSession, user: User, repo_url: str, recipe_id: int | None
) -> GitHubInstallRecipe | None:
    if recipe_id is None:
        return None
    result = await db.execute(
        select(GitHubInstallRecipe).where(
            GitHubInstallRecipe.id == recipe_id,
            GitHubInstallRecipe.repo_url == repo_url,
            col(GitHubInstallRecipe.is_enabled).is_(True),
        )
    )
    recipe = result.scalar_one_or_none()
    if recipe is None:
        raise GitHubPlanError("Approved GitHub installation recipe was not found")
    return recipe


async def inspect_release_asset_layout(asset: dict[str, Any], repo_name: str) -> dict[str, Any]:
    """Download and safely derive an install mapping for one release asset."""
    archive_path, archive_sha256, compressed_size = await _download_release_asset(asset["url"])
    try:
        try:
            entries = await asyncio.to_thread(
                _archive_entries, archive_path, asset["name"], compressed_size
            )
        except GitHubPlanError:
            raise
        except Exception as exc:
            raise GitHubPlanError("Release archive could not be safely inspected") from exc
    finally:
        try:
            os.unlink(archive_path)
        except OSError:
            pass
    _validate_release_contents(entries)
    source_prefix, mapping, mapping_required = _detect_mapping(entries, repo_name)
    return {
        "archive_sha256": archive_sha256,
        "compressed_size": compressed_size,
        "entries": entries,
        "source_prefix": source_prefix,
        "mapping": mapping,
        "mapping_required": mapping_required,
    }


async def build_github_install_plan(
    db: AsyncSession,
    user: User,
    server_id: int,
    request: GitHubPluginInstallPlanRequest,
) -> dict[str, Any]:
    server = await authorized_server(db, user, server_id)
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
    inspected = await inspect_github_plugin(
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
    layout = await inspect_release_asset_layout(selected, repo_name)
    archive_sha256 = layout["archive_sha256"]
    entries = layout["entries"]
    source_prefix = layout["source_prefix"]
    mapping = layout["mapping"]
    mapping_required = layout["mapping_required"]
    plugin_metadata = _infer_plugin_metadata(entries, inspected["documentation"])
    recipe = await _recipe_for_plan(db, user, canonical, request.recipe_id)
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
    target_revisions = await _target_revisions(server, mapped_files) if mapped_files else {}
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
        market_plan = await build_market_plan(db, server.id, market_plugin.id, server=server)
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
    async with maintenance_lock_service.get(
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
    server = await authorized_server(db, user, server_id)
    plan = await build_github_install_plan(db, user, server_id, request)
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
            dependency_plan = await build_market_plan(db, server.id, dependency_id, server=server)
            dependency_result = await execute_market_plan(
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
        current_revisions = await _target_revisions(server, plan["files"])
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
    result = await install_github_plugin_with_retry(
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
