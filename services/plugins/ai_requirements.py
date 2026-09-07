"""Split AI-suggested prerequisites into recognized runtimes and advisory notes.

The importer used to store every sentence the model produced under
``PluginAIInfo.requirements``, and a non-empty list made the install preflight
fail with "AI installation settings have unresolved requirements; administrator
review required". Most of those sentences only restated the README, so a whole
AI import turned into a catalogue nobody could install from.

A requirement is now only recorded when it names a runtime the panel actually
knows — Metamod:Source, CounterStrikeSharp, SwiftlyS2 and the loaders that ship
alongside them. Everything else becomes a note: the console shows notes before
an install so the operator can check them, but they never block one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Canonical label -> the spellings that identify it. Matching runs against the
# alphanumeric-only form of the sentence, so "Counter-Strike Sharp" and
# "counterstrikesharp" collapse onto the same marker. Every marker is long
# enough to be unambiguous on its own; short forms such as "CS#" are left out
# deliberately because they also match unrelated prose.
_RECOGNIZED_PREREQUISITES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Metamod:Source", ("metamod", "metamodsource", "sourcemm")),
    ("CounterStrikeSharp", ("counterstrikesharp", "counterstrikessharp", "cssharp")),
    # Not bare "swiftly": that is also an ordinary English adverb.
    ("SwiftlyS2", ("swiftlys2", "swiftlycore", "swiftlyplugin", "swiftlysolution")),
    ("CS2Fixes", ("cs2fixes",)),
    ("MultiAddonManager", ("multiaddonmanager",)),
)

# A sentence that says a runtime is *not* needed, or that the plugin merely runs
# next to one, is not a prerequisite. Anything ambiguous falls through to a note
# rather than becoming a requirement the operator has to clear.
_NEGATIONS = (
    "notrequire",
    "notneed",
    "noneed",
    "without",
    "doesnotrequire",
    "nodependency",
    "standalone",
    "optional",
    "compatiblewith",
    "worksalongside",
    "无需",
    "不需要",
    "不依赖",
    "可选",
)

_MAX_REQUIREMENT_LENGTH = 1000
_NOISE = re.compile(r"[^0-9a-z一-鿿]+")


def _normalize(value: str) -> str:
    """Lowercase and drop everything that is not a letter, digit or CJK char."""
    return _NOISE.sub("", value.casefold())


def recognized_prerequisites(value: str) -> list[str]:
    """Return the canonical runtimes a single requirement sentence names."""
    normalized = _normalize(value)
    if not normalized or any(marker in normalized for marker in _NEGATIONS):
        return []
    return [
        label
        for label, markers in _RECOGNIZED_PREREQUISITES
        if any(marker in normalized for marker in markers)
    ]


def requirement_label(prerequisite: str) -> str:
    return f"Requires {prerequisite}"


def split_requirements(values: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split model requirements into precise prerequisites and advisory notes.

    Returns ``(requirements, notes)``. Requirements are canonical, deduplicated
    ``"Requires <runtime>"`` lines; notes keep the model's original wording,
    bounded to the field limit, for anything that could not be recognized.
    """
    requirements: list[str] = []
    notes: list[str] = []
    for raw in values:
        value = " ".join(str(raw).split())
        if not value:
            continue
        matched = recognized_prerequisites(value)
        if matched:
            requirements.extend(requirement_label(label) for label in matched)
            continue
        notes.append(value[:_MAX_REQUIREMENT_LENGTH])
    return list(dict.fromkeys(requirements)), list(dict.fromkeys(notes))
