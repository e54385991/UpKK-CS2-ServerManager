"""Compact JSON byte counts for catalogs. Not the same as HTML/RSC transfer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts.perf.profiles import LOGIN_NAMESPACES, OVERVIEW_NAMESPACES

FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "messages"


def compact_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def load_catalog(locale: str, root: Path | None = None) -> dict[str, Any]:
    base = root or FRONTEND_ROOT
    payload = _read_json(base / f"{locale}.json")
    monitor = _read_json(base / "monitor" / f"{locale}.json")
    settings = payload.get("settings")
    merged_settings = dict(settings) if isinstance(settings, dict) else {}
    merged_settings["monitor"] = monitor
    payload["settings"] = merged_settings
    return payload


def pick_namespaces(catalog: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: catalog[name] for name in names if name in catalog}


def catalog_byte_report(root: Path | None = None) -> dict[str, Any]:
    locales = {}
    for locale in ("en-US", "zh-CN"):
        catalog = load_catalog(locale, root)
        namespaces = {
            name: compact_bytes(value)
            for name, value in catalog.items()
            if isinstance(value, (dict, str, list))
        }
        locales[locale] = {
            "full": compact_bytes(catalog),
            "login_subset": compact_bytes(pick_namespaces(catalog, LOGIN_NAMESPACES)),
            "overview_subset": compact_bytes(pick_namespaces(catalog, OVERVIEW_NAMESPACES)),
            "namespaces": namespaces,
        }
    return {
        "note": (
            "Compact UTF-8 JSON of the server catalog. This is not HTML, RSC, "
            "or gzip transfer size; those belong in the browser section."
        ),
        "locales": locales,
        "client_estimate": {
            "login": locales["en-US"]["full"],
            "overview": locales["en-US"]["full"],
            "login_zh": locales["zh-CN"]["full"],
            "overview_zh": locales["zh-CN"]["full"],
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload
