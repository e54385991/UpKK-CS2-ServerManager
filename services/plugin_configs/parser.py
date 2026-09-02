"""Pure, source-preserving plugin configuration parsing and editing."""

from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

MAX_CONFIG_BYTES = 10 * 1024 * 1024
SUPPORTED_DIRECTORY_EXTENSIONS = {
    ".json",
    ".jsonc",
    ".cfg",
    ".ini",
    ".conf",
    ".toml",
    ".yaml",
    ".yml",
    ".vdf",
    ".txt",
}


class PluginConfigError(ValueError):
    """A configuration cannot be safely parsed, validated, or changed."""


@dataclass
class ConfigField:
    field_id: str
    key: str
    group: str
    kind: str
    value: Any
    line: int
    comment: str = ""
    start: int = field(repr=False, default=0)
    end: int = field(repr=False, default=0)
    original: str = field(repr=False, default="")
    style: str = field(repr=False, default="")

    def public(self) -> dict[str, Any]:
        return {
            "id": self.field_id,
            "key": self.key,
            "group": self.group,
            "kind": self.kind,
            "value": self.value,
            "line": self.line,
            "comment": self.comment,
        }


@dataclass
class ParsedConfig:
    format: str
    fields: list[ConfigField]
    visual_supported: bool
    parse_error: Optional[str] = None

    def public_fields(self) -> list[dict[str, Any]]:
        return [item.public() for item in self.fields]


