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
from modules.models import ManagedPlugin, Server, User
from modules.schemas import GitHubPluginInstallRequest
from modules.utils import get_current_time
from services.discord_notification_service import (
    EVENT_PLUGIN_UPDATE,
    discord_notification_service,
)
from services.maintenance_lock import maintenance_lock_service
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


def canonical_repo_url(repo_url: str) -> str:
    match = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$", (repo_url or "").strip())
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
        status = dict(self._status_cache.get(server_id) or {
            "state": "idle", "phase": "idle", "message": "No plugin update task has run yet",
            "current": 0, "total": 0, "logs": [], "started_at": None, "finished_at": None,
        })
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
                    redis_manager.set(f"plugin_auto_update:status:{server_id}", status, expire=86400),
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
            "state": "idle", "phase": "idle", "message": "No plugin update task has run yet",
            "current": 0, "total": 0, "logs": [], "started_at": None, "finished_at": None,
        }

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("Plugin auto-update service started")

    def stop(self) -> None:
        self.running = False
        if self.task:
            self.task.cancel()
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
            result = await db.execute(select(Server).where(Server.enable_plugin_auto_update.is_(True)))
            servers = list(result.scalars().all())
        for server in servers:
            if server.should_skip_background_checks():
                continue
            if self._due(server.last_plugin_update_check, server.plugin_update_check_interval_hours or 1.0):
                await self.check_server(server.id)

    async def _latest_github_release(
        self, item: ManagedPlugin, user: User
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
        matches = [asset for asset in assets if fnmatch.fnmatchcase(asset.get("name", ""), pattern)]
        if len(matches) != 1:
            return False, None, f"Asset glob '{pattern}' matched {len(matches)} assets; exactly one is required"
        return True, {
            "release_id": str(data.get("id") or ""),
            "version": data.get("tag_name") or "unknown",
            "asset": matches[0],
        }, ""

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
            return True, {"release_id": url, "version": filename, "asset": {"browser_download_url": url, "name": filename}}, ""
        finally:
            await manager.disconnect()

    async def _install_item(
        self, server: Server, user: User, item: ManagedPlugin, latest: Dict[str, Any]
    ) -> Tuple[bool, str]:
        if item.framework_key == "metamod":
            return await SSHManager().update_metamod(server)
        if item.framework_key == "counterstrikesharp":
            return await SSHManager().update_counterstrikesharp(server)

        from api.routes.github_plugins import install_github_plugin
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

    async def check_server(self, server_id: int, force: bool = False) -> Dict[str, Any]:
        try:
            return await self._check_server(server_id, force=force)
        except Exception as exc:
            logger.exception("Plugin update task failed for server %s", server_id)
            await self._publish_status(
                server_id, state="failed", phase="failed", message=str(exc), log=f"Task failed: {exc}"
            )
            raise

    async def _check_server(self, server_id: int, force: bool = False) -> Dict[str, Any]:
        lock = maintenance_lock_service.get(server_id)
        if lock.locked():
            return {"success": False, "message": "Another maintenance operation is already running"}

        async with lock:
            await self._publish_status(
                server_id, state="running", phase="checking", message="Loading plugin update configuration",
                current=0, total=0, log="Plugin update check started",
            )
            async with async_session_maker() as db:
                server = await db.get(Server, server_id)
                if not server:
                    await self._publish_status(server_id, state="failed", phase="failed", message="Server not found", log="Server not found")
                    return {"success": False, "message": "Server not found"}
                if not force and not server.enable_plugin_auto_update:
                    await self._publish_status(
                        server_id, state="completed", phase="disabled", message="Plugin auto-update is disabled",
                        log="Scheduled check skipped because the server-level switch is disabled",
                    )
                    return {"success": False, "message": "Plugin auto-update is disabled"}
                user = await db.get(User, server.user_id)
                result = await db.execute(
                    select(ManagedPlugin).where(
                        ManagedPlugin.server_id == server_id,
                        ManagedPlugin.auto_update_enabled.is_(True),
                    )
                )
                items = list(result.scalars().all())
                await db.execute(
                    sql_update(Server).where(Server.id == server_id).values(last_plugin_update_check=get_current_time())
                )
                await db.commit()

            if not user:
                await self._publish_status(server_id, state="failed", phase="failed", message="Server owner not found", log="Server owner not found")
                return {"success": False, "message": "Server owner not found"}

            candidates: List[Tuple[ManagedPlugin, Dict[str, Any]]] = []
            resolve_failures: List[Tuple[ManagedPlugin, str]] = []
            await self._publish_status(
                server_id, phase="checking_releases", message=f"Checking {len(items)} selected plugin(s)",
                current=0, total=len(items), log=f"Found {len(items)} selected plugin(s)",
            )
            for item_index, item in enumerate(items, start=1):
                await self._publish_status(
                    server_id, phase="checking_releases", message=f"Requesting latest release for {item.display_name}",
                    current=item_index - 1, total=len(items), log=f"Requesting release metadata: {item.display_name}",
                )
                try:
                    if item.framework_key == "metamod":
                        ok, latest, error = await self._latest_metamod(server)
                    else:
                        ok, latest, error = await self._latest_github_release(item, user)
                except Exception as exc:
                    logger.exception("Failed to resolve latest release for managed plugin %s", item.id)
                    ok, latest, error = False, None, str(exc)
                is_current = bool(
                    ok and latest and (
                        item.installed_release_id == latest["release_id"]
                        or item.installed_version == latest["version"]
                    )
                )
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
                    await self._publish_status(server_id, current=item_index, log=f"Release check failed for {item.display_name}: {error}")
                elif not is_current:
                    candidates.append((item, latest))
                    await self._publish_status(
                        server_id, current=item_index,
                        log=f"Update available for {item.display_name}: {item.installed_version} -> {latest['version']}",
                    )
                else:
                    await self._publish_status(server_id, current=item_index, log=f"{item.display_name} is up to date")

            if not candidates:
                if resolve_failures:
                    discord_notification_service.queue_notify(
                        server, EVENT_PLUGIN_UPDATE, "plugin_auto_update", False,
                        "One or more plugin update checks failed; no files were changed.",
                        title="Plugin automatic update check failed",
                        details={"Failures": "\n".join(f"{item.display_name}: {error}" for item, error in resolve_failures)},
                    )
                terminal_success = not resolve_failures
                terminal_message = "No plugin updates available" if terminal_success else "Plugin update checks failed"
                await self._publish_status(
                    server_id, state="completed" if terminal_success else "failed",
                    phase="completed" if terminal_success else "failed", message=terminal_message,
                    current=len(items), total=len(items), log=terminal_message,
                )
                return {
                    "success": not resolve_failures,
                    "message": terminal_message,
                    "failures": [f"{item.display_name}: {error}" for item, error in resolve_failures],
                }

            candidates.sort(key=lambda pair: {"metamod": 0, "counterstrikesharp": 1}.get(pair[0].framework_key, 2))
            restart_candidates = [item for item, _ in candidates if item.source_type == "framework" and item.restart_after_update]
            status_check_ok = True
            was_running = False
            status_check_message = "Restart not requested"
            if restart_candidates:
                await self._publish_status(
                    server_id, phase="checking_server", message="Checking server state for framework restart policy",
                    current=0, total=len(candidates), log="Checking whether the server is currently running",
                )
                status_check_ok, server_state = await SSHManager().get_server_status(server)
                was_running = status_check_ok and server_state == "running"
                status_check_message = server_state if status_check_ok else "Could not determine server state"

            targets = "\n".join(
                f"{item.display_name}: {item.installed_version} → {latest['version']}" for item, latest in candidates
            )
            discord_notification_service.queue_notify(
                server, EVENT_PLUGIN_UPDATE, "plugin_auto_update", True,
                "Plugin auto-update is starting. Framework restart settings will be applied after the batch.",
                title="Plugin automatic update started",
                details={
                    "Updates": targets,
                    "Backup Before Update": ", ".join(item.display_name for item, _ in candidates if item.backup_before_update) or "Not requested",
                    "Auto Restart": ", ".join(item.display_name for item in restart_candidates) or "Not requested",
                }, state="in_progress",
            )

            backup_items = [(item, latest) for item, latest in candidates if item.backup_before_update]
            backup_success = True
            backup_message = "Not requested"
            backup_blocked_ids = set()
            if backup_items:
                await self._publish_status(
                    server_id, phase="backup", message="Creating local backup for selected plugins",
                    current=0, total=len(candidates),
                    log="Backup requested by: " + ", ".join(item.display_name for item, _ in backup_items),
                )
                backup_success, backup_message = await SSHManager().backup_plugins(server)
            if backup_items and not backup_success:
                message = f"Plugin backup failed; plugins requiring backup were skipped: {backup_message}"
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
                    server_id, phase="backup_failed", message=message,
                    current=0, total=len(candidates), log=message,
                )

            results: List[Dict[str, Any]] = []
            if backup_items and backup_success:
                update_start_message = "Selected-plugin backup completed; starting plugin updates"
                update_start_log = f"Backup completed: {backup_message}"
            elif backup_items:
                update_start_message = "Continuing plugins that do not require backup"
                update_start_log = "Backup-required plugins were skipped; continuing unprotected plugins"
            else:
                update_start_message = "No plugin backups requested; starting plugin updates"
                update_start_log = "Backup skipped because no selected plugin enabled it"
            await self._publish_status(
                server_id, phase="updating", message=update_start_message, log=update_start_log
            )
            for update_index, (item, latest) in enumerate(candidates, start=1):
                if item.id in backup_blocked_ids:
                    message = f"Skipped because the requested backup failed: {backup_message}"
                    results.append({
                        "name": item.display_name, "success": False, "message": message,
                        "version": latest["version"], "restart_after_update": item.restart_after_update,
                        "source_type": item.source_type,
                    })
                    await self._publish_status(
                        server_id, current=update_index, total=len(candidates),
                        log=f"Skipped {item.display_name}: requested backup failed",
                    )
                    continue
                await self._publish_status(
                    server_id, phase="updating", message=f"Updating {item.display_name}",
                    current=update_index - 1, total=len(candidates), log=f"Updating {item.display_name} to {latest['version']}",
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
                            saved.last_update_at = get_current_time()
                        db.add(saved)
                        await db.commit()
                results.append({
                    "name": item.display_name, "success": success, "message": message,
                    "version": latest["version"], "restart_after_update": item.restart_after_update,
                    "source_type": item.source_type,
                })
                await self._publish_status(
                    server_id, current=update_index,
                    log=f"{'Completed' if success else 'Failed'} {item.display_name}: {message}",
                )

            successful_restart_items = [
                result for result in results
                if result["success"] and result["source_type"] == "framework" and result["restart_after_update"]
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
                        server_id, phase="restarting", message="Restarting server after framework update",
                        current=len(candidates), total=len(candidates), log="Framework restart policy triggered one server restart",
                    )
                    stop_success, stop_message = await SSHManager().stop_server(server)
                    await self._publish_status(server_id, log=f"Stop result: {stop_message}")
                    await asyncio.sleep(0.5)
                    start_success, start_message = await SSHManager().start_server(server)
                    restart_success = start_success
                    restart_message = start_message if start_success else f"Restart failed: {start_message}"
                    await self._publish_status(server_id, log=f"Start result: {restart_message}")

            all_success = all(result["success"] for result in results) and not resolve_failures and restart_success
            summary_lines = [
                f"{'✓' if result['success'] else '✗'} {result['name']}: {result['version']}"
                + ("" if result["success"] else f" — {result['message']}")
                for result in results
            ]
            summary_lines.extend(f"✗ {item.display_name}: {error}" for item, error in resolve_failures)
            discord_notification_service.queue_notify(
                server, EVENT_PLUGIN_UPDATE, "plugin_auto_update", all_success,
                "Plugin update batch completed and the configured framework restart policy was applied."
                if all_success else "One or more plugin updates or the configured framework restart failed.",
                title="Plugin automatic update completed" if all_success else "Plugin automatic update failed",
                details={"Results": "\n".join(summary_lines), "Backup": backup_message, "Restart": restart_message},
            )
            terminal_message = "Plugin update batch completed" if all_success else "Plugin update batch completed with failures"
            await self._publish_status(
                server_id, state="completed" if all_success else "failed",
                phase="completed" if all_success else "failed", message=terminal_message,
                current=len(candidates), total=len(candidates), log=f"{terminal_message}. Restart: {restart_message}",
            )
            return {
                "success": all_success, "message": terminal_message, "results": results,
                "restart": {"success": restart_success, "message": restart_message, "previous_state": status_check_message},
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
        server_id=server.id, source_type="github", source_key=canonical.lower(),
        display_name=display_name, repo_url=canonical, asset_glob=asset_glob,
    )
    ok, latest, _ = await plugin_auto_update_service._latest_github_release(probe, user)
    await upsert_managed_plugin(
        server_id=server.id, source_type="github", source_key=canonical.lower(),
        display_name=display_name, repo_url=canonical,
        installed_release_id=latest["release_id"] if ok and latest else None,
        installed_version=latest["version"] if ok and latest else "unknown",
        asset_glob=asset_glob,
    )
