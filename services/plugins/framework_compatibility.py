"""Match a marketplace listing's runtime against what the server actually runs.

CounterStrikeSharp and SwiftlyS2 are mutually exclusive CS2 plugin runtimes, so
a CounterStrikeSharp plugin dropped on a SwiftlyS2 server (or the reverse) never
loads. The install preflight surfaces that as an explicit warning the operator
has to acknowledge instead of letting the install silently do nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from modules.models.plugins import PLUGIN_FRAMEWORK_SECTIONS, PluginFramework
from services.plugins.common import framework_value

# The runtimes a listing can require. Metamod is the loader underneath
# CounterStrikeSharp, not an alternative to it, so it never counts as a
# conflicting runtime here.
PLUGIN_RUNTIMES: tuple[str, ...] = tuple(item.value for item in PLUGIN_FRAMEWORK_SECTIONS)


def evaluate_framework_compatibility(
    plugin_framework: PluginFramework | str | None,
    installed_frameworks: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare the listing's runtime with the runtimes detected on the server.

    ``mismatch`` is only true when the plugin's runtime is absent *and* the
    other runtime is present — that is the case where the operator is about to
    install something that provably cannot load. A server with neither runtime
    yet reports ``missing`` without a mismatch, because installing the runtime
    afterwards is a normal order of operations. Listings marked ``other``
    declare no runtime and are never restricted.
    """
    required = framework_value(plugin_framework)
    frameworks = installed_frameworks or {}
    installed = [name for name in PLUGIN_RUNTIMES if frameworks.get(name)]
    if required not in PLUGIN_RUNTIMES:
        return {
            "plugin": required,
            "installed": installed,
            "conflicting": [],
            "missing": False,
            "mismatch": False,
        }
    missing = required not in installed
    conflicting = [name for name in installed if name != required]
    return {
        "plugin": required,
        "installed": installed,
        "conflicting": conflicting,
        "missing": missing,
        "mismatch": bool(missing and conflicting),
    }


def framework_mismatch_message(compatibility: Mapping[str, Any]) -> str:
    """One-line reason used by API errors and audit details."""
    conflicting = ", ".join(str(item) for item in compatibility.get("conflicting") or [])
    return (
        f"This server runs {conflicting or 'a different runtime'}; "
        f"{compatibility.get('plugin')} plugins do not load on it"
    )
