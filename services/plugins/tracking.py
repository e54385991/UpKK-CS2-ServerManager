"""Persistence helpers for plugin installation tracking."""

from __future__ import annotations

import re

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.database import async_session_maker
from modules.models import ManagedPlugin


def canonical_repo_url(repo_url: str) -> str:
    match = re.match(
        r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$",
        (repo_url or "").strip(),
    )
    if not match:
        raise ValueError("Invalid GitHub repository URL")
    return f"https://github.com/{match.group(1)}/{match.group(2).removesuffix('.git')}"


def repo_api_url(repo_url: str) -> str:
    canonical = canonical_repo_url(repo_url)
    owner, repo = canonical.removeprefix("https://github.com/").split("/", 1)
    return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"


def derive_asset_glob(asset_name: str | None, release_tag: str | None) -> str | None:
    if not asset_name:
        return None
    pattern = asset_name
    if release_tag:
        candidates = {release_tag, release_tag.lstrip("vV")}
        for candidate in sorted((value for value in candidates if value), key=len, reverse=True):
            pattern = re.sub(re.escape(candidate), "*", pattern, flags=re.IGNORECASE)
    return pattern if pattern != asset_name else asset_name


async def upsert_managed_plugin(
    *,
    server_id: int,
    source_type: str,
    source_key: str,
    display_name: str,
    repo_url: str | None = None,
    market_plugin_id: int | None = None,
    framework_key: str | None = None,
    installed_release_id: str | None = None,
    installed_version: str = "unknown",
    installed_asset_name: str | None = None,
    asset_glob: str | None = None,
    custom_install_path: str | None = None,
    exclude_dirs: list[str] | None = None,
    exclude_files: list[str] | None = None,
) -> ManagedPlugin:
    """Create or refresh tracking metadata without enabling automatic updates."""
    async with async_session_maker() as db:
        result = await db.execute(
            select(ManagedPlugin).where(
                ManagedPlugin.server_id == server_id,
                ManagedPlugin.source_type == source_type,
                ManagedPlugin.source_key == source_key,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            item = ManagedPlugin(
                server_id=server_id,
                source_type=source_type,
                source_key=source_key,
                display_name=display_name,
                auto_update_enabled=False,
            )
        item.display_name = display_name
        item.repo_url = canonical_repo_url(repo_url) if repo_url else item.repo_url
        item.market_plugin_id = market_plugin_id
        item.framework_key = framework_key
        item.installed_release_id = installed_release_id
        item.installed_version = installed_version or "unknown"
        item.installed_asset_name = installed_asset_name or item.installed_asset_name
        item.asset_glob = asset_glob or item.asset_glob
        item.custom_install_path = custom_install_path
        item.exclude_dirs = list(exclude_dirs or [])
        item.exclude_files = list(exclude_files or [])
        item.last_status = "installed"
        item.last_error = None
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item


async def forget_managed_plugin(
    db: AsyncSession, server_id: int, managed_plugin_id: int
) -> ManagedPlugin | None:
    """Drop one tracking row. Files already on the game server are untouched.

    Removing the record only makes the panel stop tracking versions and
    auto-updates for that plugin; ``managed_plugin_files`` rows follow through
    the foreign key cascade.
    """
    result = await db.execute(
        select(ManagedPlugin).where(
            col(ManagedPlugin.id) == managed_plugin_id,
            col(ManagedPlugin.server_id) == server_id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        return None
    await db.delete(item)
    await db.commit()
    return item


async def forget_server_managed_plugins(db: AsyncSession, server_id: int) -> int:
    """Drop every tracking row for one server and return how many were removed."""
    result = await db.execute(
        select(ManagedPlugin).where(col(ManagedPlugin.server_id) == server_id)
    )
    removed = len(list(result.scalars().all()))
    if removed:
        await db.execute(delete(ManagedPlugin).where(col(ManagedPlugin.server_id) == server_id))
        await db.commit()
    return removed
