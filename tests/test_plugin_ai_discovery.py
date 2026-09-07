"""Search planning, ranking and freshness policy for the AI marketplace importer."""

from datetime import datetime, timedelta, timezone

import pytest

from modules.plugin_ai import DEFAULT_SORT_PRIORITY, ImportOptions
from services.plugins import ai_discovery as discovery


def iso(days_ago: int) -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    )


def test_sort_priority_defaults_to_stars_then_updated_then_forks():
    assert ImportOptions().sort_priority == list(DEFAULT_SORT_PRIORITY)
    assert ImportOptions().sort == "stars"


def test_sort_priority_leads_the_chain_and_mirrors_the_github_sort_key():
    options = ImportOptions(sort_priority=["updated", "forks"])
    assert options.sort_priority == ["updated", "forks"]
    # GitHub's search API takes a single key, so `sort` follows the first one.
    assert options.sort == "updated"


def test_sort_priority_deduplicates_and_never_empties():
    assert ImportOptions(sort_priority=["forks", "forks"]).sort_priority == ["forks"]


def test_legacy_sort_only_payload_becomes_the_primary_key():
    """Jobs and clients written before the chain existed stay valid."""
    options = ImportOptions.model_validate({"sort": "forks"})
    assert options.sort_priority == ["forks", "stars", "updated"]
    assert options.sort == "forks"


def test_ranking_orders_by_each_key_in_turn():
    tied = [
        {"stargazers_count": 5, "forks_count": 1, "pushed_at": iso(200)},
        {"stargazers_count": 5, "forks_count": 9, "pushed_at": iso(2)},
    ]
    ranked = sorted(
        tied, key=lambda row: discovery.sort_ranking(row, ["stars", "updated"]), reverse=True
    )
    assert ranked[0]["pushed_at"] == tied[1]["pushed_at"]
    ranked = sorted(
        tied, key=lambda row: discovery.sort_ranking(row, ["stars", "forks"]), reverse=True
    )
    assert ranked[0]["forks_count"] == 9


def test_repository_age_falls_back_to_updated_at_and_tolerates_junk():
    assert discovery.repository_age_days({"pushed_at": iso(10)}) == 10
    assert discovery.repository_age_days({"updated_at": iso(3)}) == 3
    assert discovery.repository_age_days({"pushed_at": "not-a-date"}) is None
    assert discovery.repository_age_days({}) is None


@pytest.mark.parametrize(
    "proposed,expected",
    [
        ("stars:>500 cs2 plugin", "cs2 plugin"),
        ("pushed:>=2020-01-01 is:public fork:false gamemode", "gamemode"),
        ("topic:cs2 language:csharp", "topic:cs2 language:csharp"),
        ("   ", None),
        ("`rm -rf /` plugin", "rm -rf / plugin"),
    ],
)
def test_proposed_queries_cannot_widen_panel_controlled_filters(proposed, expected):
    assert discovery.sanitize_term(proposed) == expected


def test_search_terms_append_keywords_dedupe_and_stay_bounded():
    terms = discovery.search_terms(
        "counterstrikesharp", "vip", ["CounterStrikeSharp", "stars:>1", "topic:cs2"]
    )
    assert terms[0] == "CounterStrikeSharp vip"
    assert "topic:cs2 vip" in terms
    assert len(terms) == len(set(terms)) <= discovery.MAX_SEARCH_TERMS


def test_recognized_runtimes_map_to_panel_owned_repositories():
    assert (
        discovery.runtime_repository("Requires CounterStrikeSharp")
        == "https://github.com/roflmuffin/counterstrikesharp"
    )
    assert discovery.runtime_repository("Requires a MySQL database") is None
    # Those URLs bypass the "must appear in the documents" guard, so every one
    # of them has to be a repository the panel itself declared.
    assert discovery.TRUSTED_DEPENDENCIES == set(
        discovery.runtime_repository(f"Requires {label}")
        for label in discovery.RUNTIME_REPOSITORIES
    )
