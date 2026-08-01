"""Bounded, reversible crash isolation for Metamod and CounterStrikeSharp plugins."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import posixpath
import re
import shlex
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from modules.models import (
    ManagedPlugin,
    MarketPlugin,
    PluginDiagnosticRun,
    PluginDiagnosticStep,
    PluginQuarantineEntry,
    Server,
    User,
)
from modules.utils import get_current_time
from services.a2s_query import a2s_service
from services.ai_access import AgentAccessDenied, authorized_server
from services.ai_security import redact_sensitive_text
from services.maintenance_lock import maintenance_lock_service
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)

DiagnosticScope = Literal["metamod", "counterstrikesharp", "both"]
Progress = Callable[[str, dict[str, Any]], Awaitable[None]]
MAX_START_ATTEMPTS = 12
MAX_DURATION_SECONDS = 20 * 60
HEALTH_OBSERVE_SECONDS = 60
A2S_RECHECK_SECONDS = 2
ACTIVE_DIAGNOSTIC_STATUSES = (
    "running",
    "interrupted",
    "failed",
    "inconclusive",
    "completed_with_quarantine",
)
_blocked_servers: set[int] = set()
FATAL_LOG_PATTERN = re.compile(
    r"segmentation fault|sigsegv|core dumped|fatal error|failed to load.*(?:\.so|plugin)|"
    r"unhandled exception.*terminat|server crashed",
    re.IGNORECASE,
)


def _plan_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _health_policy(server: Server) -> dict[str, Any]:
    return {
        "process_survival_seconds": HEALTH_OBSERVE_SECONDS,
        "a2s_required": bool(server.enable_a2s_monitoring),
        "a2s_successes": 2,
        "fatal_log_delta_forbidden": True,
        "max_start_attempts": MAX_START_ATTEMPTS,
        "max_duration_seconds": MAX_DURATION_SECONDS,
    }


async def _inventory(server: Server) -> list[dict[str, str]]:
    manager = SSHManager()
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
    market_to_candidate: dict[int, str] = {}
    for plugin in managed:
        if plugin.id is None:
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
            if plugin.market_plugin_id is not None:
                market_to_candidate[plugin.market_plugin_id] = match

    market_ids = sorted(market_to_candidate)
    if market_ids:
        market_plugins = await MarketPlugin.get_by_ids(db, market_ids)
        from services.plugin_conflict_service import parse_dependency_ids

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
    server = await authorized_server(db, user, server_id)
    all_candidates = await _inventory(server)
    candidates = [item for item in all_candidates if scope == "both" or item["kind"] == scope]
    candidate_groups = await _group_candidates(db, server.id, candidates)
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
        "estimated_max_starts": min(MAX_START_ATTEMPTS, max(2, len(candidates).bit_length() + 3)),
        "warnings": warnings,
    }


async def get_diagnostic_recommendation(
    db: AsyncSession,
    user: User,
    server_id: int,
) -> dict[str, Any]:
    """Recommend, but never start, isolation after a likely post-update crash loop."""
    server = await authorized_server(db, user, server_id)
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


async def _validate_remote_path(
    manager: SSHManager,
    server: Server,
    absolute_path: str,
    *,
    allow_missing: bool,
) -> None:
    valid, error = await manager.validate_path_within_base(
        server.game_directory,
        absolute_path,
        server,
        allow_missing=allow_missing,
    )
    if not valid:
        raise ValueError(error)


async def _move_entry(
    manager: SSHManager,
    server: Server,
    entry: PluginQuarantineEntry,
    *,
    quarantine: bool,
) -> None:
    root = server.game_directory.rstrip("/")
    source = posixpath.join(root, entry.source_relative_path)
    target = posixpath.join(root, entry.quarantine_relative_path)
    origin, destination = (source, target) if quarantine else (target, source)
    await _validate_remote_path(manager, server, origin, allow_missing=True)
    await _validate_remote_path(manager, server, destination, allow_missing=True)
    command = (
        f"if test -e {shlex.quote(origin)}; then "
        f"if test -e {shlex.quote(destination)}; then exit 42; fi; "
        f"mkdir -p -- {shlex.quote(posixpath.dirname(destination))} && "
        f"mv -- {shlex.quote(origin)} {shlex.quote(destination)}; fi"
    )
    success, stdout, stderr = await manager.execute_command(command, timeout=30)
    if not success:
        raise RuntimeError(stderr or stdout or f"Unable to move {entry.candidate_key}")
    entry.is_quarantined = quarantine
    entry.restored_at = None if quarantine else get_current_time()


async def _set_candidates(
    db: AsyncSession,
    manager: SSHManager,
    server: Server,
    user: User,
    entries: dict[str, PluginQuarantineEntry],
    keys: list[str],
    *,
    quarantine: bool,
) -> None:
    for key in keys:
        entry = entries[key]
        if entry.is_quarantined == quarantine:
            continue
        await authorized_server(db, user, server.id)
        await _move_entry(manager, server, entry, quarantine=quarantine)
        db.add(entry)
        await db.commit()


async def _console_size(manager: SSHManager, server: Server) -> int:
    path = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo/console.log")
    success, stdout, _ = await manager.execute_command(
        f"stat -c %s -- {shlex.quote(path)} 2>/dev/null || printf 0", timeout=10
    )
    return int(stdout.strip()) if success and stdout.strip().isdigit() else 0


async def _console_delta(manager: SSHManager, server: Server, offset: int) -> str:
    path = posixpath.join(server.game_directory.rstrip("/"), "cs2/game/csgo/console.log")
    start = max(1, offset + 1)
    success, stdout, _ = await manager.execute_command(
        f"tail -c +{start} -- {shlex.quote(path)} 2>/dev/null | tail -c 12000", timeout=15
    )
    return stdout if success else ""


async def _record_step(
    db: AsyncSession,
    run: PluginDiagnosticRun,
    phase: str,
    candidate_keys: list[str],
    healthy: bool | None,
    evidence: dict[str, Any],
) -> None:
    result = await db.execute(
        select(PluginDiagnosticStep).where(PluginDiagnosticStep.diagnostic_run_id == run.id)
    )
    sequence = len(list(result.scalars().all())) + 1
    db.add(
        PluginDiagnosticStep(
            diagnostic_run_id=run.id,
            sequence=sequence,
            phase=phase,
            candidate_keys=candidate_keys,
            healthy=healthy,
            evidence=evidence,
        )
    )
    await db.commit()


async def _health_attempt(
    db: AsyncSession,
    run: PluginDiagnosticRun,
    server: Server,
    manager: SSHManager,
    phase: str,
    enabled_keys: list[str],
    progress: Progress | None,
) -> bool:
    principal = await db.get(User, run.requested_by)
    if principal is None:
        raise AgentAccessDenied("The diagnostic user no longer exists")
    server = await authorized_server(db, principal, server.id)
    if run.start_attempts >= MAX_START_ATTEMPTS:
        raise RuntimeError("Diagnostic start-attempt limit reached")
    run.start_attempts += 1
    db.add(run)
    await db.commit()
    offset = await _console_size(manager, server)
    if progress:
        await progress(
            "diagnostic_progress",
            {"phase": phase, "attempt": run.start_attempts, "enabled": enabled_keys},
        )
    start_ok, start_message = await manager.start_server(server)
    if start_ok:
        await asyncio.sleep(HEALTH_OBSERVE_SECONDS)
    status_ok, process_status = await manager.get_server_status(server)
    process_healthy = bool(start_ok and status_ok and process_status == "running")
    a2s_results: list[bool] = []
    a2s_required = bool((run.health_policy or {}).get("a2s_required"))
    if process_healthy and a2s_required:
        host = server.a2s_query_host or server.host
        port = server.a2s_query_port or server.game_port
        for index in range(2):
            a2s_ok, _ = await a2s_service.query_server_info(host, port, timeout=5.0)
            a2s_results.append(a2s_ok)
            if index == 0:
                await asyncio.sleep(A2S_RECHECK_SECONDS)
    delta = await _console_delta(manager, server, offset)
    fatal_matches = [line[:500] for line in delta.splitlines() if FATAL_LOG_PATTERN.search(line)][
        :20
    ]
    healthy = (
        process_healthy and not fatal_matches and (not a2s_required or a2s_results == [True, True])
    )
    evidence = {
        "start_ok": start_ok,
        "start_message": redact_sensitive_text(start_message, limit=1000),
        "process_status": process_status,
        "a2s": a2s_results,
        "fatal_log_lines": [redact_sensitive_text(line, limit=500) for line in fatal_matches],
    }
    await _record_step(db, run, phase, enabled_keys, healthy, evidence)
    return healthy


async def execute_diagnostic_plan(
    db: AsyncSession,
    user: User,
    server_id: int,
    scope: DiagnosticScope,
    expected_plan_hash: str,
    *,
    ai_run_id: str | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    async with maintenance_lock_service.get(
        server_id, operation="plugin_crash_diagnostic", wait=False, ttl=MAX_DURATION_SECONDS + 300
    ):
        server = await authorized_server(db, user, server_id)
        plan = await build_diagnostic_plan(db, user, server_id, scope)
        if plan["plan_hash"] != expected_plan_hash:
            raise ValueError("Diagnostic plan changed; inspect and approve it again")
        if not plan["candidates"]:
            raise ValueError("No plugin candidates are available for this diagnostic scope")

        status_ok, status_value = await SSHManager().get_server_status(server)
        a2s_preflight: bool | None = None
        if server.enable_a2s_monitoring:
            try:
                a2s_preflight, _ = await a2s_service.query_server_info(
                    server.a2s_query_host or server.host,
                    server.a2s_query_port or server.game_port,
                    timeout=5.0,
                )
            except Exception:
                a2s_preflight = False
        health_policy = dict(plan["health_policy"])
        health_policy["a2s_required"] = a2s_preflight is True
        health_policy["a2s_preflight"] = a2s_preflight
        run = PluginDiagnosticRun(
            server_id=server.id,
            requested_by=user.id,
            server_owner_id=server.user_id,
            ai_run_id=ai_run_id,
            scope=scope,
            status="running",
            plan_hash=plan["plan_hash"],
            candidate_snapshot=plan["candidates"],
            original_server_running=bool(status_ok and status_value == "running"),
            health_policy=health_policy,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        _blocked_servers.add(server.id)
        await _record_step(
            db,
            run,
            "preflight_versions_and_health",
            [item["key"] for item in plan["candidates"]],
            None,
            {
                "last_game_update": (
                    server.last_update_time.isoformat() if server.last_update_time else None
                ),
                "original_process_status": status_value if status_ok else "unknown",
                "a2s_preflight": a2s_preflight,
                "candidate_revisions": {
                    item["key"]: item["revision"] for item in plan["candidates"]
                },
            },
        )

        entries: dict[str, PluginQuarantineEntry] = {}
        for candidate in plan["candidates"]:
            quarantine_relative = posixpath.join(
                ".upkk/quarantine", run.id, candidate["relative_path"]
            )
            entry = PluginQuarantineEntry(
                diagnostic_run_id=run.id,
                candidate_key=candidate["key"],
                source_relative_path=candidate["relative_path"],
                quarantine_relative_path=quarantine_relative,
                source_revision=candidate["revision"],
            )
            db.add(entry)
            entries[candidate["key"]] = entry
        await db.commit()
        for entry in entries.values():
            await db.refresh(entry)

        manager = SSHManager()
        connected, message = await manager.connect(server)
        if not connected:
            run.status = "failed"
            run.error = redact_sensitive_text(message, limit=1000)
            db.add(run)
            await db.commit()
            raise RuntimeError(f"SSH connection failed: {message}")

        keys = list(entries)
        try:
            await authorized_server(db, user, server.id)
            await manager.stop_server(server)
            await _set_candidates(db, manager, server, user, entries, keys, quarantine=True)
            baseline_healthy = await _health_attempt(
                db, run, server, manager, "baseline_without_third_party", [], progress
            )
            await manager.stop_server(server)
            if not baseline_healthy:
                await _set_candidates(db, manager, server, user, entries, keys, quarantine=False)
                run.status = "framework_or_core_failure"
                run.error = "Server is unhealthy without third-party plugin candidates"
                run.completed_at = get_current_time()
                db.add(run)
                await db.commit()
                _blocked_servers.discard(server.id)
                return await diagnostic_run_payload(db, run)

            groups = {
                item["key"]: list(item["candidate_keys"]) for item in plan["candidate_groups"]
            }

            def expand(group_keys: list[str]) -> list[str]:
                return [candidate for group_key in group_keys for candidate in groups[group_key]]

            remaining_groups = list(groups)
            while len(remaining_groups) > 1:
                if time.monotonic() - started > MAX_DURATION_SECONDS:
                    raise RuntimeError("Diagnostic duration limit reached")
                group = remaining_groups[: max(1, len(remaining_groups) // 2)]
                group_candidates = expand(group)
                await authorized_server(db, user, server.id)
                await _set_candidates(
                    db, manager, server, user, entries, group_candidates, quarantine=False
                )
                healthy = await _health_attempt(
                    db,
                    run,
                    server,
                    manager,
                    "group_isolation",
                    group_candidates,
                    progress,
                )
                await manager.stop_server(server)
                await _set_candidates(
                    db, manager, server, user, entries, group_candidates, quarantine=True
                )
                remaining_groups = remaining_groups[len(group) :] if healthy else group

            suspect_group = remaining_groups[0]
            suspect_candidates = groups[suspect_group]
            await authorized_server(db, user, server.id)
            await _set_candidates(
                db, manager, server, user, entries, suspect_candidates, quarantine=False
            )
            suspect_healthy = await _health_attempt(
                db,
                run,
                server,
                manager,
                "individual_confirmation",
                suspect_candidates,
                progress,
            )
            await manager.stop_server(server)
            await _set_candidates(
                db, manager, server, user, entries, suspect_candidates, quarantine=True
            )
            if suspect_healthy:
                run.status = "inconclusive"
                run.error = "The suspected plugin group did not reproduce the crash by itself"
                final_keys: list[str] = []
            else:
                for candidate_key in suspect_candidates:
                    entries[candidate_key].is_culprit = True
                    db.add(entries[candidate_key])
                run.culprit_keys = list(suspect_candidates)
                final_keys = [key for key in keys if key not in suspect_candidates]

            await _set_candidates(db, manager, server, user, entries, final_keys, quarantine=False)
            final_healthy = await _health_attempt(
                db,
                run,
                server,
                manager,
                "final_restored_state",
                final_keys,
                progress,
            )
            if final_healthy and run.culprit_keys:
                run.status = "completed_with_quarantine"
            elif final_healthy:
                run.status = "inconclusive"
            else:
                await manager.stop_server(server)
                await _set_candidates(
                    db, manager, server, user, entries, final_keys, quarantine=True
                )
                safe_groups: list[str] = []
                additional_culprits: list[str] = []
                for group_key, candidate_keys in groups.items():
                    if group_key == suspect_group:
                        continue
                    if (
                        run.start_attempts >= MAX_START_ATTEMPTS - 1
                        or time.monotonic() - started > MAX_DURATION_SECONDS
                    ):
                        break
                    await authorized_server(db, user, server.id)
                    await _set_candidates(
                        db, manager, server, user, entries, candidate_keys, quarantine=False
                    )
                    individually_healthy = await _health_attempt(
                        db,
                        run,
                        server,
                        manager,
                        "strict_individual_fallback",
                        candidate_keys,
                        progress,
                    )
                    await manager.stop_server(server)
                    await _set_candidates(
                        db, manager, server, user, entries, candidate_keys, quarantine=True
                    )
                    if individually_healthy:
                        safe_groups.append(group_key)
                    else:
                        additional_culprits.extend(candidate_keys)
                        for candidate_key in candidate_keys:
                            entries[candidate_key].is_culprit = True
                            db.add(entries[candidate_key])
                restored_candidates = expand(safe_groups)
                await _set_candidates(
                    db,
                    manager,
                    server,
                    user,
                    entries,
                    restored_candidates,
                    quarantine=False,
                )
                run.culprit_keys = sorted(set(run.culprit_keys or []) | set(additional_culprits))
                fallback_healthy = False
                if run.start_attempts < MAX_START_ATTEMPTS:
                    fallback_healthy = await _health_attempt(
                        db,
                        run,
                        server,
                        manager,
                        "multi_fault_final_state",
                        restored_candidates,
                        progress,
                    )
                if fallback_healthy and additional_culprits:
                    run.status = "completed_with_quarantine"
                else:
                    await manager.stop_server(server)
                    await _set_candidates(
                        db,
                        manager,
                        server,
                        user,
                        entries,
                        restored_candidates,
                        quarantine=True,
                    )
                    if run.start_attempts < MAX_START_ATTEMPTS:
                        await _health_attempt(
                            db,
                            run,
                            server,
                            manager,
                            "safe_all_plugins_quarantined",
                            [],
                            progress,
                        )
                    run.status = "inconclusive"
                    run.error = (
                        "The strict fallback did not produce a stable final set; all candidates "
                        "remain isolated"
                    )
            run.completed_at = get_current_time()
            db.add(run)
            await db.commit()
            return await diagnostic_run_payload(db, run)
        except Exception as exc:
            run.status = "failed"
            run.error = redact_sensitive_text(str(exc), limit=2000)
            db.add(run)
            await db.commit()
            logger.exception("Plugin diagnostic %s failed", run.id)
            raise
        finally:
            await manager.disconnect()


async def diagnostic_run_payload(db: AsyncSession, run: PluginDiagnosticRun) -> dict[str, Any]:
    steps_result = await db.execute(
        select(PluginDiagnosticStep)
        .where(PluginDiagnosticStep.diagnostic_run_id == run.id)
        .order_by(PluginDiagnosticStep.sequence.asc())
    )
    quarantine_result = await db.execute(
        select(PluginQuarantineEntry)
        .where(PluginQuarantineEntry.diagnostic_run_id == run.id)
        .order_by(PluginQuarantineEntry.id.asc())
    )
    return {
        "id": run.id,
        "server_id": run.server_id,
        "requested_by": run.requested_by,
        "scope": run.scope,
        "status": run.status,
        "plan_hash": run.plan_hash,
        "culprit_keys": run.culprit_keys or [],
        "start_attempts": run.start_attempts,
        "error": run.error,
        "steps": [
            {
                "sequence": item.sequence,
                "phase": item.phase,
                "candidate_keys": item.candidate_keys,
                "healthy": item.healthy,
                "evidence": item.evidence,
            }
            for item in steps_result.scalars().all()
        ],
        "quarantine": [
            {
                "candidate_key": item.candidate_key,
                "source_relative_path": item.source_relative_path,
                "is_quarantined": item.is_quarantined,
                "is_culprit": item.is_culprit,
            }
            for item in quarantine_result.scalars().all()
        ],
        "created_at": run.created_at,
        "completed_at": run.completed_at,
    }


async def get_diagnostic_run(
    db: AsyncSession, user: User, server_id: int, diagnostic_id: str
) -> dict[str, Any]:
    await authorized_server(db, user, server_id)
    result = await db.execute(
        select(PluginDiagnosticRun).where(
            PluginDiagnosticRun.id == diagnostic_id,
            PluginDiagnosticRun.server_id == server_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise LookupError("Diagnostic run not found")
    return await diagnostic_run_payload(db, run)


async def restore_diagnostic_run(
    db: AsyncSession, user: User, server_id: int, diagnostic_id: str
) -> dict[str, Any]:
    async with maintenance_lock_service.get(
        server_id, operation="restore_plugin_quarantine", wait=False, ttl=900
    ):
        server = await authorized_server(db, user, server_id)
        result = await db.execute(
            select(PluginDiagnosticRun).where(
                PluginDiagnosticRun.id == diagnostic_id,
                PluginDiagnosticRun.server_id == server_id,
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise LookupError("Diagnostic run not found")
        entries_result = await db.execute(
            select(PluginQuarantineEntry).where(
                PluginQuarantineEntry.diagnostic_run_id == run.id,
                PluginQuarantineEntry.is_quarantined.is_(True),
            )
        )
        entries = list(entries_result.scalars().all())
        manager = SSHManager()
        connected, message = await manager.connect(server)
        if not connected:
            raise RuntimeError(f"SSH connection failed: {message}")
        try:
            await manager.stop_server(server)
            for entry in entries:
                await authorized_server(db, user, server.id)
                await _move_entry(manager, server, entry, quarantine=False)
                entry.is_culprit = False
                db.add(entry)
                await db.commit()
            if run.original_server_running:
                await authorized_server(db, user, server.id)
                await manager.start_server(server)
            run.status = "restored"
            run.culprit_keys = []
            run.completed_at = get_current_time()
            db.add(run)
            await db.commit()
            _blocked_servers.discard(server.id)
        finally:
            await manager.disconnect()
        return await diagnostic_run_payload(db, run)


async def has_diagnostic_blocker(server_id: int, db: AsyncSession | None = None) -> bool:
    """Return whether background automation must leave the server untouched."""
    if server_id in _blocked_servers:
        return True

    async def query(session: AsyncSession) -> bool:
        result = await session.execute(
            select(PluginDiagnosticRun.id).where(
                PluginDiagnosticRun.server_id == server_id,
                PluginDiagnosticRun.status.in_(ACTIVE_DIAGNOSTIC_STATUSES),
            )
        )
        if result is None or not hasattr(result, "first"):
            return False
        blocked = result.first() is not None
        if blocked:
            _blocked_servers.add(server_id)
        return blocked

    return await query(db) if db is not None else False


async def interrupt_active_plugin_diagnostics() -> int:
    from modules.database import async_session_maker

    async with async_session_maker() as db:
        result = await db.execute(
            select(PluginDiagnosticRun).where(
                PluginDiagnosticRun.status.in_(ACTIVE_DIAGNOSTIC_STATUSES)
            )
        )
        runs = list(result.scalars().all())
        interrupted = 0
        for run in runs:
            _blocked_servers.add(run.server_id)
            if run.status == "running":
                run.status = "interrupted"
                run.error = (
                    "Application restarted during plugin isolation; explicit restore is required"
                )
                db.add(run)
                interrupted += 1
        await db.commit()
        return interrupted
