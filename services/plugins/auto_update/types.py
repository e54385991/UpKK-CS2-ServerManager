"""Shared typing for auto-update mixins composed into the public service."""

from __future__ import annotations

from typing import Any, Protocol

from modules.models import ManagedPlugin, Server, User


class AutoUpdateServiceProtocol(Protocol):
    CHECK_LOOP_SECONDS: int

    async def _publish_status(
        self,
        server_id: int,
        *,
        state: str | None = None,
        phase: str | None = None,
        message: str | None = None,
        current: int | None = None,
        total: int | None = None,
        log: str | None = None,
    ) -> dict[str, Any]: ...

    async def _check_server(
        self, server_id: int, force: bool = False, plugin_id: int | None = None
    ) -> dict[str, Any]: ...

    async def check_server(
        self, server_id: int, force: bool = False, plugin_id: int | None = None
    ) -> dict[str, Any]: ...

    async def _latest_metamod(self, server: Server) -> tuple[bool, dict[str, Any] | None, str]: ...

    async def _latest_github_release(
        self,
        item: ManagedPlugin,
        user: User,
        linux_runtime_profile: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | None, str]: ...

    async def _install_item(
        self, server: Server, user: User, item: ManagedPlugin, latest: dict[str, Any]
    ) -> tuple[bool, str]: ...

    def _normalize_command_ids(self, values: Any) -> list[int]: ...

    async def _execute_post_update_commands(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def build_plugin_upgrade_plan(self, server_id: int, plugin_id: int) -> dict[str, Any]: ...
