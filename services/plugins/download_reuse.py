"""Serve panel-side archive downloads from the reusable local cache.

Panel-proxy downloads of immutable, version-pinned archives — marketplace plugin
releases and the Metamod:Source, CounterStrikeSharp, SwiftlyS2 and CS2Fixes
runtimes — go through here, so installing the same release on a second server
reuses the copy already on disk instead of fetching it again. The SteamCMD
bootstrap deliberately stays uncached: its URL always points at "latest", so a
cached copy would pin a stale installer.

The cache is advisory. Any failure to read or write it degrades to a normal
download rather than failing the install.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from anyio import to_thread

from modules.database import async_session_maker
from modules.models import SystemSettings
from services import plugin_download_cache as cache

logger = logging.getLogger(__name__)


async def current_policy() -> cache.CachePolicy:
    """Read the saved retention policy; any failure disables reuse for this run."""
    try:
        async with async_session_maker() as db:
            settings = await SystemSettings.get_or_create_settings(db)
            return cache.CachePolicy.from_settings(settings)
    except Exception:
        logger.exception("Plugin download cache policy unavailable; downloading without reuse")
        return cache.DISABLED


def _digest_and_size(path: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), total


async def _store(
    policy: cache.CachePolicy, source: str, key: str, version: str | None, scope: str
) -> None:
    try:
        await to_thread.run_sync(lambda: cache.put(policy.path, source, key, version, scope=scope))
        removed, freed = await to_thread.run_sync(lambda: cache.prune(policy))
        if removed:
            logger.info(
                "Pruned %s cached download(s), freeing %.1f MB", removed, freed / (1024 * 1024)
            )
    except OSError:
        logger.warning("Could not cache the download for %s", scope, exc_info=True)


async def cached_download(
    url: str,
    local_path: str,
    *,
    scope: str,
    version: str | None = None,
    cache_url: str | None = None,
    policy: cache.CachePolicy | None = None,
    **download_kwargs: Any,
) -> tuple[bool, str | None]:
    """Download ``url`` to ``local_path``, reusing a cached archive when present.

    ``cache_url`` is the identity used for the cache key. Callers pass the
    canonical upstream URL there when ``url`` carries a GitHub proxy prefix, so
    changing the proxy does not invalidate everything already downloaded.
    """
    from modules.http_helper import http_helper

    key = cache_url or url
    effective = policy if policy is not None else await current_policy()
    if effective.enabled:
        try:
            hit = await to_thread.run_sync(
                lambda: cache.get(effective.path, key, version, scope=scope)
            )
        except OSError:
            hit = None
        if hit is not None:
            try:
                await to_thread.run_sync(shutil.copyfile, str(hit), local_path)
                logger.info("Reused the cached archive for %s", scope)
                return True, None
            except OSError:
                logger.warning("Cached archive for %s is unreadable; downloading", scope)

    success, error = await http_helper.download_file(url, local_path, **download_kwargs)
    if success and effective.enabled:
        await _store(effective, local_path, key, version, scope)
    return success, error


async def cached_release_asset(
    url: str,
    *,
    scope: str,
    version: str | None = None,
    expected_sha256: str | None = None,
    policy: cache.CachePolicy | None = None,
) -> tuple[str, str, int]:
    """Return ``(temp_path, sha256, size)`` for an approved GitHub release asset.

    The caller owns and deletes ``temp_path``, exactly as with an uncached
    download, so the cache never hands out a path that install code would later
    unlink. A cached entry whose digest no longer matches the approved plan is
    dropped and re-downloaded.
    """
    from services.plugins.github_assets import download_release_asset

    effective = policy if policy is not None else await current_policy()
    if effective.enabled:
        try:
            hit = await to_thread.run_sync(
                lambda: cache.get(effective.path, url, version, scope=scope)
            )
        except OSError:
            hit = None
        if hit is not None:
            temp = tempfile.NamedTemporaryFile(
                prefix="upkk-github-plan-", suffix=".archive", delete=False
            )
            temp.close()
            try:
                await to_thread.run_sync(shutil.copyfile, str(hit), temp.name)
                digest, size = await to_thread.run_sync(_digest_and_size, temp.name)
            except OSError:
                Path(temp.name).unlink(missing_ok=True)
            else:
                if expected_sha256 is None or digest.casefold() == expected_sha256.casefold():
                    logger.info("Reused the cached release archive for %s", scope)
                    return temp.name, digest, size
                Path(temp.name).unlink(missing_ok=True)
                await to_thread.run_sync(
                    lambda: cache.discard(effective.path, url, version, scope=scope)
                )

    path, digest, size = await download_release_asset(url)
    if effective.enabled:
        await _store(effective, path, url, version, scope)
    return path, digest, size
