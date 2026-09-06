"""Runtime guard: a CounterStrikeSharp plugin must not land on a SwiftlyS2 server."""

from __future__ import annotations

import pytest

from modules.models.plugins import PluginFramework
from services.plugin_conflict_service import (
    PluginPlanError,
    validate_plugin_plan_acknowledgements,
)
from services.plugins.framework_compatibility import (
    evaluate_framework_compatibility,
    framework_mismatch_message,
)


def _plan(**framework):
    return {
        "hard_conflicts": [],
        "warnings": [],
        "framework": framework,
    }


def test_matching_runtime_is_not_a_mismatch():
    result = evaluate_framework_compatibility(
        PluginFramework.COUNTERSTRIKESHARP,
        {"metamod": True, "counterstrikesharp": True, "swiftly": False},
    )
    assert result["mismatch"] is False
    assert result["missing"] is False
    assert result["installed"] == ["counterstrikesharp"]


def test_counterstrikesharp_plugin_on_a_swiftly_server_is_a_mismatch():
    result = evaluate_framework_compatibility(
        PluginFramework.COUNTERSTRIKESHARP,
        {"metamod": True, "counterstrikesharp": False, "swiftly": True},
    )
    assert result["mismatch"] is True
    assert result["conflicting"] == ["swiftly"]
    assert "swiftly" in framework_mismatch_message(result)


def test_swiftly_plugin_on_a_counterstrikesharp_server_is_a_mismatch():
    result = evaluate_framework_compatibility(
        PluginFramework.SWIFTLY,
        {"metamod": True, "counterstrikesharp": True, "swiftly": False},
    )
    assert result["mismatch"] is True
    assert result["conflicting"] == ["counterstrikesharp"]
    assert "counterstrikesharp" in framework_mismatch_message(result)


def test_missing_runtime_alone_is_not_a_mismatch():
    result = evaluate_framework_compatibility(
        PluginFramework.COUNTERSTRIKESHARP,
        {"metamod": True, "counterstrikesharp": False, "swiftly": False},
    )
    assert result["missing"] is True
    assert result["mismatch"] is False
    assert result["conflicting"] == []


def test_metamod_alone_never_counts_as_a_conflicting_runtime():
    result = evaluate_framework_compatibility(
        PluginFramework.SWIFTLY, {"metamod": True, "counterstrikesharp": False}
    )
    assert result["conflicting"] == []
    assert result["mismatch"] is False


def test_runtime_agnostic_listings_are_never_restricted():
    for frameworks in (
        {"counterstrikesharp": True, "swiftly": False},
        {"counterstrikesharp": False, "swiftly": True},
        {},
    ):
        result = evaluate_framework_compatibility(PluginFramework.OTHER, frameworks)
        assert result["mismatch"] is False
        assert result["missing"] is False
        assert result["conflicting"] == []


def test_unknown_installed_state_is_treated_as_not_installed():
    result = evaluate_framework_compatibility("counterstrikesharp", None)
    assert result["installed"] == []
    assert result["missing"] is True
    assert result["mismatch"] is False


def test_plan_validation_requires_an_explicit_mismatch_acknowledgement():
    plan = _plan(
        plugin="counterstrikesharp",
        installed=["swiftly"],
        conflicting=["swiftly"],
        missing=True,
        mismatch=True,
    )

    with pytest.raises(PluginPlanError, match="do not load"):
        validate_plugin_plan_acknowledgements(plan, [])

    validate_plugin_plan_acknowledgements(plan, [], acknowledge_framework_mismatch=True)


def test_plan_validation_passes_without_a_mismatch():
    plan = _plan(
        plugin="counterstrikesharp",
        installed=["counterstrikesharp"],
        conflicting=[],
        missing=False,
        mismatch=False,
    )

    validate_plugin_plan_acknowledgements(plan, [])


def test_hard_conflicts_still_take_precedence_over_the_runtime_guard():
    plan = _plan(plugin="counterstrikesharp", mismatch=True)
    plan["hard_conflicts"] = [{"rule_id": 4}]

    with pytest.raises(PluginPlanError, match="hard conflict"):
        validate_plugin_plan_acknowledgements(plan, [], acknowledge_framework_mismatch=True)
