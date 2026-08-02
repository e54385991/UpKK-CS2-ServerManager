"""Read-only remote plugin inventory and tracking-record correlation."""

from __future__ import annotations

import base64
import binascii
import posixpath
import re
import shlex
from typing import Any, Iterable

from modules.models import Server
from services.ssh_manager import SSHManager

MAX_REMOTE_PLUGINS_PER_FRAMEWORK = 200
_SAFE_PLUGIN_ENTRY = re.compile(r"[A-Za-z0-9_./ -]{1,1000}")
_ALIAS_SUFFIXES = ("counterstrikesharp", "metamod", "plugin")


class PluginInventoryError(RuntimeError):
    """Raised when current remote plugin files cannot be inspected."""


def _decode_names(value: str) -> tuple[list[str], bool]:
    try:
        decoded = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PluginInventoryError("Remote plugin inventory returned invalid data") from exc
    names: list[str] = []
    for raw_name in decoded.split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8", errors="replace")
        normalized = posixpath.normpath(name)
        if (
            _SAFE_PLUGIN_ENTRY.fullmatch(name)
            and not name.startswith((".", "/"))
            and normalized != ".."
            and not normalized.startswith("../")
        ):
            names.append(name)
    unique = list(dict.fromkeys(names))
    return unique[:MAX_REMOTE_PLUGINS_PER_FRAMEWORK], len(unique) > MAX_REMOTE_PLUGINS_PER_FRAMEWORK


async def inspect_remote_plugin_inventory(server: Server) -> dict[str, Any]:
    """Inspect framework binaries and installed plugin entry points over SSH."""
    manager = SSHManager()
    connected, message = await manager.connect(server)
    if not connected:
        raise PluginInventoryError(f"SSH connection failed: {message}")

    csgo = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo")
    metamod_root = posixpath.join(csgo, "addons/metamod")
    metamod_binary = posixpath.join(metamod_root, "bin/linuxsteamrt64/metamod.2.cs2.so")
    css_root = posixpath.join(csgo, "addons/counterstrikesharp")
    css_bin = posixpath.join(css_root, "bin")
    css_plugins = posixpath.join(css_root, "plugins")
    limit = MAX_REMOTE_PLUGINS_PER_FRAMEWORK + 1
    command = (
        f"if test -f {shlex.quote(metamod_binary)}; then printf 'metamod=1\\n'; "
        "else printf 'metamod=0\\n'; fi; "
        f"if test -d {shlex.quote(css_root)} && find {shlex.quote(css_bin)} "
        "-maxdepth 5 -type f \\( -name CounterStrikeSharp.API.dll "
        "-o -name counterstrikesharp.so -o -name CounterStrikeSharp.dll \\) "
        "-print -quit 2>/dev/null | grep -q .; then printf 'counterstrikesharp=1\\n'; "
        "else printf 'counterstrikesharp=0\\n'; fi; "
        "printf 'metamod_plugins='; "
        f"if test -d {shlex.quote(metamod_root)}; then find {shlex.quote(metamod_root)} "
        "-xdev -maxdepth 1 -type f -name '*.vdf' ! -name 'counterstrikesharp.vdf' "
        f"-printf '%f\\0' | head -z -n {limit} | base64 -w0; fi; printf '\\n'; "
        "printf 'counterstrikesharp_plugins='; "
        f"if test -d {shlex.quote(css_plugins)}; then find {shlex.quote(css_plugins)} "
        "-xdev -mindepth 2 -maxdepth 4 -type f -name '*.dll' -printf '%P\\0' "
        f"| head -z -n {limit} | base64 -w0; fi; printf '\\n'"
    )
    try:
        success, stdout, stderr = await manager.execute_command(command, timeout=30)
        if not success:
            raise PluginInventoryError(stderr or stdout or "Unable to inspect installed plugins")
    finally:
        await manager.disconnect()

    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    required = {"metamod", "counterstrikesharp", "metamod_plugins", "counterstrikesharp_plugins"}
    if not required.issubset(values):
        raise PluginInventoryError("Remote plugin inventory returned incomplete data")

    metamod_names, metamod_truncated = _decode_names(values["metamod_plugins"])
    css_paths, css_truncated = _decode_names(values["counterstrikesharp_plugins"])
    css_names = list(dict.fromkeys(path.split("/", 1)[0] for path in css_paths if "/" in path))
    plugins = [
        {
            "key": f"metamod:{name.casefold()}",
            "kind": "metamod",
            "name": name,
            "relative_path": posixpath.join("cs2/game/csgo/addons/metamod", name),
        }
        for name in metamod_names
    ]
    plugins.extend(
        {
            "key": f"counterstrikesharp:{name.casefold()}",
            "kind": "counterstrikesharp",
            "name": name,
            "relative_path": posixpath.join(
                "cs2/game/csgo/addons/counterstrikesharp/plugins", name
            ),
        }
        for name in css_names
    )
    return {
        "frameworks": {
            "metamod": values["metamod"] == "1",
            "counterstrikesharp": values["counterstrikesharp"] == "1",
        },
        "plugins": plugins,
        "truncated": metamod_truncated or css_truncated,
    }


