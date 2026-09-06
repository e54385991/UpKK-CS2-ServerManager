"""Plugins schemas."""

# ruff: noqa: F403,F405

from modules.plugin_ai import PluginAIInfo

from .common import *


class LinuxRuntimeProfile(SQLModel):
    """Detected Linux userspace information used for Steam Runtime selection."""

    distro_id: Optional[str] = None
    distro_version: Optional[str] = None
    pretty_name: Optional[str] = None
    glibc_version: Optional[str] = None
    recommended_steam_runtime: Optional[Literal["steamrt3", "steamrt4"]] = None
    detection_source: Literal["glibc", "os_release", "unknown"] = "unknown"
    reason: str


class GitHubReleaseAsset(SQLModel):
    """Schema for a GitHub release asset"""

    name: str
    browser_download_url: str
    size: int
    content_type: Optional[str] = None
    steam_runtime: Optional[Literal["steamrt3", "steamrt4"]] = None
    runtime_compatibility: Literal["recommended", "alternative", "unknown", "not_applicable"] = (
        "not_applicable"
    )


class GitHubRelease(SQLModel):
    """Schema for a GitHub release"""

    id: Optional[str] = None
    tag_name: str
    name: Optional[str] = None
    published_at: Optional[str] = None
    prerelease: bool = False
    assets: List[GitHubReleaseAsset] = []


class GitHubReleasesResponse(SQLModel):
    """Schema for GitHub releases response"""

    success: bool
    releases: List[GitHubRelease] = []
    error: Optional[str] = None
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    linux_runtime_profile: Optional[LinuxRuntimeProfile] = None


class ArchiveContentItem(SQLModel):
    """Schema for an item in archive content"""

    path: str
    is_dir: bool
    size: int = 0


class ArchiveAnalysisResponse(SQLModel):
    """Schema for archive content analysis response"""

    success: bool
    has_addons_dir: bool = False
    root_dirs: List[str] = []
    all_dirs: List[str] = []  # All directories in archive (kept for backward compatibility)
    all_files: List[ArchiveContentItem] = []  # All files in archive for exclusion selection
    top_level_items: List[ArchiveContentItem] = []
    archive_type: Optional[str] = None
    error: Optional[str] = None


