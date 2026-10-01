"""MapChooser map-list parsing and update helpers.

The CS2-Upkk-PanelPLG-Mapchooser plugin stores its map pool as Valve KeyValues in
``configs/plugins/MapChooser/maps.txt``.  This module intentionally keeps the
raw document for writes so comments and fields unknown to the panel survive a
quick-add operation.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .map_keyvalues import ParsedMapsConfig as ParsedMapsConfig
from .map_keyvalues import _map_string_token as _map_string_token
from .map_keyvalues import _maps_from_root as _maps_from_root
from .map_keyvalues import _Node as _Node
from .map_keyvalues import _parse_root as _parse_root
from .map_keyvalues import _Parser as _Parser
from .map_keyvalues import _Token as _Token
from .map_keyvalues import _tokenize as _tokenize
from .map_keyvalues import content_revision as content_revision
from .map_keyvalues import parse_maps_config as parse_maps_config
from .map_plugin_config import DEFAULT_MAPS_CONFIG as DEFAULT_MAPS_CONFIG
from .map_plugin_config import DEFAULT_PLUGIN_CONFIG as DEFAULT_PLUGIN_CONFIG
from .map_plugin_config import DEFAULT_PLUGIN_CONFIG_CONTENT as DEFAULT_PLUGIN_CONFIG_CONTENT
from .map_plugin_config import MAX_MAPS_CONFIG_BYTES as MAX_MAPS_CONFIG_BYTES
from .map_plugin_config import MAX_PLUGIN_CONFIG_BYTES as MAX_PLUGIN_CONFIG_BYTES
from .map_plugin_config import PLUGIN_CONFIG_FIELD_SPECS as PLUGIN_CONFIG_FIELD_SPECS
from .map_plugin_config import MapConfigError as MapConfigError
from .map_plugin_config import PluginConfigError as PluginConfigError
from .map_plugin_config import _inferred_config_kind as _inferred_config_kind
from .map_plugin_config import _jsonc_to_json as _jsonc_to_json
from .map_plugin_config import _mask_jsonc_comments as _mask_jsonc_comments
from .map_plugin_config import _remove_trailing_commas as _remove_trailing_commas
from .map_plugin_config import _validated_plugin_value as _validated_plugin_value
from .map_plugin_config import build_plugin_config_fields as build_plugin_config_fields
from .map_plugin_config import parse_plugin_config as parse_plugin_config
from .map_plugin_config import update_plugin_config as update_plugin_config


def normalize_workshop_id(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        raise MapConfigError("Workshop ID or URL is required")

    if candidate.isdigit():
        workshop_id = candidate
    else:
        try:
            parsed = urlparse(candidate)
        except ValueError as exc:
            raise MapConfigError("Invalid Steam Workshop URL") from exc
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "steamcommunity.com",
            "www.steamcommunity.com",
        }:
            raise MapConfigError("Enter a numeric Workshop ID or a steamcommunity.com URL")
        workshop_id = (parse_qs(parsed.query).get("id") or [""])[0]

    if not re.fullmatch(r"[1-9][0-9]{5,19}", workshop_id):
        raise MapConfigError("Workshop ID must be 6 to 20 digits and cannot start with zero")
    return workshop_id


def sanitize_map_name(value: str) -> str:
    # The referenced plugin uses a deliberately simple quote-based parser, so
    # quotes and control characters cannot safely appear in keys or values.
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", (value or "").strip())
    name = name.replace('"', "'").replace("\\", "/")
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        raise MapConfigError("Map name is required")
    if len(name) > 128:
        raise MapConfigError("Map name cannot exceed 128 characters")
    return name


def validate_restricted_times(value: str) -> str:
    restricted = (value or "").strip()
    if not restricted:
        return ""
    period_pattern = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d$")
    periods = [period.strip() for period in restricted.split(";") if period.strip()]
    if not periods or any(not period_pattern.fullmatch(period) for period in periods):
        raise MapConfigError("Restricted times must use HH:mm-HH:mm, separated by semicolons")
    return ";".join(periods)


def _quoted(value: object) -> str:
    safe = str(value).replace("\\", "/").replace('"', "'")
    safe = re.sub(r"[\x00-\x1f\x7f]+", " ", safe)
    return f'"{safe}"'


def render_map_block(
    *,
    name: str,
    workshop_id: str,
    enabled: bool = True,
    min_players: int = 0,
    only_nominate: bool = False,
    restricted_times: str = "",
) -> str:
    safe_name = sanitize_map_name(name)
    safe_restricted_times = validate_restricted_times(restricted_times)
    fields = (
        ("workshop_id", workshop_id),
        ("enabled", "1" if enabled else "0"),
        ("filename", safe_name),
        ("updatedname", safe_name),
        ("MinPlayers", str(min_players) if min_players else ""),
        ("OnlyNominate", "1" if only_nominate else "0"),
        ("RestrictedTimes", safe_restricted_times),
    )
    lines = [f"\t{_quoted(safe_name)}", "\t{"]
    lines.extend(f"\t\t{_quoted(key)}\t{_quoted(value)}" for key, value in fields)
    lines.append("\t}")
    return "\n".join(lines)


def render_official_maps_config(map_names: list[str]) -> str:
    normalized_names: list[str] = []
    seen_names: set[str] = set()
    for name in map_names:
        safe_name = sanitize_map_name(name)
        name_key = safe_name.casefold()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        normalized_names.append(safe_name)

    if not normalized_names:
        raise MapConfigError("No official map VPK files were found")

    lines = ['"Maplist"', "{"]
    for name in sorted(normalized_names, key=str.casefold):
        lines.extend(
            (
                f"\t{_quoted(name)}",
                "\t{",
                '\t\t"enabled"\t"1"',
                f'\t\t"filename"\t{_quoted(name)}',
                f'\t\t"updatedname"\t{_quoted(name)}',
                "\t}",
            )
        )
    lines.extend(("}", ""))
    content = "\n".join(lines)
    parse_maps_config(content)
    return content


def append_map_to_config(
    content: str,
    *,
    name: str,
    workshop_id: str,
    enabled: bool = True,
    min_players: int = 0,
    only_nominate: bool = False,
    restricted_times: str = "",
) -> str:
    parsed = parse_maps_config(content)
    if workshop_id and any(item["workshop_id"] == workshop_id for item in parsed.maps):
        raise MapConfigError(f"Workshop ID {workshop_id} already exists in maps.txt")

    safe_name = sanitize_map_name(name)
    if any(str(item["name"]).casefold() == safe_name.casefold() for item in parsed.maps):
        raise MapConfigError(f"Map name {safe_name!r} already exists in maps.txt")

    block = render_map_block(
        name=safe_name,
        workshop_id=workshop_id,
        enabled=enabled,
        min_players=min_players,
        only_nominate=only_nominate,
        restricted_times=restricted_times,
    )
    before = content[: parsed.root_close_offset]
    after = content[parsed.root_close_offset :]
    separator = "" if before.endswith("\n") else "\n"
    updated = f"{before}{separator}{block}\n{after}"
    parse_maps_config(updated)
    return updated


def _find_map_node(content: str, *, name: str, workshop_id: str) -> _Node:
    root = _parse_root(content)
    maps = _maps_from_root(root)
    assert root.children is not None
    matches = [
        node
        for node, item in zip(root.children, maps, strict=False)
        if item["name"] == name and item["workshop_id"] == workshop_id
    ]
    if not matches:
        raise MapConfigError(f"Map {name!r} ({workshop_id}) was not found in maps.txt")
    if len(matches) > 1:
        raise MapConfigError(f"Map {name!r} ({workshop_id}) is ambiguous in maps.txt")
    return matches[0]


def set_map_enabled(
    content: str,
    *,
    name: str,
    workshop_id: str,
    enabled: bool,
) -> str:
    node = _find_map_node(content, name=name, workshop_id=workshop_id)
    assert node.children is not None
    enabled_field = next(
        (
            field
            for field in reversed(node.children)
            if field.children is None and field.name.lower() == "enabled"
        ),
        None,
    )
    enabled_value = "1" if enabled else "0"

    if enabled_field is not None:
        assert enabled_field.value_start_offset is not None
        assert enabled_field.value_end_offset is not None
        updated = (
            content[: enabled_field.value_start_offset]
            + _quoted(enabled_value)
            + content[enabled_field.value_end_offset :]
        )
    else:
        assert node.close_offset is not None
        close_line_start = content.rfind("\n", 0, node.close_offset) + 1
        close_prefix = content[close_line_start : node.close_offset]
        insert_offset = close_line_start if close_prefix.strip() == "" else node.close_offset

        field_indent = "\t\t"
        if node.children:
            first_child = node.children[0]
            child_line_start = content.rfind("\n", 0, first_child.start_offset) + 1
            child_prefix = content[child_line_start : first_child.start_offset]
            if child_prefix.strip() == "":
                field_indent = child_prefix
        else:
            node_line_start = content.rfind("\n", 0, node.start_offset) + 1
            node_prefix = content[node_line_start : node.start_offset]
            if node_prefix.strip() == "":
                field_indent = f"{node_prefix}\t"

        separator = "" if insert_offset == 0 or content[:insert_offset].endswith("\n") else "\n"
        field_line = f'{field_indent}"enabled"\t{_quoted(enabled_value)}\n'
        updated = content[:insert_offset] + separator + field_line + content[insert_offset:]

    parse_maps_config(updated)
    return updated


def remove_map_from_config(content: str, *, name: str, workshop_id: str) -> str:
    node = _find_map_node(content, name=name, workshop_id=workshop_id)
    start_offset = node.start_offset
    line_start = content.rfind("\n", 0, start_offset) + 1
    if content[line_start:start_offset].strip() == "":
        start_offset = line_start

    end_offset = node.end_offset
    while end_offset < len(content) and content[end_offset] in " \t\r":
        end_offset += 1
    if end_offset < len(content) and content[end_offset] == "\n":
        end_offset += 1

    updated = content[:start_offset] + content[end_offset:]
    parse_maps_config(updated)
    return updated
