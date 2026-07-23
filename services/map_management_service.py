"""MapChooser map-list parsing and update helpers.

The CS2-Upkk-PanelPLG-Mapchooser plugin stores its map pool as Valve KeyValues in
``configs/plugins/MapChooser/maps.txt``.  This module intentionally keeps the
raw document for writes so comments and fields unknown to the panel survive a
quick-add operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

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

# The form is generated from the keys that really exist in config.json.  These
# specifications only add useful input constraints and grouping for known
# MapChooser settings; future scalar settings are still rendered automatically.
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


def _jsonc_to_json(content: str) -> str:
    """Remove JSONC comments and trailing commas while preserving positions."""
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
            continue

        index += 1

    # Comments have become whitespace. A comma followed only by whitespace and
    # a closing object/array delimiter is a JSONC trailing comma.
    normalized = "".join(output)
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
        maximum_length = int(spec.get("maxlength", 4096))
        if len(value) > maximum_length:
            raise PluginConfigError(f"{key} cannot exceed {maximum_length} characters")
        normalized = value
    else:  # Defensive: only scalar kinds produced above can reach this helper.
        raise PluginConfigError(f"{key} has an unsupported value type")

    if kind in {"integer", "number"}:
        minimum = spec.get("min")
        maximum = spec.get("max")
        if minimum is not None and normalized < minimum:
            raise PluginConfigError(f"{key} cannot be less than {minimum}")
        if maximum is not None and normalized > maximum:
            raise PluginConfigError(f"{key} cannot be greater than {maximum}")
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


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int
    line: int


@dataclass
class _Node:
    name: str
    value: Optional[str] = None
    children: Optional[list["_Node"]] = None
    close_offset: Optional[int] = None
    start_offset: int = 0
    end_offset: int = 0
    value_start_offset: Optional[int] = None
    value_end_offset: Optional[int] = None


@dataclass(frozen=True)
class ParsedMapsConfig:
    maps: list[dict[str, object]]
    root_close_offset: int


def content_revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _tokenize(content: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    line = 1
    length = len(content)

    while index < length:
        char = content[index]
        if char.isspace() or char == "\ufeff":
            if char == "\n":
                line += 1
            index += 1
            continue

        if content.startswith("//", index):
            newline = content.find("\n", index + 2)
            if newline == -1:
                break
            index = newline
            continue

        if content.startswith("/*", index):
            end_comment = content.find("*/", index + 2)
            if end_comment == -1:
                raise MapConfigError(f"Unterminated block comment at line {line}")
            line += content.count("\n", index, end_comment + 2)
            index = end_comment + 2
            continue

        if char in "{}":
            tokens.append(_Token(char, char, index, index + 1, line))
            index += 1
            continue

        if char == '"':
            start = index
            start_line = line
            index += 1
            value: list[str] = []
            while index < length:
                current = content[index]
                if current == '"':
                    index += 1
                    tokens.append(_Token("string", "".join(value), start, index, start_line))
                    break
                if current == "\\" and index + 1 < length:
                    escaped = content[index + 1]
                    if escaped in {'"', "\\"}:
                        value.append(escaped)
                        index += 2
                        continue
                if current == "\n":
                    line += 1
                value.append(current)
                index += 1
            else:
                raise MapConfigError(f"Unterminated quoted string at line {start_line}")
            continue

        start = index
        while index < length:
            if content[index].isspace() or content[index] in '{}"':
                break
            if content.startswith("//", index) or content.startswith("/*", index):
                break
            index += 1
        if start == index:
            raise MapConfigError(f"Unexpected character at line {line}")
        tokens.append(_Token("string", content[start:index], start, index, line))

    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.index = 0

    def _peek(self) -> Optional[_Token]:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def _take(self) -> _Token:
        token = self._peek()
        if token is None:
            raise MapConfigError("Unexpected end of maps.txt")
        self.index += 1
        return token

    def parse_node(self) -> _Node:
        key = self._take()
        if key.kind != "string":
            raise MapConfigError(f"Expected a key at line {key.line}")

        next_token = self._take()
        if next_token.kind == "string":
            return _Node(
                name=key.value,
                value=next_token.value,
                start_offset=key.start,
                end_offset=next_token.end,
                value_start_offset=next_token.start,
                value_end_offset=next_token.end,
            )
        if next_token.kind != "{":
            raise MapConfigError(f"Expected a value or '{{' after {key.value!r} at line {key.line}")

        children: list[_Node] = []
        while True:
            token = self._peek()
            if token is None:
                raise MapConfigError(f"Missing closing '}}' for {key.value!r}")
            if token.kind == "}":
                close_token = self._take()
                return _Node(
                    name=key.value,
                    children=children,
                    close_offset=close_token.start,
                    start_offset=key.start,
                    end_offset=close_token.end,
                )
            children.append(self.parse_node())


def _parse_root(content: str) -> _Node:
    if not isinstance(content, str) or not content.strip():
        raise MapConfigError("maps.txt cannot be empty")
    if len(content.encode("utf-8")) > MAX_MAPS_CONFIG_BYTES:
        raise MapConfigError("maps.txt exceeds the 15 MiB size limit")

    parser = _Parser(_tokenize(content))
    root = parser.parse_node()
    if parser._peek() is not None:
        token = parser._peek()
        raise MapConfigError(f"Unexpected content at line {token.line}")
    if root.name.lower() != "maplist" or root.children is None or root.close_offset is None:
        raise MapConfigError('maps.txt must contain one root object named "Maplist"')
    return root


def _maps_from_root(root: _Node) -> list[dict[str, object]]:
    assert root.children is not None

    maps: list[dict[str, object]] = []
    seen_workshop_ids: set[str] = set()
    for child in root.children:
        if child.children is None:
            raise MapConfigError(f"Map entry {child.name!r} must be an object")
        values = {
            field.name.lower(): field.value or ""
            for field in child.children
            if field.children is None
        }
        workshop_id = values.get("workshop_id", "").strip()
        if workshop_id and not workshop_id.isdigit():
            raise MapConfigError(f"Map entry {child.name!r} has an invalid workshop_id")
        if workshop_id and workshop_id != "0" and workshop_id in seen_workshop_ids:
            raise MapConfigError(f"Workshop ID {workshop_id} appears more than once")
        if workshop_id and workshop_id != "0":
            seen_workshop_ids.add(workshop_id)

        maps.append(
            {
                "name": child.name,
                "workshop_id": workshop_id,
                "enabled": values.get("enabled", "1") != "0",
                "filename": values.get("filename", child.name),
                "updated_name": values.get("updatedname", ""),
                "min_players": values.get("minplayers", ""),
                "only_nominate": values.get("onlynominate", "0") == "1",
                "restricted_times": values.get("restrictedtimes", ""),
            }
        )

    return maps


def parse_maps_config(content: str) -> ParsedMapsConfig:
    root = _parse_root(content)
    assert root.close_offset is not None
    return ParsedMapsConfig(
        maps=_maps_from_root(root),
        root_close_offset=root.close_offset,
    )


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
