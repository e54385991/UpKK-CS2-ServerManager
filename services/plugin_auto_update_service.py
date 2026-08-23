"""Per-server automatic updates for tracked GitHub plugins and frameworks."""

import asyncio
import fnmatch
import logging
import re
from datetime import timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import update as sql_update
from sqlmodel import select

from modules.database import async_session_maker
from modules.http_helper import http_helper
from modules.models import CustomCommand, DeploymentLog, ManagedPlugin, Server, User
from modules.schemas import GitHubPluginInstallRequest
from modules.utils import get_current_time
from services.custom_command_service import (
    CustomCommandError,
    execute_custom_commands,
    format_custom_command_log,
)
from services.discord_notification_service import (
    EVENT_PLUGIN_UPDATE,
    discord_notification_service,
)
from services.maintenance_lock import OperationBusyError, maintenance_lock_service
from services.plugin_installation import install_github_plugin
from services.redis_manager import redis_manager
from services.ssh_manager import SSHManager

logger = logging.getLogger(__name__)

CONFIG_EXCLUSIONS = ["*.cfg", "*.conf", "*.ini", "*.json", "*.toml", "*.yaml", "*.yml"]
FRAMEWORKS = {
    "counterstrikesharp": {
        "name": "CounterStrikeSharp",
        "repo_url": "https://github.com/roflmuffin/CounterStrikeSharp",
        "asset_glob": "counterstrikesharp-with-runtime-linux*.zip",
    },
    "metamod": {
        "name": "Metamod:Source",
        "repo_url": "https://github.com/alliedmodders/metamod-source",
        "asset_glob": "mmsource-*-linux.tar.gz",
    },
}
ARCHIVE_EXTENSIONS = (".tar.gz", ".zip", ".tgz", ".tar", ".7z")


def canonical_repo_url(repo_url: str) -> str:
    match = re.match(
        r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$", (repo_url or "").strip()
    )
    if not match:
        raise ValueError("Invalid GitHub repository URL")
    return f"https://github.com/{match.group(1)}/{match.group(2).removesuffix('.git')}"


def repo_api_url(repo_url: str) -> str:
    canonical = canonical_repo_url(repo_url)
    owner, repo = canonical.removeprefix("https://github.com/").split("/", 1)
    return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"


def derive_asset_glob(asset_name: Optional[str], release_tag: Optional[str]) -> Optional[str]:
    if not asset_name:
        return None
    pattern = asset_name
    if release_tag:
        candidates = {release_tag, release_tag.lstrip("vV")}
        for candidate in sorted((value for value in candidates if value), key=len, reverse=True):
            pattern = re.sub(re.escape(candidate), "*", pattern, flags=re.IGNORECASE)
    return pattern if pattern != asset_name else asset_name