def content_revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def path_hash(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def normalize_relative_path(game_directory: str, requested_path: str) -> str:
    """Normalize an absolute or game-root-relative POSIX path."""
    if not isinstance(requested_path, str) or not requested_path.strip():
        raise PluginConfigError("Path is required")
    value = requested_path.strip()
    if "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PluginConfigError("Path must be a valid POSIX path")

    base = posixpath.normpath(game_directory)
    if value.startswith("/"):
        absolute = posixpath.normpath(value)
        if absolute != base and not absolute.startswith(base.rstrip("/") + "/"):
            raise PluginConfigError("Path is outside the server game directory")
        relative = posixpath.relpath(absolute, base)
    else:
        relative = posixpath.normpath(value)

    if relative.startswith("/") or relative == ".." or relative.startswith("../"):
        raise PluginConfigError("Path is outside the server game directory")
    if len(relative.encode("utf-8")) > 1000:
        raise PluginConfigError("Path is too long")
    return relative


def _extension(filename: str) -> str:
    return posixpath.splitext(filename)[1].lower()


def format_for_filename(filename: str, content: Optional[str] = None) -> str:
    extension = _extension(filename)
    if extension in {".json", ".jsonc"}:
        return "jsonc" if extension == ".jsonc" else "json"
    if extension == ".ini":
        return "ini"
    if extension in {".cfg", ".conf"}:
        if content and re.search(r"(?m)^\s*\[[^\]\r\n]+\]\s*(?:[;#].*)?$", content):
            return "ini"
        return "cfg"
    return "raw"


@dataclass(frozen=True)
class _JsonToken:
    kind: str
    value: Any
    start: int
    end: int
    line: int


def _json_string_token(content: str, index: int, line: int) -> tuple[_JsonToken, int]:
    start = index
    token_line = line
    index += 1
    escaped = False
    while index < len(content):
        current = content[index]
        if current == "\n" and not escaped:
            raise PluginConfigError(f"Unterminated JSON string at line {token_line}")
        if escaped:
            escaped = False
        elif current == "\\":
            escaped = True
        elif current == '"':
            index += 1
            break
        index += 1
    else:
        raise PluginConfigError(f"Unterminated JSON string at line {token_line}")
    raw = content[start:index]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PluginConfigError(f"Invalid JSON string at line {token_line}: {exc.msg}") from exc
    return _JsonToken("string", value, start, index, token_line), index


class _JsoncParser:
    _number = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")

    def __init__(self, content: str) -> None:
        self.content = content
        self.tokens = self._tokenize()
        self.index = 0
        self.fields: list[ConfigField] = []

    def _tokenize(self) -> list[_JsonToken]:
        tokens: list[_JsonToken] = []
        index = 0
        line = 1
        content = self.content
        while index < len(content):
            char = content[index]
            if char == "\ufeff" and index == 0:
                index += 1
                continue
            if char.isspace():
                line += char == "\n"
                index += 1
                continue
            if content.startswith("//", index):
                newline = content.find("\n", index + 2)
                if newline < 0:
                    break
                index = newline
                continue
            if content.startswith("/*", index):
                end = content.find("*/", index + 2)
                if end < 0:
                    raise PluginConfigError(f"Unterminated JSONC comment at line {line}")
                segment = content[index : end + 2]
                line += segment.count("\n")
                index = end + 2
                continue
            if char in "{}[]:,":
                tokens.append(_JsonToken(char, char, index, index + 1, line))
                index += 1
                continue
            if char == '"':
                token, index = _json_string_token(content, index, line)
                tokens.append(token)
                continue
            number = self._number.match(content, index)
            if number:
                raw = number.group(0)
                value: Any = float(raw) if any(c in raw for c in ".eE") else int(raw)
                tokens.append(_JsonToken("number", value, index, number.end(), line))
                index = number.end()
                continue
            matched_keyword = False
            for word, value in (("true", True), ("false", False), ("null", None)):
                if content.startswith(word, index):
                    tokens.append(_JsonToken(word, value, index, index + len(word), line))
                    index += len(word)
                    matched_keyword = True
                    break
            if matched_keyword:
                continue
            raise PluginConfigError(f"Unexpected JSON token at line {line}, column {index + 1}")
        tokens.append(_JsonToken("eof", None, len(content), len(content), line))
        return tokens

    def _peek(self) -> _JsonToken:
        return self.tokens[self.index]

    def _take(self, kind: Optional[str] = None) -> _JsonToken:
        token = self._peek()
        if kind is not None and token.kind != kind:
            raise PluginConfigError(f"Expected {kind!r} at line {token.line}, found {token.kind!r}")
        self.index += 1
        return token

    @staticmethod
    def _pointer(path: list[Any]) -> str:
        encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
        return "/" + "/".join(encoded)

    @staticmethod
    def _group(path: list[Any]) -> str:
        return (
            " / ".join(str(part) if not isinstance(part, int) else f"[{part}]" for part in path)
            or "General"
        )

    def _value(self, path: list[Any], label: str) -> None:
        token = self._peek()
        if token.kind == "{":
            self._take("{")
            if self._peek().kind == "}":
                self._take("}")
                return
            while True:
                key = self._take("string")
                self._take(":")
                self._value([*path, key.value], str(key.value))
                if self._peek().kind == ",":
                    self._take(",")
                    if self._peek().kind == "}":
                        self._take("}")
                        return
                    continue
                self._take("}")
                return
        if token.kind == "[":
            self._take("[")
            array_index = 0
            if self._peek().kind == "]":
                self._take("]")
                return
            while True:
                self._value([*path, array_index], f"[{array_index}]")
                array_index += 1
                if self._peek().kind == ",":
                    self._take(",")
                    if self._peek().kind == "]":
                        self._take("]")
                        return
                    continue
                self._take("]")
                return
        token = self._take()
        if token.kind == "string":
            kind = "string"
        elif token.kind == "number":
            kind = "number" if isinstance(token.value, float) else "integer"
        elif token.kind in {"true", "false"}:
            kind = "boolean"
        elif token.kind == "null":
            return
        else:
            raise PluginConfigError(f"Expected a JSON value at line {token.line}")
        self.fields.append(
            ConfigField(
                field_id="json:" + self._pointer(path),
                key=label,
                group=self._group(path[:-1]),
                kind=kind,
                value=token.value,
                line=token.line,
                start=token.start,
                end=token.end,
                original=self.content[token.start : token.end],
                style="json",
            )
        )

    def parse(self) -> list[ConfigField]:
        self._value([], "$root")
        if self._peek().kind != "eof":
            token = self._peek()
            raise PluginConfigError(f"Unexpected content at line {token.line}")
        occurrences: dict[str, int] = {}
        for item in self.fields:
            occurrences[item.field_id] = occurrences.get(item.field_id, 0) + 1
            if occurrences[item.field_id] > 1:
                item.field_id = f"{item.field_id}#{occurrences[item.field_id]}"
        return self.fields


def _find_inline_comment(value: str, markers: Iterable[str]) -> tuple[str, str]:
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote:
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        else:
            for marker in markers:
                if value.startswith(marker, index) and (index == 0 or value[index - 1].isspace()):
                    return value[:index], value[index + len(marker) :].strip()
        index += 1
    return value, ""


def _decode_line_value(raw: str) -> tuple[str, Any, str]:
    stripped = raw.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        quote = stripped[0]
        inner = stripped[1:-1]
        inner = inner.replace("\\" + quote, quote).replace("\\\\", "\\")
        return "string", inner, "quoted:" + quote
    lowered = stripped.lower()
    if lowered in {"true", "false", "yes", "no", "on", "off"}:
        return "boolean", lowered in {"true", "yes", "on"}, "boolean:" + stripped
    if re.fullmatch(r"[+-]?[0-9]+", stripped):
        return "integer", int(stripped), "number"
    if re.fullmatch(
        r"[+-]?(?:(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[eE][+-]?[0-9]+)?|[0-9]+[eE][+-]?[0-9]+)",
        stripped,
    ):
        return "number", float(stripped), "number"
    return "string", stripped, "plain"


def _line_offsets(content: str) -> Iterable[tuple[int, int, int, str]]:
    offset = 0
    for line_number, line in enumerate(content.splitlines(keepends=True), 1):
        body = line.rstrip("\r\n")
        yield line_number, offset, offset + len(body), body
        offset += len(line)
    if not content:
        return
    if not content.endswith(("\n", "\r")):
        return


def _parse_cfg(content: str) -> list[ConfigField]:
    fields: list[ConfigField] = []
    pending_comments: list[str] = []
    command = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_.:-]*)(\s+)(.+?)\s*$")
    for line_number, line_start, _, body in _line_offsets(content):
        stripped = body.strip()
        if not stripped:
            pending_comments = []
            continue
        if stripped.startswith("//"):
            pending_comments.append(stripped[2:].strip())
            continue
        match = command.match(body)
        if not match:
            pending_comments = []
            continue
        value_with_comment = match.group(4)
        value_part, inline_comment = _find_inline_comment(value_with_comment, ("//",))
        value_part = value_part.rstrip()
        if not value_part:
            pending_comments = []
            continue
        value_start = line_start + match.start(4)
        value_end = value_start + len(value_part)
        kind, value, style = _decode_line_value(value_part)
        comment = "\n".join([*pending_comments, *([inline_comment] if inline_comment else [])])
        fields.append(
            ConfigField(
                field_id=f"cfg:{line_number}:{match.group(2)}",
                key=match.group(2),
                group="General",
                kind=kind,
                value=value,
                line=line_number,
                comment=comment,
                start=value_start,
                end=value_end,
                original=value_part,
                style=style,
            )
        )
        pending_comments = []
    return fields


