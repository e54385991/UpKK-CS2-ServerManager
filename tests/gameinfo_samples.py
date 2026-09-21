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

# SteamDatabase / Valve stock SearchPaths (three tabs, tab separator, blank line).
GAMEINFO_VALVE_SEARCHPATHS = (
    "SearchPaths\n"
    "\t\t{\n"
    "\t\t\tGame_LowViolence\tcsgo_lv // Perfect World content override\n"
    "\n"
    "\t\t\tGame\tcsgo\n"
    "\t\t\tGame\tcsgo_imported\n"
    "\t\t}\n"
)

GAMEINFO_VALVE_BOTH = (
    "SearchPaths\n"
    "\t\t{\n"
    "\t\t\tGame_LowViolence\tcsgo_lv // Perfect World content override\n"
    "\t\t\tGame\tcsgo/addons/metamod\n"
    "\t\t\tGame\tcsgo/addons/swiftlys2\n"
    "\n"
    "\t\t\tGame\tcsgo\n"
    "\t\t\tGame\tcsgo_imported\n"
    "\t\t}\n"
)

# Panel leftover: old sed / unindented Metamod plus a 3-tab Swiftly line.
GAMEINFO_MIXED_INDENT = (
    "\t\t{\n"
    "\t\t\tGame_LowViolence\tcsgo_lv\n"
    "Game\tcsgo/addons/metamod\n"
    "\t\t\tGame\tcsgo/addons/swiftlys2\n"
    "\t\t\tGame\tcsgo\n"
    "\t\t}\n"
)

GAMEINFO_MIXED_INDENT_FIXED = (
    "\t\t{\n"
    "\t\t\tGame_LowViolence\tcsgo_lv\n"
    "\t\t\tGame\tcsgo/addons/metamod\n"
    "\t\t\tGame\tcsgo/addons/swiftlys2\n"
    "\t\t\tGame\tcsgo\n"
    "\t\t}\n"
)

# Quoted / GameBin / bare addons paths that older writers have left behind.
GAMEINFO_LEFTOVER_VARIANTS = (
    "\t\t{\n"
    "\t\t\tGame_LowViolence\tcsgo_lv\n"
    '\t\t\tGame\t"csgo/addons/metamod"\n'
    "\t\t\tGameBin\taddons/swiftlys2\n"
    "\t\t\tGame\tcsgo\n"
    "\t\t}\n"
)
