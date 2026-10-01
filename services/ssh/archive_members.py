"""Focused helpers; mutable state remains owned by the original controller."""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from services.compat import LateBoundModule

if TYPE_CHECKING:
    pass

host = LateBoundModule("services.ssh.file_archive")


def _normalize_archive_member(
    cls, member_name: str, *, allow_backslash_separators: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """Normalize one archive member, returning (path, error)."""
    if not isinstance(member_name, str):
        return None, "Archive contains a non-text member name"
    if any(ord(char) < 32 or ord(char) == 127 for char in member_name):
        return None, "Archive contains a member name with control characters"
    original_member_name = member_name
    if "\\" in member_name:
        if not allow_backslash_separators:
            return None, "Archive contains a member name with backslash separators"
        member_name = member_name.replace("\\", "/")
    if len(member_name.encode("utf-8")) > cls.ARCHIVE_MAX_MEMBER_PATH_BYTES:
        return None, "Archive contains an excessively long member path"

    # GNU tar emits the two POSIX root markers; Windows-created TARs can
    # also contain the exact equivalent '.\\'. Do not infer a root marker
    # after rewriting separators or trimming slashes: doing so would
    # silently accept absolute '/', '//' or Windows '\\' members.
    if original_member_name in (".", "./") or (
        allow_backslash_separators and original_member_name == ".\\"
    ):
        return None, None
    if member_name.startswith("/") or re.match(r"^[A-Za-z]:", member_name):
        return None, f"Archive member uses an absolute path: {original_member_name!r}"

    value = member_name.rstrip("/")
    while value.startswith("./"):
        value = value[2:]
    if value in ("", "."):
        return None, f"Archive member contains an unsafe empty path: {original_member_name!r}"
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return None, f"Archive member uses an absolute path: {original_member_name!r}"

    components = value.split("/")
    if any(component in ("", ".", "..") for component in components):
        return None, f"Archive member contains an unsafe path component: {member_name!r}"
    normalized = posixpath.normpath(value)
    if normalized == ".." or normalized.startswith("../"):
        return None, f"Archive member escapes the archive root: {member_name!r}"
    return normalized, None


def _decode_tar_listing_name(value: str) -> Tuple[Optional[str], Optional[str]]:
    """Decode the escaped body of one GNU tar C-quoted member name."""
    decoded = bytearray()
    index = 0
    escapes = {
        "\\": ord("\\"),
        '"': ord('"'),
        "a": 7,
        "b": 8,
        "f": 12,
        "n": 10,
        "r": 13,
        "t": 9,
        "v": 11,
    }
    while index < len(value):
        char = value[index]
        if char != "\\":
            decoded.extend(char.encode("utf-8"))
            index += 1
            continue

        index += 1
        if index >= len(value):
            return None, "TAR returned a malformed escaped member name"
        escaped = value[index]
        if escaped in escapes:
            decoded.append(escapes[escaped])
            index += 1
            continue
        if escaped in "01234567":
            end = index + 1
            while end < min(index + 3, len(value)) and value[end] in "01234567":
                end += 1
            decoded_byte = int(value[index:end], 8)
            if decoded_byte > 255:
                return None, "TAR returned an invalid octal escape in a member name"
            decoded.append(decoded_byte)
            index = end
            continue
        return None, "TAR returned an unsupported escaped member name"
    try:
        return decoded.decode("utf-8", errors="strict"), None
    except UnicodeDecodeError:
        return None, "Archive contains a member name which is not valid UTF-8"


def _parse_tar_c_verbose_listing_line(
    cls, line: str
) -> Tuple[Optional[Tuple[str, bool]], Optional[str]]:
    """Parse GNU tar's stable numeric, UTC, C-quoted verbose format."""
    fields = line.split(None, 5)
    if len(fields) != 6 or not fields[0]:
        return None, "TAR returned a malformed member listing"

    member_type = fields[0][0]
    if member_type not in ("-", "d"):
        # Reject links before parsing their ``name -> target`` suffix.
        return None, "Archive contains a link or special TAR member"

    quoted_name = fields[5]
    if len(quoted_name) < 2 or not quoted_name.startswith('"') or not quoted_name.endswith('"'):
        return None, "TAR returned an unquoted or malformed member name"
    decoded_name, decode_error = cls._decode_tar_listing_name(quoted_name[1:-1])
    if decode_error:
        return None, decode_error
    if decoded_name is None:
        return None, "TAR returned a malformed member name"
    return (decoded_name, member_type == "d"), None


def _build_archive_info(
    cls, archive_type: str, raw_members: List[Tuple[str, bool]]
) -> Tuple[bool, Dict[str, Any], str]:
    if len(raw_members) > cls.ARCHIVE_MAX_ENTRIES:
        return (
            False,
            {},
            (f"Archive contains too many entries (maximum {cls.ARCHIVE_MAX_ENTRIES})"),
        )

    normalize_backslashes = archive_type in host.ConnectionMixin.ARCHIVE_TYPES_ALLOW_BACKSLASH
    has_backslash_separators = False
    member_types: Dict[str, bool] = {}
    members: List[Dict[str, Any]] = []
    for raw_name, is_directory in raw_members:
        if "\\" in raw_name:
            has_backslash_separators = True
        normalized, error = cls._normalize_archive_member(
            raw_name,
            allow_backslash_separators=normalize_backslashes,
        )
        if error:
            return False, {}, error
        if normalized is None:
            continue
        if normalized in member_types:
            return False, {}, f"Archive contains a duplicate member path: {normalized}"
        member_types[normalized] = is_directory
        members.append({"path": normalized, "is_dir": is_directory})

    file_paths = {path for path, is_directory in member_types.items() if not is_directory}
    for path in member_types:
        parts = path.split("/")
        for index in range(1, len(parts)):
            ancestor = "/".join(parts[:index])
            if ancestor in file_paths:
                return False, {}, (f"Archive member path conflicts with file ancestor: {path}")

    folders = set()
    for member in members:
        parts = member["path"].split("/")
        folder_depth = len(parts) if member["is_dir"] else len(parts) - 1
        for index in range(1, folder_depth + 1):
            folders.add("/".join(parts[:index]))
            if len(folders) > cls.ARCHIVE_MAX_FOLDERS:
                return (
                    False,
                    {},
                    (f"Archive contains too many folders (maximum {cls.ARCHIVE_MAX_FOLDERS})"),
                )

    return (
        True,
        {
            "archive_type": archive_type,
            "folders": sorted(folders, key=lambda value: (value.count("/"), value.lower())),
            "entry_count": len(members),
            "members": members,
            "has_backslash_separators": has_backslash_separators,
        },
        "",
    )


def _parse_7z_listing(output: str) -> Tuple[Optional[List[Tuple[str, bool]]], Optional[str]]:
    """Parse technical 7-Zip listing output after its entry separator."""
    normalized_output = output.replace("\r\n", "\n").replace("\r", "\n")
    separator_match = re.search(r"^-{10,}\s*$", normalized_output, flags=re.MULTILINE)
    if separator_match:
        normalized_output = normalized_output[separator_match.end() :]

    raw_members: List[Tuple[str, bool]] = []
    for block in re.split(r"\n\s*\n", normalized_output.strip()):
        if not block.strip():
            continue
        properties: Dict[str, str] = {}
        malformed = False
        for line in block.splitlines():
            if " = " not in line:
                malformed = True
                continue
            key, value = line.split(" = ", 1)
            properties[key] = value
        path = properties.get("Path")
        if not path:
            # Archive metadata and banner blocks are not members.
            continue
        if malformed:
            return None, "7-Zip returned a malformed technical listing"

        attributes = properties.get("Attributes", "")
        lower_attributes = attributes.lower()
        if (
            properties.get("Symbolic Link")
            or properties.get("Hard Link")
            or properties.get("Anti") == "+"
            or re.search(r"(^|\s)l[rwx-]{9}", lower_attributes)
            or re.search(r"(^|\s)[bcps][rwx-]{9}", lower_attributes)
        ):
            return None, f"Archive contains an unsupported link or special entry: {path}"
        is_directory = properties.get("Folder") == "+" or attributes.startswith("D")
        raw_members.append((path, is_directory))
    return raw_members, None