def _parse_ini(content: str) -> list[ConfigField]:
    fields: list[ConfigField] = []
    section = "General"
    pending_comments: list[str] = []
    section_pattern = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[;#].*)?$")
    assignment = re.compile(r"^(\s*)([^=:#\s][^=:#]*?)(\s*)([=:])(\s*)(.*?)\s*$")
    for line_number, line_start, _, body in _line_offsets(content):
        stripped = body.strip()
        if not stripped:
            pending_comments = []
            continue
        if stripped.startswith((";", "#")):
            pending_comments.append(stripped[1:].strip())
            continue
        section_match = section_pattern.match(body)
        if section_match:
            section = section_match.group(1).strip() or "General"
            pending_comments = []
            continue
        match = assignment.match(body)
        if not match:
            pending_comments = []
            continue
        raw_with_comment = match.group(6)
        value_part, inline_comment = _find_inline_comment(raw_with_comment, (";", "#"))
        value_part = value_part.rstrip()
        value_start = line_start + match.start(6)
        value_end = value_start + len(value_part)
        kind, value, style = _decode_line_value(value_part)
        key = match.group(2).strip()
        comment = "\n".join([*pending_comments, *([inline_comment] if inline_comment else [])])
        fields.append(
            ConfigField(
                field_id=f"ini:{line_number}:{section}:{key}",
                key=key,
                group=section,
                kind=kind,
                value=value,
                line=line_number,
                comment=comment,
                start=value_start,
                end=value_end,
                original=value_part,
                style=style,
            )
        )
        pending_comments = []
    return fields


def parse_config(content: str, filename: str) -> ParsedConfig:
    config_format = format_for_filename(filename, content)
    if config_format in {"json", "jsonc"}:
        try:
            fields = _JsoncParser(content).parse()
            return ParsedConfig(config_format, fields, bool(fields))
        except PluginConfigError as exc:
            return ParsedConfig(config_format, [], False, str(exc))
    if config_format == "cfg":
        fields = _parse_cfg(content)
        error = None if fields else "No supported command/value lines were found"
        return ParsedConfig(config_format, fields, bool(fields), error)
    if config_format == "ini":
        fields = _parse_ini(content)
        error = None if fields else "No supported INI settings were found"
        return ParsedConfig(config_format, fields, bool(fields), error)
    return ParsedConfig("raw", [], False)


def _match_case(template: str, value: str) -> str:
    if template.isupper():
        return value.upper()
    if template[:1].isupper():
        return value.capitalize()
    return value


def _serialize_field(item: ConfigField, value: Any) -> str:
    if item.kind == "boolean":
        if not isinstance(value, bool):
            raise PluginConfigError(f"{item.key} must be true or false")
        if item.style == "json":
            return "true" if value else "false"
        template = item.style.partition(":")[2] or "true"
        lowered = template.lower()
        pairs = {
            "true": "false",
            "false": "true",
            "yes": "no",
            "no": "yes",
            "on": "off",
            "off": "on",
        }
        positive = lowered in {"true", "yes", "on"}
        selected = lowered if value == positive else pairs[lowered]
        return _match_case(template, selected)
    if item.kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise PluginConfigError(f"{item.key} must be an integer")
        return str(value)
    if item.kind == "number":
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise PluginConfigError(f"{item.key} must be a finite number")
        return json.dumps(value, allow_nan=False, separators=(",", ":"))
    if item.kind != "string" or not isinstance(value, str):
        raise PluginConfigError(f"{item.key} must be text")
    if item.style == "json":
        return json.dumps(value, ensure_ascii=False)
    if item.style.startswith("quoted:"):
        quote = item.style[-1]
        escaped = value.replace("\\", "\\\\").replace(quote, "\\" + quote)
        return quote + escaped + quote
    ini_comment = item.field_id.startswith("ini:") and any(mark in value for mark in (";", "#"))
    if not value or any(char.isspace() for char in value) or "//" in value or ini_comment:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def apply_visual_changes(
    content: str,
    filename: str,
    changes: list[dict[str, Any]],
) -> str:
    parsed = parse_config(content, filename)
    if not parsed.visual_supported:
        raise PluginConfigError(parsed.parse_error or "This file has no visual editor")
    by_id = {item.field_id: item for item in parsed.fields}
    replacements: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for change in changes:
        field_id = change.get("id")
        if not isinstance(field_id, str) or field_id in seen or field_id not in by_id:
            raise PluginConfigError("The visual edit contains an unknown or duplicate field")
        seen.add(field_id)
        item = by_id[field_id]
        replacements.append((item.start, item.end, _serialize_field(item, change.get("value"))))
    updated = content
    for start, end, replacement in sorted(replacements, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    if len(updated.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise PluginConfigError("Updated configuration exceeds the 10 MiB size limit")
    return updated


def validate_raw_content(content: str, filename: str) -> None:
    if not isinstance(content, str):
        raise PluginConfigError("Configuration content must be text")
    if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise PluginConfigError("Configuration exceeds the 10 MiB size limit")
    if format_for_filename(filename, content) in {"json", "jsonc"}:
        parsed = parse_config(content, filename)
        if parsed.parse_error:
            raise PluginConfigError(parsed.parse_error)


__all__ = [
    "MAX_CONFIG_BYTES",
    "SUPPORTED_DIRECTORY_EXTENSIONS",
    "PluginConfigError",
    "ConfigField",
    "ParsedConfig",
    "content_revision",
    "path_hash",
    "normalize_relative_path",
    "format_for_filename",
    "parse_config",
    "apply_visual_changes",
    "validate_raw_content",
]
