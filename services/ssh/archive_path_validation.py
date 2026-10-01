"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Tuple

import asyncssh

from modules.models import Server
from services.compat import LateBoundModule

if TYPE_CHECKING:
    from .file_archive import ArchiveOperationsMixin

host = LateBoundModule("services.ssh.file_archive")


def _canonical_path_is_within(base_path: str, target_path: str) -> bool:
    base = posixpath.normpath(base_path)
    target = posixpath.normpath(target_path)
    return target == base or target.startswith(base.rstrip("/") + "/")


async def _probe_remote_path(self: ArchiveOperationsMixin, sftp, target: str, allow_missing: bool):
    probe = target
    missing_parts: list[str] = []
    while True:
        try:
            attrs = await sftp.lstat(probe)
            canonical = posixpath.normpath(str(await sftp.realpath(probe)))
            return attrs, canonical, missing_parts
        except asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath:
            if not allow_missing:
                raise ValueError("Remote path does not exist") from None
            parent = posixpath.dirname(probe)
            component = posixpath.basename(probe)
            if parent == probe:
                raise ValueError("Cannot resolve an existing parent directory") from None
            if not component:
                raise ValueError("Remote path contains an invalid component") from None
            missing_parts.append(component)
            probe = parent
        except asyncssh.SFTPError as exc:
            raise ValueError(f"Cannot resolve remote path: {exc}") from exc


def _regular_archive_error(
    target_attrs: asyncssh.SFTPAttrs | None, missing_parts: list[str]
) -> str | None:
    if missing_parts:
        return "Archive file does not exist"
    if target_attrs is None or target_attrs.type != host.FILEXFER_TYPE_REGULAR:
        return "Archive path must be a regular file and cannot be a symlink"

    return None


def _canonical_missing_target(canonical_existing: str, missing_parts: list[str]) -> str:
    canonical_target = canonical_existing
    for component in reversed(missing_parts):
        canonical_target = posixpath.join(canonical_target, component)
    canonical_target = posixpath.normpath(canonical_target)

    return canonical_target


async def validate_path_within_base(
    self: ArchiveOperationsMixin,
    base_path: str,
    target_path: str,
    server: Server,
    allow_missing: bool = False,
    require_regular: bool = False,
) -> Tuple[bool, str]:
    """Validate a remote path against the canonical server directory.

    Lexical checks alone do not catch a symlink beneath ``base_path`` which
    points outside it. This resolves the nearest existing ancestor and then
    applies any missing suffix components to that canonical path.
    """
    normalized_base = posixpath.normpath(base_path)
    normalized_target = posixpath.normpath(target_path)
    if not self._canonical_path_is_within(normalized_base, normalized_target):
        return False, "Path is outside the server directory"

    if not self.conn:
        success, msg = await self.connect(server)
        if not success:
            return False, f"Connection failed: {msg}"

    try:
        async with self.conn.start_sftp_client() as sftp:
            try:
                base_attrs = await sftp.stat(normalized_base)
                if base_attrs.type != host.FILEXFER_TYPE_DIRECTORY:
                    return False, "Server directory is not a directory"
                canonical_base = posixpath.normpath(str(await sftp.realpath(normalized_base)))
            except asyncssh.SFTPError as exc:
                return False, f"Cannot resolve server directory: {exc}"

            try:
                target_attrs, canonical_existing, missing_parts = await self._probe_remote_path(
                    sftp, normalized_target, allow_missing
                )
            except ValueError as exc:
                return False, str(exc)

            canonical_target = _canonical_missing_target(canonical_existing, missing_parts)
            if not self._canonical_path_is_within(canonical_base, canonical_target):
                return False, "Remote path resolves outside the server directory"

            if require_regular:
                regular_error = _regular_archive_error(target_attrs, missing_parts)
                if regular_error is not None:
                    return False, regular_error
            return True, ""
    except asyncssh.SFTPError as exc:
        return False, f"SFTP path validation failed: {exc}"
    except Exception as exc:
        return False, f"Path validation failed: {exc}"
