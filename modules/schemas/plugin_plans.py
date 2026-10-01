"""Plugins schemas."""

# ruff: noqa: F403,F405

from .common import *
from .plugin_releases import LinuxRuntimeProfile


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
