"""Compatibility with ClientCvarValue's switch to versioned Linux archives."""

import re
import shlex
import uuid
from collections.abc import Awaitable, Callable

_REPOSITORY = "https://github.com/komashchenko/clientcvarvalue"
_LINUX_ARCHIVE = re.compile(r"ClientCvarValue-v?\d+(?:\.\d+)+-linux\.tar\.gz", re.IGNORECASE)
_LEGACY_LOADER = "addons/metamod/client_cvar_value.vdf"
_CURRENT_FILES = (
    "addons/metamod/clientcvarvalue.vdf",
    "addons/clientcvarvalue/bin/linuxsteamrt64/clientcvarvalue.so",
)


def _is_versioned_linux_asset(repo_url: str | None, name: str) -> bool:
    repository = (repo_url or "").strip().casefold().rstrip("/").removesuffix(".git")
    return repository == _REPOSITORY and bool(_LINUX_ARCHIVE.fullmatch(name))


def matches_legacy_linux_asset(repo_url: str | None, pattern: str, name: str) -> bool:
    return pattern == "linux.zip" and _is_versioned_linux_asset(repo_url, name)


async def retire_legacy_loader(
    execute: Callable[..., Awaitable[tuple[bool, str, str]]],
    download_url: str,
    source_dir: str,
    csgo_dir: str,
) -> tuple[bool, str]:
    """Disable the obsolete VDF after the new loader and binary are in place."""
    repo_url, _, asset_path = download_url.partition("/releases/download/")
    if not _is_versioned_linux_asset(repo_url, asset_path.rsplit("/", 1)[-1]):
        return True, ""

    legacy = shlex.quote(f"{csgo_dir}/{_LEGACY_LOADER}")
    backup = shlex.quote(f"{csgo_dir}/{_LEGACY_LOADER}.{uuid.uuid4().hex}.disabled")
    checks = [
        f"cmp -s -- {shlex.quote(f'{source_dir}/{path}')} {shlex.quote(f'{csgo_dir}/{path}')}"
        for path in _CURRENT_FILES
    ]
    success, _, error = await execute(
        f"if test -e {legacy} || test -L {legacy}; then "
        + " && ".join([*checks, f"mv -- {legacy} {backup}"])
        + "; fi",
        timeout=120,
    )
    return success, (
        "" if success else error or "The new ClientCvarValue files could not be verified"
    )
