"""Revision-checked planning and execution for CS2 startup configuration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import Server, ServerStatus, User
from modules.server_startup import (
    GAME_MODE_MAPPING,
    normalize_additional_parameters,
    normalize_default_map,
    normalize_game_mode,
    normalize_game_type,
)
from services.a2s_query import a2s_service
from services.game_session import normalize_session_manager
from services.maintenance_lock import maintenance_lock_service
from services.redis_manager import redis_manager
from services.server_lifecycle_policy import apply_user_lifecycle_intent
from services.ssh_manager import SSHManager

ProgressCallback = Callable[..., Awaitable[None]]

EDITABLE_STARTUP_FIELDS = (
    "default_map",
    "max_players",
    "game_mode",
    "game_type",
    "additional_parameters",
)

# A plan is invalidated when any field which contributes to the effective
# startup command changes, including fields the Agent itself cannot edit.
STARTUP_REVISION_FIELDS = (
    "game_port",
    "game_directory",
    "server_name",
    "server_password",
    "rcon_password",
    "steam_account_token",
    "default_map",
    "max_players",
    "game_mode",
    "game_type",
    "additional_parameters",
    "ip_address",
    "client_port",
    "tv_port",
    "tv_enable",
    "cpu_affinity",
    "session_manager",
)


class StartupPlanError(ValueError):
    """Raised when a startup change cannot be planned or safely applied."""


def startup_configuration_revision(server: Server) -> str:
    payload = {field: getattr(server, field, None) for field in STARTUP_REVISION_FIELDS}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _plan_hash(plan: dict[str, Any]) -> str:
    payload = {key: value for key, value in plan.items() if key != "plan_hash"}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _normalize_changes(request: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if "default_map" in request:
        changes["default_map"] = normalize_default_map(request["default_map"])
    if "max_players" in request:
        players = int(request["max_players"])
        if not 1 <= players <= 64:
            raise StartupPlanError("Maximum players must be between 1 and 64")
        changes["max_players"] = players
    if "game_mode" in request:
        changes["game_mode"] = normalize_game_mode(request["game_mode"])
    if "game_type" in request:
        changes["game_type"] = normalize_game_type(request["game_type"])
    if "additional_parameters" in request:
        changes["additional_parameters"] = normalize_additional_parameters(
            request["additional_parameters"]
        )

    # Named modes have an authoritative numeric pair. Keep the dedicated
    # game_type field synchronized unless the caller explicitly supplied it.
    requested_mode = changes.get("game_mode")
    if requested_mode in GAME_MODE_MAPPING and "game_type" not in request:
        changes["game_type"] = GAME_MODE_MAPPING[requested_mode][0]
    return changes


def build_server_startup_plan(server: Server, request: dict[str, Any]) -> dict[str, Any]:
    """Return an immutable before/after plan without touching the server."""
    changes = _normalize_changes(request)
    before = {field: getattr(server, field, None) for field in EDITABLE_STARTUP_FIELDS}
    after = dict(before)
    after.update(changes)
    changed_fields = [field for field in EDITABLE_STARTUP_FIELDS if before[field] != after[field]]
    effective_changes = [
        {"field": field, "before": before[field], "after": after[field]} for field in changed_fields
    ]
    blocking_reasons = [] if changed_fields else ["The requested startup settings are unchanged"]
    plan: dict[str, Any] = {
        "server_id": server.id,
        "configuration_revision": startup_configuration_revision(server),
        "before": before,
        "after": after,
        "changes": effective_changes,
        "steps": [
            {"action": "validate_startup_revision"},
            {"action": "save_startup_settings", "fields": changed_fields},
            {"action": "restart_server"},
            {"action": "verify_server"},
        ],
        "blocked": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "partial_failure_policy": (
            "If saving succeeds but restart or verification fails, the new settings remain saved "
            "and the result reports that partial failure explicitly."
        ),
    }
    plan["plan_hash"] = _plan_hash(plan)
    return plan


async def _current_server(db: AsyncSession, user: User, server_id: int) -> Server:
    server = (
        await Server.get_by_id(db, server_id)
        if user.is_admin
        else await Server.get_by_id_and_user(db, server_id, user.id)
    )
    if server is None:
        raise StartupPlanError("Server permission changed before execution")
    return server


async def _verify_process(server: Server) -> tuple[bool, str]:
    manager = SSHManager()
    success, message = await manager.connect(server)
    if not success:
        return False, f"Process verification SSH connection failed: {message}"
    try:
        running_managers = await manager._running_server_session_managers(server)
    except Exception as exc:
        return False, f"Process verification failed: {exc}"
    finally:
        await manager.disconnect()
    expected = normalize_session_manager(server.session_manager)
    if expected not in running_managers:
        return False, "The expected detached CS2 session is not running"
    return True, f"CS2 process is running in the {expected} session"


async def execute_server_startup_plan(
    db: AsyncSession,
    user: User,
    server_id: int,
    request: dict[str, Any],
    expected_plan_hash: str,
    *,
    progress: ProgressCallback | None = None,
    lock_operation: str = "server_startup_update",
) -> dict[str, Any]:
    """Save an approved startup plan, restart CS2, and verify the result."""

    async def report(step_id: str, step_status: str, message: str) -> None:
        if progress is not None:
            await progress(
                message,
                "info" if step_status not in {"failed", "interrupted"} else "error",
                {"step_id": step_id, "step_status": step_status},
            )

    async with maintenance_lock_service.get(
        server_id,
        operation=lock_operation,
        wait=False,
        ttl=1800,
    ):
        server = await _current_server(db, user, server_id)
        plan = build_server_startup_plan(server, request)
        await report(
            "validate_startup_revision",
            "running",
            "Validating the approved startup configuration revision",
        )
        if plan["plan_hash"] != expected_plan_hash:
            await report(
                "validate_startup_revision",
                "failed",
                "Startup configuration changed before approval execution",
            )
            raise StartupPlanError(
                "Startup configuration changed before approval; review and approve a new plan"
            )
        if plan["blocked"]:
            await report(
                "validate_startup_revision",
                "failed",
                "; ".join(plan["blocking_reasons"]),
            )
            raise StartupPlanError("; ".join(plan["blocking_reasons"]))

        manager = SSHManager()
        preflight_ok, preflight_message = await manager.check_session_manager_available(server)
        if not preflight_ok:
            await report("validate_startup_revision", "failed", preflight_message)
            raise StartupPlanError(
                f"Restart preflight failed before settings were saved: {preflight_message}"
            )
        await report(
            "validate_startup_revision",
            "completed",
            "Approved revision and restart prerequisites are still valid",
        )

        await report("save_startup_settings", "running", "Saving startup settings")
        for change in plan["changes"]:
            setattr(server, change["field"], change["after"])
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            for change in plan["changes"]:
                setattr(server, change["field"], change["before"])
            await report(
                "save_startup_settings",
                "failed",
                "Database save failed; no restart was attempted",
            )
            raise
        cache_cleared = await redis_manager.clear_server_cache(server_id)
        await report("save_startup_settings", "completed", "Startup settings saved")

        async def commit_status(status: ServerStatus) -> str | None:
            server.status = status
            try:
                await db.commit()
            except Exception as exc:
                await db.rollback()
                return f"Panel status could not be saved: {exc}"
            return None

        async def restart_failure(message: str) -> dict[str, Any]:
            status_warning = await commit_status(ServerStatus.ERROR)
            await report("restart_server", "failed", message)
            await report(
                "verify_server",
                "interrupted",
                "Verification was skipped because restart failed",
            )
            return {
                "success": False,
                "partial_failure": True,
                "configuration_saved": True,
                "cache_cleared": cache_cleared,
                "changes": plan["changes"],
                "restart": {"success": False, "message": message},
                "verification": {"process": False, "a2s": False},
                "status_tracking_warning": status_warning,
                "message": (
                    "Startup settings were saved, but the server restart failed. "
                    "The saved settings will be used on the next successful start."
                ),
            }

        await report("restart_server", "running", "Restarting the CS2 server")
        apply_user_lifecycle_intent(server, "restart")
        await db.commit()
        try:
            stopped, stop_message = await manager.stop_server(server)
        except Exception as exc:
            return await restart_failure(f"Restart stop failed unexpectedly: {exc}")
        if not stopped:
            return await restart_failure(f"Restart stopped before start: {stop_message}")
        start_progress = None
        if progress is not None:

            async def start_progress(message: str) -> None:
                await progress(message, "info", None)

        try:
            started, start_message = await manager.start_server(server, start_progress)
        except Exception as exc:
            return await restart_failure(f"Restart start failed unexpectedly: {exc}")
        if not started:
            return await restart_failure(start_message)
        await report("restart_server", "completed", start_message)

        await report("verify_server", "running", "Verifying the CS2 process and A2S response")
        process_ok, process_message = await _verify_process(server)
        a2s_host = server.a2s_query_host or server.host
        a2s_port = server.a2s_query_port or server.game_port
        a2s_ok, a2s_info = await a2s_service.query_server_info(a2s_host, a2s_port, timeout=3.0)
        verification = {
            "process": process_ok,
            "process_message": process_message,
            "a2s": a2s_ok,
            "a2s_host": a2s_host,
            "a2s_port": a2s_port,
            "a2s_info": a2s_info,
        }
        if not process_ok:
            verification_status_warning = await commit_status(ServerStatus.ERROR)
            await report("verify_server", "failed", process_message)
            return {
                "success": False,
                "partial_failure": True,
                "configuration_saved": True,
                "cache_cleared": cache_cleared,
                "changes": plan["changes"],
                "restart": {"success": True, "message": start_message},
                "verification": verification,
                "status_tracking_warning": verification_status_warning,
                "message": "Settings were saved and start returned success, but process verification failed.",
            }

        verification_message = process_message
        if a2s_ok:
            verification_message += "; A2S responded successfully"
        else:
            verification_message += "; A2S is not responding yet"
        status_warning = await commit_status(ServerStatus.RUNNING)
        await report("verify_server", "completed", verification_message)
        return {
            "success": True,
            "partial_failure": False,
            "configuration_saved": True,
            "cache_cleared": cache_cleared,
            "changes": plan["changes"],
            "restart": {"success": True, "message": start_message},
            "verification": verification,
            "status_tracking_warning": status_warning,
            "message": verification_message,
        }