class GitHubPluginInstallRequest(SQLModel):
    """Schema for GitHub plugin installation request"""

    download_url: str = Field(..., description="Direct download URL for the release asset")
    exclude_dirs: List[str] = Field(
        default=[],
        description="Directories to exclude during extraction (deprecated, use exclude_files)",
    )
    exclude_files: List[str] = Field(
        default=[], description="Files to exclude during extraction (for updates)"
    )
    custom_install_path: Optional[str] = Field(
        default=None,
        description="Custom extraction path for non-standard packages (e.g., 'addons')",
    )
    repo_url: Optional[str] = Field(default=None, max_length=500)
    release_id: Optional[str] = Field(default=None, max_length=100)
    release_tag: Optional[str] = Field(default=None, max_length=100)
    asset_name: Optional[str] = Field(default=None, max_length=500)
    asset_glob: Optional[str] = Field(default=None, max_length=500)
    display_name: Optional[str] = Field(default=None, max_length=255)
    record_installation: bool = True
    suppress_notification: bool = False
    source_prefix: Optional[str] = Field(default=None, max_length=500)
    allowed_roots: List[Literal["addons", "cfg"]] = Field(default_factory=list)
    expected_archive_sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)
    installation_plan_hash: Optional[str] = Field(default=None, min_length=64, max_length=64)
    config_policy: Literal["preserve", "overwrite"] = "preserve"
    install_mode: Literal["install", "upgrade"] = "install"
    acknowledge_warning_rule_ids: List[int] = Field(default_factory=list)
    acknowledge_unknown_compatibility: bool = False

    @field_validator("download_url")
    @classmethod
    def validate_download_url(cls, v):
        """Validate that URL is from GitHub releases"""
        if not v.startswith("https://github.com/") or "/releases/download/" not in v:
            raise ValueError("Download URL must be a GitHub releases download URL")
        return v

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, v):
        if v is None:
            return v
        value = v.strip().rstrip("/")
        if not re.match(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", value):
            raise ValueError("repo_url must be a GitHub repository URL")
        return value

    @field_validator("source_prefix")
    @classmethod
    def validate_source_prefix(cls, v):
        if v is None:
            return v
        value = v.replace("\\", "/").strip("/")
        if not value or value == ".":
            return None
        if ".." in value.split("/") or "\x00" in value:
            raise ValueError("source_prefix must remain inside the archive")
        return value

    @field_validator("exclude_dirs")
    @classmethod
    def validate_exclude_dirs(cls, v):
        """Validate exclude directories to prevent path traversal"""
        for dir_path in v:
            if ".." in dir_path or dir_path.startswith("/"):
                raise ValueError("Exclude directories cannot contain path traversal sequences")
        return v

    @field_validator("exclude_files")
    @classmethod
    def validate_exclude_files(cls, v):
        """Validate exclude files to prevent path traversal"""
        for file_path in v:
            if ".." in file_path or file_path.startswith("/"):
                raise ValueError("Exclude files cannot contain path traversal sequences")
        return v


class GitHubPluginInstallResponse(SQLModel):
    """Schema for GitHub plugin installation response"""

    success: bool
    message: str
    installed_files: int = 0


class MarketPluginCreate(SQLModel):
    """Schema for creating a market plugin (admin only)"""

    github_url: str = Field(..., max_length=500, description="GitHub repository URL")
    title: Optional[str] = Field(
        None, max_length=255, description="Plugin title (auto-filled if not provided)"
    )
    description: Optional[str] = Field(
        None, description="Plugin description (auto-filled if not provided)"
    )
    author: Optional[str] = Field(None, max_length=255, description="Plugin author")
    version: Optional[str] = Field(None, max_length=50, description="Plugin version")
    category: str = Field(default="other", description="Plugin category")
    framework: str = Field(
        default="counterstrikesharp",
        description="Marketplace section: counterstrikesharp, swiftly, or other",
    )
    tags: Optional[str] = Field(None, description="Comma-separated tags")
    is_recommended: bool = Field(default=False, description="Whether to mark as recommended")
    icon_url: Optional[str] = Field(None, max_length=500, description="Icon URL")
    dependencies: Optional[str] = Field(None, description="Comma-separated plugin IDs")
    custom_install_path: Optional[str] = Field(
        None,
        max_length=255,
        description="Custom extraction path for non-standard packages (e.g., 'addons')",
    )


class MarketPluginUpdate(SQLModel):
    """Schema for updating a market plugin (admin only)"""

    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    author: Optional[str] = Field(None, max_length=255)
    version: Optional[str] = Field(None, max_length=50)
    category: Optional[str] = None
    framework: Optional[str] = None
    tags: Optional[str] = None
    is_recommended: Optional[bool] = None
    icon_url: Optional[str] = Field(None, max_length=500)
    dependencies: Optional[str] = None
    custom_install_path: Optional[str] = Field(None, max_length=255)


class DependencyInfo(SQLModel):
    """Schema for dependency information"""

    id: int
    title: str


class PluginConflictRuleInput(SQLModel):
    """One symmetric conflict rule managed from either plugin endpoint."""

    other_plugin_id: int = Field(gt=0)
    severity: Literal["hard", "warning"]
    reason: str = Field(min_length=1, max_length=2000)
    is_enabled: bool = True


class PluginConflictRuleResponse(SQLModel):
    id: int
    plugin_a_id: int
    plugin_b_id: int
    severity: Literal["hard", "warning"]
    reason: Optional[str] = None
    is_enabled: bool


class PluginConflictRulesUpdate(SQLModel):
    rules: List[PluginConflictRuleInput] = Field(default_factory=list, max_length=200)


class MarketPluginResponse(SQLModel):
    """Schema for market plugin response"""

    id: int
    github_url: str
    title: str
    description: Optional[str] = None
    author: Optional[str] = None
    version: Optional[str] = None
    category: str
    framework: str = "counterstrikesharp"
    tags: Optional[str] = None
    is_recommended: bool
    icon_url: Optional[str] = None
    dependencies: Optional[str] = None
    custom_install_path: Optional[str] = None
    ai_metadata: PluginAIInfo | None = None
    dependency_details: Optional[List[DependencyInfo]] = None
    download_count: int
    install_count: int
    created_at: datetime
    updated_at: datetime


class MarketPluginListResponse(SQLModel):
    """Schema for market plugin list response with pagination"""

    success: bool
    plugins: List[MarketPluginResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class MarketPluginInstallRequest(SQLModel):
    """Schema for installing a market plugin"""

    plugin_id: int = Field(..., description="Market plugin ID to install")
    server_id: int = Field(..., description="Server ID to install plugin on")
    exclude_dirs: List[str] = Field(
        default=[], description="Directories to exclude from installation"
    )


class PluginAutoUpdateSettings(SQLModel):
    enable_plugin_auto_update: bool
    plugin_update_check_interval_hours: float = Field(ge=0.0167, le=24.0)
    enable_plugin_post_update_commands: bool = False
    plugin_post_update_command_ids: List[int] = Field(default_factory=list)

    @field_validator("plugin_post_update_command_ids")
    @classmethod
    def validate_post_update_command_ids(cls, values: List[int]) -> List[int]:
        if values is None:
            return []
        cleaned: List[int] = []
        seen = set()
        for value in values:
            command_id = int(value)
            if command_id <= 0:
                raise ValueError("plugin_post_update_command_ids must contain positive integers")
            if command_id in seen:
                continue
            seen.add(command_id)
            cleaned.append(command_id)
        if len(cleaned) > 20:
            raise ValueError("At most 20 post-update quick commands can be configured")
        return cleaned


class ManagedPluginCreate(SQLModel):
    source_type: str = Field(default="github", max_length=30)
    source_key: Optional[str] = Field(default=None, max_length=500)
    display_name: str = Field(min_length=1, max_length=255)
    repo_url: Optional[str] = Field(default=None, max_length=500)
    market_plugin_id: Optional[int] = None
    framework_key: Optional[str] = Field(default=None, max_length=100)
    installed_release_id: Optional[str] = Field(default=None, max_length=100)
    installed_version: str = Field(default="unknown", max_length=100)
    asset_glob: Optional[str] = Field(default=None, max_length=500)
    custom_install_path: Optional[str] = Field(default=None, max_length=255)
    exclude_dirs: List[str] = Field(default_factory=list)
    exclude_files: List[str] = Field(default_factory=list)
    auto_update_enabled: bool = False
    backup_before_update: bool = False
    restart_after_update: bool = False

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value):
        if value not in {"github", "market", "framework"}:
            raise ValueError("source_type must be github, market, or framework")
        return value

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, value):
        if value and not re.match(
            r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$", value.strip()
        ):
            raise ValueError("repo_url must be a GitHub repository URL")
        return value.strip().rstrip("/") if value else value

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_exclusions(cls, values):
        for value in values:
            if ".." in value or value.startswith("/") or "\x00" in value:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
        return values


