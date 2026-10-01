"""MapChooser map-list parsing and update helpers.

The CS2-Upkk-PanelPLG-Mapchooser plugin stores its map pool as Valve KeyValues in
``configs/plugins/MapChooser/maps.txt``.  This module intentionally keeps the
raw document for writes so comments and fields unknown to the panel survive a
quick-add operation.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

DEFAULT_MAPS_CONFIG = '"Maplist"\n{\n}\n'

MAX_MAPS_CONFIG_BYTES = 15 * 1024 * 1024

MAX_PLUGIN_CONFIG_BYTES = 256 * 1024

DEFAULT_PLUGIN_CONFIG: dict[str, object] = {
    "VoteStartTime": 3.0,
    "AllowExtend": True,
    "ExtendTimeStep": 10.0,
    "ExtendLimit": 3,
    "ExcludeMaps": 0,
    "IncludeMaps": 5,
    "IncludeCurrent": False,
    "DontChangeRTV": True,
    "VoteDuration": 15.0,
    "IgnoreSpec": True,
    "AllowRtv": True,
    "UseGameTimeLimit": True,
    "RTVPercent": 0.6,
    "RTVDelay": 3.0,
    "EnforceTimeLimit": True,
    "ChangeMapUse_host_workshop_map": False,
    "DisplayHudTimeleftRemaining": 0,
}

DEFAULT_PLUGIN_CONFIG_CONTENT = (
    json.dumps(
        DEFAULT_PLUGIN_CONFIG,
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)

PLUGIN_CONFIG_FIELD_SPECS: dict[str, dict[str, object]] = {
    "VoteStartTime": {"kind": "number", "group": "vote", "min": 0, "step": 0.5},
    "AllowExtend": {"kind": "boolean", "group": "extend"},
    "ExtendTimeStep": {"kind": "number", "group": "extend", "min": 0, "step": 0.5},
    "ExtendLimit": {"kind": "integer", "group": "extend", "min": 0, "step": 1},
    "ExcludeMaps": {"kind": "integer", "group": "mapPool", "min": 0, "step": 1},
    "IncludeMaps": {"kind": "integer", "group": "mapPool", "min": 1, "step": 1},
    "IncludeCurrent": {"kind": "boolean", "group": "mapPool"},
    "DontChangeRTV": {"kind": "boolean", "group": "rtv"},
    "VoteDuration": {"kind": "number", "group": "vote", "min": 1, "max": 60, "step": 1},
    "IgnoreSpec": {"kind": "boolean", "group": "vote"},
    "AllowRtv": {"kind": "boolean", "group": "rtv"},
    "UseGameTimeLimit": {"kind": "boolean", "group": "mapChange"},
    "RTVPercent": {"kind": "number", "group": "rtv", "min": 0, "max": 1, "step": 0.05},
    "RTVDelay": {"kind": "number", "group": "rtv", "min": 0, "step": 0.5},
    "EnforceTimeLimit": {"kind": "boolean", "group": "mapChange"},
    "ChangeMapUse_host_workshop_map": {"kind": "boolean", "group": "mapChange"},
    "DisplayHudTimeleftRemaining": {"kind": "integer", "group": "display", "min": 0, "step": 1},
    "RunOfFVote": {"kind": "boolean", "group": "vote"},
    "VotePercent": {"kind": "number", "group": "vote", "min": 0, "max": 1, "step": 0.05},
    "AutoDownload": {"kind": "boolean", "group": "mapPool"},
    "VoteStartSound": {"kind": "string", "group": "display", "maxlength": 4096},
}


class MapConfigError(ValueError):
    """Raised when a MapChooser maps.txt document is malformed."""


class PluginConfigError(ValueError):
    """Raised when a MapChooser config.json document or update is invalid."""


def _mask_jsonc_block(content: str, output: list[str], index: int) -> int:
    comment_line = content.count("\n", 0, index) + 1
    output[index] = output[index + 1] = " "
    index += 2
    while index < len(content) and not content.startswith("*/", index):
        if content[index] not in "\r\n":
            output[index] = " "
        index += 1
    if index >= len(content):
        raise PluginConfigError(f"Unterminated JSONC block comment at line {comment_line}")
    output[index] = output[index + 1] = " "
    index += 2
    return index


def _mask_jsonc_comments(content: str) -> str:
    output = list(content)
    index = 0
    in_string = False
    escaped = False

    while index < len(content):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if content.startswith("//", index):
            output[index] = output[index + 1] = " "
            index += 2
            while index < len(content) and content[index] not in "\r\n":
                output[index] = " "
                index += 1
            continue

        if content.startswith("/*", index):
            index = _mask_jsonc_block(content, output, index)
            continue

        index += 1

    return "".join(output)


def _remove_trailing_commas(normalized: str) -> str:
    output = list(normalized)
    index = 0
    in_string = False
    escaped = False
    while index < len(normalized):
        char = normalized[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == ",":
            lookahead = index + 1
            while lookahead < len(normalized) and normalized[lookahead].isspace():
                lookahead += 1
            if lookahead < len(normalized) and normalized[lookahead] in "}]":
                output[index] = " "
        index += 1
    return "".join(output)


def _jsonc_to_json(content: str) -> str:
    """Remove JSONC comments and trailing commas while preserving positions."""
    return _remove_trailing_commas(_mask_jsonc_comments(content))


def parse_plugin_config(content: str) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise PluginConfigError("config.json cannot be empty")
    if len(content.encode("utf-8")) > MAX_PLUGIN_CONFIG_BYTES:
        raise PluginConfigError("config.json exceeds the 256 KiB size limit")

    # CounterStrikeSharp plugin configurations are commonly written by .NET
    # tooling, which may prefix UTF-8 JSON with a BOM.  Python's json.loads
    # rejects that marker when it receives an already-decoded string, so remove
    # it at the document boundary.  update_plugin_config serializes the parsed
    # object again and consequently also repairs the remote file on save.
    content = _jsonc_to_json(content.lstrip("\ufeff"))

    def reject_nonstandard_number(value: str) -> None:
        raise PluginConfigError(f"config.json contains the non-standard number {value}")

    try:
        parsed = json.loads(content, parse_constant=reject_nonstandard_number)
    except PluginConfigError:
        raise
    except json.JSONDecodeError as exc:
        raise PluginConfigError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(parsed, dict):
        raise PluginConfigError("config.json must contain a JSON object")
    return parsed


def _inferred_config_kind(value: object) -> Optional[str]:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float) and math.isfinite(value):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def build_plugin_config_fields(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    fields: list[dict[str, Any]] = []
    unsupported_fields: list[str] = []
    for key, value in config.items():
        spec = PLUGIN_CONFIG_FIELD_SPECS.get(key, {})
        inferred_kind = _inferred_config_kind(value)
        if inferred_kind is None:
            if spec:
                raise PluginConfigError(f"{key} must be a {spec['kind']}")
            unsupported_fields.append(key)
            continue

        expected_kind = str(spec.get("kind", inferred_kind))
        valid_kind = inferred_kind == expected_kind or (
            expected_kind == "number" and inferred_kind == "integer"
        )
        if not valid_kind:
            raise PluginConfigError(f"{key} must be a {expected_kind}, not {inferred_kind}")

        field: dict[str, Any] = {
            "key": key,
            "kind": expected_kind,
            "value": value,
            "group": str(spec.get("group", "other")),
            "known": key in PLUGIN_CONFIG_FIELD_SPECS,
        }
        for option in ("min", "max", "step", "maxlength"):
            if option in spec:
                field[option] = spec[option]
        fields.append(field)
    return fields, unsupported_fields


def _validate_plugin_numeric_bounds(key: str, normalized: object, spec: dict[str, object]) -> None:
    minimum = spec.get("min")
    maximum = spec.get("max")
    if (
        isinstance(normalized, (int, float))
        and isinstance(minimum, (int, float))
        and normalized < minimum
    ):
        raise PluginConfigError(f"{key} cannot be less than {minimum}")
    if (
        isinstance(normalized, (int, float))
        and isinstance(maximum, (int, float))
        and normalized > maximum
    ):
        raise PluginConfigError(f"{key} cannot be greater than {maximum}")


def _validated_plugin_value(key: str, value: Any, kind: str, spec: dict[str, object]) -> Any:
    if kind == "boolean":
        if not isinstance(value, bool):
            raise PluginConfigError(f"{key} must be true or false")
        normalized = value
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise PluginConfigError(f"{key} must be an integer")
        normalized = value
    elif kind == "number":
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise PluginConfigError(f"{key} must be a finite number")
        normalized = float(value)
    elif kind == "string":
        if not isinstance(value, str):
            raise PluginConfigError(f"{key} must be text")
        raw_maximum_length = spec.get("maxlength", 4096)
        maximum_length = (
            int(raw_maximum_length) if isinstance(raw_maximum_length, (int, float, str)) else 4096
        )
        if len(value) > maximum_length:
            raise PluginConfigError(f"{key} cannot exceed {maximum_length} characters")
        normalized = value
    else:  # Defensive: only scalar kinds produced above can reach this helper.
        raise PluginConfigError(f"{key} has an unsupported value type")

    if kind in {"integer", "number"}:
        _validate_plugin_numeric_bounds(key, normalized, spec)
    return normalized


def update_plugin_config(
    content: str,
    values: dict[str, Any],
    *,
    allow_missing_known_fields: bool = False,
) -> str:
    config = parse_plugin_config(content)
    fields, _ = build_plugin_config_fields(config)
    fields_by_key = {field["key"]: field for field in fields}

    for key in values:
        if key not in config and not (
            allow_missing_known_fields and key in PLUGIN_CONFIG_FIELD_SPECS
        ):
            raise PluginConfigError(f"Unknown config.json setting: {key}")
        if key in config and key not in fields_by_key:
            raise PluginConfigError(f"{key} is a complex setting and cannot be edited visually")

    for key, value in values.items():
        spec = PLUGIN_CONFIG_FIELD_SPECS.get(key, {})
        kind = str(fields_by_key[key]["kind"]) if key in fields_by_key else str(spec["kind"])
        config[key] = _validated_plugin_value(key, value, kind, spec)

    # json.loads/dumps preserves object order in supported Python versions, so
    # saving keeps the plugin author's field order as well as unknown fields.
    updated = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if len(updated.encode("utf-8")) > MAX_PLUGIN_CONFIG_BYTES:
        raise PluginConfigError("updated config.json exceeds the 256 KiB size limit")
    return updated
