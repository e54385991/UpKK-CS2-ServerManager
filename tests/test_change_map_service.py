"""Resolve live map changes from a MapChooser pool by name or Workshop ID."""

from __future__ import annotations

import pytest

from modules.schemas.discord import DiscordCapability
from services.change_map_service import (
    ChangeMapAmbiguousError,
    ChangeMapError,
    MapCandidate,
    match_map_query,
    resolve_change_map,
    resolve_unique_map,
    workshop_id_fallback,
)
from services.discord_menu_ui import action_capability

POOL = [
    {
        "name": "ze_saw_p",
        "workshop_id": "3171881962",
        "enabled": True,
        "filename": "ze_saw_p",
        "updated_name": "Saw",
    },
    {
        "name": "ze_ffvii_mako_reactor_v5_3",
        "workshop_id": "3070591565",
        "enabled": True,
        "filename": "ze_ffvii_mako_reactor_v5_3",
        "updated_name": "",
    },
    {
        "name": "de_dust2",
        "workshop_id": "",
        "enabled": True,
        "filename": "de_dust2",
        "updated_name": "",
    },
]


def test_discord_menu_exposes_change_map_capability():
    assert action_capability("change_map") is DiscordCapability.CHANGE_MAP


def test_workshop_maps_use_host_workshop_map_id():
    candidate = MapCandidate(name="ze_saw_p", workshop_id="3171881962")
    assert candidate.command == "host_workshop_map 3171881962"
    assert candidate.is_workshop is True
    assert candidate.identity_key == "ws:3171881962"


def test_official_maps_use_map_command():
    candidate = MapCandidate(name="Dust II", workshop_id="", filename="de_dust2")
    assert candidate.command == "map de_dust2"


def test_match_accepts_partial_name_and_workshop_id():
    assert [item.name for item in match_map_query(POOL, "saw")] == ["ze_saw_p"]
    assert [item.workshop_id for item in match_map_query(POOL, "3171881962")] == ["3171881962"]
    assert [item.name for item in match_map_query(POOL, "dust")] == ["de_dust2"]


def test_workshop_url_and_unknown_id_fall_back_to_host_workshop_map():
    unique, matches = resolve_change_map(
        POOL, "https://steamcommunity.com/sharedfiles/filedetails/?id=3171881962"
    )
    assert unique is not None
    assert unique.workshop_id == "3171881962"
    fallback = workshop_id_fallback("3298427415")
    assert fallback is not None
    assert fallback.command == "host_workshop_map 3298427415"
    unique, matches = resolve_change_map(POOL, "3298427415")
    assert unique is not None
    assert unique.command == "host_workshop_map 3298427415"
    assert matches == [unique]


def test_ambiguous_partial_name_requires_a_more_specific_query():
    matches = match_map_query(POOL, "ze_")
    assert {item.name for item in matches} == {"ze_saw_p", "ze_ffvii_mako_reactor_v5_3"}
    with pytest.raises(ChangeMapAmbiguousError):
        resolve_unique_map(POOL, "ze_")
    with pytest.raises(ChangeMapError, match="No map matched"):
        resolve_unique_map(POOL, "missing_map")
