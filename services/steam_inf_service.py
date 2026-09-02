"""
Steam.inf Version Cache Service
Reads PatchVersion from cs2/game/csgo/steam.inf file instead of relying on A2S protocol
Provides more stable version information for auto-update triggers
"""

import asyncio
import logging
import re
import shlex
from collections.abc import Callable
from typing import Any, Optional, Protocol, Tuple

from sqlmodel import col

from modules.models import Server
from services.redis_manager import redis_manager

logger = logging.getLogger(__name__)

_PATCH_VERSION_RE = re.compile(r"PatchVersion=(\d+\.\d+\.\d+\.\d+)")
_SERVER_VERSION_RE = re.compile(r"ServerVersion=(\d+)")
_CLIENT_VERSION_RE = re.compile(r"ClientVersion=(\d+)")


def _optional_text(value: object) -> Optional[str]:
    """Normalize Redis/JSON values so numeric build ids stay strings."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    return text or None


def parse_steam_inf_fields(output: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract PatchVersion and ServerVersion/ClientVersion from steam.inf text."""
    patch = _PATCH_VERSION_RE.search(output or "")
    server_build = _SERVER_VERSION_RE.search(output or "")
    client_build = _CLIENT_VERSION_RE.search(output or "")
    version = patch.group(1) if patch else None
    build = None
    if server_build:
        build = server_build.group(1)
    elif client_build:
        build = client_build.group(1)
    return version, build


class SSHSession(Protocol):
    async def connect(self, server: Server) -> tuple[bool, str]: ...

    async def execute_command(self, command: str) -> tuple[bool, str, str]: ...

    async def disconnect(self) -> Any: ...


SSHManagerFactory = Callable[[], SSHSession]
_ssh_manager_factory: SSHManagerFactory | None = None


def configure_ssh_manager_factory(factory: SSHManagerFactory) -> None:
    """Inject the SSH facade without importing it back from this service."""
    global _ssh_manager_factory
    _ssh_manager_factory = factory


