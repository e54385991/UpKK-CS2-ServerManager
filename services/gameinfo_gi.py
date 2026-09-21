"""CS2 ``gameinfo.gi`` SearchPaths helpers.

Official SwiftlyS2 installation (https://swiftlys2.net/docs/installation/):

- SwiftlyS2 does not need MetaMod; it ships its own Loader under
  ``addons/swiftlys2``.
- After ``Game_LowViolence csgo_lv``, add ``Game csgo/addons/swiftlys2``.
- When MetaMod is also installed, MetaMod must be listed first:

    Game csgo/addons/metamod
    Game csgo/addons/swiftlys2

Inserted ``Game`` lines use the same indent and key/value separator as the
vanilla ``Game csgo`` sibling (Valve stock file uses three tabs plus a tab
separator). ``Game_LowViolence`` is only the insertion anchor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GAMEINFO_LOW_VIOLENCE_MARKER = "Game_LowViolence"
GAMEINFO_METAMOD_PATH = "csgo/addons/metamod"
GAMEINFO_SWIFTLY_PATH = "csgo/addons/swiftlys2"

_LOW_VIOLENCE_RE = re.compile(r"^[ \t]*Game_LowViolence\b")
_ADDON_GAME_RE = re.compile(
    r"^[ \t]*Game(?:Bin)?[ \t]+\"?(?:csgo/)?addons/"
    r"(?P<name>metamod|swiftlys2)/?\"?"
    r"(?:[ \t]*(?://.*)?)?[ \t]*$"
)
_VANILLA_GAME_CSGO_RE = re.compile(
    r"^(?P<indent>[ \t]*)Game(?P<sep>[ \t]+)csgo(?:[ \t]*(?://.*)?)?[ \t]*$"
)


@dataclass(frozen=True)
class GameinfoRewriteResult:
    content: str
    changed: bool
    missing_anchor: bool
    has_metamod: bool
    has_swiftly: bool


def gameinfo_has_search_path(content: str, path: str) -> bool:
    """Return True when ``Game <path>`` is already present."""
    return any(_addon_path(line) == path for line in content.splitlines())


def _addon_path(line: str) -> str | None:
    match = _ADDON_GAME_RE.match(line.rstrip("\r\n"))
    if not match:
        return None
    name = match.group("name")
    return GAMEINFO_METAMOD_PATH if name == "metamod" else GAMEINFO_SWIFTLY_PATH


def _leading_whitespace(line: str) -> str:
    stripped = line.lstrip(" \t")
    return line[: len(line) - len(stripped)]


def _line_ending(content: str) -> str:
    return "\r\n" if "\r\n" in content else "\n"


def _vanilla_game_csgo_style(lines: list[str]) -> tuple[str, str] | None:
    """Indent and separator from the first vanilla ``Game csgo`` sibling."""
    for line in lines:
        match = _VANILLA_GAME_CSGO_RE.match(line.rstrip("\r\n"))
        if match:
            return match.group("indent"), match.group("sep")
    return None


def rewrite_gameinfo_search_paths(
    content: str,
    *,
    include_metamod: bool = False,
    include_swiftly: bool = False,
) -> GameinfoRewriteResult:
    """Place Loader entries immediately after ``Game_LowViolence``.

    Existing Metamod / SwiftlyS2 ``Game`` lines are kept even when the matching
    ``include_*`` flag is false, then rewritten in official order so a later
    Metamod install cannot leave SwiftlyS2 above Metamod.
    """
    newline = _line_ending(content)
    raw_lines = content.splitlines(keepends=True)
    existing_metamod = False
    existing_swiftly = False
    kept: list[str] = []
    anchor_index: int | None = None

    for line in raw_lines:
        path = _addon_path(line)
        if path == GAMEINFO_METAMOD_PATH:
            existing_metamod = True
            continue
        if path == GAMEINFO_SWIFTLY_PATH:
            existing_swiftly = True
            continue
        if anchor_index is None and _LOW_VIOLENCE_RE.match(line):
            anchor_index = len(kept)
        kept.append(line)

    need_metamod = include_metamod or existing_metamod
    need_swiftly = include_swiftly or existing_swiftly
    if not need_metamod and not need_swiftly:
        return GameinfoRewriteResult(
            content=content,
            changed=False,
            missing_anchor=False,
            has_metamod=False,
            has_swiftly=False,
        )
    if anchor_index is None:
        return GameinfoRewriteResult(
            content=content,
            changed=False,
            missing_anchor=True,
            has_metamod=existing_metamod,
            has_swiftly=existing_swiftly,
        )

    style = _vanilla_game_csgo_style(kept)
    if style is not None:
        indent, separator = style
    else:
        indent = _leading_whitespace(kept[anchor_index])
        separator = "\t"
    insertions = []
    if need_metamod:
        insertions.append(f"{indent}Game{separator}{GAMEINFO_METAMOD_PATH}{newline}")
    if need_swiftly:
        insertions.append(f"{indent}Game{separator}{GAMEINFO_SWIFTLY_PATH}{newline}")
    new_content = "".join(kept[: anchor_index + 1] + insertions + kept[anchor_index + 1 :])
    return GameinfoRewriteResult(
        content=new_content,
        changed=new_content != content,
        missing_anchor=False,
        has_metamod=need_metamod,
        has_swiftly=need_swiftly,
    )
