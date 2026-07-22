"""Plugins models."""

# ruff: noqa: F403,F405

from .common import *


class PluginCategory(str, enum.Enum):
    """Plugin category enumeration"""

    GAME_MODE = "game_mode"
    ENTERTAINMENT = "entertainment"
    UTILITY = "utility"
    ADMIN = "admin"
    PERFORMANCE = "performance"
    LIBRARY = "library"
    OTHER = "other"


class MarketPlugin(SQLModel, table=True):
    """Plugin market model - stores plugins available for installation"""

    __tablename__ = "market_plugins"

    id: Optional[int] = Field(default=None, primary_key=True, index=True)
    github_url: str = Field(max_length=500, nullable=False, unique=True, index=True)
    title: str = Field(max_length=255, nullable=False, index=True)
    description: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    author: Optional[str] = Field(default=None, max_length=255)
    version: Optional[str] = Field(default=None, max_length=50)
    category: PluginCategory = Field(
        default=PluginCategory.OTHER, sa_column=Column(SQLEnum(PluginCategory), nullable=False)
    )
    tags: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )  # Comma-separated tags
    is_recommended: bool = Field(default=False)
    icon_url: Optional[str] = Field(default=None, max_length=500)
    dependencies: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )  # Comma-separated plugin IDs
    custom_install_path: Optional[str] = Field(
        default=None, max_length=255
    )  # Custom extraction path for non-standard packages (e.g., "addons")
    download_count: int = Field(default=0)
    install_count: int = Field(default=0)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )

    def __repr__(self):
        return (
            f"<MarketPlugin(id={self.id}, title='{self.title}', category='{self.category.value}')>"
        )

    @classmethod
    async def get_by_id(cls, session: AsyncSession, plugin_id: int) -> Optional["MarketPlugin"]:
        """Get plugin by ID"""
        result = await session.execute(select(cls).where(cls.id == plugin_id))
        return result.scalar_one_or_none()

    @classmethod
    async def get_by_github_url(
        cls, session: AsyncSession, github_url: str
    ) -> Optional["MarketPlugin"]:
        """Get plugin by GitHub URL"""
        result = await session.execute(select(cls).where(cls.github_url == github_url))
        return result.scalar_one_or_none()

    @classmethod
    async def search_plugins(
        cls,
        session: AsyncSession,
        category: Optional[PluginCategory] = None,
        search_query: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[List["MarketPlugin"], int]:
        """
        Search plugins with filters and pagination.
        Returns tuple of (plugins, total_count)
        """
        from sqlalchemy import func as sqlfunc
        from sqlalchemy import or_

        query = select(cls)
        count_query = select(sqlfunc.count()).select_from(cls)

        # Apply category filter
        if category:
            query = query.where(cls.category == category)
            count_query = count_query.where(cls.category == category)

        # Apply search query (search in title, description, author)
        if search_query and search_query.strip():
            search_pattern = f"%{search_query.strip()}%"
            search_condition = or_(
                cls.title.like(search_pattern),
                cls.description.like(search_pattern),
                cls.author.like(search_pattern),
            )
            query = query.where(search_condition)
            count_query = count_query.where(search_condition)

        # Get total count
        count_result = await session.execute(count_query)
        total_count = count_result.scalar()

        # Apply ordering (recommended first, then by install count)
        query = query.order_by(
            cls.is_recommended.desc(), cls.install_count.desc(), cls.created_at.desc()
        )

        # Apply pagination
        query = query.offset(skip).limit(limit)

        # Execute query
        result = await session.execute(query)
        plugins = result.scalars().all()

        return plugins, total_count


class ManagedPlugin(SQLModel, table=True):
    """A GitHub-backed plugin/framework managed for one game server."""

    __tablename__ = "managed_plugins"
    __table_args__ = (
        UniqueConstraint("server_id", "source_type", "source_key", name="uq_managed_plugin_source"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    source_type: str = Field(max_length=30)  # github, market, framework
    source_key: str = Field(max_length=500)
    display_name: str = Field(max_length=255)
    repo_url: Optional[str] = Field(default=None, max_length=500)
    market_plugin_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("market_plugins.id", ondelete="SET NULL"), nullable=True
        ),
    )
    framework_key: Optional[str] = Field(default=None, max_length=100)
    installed_release_id: Optional[str] = Field(default=None, max_length=100)
    installed_version: str = Field(default="unknown", max_length=100)
    latest_version: Optional[str] = Field(default=None, max_length=100)
    asset_glob: Optional[str] = Field(default=None, max_length=500)
    custom_install_path: Optional[str] = Field(default=None, max_length=255)
    exclude_dirs: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    exclude_files: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    auto_update_enabled: bool = Field(default=False)
    backup_before_update: bool = Field(default=False)
    restart_after_update: bool = Field(default=False)
    last_check_at: Optional[datetime] = Field(default=None)
    last_update_at: Optional[datetime] = Field(default=None)
    last_status: Optional[str] = Field(default=None, max_length=30)
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )


class PluginConfigSource(SQLModel, table=True):
    """A file or directory exposed by the generic plugin configuration editor."""

    __tablename__ = "plugin_config_sources"
    __table_args__ = (
        UniqueConstraint("server_id", "path_hash", name="uq_plugin_config_source_path"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("servers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    relative_path: str = Field(max_length=1000)
    path_hash: str = Field(max_length=64)
    source_type: str = Field(max_length=16)  # directory or file
    is_default: bool = Field(default=False)
    is_enabled: bool = Field(default=True)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={
            "server_default": text("CURRENT_TIMESTAMP"),
            "onupdate": func.now(),
        },
    )
