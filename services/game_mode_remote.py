"""SSH helpers for game-mode install: inspect, wipe, wait, atomic replace."""

from __future__ import annotations

import asyncio
import posixpath
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from modules import Server
from services.ssh_manager import SSHManager

ProgressCallback = Callable[..., Awaitable[None]]

ADDONS_SUFFIX = "/cs2/game/csgo/addons"
WAIT_CONFIG_TIMEOUT_SECONDS = 240
WAIT_CONFIG_INTERVAL_SECONDS = 8


class GameModeRemoteError(ValueError):
    """Raised when a remote game-mode step cannot proceed safely."""


def resolve_csgo_directory(game_directory: str) -> str:
    raw = str(game_directory or "").strip()
    if not raw or any(part == ".." for part in raw.split("/")):
        raise GameModeRemoteError("Server game directory is not safe")
    root = posixpath.normpath(raw)
    return posixpath.join(root, "cs2/game/csgo")


def resolve_addons_directory(game_directory: str) -> str:
    csgo = resolve_csgo_directory(game_directory)
    path = posixpath.normpath(posixpath.join(csgo, "addons"))
    if not path.endswith(ADDONS_SUFFIX):
        raise GameModeRemoteError(
            "Refusing to operate on a path that is not the CS2 addons directory"
        )
    return path


def remote_paths(server: Server) -> dict[str, str]:
    csgo = resolve_csgo_directory(server.game_directory)
    css = posixpath.join(csgo, "addons/counterstrikesharp")
    return {
        "csgo": csgo,
        "addons": resolve_addons_directory(server.game_directory),
        "metamod": posixpath.join(csgo, "addons/metamod"),
        "css": css,
        "css_bin": posixpath.join(css, "bin"),
        "swiftly": posixpath.join(csgo, "addons/swiftlys2"),
        "cs2kz": posixpath.join(csgo, "addons/metamod/cs2kz.vdf"),
        "plugins": posixpath.join(css, "plugins"),
        "mapchooser_dll": posixpath.join(css, "plugins/MapChooser/MapChooser.dll"),
        "maps": posixpath.join(css, "configs/plugins/MapChooser/maps.txt"),
        "config": posixpath.join(css, "configs/plugins/MapChooser/config.json"),
    }


def wait_file_paths(server: Server, relative_files: tuple[str, ...]) -> list[str]:
    csgo = resolve_csgo_directory(server.game_directory)
    paths: list[str] = []
    for relative in relative_files:
        cleaned = relative.strip().lstrip("/")
        if not cleaned or any(part == ".." for part in cleaned.split("/")):
            raise GameModeRemoteError(f"Unsafe wait path: {relative}")
        paths.append(posixpath.join(csgo, cleaned))
    return paths


async def connect(server: Server) -> SSHManager:
    manager = SSHManager()
    success, message = await manager.connect(server)
    if not success:
        raise GameModeRemoteError(f"SSH connection failed: {message}")
    return manager


async def inspect_game_mode_state(manager: SSHManager, server: Server) -> dict[str, bool]:
    paths = remote_paths(server)
    command = (
        f"if test -d {shlex.quote(paths['addons'])}; then echo addons=1; else echo addons=0; fi; "
        f"if test -d {shlex.quote(paths['metamod'])}; then echo metamod=1; else echo metamod=0; fi; "
        f"if test -d {shlex.quote(paths['css'])} && find {shlex.quote(paths['css_bin'])} "
        "-maxdepth 5 -type f \\( -name CounterStrikeSharp.API.dll -o -name counterstrikesharp.so "
        "-o -name CounterStrikeSharp.dll \\) -print -quit 2>/dev/null | grep -q .; "
        "then echo css=1; else echo css=0; fi; "
        f"if test -d {shlex.quote(paths['swiftly'])}; then echo swiftly=1; else echo swiftly=0; fi; "
        f"if test -f {shlex.quote(paths['cs2kz'])} || "
        f"find {shlex.quote(paths['metamod'])} -maxdepth 3 -type f -name 'cs2kz.vdf' "
        "-print -quit 2>/dev/null | grep -q .; then echo cs2kz=1; else echo cs2kz=0; fi; "
        f"if test -f {shlex.quote(paths['mapchooser_dll'])} || "
        f"find {shlex.quote(paths['plugins'])} -maxdepth 4 -type f -name MapChooser.dll "
        "-print -quit 2>/dev/null | grep -q .; then echo mapchooser=1; else echo mapchooser=0; fi; "
        f"if test -f {shlex.quote(paths['maps'])}; then echo maps=1; else echo maps=0; fi; "
        f"if test -f {shlex.quote(paths['config'])}; then echo config=1; else echo config=0; fi"
    )
    success, stdout, stderr = await manager.execute_command(command, timeout=20)
    if not success:
        raise GameModeRemoteError(stderr or stdout or "Game-mode inspection failed")
    values: dict[str, bool] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value.strip() == "1"
    return values


