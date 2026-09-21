"""Canonical ``gameinfo.gi`` snippets used by SearchPaths tests."""

GAMEINFO_BARE = "\tSearchPaths\n\t{\n\t\tGame_LowViolence\tcsgo_lv\n\t\tGame\tcsgo\n\t}\n"

GAMEINFO_METAMOD = (
    "\tSearchPaths\n"
    "\t{\n"
    "\t\tGame_LowViolence\tcsgo_lv\n"
    "\t\tGame\tcsgo/addons/metamod\n"
    "\t\tGame\tcsgo\n"
    "\t}\n"
)

GAMEINFO_SWIFTLY = (
    "\tSearchPaths\n"
    "\t{\n"
    "\t\tGame_LowViolence\tcsgo_lv\n"
    "\t\tGame\tcsgo/addons/swiftlys2\n"
    "\t\tGame\tcsgo\n"
    "\t}\n"
)

GAMEINFO_BOTH = (
    "\tSearchPaths\n"
    "\t{\n"
    "\t\tGame_LowViolence\tcsgo_lv\n"
    "\t\tGame\tcsgo/addons/metamod\n"
    "\t\tGame\tcsgo/addons/swiftlys2\n"
    "\t\tGame\tcsgo\n"
    "\t}\n"
)

GAMEINFO_SWIFTLY_THEN_METAMOD = (
    "\tSearchPaths\n"
    "\t{\n"
    "\t\tGame_LowViolence\tcsgo_lv\n"
    "\t\tGame\tcsgo/addons/swiftlys2\n"
    "\t\tGame\tcsgo/addons/metamod\n"
    "\t\tGame\tcsgo\n"
    "\t}\n"
)