def _alias_variants(value: str | None) -> set[str]:
    if not value:
        return set()
    candidate = value.strip().rstrip("/").rsplit("/", 1)[-1]
    candidate = posixpath.splitext(candidate)[0]
    normalized = re.sub(r"[^a-z0-9]+", "", candidate.casefold())
    if not normalized:
        return set()
    variants = {normalized}
    pending = [normalized]
    while pending:
        current = pending.pop()
        for suffix in _ALIAS_SUFFIXES:
            if current.endswith(suffix) and len(current) > len(suffix) + 2:
                shortened = current[: -len(suffix)]
                if shortened not in variants:
                    variants.add(shortened)
                    pending.append(shortened)
    return variants


def _aliases_match(left: set[str], right: set[str]) -> bool:
    if left & right:
        return True
    return any(
        len(shorter) >= 6 and longer.endswith(shorter) for longer in left for shorter in right
    ) or any(len(shorter) >= 6 and longer.endswith(shorter) for longer in right for shorter in left)


def installation_evidence(item: Any, inventory: dict[str, Any]) -> list[dict[str, str]]:
    """Return remote evidence matching a managed record or market plugin."""
    frameworks = inventory.get("frameworks") or {}
    framework_key = str(getattr(item, "framework_key", "") or "").casefold()
    identity_aliases: set[str] = set()
    for value in (
        getattr(item, "display_name", None),
        getattr(item, "title", None),
        getattr(item, "repo_url", None),
        getattr(item, "github_url", None),
    ):
        identity_aliases.update(_alias_variants(value))

    if framework_key in frameworks and frameworks[framework_key]:
        return [{"kind": "framework", "name": framework_key}]
    if frameworks.get("metamod") and "metamodsource" in identity_aliases:
        return [{"kind": "framework", "name": "metamod"}]
    if frameworks.get("counterstrikesharp") and "counterstrikesharp" in identity_aliases:
        return [{"kind": "framework", "name": "counterstrikesharp"}]

    aliases = set(identity_aliases)
    aliases.update(_alias_variants(getattr(item, "custom_install_path", None)))
    matches: list[dict[str, str]] = []
    for plugin in inventory.get("plugins") or []:
        if _aliases_match(aliases, _alias_variants(str(plugin.get("name") or ""))):
            matches.append(
                {
                    "kind": str(plugin.get("kind") or "unknown"),
                    "name": str(plugin.get("name") or "unknown"),
                    "relative_path": str(plugin.get("relative_path") or ""),
                    "key": str(plugin.get("key") or ""),
                }
            )
    return matches


def verified_market_plugin_ids(
    managed: Iterable[Any],
    planned_plugins: Iterable[Any],
    inventory: dict[str, Any],
) -> set[int]:
    """Resolve market IDs that have current remote filesystem evidence."""
    installed: set[int] = set()
    for item in managed:
        market_plugin_id = getattr(item, "market_plugin_id", None)
        if market_plugin_id is not None and installation_evidence(item, inventory):
            installed.add(int(market_plugin_id))
    for plugin in planned_plugins:
        plugin_id = getattr(plugin, "id", None)
        if plugin_id is not None and installation_evidence(plugin, inventory):
            installed.add(int(plugin_id))
    return installed
