"""Auto-update mixins looked up through the public service facade."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import col, select

from modules.models import CustomCommand, DeploymentLog, ManagedPlugin, Server, User
from services.compat import LateBoundModule
from services.custom_command_service import CustomCommandError
from services.maintenance_lock import OperationBusyError
from services.plugins.ai_install_policy import managed_install_request
from services.plugins.auto_update.types import AutoUpdateServiceProtocol

logger = logging.getLogger("services.plugin_auto_update_service")
host = LateBoundModule("services.plugin_auto_update_service")


class AutoUpdateApplyMixin:
    async def _install_item(
        self, server: Server, user: User, item: ManagedPlugin, latest: Dict[str, Any]
    ) -> Tuple[bool, str]:
        if item.framework_key == "metamod":
            return await host._ssh_manager().update_metamod(server)
        if item.framework_key == "counterstrikesharp":
            return await host._ssh_manager().update_counterstrikesharp(server)

        request = await managed_install_request(item, latest, host.CONFIG_EXCLUSIONS)
        async with host.async_session_maker() as db:
            result = await host.install_github_plugin(server.id, request, db, user)
        return result.success, result.message

    async def check_server(
        self: AutoUpdateServiceProtocol,
        server_id: int,
        force: bool = False,
        plugin_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            return await self._check_server(server_id, force=force, plugin_id=plugin_id)
        except OperationBusyError:
            return {
                "success": False,
                "message": "Another maintenance operation is already running",
            }
        except Exception as exc:
            logger.exception(
                "Plugin update task failed for server %s (plugin %s)",
                server_id,
                plugin_id or "batch",
            )
            await self._publish_status(
                server_id,
                state="failed",
                phase="failed",
                message=str(exc),
                log=f"Task failed: {exc}",
            )
            raise

    async def check_plugin(
        self: AutoUpdateServiceProtocol, server_id: int, plugin_id: int
    ) -> Dict[str, Any]:
        """Run the normal update pipeline for one managed plugin.

        This is intentionally not a dry-run: it verifies the release and then
        executes the same protected installation, backup and batch restart
        policy as a scheduled update, while ignoring the item's auto-update
        switch so it can be tested before enabling it.
        """
        return await self.check_server(server_id, force=True, plugin_id=plugin_id)

    async def build_plugin_upgrade_plan(
        self: AutoUpdateServiceProtocol, server_id: int, plugin_id: int
    ) -> Dict[str, Any]:
        """Build a stable, read-only plan for one tracked plugin or framework upgrade."""
        async with host.async_session_maker() as db:
            server = await db.get(Server, server_id)
            item = await db.get(ManagedPlugin, plugin_id)
            if server is None or item is None or item.server_id != server_id:
                raise ValueError("Managed plugin not found")
            user = await db.get(User, server.user_id)
            if user is None or not user.is_active:
                raise ValueError("Server owner not found or inactive")
        from services.linux_runtime_service import detect_linux_runtime_profile

        runtime = await detect_linux_runtime_profile(server)
        if item.framework_key == "metamod":
            ok, latest, error = await self._latest_metamod(server)
        else:
            ok, latest, error = await self._latest_github_release(item, user, runtime)
        if not ok or latest is None:
            raise ValueError(error or "Unable to resolve the latest release")
        selected_asset = str(latest["asset"].get("name") or "")
        same_release = bool(
            item.installed_release_id == latest["release_id"]
            or item.installed_version == latest["version"]
        )
        same_asset = bool(
            not item.installed_asset_name or item.installed_asset_name == selected_asset
        )
        return {
            "server_id": server_id,
            "plugin_id": item.id,
            "name": item.display_name,
            "source_type": item.source_type,
            "framework": item.framework_key,
            "installed_version": item.installed_version,
            "latest_version": latest["version"],
            "release_id": latest["release_id"],
            "asset": selected_asset,
            "backup_before_update": bool(item.backup_before_update),
            "config_policy": item.config_policy or "preserve",
            "config_exclusions": list(host.CONFIG_EXCLUSIONS),
            "restart_after_update": bool(item.restart_after_update),
            "no_op": same_release and same_asset,
        }

    @staticmethod
    def _normalize_command_ids(values) -> List[int]:
        if not values:
            return []
        cleaned: List[int] = []
        seen = set()
        for value in values:
            try:
                command_id = int(value)
            except TypeError, ValueError:
                continue
            if command_id <= 0 or command_id in seen:
                continue
            seen.add(command_id)
            cleaned.append(command_id)
        return cleaned

    async def _execute_post_update_commands(
        self: AutoUpdateServiceProtocol,
        server: Server,
        command_ids: List[int],
    ) -> Dict[str, Any]:
        """Execute configured quick commands after the full plugin update batch."""
        command_ids = self._normalize_command_ids(command_ids)
        if not command_ids:
            return {
                "success": True,
                "message": "No post-update quick commands configured",
                "results": [],
            }

        async with host.async_session_maker() as db:
            result = await db.execute(
                select(CustomCommand).where(
                    CustomCommand.server_id == server.id,
                    col(CustomCommand.id).in_(command_ids),
                )
            )
            by_id = {item.id: item for item in result.scalars().all()}

        ordered = [(command_id, by_id.get(command_id)) for command_id in command_ids]
        command_results: List[Dict[str, Any]] = []
        overall_success = True

        for index, (command_id, custom_command) in enumerate(ordered, start=1):
            if custom_command is None:
                overall_success = False
                entry = {
                    "id": command_id,
                    "name": f"#{command_id}",
                    "target": None,
                    "success": False,
                    "message": "Quick command no longer exists on this server",
                }
                command_results.append(entry)
                await self._publish_status(
                    server.id,
                    phase="post_update_commands",
                    message=f"Post-update quick command missing: #{command_id}",
                    log=entry["message"],
                )
                continue

            await self._publish_status(
                server.id,
                phase="post_update_commands",
                message=f"Executing post-update quick command: {custom_command.name}",
                current=index - 1,
                total=len(ordered),
                log=(
                    f"Running post-update quick command {index}/{len(ordered)}: "
                    f"{custom_command.name} ({custom_command.target})"
                ),
            )
            try:
                execution = await host.execute_custom_commands(
                    server, custom_command.target, custom_command.commands
                )
            except CustomCommandError as exc:
                execution = {
                    "success": False,
                    "message": str(exc),
                    "target": custom_command.target,
                    "results": [],
                }
            except Exception as exc:
                logger.exception(
                    "Post-update quick command failed for server %s command %s",
                    server.id,
                    command_id,
                )
                execution = {
                    "success": False,
                    "message": str(exc),
                    "target": custom_command.target,
                    "results": [],
                }

            success = bool(execution.get("success"))
            overall_success = overall_success and success
            entry = {
                "id": command_id,
                "name": custom_command.name,
                "target": custom_command.target,
                "success": success,
                "message": execution.get("message") or ("Success" if success else "Failed"),
            }
            command_results.append(entry)

            output = host.format_custom_command_log(
                custom_command.target, execution.get("results", [])
            )
            async with host.async_session_maker() as db:
                db.add(
                    DeploymentLog(
                        server_id=server.id,
                        action=f"plugin_post_update_command_{custom_command.target}",
                        status="success" if success else "failed",
                        output=(
                            "Plugin auto-update post command: "
                            + custom_command.name
                            + "\n\n"
                            + output
                        ).strip(),
                        error_message=None if success else entry["message"],
                    )
                )
                await db.commit()

            await self._publish_status(
                server.id,
                current=index,
                total=len(ordered),
                log=(
                    f"{'Completed' if success else 'Failed'} post-update quick command "
                    f"{custom_command.name}: {entry['message']}"
                ),
            )

        message = (
            f"Executed {len(command_results)} post-update quick command(s)"
            if overall_success
            else "One or more post-update quick commands failed"
        )
        return {
            "success": overall_success,
            "message": message,
            "results": command_results,
        }
