"""Remote path and console helpers for plugin crash isolation."""

from __future__ import annotations

import posixpath
import shlex

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import PluginQuarantineEntry, Server, User
from modules.utils import get_current_time
from services.compat import LateBoundModule
from services.ssh_manager import SSHManager

host = LateBoundModule("services.plugin_diagnostic_service")


async def _validate_remote_path(
    manager: SSHManager,
    server: Server,
    absolute_path: str,
    *,
    allow_missing: bool,
) -> None:
    valid, error = await manager.validate_path_within_base(
        server.game_directory,
        absolute_path,
        server,
        allow_missing=allow_missing,
    )
    if not valid:
        raise ValueError(error)


async def _move_entry(
    manager: SSHManager,
    server: Server,
    entry: PluginQuarantineEntry,
    *,
    quarantine: bool,
) -> None:
    root = server.game_directory.rstrip("/")
    source = posixpath.join(root, entry.source_relative_path)
    target = posixpath.join(root, entry.quarantine_relative_path)
    origin, destination = (source, target) if quarantine else (target, source)
    await _validate_remote_path(manager, server, origin, allow_missing=True)
    await _validate_remote_path(manager, server, destination, allow_missing=True)
    command = (
        f"if test -e {shlex.quote(origin)}; then "
        f"if test -e {shlex.quote(destination)}; then exit 42; fi; "
        f"mkdir -p -- {shlex.quote(posixpath.dirname(destination))} && "
        f"mv -- {shlex.quote(origin)} {shlex.quote(destination)}; fi"
    )
    success, stdout, stderr = await manager.execute_command(command, timeout=30)
    if not success:
        raise RuntimeError(stderr or stdout or f"Unable to move {entry.candidate_key}")
    entry.is_quarantined = quarantine
    entry.restored_at = None if quarantine else get_current_time()


async def _set_candidates(
    db: AsyncSession,
    manager: SSHManager,
    server: Server,
    user: User,
    entries: dict[str, PluginQuarantineEntry],
    keys: list[str],
    *,
    quarantine: bool,
) -> None:
    for key in keys:
        entry = entries[key]
        if entry.is_quarantined == quarantine:
            continue
        await host.authorized_server(db, user, server.id)
        await _move_entry(manager, server, entry, quarantine=quarantine)
        db.add(entry)
        await db.commit()


async def _console_size(manager: SSHManager, server: Server) -> int:
    path = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo/console.log")
    success, stdout, _ = await manager.execute_command(
        f"stat -c %s -- {shlex.quote(path)} 2>/dev/null || printf 0", timeout=10
    )
    return int(stdout.strip()) if success and stdout.strip().isdigit() else 0


async def _console_delta(manager: SSHManager, server: Server, offset: int) -> str:
    path = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo/console.log")
    start = max(1, offset + 1)
    success, stdout, _ = await manager.execute_command(
        f"tail -c +{start} -- {shlex.quote(path)} 2>/dev/null | tail -c 12000", timeout=15
    )
    return stdout if success else ""
