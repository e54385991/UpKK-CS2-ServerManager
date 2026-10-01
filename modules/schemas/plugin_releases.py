"""Plugins schemas."""

# ruff: noqa: F403,F405

from modules.plugin_ai import (
    InstallationMapping,
)

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
    archive_mappings: List[InstallationMapping] = Field(default_factory=list, max_length=20)
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
