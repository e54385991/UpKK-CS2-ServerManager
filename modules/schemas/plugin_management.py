"""Plugins schemas."""

# ruff: noqa: F403,F405

from .common import *


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
