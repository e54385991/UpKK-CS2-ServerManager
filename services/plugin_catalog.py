"""Portable plugin-market catalog import/export.

Catalogs are keyed by GitHub repository URL so they can move between panels
without leaking local numeric IDs. Dependency lists and conflict pairs use the
same URLs. Local storage still keeps comma-separated IDs and ID-pair rules.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models.plugins import MarketPlugin, PluginCategory, PluginConflictRule
from modules.schemas.plugins import (
    PluginCatalogConflict,
    PluginCatalogEntry,
    PluginCatalogExport,
    PluginCatalogImportRequest,
    PluginCatalogImportResponse,
    PluginCatalogImportResult,
)
from services.plugins.common import PluginPlanError, parse_dependency_ids

DEFAULT_PLUGIN_CATALOG_PATH = Path(__file__).resolve().parent / "defaults" / "plugin-catalog.json"

_GITHUB_REPO = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/.*)?$",
    re.IGNORECASE,
)


def catalog_github_url(value: str) -> str:
    """Normalize a market GitHub URL to ``https://github.com/owner/repo``.

    Stored rows sometimes include a ``/releases`` suffix or a trailing slash.
    The catalog always keys plugins by the repository itself.
    """
    match = _GITHUB_REPO.fullmatch((value or "").strip().rstrip("/"))
    if not match:
        raise ValueError("github_url must be a GitHub repository URL")
    owner = match.group(1)
    repo = match.group(2).removesuffix(".git")
    if not repo:
        raise ValueError("github_url must be a GitHub repository URL")
    return f"https://github.com/{owner}/{repo}"


def catalog_lookup_key(value: str) -> str:
    return catalog_github_url(value).casefold()


def _category_value(category: PluginCategory | str) -> str:
    return category.value if isinstance(category, PluginCategory) else str(category)


def _parse_category(value: str) -> PluginCategory | None:
    try:
        return PluginCategory(value)
    except ValueError:
        try:
            return PluginCategory[value.upper()]
        except KeyError, ValueError:
            return None


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _conflict_name(url_a: str, url_b: str) -> str:
    left, right = sorted((url_a, url_b), key=str.casefold)
    return f"{left} × {right}"


def _index_plugins(plugins: list[MarketPlugin]) -> dict[str, MarketPlugin]:
    by_key: dict[str, MarketPlugin] = {}
    for plugin in plugins:
        try:
            by_key[catalog_lookup_key(plugin.github_url)] = plugin
        except ValueError:
            continue
    return by_key


def plugin_to_catalog_entry(
    plugin: MarketPlugin,
    by_id: dict[int, MarketPlugin],
) -> PluginCatalogEntry | None:
    try:
        github_url = catalog_github_url(plugin.github_url)
    except ValueError:
        return None
    dependencies: list[str] = []
    try:
        dep_ids = parse_dependency_ids(plugin.dependencies)
    except ValueError, PluginPlanError:
        dep_ids = []
    for dep_id in dep_ids:
        dep = by_id.get(dep_id)
        if dep is None:
            continue
        try:
            dependencies.append(catalog_github_url(dep.github_url))
        except ValueError:
            continue
    return PluginCatalogEntry(
        github_url=github_url,
        title=plugin.title,
        description=plugin.description,
        author=plugin.author,
        version=plugin.version,
        category=_category_value(plugin.category),
        tags=plugin.tags,
        is_recommended=bool(plugin.is_recommended),
        icon_url=plugin.icon_url,
        custom_install_path=plugin.custom_install_path,
        dependencies=dependencies,
    )


def conflict_to_catalog_item(
    rule: PluginConflictRule,
    by_id: dict[int, MarketPlugin],
) -> PluginCatalogConflict | None:
    plugin_a = by_id.get(rule.plugin_a_id)
    plugin_b = by_id.get(rule.plugin_b_id)
    if plugin_a is None or plugin_b is None:
        return None
    try:
        url_a = catalog_github_url(plugin_a.github_url)
        url_b = catalog_github_url(plugin_b.github_url)
    except ValueError:
        return None
    if catalog_lookup_key(url_a) == catalog_lookup_key(url_b):
        return None
    left, right = sorted((url_a, url_b), key=str.casefold)
    severity = rule.severity if rule.severity in {"hard", "warning"} else "hard"
    return PluginCatalogConflict(
        plugin_a_url=left,
        plugin_b_url=right,
        severity=severity,
        reason=rule.reason,
        is_enabled=bool(rule.is_enabled),
    )


async def _load_market_plugins(db: AsyncSession) -> list[MarketPlugin]:
    result = await db.execute(select(MarketPlugin).order_by(MarketPlugin.id))
    return list(result.scalars().all())


async def _load_conflict_rules(db: AsyncSession) -> list[PluginConflictRule]:
    result = await db.execute(select(PluginConflictRule).order_by(PluginConflictRule.id))
    return list(result.scalars().all())


def load_default_plugin_catalog() -> PluginCatalogImportRequest:
    """Load the shipped marketplace catalog used for empty-database seeding."""
    payload = json.loads(DEFAULT_PLUGIN_CATALOG_PATH.read_text(encoding="utf-8"))
    return PluginCatalogImportRequest.model_validate(payload)


async def ensure_default_plugin_catalog(
    db: AsyncSession,
) -> PluginCatalogImportResponse | None:
    """Import the shipped catalog when the marketplace has no plugins."""
    existing = await _load_market_plugins(db)
    if existing:
        return None
    return await import_plugin_catalog(db, load_default_plugin_catalog())


async def collect_export_bundle(db: AsyncSession) -> PluginCatalogExport:
    """Return the full marketplace as a portable catalog."""
    plugins = await _load_market_plugins(db)
    by_id = {int(plugin.id): plugin for plugin in plugins if plugin.id is not None}
    entries = [
        entry for plugin in plugins if (entry := plugin_to_catalog_entry(plugin, by_id)) is not None
    ]
    conflicts: list[PluginCatalogConflict] = []
    seen: set[tuple[str, str]] = set()
    for rule in await _load_conflict_rules(db):
        item = conflict_to_catalog_item(rule, by_id)
        if item is None:
            continue
        pair = (catalog_lookup_key(item.plugin_a_url), catalog_lookup_key(item.plugin_b_url))
        if pair in seen:
            continue
        seen.add(pair)
        conflicts.append(item)
    return PluginCatalogExport(
        format="upkk-cs2-plugin-catalog",
        version=1,
        exported_at=datetime.now(timezone.utc),
        plugins=entries,
        conflicts=conflicts,
    )


def _apply_entry_fields(
    plugin: MarketPlugin, entry: PluginCatalogEntry, category: PluginCategory
) -> None:
    plugin.title = entry.title
    plugin.description = entry.description
    plugin.author = entry.author
    plugin.version = entry.version
    plugin.category = category
    plugin.tags = entry.tags
    plugin.is_recommended = entry.is_recommended
    plugin.icon_url = _optional_text(entry.icon_url)
    plugin.custom_install_path = _optional_text(entry.custom_install_path)


def _apply_dependencies(
    plugin: MarketPlugin,
    entry: PluginCatalogEntry,
    by_key: dict[str, MarketPlugin],
) -> list[str]:
    dep_ids: list[int] = []
    missing: list[str] = []
    for raw in entry.dependencies:
        try:
            key = catalog_lookup_key(raw)
            url = catalog_github_url(raw)
        except ValueError:
            missing.append(raw)
            continue
        dep = by_key.get(key)
        if dep is None or dep.id is None:
            missing.append(url)
            continue
        if plugin.id is not None and dep.id == plugin.id:
            continue
        if dep.id not in dep_ids:
            dep_ids.append(int(dep.id))
    plugin.dependencies = ",".join(str(item) for item in dep_ids) if dep_ids else None
    return missing


def _summarize(results: list[PluginCatalogImportResult]) -> PluginCatalogImportResponse:
    return PluginCatalogImportResponse(
        total=len(results),
        imported=sum(1 for item in results if item.action == "imported"),
        updated=sum(1 for item in results if item.action == "updated"),
        skipped=sum(1 for item in results if item.action == "skipped"),
        failed=sum(1 for item in results if item.action == "failed"),
        results=results,
    )


async def import_plugin_catalog(
    db: AsyncSession,
    request: PluginCatalogImportRequest,
) -> PluginCatalogImportResponse:
    """Import a catalog. ``skip`` keeps existing rows; ``update`` overwrites them."""
    results: list[PluginCatalogImportResult] = []
    existing = await _load_market_plugins(db)
    by_key = _index_plugins(existing)
    pending_deps: list[tuple[MarketPlugin, PluginCatalogEntry, PluginCatalogImportResult]] = []
    seen_keys: set[str] = set()
    index = 0

    for entry in request.plugins:
        index += 1
        try:
            url = catalog_github_url(entry.github_url)
            key = catalog_lookup_key(entry.github_url)
        except ValueError as exc:
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="plugin",
                    name=entry.title,
                    action="failed",
                    message=str(exc),
                )
            )
            continue
        if key in seen_keys:
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="plugin",
                    name=entry.title,
                    action="failed",
                    message=f"Duplicate github_url in catalog: {url}",
                )
            )
            continue
        seen_keys.add(key)
        category = _parse_category(entry.category)
        if category is None:
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="plugin",
                    name=entry.title,
                    action="failed",
                    message=f"Invalid category: {entry.category}",
                )
            )
            continue
        current = by_key.get(key)
        if current is None:
            plugin = MarketPlugin(
                github_url=url,
                title=entry.title,
                description=entry.description,
                author=entry.author,
                version=entry.version,
                category=category,
                tags=entry.tags,
                is_recommended=entry.is_recommended,
                icon_url=_optional_text(entry.icon_url),
                custom_install_path=_optional_text(entry.custom_install_path),
                dependencies=None,
            )
            db.add(plugin)
            by_key[key] = plugin
            result = PluginCatalogImportResult(
                index=index,
                kind="plugin",
                name=entry.title,
                action="imported",
            )
            results.append(result)
            pending_deps.append((plugin, entry, result))
            continue
        if request.conflict_strategy == "skip":
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="plugin",
                    name=entry.title,
                    action="skipped",
                    plugin_id=current.id,
                )
            )
            continue
        _apply_entry_fields(current, entry, category)
        db.add(current)
        result = PluginCatalogImportResult(
            index=index,
            kind="plugin",
            name=entry.title,
            action="updated",
            plugin_id=current.id,
        )
        results.append(result)
        pending_deps.append((current, entry, result))

    await db.flush()

    for plugin, entry, result in pending_deps:
        if result.plugin_id is None:
            result.plugin_id = plugin.id
        missing = _apply_dependencies(plugin, entry, by_key)
        db.add(plugin)
        if missing:
            result.message = "Unresolved dependencies: " + ", ".join(missing)

    existing_rules = await _load_conflict_rules(db)
    rules_by_pair: dict[tuple[int, int], PluginConflictRule] = {}
    for rule in existing_rules:
        rules_by_pair[(int(rule.plugin_a_id), int(rule.plugin_b_id))] = rule
    seen_pairs: set[tuple[str, str]] = set()

    for conflict in request.conflicts:
        index += 1
        try:
            url_a = catalog_github_url(conflict.plugin_a_url)
            url_b = catalog_github_url(conflict.plugin_b_url)
            key_a = catalog_lookup_key(conflict.plugin_a_url)
            key_b = catalog_lookup_key(conflict.plugin_b_url)
        except ValueError as exc:
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="conflict",
                    name=_conflict_name(conflict.plugin_a_url, conflict.plugin_b_url),
                    action="failed",
                    message=str(exc),
                )
            )
            continue
        name = _conflict_name(url_a, url_b)
        if key_a == key_b:
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="conflict",
                    name=name,
                    action="failed",
                    message="A plugin cannot conflict with itself",
                )
            )
            continue
        pair_key = tuple(sorted((key_a, key_b)))
        if pair_key in seen_pairs:
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="conflict",
                    name=name,
                    action="failed",
                    message="Duplicate conflict pair in catalog",
                )
            )
            continue
        seen_pairs.add(pair_key)
        plugin_a = by_key.get(key_a)
        plugin_b = by_key.get(key_b)
        if plugin_a is None or plugin_a.id is None or plugin_b is None or plugin_b.id is None:
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="conflict",
                    name=name,
                    action="failed",
                    message="Both plugins must exist before a conflict rule can be imported",
                )
            )
            continue
        a_id, b_id = sorted((int(plugin_a.id), int(plugin_b.id)))
        current_rule = rules_by_pair.get((a_id, b_id))
        if current_rule is None:
            rule = PluginConflictRule(
                plugin_a_id=a_id,
                plugin_b_id=b_id,
                severity=conflict.severity,
                reason=_optional_text(conflict.reason),
                is_enabled=conflict.is_enabled,
            )
            db.add(rule)
            rules_by_pair[(a_id, b_id)] = rule
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="conflict",
                    name=name,
                    action="imported",
                )
            )
            continue
        if request.conflict_strategy == "skip":
            results.append(
                PluginCatalogImportResult(
                    index=index,
                    kind="conflict",
                    name=name,
                    action="skipped",
                )
            )
            continue
        current_rule.severity = conflict.severity
        current_rule.reason = _optional_text(conflict.reason)
        current_rule.is_enabled = conflict.is_enabled
        db.add(current_rule)
        results.append(
            PluginCatalogImportResult(
                index=index,
                kind="conflict",
                name=name,
                action="updated",
            )
        )

    await db.commit()
    return _summarize(results)


async def delete_market_plugin(db: AsyncSession, plugin_id: int) -> MarketPlugin | None:
    """Remove a catalog listing and drop it from other plugins' dependency lists.

    Conflict rows cascade in the database. Managed-plugin tracking is set
    null. Files already on game servers stay until they are uninstalled.
    """
    plugins = await _load_market_plugins(db)
    target = next((item for item in plugins if item.id == plugin_id), None)
    if target is None:
        return None
    for other in plugins:
        if other.id == plugin_id or not other.dependencies:
            continue
        try:
            dep_ids = parse_dependency_ids(other.dependencies)
        except PluginPlanError:
            continue
        if plugin_id not in dep_ids:
            continue
        remaining = [dep_id for dep_id in dep_ids if dep_id != plugin_id]
        other.dependencies = ",".join(str(dep_id) for dep_id in remaining) or None
        db.add(other)
    await db.delete(target)
    await db.commit()
    return target