class ManagedPluginUpdate(SQLModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    installed_release_id: Optional[str] = Field(default=None, max_length=100)
    installed_version: Optional[str] = Field(default=None, max_length=100)
    asset_glob: Optional[str] = Field(default=None, max_length=500)
    custom_install_path: Optional[str] = Field(default=None, max_length=255)
    exclude_dirs: Optional[List[str]] = None
    exclude_files: Optional[List[str]] = None
    auto_update_enabled: Optional[bool] = None
    backup_before_update: Optional[bool] = None
    restart_after_update: Optional[bool] = None

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_exclusions(cls, values):
        if values is None:
            return values
        for value in values:
            if ".." in value or value.startswith("/") or "\x00" in value:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
        return values


class ManagedPluginResponse(SQLModel):
    id: int
    server_id: int
    source_type: str
    source_key: str
    display_name: str
    repo_url: Optional[str] = None
    market_plugin_id: Optional[int] = None
    framework_key: Optional[str] = None
    installed_release_id: Optional[str] = None
    installed_version: str
    latest_version: Optional[str] = None
    asset_glob: Optional[str] = None
    custom_install_path: Optional[str] = None
    exclude_dirs: List[str] = Field(default_factory=list)
    exclude_files: List[str] = Field(default_factory=list)
    auto_update_enabled: bool
    backup_before_update: bool = False
    restart_after_update: bool = False
    last_check_at: Optional[datetime] = None
    last_update_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None


class PluginAutoUpdateResponse(SQLModel):
    enable_plugin_auto_update: bool
    plugin_update_check_interval_hours: float
    last_plugin_update_check: Optional[datetime] = None
    enable_plugin_post_update_commands: bool = False
    plugin_post_update_command_ids: List[int] = Field(default_factory=list)
    plugins: List[ManagedPluginResponse] = Field(default_factory=list)


class GitHubRepoInfo(SQLModel):
    """Schema for GitHub repository information"""

    success: bool
    repo_name: Optional[str] = None
    description: Optional[str] = None
    readme: Optional[str] = None
    author: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    # Marketplace classification guessed from the repository, for form pre-fill.
    framework: Optional[str] = None
    category: Optional[str] = None
    error: Optional[str] = None


class PluginUninstallRequest(SQLModel):
    """Schema for plugin uninstallation request"""

    files_to_delete: List[str] = Field(
        ..., description="List of file paths to delete (relative to csgo directory)"
    )

    @field_validator("files_to_delete")
    @classmethod
    def validate_files_to_delete(cls, v):
        """Validate file paths to prevent path traversal and injection attacks"""
        import os
        import urllib.parse

        for file_path in v:
            # Normalize the path first
            normalized = os.path.normpath(file_path)

            # Check for various path traversal attempts
            if ".." in file_path or ".." in normalized:
                raise ValueError("File paths cannot contain path traversal sequences (..)")

            # Check for absolute paths
            if file_path.startswith("/") or os.path.isabs(normalized):
                raise ValueError("File paths must be relative (cannot start with /)")

            # Check for null bytes
            if "\x00" in file_path:
                raise ValueError("File paths cannot contain null bytes")

            # Check for URL-encoded path traversal (specifically look for encoded dots and slashes)
            # Only reject if there are actual encoded path traversal sequences
            decoded = urllib.parse.unquote(file_path)
            if ".." in decoded and ".." not in file_path:
                # Path contains encoded ".." which could be used for traversal
                raise ValueError("File paths cannot contain URL-encoded path traversal sequences")

            # Ensure normalized path doesn't escape the base directory
            if normalized.startswith("..") or normalized == "..":
                raise ValueError("Normalized path cannot escape base directory")

        return v


class PluginUninstallResponse(SQLModel):
    """Schema for plugin uninstallation response"""

    success: bool
    message: str
    deleted_files: int = 0
    failed_files: List[str] = []


class InstalledPluginFile(SQLModel):
    """Schema for an installed plugin file"""

    path: str
    size: int = 0
    is_dir: bool = False


class InstalledPluginAnalysisResponse(SQLModel):
    """Schema for analyzing installed plugins"""

    success: bool
    files: List[InstalledPluginFile] = []
    total_size: int = 0
    error: Optional[str] = None


class MetamodStatusResponse(SQLModel):
    """Schema for metamod installation status"""

    success: bool
    installed: bool
    path: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


class PluginDiagnosticPlanRequest(SQLModel):
    scope: Literal["metamod", "counterstrikesharp", "both"] = "both"


class PluginDiagnosticExecuteRequest(PluginDiagnosticPlanRequest):
    expected_plan_hash: str = Field(min_length=64, max_length=64)


class PluginDiagnosticPlanResponse(SQLModel):
    server_id: int
    scope: str
    plan_hash: str
    candidates: List[Dict] = Field(default_factory=list)
    candidate_groups: List[Dict] = Field(default_factory=list)
    estimated_max_starts: int
    health_policy: Dict = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class PluginDiagnosticRunResponse(SQLModel):
    id: str
    server_id: int
    requested_by: int
    scope: str
    status: str
    plan_hash: str
    culprit_keys: List[str] = Field(default_factory=list)
    start_attempts: int = 0
    error: Optional[str] = None
    steps: List[Dict] = Field(default_factory=list)
    quarantine: List[Dict] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class GitHubPluginInspectRequest(SQLModel):
    repo_url: str = Field(min_length=1, max_length=500)
    mode: Literal["install", "upgrade"] = "install"


class GitHubPluginSearchResponse(SQLModel):
    query: str
    candidates: List[Dict] = Field(default_factory=list)
    recommended_repo_url: Optional[str] = None
    linux_runtime_profile: Optional[LinuxRuntimeProfile] = None


class GitHubPluginInspectResponse(SQLModel):
    repo_url: str
    repository: Dict = Field(default_factory=dict)
    release: Dict = Field(default_factory=dict)
    selected_asset: Optional[Dict] = None
    documentation: Dict = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    linux_runtime_profile: Optional[LinuxRuntimeProfile] = None


class GitHubPluginInstallPlanRequest(GitHubPluginInspectRequest):
    asset_name: Optional[str] = Field(default=None, max_length=500)
    config_policy: Literal["preserve", "overwrite"] = "preserve"
    recipe_id: Optional[int] = Field(default=None, gt=0)
    source_prefix: Optional[str] = Field(default=None, max_length=500)
    target_prefix: Optional[str] = Field(default=None, max_length=500)
    exclude_dirs: List[str] = Field(default_factory=list)
    exclude_files: List[str] = Field(default_factory=list)

    @field_validator("source_prefix", "target_prefix")
    @classmethod
    def validate_mapping_prefix(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = value.replace("\\", "/").strip()
        if not text or text == ".":
            return None
        if text.startswith("/") or ".." in text.split("/") or "\x00" in text:
            raise ValueError("mapping prefix must stay inside the archive")
        return text.strip("/")

    @field_validator("exclude_dirs", "exclude_files")
    @classmethod
    def validate_plan_exclusions(cls, values: List[str]) -> List[str]:
        cleaned: List[str] = []
        for value in values or []:
            text = str(value).replace("\\", "/").strip()
            if not text:
                continue
            if ".." in text.split("/") or text.startswith("/") or "\x00" in text:
                raise ValueError("exclusion paths must be relative and cannot contain traversal")
            cleaned.append(text)
        return cleaned


class GitHubPluginInstallExecuteRequest(GitHubPluginInstallPlanRequest):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: List[int] = Field(default_factory=list)
    acknowledge_unknown_compatibility: bool = False


class GitHubPluginInstallPlanResponse(SQLModel):
    server_id: int
    repo_url: str
    mode: str
    config_policy: str
    plan_hash: str
    release: Dict = Field(default_factory=dict)
    asset: Dict = Field(default_factory=dict)
    archive_sha256: str
    mapping: List[Dict] = Field(default_factory=list)
    files: List[Dict] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    mapping_required: bool = False
    plugin_metadata: Dict = Field(default_factory=dict)
    dependencies: List[Dict] = Field(default_factory=list)
    already_installed: List[int] = Field(default_factory=list)
    hard_conflicts: List[Dict] = Field(default_factory=list)
    conflict_warnings: List[Dict] = Field(default_factory=list)
    compatibility_unknown: bool = False
    linux_runtime_profile: Optional[LinuxRuntimeProfile] = None


class GitHubInstallRecipeCreate(SQLModel):
    repo_url: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=255)
    source_prefix: str = Field(max_length=500)
    target_prefix: Literal["addons", "cfg"]
    framework: Optional[Literal["metamod", "counterstrikesharp"]] = None
    config_globs: List[str] = Field(default_factory=list, max_length=50)
    required_repositories: List[str] = Field(default_factory=list, max_length=20)
    documentation_commit: Optional[str] = Field(default=None, max_length=64)


_GITHUB_REPO_PREFIX = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    re.IGNORECASE,
)


def _require_github_repo_url(value: str) -> str:
    normalized = (value or "").strip()
    if not _GITHUB_REPO_PREFIX.match(normalized):
        raise ValueError("must be a GitHub repository URL")
    return normalized


class PluginCatalogEntry(SQLModel):
    """One marketplace plugin in a portable catalog, keyed by GitHub URL."""

    github_url: str = Field(..., max_length=500)
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    author: Optional[str] = Field(None, max_length=255)
    version: Optional[str] = Field(None, max_length=50)
    category: str = Field(default="other")
    framework: str = Field(default="counterstrikesharp")
    tags: Optional[str] = None
    is_recommended: bool = False
    icon_url: Optional[str] = Field(None, max_length=500)
    custom_install_path: Optional[str] = Field(None, max_length=255)
    ai_metadata: PluginAIInfo | None = None
    dependencies: List[str] = Field(default_factory=list, max_length=50)

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, value: str) -> str:
        return _require_github_repo_url(value)

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, values: List[str]) -> List[str]:
        return [_require_github_repo_url(item) for item in values]


class PluginCatalogConflict(SQLModel):
    """A symmetric conflict rule identified by GitHub repository URLs."""

    plugin_a_url: str = Field(..., max_length=500)
    plugin_b_url: str = Field(..., max_length=500)
    severity: Literal["hard", "warning"] = "hard"
    reason: Optional[str] = Field(default=None, max_length=2000)
    is_enabled: bool = True

    @field_validator("plugin_a_url", "plugin_b_url")
    @classmethod
    def validate_conflict_url(cls, value: str) -> str:
        return _require_github_repo_url(value)


class PluginCatalogExport(SQLModel):
    """Portable plugin-market catalog. Local numeric IDs are never included."""

    format: Literal["upkk-cs2-plugin-catalog"] = "upkk-cs2-plugin-catalog"
    version: int = Field(default=1, ge=1, le=1)
    exported_at: Optional[datetime] = None
    plugins: List[PluginCatalogEntry] = Field(default_factory=list, max_length=500)
    conflicts: List[PluginCatalogConflict] = Field(default_factory=list, max_length=2000)


class PluginCatalogImportRequest(PluginCatalogExport):
    """Admin-only catalog import with skip/update conflict handling."""

    conflict_strategy: Literal["skip", "update"] = "skip"


class PluginCatalogImportResult(SQLModel):
    """Result for one plugin or conflict rule in a catalog import."""

    index: int
    kind: Literal["plugin", "conflict"]
    name: str
    action: Literal["imported", "updated", "skipped", "failed"]
    plugin_id: Optional[int] = None
    message: Optional[str] = None


class PluginCatalogImportResponse(SQLModel):
    """Summary returned after processing a plugin catalog import."""

    total: int
    imported: int
    updated: int
    skipped: int
    failed: int
    results: List[PluginCatalogImportResult]
