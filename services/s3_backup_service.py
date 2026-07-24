"""
S3-compatible storage service for plugin backups.
"""

import asyncio
import hmac
import inspect
import logging
import os
import secrets
import shutil
import tempfile
import threading
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple

from anyio import to_thread

from modules.models import Server

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
except Exception:  # pragma: no cover - handled at runtime with a clear message
    boto3 = None
    Config = None
    BotoCoreError = Exception
    ClientError = Exception


DEFAULT_S3_RETENTION_COUNT = 10
MAX_S3_RETENTION_COUNT = 10000
DEFAULT_S3_CLIENT_CACHE_SIZE = 32
S3_BACKUP_SERVICE_KEY = "s3_backup"


class S3UserConfig(Protocol):
    """Structural view of the credentials required by S3 operations."""

    @property
    def id(self) -> int | None: ...

    @property
    def s3_enabled(self) -> bool: ...

    @property
    def s3_endpoint_url(self) -> str | None: ...

    @property
    def s3_region(self) -> str | None: ...

    @property
    def s3_bucket(self) -> str | None: ...

    @property
    def s3_access_key_id(self) -> str | None: ...

    @property
    def s3_secret_access_key(self) -> str | None: ...

    @property
    def s3_prefix(self) -> str | None: ...

    @property
    def s3_use_ssl(self) -> bool: ...

    @property
    def s3_retention_count(self) -> int | None: ...


@dataclass(slots=True)
class _CachedS3Client:
    client: Any
    active_leases: int = 0
    retired: bool = False
    close_started: bool = False