async def wait_for_remote_files(
    manager: SSHManager,
    paths: list[str],
    *,
    timeout_seconds: float = WAIT_CONFIG_TIMEOUT_SECONDS,
    interval_seconds: float = WAIT_CONFIG_INTERVAL_SECONDS,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    progress: ProgressCallback | None = None,
) -> None:
    if not paths:
        return
    deadline = time.monotonic() + timeout_seconds
    while True:
        command = "; ".join(
            f"if test -f {shlex.quote(path)}; then echo {index}=1; else echo {index}=0; fi"
            for index, path in enumerate(paths)
        )
        success, stdout, stderr = await manager.execute_command(command, timeout=20)
        if not success:
            raise GameModeRemoteError(stderr or stdout or "Waiting for generated configs failed")
        present = {index: False for index, _path in enumerate(paths)}
        for line in stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator and key.isdigit():
                present[int(key)] = value.strip() == "1"
        missing = [paths[index] for index, found in present.items() if not found]
        if not missing:
            return
        if time.monotonic() >= deadline:
            names = ", ".join(posixpath.basename(path) for path in missing)
            raise GameModeRemoteError(
                "Timed out waiting for plugin-generated configuration files: " + names
            )
        if progress is not None:
            await progress(
                "Waiting for generated configs: "
                + ", ".join(posixpath.basename(path) for path in missing)
            )
        await sleep(interval_seconds)


async def wipe_addons_directory(manager: SSHManager, addons_path: str) -> None:
    resolved = posixpath.normpath(addons_path)
    if not resolved.endswith(ADDONS_SUFFIX):
        raise GameModeRemoteError("Refusing to wipe a path that is not the CS2 addons directory")
    success, stdout, stderr = await manager.execute_command(
        f"rm -rf -- {shlex.quote(resolved)} && mkdir -p -- {shlex.quote(resolved)}",
        timeout=120,
    )
    if not success:
        raise GameModeRemoteError(stderr or stdout or "Failed to wipe the addons directory")


async def replace_remote_file(
    manager: SSHManager,
    server: Server,
    path: str,
    content: str,
    *,
    existed: bool,
) -> str | None:
    parent = posixpath.dirname(path)
    success, stdout, stderr = await manager.execute_command(
        f"mkdir -p -- {shlex.quote(parent)}", timeout=20
    )
    if not success:
        raise GameModeRemoteError(stderr or stdout or f"Unable to create {parent}")
    backup: str | None = None
    if existed:
        backup = f"{path}.upkk-backup-{uuid.uuid4().hex}"
        success, stdout, stderr = await manager.execute_command(
            f"cp -p -- {shlex.quote(path)} {shlex.quote(backup)}", timeout=20
        )
        if not success:
            raise GameModeRemoteError(stderr or stdout or f"Unable to back up {path}")
    temporary = f"{path}.upkk-{uuid.uuid4().hex}.tmp"
    success, error = await manager.write_file(temporary, content, server)
    if not success:
        raise GameModeRemoteError(f"Unable to stage {path}: {error}")
    success, stdout, stderr = await manager.execute_command(
        f"mv -f -- {shlex.quote(temporary)} {shlex.quote(path)}", timeout=20
    )
    if not success:
        await manager.execute_command(f"rm -f -- {shlex.quote(temporary)}", timeout=10)
        raise GameModeRemoteError(stderr or stdout or f"Unable to replace {path}")
    return backup
