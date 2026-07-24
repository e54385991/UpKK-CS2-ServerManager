"""Identity models."""

# ruff: noqa: F403,F405

from cs2_manager.infrastructure.credentials import hash_token, register_credential_shadows
from modules.config import settings

from .common import *


class User(SQLModel, table=True):
    """User model for authentication and authorization"""

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    username: str = Field(max_length=100, unique=True, nullable=False, index=True)
    email: str = Field(max_length=255, unique=True, nullable=False, index=True)
    hashed_password: str = Field(max_length=255, nullable=False)
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False)
    # ``api_key`` is retained only for the expand/migrate/contract rollout. New
    # keys are stored as keyed digests and can therefore only be shown once.
    api_key: Optional[str] = Field(default=None, max_length=64, unique=True, index=True)
    api_key_hash: Optional[str] = Field(
        default=None,
        sa_column=Column(CHAR(64), nullable=True, unique=True, index=True),
    )
    api_key_prefix: Optional[str] = Field(default=None, max_length=12)
    steam_api_key: Optional[str] = Field(default=None, max_length=64)
    steam_api_key_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True), exclude=True
    )
    github_token: Optional[str] = Field(default=None, max_length=255)
    github_token_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True), exclude=True
    )  # GitHub Fine-grained personal access token
    s3_enabled: bool = Field(default=False)
    s3_endpoint_url: Optional[str] = Field(default=None, max_length=500)
    s3_region: Optional[str] = Field(default=None, max_length=100)
    s3_bucket: Optional[str] = Field(default=None, max_length=255)
    s3_access_key_id: Optional[str] = Field(default=None, max_length=255)
    s3_access_key_id_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True), exclude=True
    )
    s3_secret_access_key: Optional[str] = Field(default=None, max_length=255)
    s3_secret_access_key_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True), exclude=True
    )
    s3_prefix: Optional[str] = Field(default=None, max_length=255)
    s3_use_ssl: bool = Field(default=True)
    s3_retention_count: Optional[int] = Field(default=10)
    google_id: Optional[str] = Field(
        default=None, max_length=255, unique=True, index=True
    )  # Google OAuth ID
    oauth_provider: Optional[str] = Field(
        default=None, max_length=50
    )  # OAuth provider (google, etc.)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', email='{self.email}')>"

    @property
    def has_api_key(self) -> bool:
        """Check if user has an API key configured"""
        return self.api_key_hash is not None or self.api_key is not None

    @property
    def has_steam_api_key(self) -> bool:
        """Check if user has a Steam API key configured"""
        return self.steam_api_key is not None

    @property
    def has_github_token(self) -> bool:
        """Check if user has a GitHub token configured"""
        return self.github_token is not None

    @property
    def has_s3_config(self) -> bool:
        """Check if user has enough S3 settings for backup operations"""
        return bool(
            self.s3_enabled
            and self.s3_bucket
            and self.s3_access_key_id
            and self.s3_secret_access_key
        )

    @classmethod
    async def get_by_username(cls, session: AsyncSession, username: str) -> Optional["User"]:
        """Get user by username"""
        result = await session.execute(select(cls).where(cls.username == username))
        return result.scalar_one_or_none()

    @classmethod
    async def get_by_email(cls, session: AsyncSession, email: str) -> Optional["User"]:
        """Get user by email"""
        result = await session.execute(select(cls).where(cls.email == email))
        return result.scalar_one_or_none()

    @classmethod
    async def get_by_api_key(cls, session: AsyncSession, api_key: str) -> Optional["User"]:
        """Get a user by a keyed digest, with a temporary plaintext fallback."""
        digest = hash_token(api_key, settings.TOKEN_HASH_KEY or settings.SECRET_KEY)
        result = await session.execute(select(cls).where(cls.api_key_hash == digest))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        # Compatibility for rows not yet processed by the credential backfill.
        result = await session.execute(select(cls).where(cls.api_key == api_key))
        return result.scalar_one_or_none()

    def set_api_key(self, api_key: str | None) -> None:
        """Replace or revoke the API key without retaining recoverable plaintext."""
        self.api_key = None
        self.api_key_hash = (
            hash_token(api_key, settings.TOKEN_HASH_KEY or settings.SECRET_KEY)
            if api_key is not None
            else None
        )
        self.api_key_prefix = api_key[:8] if api_key is not None else None

    @classmethod
    async def get_by_google_id(cls, session: AsyncSession, google_id: str) -> Optional["User"]:
        """Get user by Google ID"""
        result = await session.execute(select(cls).where(cls.google_id == google_id))
        return result.scalar_one_or_none()


class PasswordResetToken(SQLModel, table=True):
    """Password reset token model"""

    __tablename__ = "password_reset_tokens"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    # ``token`` is the legacy plaintext column and becomes nullable in the
    # expand migration. All newly-created reset tokens use ``token_hash``.
    token: Optional[str] = Field(default=None, max_length=64, unique=True, index=True)
    token_hash: Optional[str] = Field(
        default=None,
        sa_column=Column(CHAR(64), nullable=True, unique=True, index=True),
    )
    token_prefix: Optional[str] = Field(default=None, max_length=12)
    expires_at: datetime = Field(nullable=False)
    used: bool = Field(default=False)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": func.now()}
    )

    def __repr__(self):
        return f"<PasswordResetToken(id={self.id}, user_id={self.user_id}, used={self.used})>"

    @property
    def is_expired(self) -> bool:
        """Check if token is expired"""
        from modules.utils import get_current_time

        current_time = get_current_time()

        # Ensure both datetimes are timezone-aware for comparison
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            # If expires_at is naive, make it aware using the same timezone as current_time
            expires_at = expires_at.replace(tzinfo=current_time.tzinfo)

        return current_time > expires_at

    @property
    def is_valid(self) -> bool:
        """Check if token is valid (not used and not expired)"""
        return not self.used and not self.is_expired

    @classmethod
    async def get_by_token(
        cls, session: AsyncSession, token: str
    ) -> Optional["PasswordResetToken"]:
        """Look up a reset token by digest, with a legacy plaintext fallback."""
        digest = hash_token(token, settings.TOKEN_HASH_KEY or settings.SECRET_KEY)
        result = await session.execute(select(cls).where(cls.token_hash == digest))
        reset_token = result.scalar_one_or_none()
        if reset_token is not None:
            return reset_token
        result = await session.execute(select(cls).where(cls.token == token))
        return result.scalar_one_or_none()

    @classmethod
    async def create_token(
        cls, session: AsyncSession, user_id: int, token: str, expires_at: datetime
    ) -> "PasswordResetToken":
        """Create a new password reset token"""
        reset_token = cls(
            user_id=user_id,
            token=None,
            token_hash=hash_token(token, settings.TOKEN_HASH_KEY or settings.SECRET_KEY),
            token_prefix=token[:8],
            expires_at=expires_at,
        )
        session.add(reset_token)
        await session.commit()
        await session.refresh(reset_token)
        return reset_token


register_credential_shadows(
    User,
    (
        "steam_api_key",
        "github_token",
        "s3_access_key_id",
        "s3_secret_access_key",
    ),
)
