"""Drive inbox mutations through the existing hub public API."""

from __future__ import annotations

from typing import Any


class HubInboxLifecycle:
    """Progress → complete → fail → clear, using only hub entry points."""

    def __init__(self, server_id: int, actor_id: int) -> None:
        self.server_id = server_id
        self.actor_id = actor_id
        self.running_id: str | None = None

    async def play(self, step: str) -> str:
        from services.server_operation_hub import server_operation_hub

        if step == "progress":
            await self._progress(server_operation_hub)
        elif step == "complete":
            await self._complete(server_operation_hub)
        elif step == "fail":
            await self._fail(server_operation_hub)
        elif step == "clear":
            await server_operation_hub.clear_failed([self.server_id])
        return step

    async def _create(self, hub: Any, command: str) -> dict[str, Any]:
        return await hub.create(
            server_id=self.server_id,
            action="install_plugin",
            actor_user_id=self.actor_id,
            command=command,
        )

    async def _progress(self, hub: Any) -> None:
        if self.running_id is None:
            record = await self._create(hub, "perf realtime progress")
            self.running_id = str(record["operation_id"])
            await hub.mark_running(self.running_id)
        await hub.emit(
            self.running_id,
            "progress",
            kind="status",
            message="perf realtime progress",
        )

    async def _complete(self, hub: Any) -> None:
        if self.running_id is None:
            await self._progress(hub)
        if self.running_id is None:
            return
        await hub.finish(self.running_id, success=True, message="perf realtime complete")
        self.running_id = None

    async def _fail(self, hub: Any) -> None:
        record = await self._create(hub, "perf realtime fail")
        operation_id = str(record["operation_id"])
        await hub.mark_running(operation_id)
        await hub.finish(operation_id, success=False, message="perf realtime fail")
