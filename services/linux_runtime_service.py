"""Detect Linux compatibility and select paired Steam Runtime release assets."""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from typing import Any, Iterable, Sequence

from modules.models import Server
from services.ssh_manager import SSHManager

STEAM_RUNTIME_3 = "steamrt3"
STEAM_RUNTIME_4 = "steamrt4"
STEAM_RUNTIMES = (STEAM_RUNTIME_3, STEAM_RUNTIME_4)

_STEAM_RUNTIME_MARKER = re.compile(
    r"(?<![A-Za-z0-9])steam[-_.]?rt[-_.]?(?P<version>[34])(?![0-9])",
    re.IGNORECASE,
)
_VERSION_COMPONENT = re.compile(r"\d+")
_GLIBC_VERSION = re.compile(r"\bglibc\s+(?P<version>\d+(?:\.\d+)+)", re.IGNORECASE)
_PROBE_MARKER = "__UPKK_OS_RELEASE__"


class RuntimeSelectionRequired(ValueError):
    """Raised when paired Runtime assets exist but compatibility is unknown."""


def _version_tuple(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    components = tuple(int(item) for item in _VERSION_COMPONENT.findall(value))
    return components or None


def _version_at_least(value: str | None, minimum: tuple[int, ...]) -> bool | None:
    parsed = _version_tuple(value)
    if parsed is None:
        return None
    width = max(len(parsed), len(minimum))
    return (*parsed, *([0] * (width - len(parsed)))) >= (
        *minimum,
        *([0] * (width - len(minimum))),
    )


def _parse_os_release_value(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = shlex.split(value, posix=True)
    except ValueError:
        return value.strip("\"'")
    return parsed[0] if parsed else ""


def parse_linux_runtime_probe(output: str) -> dict[str, Any]:
    """Parse the bounded SSH probe and derive the preferred Steam Runtime."""
    libc_output, marker, os_output = (output or "").partition(_PROBE_MARKER)
    glibc_match = _GLIBC_VERSION.search(libc_output)
    glibc_version = glibc_match.group("version") if glibc_match else None

    os_release: dict[str, str] = {}
    if marker:
        for line in os_output.splitlines():
            key, separator, raw_value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                continue
            os_release[key] = _parse_os_release_value(raw_value)

    distro_id = os_release.get("ID", "").casefold() or None
    distro_version = os_release.get("VERSION_ID") or None
    pretty_name = os_release.get("PRETTY_NAME") or None
    recommended: str | None = None
    source = "unknown"

    if glibc_version is not None:
        is_new_glibc = _version_at_least(glibc_version, (2, 41))
        recommended = STEAM_RUNTIME_4 if is_new_glibc else STEAM_RUNTIME_3
        source = "glibc"
        reason = (
            f"glibc {glibc_version} requires the non-executable-stack SteamRT4 build"
            if is_new_glibc
            else f"glibc {glibc_version} is compatible with the established SteamRT3 build"
        )
    elif distro_id == "ubuntu":
        is_new_ubuntu = _version_at_least(distro_version, (25, 4))
        if is_new_ubuntu is not None:
            recommended = STEAM_RUNTIME_4 if is_new_ubuntu else STEAM_RUNTIME_3
            source = "os_release"
            reason = f"Ubuntu {distro_version} fallback selects {recommended}"
        else:
            reason = "Ubuntu was detected, but its version could not be determined"
    elif distro_id == "debian":
        is_new_debian = _version_at_least(distro_version, (13,))
        if is_new_debian is not None:
            recommended = STEAM_RUNTIME_4 if is_new_debian else STEAM_RUNTIME_3
            source = "os_release"
            reason = f"Debian {distro_version} fallback selects {recommended}"
        else:
            reason = "Debian was detected, but its version could not be determined"
    else:
        reason = "glibc and a supported Ubuntu/Debian fallback could not be determined"

    return {
        "distro_id": distro_id,
        "distro_version": distro_version,
        "pretty_name": pretty_name,
        "glibc_version": glibc_version,
        "recommended_steam_runtime": recommended,
        "detection_source": source,
        "reason": reason,
    }


def unknown_linux_runtime_profile(reason: str) -> dict[str, Any]:
    return {
        "distro_id": None,
        "distro_version": None,
        "pretty_name": None,
        "glibc_version": None,
        "recommended_steam_runtime": None,
        "detection_source": "unknown",
        "reason": reason,
    }


async def detect_linux_runtime_profile(server: Server) -> dict[str, Any]:
    """Probe one authorized server without persisting potentially stale OS state."""
    manager = SSHManager()
    try:
        connected, message = await manager.connect(server)
    except Exception as exc:
        return unknown_linux_runtime_profile(f"SSH runtime detection failed: {exc}")
    if not connected:
        return unknown_linux_runtime_profile(f"SSH runtime detection failed: {message}")
    command = (
        "(getconf GNU_LIBC_VERSION 2>/dev/null || ldd --version 2>/dev/null | head -n 1 || true); "
        f"printf '\\n{_PROBE_MARKER}\\n'; "
        "cat /etc/os-release 2>/dev/null || true"
    )
    try:
        try:
            success, stdout, stderr = await manager.execute_command(command, timeout=15)
        except Exception as exc:
            return unknown_linux_runtime_profile(f"Linux runtime probe failed: {exc}")
    finally:
        try:
            await manager.disconnect()
        except Exception:
            pass
    if not success and not stdout:
        return unknown_linux_runtime_profile(
            f"Linux runtime probe failed: {(stderr or 'no output')[:300]}"
        )
    return parse_linux_runtime_probe(stdout)


def steam_runtime_for_asset(asset_name: str) -> str | None:
    match = _STEAM_RUNTIME_MARKER.search(asset_name or "")
    return f"steamrt{match.group('version')}" if match else None


def steam_runtime_asset_family(asset_name: str) -> str | None:
    if not _STEAM_RUNTIME_MARKER.search(asset_name or ""):
        return None
    return _STEAM_RUNTIME_MARKER.sub("{steamrt}", asset_name, count=1).casefold()


def _asset_name(asset: Any) -> str:
    if isinstance(asset, dict):
        return str(asset.get("name") or "")
    return str(getattr(asset, "name", "") or "")


def paired_runtime_families(assets: Iterable[Any]) -> set[str]:
    runtimes_by_family: dict[str, set[str]] = defaultdict(set)
    for asset in assets:
        name = _asset_name(asset)
        family = steam_runtime_asset_family(name)
        runtime = steam_runtime_for_asset(name)
        if family and runtime:
            runtimes_by_family[family].add(runtime)
    return {
        family
        for family, runtimes in runtimes_by_family.items()
        if set(STEAM_RUNTIMES).issubset(runtimes)
    }


def has_paired_runtime_assets(assets: Iterable[Any]) -> bool:
    return bool(paired_runtime_families(assets))


def runtime_compatibility(
    asset_name: str,
    assets: Iterable[Any],
    profile: dict[str, Any] | None,
) -> str:
    runtime = steam_runtime_for_asset(asset_name)
    family = steam_runtime_asset_family(asset_name)
    if not runtime or not family or family not in paired_runtime_families(assets):
        return "not_applicable"
    recommended = (profile or {}).get("recommended_steam_runtime")
    if recommended not in STEAM_RUNTIMES:
        return "unknown"
    return "recommended" if runtime == recommended else "alternative"


def annotate_runtime_assets(
    assets: Sequence[dict[str, Any]], profile: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Copy API assets and add backward-compatible Runtime annotations."""
    annotated: list[dict[str, Any]] = []
    for asset in assets:
        item = dict(asset)
        item["steam_runtime"] = steam_runtime_for_asset(_asset_name(asset))
        item["runtime_compatibility"] = runtime_compatibility(_asset_name(asset), assets, profile)
        annotated.append(item)
    return annotated


def prioritize_runtime_assets(assets: Sequence[Any], profile: dict[str, Any] | None) -> list[Any]:
    """Move the compatible member of paired families ahead without dropping assets."""
    paired = paired_runtime_families(assets)
    if not paired:
        return list(assets)
    recommended = (profile or {}).get("recommended_steam_runtime")
    if recommended not in STEAM_RUNTIMES:
        raise RuntimeSelectionRequired(
            "SteamRT3 and SteamRT4 assets are available, but the server runtime could not be "
            "detected; select an asset explicitly"
        )

    indexed = list(enumerate(assets))

    def rank(item: tuple[int, Any]) -> tuple[int, int]:
        index, asset = item
        name = _asset_name(asset)
        family = steam_runtime_asset_family(name)
        runtime = steam_runtime_for_asset(name)
        if family in paired and runtime == recommended:
            return (0, index)
        if family not in paired:
            return (1, index)
        return (2, index)

    return [asset for _index, asset in sorted(indexed, key=rank)]


def select_unique_runtime_asset(
    assets: Sequence[Any], profile: dict[str, Any] | None
) -> Any | None:
    """Resolve an otherwise exact RT3/RT4 ambiguity for immutable plans."""
    if len(assets) == 1:
        return assets[0]
    paired = paired_runtime_families(assets)
    if not paired:
        return None
    recommended = (profile or {}).get("recommended_steam_runtime")
    if recommended not in STEAM_RUNTIMES:
        raise RuntimeSelectionRequired(
            "SteamRT3 and SteamRT4 assets are available, but the server runtime could not be "
            "detected; select an asset explicitly"
        )
    paired_assets = [
        asset for asset in assets if steam_runtime_asset_family(_asset_name(asset)) in paired
    ]
    # Do not guess between a Runtime pair and a separate full/upgrade/package family.
    if len(paired) != 1 or len(paired_assets) != len(assets):
        return None
    matches = [
        asset
        for asset in paired_assets
        if steam_runtime_for_asset(_asset_name(asset)) == recommended
    ]
    return matches[0] if len(matches) == 1 else None
