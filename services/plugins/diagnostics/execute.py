"""Execute and restore plugin crash-isolation runs."""

from __future__ import annotations

import logging
import posixpath
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from modules.models import (
    PluginDiagnosticRun,
    PluginDiagnosticStep,
    PluginQuarantineEntry,
    Server,
    User,
)
from services.ai_security import redact_sensitive_text
from services.compat import LateBoundModule
from services.plugins.diagnostic_policy import (
    ACTIVE_DIAGNOSTIC_STATUSES,
)
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)
DiagnosticScope = Literal["metamod", "counterstrikesharp", "both"]
Progress = Callable[[str, dict[str, Any]], Awaitable[None]]
host = LateBoundModule("services.plugin_diagnostic_service")


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
        raise host.AgentAccessDenied("The diagnostic user no longer exists")
    server = await host.authorized_server(db, principal, server.id)
    if run.start_attempts >= host.MAX_START_ATTEMPTS:
        raise RuntimeError("Diagnostic start-attempt limit reached")
    run.start_attempts += 1
    db.add(run)
    await db.commit()
    offset = await host._console_size(manager, server)
    if progress:
        await host._emit_readable_progress(progress, phase, enabled_keys)
        await progress(
            "diagnostic_progress",
            {"phase": phase, "attempt": run.start_attempts, "enabled": enabled_keys},
        )
    start_ok, start_message = await manager.start_server(server)
    if start_ok:
        await host.asyncio.sleep(host.HEALTH_OBSERVE_SECONDS)
    status_ok, process_status = await manager.get_server_status(server)
    process_healthy = bool(start_ok and status_ok and process_status == "running")
    a2s_results: list[bool] = []
    a2s_required = bool((run.health_policy or {}).get("a2s_required"))
    if process_healthy and a2s_required:
        query_host = server.a2s_query_host or server.host
        port = server.a2s_query_port or server.game_port
        for index in range(2):
            a2s_ok, _ = await host.a2s_service.query_server_info(query_host, port, timeout=5.0)
            a2s_results.append(a2s_ok)
            if index == 0:
                await host.asyncio.sleep(host.A2S_RECHECK_SECONDS)
    delta = await host._console_delta(manager, server, offset)
    fatal_matches = [
        line[:500] for line in delta.splitlines() if host.FATAL_LOG_PATTERN.search(line)
    ][:20]
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
    await host._record_step(db, run, phase, enabled_keys, healthy, evidence)
    return healthy


def _expand_groups(groups: dict[str, list[str]], group_keys: list[str]) -> list[str]:
    return [candidate for group_key in group_keys for candidate in groups[group_key]]


