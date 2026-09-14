"""Reusable on-disk cache for archives the panel downloads for managed hosts.

Plugin releases and the runtime frameworks (Metamod:Source, CounterStrikeSharp,
SwiftlyS2, CS2Fixes) are immutable once published, so installing the same
version on a second server should not pull the same archive from GitHub again.

Layout is ``<root>/<scope>-<digest>-<filename>``. The digest over the download
URL (plus an optional version) is what prevents collisions — two plugins that
both ship ``plugin.zip`` land on different entries. The readable ``scope``
prefix (``plugin-samyycx-cs2-playermodelchanger``, ``framework-metamod``) exists
so an operator browsing the directory can tell what an entry is before deleting
it by hand.

Retention is policy driven: entries untouched for ``max_age_days`` are dropped,
then the least recently used entries are dropped until the directory fits in
``max_megabytes``. Either limit is disabled with ``0``, and an administrator can
always clear everything from Settings.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "cache_serverplugins"
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_MAX_MEGABYTES = 4096
MAX_AGE_DAYS_LIMIT = 3650
MAX_MEGABYTES_LIMIT = 1_048_576
TEMP_SUFFIX = ".part"
# A ``.part`` file older than this belongs to a download that never finished.
STALE_TEMP_SECONDS = 24 * 60 * 60

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
# The readable prefix keeps no dots, so a scope can never render as "..".
_UNSAFE_SCOPE = re.compile(r"[^A-Za-z0-9_-]+")


def bounded(value: object, limit: int, fallback: int) -> int:
    """Clamp a stored retention setting to the range the panel supports."""
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return max(0, min(limit, value))


def safe_scope(scope: str | None) -> str:
    """Reduce a caller-supplied label to a readable, filesystem-safe prefix."""
    cleaned = _UNSAFE_SCOPE.sub("-", (scope or "").strip()).strip("-")
    return cleaned[:60].strip("-").casefold() or "download"


@dataclass(frozen=True)
class CachePolicy:
    """Effective cache configuration for one download."""

    enabled: bool = True
    path: str | None = None
    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    max_megabytes: int = DEFAULT_MAX_MEGABYTES

    @classmethod
    def from_settings(cls, settings: object) -> CachePolicy:
        return cls(
            enabled=bool(getattr(settings, "plugin_download_cache_enabled", True)),
            path=getattr(settings, "plugin_download_cache_path", None),
            max_age_days=bounded(
                getattr(settings, "plugin_download_cache_max_age_days", None),
                MAX_AGE_DAYS_LIMIT,
                DEFAULT_MAX_AGE_DAYS,
            ),
            max_megabytes=bounded(
                getattr(settings, "plugin_download_cache_max_megabytes", None),
                MAX_MEGABYTES_LIMIT,
                DEFAULT_MAX_MEGABYTES,
            ),
        )


DISABLED = CachePolicy(enabled=False)


def cache_key(url: str, version: str | None = None) -> str:
    return hashlib.sha256(f"{url}\0{version or ''}".encode()).hexdigest()


def cache_root(configured: str | None) -> Path:
    root = (
        Path(configured).expanduser() if configured and configured.strip() else DEFAULT_CACHE_PATH
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def cached_path(
    configured: str | None,
    url: str,
    version: str | None = None,
    *,
    scope: str | None = None,
) -> Path:
    name = Path(urlsplit(url).path).name or "archive"
    safe_name = _UNSAFE.sub("_", name)[:120]
    return cache_root(configured) / f"{safe_scope(scope)}-{cache_key(url, version)}-{safe_name}"


def put(
    configured: str | None,
    source: str,
    url: str,
    version: str | None = None,
    *,
    scope: str | None = None,
) -> Path:
    target = cached_path(configured, url, version, scope=scope)
    if target.exists():
        return target
    tmp = target.with_name(f"{target.name}{TEMP_SUFFIX}")
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)
    return target


def get(
    configured: str | None,
    url: str,
    version: str | None = None,
    *,
    scope: str | None = None,
) -> Path | None:
    """Return a usable entry, refreshing its last-use time so retention is LRU."""
    target = cached_path(configured, url, version, scope=scope)
    if not target.is_file():
        return None
    with suppress(OSError):
        os.utime(target)
    return target


def discard(
    configured: str | None,
    url: str,
    version: str | None = None,
    *,
    scope: str | None = None,
) -> None:
    """Drop one entry, used when a cached archive fails its integrity check."""
    with suppress(OSError):
        cached_path(configured, url, version, scope=scope).unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class _CacheFile:
    mtime: float
    size: int
    path: Path
    is_temp: bool


def _scan_directory(root: Path) -> list[_CacheFile]:
    """One directory listing plus one stat per name. Used by prune, stats, and tests."""
    found: list[_CacheFile] = []
    try:
        listing = root.iterdir()
    except OSError:
        return found
    for path in listing:
        try:
            info = path.stat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        found.append(
            _CacheFile(
                mtime=info.st_mtime,
                size=info.st_size,
                path=path,
                is_temp=path.name.endswith(TEMP_SUFFIX),
            )
        )
    return found


def _entries(root: Path) -> list[tuple[float, int, Path]]:
    return [
        (item.mtime, item.size, item.path) for item in _scan_directory(root) if not item.is_temp
    ]


def _remove(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    return True


def stats(configured: str | None) -> dict[str, int]:
    root = cache_root(configured)
    found = _entries(root)
    return {
        "files": len(found),
        "bytes": sum(size for _, size, _ in found),
        "oldest_age_seconds": int(time.time() - min((m for m, _, _ in found), default=time.time())),
    }


def prune(policy: CachePolicy) -> tuple[int, int]:
    """Apply the retention policy. Returns ``(removed_files, freed_bytes)``."""
    root = cache_root(policy.path)
    now = time.time()
    cutoff = now - policy.max_age_days * 86400 if policy.max_age_days else None
    removed = 0
    freed = 0
    keep: list[_CacheFile] = []
    for item in _scan_directory(root):
        if item.is_temp:
            if now - item.mtime > STALE_TEMP_SECONDS:
                _remove(item.path)
            continue
        if cutoff is not None and item.mtime < cutoff:
            if _remove(item.path):
                removed, freed = removed + 1, freed + item.size
            continue
        keep.append(item)

    limit = policy.max_megabytes * 1024 * 1024
    if limit:
        total = sum(item.size for item in keep)
        for item in sorted(keep, key=lambda entry: entry.mtime):
            if total <= limit:
                break
            if _remove(item.path):
                removed, freed, total = removed + 1, freed + item.size, total - item.size
    return removed, freed


def clear(configured: str | None) -> int:
    root = cache_root(configured)
    count = 0
    for path in root.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)
            count += 1
        elif path.is_dir():
            shutil.rmtree(path)
    return count
