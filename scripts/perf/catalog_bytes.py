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
    result: dict[str, Any] = {}
    for name in names:
        _assign_path(result, catalog, name.split("."), name)
    return result


def _assign_path(
    target: dict[str, Any],
    source: Mapping[str, Any],
    parts: list[str],
    namespace: str,
) -> None:
    if not parts:
        raise KeyError(f"Missing message namespace: {namespace}")
    head, *rest = parts
    if head not in source:
        raise KeyError(f"Missing message namespace: {namespace}")
    if not rest:
        target[head] = source[head]
        return
    value = source[head]
    if not isinstance(value, Mapping):
        raise TypeError(f"Message namespace is not nested: {namespace}")
    child = target.get(head)
    if not isinstance(child, dict):
        child = {}
        target[head] = child
    _assign_path(child, value, rest, namespace)


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
            "login": locales["en-US"]["login_subset"],
            "overview": locales["en-US"]["overview_subset"],
            "login_zh": locales["zh-CN"]["login_subset"],
            "overview_zh": locales["zh-CN"]["overview_subset"],
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload
