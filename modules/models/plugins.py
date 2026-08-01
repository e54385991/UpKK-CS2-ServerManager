"""Plugins models."""

# ruff: noqa: F403,F405

import uuid

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


class PluginConflictRule(SQLModel, table=True):
    """A symmetric compatibility rule between two market plugins."""

    __tablename__ = "plugin_conflict_rules"
    __table_args__ = (
        UniqueConstraint("plugin_a_id", "plugin_b_id", name="uq_plugin_conflict_pair"),
        CheckConstraint("plugin_a_id < plugin_b_id", name="ck_plugin_conflict_canonical_pair"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    plugin_a_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("market_plugins.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    plugin_b_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("market_plugins.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    severity: str = Field(default="hard", max_length=16)
    reason: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    is_enabled: bool = Field(default=True)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )


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
    async def get_by_ids(
        cls,
        session: AsyncSession,
        plugin_ids: List[int],
    ) -> List["MarketPlugin"]:
        """Get plugins in one query while ignoring duplicate requested IDs."""
        unique_ids = list(dict.fromkeys(plugin_ids))
        if not unique_ids:
            return []
        result = await session.execute(select(cls).where(cls.id.in_(unique_ids)))
        return list(result.scalars().all())

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
    install_recipe_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("github_install_recipes.id", ondelete="SET NULL"), nullable=True
        ),
    )
    installed_asset_name: Optional[str] = Field(default=None, max_length=500)
    archive_sha256: Optional[str] = Field(default=None, max_length=64)
    config_policy: str = Field(default="preserve", max_length=32)
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


class GitHubInstallRecipe(SQLModel, table=True):
    """Admin-approved declarative mapping for an otherwise ambiguous release archive."""

    __tablename__ = "github_install_recipes"
    __table_args__ = (UniqueConstraint("repo_url", "revision", name="uq_github_recipe_revision"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_url: str = Field(max_length=500, index=True)
    display_name: str = Field(max_length=255)
    source_prefix: str = Field(max_length=500)
    target_prefix: str = Field(max_length=500)
    framework: Optional[str] = Field(default=None, max_length=32)
    config_globs: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    required_repositories: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    documentation_commit: Optional[str] = Field(default=None, max_length=64)
    revision: str = Field(max_length=64)
    is_enabled: bool = Field(default=True)
    created_by: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )


class ManagedPluginFile(SQLModel, table=True):
    """Revisioned file inventory used for safe upgrades and configuration preservation."""

    __tablename__ = "managed_plugin_files"
    __table_args__ = (
        UniqueConstraint("managed_plugin_id", "path_hash", name="uq_managed_plugin_file"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    managed_plugin_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("managed_plugins.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    relative_path: str = Field(max_length=1000)
    path_hash: str = Field(max_length=64)
    sha256: str = Field(max_length=64)
    file_role: str = Field(default="data", max_length=32)
    preserved: bool = Field(default=False)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )


class PluginDiagnosticRun(SQLModel, table=True):
    """Persistent, user-attributed plugin isolation state machine."""

    __tablename__ = "plugin_diagnostic_runs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=36)
    server_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    requested_by: int = Field(
        sa_column=Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    )
    server_owner_id: int = Field(sa_column=Column(Integer, nullable=False))
    ai_run_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(36), ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True),
    )
    scope: str = Field(max_length=32)
    status: str = Field(default="planned", max_length=40, index=True)
    plan_hash: str = Field(max_length=64)
    candidate_snapshot: List[dict] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    original_server_running: bool = Field(default=False)
    health_policy: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    culprit_keys: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    start_attempts: int = Field(default=0)
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP"), "onupdate": func.now()},
    )
    completed_at: Optional[datetime] = Field(default=None)


class PluginDiagnosticStep(SQLModel, table=True):
    __tablename__ = "plugin_diagnostic_steps"
    __table_args__ = (
        UniqueConstraint("diagnostic_run_id", "sequence", name="uq_diagnostic_step_sequence"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    diagnostic_run_id: str = Field(
        max_length=36,
        sa_column=Column(
            String(36),
            ForeignKey("plugin_diagnostic_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    sequence: int = Field(ge=1)
    phase: str = Field(max_length=64)
    candidate_keys: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    healthy: Optional[bool] = Field(default=None)
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )


class PluginQuarantineEntry(SQLModel, table=True):
    __tablename__ = "plugin_quarantine_entries"
    __table_args__ = (
        UniqueConstraint("diagnostic_run_id", "candidate_key", name="uq_quarantine_candidate"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    diagnostic_run_id: str = Field(
        max_length=36,
        sa_column=Column(
            String(36),
            ForeignKey("plugin_diagnostic_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    candidate_key: str = Field(max_length=500)
    source_relative_path: str = Field(max_length=1000)
    quarantine_relative_path: str = Field(max_length=1000)
    source_revision: str = Field(max_length=128)
    is_quarantined: bool = Field(default=False, index=True)
    is_culprit: bool = Field(default=False)
    created_at: Optional[datetime] = Field(
        default=None, sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")}
    )
    restored_at: Optional[datetime] = Field(default=None)


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
