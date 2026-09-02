"""
S3-compatible storage service for plugin backups.
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

from anyio import to_thread

from modules.models import Server, User

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


class S3BackupService:
    """Upload, list, and download plugin backup archives from S3-compatible storage."""

    def is_configured(self, user: User) -> bool:
        return bool(
            user
            and user.s3_enabled
            and user.s3_bucket
            and user.s3_access_key_id
            and user.s3_secret_access_key
        )

    def get_server_prefix(self, user: User, server: Server) -> str:
        base_prefix = (user.s3_prefix or "").strip().strip("/")
        owner_part = f"user-{user.id}/server-{server.id}"
        return f"{base_prefix}/{owner_part}" if base_prefix else owner_part

    def build_backup_key(self, user: User, server: Server, filename: str) -> str:
        safe_filename = os.path.basename(filename).replace("\\", "")
        return f"{self.get_server_prefix(user, server)}/{safe_filename}"

    def validate_object_key(self, user: User, server: Server, object_key: str) -> bool:
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

    def get_retention_count(self, user: User) -> int:
        count: object = getattr(user, "s3_retention_count", None)
        if not isinstance(count, (int, float, str)):
            return DEFAULT_S3_RETENTION_COUNT
        try:
            count = int(count)
        except TypeError, ValueError:
            return DEFAULT_S3_RETENTION_COUNT
        if count <= 0:
            return DEFAULT_S3_RETENTION_COUNT
        return min(count, MAX_S3_RETENTION_COUNT)

    def _get_region_name(self, user: User) -> Optional[str]:
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

    def _list_backup_objects(self, client, user: User, server: Server) -> List[Dict[str, Any]]:
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

    def _get_client(self, user: User):
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

    def build_test_key(self, user: User) -> str:
        base_prefix = (user.s3_prefix or "").strip().strip("/")
        test_part = f".upkk-s3-test/user-{user.id}/{uuid.uuid4().hex}.txt"
        return f"{base_prefix}/{test_part}" if base_prefix else test_part

    async def test_connection(self, user: User) -> Tuple[bool, str, List[Dict[str, str]]]:
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
            client = self._get_client(user)
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
                    raise RuntimeError("Downloaded probe object did not match uploaded content.")
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
                    f"{failure_message}\n{cleanup_message}" if failure_message else cleanup_message
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
        user: User,
        backup_path: str,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[str]]:
        if not self.is_configured(user):
            return True, "S3 upload skipped because S3-compatible storage is not configured.", None

        temp_dir = tempfile.mkdtemp(prefix="cs2_s3_backup_")
        local_path = os.path.join(temp_dir, os.path.basename(backup_path))

        async def send_progress(message: str):
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
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
            client = self._get_client(user)

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
        self, user: User, server: Server
    ) -> Tuple[bool, List[Dict[str, Any]], str]:
        if not self.is_configured(user):
            return True, [], "S3-compatible storage is not configured."

        try:
            client = self._get_client(user)

            def _list():
                return self._list_backup_objects(client, user, server)

            return True, await asyncio.to_thread(_list), ""
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            return False, [], f"Failed to list S3 backups: {exc}"

    async def enforce_retention(
        self,
        user: User,
        server: Server,
        client=None,
        progress_callback=None,
    ) -> Tuple[bool, str, int]:
        retention_count = self.get_retention_count(user)

        if not self.is_configured(user):
            return True, "", 0

        async def send_progress(message: str):
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(message)
                else:
                    progress_callback(message)

        try:
            s3_client = client or self._get_client(user)

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
        user: User,
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
            client = self._get_client(user)
            await asyncio.to_thread(client.download_file, user.s3_bucket, object_key, local_path)
            return True, ""
        except (BotoCoreError, ClientError, RuntimeError) as exc:
            return False, f"Failed to download S3 backup: {exc}"


s3_backup_service = S3BackupService()