class SteamInfService:
    """Service to read and cache CS2 version from steam.inf file"""

    # Cache TTL: 365 days (long-term cache, refreshed on operations and periodically)
    CACHE_TTL_SECONDS = 365 * 24 * 60 * 60

    # Periodic refresh interval: 24 hours
    REFRESH_INTERVAL_SECONDS = 24 * 60 * 60

    def __init__(self):
        # Cache is long-term - refreshed on server operations or periodic refresh
        self.refresh_interval = self.REFRESH_INTERVAL_SECONDS
        self.refresh_task: Optional[asyncio.Task] = None
        self.running = False

    async def start(self):
        """Start periodic refresh task"""
        if self.refresh_task is None or self.refresh_task.done():
            self.running = True
            self.refresh_task = asyncio.create_task(self._refresh_loop())
            logger.info("Steam.inf periodic refresh started (every 24 hours)")

    async def stop(self):
        """Stop periodic refresh task"""
        self.running = False
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
        if self.refresh_task:
            await asyncio.gather(self.refresh_task, return_exceptions=True)
        self.refresh_task = None
        logger.info("Steam.inf periodic refresh stopped")

    async def _refresh_loop(self):
        """Periodic refresh loop"""
        while self.running:
            try:
                await self._periodic_refresh_all()
            except Exception as e:
                logger.error(f"Error in steam.inf periodic refresh: {e}")

            # Wait for next interval
            await asyncio.sleep(self.refresh_interval)

    async def _periodic_refresh_all(self):
        """Periodically refresh all servers' version cache"""
        from sqlmodel import select

        from modules.database import async_session_maker

        try:
            # Fetch server list quickly and close DB connection to avoid pool exhaustion
            async with async_session_maker() as db:
                result = await db.execute(select(Server))
                servers = result.scalars().all()

            logger.info(f"Periodic refresh: Updating steam.inf version for {len(servers)} servers")

            # Refresh each server's version with timeout protection
            # DB session is already closed, so SSH operations won't hold DB connections
            for server in servers:
                try:
                    # Skip servers that are marked as down due to SSH failures
                    if server.should_skip_background_checks():
                        logger.info(
                            f"Skipping steam.inf refresh for server {server.id} - marked as SSH down for 3+ days"
                        )
                        continue

                    # Wrap each server refresh in a timeout to prevent one slow server from blocking all others
                    # Use 35 seconds timeout (slightly more than the _read_version_from_file timeout)
                    async def _refresh_server(server=server):
                        success, version = await self.get_version_from_steam_inf(
                            server, force_refresh=True
                        )
                        if success:
                            logger.debug(f"Refreshed version for server {server.id}: {version}")

                    await asyncio.wait_for(_refresh_server(), timeout=35)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Timeout refreshing version for server {server.id} - skipping to prevent blocking"
                    )
                except Exception as e:
                    logger.error(f"Error refreshing version for server {server.id}: {e}")

        except Exception as e:
            logger.error(f"Error in periodic refresh: {e}")

    async def get_version_from_steam_inf(
        self, server: Server, force_refresh: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Get CS2 version from steam.inf file

        Args:
            server: Server instance
            force_refresh: If True, bypass cache and read from file

        Returns:
            Tuple[bool, Optional[str]]: (success, version_string)
            version_string format: "1.41.2.6" or None if failed
        """
        success, version, _build_id = await self.get_steam_inf_details(
            server, force_refresh=force_refresh
        )
        return success, version

    async def get_steam_inf_details(
        self, server: Server, force_refresh: bool = False, *, timeout: float = 30
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Return PatchVersion and optional Steam build id from steam.inf."""
        cache_key = f"steam_inf:version:{server.id}"
        build_key = f"steam_inf:build:{server.id}"

        if not force_refresh:
            cached_version = _optional_text(await redis_manager.get(cache_key))
            if cached_version:
                cached_build = _optional_text(await redis_manager.get(build_key))
                logger.debug(
                    f"Using cached steam.inf version for server {server.id}: {cached_version}"
                )
                return True, cached_version, cached_build
            logger.info(f"Cache missing for server {server.id}, proactively refreshing...")
            force_refresh = True

        if force_refresh:
            success, version, build_id = await self._read_version_from_file(server, timeout=timeout)
            if success and version:
                await redis_manager.set(cache_key, version, expire=self.CACHE_TTL_SECONDS)
                if build_id:
                    await redis_manager.set(build_key, build_id, expire=self.CACHE_TTL_SECONDS)
                else:
                    await redis_manager.delete(build_key)
                logger.info(
                    f"Cached steam.inf version for server {server.id}: {version} (unlimited TTL, periodic refresh enabled)"
                )
                return True, version, build_id

        return False, None, None

    async def _read_version_from_file(
        self, server: Server, *, timeout: float = 30
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Read PatchVersion from steam.inf file via SSH

        Args:
            server: Server instance

        Returns:
            Tuple[bool, Optional[str], Optional[str]]: (success, version, build_id)
        """
        if _ssh_manager_factory is None:
            logger.error("Steam.inf SSH manager factory is not configured")
            return False, None, None
        ssh_manager = _ssh_manager_factory()

        try:
            # Wrap the entire operation in a timeout to prevent blocking
            # Use 30 seconds timeout to avoid long waits on connection issues
            async def _do_read():
                # Connect to server
                success, msg = await ssh_manager.connect(server)
                if not success:
                    logger.warning(
                        f"Failed to connect to server {server.id} for steam.inf read: {msg}"
                    )
                    return False, None, None

                # Path to steam.inf file
                steam_inf_path = f"{server.game_directory}/cs2/game/csgo/steam.inf"

                # Properly escape the path for shell command
                escaped_path = shlex.quote(steam_inf_path)

                # Check if file exists
                check_cmd = f"test -f {escaped_path} && echo 'exists' || echo 'missing'"
                success, stdout, stderr = await ssh_manager.execute_command(check_cmd)

                if not success or "missing" in stdout:
                    logger.warning(
                        f"steam.inf file not found for server {server.id} at {steam_inf_path}"
                    )
                    return False, None, None

                read_cmd = f"grep -E '^(PatchVersion|ServerVersion|ClientVersion)=' {escaped_path}"
                success, stdout, stderr = await ssh_manager.execute_command(read_cmd)

                if not success or not stdout:
                    logger.warning(
                        f"Failed to read PatchVersion from steam.inf for server {server.id}"
                    )
                    return False, None, None

                version, build_id = parse_steam_inf_fields(stdout)

                if version:
                    logger.info(
                        f"Read version from steam.inf for server {server.id}: {version}"
                        + (f" (build {build_id})" if build_id else "")
                    )
                    return True, version, build_id
                logger.warning(
                    f"Could not parse PatchVersion from steam.inf for server {server.id}: {stdout}"
                )
                return False, None, None

            # Apply timeout to prevent blocking the event loop
            return await asyncio.wait_for(_do_read(), timeout=timeout)

        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout reading steam.inf for server {server.id} after {timeout} seconds"
            )
            return False, None, None
        except Exception as e:
            logger.error(f"Error reading steam.inf for server {server.id}: {e}")
            return False, None, None
        finally:
            await ssh_manager.disconnect()

    def _parse_patch_version(self, output: str) -> Optional[str]:
        """Parse PatchVersion from grep output."""
        version, _build_id = parse_steam_inf_fields(output)
        return version

    async def refresh_version_cache(self, server: Server) -> Tuple[bool, Optional[str]]:
        """
        Force refresh version cache by reading from file
        Should be called after server start/restart/update/verify operations
        Also updates the database current_game_version field

        Args:
            server: Server instance

        Returns:
            Tuple[bool, Optional[str]]: (success, version_string)
        """
        logger.info(f"Refreshing steam.inf version cache for server {server.id}")
        success, version = await self.get_version_from_steam_inf(server, force_refresh=True)

        # Update database current_game_version if we successfully got the version
        if success and version:
            try:
                from sqlalchemy import update

                from modules.database import async_session_maker
                from modules.models import Server as ServerModel

                async with async_session_maker() as db:
                    # Only update if the version is different
                    await db.execute(
                        update(ServerModel)
                        .where(col(ServerModel.id) == server.id)
                        .values(current_game_version=version)
                    )
                    await db.commit()
                    logger.info(f"Updated server {server.id} database version to {version}")
            except Exception as e:
                logger.error(f"Failed to update server version in database: {e}")

        return success, version

    async def clear_version_cache(self, server_id: int):
        """
        Clear cached version for a server

        Args:
            server_id: Server ID
        """
        cache_key = f"steam_inf:version:{server_id}"
        build_key = f"steam_inf:build:{server_id}"
        await redis_manager.delete(cache_key)
        await redis_manager.delete(build_key)
        logger.debug(f"Cleared steam.inf version cache for server {server_id}")


# Global instance
steam_inf_service = SteamInfService()
