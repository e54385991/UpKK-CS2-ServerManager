"""Build crash-isolation plans and inspect installed plugin candidates."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shlex
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import (
    ManagedPlugin,
    PluginDiagnosticRun,
    Server,
    User,
)
from modules.utils import get_current_time
from services.compat import LateBoundModule
from services.plugins.common import parse_dependency_ids

DiagnosticScope = Literal["metamod", "counterstrikesharp", "both"]
host = LateBoundModule("services.plugin_diagnostic_service")


async def has_diagnostic_blocker(server_id: int, db: AsyncSession | None = None) -> bool:
    """Compatibility facade for the shared diagnostic coordination policy."""
    return await host._has_diagnostic_blocker(server_id, db)


def _plan_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _health_policy(server: Server) -> dict[str, Any]:
    return {
        "process_survival_seconds": host.HEALTH_OBSERVE_SECONDS,
        "a2s_required": bool(server.enable_a2s_monitoring),
        "a2s_successes": 2,
        "fatal_log_delta_forbidden": True,
        "max_start_attempts": host.MAX_START_ATTEMPTS,
        "max_duration_seconds": host.MAX_DURATION_SECONDS,
    }


async def _inventory(server: Server) -> list[dict[str, str]]:
    manager = host.SSHManager()
    connected, message = await manager.connect(server)
    if not connected:
        raise RuntimeError(f"SSH connection failed: {message}")
    game_root = server.game_directory.rstrip("/")
    csgo = posixpath.join(game_root, "cs2/game/csgo")
    mm_root = posixpath.join(csgo, "addons/metamod")
    css_root = posixpath.join(csgo, "addons/counterstrikesharp/plugins")
    command = (
        f"if test -d {shlex.quote(mm_root)}; then "
        f"find {shlex.quote(mm_root)} -xdev -maxdepth 1 -type f -name '*.vdf' "
        "! -name 'counterstrikesharp.vdf' -printf 'metamod\\t%p\\t%T@:%s\\n'; fi; "
        f"if test -d {shlex.quote(css_root)}; then "
        f"find {shlex.quote(css_root)} -xdev -mindepth 1 -maxdepth 1 -type d "
        "! -name '.*' -printf 'counterstrikesharp\\t%p\\t%T@:%s\\n'; fi"
    )
    try:
        success, stdout, stderr = await manager.execute_command(command, timeout=30)
        if not success:
            raise RuntimeError(stderr or stdout or "Unable to inspect installed plugins")

        prefix = game_root + "/"
        candidates: list[dict[str, str]] = []
        for line in stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            kind, absolute, _stat_revision = parts
            if not absolute.startswith(prefix):
                continue
            relative = absolute.removeprefix(prefix)
            name = posixpath.basename(relative)
            key = f"{kind}:{name.casefold()}"
            quoted = shlex.quote(absolute)
            revision_command = (
                f"if test -f {quoted}; then sha256sum -- {quoted} | awk '{{print $1}}'; "
                f"elif test -d {quoted}; then "
                f"find {quoted} -xdev -type f -exec sha256sum -- {{}} + | "
                "LC_ALL=C sort | sha256sum | awk '{print $1}'; else exit 44; fi"
            )
            revision_ok, revision_out, revision_error = await manager.execute_command(
                revision_command, timeout=60
            )
            revision = revision_out.strip().splitlines()[0] if revision_out.strip() else ""
            if not revision_ok or not re.fullmatch(r"[a-fA-F0-9]{64}", revision):
                raise RuntimeError(
                    revision_error or f"Unable to snapshot plugin revision for {name}"
                )
            candidates.append(
                {
                    "key": key,
                    "kind": kind,
                    "name": name,
                    "relative_path": relative,
                    "revision": revision.lower(),
                }
            )
        return sorted(candidates, key=lambda item: (item["kind"], item["name"].casefold()))
    finally:
        await manager.disconnect()


def _plugin_alias(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _link_managed_plugins(
    managed: list[ManagedPlugin], candidate_aliases: dict[str, set[str]]
) -> dict[int, str]:
    links: dict[int, str] = {}
    for plugin in managed:
        if plugin.id is None or plugin.market_plugin_id is None:
            continue
        aliases = {
            _plugin_alias(plugin.display_name),
            _plugin_alias((plugin.repo_url or "").rstrip("/").rsplit("/", 1)[-1]),
            _plugin_alias(posixpath.basename(plugin.custom_install_path or "")),
        }
        aliases.discard("")
        match = next(
            (key for key, values in candidate_aliases.items() if aliases & values),
            None,
        )
        if match is not None:
            links[plugin.market_plugin_id] = match
    return links


async def _group_candidates(
    db: AsyncSession, server_id: int, candidates: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Keep explicitly declared market dependencies in the same diagnostic unit."""
    parent = {item["key"]: item["key"] for item in candidates}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    managed_result = await db.execute(
        select(ManagedPlugin).where(ManagedPlugin.server_id == server_id)
    )
    managed = list(managed_result.scalars().all())
    candidate_aliases = {
        item["key"]: {
            _plugin_alias(item["name"]),
            _plugin_alias(posixpath.splitext(item["name"])[0]),
        }
        for item in candidates
    }
    market_to_candidate = _link_managed_plugins(managed, candidate_aliases)

    market_ids = sorted(market_to_candidate)
    if market_ids:
        market_plugins = await host.MarketPlugin.get_by_ids(db, market_ids)
        for plugin in market_plugins:
            candidate_key = market_to_candidate.get(plugin.id)
            if candidate_key is None:
                continue
            for dependency_id in parse_dependency_ids(plugin.dependencies):
                dependency_key = market_to_candidate.get(dependency_id)
                if dependency_key is not None:
                    union(candidate_key, dependency_key)

    grouped: dict[str, list[str]] = {}
    for item in candidates:
        grouped.setdefault(find(item["key"]), []).append(item["key"])
    groups = []
    for members in grouped.values():
        members.sort()
        groups.append(
            {
                "key": "group:" + hashlib.sha256("\n".join(members).encode()).hexdigest()[:16],
                "candidate_keys": members,
                "reason": "declared_dependency" if len(members) > 1 else "independent",
            }
        )
    return sorted(groups, key=lambda item: item["key"])


