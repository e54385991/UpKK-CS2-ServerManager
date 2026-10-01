"""Plugins schemas."""

# ruff: noqa: F403,F405

from modules.plugin_ai import (
    PluginAIInfo,
    PluginDescriptionI18n,
)

from .common import *

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
    description_i18n: PluginDescriptionI18n | None = None
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
