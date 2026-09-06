"""System models."""

# ruff: noqa: F403,F405

from .common import *


class SSHServerSudo(SQLModel, table=True):
    """SSH Server Sudo Configuration model for setup wizard"""

    __tablename__: ClassVar[str] = "ssh_servers_sudo"

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)
    host: str = Field(max_length=255, nullable=False)
    ssh_port: int = Field(default=22, nullable=False)
    sudo_user: str = Field(max_length=100, nullable=False)
    sudo_password: str = Field(max_length=255, nullable=False)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )

    def __repr__(self):
        return f"<SSHServerSudo(id={self.id}, host='{self.host}', port={self.ssh_port}, user='{self.sudo_user}')>"

    @classmethod
    async def get_by_unique_key(
        cls, session: AsyncSession, user_id: int, host: str, ssh_port: int, sudo_user: str
    ) -> Optional["SSHServerSudo"]:
        """Get SSH sudo config by unique composite key"""
        result = await session.execute(
            select(cls).where(
                cls.user_id == user_id,
                cls.host == host,
                cls.ssh_port == ssh_port,
                cls.sudo_user == sudo_user,
            )
        )
        return result.scalar_one_or_none()

    @classmethod
    async def upsert(
        cls,
        session: AsyncSession,
        user_id: int,
        host: str,
        ssh_port: int,
        sudo_user: str,
        sudo_password: str,
    ) -> "SSHServerSudo":
        """Insert or update SSH sudo configuration"""
        # Try to get existing record
        existing = await cls.get_by_unique_key(session, user_id, host, ssh_port, sudo_user)

        if existing:
            # Update existing record (updated_at is handled by database trigger)
            existing.sudo_password = sudo_password
            session.add(existing)
            await session.commit()
            await session.refresh(existing)
            return existing
        else:
            # Create new record
            new_record = cls(
                user_id=user_id,
                host=host,
                ssh_port=ssh_port,
                sudo_user=sudo_user,
                sudo_password=sudo_password,
            )
            session.add(new_record)
            await session.commit()
            await session.refresh(new_record)
            return new_record


class SystemSettings(SQLModel, table=True):
    """System settings model for global configuration"""

    __tablename__: ClassVar[str] = "system_settings"

    id: int = Field(default=None, primary_key=True)
    # Proxy configuration
    default_proxy_mode: str = Field(default="panel", max_length=50)  # direct, panel, github_url
    github_proxy_url: Optional[str] = Field(default=None, max_length=500)
    plugin_download_cache_enabled: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("true")}
    )
    plugin_download_cache_path: Optional[str] = Field(default=None, max_length=1000)

    # Public and sensitive form protection. Keep enabled by default.
    captcha_enabled: bool = Field(
        default=True,
        sa_column_kwargs={"server_default": text("true")},
    )

    # Request header carrying the real client address behind a reverse proxy.
    # NULL means the panel trusts only the direct connection address.
    client_ip_header: Optional[str] = Field(
        default="X-Forwarded-For",
        max_length=64,
        sa_column_kwargs={"server_default": text("'X-Forwarded-For'")},
    )

    # How much the panel prints to stdout. NULL follows the LOG_LEVEL
    # environment variable; the log file always keeps the environment level.
    log_level: Optional[str] = Field(
        default="ERROR",
        max_length=16,
        sa_column_kwargs={"server_default": text("'ERROR'")},
    )

    # Shared GitHub API credential used only when a user has no personal token.
    global_github_token: Optional[str] = Field(default=None, max_length=255)
    github_token_fingerprint: Optional[str] = Field(default=None, max_length=64)
    github_token_verification: dict[str, object] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    # Email configuration
    email_enabled: bool = Field(default=False)
    email_provider: str = Field(default="gmail", max_length=50)  # gmail, smtp
    email_from_address: Optional[str] = Field(default=None, max_length=255)
    email_from_name: Optional[str] = Field(default=None, max_length=255)

    # Gmail API configuration
    gmail_credentials_json: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    gmail_token_json: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    # SMTP configuration (alternative to Gmail API)
    smtp_host: Optional[str] = Field(default=None, max_length=255)
    smtp_port: Optional[int] = Field(default=587)
    smtp_username: Optional[str] = Field(default=None, max_length=255)
    smtp_password: Optional[str] = Field(default=None, max_length=255)
    smtp_use_tls: bool = Field(default=True)

    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": func.now()}
    )
    updated_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": func.now(), "onupdate": func.now()}
    )

    def __repr__(self):
        return f"<SystemSettings(id={self.id}, proxy_mode='{self.default_proxy_mode}', email_enabled={self.email_enabled})>"

    @property
    def has_global_github_token(self) -> bool:
        """Whether a non-blank global GitHub fallback token is configured."""
        return bool((self.global_github_token or "").strip())

    @property
    def global_github_token_prefix(self) -> Optional[str]:
        """Return a safe preview for the admin UI without exposing the token."""
        token = self.global_github_token
        if not token or not token.strip():
            return None
        return f"{token.strip()[:12]}..."

    @classmethod
    async def get_settings(cls, session: AsyncSession) -> Optional["SystemSettings"]:
        """Get system settings (there should only be one row)"""
        result = await session.execute(select(cls).limit(1))
        return result.scalar_one_or_none()

    @classmethod
    async def get_or_create_settings(cls, session: AsyncSession) -> "SystemSettings":
        """Get system settings or create if not exists"""
        settings = await cls.get_settings(session)
        if not settings:
            settings = cls()
            session.add(settings)
            await session.commit()
            await session.refresh(settings)
        return settings