class S3BackupService:
    """Upload, list, and download plugin backup archives from S3-compatible storage."""

    def __init__(self, max_cached_clients: int = DEFAULT_S3_CLIENT_CACHE_SIZE) -> None:
        if max_cached_clients < 1:
            raise ValueError("max_cached_clients must be at least 1")
        self._max_cached_clients = max_cached_clients
        self._client_cache: OrderedDict[tuple[int, bytes], _CachedS3Client] = OrderedDict()
        self._client_cache_lock = threading.RLock()
        # A process-local HMAC key keeps credential material out of cache keys
        # and prevents a digest from becoming an offline secret oracle.
        self._cache_digest_key = secrets.token_bytes(32)

    def is_configured(self, user: S3UserConfig | None) -> bool:
        return bool(
            user
            and user.s3_enabled
            and user.s3_bucket
            and user.s3_access_key_id
            and user.s3_secret_access_key
        )

    def get_server_prefix(self, user: S3UserConfig, server: Server) -> str:
        base_prefix = (user.s3_prefix or "").strip().strip("/")
        owner_part = f"user-{user.id}/server-{server.id}"
        return f"{base_prefix}/{owner_part}" if base_prefix else owner_part

    def build_backup_key(self, user: S3UserConfig, server: Server, filename: str) -> str:
        safe_filename = os.path.basename(filename).replace("\\", "")
        return f"{self.get_server_prefix(user, server)}/{safe_filename}"

    def validate_object_key(
        self,
        user: S3UserConfig,
        server: Server,
        object_key: str,
    ) -> bool:
        prefix = self.get_server_prefix(user, server)
        return object_key.startswith(f"{prefix}/") and len(object_key) > len(prefix) + 1

    def safe_object_filename(self, object_key: str) -> str:
        filename = object_key.rsplit("/", 1)[-1].strip()
        if not filename:
            filename = "backup.tar.gz"
        safe_chars = []
        for char in filename:
            if char.isalnum() or char in ("-", "_", ".", "+"):
                safe_chars.append(char)
            else:
                safe_chars.append("_")
        return "".join(safe_chars)[:255] or "backup.tar.gz"

    def get_retention_count(self, user: S3UserConfig) -> int:
        raw_count = user.s3_retention_count
        if raw_count is None:
            return DEFAULT_S3_RETENTION_COUNT
        try:
            count = int(raw_count)
        except TypeError, ValueError:
            return DEFAULT_S3_RETENTION_COUNT
        if count <= 0:
            return DEFAULT_S3_RETENTION_COUNT
        return min(count, MAX_S3_RETENTION_COUNT)

    def _get_region_name(self, user: S3UserConfig) -> Optional[str]:
        region = (user.s3_region or "").strip()
        if region:
            return region

        endpoint_url = (user.s3_endpoint_url or "").strip().lower()
        if "r2.cloudflarestorage.com" in endpoint_url:
            return "auto"

        return None

    def _sort_backups_newest_first(self, items: List[Dict[str, Any]]) -> None:
        def sort_key(item: Dict[str, Any]):
            last_modified = item.get("last_modified")
            modified_ts = last_modified.timestamp() if last_modified else 0
            return modified_ts, item.get("key") or ""

        items.sort(key=sort_key, reverse=True)

    def _list_backup_objects(
        self,
        client,
        user: S3UserConfig,
        server: Server,
    ) -> List[Dict[str, Any]]:
        prefix = f"{self.get_server_prefix(user, server)}/"
        paginator = client.get_paginator("list_objects_v2")
        items: List[Dict[str, Any]] = []
        for page in paginator.paginate(Bucket=user.s3_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj.get("Key")
                if not key or key.endswith("/"):
                    continue
                items.append(
                    {
                        "key": key,
                        "filename": key.rsplit("/", 1)[-1],
                        "size": int(obj.get("Size") or 0),
                        "last_modified": obj.get("LastModified"),
                        "etag": (obj.get("ETag") or "").strip('"') or None,
                    }
                )
        self._sort_backups_newest_first(items)
        return items

    def _configuration_digest(self, user: S3UserConfig) -> bytes:
        digest = hmac.new(self._cache_digest_key, digestmod="sha256")
        values = (
            user.s3_enabled,
            user.s3_endpoint_url,
            user.s3_region,
            user.s3_bucket,
            user.s3_access_key_id,
            user.s3_secret_access_key,
            user.s3_prefix,
            user.s3_use_ssl,
            user.s3_retention_count,
        )
        for value in values:
            encoded = repr(value).encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
        return digest.digest()

    def _get_client(self, user: S3UserConfig):
        """Construct a client for a cache miss.

        The method name is retained for compatibility with existing adapters;
        service operations acquire it through ``_client_lease`` so the
        resulting client is reused and closed by this service.
        """
        if boto3 is None:
            raise RuntimeError(
                "boto3 is not installed. Please install dependencies from requirements.txt."
            )

        client_kwargs: Dict[str, Any] = {
            "aws_access_key_id": user.s3_access_key_id,
            "aws_secret_access_key": user.s3_secret_access_key,
            "use_ssl": bool(user.s3_use_ssl),
        }

        region_name = self._get_region_name(user)
        if region_name:
            client_kwargs["region_name"] = region_name
        if user.s3_endpoint_url:
            client_kwargs["endpoint_url"] = user.s3_endpoint_url

        if Config is not None:
            config_kwargs: Dict[str, Any] = {
                "signature_version": "s3v4",
                "retries": {"max_attempts": 3, "mode": "standard"},
            }
            if user.s3_endpoint_url:
                config_kwargs["s3"] = {"addressing_style": "path"}
            client_kwargs["config"] = Config(**config_kwargs)

        return boto3.client("s3", **client_kwargs)

    def _retire_entry_locked(self, entry: _CachedS3Client) -> Any | None:
        entry.retired = True
        if entry.active_leases == 0 and not entry.close_started:
            entry.close_started = True
            return entry.client
        return None

    def _acquire_cached_client(
        self,
        user: S3UserConfig,
    ) -> tuple[_CachedS3Client, list[Any]]:
        user_id = int(user.id) if user.id is not None else None
        if user_id is None:
            return _CachedS3Client(
                client=self._get_client(user),
                active_leases=1,
                retired=True,
            ), []

        key = (user_id, self._configuration_digest(user))
        clients_to_close: list[Any] = []
        with self._client_cache_lock:
            entry = self._client_cache.get(key)
            if entry is None:
                # A newly observed configuration supersedes older cached
                # versions for this user. Active leases finish safely before
                # their retired client is closed.
                stale_keys = [
                    cached_key
                    for cached_key in self._client_cache
                    if cached_key[0] == user_id and cached_key != key
                ]
                for stale_key in stale_keys:
                    stale_entry = self._client_cache.pop(stale_key)
                    client = self._retire_entry_locked(stale_entry)
                    if client is not None:
                        clients_to_close.append(client)

                entry = _CachedS3Client(client=self._get_client(user))
                self._client_cache[key] = entry
            else:
                self._client_cache.move_to_end(key)

            entry.active_leases += 1
            while len(self._client_cache) > self._max_cached_clients:
                _old_key, old_entry = self._client_cache.popitem(last=False)
                client = self._retire_entry_locked(old_entry)
                if client is not None:
                    clients_to_close.append(client)

        return entry, clients_to_close

    def _release_cached_client(self, entry: _CachedS3Client) -> Any | None:
        with self._client_cache_lock:
            if entry.active_leases <= 0:
                return None
            entry.active_leases -= 1
            if entry.active_leases == 0 and entry.retired and not entry.close_started:
                entry.close_started = True
                return entry.client
        return None

    async def _close_clients(self, clients: List[Any]) -> None:
        seen: set[int] = set()
        for client in clients:
            if id(client) in seen:
                continue
            seen.add(id(client))
            close = getattr(client, "close", None)
            if close is None:
                continue
            try:
                result = await asyncio.to_thread(close)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                # Client close errors must not expose endpoint or credential
                # values through logs during eviction or shutdown.
                logger.warning("Failed to close cached S3 client (%s)", type(exc).__name__)

    @asynccontextmanager
    async def _client_lease(self, user: S3UserConfig) -> AsyncIterator[Any]:
        entry, clients_to_close = self._acquire_cached_client(user)
        await self._close_clients(clients_to_close)
        try:
            yield entry.client
        finally:
            client_to_close = self._release_cached_client(entry)
            if client_to_close is not None:
                await self._close_clients([client_to_close])

    async def invalidate_user(self, user_id: Optional[int]) -> int:
        """Evict every cached client for a user after S3 configuration changes."""
        if user_id is None:
            return 0

        clients_to_close: list[Any] = []
        with self._client_cache_lock:
            keys = [key for key in self._client_cache if key[0] == int(user_id)]
            for key in keys:
                entry = self._client_cache.pop(key)
                client = self._retire_entry_locked(entry)
                if client is not None:
                    clients_to_close.append(client)
        await self._close_clients(clients_to_close)
        return len(keys)

    async def close(self) -> None:
        """Close all idle clients and retire active leases during shutdown."""
        clients_to_close: list[Any] = []
        with self._client_cache_lock:
            entries = list(self._client_cache.values())
            self._client_cache.clear()
            for entry in entries:
                client = self._retire_entry_locked(entry)
                if client is not None:
                    clients_to_close.append(client)
        await self._close_clients(clients_to_close)

    @property
    def cached_client_count(self) -> int:
        with self._client_cache_lock:
            return len(self._client_cache)

    def build_test_key(self, user: S3UserConfig) -> str:
        base_prefix = (user.s3_prefix or "").strip().strip("/")
        test_part = f".upkk-s3-test/user-{user.id}/{uuid.uuid4().hex}.txt"
        return f"{base_prefix}/{test_part}" if base_prefix else test_part

    async def test_connection(
        self,
        user: S3UserConfig,
    ) -> Tuple[bool, str, List[Dict[str, str]]]:
        steps: List[Dict[str, str]] = []

        def add_step(name: str, status: str, message: str):
            steps.append(
                {
                    "name": name,
                    "status": status,
                    "message": message,
                }
            )

        if not self.is_configured(user):
            add_step("configuration", "failed", "S3-compatible storage is not fully configured.")
            return False, "S3-compatible storage is not fully configured.", steps

        uploaded = False
        test_key = self.build_test_key(user)
        payload = f"UpKK S3 connectivity test {uuid.uuid4().hex}".encode("utf-8")
        try:
            async with self._client_lease(user) as client:
                prefix = (user.s3_prefix or "").strip().strip("/")

                try:
                    await asyncio.to_thread(
                        client.list_objects_v2,
                        Bucket=user.s3_bucket,
                        Prefix=prefix,
                        MaxKeys=1,
                    )
                    add_step("list", "success", "Bucket list/read permission is available.")
                except (BotoCoreError, ClientError, RuntimeError) as exc:
                    add_step("list", "failed", str(exc))
                    return False, f"S3 list/read test failed: {exc}", steps

                try:
                    await asyncio.to_thread(
                        client.put_object,
                        Bucket=user.s3_bucket,
                        Key=test_key,
                        Body=payload,
                        ContentType="text/plain",
                    )
                    uploaded = True
                    add_step("upload", "success", f"Probe object uploaded: {test_key}")
                except (BotoCoreError, ClientError, RuntimeError) as exc:
                    add_step("upload", "failed", str(exc))
                    return False, f"S3 upload test failed: {exc}", steps

                failure_message = None
                try:
                    response = await asyncio.to_thread(
                        client.get_object,
                        Bucket=user.s3_bucket,
                        Key=test_key,
                    )
                    downloaded = await asyncio.to_thread(response["Body"].read)
                    if downloaded != payload:
                        raise RuntimeError(
                            "Downloaded probe object did not match uploaded content."
                        )
                    add_step("download", "success", "Probe object downloaded and verified.")
                except (BotoCoreError, ClientError, RuntimeError) as exc:
                    add_step("download", "failed", str(exc))
                    failure_message = f"S3 download test failed: {exc}"

                try:
                    if uploaded:
                        await asyncio.to_thread(
                            client.delete_object,
                            Bucket=user.s3_bucket,
                            Key=test_key,
                        )
                        add_step("delete", "success", "Probe object deleted successfully.")
                except (BotoCoreError, ClientError, RuntimeError) as exc:
                    add_step("delete", "failed", str(exc))
                    cleanup_message = f"S3 delete test failed: {exc}"
                    failure_message = (
                        f"{failure_message}\n{cleanup_message}"
                        if failure_message
                        else cleanup_message
                    )

                if failure_message:
                    return False, failure_message, steps
                return (
                    True,
                    "S3-compatible storage test succeeded: list, upload, download, and delete all passed.",
                    steps,
                )
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            add_step("connection", "failed", str(exc))
            return False, f"S3 connection test failed: {exc}", steps

    async def upload_remote_backup(
        self,
        ssh_manager,
        server: Server,
        user: S3UserConfig,
        backup_path: str,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[str]]:
        if not self.is_configured(user):
            return True, "S3 upload skipped because S3-compatible storage is not configured.", None

        temp_dir = tempfile.mkdtemp(prefix="cs2_s3_backup_")
        local_path = os.path.join(temp_dir, os.path.basename(backup_path))

        async def send_progress(message: str):
            if progress_callback:
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        try:
            await send_progress("Downloading backup archive to panel for S3 upload...")
            download_success, download_error = await ssh_manager.download_file(
                backup_path, local_path, server
            )
            if not download_success:
                return False, f"Failed to download backup before S3 upload: {download_error}", None

            key = self.build_backup_key(user, server, os.path.basename(local_path))
            async with self._client_lease(user) as client:
                await send_progress(f"Uploading backup archive to S3: {key}")
                await asyncio.to_thread(client.upload_file, local_path, user.s3_bucket, key)

                message = f"S3 upload completed: {key}"
                retention_success, retention_message, _ = await self.enforce_retention(
                    user,
                    server,
                    client=client,
                    progress_callback=send_progress,
                )
            if retention_message:
                message = f"{message}\n{retention_message}"
            if not retention_success:
                message = f"{message}\nS3 upload was kept, but retention cleanup needs attention."

            return True, message, key
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            return False, f"S3 upload failed: {exc}", None
        finally:
            try:
                await ssh_manager.disconnect()
            except Exception:
                pass
            await to_thread.run_sync(lambda: shutil.rmtree(temp_dir, ignore_errors=True))

    async def list_backups(
        self, user: S3UserConfig, server: Server
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        if not self.is_configured(user):
            return True, [], "S3-compatible storage is not configured."

        try:
            async with self._client_lease(user) as client:

                def _list():
                    return self._list_backup_objects(client, user, server)

                return True, await asyncio.to_thread(_list), ""
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            return False, [], f"Failed to list S3 backups: {exc}"

    async def enforce_retention(
        self,
        user: S3UserConfig,
        server: Server,
        client=None,
        progress_callback=None,
    ) -> Tuple[bool, str, int]:
        retention_count = self.get_retention_count(user)

        if not self.is_configured(user):
            return True, "", 0

        async def send_progress(message: str):
            if progress_callback:
                if inspect.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        if client is None:
            try:
                async with self._client_lease(user) as cached_client:
                    return await self.enforce_retention(
                        user,
                        server,
                        client=cached_client,
                        progress_callback=progress_callback,
                    )
            except (BotoCoreError, ClientError, RuntimeError) as exc:
                return False, f"S3 retention cleanup failed: {exc}", 0

        try:
            s3_client = client

            def _cleanup():
                items = self._list_backup_objects(s3_client, user, server)
                old_items = items[retention_count:]
                deleted_count = 0
                for index in range(0, len(old_items), 1000):
                    batch = old_items[index : index + 1000]
                    if not batch:
                        continue
                    response = s3_client.delete_objects(
                        Bucket=user.s3_bucket,
                        Delete={"Objects": [{"Key": item["key"]} for item in batch]},
                    )
                    errors = response.get("Errors") or []
                    if errors:
                        first_error = errors[0]
                        raise RuntimeError(
                            f"failed to delete {len(errors)} backup object(s); "
                            f"first error: {first_error.get('Key')}: {first_error.get('Message')}"
                        )
                    deleted_count += len(batch)
                return deleted_count

            deleted_count = await asyncio.to_thread(_cleanup)
            if deleted_count:
                message = (
                    f"S3 retention cleanup kept the newest {retention_count} backup(s) "
                    f"and deleted {deleted_count} older backup(s)."
                )
                await send_progress(message)
                return True, message, deleted_count
            return True, "", 0
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            return False, f"S3 retention cleanup failed: {exc}", 0

    async def download_backup(
        self,
        user: S3UserConfig,
        server: Server,
        object_key: str,
        local_path: str,
    ) -> Tuple[bool, str]:
        if not self.is_configured(user):
            return False, "S3-compatible storage is not configured."
        if not self.validate_object_key(user, server, object_key):
            return False, "S3 backup object is outside this server's backup prefix."

        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            async with self._client_lease(user) as client:
                await asyncio.to_thread(
                    client.download_file, user.s3_bucket, object_key, local_path
                )
            return True, ""
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            return False, f"Failed to download S3 backup: {exc}"


s3_backup_service = S3BackupService()
