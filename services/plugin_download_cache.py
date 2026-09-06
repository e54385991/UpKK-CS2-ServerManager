"""Content-addressed cache for panel-proxy plugin downloads."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "plugin-downloads"


def cache_key(url: str, version: str | None = None) -> str:
    return hashlib.sha256(f"{url}\0{version or ''}".encode()).hexdigest()


def cache_root(configured: str | None) -> Path:
    root = (
        Path(configured).expanduser() if configured and configured.strip() else DEFAULT_CACHE_PATH
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def cached_path(configured: str | None, url: str, version: str | None = None) -> Path:
    name = Path(urlsplit(url).path).name or "archive"
    safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)[:120]
    return cache_root(configured) / f"{cache_key(url, version)}-{safe_name}"


def put(configured: str | None, source: str, url: str, version: str | None = None) -> Path:
    target = cached_path(configured, url, version)
    if target.exists():
        return target
    tmp = target.with_suffix(target.suffix + ".tmp")
    shutil.copyfile(source, tmp)
    os.replace(tmp, target)
    return target


def get(configured: str | None, url: str, version: str | None = None) -> Path | None:
    target = cached_path(configured, url, version)
    return target if target.is_file() else None


def stats(configured: str | None) -> dict[str, int]:
    root = cache_root(configured)
    files = [p for p in root.iterdir() if p.is_file() and not p.name.endswith(".tmp")]
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}


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
