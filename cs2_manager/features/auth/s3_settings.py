"""S3 settings persistence and detached configuration snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from modules.models import User


class S3SettingsUserNotFoundError(LookupError):
    """The authenticated user disappeared before the S3 use case ran."""


@dataclass(frozen=True, slots=True)
class S3UserConfiguration:
    """Least-privilege S3 configuration safe to use after the DB phase."""

    id: int
    s3_enabled: bool
    s3_endpoint_url: str | None
    s3_region: str | None
    s3_bucket: str | None
    s3_access_key_id: str | None
    s3_secret_access_key: str | None
    s3_prefix: str | None
    s3_use_ssl: bool
    s3_retention_count: int | None

    @classmethod
    def from_user(cls, user: User) -> "S3UserConfiguration":
        if user.id is None:
            raise ValueError("Persisted S3 settings require a user id")
        return cls(
            id=user.id,
            s3_enabled=bool(user.s3_enabled),
            s3_endpoint_url=user.s3_endpoint_url,
            s3_region=user.s3_region,
            s3_bucket=user.s3_bucket,
            s3_access_key_id=user.s3_access_key_id,
            s3_secret_access_key=user.s3_secret_access_key,
            s3_prefix=user.s3_prefix,
            s3_use_ssl=bool(user.s3_use_ssl),
            s3_retention_count=user.s3_retention_count,
        )


@dataclass(frozen=True, slots=True)
class S3SettingsPatch:
    """Validated update values with the legacy no-change semantics preserved."""

    enabled: bool | None = None
    endpoint_url: str | None = None
    region: str | None = None
    bucket: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    prefix: str | None = None
    use_ssl: bool | None = None
    retention_count: int | None = None
    clear_secret: bool = False


class S3SettingsRepository:
    """Load and mutate user S3 settings without owning transaction commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_user(self, user_id: int) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise S3SettingsUserNotFoundError("User not found")
        return user

    async def get_configuration(self, user_id: int) -> S3UserConfiguration:
        return S3UserConfiguration.from_user(await self.require_user(user_id))

    def apply(
        self,
        user: User,
        patch: S3SettingsPatch,
    ) -> S3UserConfiguration:
        if patch.enabled is not None:
            user.s3_enabled = patch.enabled
        if patch.endpoint_url is not None:
            user.s3_endpoint_url = patch.endpoint_url or None
        if patch.region is not None:
            user.s3_region = patch.region or None
        if patch.bucket is not None:
            user.s3_bucket = patch.bucket or None
        if patch.access_key_id is not None:
            user.s3_access_key_id = patch.access_key_id or None
        if patch.prefix is not None:
            user.s3_prefix = patch.prefix or None
        if patch.use_ssl is not None:
            user.s3_use_ssl = patch.use_ssl
        if patch.retention_count is not None:
            user.s3_retention_count = patch.retention_count

        if patch.clear_secret:
            user.s3_secret_access_key = None
        elif patch.secret_access_key is not None and patch.secret_access_key.strip():
            user.s3_secret_access_key = patch.secret_access_key.strip()

        return S3UserConfiguration.from_user(user)
