"""Plan and apply the newer-glibc patchelf fix during a quick install.

Debian 13 and Ubuntu 25 ship a glibc that refuses libraries with an executable
stack, so the CounterStrikeSharp libraries a quick install writes have to be
patched before the game starts again. The plan advertises the step so the
operator sees it up front, and the executor runs it between the stop and the
start, while nothing has the libraries mapped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from modules.models import Server
from services.server_compatibility import (
    build_clear_execstack_command,
    normalize_execstack_targets,
    run_clear_execstack,
)
from services.ssh_manager import SSHManager

ProgressCallback = Callable[..., Awaitable[None]]

EXECSTACK_STEP_ID = "clear_execstack"


def append_execstack_step(
    server: Server,
    enabled: bool,
    steps: list[dict[str, Any]],
    mutations: list[dict[str, Any]],
) -> None:
    """Plan the newer-glibc patchelf fix for the plugin libraries we install."""
    if not enabled:
        return
    targets = normalize_execstack_targets(getattr(server, "execstack_fix_targets", None))
    steps.append(
        {
            "id": EXECSTACK_STEP_ID,
            "action": EXECSTACK_STEP_ID,
            "status": "pending",
            "command": build_clear_execstack_command(server.game_directory, targets),
            "targets": list(targets),
        }
    )
    mutations.append(
        {
            "id": EXECSTACK_STEP_ID,
            "target": ", ".join(targets),
            "before": "executable-stack flag set",
            "after": "executable-stack flag cleared",
            "destructive": False,
            "status": "pending",
        }
    )


async def run_planned_execstack_step(
    plan: dict[str, Any],
    server: Server,
    report: ProgressCallback,
) -> None:
    """Apply the planned patchelf fix, if any, without failing the install.

    A host that lacks ``patchelf --clear-execstack`` still gets its plugins;
    the operator is told the flag could not be cleared instead of losing the
    whole quick install to it.
    """
    step = next(
        (item for item in plan.get("steps") or [] if item.get("id") == EXECSTACK_STEP_ID),
        None,
    )
    if step is None:
        return
    await report(
        EXECSTACK_STEP_ID,
        "running",
        "Clearing the executable-stack flag from the installed plugin libraries",
    )
    fixed, detail = await run_clear_execstack(SSHManager(), server, step.get("targets"))
    if fixed:
        await report(EXECSTACK_STEP_ID, "completed", f"Executable-stack flag cleared: {detail}")
    else:
        await report(
            EXECSTACK_STEP_ID,
            "failed",
            f"Executable-stack cleanup failed; continuing the install: {detail}",
        )
