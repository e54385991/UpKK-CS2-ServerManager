"""Progress snapshots and assistant delta emission."""

from __future__ import annotations

import time
from typing import Any

from modules.models import AIMessage, AIRun, AIToolRun
from services.ai_events import ai_event_hub
from services.compat import LateBoundModule

host = LateBoundModule("services.ai_orchestrator")


async def _emit(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
    await ai_event_hub.emit(run_id, event_type, payload)


def _add_run_error_message(db, run: AIRun, message: str) -> None:
    db.add(
        AIMessage(
            conversation_id=run.conversation_id,
            role="assistant",
            content=host.redact_sensitive_text(message, limit=2000),
            tool_name=host.RUN_ERROR_TOOL_NAME,
            visible=True,
        )
    )


def _update_progress_snapshot(tool_run: AIToolRun, payload: dict[str, Any]) -> None:
    previous = getattr(tool_run, "progress_snapshot", None)
    snapshot = dict(previous) if isinstance(previous, dict) else {"version": 1, "steps": []}
    steps = [dict(step) for step in snapshot.get("steps") or [] if isinstance(step, dict)]
    now = host.get_current_time()
    step_id = str(payload.get("step_id") or "")
    step_status = str(payload.get("step_status") or "")
    if step_status not in host.STEP_STATUSES:
        step_status = ""
    if step_id and step_status:
        for step in steps:
            if str(step.get("id")) != step_id:
                continue
            step["status"] = step_status
            if step_status == "running" and not step.get("started_at"):
                step["started_at"] = now.isoformat()
            if step_status in host.TERMINAL_STEP_STATUSES:
                step["completed_at"] = now.isoformat()
            break
    running = next((step for step in steps if step.get("status") == "running"), None)
    snapshot.update(
        {
            "version": 1,
            "steps": steps,
            "current_step": running.get("id") if running else None,
            "message": host.redact_sensitive_text(str(payload.get("message") or ""), limit=2000),
            "completed": sum(step.get("status") in {"completed", "skipped"} for step in steps),
            "total": len(steps),
        }
    )
    tool_run.progress_snapshot = snapshot
    tool_run.progress_updated_at = now


def _finalize_progress_snapshot(
    tool_run: AIToolRun, *, success: bool, message: str, interrupted: bool = False
) -> None:
    snapshot = getattr(tool_run, "progress_snapshot", None)
    if not isinstance(snapshot, dict):
        return
    steps = [dict(step) for step in snapshot.get("steps") or [] if isinstance(step, dict)]
    now = host.get_current_time()
    failure_recorded = any(step.get("status") == "failed" for step in steps)
    for step in steps:
        if step.get("status") in host.TERMINAL_STEP_STATUSES:
            continue
        if success:
            status = "completed"
        elif interrupted:
            status = "interrupted"
        elif step.get("status") == "running" or not failure_recorded:
            status = "failed"
            failure_recorded = True
        else:
            status = "interrupted"
        step["status"] = status
        step["started_at"] = step.get("started_at") or now.isoformat()
        step["completed_at"] = now.isoformat()
    snapshot.update(
        {
            "steps": steps,
            "current_step": None,
            "message": host.redact_sensitive_text(message, limit=2000),
            "completed": sum(step.get("status") in {"completed", "skipped"} for step in steps),
            "total": len(steps),
        }
    )
    tool_run.progress_snapshot = snapshot
    tool_run.progress_updated_at = now


class _AssistantDeltaEmitter:
    """Coalesce provider tokens into bounded, responsive browser events."""

    def __init__(self, run_id: str, round_index: int, input_tokens: int = 0) -> None:
        self.run_id = run_id
        self.round_index = round_index
        self.input_tokens = max(input_tokens, 0)
        self.buffer = ""
        self.output_chars = 0
        self.last_emit = time.monotonic()

    async def add(self, delta: str) -> None:
        self.buffer += delta
        self.output_chars += len(delta)
        now = time.monotonic()
        if len(self.buffer) >= host.AI_DELTA_EVENT_CHARS or now - self.last_emit >= 0.1:
            await self.flush()

    async def flush(self) -> None:
        if not self.buffer:
            return
        delta, self.buffer = self.buffer, ""
        self.last_emit = time.monotonic()
        await host._emit(
            self.run_id,
            "assistant_delta",
            {"round": self.round_index, "delta": delta},
        )
        output_tokens = max(1, (self.output_chars + 3) // 4)
        await host._emit(
            self.run_id,
            "token_usage",
            {
                "round": self.round_index,
                "input_tokens": self.input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": self.input_tokens + output_tokens,
                "estimated": True,
                "streaming": True,
            },
        )
