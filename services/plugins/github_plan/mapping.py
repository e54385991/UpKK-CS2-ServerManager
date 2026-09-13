"""Archive mapping and recipe lookup for GitHub install plans."""

from __future__ import annotations

import posixpath
import shlex
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import GitHubInstallRecipe, User
from services.compat import LateBoundModule
from services.plugins import release_archive
from services.plugins.github_assets import GitHubPlanError
from services.plugins.github_plan.common import MAX_AUTOMATIC_FILES

host = LateBoundModule("services.github_plugin_plan_service")


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
    manager = host.SSHManager()
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
    return await release_archive.inspect_release_asset_layout(
        asset,
        repo_name,
        download=host._download_release_asset,
        read_entries=host._archive_entries,
    )
