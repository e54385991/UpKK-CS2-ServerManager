"""AI 导入的「待处理需求」只写入能精确识别的运行依赖，其余转为安装提示。"""

from __future__ import annotations

import pytest

from services.plugins.ai_requirements import (
    recognized_prerequisites,
    requirement_label,
    split_requirements,
)


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("Requires Metamod:Source", ["Metamod:Source"]),
        ("需要先安装 metamod", ["Metamod:Source"]),
        ("Install SourceMM before the plugin", ["Metamod:Source"]),
        ("CounterStrikeSharp v1.0.300 or newer", ["CounterStrikeSharp"]),
        ("counter-strike-sharp is required", ["CounterStrikeSharp"]),
        ("Depends on SwiftlyS2", ["SwiftlyS2"]),
        ("Requires Swiftly Core", ["SwiftlyS2"]),
        ("Needs CS2Fixes", ["CS2Fixes"]),
        ("Requires MultiAddonManager", ["MultiAddonManager"]),
        ("Needs Metamod:Source and CounterStrikeSharp", ["Metamod:Source", "CounterStrikeSharp"]),
    ],
)
def test_named_runtimes_are_recognized(sentence, expected):
    assert recognized_prerequisites(sentence) == expected


@pytest.mark.parametrize(
    "sentence",
    [
        "Configure a MySQL database in config.json",
        "Edit the CSS stylesheet shipped with the web panel",
        "本插件无需 Metamod",
        "Does not require CounterStrikeSharp",
        "Works alongside SwiftlyS2",
        "Metamod support is optional",
        # "swiftly" here is an ordinary adverb, not the SwiftlyS2 runtime.
        "The plugin reloads swiftly after a map change",
        "",
        "   ",
    ],
)
def test_vague_or_negated_sentences_are_not_prerequisites(sentence):
    assert recognized_prerequisites(sentence) == []


def test_split_keeps_precise_lines_and_demotes_the_rest():
    requirements, notes = split_requirements(
        [
            "  需要   Metamod:Source  ",
            "Requires metamod-source",
            "Configure a MySQL database first",
            "Configure a MySQL database first",
            "",
        ]
    )

    # Deduplicated and canonical, so the review form shows one line per runtime.
    assert requirements == [requirement_label("Metamod:Source")]
    assert notes == ["Configure a MySQL database first"]


def test_notes_are_bounded_to_the_stored_field_limit():
    _requirements, notes = split_requirements(["z" * 5_000])
    assert len(notes[0]) == 1_000
