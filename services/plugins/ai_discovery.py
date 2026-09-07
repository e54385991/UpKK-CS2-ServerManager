"""Query planning, freshness and dependency policy for the AI importer.

Three things live here rather than in ``ai_import_runner`` so the runner stays a
readable pipeline:

* **Search planning** — one hand-written query per framework was a narrow view of
  GitHub. The importer now sweeps several deterministic queries (product name,
  topic, install path) and may add model-proposed ones, with every qualifier the
  panel controls stripped back out so a proposed query cannot widen the star,
  fork or freshness filters the administrator set.
* **Freshness** — the GitHub search filter is only a first pass. Explicitly
  listed repositories and dependencies never went through it at all, which is
  how repositories untouched for years still reached the marketplace. The runner
  re-checks ``pushed_at`` against the submitted window and says what it did.
* **Dependencies** — a prerequisite naming a runtime the panel already knows
  resolves to that runtime's canonical repository, so the imported entry links a
  real dependency row instead of only carrying a sentence to read.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone

from modules.plugin_ai import repository_url
from services.plugins.ai_requirements import requirement_label

# The deterministic terms always run; the remainder is what a model may add.
# Every term costs one throttled GitHub search per page, so the sweep for both
# frameworks stays a few minutes even at the ceiling.
MAX_SEARCH_TERMS = 8
MAX_PROPOSED_TERMS = 4
SEARCH_PAGES = 2
DEPENDENCY_ATTEMPTS = 3

# Deterministic starting queries. The first entry of each list stays the plain
# product name so behaviour without model expansion matches the previous sweep.
FRAMEWORK_TERMS: dict[str, tuple[str, ...]] = {
    "counterstrikesharp": (
        "CounterStrikeSharp",
        "topic:counterstrikesharp",
        "CounterStrikeSharp plugin cs2",
        "CSSharp cs2 server plugin",
    ),
    "swiftly": (
        "SwiftlyS2",
        "topic:swiftlys2",
        "SwiftlyS2 plugin cs2",
        "swiftly-solution cs2 plugin",
    ),
}

# Qualifiers the panel sets itself from the submitted options. A model-proposed
# query may add `topic:` or `language:`, but never these.
RESERVED_QUALIFIERS = frozenset(
    {
        "archived",
        "created",
        "fork",
        "forks",
        "is",
        "pushed",
        "sort",
        "stars",
    }
)

# Canonical repositories for the runtimes ``ai_requirements`` recognizes. These
# come from the panel, not from a model or a README, so the importer may follow
# them without the "URL appears in the retrieved documents" check that guards
# model-supplied dependency URLs.
RUNTIME_REPOSITORIES: dict[str, str] = {
    "Metamod:Source": "https://github.com/alliedmodders/metamod-source",
    "CounterStrikeSharp": "https://github.com/roflmuffin/counterstrikesharp",
    "SwiftlyS2": "https://github.com/swiftly-solution/swiftly",
    "CS2Fixes": "https://github.com/source2ze/cs2fixes",
    "MultiAddonManager": "https://github.com/source2ze/multiaddonmanager",
}

TRUSTED_DEPENDENCIES = frozenset(repository_url(value) for value in RUNTIME_REPOSITORIES.values())

_LABEL_BY_REQUIREMENT = {
    requirement_label(label): repository_url(url) for label, url in RUNTIME_REPOSITORIES.items()
}

_TERM_NOISE = re.compile(r"[^0-9A-Za-z_.:\-+#/ ]+")


class DependencyResolutionError(RuntimeError):
    """A prerequisite could not be imported, so the plugin must not be listed."""


def runtime_repository(requirement: str) -> str | None:
    """Map a canonical ``Requires <runtime>`` line to that runtime's repository."""
    return _LABEL_BY_REQUIREMENT.get(requirement.strip())


def sanitize_term(value: object) -> str | None:
    """Reduce a proposed query to plain terms plus qualifiers the panel allows.

    Reserved qualifiers are dropped whole. Stripping their punctuation first
    would leave the bare value behind — ``stars:>500`` becoming a stray ``500``
    search term — so tokens are classified before any character is removed.
    """
    kept: list[str] = []
    for token in " ".join(str(value).split())[:200].split(" "):
        head, separator, _ = token.partition(":")
        if separator and head.casefold() in RESERVED_QUALIFIERS:
            continue
        cleaned = " ".join(_TERM_NOISE.sub(" ", token).split())
        if cleaned:
            kept.append(cleaned)
    return " ".join(kept)[:120].strip() or None


def search_terms(framework: str, keywords: str, proposed: list[str] | None = None) -> list[str]:
    """Build the bounded, deduplicated query sweep for one framework."""
    suffix = keywords.strip()
    terms = [f"{base} {suffix}".strip() for base in FRAMEWORK_TERMS.get(framework, ())]
    extra: list[str] = []
    for candidate in proposed or []:
        cleaned = sanitize_term(candidate)
        if cleaned:
            extra.append(f"{cleaned} {suffix}".strip())
    room = max(0, MAX_SEARCH_TERMS - len(terms))
    combined = [*terms, *extra[: min(room, MAX_PROPOSED_TERMS)]]
    return list(dict.fromkeys(combined))[:MAX_SEARCH_TERMS]


def repository_age_days(repo: dict[str, object]) -> int | None:
    """Days since the repository last received a push, or ``None`` if unknown."""
    for field in ("pushed_at", "updated_at"):
        raw = repo.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - moment).days)
    return None


def sort_value(repo: dict[str, object], key: str) -> float:
    """One comparable, descending-ordered ranking component."""
    if key == "updated":
        age = repository_age_days(repo)
        return -float(age) if age is not None else float("-inf")
    raw = repo.get("stargazers_count" if key == "stars" else "forks_count")
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0


def sort_ranking(repo: dict[str, object], priority: Sequence[str]) -> tuple[float, ...]:
    """Rank a repository by every configured key, most significant first."""
    return tuple(sort_value(repo, key) for key in priority)