async def build_diagnostic_plan(
    db: AsyncSession,
    user: User,
    server_id: int,
    scope: DiagnosticScope,
) -> dict[str, Any]:
    server = await host.authorized_server(db, user, server_id)
    all_candidates = await host._inventory(server)
    candidates = [item for item in all_candidates if scope == "both" or item["kind"] == scope]
    candidate_groups = await host._group_candidates(db, server.id, candidates)
    payload = {
        "server_id": server.id,
        "server_owner_id": server.user_id,
        "scope": scope,
        "candidates": candidates,
        "candidate_groups": candidate_groups,
        "health_policy": _health_policy(server),
    }
    warnings = []
    if not candidates:
        warnings.append("No third-party plugin candidates were found for this scope")
    return {
        **payload,
        "plan_hash": _plan_hash(payload),
        "estimated_max_starts": min(
            host.MAX_START_ATTEMPTS, max(2, len(candidates).bit_length() + 3)
        ),
        "warnings": warnings,
    }


async def get_diagnostic_recommendation(
    db: AsyncSession,
    user: User,
    server_id: int,
) -> dict[str, Any]:
    """Recommend, but never start, isolation after a likely post-update crash loop."""
    server = await host.authorized_server(db, user, server_id)
    from services.server_monitor import server_monitor

    restart_info = server_monitor.get_restart_info(server.id)
    now = get_current_time()
    last_update = server.last_update_time
    if last_update is not None and last_update.tzinfo is None:
        last_update = last_update.replace(tzinfo=now.tzinfo)
    recently_updated = bool(
        last_update is not None and timedelta(0) <= now - last_update <= timedelta(minutes=30)
    )
    restart_count = int(restart_info["restart_count"])
    restart_protection = not bool(restart_info["can_restart"])
    post_update_failures = recently_updated and restart_count >= 2
    recommended = post_update_failures or restart_protection
    if restart_protection:
        reason = "restart_loop_protection"
    elif post_update_failures:
        reason = "post_update_start_failures"
    else:
        reason = None
    return {
        "recommended": recommended,
        "reason": reason,
        "recently_updated": recently_updated,
        "last_update_time": last_update,
        "restart_count": restart_count,
        "max_restarts": int(restart_info["max_restarts"]),
        "window_minutes": 30,
    }


async def get_latest_diagnostic_run(db: AsyncSession, user: User, server_id: int) -> dict[str, Any]:
    await host.authorized_server(db, user, server_id)
    result = await db.execute(
        select(PluginDiagnosticRun)
        .where(PluginDiagnosticRun.server_id == server_id)
        .order_by(
            col(PluginDiagnosticRun.created_at).desc(),
            col(PluginDiagnosticRun.id).desc(),
        )
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise LookupError("Diagnostic run not found")
    return await host.diagnostic_run_payload(db, run)
