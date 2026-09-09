"""Semantic query groups remain bounded and cannot override repository scope."""

import json

import pytest

from services.plugins.ai_discovery import FRAMEWORK_TERMS, MAX_SEARCH_TERMS
from services.plugins.ai_search_plan import compile_group, parse_plan, planned_searches


def test_plan_separates_synonyms_deduplicates_groups_and_keeps_frameworks():
    plan = parse_plan(
        json.dumps(
            {
                "swiftly": [
                    {"terms": ["retakes"]},
                    {"terms": ["practice", "retake"]},
                    {"terms": ["Retake", "Practice"]},
                ],
                "counterstrikesharp": [{"terms": ["rank"], "topics": ["cs2-plugin"]}],
                "other": [{"terms": ["server", "utility"]}],
            }
        ),
        ["swiftly", "counterstrikesharp", "other"],
    )
    assert len(plan["swiftly"]) == 2
    assert plan["counterstrikesharp"] == [
        "CounterStrikeSharp rank in:name,description topic:cs2-plugin"
    ]
    assert plan["other"] == ["CS2 server utility in:name,description"]
    queries = planned_searches("swiftly", "回防练习", plan["swiftly"])
    assert queries[:2] == plan["swiftly"]
    assert queries[2:] == list(FRAMEWORK_TERMS["swiftly"])


@pytest.mark.parametrize(
    "group",
    [
        None,
        [],
        "Swift plugin",
        {"query": "stars:>1"},
        {"terms": ["stars:>500", "OR", "is:private", "-plugin"]},
        {"terms": ["Swiftly", "Swift"]},
        {"terms": [123, None]},
        {"terms": ["vip -rank"]},
    ],
)
def test_invalid_or_unscoped_groups_cannot_become_broad_searches(group):
    assert compile_group("swiftly", group) is None


def test_qualifiers_are_only_emitted_by_compiler():
    query = compile_group(
        "swiftly",
        {
            "terms": ["rank", "fork:true", "retakes OR stars:>10"],
            "topics": ["cs2-plugin is:private"],
        },
    )
    assert query == "SwiftlyS2 rank in:name,description"


def test_missing_framework_and_invalid_payload_use_bounded_fallbacks():
    assert parse_plan("[]", ["swiftly"]) == {}
    assert parse_plan('{"swiftly": "invalid"}', ["swiftly"]) == {}
    assert planned_searches("swiftly", "vip", []) == [
        f"{term} vip" for term in FRAMEWORK_TERMS["swiftly"]
    ]
    with pytest.raises(ValueError):
        parse_plan("not JSON", ["swiftly"])


def test_plan_cap_counts_valid_unique_groups_not_duplicate_or_invalid_entries():
    groups = [{"terms": ["rank"]}, {"terms": ["RANK"]}, {"query": "bad"}]
    groups += [{"terms": [f"feature{i}"]} for i in range(20)]
    plan = parse_plan(json.dumps({"swiftly": groups}), ["swiftly"])
    assert len(plan["swiftly"]) == 6
    assert len(planned_searches("swiftly", "", plan["swiftly"])) == MAX_SEARCH_TERMS


def test_query_size_and_negations_are_bounded():
    assert compile_group("swiftly", {"terms": ["a" * 48, "b" * 48, "c" * 48]}) is None
    assert compile_group("swiftly", {"terms": ["rank"], "topics": ["cs2-plugin"]})