async def _run_group_isolation(
    db: AsyncSession,
    user: User,
    server: Server,
    manager: SSHManager,
    run: PluginDiagnosticRun,
    entries: dict[str, PluginQuarantineEntry],
    groups: dict[str, list[str]],
    started: float,
    progress: Progress | None,
) -> str:
    remaining_groups = list(groups)
    while len(remaining_groups) > 1:
        if host.time.monotonic() - started > host.MAX_DURATION_SECONDS:
            raise RuntimeError("Diagnostic duration limit reached")
        group = remaining_groups[: max(1, len(remaining_groups) // 2)]
        group_candidates = _expand_groups(groups, group)
        await host.authorized_server(db, user, server.id)
        await host._set_candidates(
            db, manager, server, user, entries, group_candidates, quarantine=False
        )
        healthy = await host._health_attempt(
            db, run, server, manager, "group_isolation", group_candidates, progress
        )
        await manager.stop_server(server)
        await host._set_candidates(
            db, manager, server, user, entries, group_candidates, quarantine=True
        )
        remaining_groups = remaining_groups[len(group) :] if healthy else group
    return remaining_groups[0]


async def _run_strict_fallback(
    db: AsyncSession,
    user: User,
    server: Server,
    manager: SSHManager,
    run: PluginDiagnosticRun,
    entries: dict[str, PluginQuarantineEntry],
    groups: dict[str, list[str]],
    suspect_group: str,
    keys: list[str],
    started: float,
    progress: Progress | None,
) -> None:
    await manager.stop_server(server)
    await host._set_candidates(db, manager, server, user, entries, keys, quarantine=True)
    safe_groups: list[str] = []
    additional_culprits: list[str] = []
    for group_key, candidate_keys in groups.items():
        if group_key == suspect_group or run.start_attempts >= host.MAX_START_ATTEMPTS - 1:
            continue
        if host.time.monotonic() - started > host.MAX_DURATION_SECONDS:
            break
        await host.authorized_server(db, user, server.id)
        await host._set_candidates(
            db, manager, server, user, entries, candidate_keys, quarantine=False
        )
        healthy = await host._health_attempt(
            db, run, server, manager, "strict_individual_fallback", candidate_keys, progress
        )
        await manager.stop_server(server)
        await host._set_candidates(
            db, manager, server, user, entries, candidate_keys, quarantine=True
        )
        if healthy:
            safe_groups.append(group_key)
            continue
        additional_culprits.extend(candidate_keys)
        for candidate_key in candidate_keys:
            entries[candidate_key].is_culprit = True
            db.add(entries[candidate_key])
    restored_candidates = _expand_groups(groups, safe_groups)
    await host._set_candidates(
        db, manager, server, user, entries, restored_candidates, quarantine=False
    )
    run.culprit_keys = sorted(set(run.culprit_keys or []) | set(additional_culprits))
    fallback_healthy = False
    if run.start_attempts < host.MAX_START_ATTEMPTS:
        fallback_healthy = await host._health_attempt(
            db, run, server, manager, "multi_fault_final_state", restored_candidates, progress
        )
    if fallback_healthy and additional_culprits:
        run.status = "completed_with_quarantine"
        return
    await manager.stop_server(server)
    await host._set_candidates(
        db, manager, server, user, entries, restored_candidates, quarantine=True
    )
    if run.start_attempts < host.MAX_START_ATTEMPTS:
        await host._health_attempt(
            db, run, server, manager, "safe_all_plugins_quarantined", [], progress
        )
    run.status = "inconclusive"
    run.error = (
        "The strict fallback did not produce a stable final set; all candidates remain isolated"
    )


async def _run_suspect_analysis(
    db: AsyncSession,
    user: User,
    server: Server,
    manager: SSHManager,
    run: PluginDiagnosticRun,
    entries: dict[str, PluginQuarantineEntry],
    groups: dict[str, list[str]],
    suspect_group: str,
    keys: list[str],
    started: float,
    progress: Progress | None,
) -> None:
    suspect_candidates = groups[suspect_group]
    await host.authorized_server(db, user, server.id)
    await host._set_candidates(
        db, manager, server, user, entries, suspect_candidates, quarantine=False
    )
    suspect_healthy = await host._health_attempt(
        db, run, server, manager, "individual_confirmation", suspect_candidates, progress
    )
    await manager.stop_server(server)
    await host._set_candidates(
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
    await host._set_candidates(db, manager, server, user, entries, final_keys, quarantine=False)
    final_healthy = await host._health_attempt(
        db, run, server, manager, "final_restored_state", final_keys, progress
    )
    if final_healthy and run.culprit_keys:
        run.status = "completed_with_quarantine"
    elif final_healthy:
        run.status = "inconclusive"
    else:
        await host._run_strict_fallback(
            db, user, server, manager, run, entries, groups, suspect_group, keys, started, progress
        )


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
    started = host.time.monotonic()
    async with host.maintenance_lock_service.get(
        server_id,
        operation="plugin_crash_diagnostic",
        wait=False,
        ttl=host.MAX_DURATION_SECONDS + 300,
    ):
        server = await host.authorized_server(db, user, server_id)
        plan = await host.build_diagnostic_plan(db, user, server_id, scope)
        if plan["plan_hash"] != expected_plan_hash:
            raise ValueError("Diagnostic plan changed; inspect and approve it again")
        if not plan["candidates"]:
            raise ValueError("No plugin candidates are available for this diagnostic scope")

        status_ok, status_value = await host.SSHManager().get_server_status(server)
        a2s_preflight: bool | None = None
        if server.enable_a2s_monitoring:
            try:
                a2s_preflight, _ = await host.a2s_service.query_server_info(
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
        host._blocked_servers.add(server.id)
        if progress:
            await host._emit_readable_progress(progress, "preflight_versions_and_health", [])
        await host._record_step(
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

        manager = host.SSHManager()
        connected, message = await manager.connect(server)
        if not connected:
            run.status = "failed"
            run.error = redact_sensitive_text(message, limit=1000)
            db.add(run)
            await db.commit()
            raise RuntimeError(f"SSH connection failed: {message}")

        keys = list(entries)
        try:
            await host.authorized_server(db, user, server.id)
            if progress:
                await host._emit_readable_progress(progress, "baseline_without_third_party", [])
            await manager.stop_server(server)
            await host._set_candidates(db, manager, server, user, entries, keys, quarantine=True)
            baseline_healthy = await host._health_attempt(
                db, run, server, manager, "baseline_without_third_party", [], progress
            )
            await manager.stop_server(server)
            if not baseline_healthy:
                await host._set_candidates(
                    db, manager, server, user, entries, keys, quarantine=False
                )
                run.status = "framework_or_core_failure"
                run.error = "Server is unhealthy without third-party plugin candidates"
                run.completed_at = host.get_current_time()
                db.add(run)
                await db.commit()
                host._blocked_servers.discard(server.id)
                return await host.diagnostic_run_payload(db, run)

            groups = {
                item["key"]: list(item["candidate_keys"]) for item in plan["candidate_groups"]
            }
            suspect_group = await host._run_group_isolation(
                db, user, server, manager, run, entries, groups, started, progress
            )
            await host._run_suspect_analysis(
                db,
                user,
                server,
                manager,
                run,
                entries,
                groups,
                suspect_group,
                keys,
                started,
                progress,
            )
            run.completed_at = host.get_current_time()
            db.add(run)
            await db.commit()
            return await host.diagnostic_run_payload(db, run)
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
        .order_by(col(PluginDiagnosticStep.sequence).asc())
    )
    quarantine_result = await db.execute(
        select(PluginQuarantineEntry)
        .where(PluginQuarantineEntry.diagnostic_run_id == run.id)
        .order_by(col(PluginQuarantineEntry.id).asc())
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
    await host.authorized_server(db, user, server_id)
    result = await db.execute(
        select(PluginDiagnosticRun).where(
            PluginDiagnosticRun.id == diagnostic_id,
            PluginDiagnosticRun.server_id == server_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise LookupError("Diagnostic run not found")
    return await host.diagnostic_run_payload(db, run)


async def restore_diagnostic_run(
    db: AsyncSession, user: User, server_id: int, diagnostic_id: str
) -> dict[str, Any]:
    async with host.maintenance_lock_service.get(
        server_id, operation="restore_plugin_quarantine", wait=False, ttl=900
    ):
        server = await host.authorized_server(db, user, server_id)
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
                col(PluginQuarantineEntry.is_quarantined).is_(True),
            )
        )
        entries = list(entries_result.scalars().all())
        manager = host.SSHManager()
        connected, message = await manager.connect(server)
        if not connected:
            raise RuntimeError(f"SSH connection failed: {message}")
        try:
            await manager.stop_server(server)
            for entry in entries:
                await host.authorized_server(db, user, server.id)
                await host._move_entry(manager, server, entry, quarantine=False)
                entry.is_culprit = False
                db.add(entry)
                await db.commit()
            if run.original_server_running:
                await host.authorized_server(db, user, server.id)
                await manager.start_server(server)
            run.status = "restored"
            run.culprit_keys = []
            run.completed_at = host.get_current_time()
            db.add(run)
            await db.commit()
            host._blocked_servers.discard(server.id)
        finally:
            await manager.disconnect()
        return await host.diagnostic_run_payload(db, run)


async def interrupt_active_plugin_diagnostics() -> int:
    from modules.database import async_session_maker

    async with async_session_maker() as db:
        result = await db.execute(
            select(PluginDiagnosticRun).where(
                col(PluginDiagnosticRun.status).in_(ACTIVE_DIAGNOSTIC_STATUSES)
            )
        )
        runs = list(result.scalars().all())
        interrupted = 0
        for run in runs:
            host._blocked_servers.add(run.server_id)
            if run.status == "running":
                run.status = "interrupted"
                run.error = (
                    "Application restarted during plugin isolation; explicit restore is required"
                )
                db.add(run)
                interrupted += 1
        await db.commit()
        return interrupted
