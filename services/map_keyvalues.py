"""MapChooser map-list parsing and update helpers.

The CS2-Upkk-PanelPLG-Mapchooser plugin stores its map pool as Valve KeyValues in
``configs/plugins/MapChooser/maps.txt``.  This module intentionally keeps the
raw document for writes so comments and fields unknown to the panel survive a
quick-add operation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from .map_plugin_config import MAX_MAPS_CONFIG_BYTES, MapConfigError


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


def _map_string_token(content: str, index: int, line: int) -> tuple[_Token, int, int]:
    start = index
    start_line = line
    index += 1
    value: list[str] = []
    while index < len(content):
        current = content[index]
        if current == '"':
            index += 1
            return _Token("string", "".join(value), start, index, start_line), index, line
        if current == "\\" and index + 1 < len(content):
            escaped = content[index + 1]
            if escaped in {'"', "\\"}:
                value.append(escaped)
                index += 2
                continue
        if current == "\n":
            line += 1
        value.append(current)
        index += 1
    raise MapConfigError(f"Unterminated quoted string at line {start_line}")


def _map_unquoted_token(content: str, index: int, line: int) -> tuple[_Token, int]:
    start = index
    while index < len(content):
        if content[index].isspace() or content[index] in '{}"':
            break
        if content.startswith("//", index) or content.startswith("/*", index):
            break
        index += 1
    if start == index:
        raise MapConfigError(f"Unexpected character at line {line}")
    return _Token("string", content[start:index], start, index, line), index


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
            token, index, line = _map_string_token(content, index, line)
            tokens.append(token)
            continue

        token, index = _map_unquoted_token(content, index, line)
        tokens.append(token)

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
    token = parser._peek()
    if token is not None:
        raise MapConfigError(f"Unexpected content at line {token.line or 0}")
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
