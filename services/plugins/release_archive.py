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

from services.plugins.archive_mapping import detect_mapping as _detect_mapping
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
        members = archive.infolist()
        _validate_archive_entries(
            [
                {
                    "path": _safe_entry_name(item.filename),
                    "size": item.file_size,
                    "is_dir": item.is_dir(),
                }
                for item in members
            ],
            os.path.getsize(path),
        )
        for item in members:
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
            _add_manifest(entries, lambda item=item: archive.open(item, "r"))
    return entries


def _tar_entries(path: str) -> list[dict[str, Any]]:
    entries = []
    with tarfile.open(path, "r:*") as archive:
        members = archive.getmembers()
        _validate_archive_entries(
            [
                {"path": _safe_entry_name(item.name), "size": item.size, "is_dir": item.isdir()}
                for item in members
            ],
            os.path.getsize(path),
        )
        for item in members:
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
            _add_manifest(entries, lambda item=item: archive.extractfile(item))
    return entries


def _add_manifest(entries: list[dict[str, Any]], open_file: Callable[[], Any]) -> None:
    entry = entries[-1]
    if (
        entry["is_dir"]
        or entry["size"] > 16_000
        or not entry["path"].endswith((".deps.json", ".vdf"))
        or sum("text" in item for item in entries) >= 8
    ):
        return
    handle = open_file()
    if handle is not None:
        with handle:
            entry["text"] = handle.read(16_000).decode("utf-8", errors="replace")


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
        "archive_mappings": mapping
        if len(mapping) > 1 and any(item["target"] not in {"addons", "cfg"} for item in mapping)
        else [],
        "archive_sha256": archive_sha256,
        "compressed_size": compressed_size,
        "entries": entries,
        "source_prefix": source_prefix,
        "mapping": mapping,
        "mapping_required": mapping_required,
    }