async def upsert_managed_plugin(
    *,
    server_id: int,
    source_type: str,
    source_key: str,
    display_name: str,
    repo_url: Optional[str] = None,
    market_plugin_id: Optional[int] = None,
    framework_key: Optional[str] = None,
    installed_release_id: Optional[str] = None,
    installed_version: str = "unknown",
    installed_asset_name: Optional[str] = None,
    asset_glob: Optional[str] = None,
    custom_install_path: Optional[str] = None,
    exclude_dirs: Optional[List[str]] = None,
    exclude_files: Optional[List[str]] = None,
) -> ManagedPlugin:
    """Create or refresh tracking metadata without enabling automatic updates."""
    async with async_session_maker() as db:
        result = await db.execute(
            select(ManagedPlugin).where(
                ManagedPlugin.server_id == server_id,
                ManagedPlugin.source_type == source_type,
                ManagedPlugin.source_key == source_key,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            item = ManagedPlugin(
                server_id=server_id,
                source_type=source_type,
                source_key=source_key,
                display_name=display_name,
                auto_update_enabled=False,
            )
        item.display_name = display_name
        item.repo_url = canonical_repo_url(repo_url) if repo_url else item.repo_url
        item.market_plugin_id = market_plugin_id
        item.framework_key = framework_key
        item.installed_release_id = installed_release_id
        item.installed_version = installed_version or "unknown"
        item.installed_asset_name = installed_asset_name or item.installed_asset_name
        item.asset_glob = asset_glob or item.asset_glob
        item.custom_install_path = custom_install_path
        item.exclude_dirs = list(exclude_dirs or [])
        item.exclude_files = list(exclude_files or [])
        item.last_status = "installed"
        item.last_error = None
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item


class PluginAutoUpdateService:
    CHECK_LOOP_SECONDS = 60

    def __init__(self) -> None:
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self._status_cache: Dict[int, Dict[str, Any]] = {}
        self._redis_status_retry_after = 0.0

    async def _publish_status(
        self,
        server_id: int,
        *,
        state: Optional[str] = None,
        phase: Optional[str] = None,
        message: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        log: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = dict(
            self._status_cache.get(server_id)
            or {
                "state": "idle",
                "phase": "idle",
                "message": "No plugin update task has run yet",
                "current": 0,
                "total": 0,
                "logs": [],
                "started_at": None,
                "finished_at": None,
            }
        )
        if state:
            previous_state = status.get("state")
            status["state"] = state
            if state == "running" and previous_state != "running":
                status["started_at"] = get_current_time().isoformat()
                status["finished_at"] = None
                status["logs"] = []
            elif state in {"completed", "failed"}:
                status["finished_at"] = get_current_time().isoformat()
        if phase is not None:
            status["phase"] = phase
        if message is not None:
            status["message"] = message
        if current is not None:
            status["current"] = current
        if total is not None:
            status["total"] = total
        if log:
            logs = list(status.get("logs") or [])
            logs.append({"time": get_current_time().isoformat(), "message": log})
            status["logs"] = logs[-100:]
        self._status_cache[server_id] = status
        now = asyncio.get_running_loop().time()
        if now >= self._redis_status_retry_after:
            try:
                await asyncio.wait_for(
                    redis_manager.set(
                        f"plugin_auto_update:status:{server_id}", status, expire=86400
                    ),
                    timeout=0.2,
                )
            except Exception as exc:
                self._redis_status_retry_after = now + 30
                logger.debug("Redis plugin status write unavailable; using memory cache: %s", exc)
        return status

    async def get_status(self, server_id: int) -> Dict[str, Any]:
        cached = None
        now = asyncio.get_running_loop().time()
        if now >= self._redis_status_retry_after:
            try:
                cached = await asyncio.wait_for(
                    redis_manager.get(f"plugin_auto_update:status:{server_id}"), timeout=0.2
                )
            except Exception as exc:
                self._redis_status_retry_after = now + 30
                logger.debug("Redis plugin status read unavailable; using memory cache: %s", exc)
        if isinstance(cached, dict):
            self._status_cache[server_id] = cached
            return cached
        return self._status_cache.get(server_id) or {
            "state": "idle",
            "phase": "idle",
            "message": "No plugin update task has run yet",
            "current": 0,
            "total": 0,
            "logs": [],
            "started_at": None,
            "finished_at": None,
        }

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("Plugin auto-update service started")

    async def stop(self) -> None:
        self.running = False
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.check_all_servers()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Plugin auto-update loop failed")
            await asyncio.sleep(self.CHECK_LOOP_SECONDS)

    @staticmethod
    def _due(last_check, interval_hours: float) -> bool:
        if last_check is None:
            return True
        now = get_current_time()
        if last_check.tzinfo is None:
            last_check = last_check.replace(tzinfo=timezone.utc)
        return (now - last_check).total_seconds() >= interval_hours * 3600

    async def check_all_servers(self) -> None:
        async with async_session_maker() as db:
            result = await db.execute(
                select(Server).where(Server.enable_plugin_auto_update.is_(True))
            )
            servers = list(result.scalars().all())
        for server in servers:
            if server.should_skip_background_checks():
                continue
            if self._due(
                server.last_plugin_update_check, server.plugin_update_check_interval_hours or 1.0
            ):
                await self.check_server(server.id)

    @staticmethod
    def _is_windows_asset(asset_name: str) -> bool:
        """Match the platform filtering used by the plugin-market installer."""
        name = (asset_name or "").casefold()
        return "windows" in name or "-win-" in name or "_win_" in name or name.endswith("-win.zip")

    @staticmethod
    def _archive_extension(asset_name: str) -> Optional[str]:
        name = (asset_name or "").casefold()
        for extension in ARCHIVE_EXTENSIONS:
            if name.endswith(extension):
                return extension
        return None

    @classmethod
    def _fallback_release_assets(
        cls, item: ManagedPlugin, assets: List[Dict[str, Any]], pattern: str
    ) -> List[Dict[str, Any]]:
        """Select a changed-but-compatible market asset without guessing.

        A market record stores a glob derived from the asset selected during
        installation. Projects occasionally remove the version component or
        rename the archive in a later release, which makes the literal glob
        return zero matches. We first retain the fixed prefix/suffix and archive
        type, then (for market records) use the same single Linux archive rule as
        the installer. Every fallback still requires exactly one candidate.
        """
        wildcard_positions = [index for index, char in enumerate(pattern) if char in "*?"]
        if wildcard_positions:
            first_wildcard = wildcard_positions[0]
            last_wildcard = wildcard_positions[-1]
            prefix = pattern[:first_wildcard].casefold()
            suffix = pattern[last_wildcard + 1 :].casefold()
        else:
            prefix = pattern.casefold()
            suffix = ""
        expected_extension = cls._archive_extension(pattern)

        all_archives: List[Dict[str, Any]] = []
        archives: List[Dict[str, Any]] = []
        for asset in assets:
            name = str(asset.get("name") or "")
            if not name or cls._is_windows_asset(name):
                continue
            extension = cls._archive_extension(name)
            if extension is None:
                continue
            all_archives.append(asset)
            if not expected_extension or extension == expected_extension:
                archives.append(asset)

        def has_shape(asset: Dict[str, Any], include_suffix: bool = True) -> bool:
            name = str(asset.get("name") or "").casefold()
            if prefix and not name.startswith(prefix):
                return False
            return not (include_suffix and suffix and not name.endswith(suffix))

        # Keep the stored resource rule authoritative wherever possible. This
        # handles e.g. `Plugin-*-linux.tar.gz` -> `Plugin-linux.tar.gz`.
        shaped = [asset for asset in archives if has_shape(asset)]
        if shaped:
            return shaped

        # A market install historically selected the first suitable Linux
        # archive. For auto-update, only accept this compatibility fallback when
        # it is unambiguous, so a changed naming scheme cannot overwrite a wrong
        # file. GitHub registrations still require their configured prefix.
        if item.source_type == "market" or item.market_plugin_id is not None:
            prefixed = [asset for asset in archives if has_shape(asset, include_suffix=False)]
            if prefixed:
                return prefixed
            if archives:
                return archives
            # The market installer accepts any supported Linux archive. If a
            # project changes tar.gz to zip while retaining the plugin prefix,
            # preserve that behavior, but still require a single candidate at
            # the caller so an ambiguous release is never overwritten.
            return [
                asset for asset in all_archives if has_shape(asset, include_suffix=False)
            ] or all_archives
        return []

    async def _latest_github_release(
        self,
        item: ManagedPlugin,
        user: User,
        linux_runtime_profile: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        if not item.repo_url:
            return False, None, "GitHub repository is not configured"
        success, data, error = await http_helper.get(
            repo_api_url(item.repo_url),
            headers={"Accept": "application/vnd.github+json", "User-Agent": "CS2-ServerManager"},
            timeout=30,
            github_token=user.github_token if user.has_github_token else None,
        )
        if not success or not isinstance(data, dict):
            return False, None, error or "Failed to fetch latest stable release"
        if data.get("draft") or data.get("prerelease"):
            return False, None, "GitHub latest release is not a stable release"
        assets = data.get("assets") or []
        pattern = item.asset_glob or "*"
        matches = [
            asset for asset in assets if fnmatch.fnmatchcase(str(asset.get("name") or ""), pattern)
        ]
        fallback_matches: List[Dict[str, Any]] = []
        if not matches:
            fallback_matches = self._fallback_release_assets(item, assets, pattern)
            if len(fallback_matches) == 1:
                matches = fallback_matches
                logger.info(
                    "Using compatible release asset fallback for %s: glob %r -> %s",
                    item.display_name,
                    pattern,
                    fallback_matches[0].get("name"),
                )
            elif linux_runtime_profile is None or not fallback_matches:
                fallback_count = len(fallback_matches)
                return (
                    False,
                    None,
                    (
                        f"Asset glob '{pattern}' matched 0 assets; compatible Linux archive fallback "
                        f"matched {fallback_count}; exactly one is required"
                    ),
                )

        # A scheduled check supplies a freshly detected profile. If the stored
        # glob points at RT3 but the host now needs RT4 (or vice versa), select
        # the sibling from the same asset family instead of pinning stale ABI
        # metadata forever. Registration helpers omit the profile and retain
        # their explicit asset/glob selection.
        if linux_runtime_profile is not None:
            from services.linux_runtime_service import (
                RuntimeSelectionRequired,
                paired_runtime_families,
                prioritize_runtime_assets,
                steam_runtime_asset_family,
            )

            paired = paired_runtime_families(assets)
            reference_families = {
                family
                for name in [
                    item.installed_asset_name,
                    *(str(asset.get("name") or "") for asset in matches),
                    *(str(asset.get("name") or "") for asset in fallback_matches),
                ]
                if name and (family := steam_runtime_asset_family(str(name)))
            }
            related = paired & reference_families
            if len(related) == 1:
                family = next(iter(related))
                siblings = [
                    asset
                    for asset in assets
                    if steam_runtime_asset_family(str(asset.get("name") or "")) == family
                ]
                try:
                    matches = [prioritize_runtime_assets(siblings, linux_runtime_profile)[0]]
                except RuntimeSelectionRequired as exc:
                    return False, None, f"{item.display_name}: {exc}"
            elif len(related) > 1:
                return (
                    False,
                    None,
                    f"Multiple paired Steam Runtime asset families match '{pattern}'",
                )
            elif not matches and fallback_matches:
                matches = fallback_matches

        if len(matches) != 1:
            return (
                False,
                None,
                f"Asset glob '{pattern}' matched {len(matches)} assets; exactly one is required",
            )
        return (
            True,
            {
                "release_id": str(data.get("id") or ""),
                "version": data.get("tag_name") or "unknown",
                "asset": matches[0],
            },
            "",
        )

    async def _latest_metamod(self, server: Server) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        manager = SSHManager()
        connected, message = await manager.connect(server)
        if not connected:
            return False, None, message
        try:
            success, value = await manager._fetch_latest_metamod_url(None)
            if not success:
                return False, None, value
            url = value.strip()
            filename = url.rsplit("/", 1)[-1]
            return (
                True,
                {
                    "release_id": url,
                    "version": filename,
                    "asset": {"browser_download_url": url, "name": filename},
                },
                "",
            )
        finally:
            await manager.disconnect()

    async def _install_item(
        self, server: Server, user: User, item: ManagedPlugin, latest: Dict[str, Any]
    ) -> Tuple[bool, str]:
        if item.framework_key == "metamod":
            return await SSHManager().update_metamod(server)
        if item.framework_key == "counterstrikesharp":
            return await SSHManager().update_counterstrikesharp(server)

        # Automatic updates operate on the selected managed item only.  Do not
        # recurse through a market dependency graph; CONFIG_EXCLUSIONS plus the
        # persisted rules below are the non-destructive upgrade-mode behavior.
        request = GitHubPluginInstallRequest(
            download_url=latest["asset"]["browser_download_url"],
            exclude_dirs=item.exclude_dirs or [],
            exclude_files=list(dict.fromkeys((item.exclude_files or []) + CONFIG_EXCLUSIONS)),
            custom_install_path=item.custom_install_path,
            repo_url=item.repo_url,
            release_id=latest["release_id"],
            release_tag=latest["version"],
            asset_name=latest["asset"].get("name"),
            record_installation=False,
            suppress_notification=True,
        )
        async with async_session_maker() as db:
            result = await install_github_plugin(server.id, request, db, user)
        return result.success, result.message

    async def check_server(
        self, server_id: int, force: bool = False, plugin_id: Optional[int] = None
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

    async def check_plugin(self, server_id: int, plugin_id: int) -> Dict[str, Any]:
        """Run the normal update pipeline for one managed plugin.

        This is intentionally not a dry-run: it verifies the release and then
        executes the same protected installation, backup and batch restart
        policy as a scheduled update, while ignoring the item's auto-update
        switch so it can be tested before enabling it.
        """
        return await self.check_server(server_id, force=True, plugin_id=plugin_id)

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
        self,
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

        async with async_session_maker() as db:
            result = await db.execute(
                select(CustomCommand).where(
                    CustomCommand.server_id == server.id,
                    CustomCommand.id.in_(command_ids),
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
                execution = await execute_custom_commands(
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

            output = format_custom_command_log(custom_command.target, execution.get("results", []))
            async with async_session_maker() as db:
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

    async def _check_server(
        self, server_id: int, force: bool = False, plugin_id: Optional[int] = None
    ) -> Dict[str, Any]:
        lock = maintenance_lock_service.get(
            server_id,
            operation="plugin_auto_update",
            wait=False,
            ttl=3600,
        )

        async with lock:
            run_label = "Plugin test update" if plugin_id is not None else "Plugin update check"
            await self._publish_status(
                server_id,
                state="running",
                phase="checking",
                message=f"Loading {run_label.lower()} configuration",
                current=0,
                total=0,
                log=f"{run_label} started",
            )
            async with async_session_maker() as db:
                from services.plugin_diagnostic_service import has_diagnostic_blocker

                if await has_diagnostic_blocker(server_id, db):
                    return {
                        "success": False,
                        "message": "Plugin diagnostic quarantine requires attention",
                    }
                server = await db.get(Server, server_id)
                if not server:
                    await self._publish_status(
                        server_id,
                        state="failed",
                        phase="failed",
                        message="Server not found",
                        log="Server not found",
                    )
                    return {"success": False, "message": "Server not found"}
                if not force and not server.enable_plugin_auto_update:
                    await self._publish_status(
                        server_id,
                        state="completed",
                        phase="disabled",
                        message="Plugin auto-update is disabled",
                        log="Scheduled check skipped because the server-level switch is disabled",
                    )
                    return {"success": False, "message": "Plugin auto-update is disabled"}
                user = await db.get(User, server.user_id)
                post_update_commands_enabled = bool(
                    getattr(server, "enable_plugin_post_update_commands", False)
                )
                post_update_command_ids = self._normalize_command_ids(
                    getattr(server, "plugin_post_update_command_ids", None)
                )
                item_filters = [ManagedPlugin.server_id == server_id]
                if plugin_id is None:
                    item_filters.append(ManagedPlugin.auto_update_enabled.is_(True))
                else:
                    item_filters.append(ManagedPlugin.id == plugin_id)
                result = await db.execute(select(ManagedPlugin).where(*item_filters))
                items = list(result.scalars().all())
                # A manual per-item test must not postpone the next scheduled
                # server-wide check.
                if plugin_id is None:
                    await db.execute(
                        sql_update(Server)
                        .where(Server.id == server_id)
                        .values(last_plugin_update_check=get_current_time())
                    )
                await db.commit()

            from services.linux_runtime_service import detect_linux_runtime_profile

            linux_runtime_profile = await detect_linux_runtime_profile(server)

            if plugin_id is not None and not items:
                message = "Managed plugin not found"
                await self._publish_status(
                    server_id, state="failed", phase="failed", message=message, log=message
                )
                return {"success": False, "message": message}

            if not user:
                await self._publish_status(
                    server_id,
                    state="failed",
                    phase="failed",
                    message="Server owner not found",
                    log="Server owner not found",
                )
                return {"success": False, "message": "Server owner not found"}

            candidates: List[Tuple[ManagedPlugin, Dict[str, Any]]] = []
            resolve_failures: List[Tuple[ManagedPlugin, str]] = []
            await self._publish_status(
                server_id,
                phase="checking_releases",
                message=f"Checking {len(items)} selected plugin(s)",
                current=0,
                total=len(items),
                log=f"Found {len(items)} selected plugin(s)",
            )
            for item_index, item in enumerate(items, start=1):
                await self._publish_status(
                    server_id,
                    phase="checking_releases",
                    message=f"Requesting latest release for {item.display_name}",
                    current=item_index - 1,
                    total=len(items),
                    log=f"Requesting release metadata: {item.display_name}",
                )
                try:
                    if item.framework_key == "metamod":
                        ok, latest, error = await self._latest_metamod(server)
                    else:
                        ok, latest, error = await self._latest_github_release(
                            item,
                            user,
                            linux_runtime_profile,
                        )
                except Exception as exc:
                    logger.exception(
                        "Failed to resolve latest release for managed plugin %s", item.id
                    )
                    ok, latest, error = False, None, str(exc)
                same_release = bool(
                    ok
                    and latest
                    and (
                        item.installed_release_id == latest["release_id"]
                        or item.installed_version == latest["version"]
                    )
                )
                selected_asset_name = str(latest["asset"].get("name") or "") if latest else ""
                same_asset = bool(
                    not item.installed_asset_name
                    or item.installed_asset_name == selected_asset_name
                )
                if not item.installed_asset_name and item.asset_glob:
                    same_asset = fnmatch.fnmatchcase(selected_asset_name, item.asset_glob)
                is_current = same_release and same_asset
                async with async_session_maker() as db:
                    saved = await db.get(ManagedPlugin, item.id)
                    if saved:
                        saved.last_check_at = get_current_time()
                        saved.latest_version = latest["version"] if latest else None
                        if not ok:
                            saved.last_status = "failed"
                            saved.last_error = error
                        elif is_current:
                            saved.last_status = "up_to_date"
                            saved.last_error = None
                        db.add(saved)
                        await db.commit()
                if not ok or not latest:
                    resolve_failures.append((item, error))
                    await self._publish_status(
                        server_id,
                        current=item_index,
                        log=f"Release check failed for {item.display_name}: {error}",
                    )
                elif not is_current:
                    candidates.append((item, latest))
                    await self._publish_status(
                        server_id,
                        current=item_index,
                        log=f"Update available for {item.display_name}: {item.installed_version} -> {latest['version']}",
                    )
                else:
                    await self._publish_status(
                        server_id, current=item_index, log=f"{item.display_name} is up to date"
                    )

            if not candidates:
                if resolve_failures:
                    discord_notification_service.queue_notify(
                        server,
                        EVENT_PLUGIN_UPDATE,
                        "plugin_auto_update",
                        False,
                        "One or more plugin update checks failed; no files were changed.",
                        title="Plugin automatic update check failed",
                        details={
                            "Failures": "\n".join(
                                f"{item.display_name}: {error}" for item, error in resolve_failures
                            )
                        },
                    )
                terminal_success = not resolve_failures
                terminal_message = (
                    "No plugin updates available"
                    if terminal_success
                    else "Plugin update checks failed"
                )
                await self._publish_status(
                    server_id,
                    state="completed" if terminal_success else "failed",
                    phase="completed" if terminal_success else "failed",
                    message=terminal_message,
                    current=len(items),
                    total=len(items),
                    log=terminal_message,
                )
                return {
                    "success": not resolve_failures,
                    "message": terminal_message,
                    "failures": [
                        f"{item.display_name}: {error}" for item, error in resolve_failures
                    ],
                }

            candidates.sort(
                key=lambda pair: {"metamod": 0, "counterstrikesharp": 1}.get(
                    pair[0].framework_key, 2
                )
            )
            # Restart is a batch-level policy for every managed item (ordinary
            # GitHub/market plugins and frameworks).  Multiple selected items
            # therefore result in one state check and, at most, one restart.
            restart_candidates = [item for item, _ in candidates if item.restart_after_update]
            status_check_ok = True
            was_running = False
            status_check_message = "Restart not requested"
            if restart_candidates:
                await self._publish_status(
                    server_id,
                    phase="checking_server",
                    message="Checking server state for batch restart policy",
                    current=0,
                    total=len(candidates),
                    log="Checking whether the server is currently running",
                )
                status_check_ok, server_state = await SSHManager().get_server_status(server)
                was_running = status_check_ok and server_state == "running"
                status_check_message = (
                    server_state if status_check_ok else "Could not determine server state"
                )

            targets = "\n".join(
                f"{item.display_name}: {item.installed_version} → {latest['version']}"
                for item, latest in candidates
            )
            discord_notification_service.queue_notify(
                server,
                EVENT_PLUGIN_UPDATE,
                "plugin_auto_update",
                True,
                "Plugin auto-update is starting. Restart settings will be applied once after the batch.",
                title="Plugin automatic update started",
                details={
                    "Updates": targets,
                    "Backup Before Update": ", ".join(
                        item.display_name for item, _ in candidates if item.backup_before_update
                    )
                    or "Not requested",
                    "Auto Restart": ", ".join(item.display_name for item in restart_candidates)
                    or "Not requested",
                },
                state="in_progress",
            )

            backup_items = [
                (item, latest) for item, latest in candidates if item.backup_before_update
            ]
            backup_success = True
            backup_message = "Not requested"
            backup_blocked_ids = set()
            if backup_items:
                await self._publish_status(
                    server_id,
                    phase="backup",
                    message="Creating local backup for selected plugins",
                    current=0,
                    total=len(candidates),
                    log="Backup requested by: "
                    + ", ".join(item.display_name for item, _ in backup_items),
                )
                backup_success, backup_message = await SSHManager().backup_plugins(server)
            if backup_items and not backup_success:
                message = (
                    f"Plugin backup failed; plugins requiring backup were skipped: {backup_message}"
                )
                backup_blocked_ids = {item.id for item, _ in backup_items}
                async with async_session_maker() as db:
                    for item, _ in backup_items:
                        saved = await db.get(ManagedPlugin, item.id)
                        if saved:
                            saved.last_status = "failed"
                            saved.last_error = message
                            db.add(saved)
                    await db.commit()
                await self._publish_status(
                    server_id,
                    phase="backup_failed",
                    message=message,
                    current=0,
                    total=len(candidates),
                    log=message,
                )

            results: List[Dict[str, Any]] = []
            if backup_items and backup_success:
                update_start_message = "Selected-plugin backup completed; starting plugin updates"
                update_start_log = f"Backup completed: {backup_message}"
            elif backup_items:
                update_start_message = "Continuing plugins that do not require backup"
                update_start_log = (
                    "Backup-required plugins were skipped; continuing unprotected plugins"
                )
            else:
                update_start_message = "No plugin backups requested; starting plugin updates"
                update_start_log = "Backup skipped because no selected plugin enabled it"
            await self._publish_status(
                server_id, phase="updating", message=update_start_message, log=update_start_log
            )
            for update_index, (item, latest) in enumerate(candidates, start=1):
                if item.id in backup_blocked_ids:
                    message = f"Skipped because the requested backup failed: {backup_message}"
                    results.append(
                        {
                            "name": item.display_name,
                            "success": False,
                            "message": message,
                            "version": latest["version"],
                            "restart_after_update": item.restart_after_update,
                            "source_type": item.source_type,
                        }
                    )
                    await self._publish_status(
                        server_id,
                        current=update_index,
                        total=len(candidates),
                        log=f"Skipped {item.display_name}: requested backup failed",
                    )
                    continue
                await self._publish_status(
                    server_id,
                    phase="updating",
                    message=f"Updating {item.display_name}",
                    current=update_index - 1,
                    total=len(candidates),
                    log=f"Updating {item.display_name} to {latest['version']}",
                )
                try:
                    success, message = await self._install_item(server, user, item, latest)
                except Exception as exc:
                    logger.exception("Managed plugin update failed for item %s", item.id)
                    success, message = False, str(exc)
                async with async_session_maker() as db:
                    saved = await db.get(ManagedPlugin, item.id)
                    if saved:
                        saved.last_status = "success" if success else "failed"
                        saved.last_error = None if success else message
                        if success:
                            saved.installed_release_id = latest["release_id"]
                            saved.installed_version = latest["version"]
                            saved.installed_asset_name = latest["asset"].get("name")
                            saved.asset_glob = derive_asset_glob(
                                latest["asset"].get("name"), latest["version"]
                            )
                            saved.last_update_at = get_current_time()
                        db.add(saved)
                        await db.commit()
                results.append(
                    {
                        "name": item.display_name,
                        "success": success,
                        "message": message,
                        "version": latest["version"],
                        "asset_name": latest["asset"].get("name"),
                        "restart_after_update": item.restart_after_update,
                        "source_type": item.source_type,
                    }
                )
                await self._publish_status(
                    server_id,
                    current=update_index,
                    log=f"{'Completed' if success else 'Failed'} {item.display_name}: {message}",
                )

            successful_restart_items = [
                result for result in results if result["success"] and result["restart_after_update"]
            ]
            restart_success = True
            restart_message = "Not requested"
            if successful_restart_items:
                if not status_check_ok:
                    restart_success = False
                    restart_message = "Automatic restart requested, but the pre-update server state could not be determined"
                elif not was_running:
                    restart_message = "Skipped because the server was stopped before the update"
                else:
                    await self._publish_status(
                        server_id,
                        phase="restarting",
                        message="Restarting server once after plugin update batch",
                        current=len(candidates),
                        total=len(candidates),
                        log="Batch restart policy triggered one server restart",
                    )
                    restart_manager = SSHManager()
                    (
                        manager_ready,
                        preflight_message,
                    ) = await restart_manager.check_session_manager_available(server)
                    if not manager_ready:
                        restart_success = False
                        restart_message = (
                            f"Restart aborted before stopping: {preflight_message}. "
                            "The existing game session was left untouched."
                        )
                        await self._publish_status(server_id, log=restart_message)
                    else:
                        stop_success, stop_message = await restart_manager.stop_server(server)
                        await self._publish_status(server_id, log=f"Stop result: {stop_message}")
                        if not stop_success:
                            # Never issue start after a failed stop: that could
                            # create a second process in another managed session.
                            restart_success = False
                            restart_message = (
                                f"Restart failed while stopping server: {stop_message}"
                            )
                            await self._publish_status(server_id, log=restart_message)
                        else:
                            await asyncio.sleep(0.5)
                            start_success, start_message = await restart_manager.start_server(
                                server
                            )
                            restart_success = start_success
                            restart_message = (
                                start_message
                                if start_success
                                else f"Restart failed: {start_message}"
                            )
                            await self._publish_status(
                                server_id, log=f"Start result: {restart_message}"
                            )

            post_update_success = True
            post_update_message = "Not requested"
            post_update_results: List[Dict[str, Any]] = []
            successful_updates = [result for result in results if result["success"]]
            # Only after every selected plugin update has finished (and any batch
            # restart has been attempted). Commands run only when at least one
            # plugin was actually updated successfully.
            if post_update_commands_enabled and post_update_command_ids and successful_updates:
                await self._publish_status(
                    server_id,
                    phase="post_update_commands",
                    message="Running configured post-update quick commands",
                    current=len(candidates),
                    total=len(candidates),
                    log=(
                        "All plugin updates finished; executing "
                        f"{len(post_update_command_ids)} post-update quick command(s)"
                    ),
                )
                post_update = await self._execute_post_update_commands(
                    server, post_update_command_ids
                )
                post_update_success = bool(post_update.get("success"))
                post_update_message = post_update.get("message") or post_update_message
                post_update_results = list(post_update.get("results") or [])
            elif post_update_commands_enabled and post_update_command_ids:
                post_update_message = (
                    "Skipped because no plugin was updated successfully in this batch"
                )
                await self._publish_status(server_id, log=post_update_message)

            all_success = (
                all(result["success"] for result in results)
                and not resolve_failures
                and restart_success
                and post_update_success
            )
            summary_lines = [
                f"{'✓' if result['success'] else '✗'} {result['name']}: {result['version']}"
                + ("" if result["success"] else f" — {result['message']}")
                for result in results
            ]
            summary_lines.extend(
                f"✗ {item.display_name}: {error}" for item, error in resolve_failures
            )
            discord_notification_service.queue_notify(
                server,
                EVENT_PLUGIN_UPDATE,
                "plugin_auto_update",
                all_success,
                "Plugin update batch completed and the configured restart policy was applied once."
                if all_success
                else "One or more plugin updates or the configured batch restart failed.",
                title="Plugin automatic update completed"
                if all_success
                else "Plugin automatic update failed",
                details={
                    "Results": "\n".join(summary_lines),
                    "Backup": backup_message,
                    "Restart": restart_message,
                    "Post-update Commands": (
                        post_update_message
                        if not post_update_results
                        else "\n".join(
                            ("OK" if item["success"] else "FAIL")
                            + " "
                            + item["name"]
                            + ": "
                            + item["message"]
                            for item in post_update_results
                        )
                    ),
                },
            )
            terminal_message = (
                "Plugin update batch completed"
                if all_success
                else "Plugin update batch completed with failures"
            )
            await self._publish_status(
                server_id,
                state="completed" if all_success else "failed",
                phase="completed" if all_success else "failed",
                message=terminal_message,
                current=len(candidates),
                total=len(candidates),
                log=(
                    f"{terminal_message}. Restart: {restart_message}. "
                    f"Post-update commands: {post_update_message}"
                ),
            )
            return {
                "success": all_success,
                "message": terminal_message,
                "results": results,
                "restart": {
                    "success": restart_success,
                    "message": restart_message,
                    "previous_state": status_check_message,
                },
                "post_update_commands": {
                    "success": post_update_success,
                    "message": post_update_message,
                    "results": post_update_results,
                },
            }


plugin_auto_update_service = PluginAutoUpdateService()


async def record_framework_installation(server: Server, user: User, framework_key: str) -> None:
    config = FRAMEWORKS[framework_key]
    probe = ManagedPlugin(
        server_id=server.id,
        source_type="framework",
        source_key=framework_key,
        display_name=config["name"],
        repo_url=config["repo_url"],
        framework_key=framework_key,
        asset_glob=config["asset_glob"],
    )
    if framework_key == "metamod":
        ok, latest, _ = await plugin_auto_update_service._latest_metamod(server)
    else:
        ok, latest, _ = await plugin_auto_update_service._latest_github_release(probe, user)
    await upsert_managed_plugin(
        server_id=server.id,
        source_type="framework",
        source_key=framework_key,
        display_name=config["name"],
        repo_url=config["repo_url"],
        framework_key=framework_key,
        installed_release_id=latest["release_id"] if ok and latest else None,
        installed_version=latest["version"] if ok and latest else "unknown",
        asset_glob=config["asset_glob"],
    )


async def record_known_github_installation(
    server: Server, user: User, repo_url: str, display_name: str, asset_glob: str
) -> None:
    canonical = canonical_repo_url(repo_url)
    probe = ManagedPlugin(
        server_id=server.id,
        source_type="github",
        source_key=canonical.lower(),
        display_name=display_name,
        repo_url=canonical,
        asset_glob=asset_glob,
    )
    ok, latest, _ = await plugin_auto_update_service._latest_github_release(probe, user)
    await upsert_managed_plugin(
        server_id=server.id,
        source_type="github",
        source_key=canonical.lower(),
        display_name=display_name,
        repo_url=canonical,
        installed_release_id=latest["release_id"] if ok and latest else None,
        installed_version=latest["version"] if ok and latest else "unknown",
        asset_glob=asset_glob,
    )
