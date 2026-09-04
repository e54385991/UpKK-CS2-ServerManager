"""补充 MapChooser 配置解析器的格式、类型和编辑边界。"""

from __future__ import annotations

import math

import pytest

from services.map_management_service import (
    DEFAULT_MAPS_CONFIG,
    DEFAULT_PLUGIN_CONFIG_CONTENT,
    MapConfigError,
    PluginConfigError,
    append_map_to_config,
    build_plugin_config_fields,
    normalize_workshop_id,
    parse_maps_config,
    parse_plugin_config,
    remove_map_from_config,
    render_map_block,
    render_official_maps_config,
    sanitize_map_name,
    set_map_enabled,
    update_plugin_config,
    validate_restricted_times,
)


def test_plugin_config_jsonc_types_and_limits():
    content = '\ufeff{ "VoteDuration": 10, // comment\n "unknown": [1,], "text": "// kept" }'
    parsed = parse_plugin_config(content)
    assert parsed["text"] == "// kept"
    fields, unsupported = build_plugin_config_fields(parsed)
    assert any(field["key"] == "VoteDuration" for field in fields)
    assert unsupported == ["unknown"]
    for value in ("", "[]", "{bad", '{"VoteDuration": NaN}'):
        with pytest.raises(PluginConfigError):
            parse_plugin_config(value)
    with pytest.raises(PluginConfigError, match="comment"):
        parse_plugin_config('{"x": /* unterminated}')
    with pytest.raises(PluginConfigError, match="256 KiB"):
        parse_plugin_config('{"x":"' + ("a" * (256 * 1024)) + '"}')
    with pytest.raises(PluginConfigError, match="must be a boolean"):
        build_plugin_config_fields({"AllowExtend": 1})
    with pytest.raises(PluginConfigError, match="boolean"):
        build_plugin_config_fields({"AllowExtend": {"nested": True}})

    for key, value in (
        ("AllowExtend", 1),
        ("ExtendLimit", True),
        ("VoteDuration", math.inf),
        ("VoteDuration", 0),
        ("VotePercent", 2),
        ("VoteStartSound", "x" * 5000),
    ):
        with pytest.raises(PluginConfigError):
            update_plugin_config(DEFAULT_PLUGIN_CONFIG_CONTENT, {key: value})
    with pytest.raises(PluginConfigError, match="Unknown"):
        update_plugin_config(DEFAULT_PLUGIN_CONFIG_CONTENT, {"not_known": 1})
    with pytest.raises(PluginConfigError, match="complex"):
        update_plugin_config('{"nested":{"a":1}}', {"nested": 2})
    updated = update_plugin_config(
        DEFAULT_PLUGIN_CONFIG_CONTENT,
        {"ChangeMapUse_host_workshop_map": True},
    )
    assert parse_plugin_config(updated)["ChangeMapUse_host_workshop_map"] is True
    added = update_plugin_config("{}", {"VoteDuration": 15}, allow_missing_known_fields=True)
    assert parse_plugin_config(added)["VoteDuration"] == 15.0


def test_keyvalues_parser_errors_and_map_metadata():
    with pytest.raises(MapConfigError):
        parse_maps_config("")
    for content in ('"Other"\n{}\n', '"Maplist"\n{\n"x"\n}\n', '"Maplist"\n{\n"x"\n{\n'):
        with pytest.raises(MapConfigError):
            parse_maps_config(content)
    with pytest.raises(MapConfigError, match="Unterminated"):
        parse_maps_config('"Maplist" { "x }')
    with pytest.raises(MapConfigError, match="more than once"):
        parse_maps_config(
            '"Maplist" { "a" { "workshop_id" "123" } "b" { "workshop_id" "123" } }'
        )
    with pytest.raises(MapConfigError, match="invalid workshop"):
        parse_maps_config('"Maplist" { "a" { "workshop_id" "abc" } }')
    with pytest.raises(MapConfigError, match="must be an object"):
        parse_maps_config('"Maplist" { "a" "value" }')
    content = (
        '"Maplist"\n{\n'
        ' "Dust" { "workshop_id" "0" "enabled" "0" "MinPlayers" "4" '
        '"OnlyNominate" "1" "RestrictedTimes" "10:00-11:00" }\n}\n'
    )
    result = parse_maps_config(content)
    assert result.maps[0]["enabled"] is False
    assert result.maps[0]["only_nominate"] is True
    assert result.maps[0]["min_players"] == "4"


def test_map_names_restricted_times_and_rendering():
    assert normalize_workshop_id("123456") == "123456"
    assert normalize_workshop_id("https://steamcommunity.com/sharedfiles/filedetails/?id=123456") == "123456"
    assert normalize_workshop_id("https://www.steamcommunity.com/sharedfiles/?id=123456") == "123456"
    for value in ("", "123", "0123456", "https://example.com/?id=123456"):
        with pytest.raises(MapConfigError):
            normalize_workshop_id(value)
    assert sanitize_map_name('  a"b\\c\n d  ') == "a'b/c d"
    with pytest.raises(MapConfigError):
        sanitize_map_name("\x00")
    with pytest.raises(MapConfigError, match="128"):
        sanitize_map_name("x" * 129)
    assert validate_restricted_times(" 10:00-11:00;12:30-13:00 ") == "10:00-11:00;12:30-13:00"
    with pytest.raises(MapConfigError):
        validate_restricted_times("bad")
    with pytest.raises(MapConfigError):
        validate_restricted_times("10:00-25:00")
    block = render_map_block(name='de_dust"2', workshop_id="123456", enabled=False, min_players=2, only_nominate=True)
    assert '"enabled"\t"0"' in block
    official = render_official_maps_config(["de_z", "DE_Z", "de_a"])
    assert official.index('"de_a"') < official.index('"de_z"')
    with pytest.raises(MapConfigError):
        render_official_maps_config([])


def test_map_edit_operations_and_duplicates():
    content = append_map_to_config(DEFAULT_MAPS_CONFIG, name="de_dust2", workshop_id="123456")
    with pytest.raises(MapConfigError, match="already exists"):
        append_map_to_config(content, name="other", workshop_id="123456")
    with pytest.raises(MapConfigError, match="already exists"):
        append_map_to_config(content, name="DE_DUST2", workshop_id="999999")
    enabled = set_map_enabled(content, name="de_dust2", workshop_id="123456", enabled=False)
    assert '"enabled"\t"0"' in enabled
    restored = set_map_enabled(enabled, name="de_dust2", workshop_id="123456", enabled=True)
    assert '"enabled"\t"1"' in restored
    no_enabled = (
        '"Maplist" { "de_dust2" { "workshop_id" "123456" "filename" "de_dust2" } }'
    )
    inserted = set_map_enabled(no_enabled, name="de_dust2", workshop_id="123456", enabled=True)
    assert '"enabled"' in inserted
    removed = remove_map_from_config(restored, name="de_dust2", workshop_id="123456")
    assert parse_maps_config(removed).maps == []
    with pytest.raises(MapConfigError, match="not found"):
        remove_map_from_config(DEFAULT_MAPS_CONFIG, name="none", workshop_id="123456")
