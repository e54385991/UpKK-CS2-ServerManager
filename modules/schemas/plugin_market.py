"""Plugins schemas."""

# ruff: noqa: F403,F405

from modules.plugin_ai import (
    InstallationConfig,
    PluginAIInfo,
    PluginDescriptionI18n,
)

from .common import *


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
    installation: Optional[InstallationConfig] = None


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
    description_i18n: PluginDescriptionI18n | None = None
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
