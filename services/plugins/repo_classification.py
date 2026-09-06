"""Guess a marketplace listing's runtime and category from its repository.

The administrator still reviews and can override every guess in the add form —
this only pre-fills the two dropdowns so a new listing does not silently keep
the CounterStrikeSharp default when the repository is obviously a SwiftlyS2 or
Metamod-only plugin.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from modules.models.plugins import PluginCategory, PluginFramework

# The README is only skimmed: install instructions and the plugin's own
# namespace/paths live near the top, while a long tail of changelog and credits
# adds false positives.
README_SCAN_CHARS = 6000

# Distinctive markers only. A passing "works great alongside CounterStrikeSharp"
# also matches, which is why both runtimes matching resolves to "no restriction"
# rather than to one of them.
_COUNTERSTRIKESHARP_MARKERS = (
    "counterstrikesharp",
    "counter-strike-sharp",
    "cssharp",
    "css_plugin",
    "minimumapiversion",
)
_SWIFTLY_MARKERS = (
    "swiftlys2",
    "swiftly-s2",
    "swiftly",
)
_METAMOD_MARKERS = (
    "metamod",
    "sourcehook",
    "sourcemm",
)

# Ordered: the first matching group wins, so a "library" repository is not
# re-labelled by a stray "admin" mention further down its README.
_CATEGORY_MARKERS: tuple[tuple[PluginCategory, tuple[str, ...]], ...] = (
    (
        PluginCategory.LIBRARY,
        ("shared library", "plugin library", "dependency", "api wrapper", "sdk", "framework for"),
    ),
    (
        PluginCategory.GAME_MODE,
        (
            "game mode",
            "gamemode",
            "retake",
            "deathmatch",
            "jailbreak",
            "zombie escape",
            "zombie riot",
            "prop hunt",
            "hide and seek",
            "surf",
            "bhop",
            "bunnyhop",
            "kreedz",
            "practice mode",
            "matchmaking",
            "match plugin",
            "5v5",
        ),
    ),
    (
        PluginCategory.ADMIN,
        ("admin", "moderation", "ban system", "mute", "punish", "kick player"),
    ),
    (
        PluginCategory.PERFORMANCE,
        ("performance", "optimization", "optimisation", "reduce lag", "fps boost", "tickrate"),
    ),
    (
        PluginCategory.ENTERTAINMENT,
        ("entertainment", "for fun", "mini game", "minigame", "emoji", "dance", "music player"),
    ),
    (
        PluginCategory.UTILITY,
        (
            "utility",
            "quality of life",
            "advertisement",
            "map chooser",
            "mapchooser",
            "voting",
            "vote system",
            "statistics",
            "ranking",
            "scoreboard",
            "discord webhook",
        ),
    ),
)


def _haystack(
    *,
    name: str | None,
    description: str | None,
    readme: str | None,
    topics: Iterable[str] | None,
) -> str:
    parts = [
        (name or ""),
        (description or ""),
        " ".join(str(topic) for topic in topics or ()),
        (readme or "")[:README_SCAN_CHARS],
    ]
    # Fold separators so "counter-strike-sharp" and "CounterStrikeSharp" both
    # reduce to markers we can substring-match.
    return re.sub(r"\s+", " ", " ".join(parts)).casefold()


def _contains(haystack: str, markers: Iterable[str]) -> bool:
    return any(marker in haystack for marker in markers)


def detect_plugin_framework(
    *,
    name: str | None = None,
    description: str | None = None,
    readme: str | None = None,
    topics: Iterable[str] | None = None,
) -> PluginFramework | None:
    """Return the runtime the repository targets, or ``None`` when unclear.

    A repository that mentions both runtimes, or that is a Metamod plugin with
    no runtime of its own, is classified as ``OTHER`` (no runtime restriction).
    """
    haystack = _haystack(name=name, description=description, readme=readme, topics=topics)
    counterstrikesharp = _contains(haystack, _COUNTERSTRIKESHARP_MARKERS)
    swiftly = _contains(haystack, _SWIFTLY_MARKERS)
    if counterstrikesharp and swiftly:
        return PluginFramework.OTHER
    if counterstrikesharp:
        return PluginFramework.COUNTERSTRIKESHARP
    if swiftly:
        return PluginFramework.SWIFTLY
    if _contains(haystack, _METAMOD_MARKERS):
        return PluginFramework.OTHER
    return None


def suggest_plugin_category(
    *,
    name: str | None = None,
    description: str | None = None,
    readme: str | None = None,
    topics: Iterable[str] | None = None,
) -> PluginCategory | None:
    """Return a marketplace category guess, or ``None`` when nothing matches."""
    haystack = _haystack(name=name, description=description, readme=readme, topics=topics)
    for category, markers in _CATEGORY_MARKERS:
        if _contains(haystack, markers):
            return category
    return None
