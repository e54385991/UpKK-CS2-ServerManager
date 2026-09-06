"""Shared release archive safety and layout inspection, independent of install execution."""

from __future__ import annotations

import asyncio
import hashlib
import os
import posixpath
import re
import stat
import tarfile
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any

from services.plugins.github_assets import GitHubPlanError, download_release_asset

MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000
MAX_COMPRESSION_RATIO = 200
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


async def inspect_release_asset_layout(
    asset: dict[str, Any],
    repo_name: str,
    *,
    download: Callable[[str], Awaitable[tuple[str, str, int]]] = download_release_asset,
    read_entries: Callable[[str, str, int], list[dict[str, Any]]] = _archive_entries,
) -> dict[str, Any]:
    """Download and safely derive an install mapping for one release asset."""
    archive_path, archive_sha256, compressed_size = await download(asset["url"])
    try:
        try:
            entries = await asyncio.to_thread(
                read_entries, archive_path, asset["name"], compressed_size
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
