"""Resolve a live CS2 map change from the MapChooser pool by name or Workshop ID."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from modules import Server
from services.map_management_service import MapConfigError, normalize_workshop_id
from services.workshop_map_service import WorkshopPlanError, read_map_pool


class ChangeMapError(ValueError):
    """Raised when a live map change cannot be resolved uniquely."""


class ChangeMapAmbiguousError(ChangeMapError):
    def __init__(self, matches: Sequence["MapCandidate"]) -> None:
        self.matches = list(matches)
        preview = ", ".join(item.display_label() for item in self.matches[:8])
        extra = "" if len(self.matches) <= 8 else f" and {len(self.matches) - 8} more"
        super().__init__(f"Multiple maps matched: {preview}{extra}")


@dataclass(frozen=True, slots=True)
class MapCandidate:
    name: str
    workshop_id: str
    enabled: bool = True
    filename: str = ""
    updated_name: str = ""

    @property
    def is_workshop(self) -> bool:
        return bool(self.workshop_id) and self.workshop_id != "0"

    @property
    def command(self) -> str:
        if self.is_workshop:
            return f"host_workshop_map {self.workshop_id}"
        map_name = (self.filename or self.name).strip()
        return f"map {map_name}"

    @property
    def identity_key(self) -> str:
        if self.is_workshop:
            return f"ws:{self.workshop_id}"
        return f"name:{self.name}"

    def display_label(self) -> str:
        if self.is_workshop:
            return f"{self.name} ({self.workshop_id})"
        return f"{self.name} (official)"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "workshop_id": self.workshop_id or None,
            "enabled": self.enabled,
            "filename": self.filename or None,
            "command": self.command,
            "identity": self.identity_key,
        }


def candidate_from_map(item: Mapping[str, Any]) -> MapCandidate:
    workshop_id = str(item.get("workshop_id") or "").strip()
    if workshop_id == "0":
        workshop_id = ""
    return MapCandidate(
        name=str(item.get("name") or "").strip(),
        workshop_id=workshop_id,
        enabled=bool(item.get("enabled", True)),
        filename=str(item.get("filename") or "").strip(),
        updated_name=str(item.get("updated_name") or "").strip(),
    )


def workshop_id_fallback(query: str) -> MapCandidate | None:
    try:
        workshop_id = normalize_workshop_id(query)
    except MapConfigError:
        return None
    return MapCandidate(name=f"workshop:{workshop_id}", workshop_id=workshop_id)


def match_map_query(maps: Sequence[Mapping[str, Any]], query: str) -> list[MapCandidate]:
    """Match a name fragment or Workshop ID against the MapChooser pool."""
    raw = (query or "").strip()
    if not raw:
        return []
    candidates = [candidate_from_map(item) for item in maps if str(item.get("name") or "").strip()]
    workshop_id = None
    try:
        workshop_id = normalize_workshop_id(raw)
    except MapConfigError:
        pass
    if workshop_id:
        exact = [item for item in candidates if item.workshop_id == workshop_id]
        if exact:
            return exact

    needle = raw.casefold()
    scored: list[tuple[int, MapCandidate]] = []
    for item in candidates:
        name = item.name.casefold()
        filename = item.filename.casefold()
        updated = item.updated_name.casefold()
        if needle in {name, filename, updated}:
            score = 1
        elif name.startswith(needle) or filename.startswith(needle) or updated.startswith(needle):
            score = 2
        elif (
            needle in name
            or needle in filename
            or needle in updated
            or (needle.isdigit() and needle in item.workshop_id)
        ):
            score = 3
        else:
            continue
        scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], pair[1].name.casefold(), pair[1].workshop_id))
    return [item for _score, item in scored]


def resolve_unique_map(maps: Sequence[Mapping[str, Any]], query: str) -> MapCandidate:
    matches = match_map_query(maps, query)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ChangeMapAmbiguousError(matches)
    fallback = workshop_id_fallback(query)
    if fallback is not None:
        return fallback
    raise ChangeMapError(f"No map matched {query!r}")


def resolve_change_map(
    maps: Sequence[Mapping[str, Any]], query: str
) -> tuple[MapCandidate | None, list[MapCandidate]]:
    matches = match_map_query(maps, query)
    if len(matches) == 1:
        return matches[0], matches
    if matches:
        return None, matches
    fallback = workshop_id_fallback(query)
    if fallback is not None:
        return fallback, [fallback]
    return None, []


async def load_map_pool(server: Server) -> list[dict[str, object]]:
    try:
        return await read_map_pool(server)
    except WorkshopPlanError as exc:
        raise ChangeMapError(str(exc)) from exc


async def load_map_matches(server: Server, query: str) -> list[MapCandidate]:
    _unique, matches = resolve_change_map(await load_map_pool(server), query)
    return matches
