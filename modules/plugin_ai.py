"""Validated, portable values for AI-assisted marketplace entries."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def repository_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise ValueError("Only public HTTPS github.com repositories are supported")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parsed.query or parsed.fragment:
        raise ValueError("Expected a GitHub owner/repository URL")
    if not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError("Invalid GitHub repository")
    owner, repo = parts
    repo = repo.removesuffix(".git")
    if not repo or any(part in {".", ".."} for part in (owner, repo)):
        raise ValueError("Invalid GitHub repository")
    return f"https://github.com/{owner}/{repo}".lower()


class StrictValue(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginDescriptionI18n(StrictValue):
    """Optional descriptions collected for the supported console locales.

    ``original`` is always the source-language AI summary. The locale fields
    are populated only when an administrator requests a translation during an
    AI import, so the default import does not spend an extra translation call.
    """

    original: str | None = Field(default=None, max_length=10000)
    zh_cn: str | None = Field(default=None, max_length=10000)
    en_us: str | None = Field(default=None, max_length=10000)

    @field_validator("original", "zh_cn", "en_us")
    @classmethod
    def non_blank_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value if value.strip() else None


class InstallationMapping(StrictValue):
    """Copy a real archive file or directory into a game-relative directory."""

    source: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=255)

    @field_validator("source", "target")
    @classmethod
    def safe_mapping_path(cls, value: str) -> str:
        value = value.rstrip("/")
        if value.startswith("/") or not re.fullmatch(r"[A-Za-z0-9_./ -]+", value):
            raise ValueError("Mapping paths must be safe relative paths")
        if any(part in {"", ".."} for part in value.split("/")):
            raise ValueError("Mapping cannot escape the archive or game directory")
        return value

    @field_validator("target")
    @classmethod
    def allowed_target(cls, value: str) -> str:
        if value.split("/", 1)[0] not in {"addons", "cfg"}:
            raise ValueError("Mapping target must be inside addons or cfg")
        return value


class InstallationConfig(StrictValue):
    asset_glob: str = Field(default="*", min_length=1, max_length=200)
    automatic: bool = False
    mappings: list[InstallationMapping] = Field(default_factory=list, max_length=20)
    source_prefix: str = Field(default="", max_length=500)
    target_path: str | None = Field(default=None, max_length=255)

    @field_validator("source_prefix", "target_path")
    @classmethod
    def safe_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("/") or "\\" in value or ":" in value:
            raise ValueError("Installation paths must be relative")
        if ".." in value.split("/") or any(ord(char) < 32 for char in value):
            raise ValueError("Unsafe installation path")
        if not re.fullmatch(r"[A-Za-z0-9_./ -]*", value):
            raise ValueError("Unsupported installation path characters")
        return value.rstrip("/")

    @field_validator("target_path")
    @classmethod
    def safe_target(cls, value: str | None) -> str | None:
        if value is not None and value.split("/", 1)[0] not in {"addons", "cfg"}:
            raise ValueError("Target must be inside addons or cfg")
        return value

    @field_validator("asset_glob")
    @classmethod
    def safe_glob(cls, value: str) -> str:
        if "/" in value or "\\" in value or any(ord(c) < 32 for c in value):
            raise ValueError("Asset glob must match a filename")
        return value


class DocumentationSource(StrictValue):
    path: str = Field(max_length=500)
    commit: str = Field(pattern=r"^[a-fA-F0-9]{40,64}$")


class PluginAIInfo(StrictValue):
    """AI-derived marketplace metadata an administrator reviews before install.

    ``requirements`` holds only prerequisites the panel recognizes precisely —
    a named runtime such as Metamod:Source or CounterStrikeSharp (see
    ``services.plugins.ai_requirements``). ``notes`` holds everything the model
    said that could not be pinned to a known runtime, plus the importer's own
    advisories. Notes are shown before an install and never block it, so a vague
    model sentence cannot make a listing uninstallable.
    """

    model: str = Field(max_length=255)
    reviewed: bool = False
    installation: InstallationConfig | None = None
    requirements: list[str] = Field(default_factory=list, max_length=50)
    notes: list[str] = Field(default_factory=list, max_length=50)
    sources: list[DocumentationSource] = Field(default_factory=list, max_length=10)

    @field_validator("requirements", "notes")
    @classmethod
    def bounded_requirements(cls, values: list[str]) -> list[str]:
        if any(len(value) > 1000 for value in values):
            raise ValueError("Requirement is too long")
        return values

    def revision(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()


SortKey = Literal["stars", "forks", "updated"]
DEFAULT_SORT_PRIORITY: tuple[SortKey, ...] = ("stars", "updated", "forks")


class ImportOptions(StrictValue):
    """One submitted AI marketplace sweep.

    ``sort_priority`` is the ordered tie-breaker chain applied to the merged
    candidate list; ``sort`` mirrors its first key because that is the single
    value GitHub's search API accepts, and it keeps jobs and clients that were
    written before the chain existed valid.
    """

    framework: Literal["counterstrikesharp", "swiftly", "other", "all"] = "all"
    description_language: Literal["original", "zh-CN", "en-US"] = "original"
    keywords: str = Field(default="", max_length=200)
    min_stars: int = Field(default=10, ge=0, le=1_000_000)
    min_forks: int = Field(default=0, ge=0, le=1_000_000)
    sort: SortKey = "stars"
    sort_priority: list[SortKey] = Field(
        default_factory=lambda: list(DEFAULT_SORT_PRIORITY), min_length=1, max_length=3
    )
    updated_within_days: int = Field(default=90, ge=1, le=3650)
    # Plan semantic keyword groups first, with deterministic search fallbacks.
    expand_search: bool = True
    # Drop a plugin whose prerequisites could not be imported automatically.
    require_dependencies: bool = True
    repositories: list[str] = Field(default_factory=list, max_length=10)
    minutes: int = Field(default=15, ge=1, le=120)
    max_plugins: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="before")
    @classmethod
    def carry_legacy_sort(cls, data: object) -> object:
        """A payload with only ``sort`` keeps that key as the primary ordering."""
        if isinstance(data, dict) and "sort" in data and "sort_priority" not in data:
            primary = data["sort"]
            if primary in DEFAULT_SORT_PRIORITY:
                rest = [key for key in DEFAULT_SORT_PRIORITY if key != primary]
                return {**data, "sort_priority": [primary, *rest]}
        return data

    @model_validator(mode="after")
    def align_sort(self) -> ImportOptions:
        priority = list(dict.fromkeys(self.sort_priority)) or list(DEFAULT_SORT_PRIORITY)
        self.sort_priority = priority
        self.sort = priority[0]
        return self

    @field_validator("repositories")
    @classmethod
    def valid_repositories(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(repository_url(value) for value in values))


class RepositoryAnalysis(StrictValue):
    is_plugin: bool
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(max_length=10000)
    description_i18n: PluginDescriptionI18n | None = None
    category: Literal[
        "game_mode", "entertainment", "utility", "admin", "performance", "library", "other"
    ]
    framework: Literal["counterstrikesharp", "swiftly", "other"]
    installation: InstallationConfig | None = None
    dependencies: list[str] = Field(default_factory=list, max_length=30)
    requirements: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("dependencies")
    @classmethod
    def valid_dependencies(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(repository_url(value) for value in values))


class ImportItem(StrictValue):
    repository: str
    status: Literal["imported", "skipped", "failed"]
    message: str = Field(default="", max_length=2000)
    plugin_id: int | None = None


class ImportTokenUsage(StrictValue):
    input_tokens: int = Field(ge=0, le=10_000_000)
    output_tokens: int = Field(ge=0, le=10_000_000)
    reasoning_tokens: int = Field(ge=0, le=10_000_000)
    estimated: bool
    stage: Literal["waiting", "thinking", "generating", "completed"]


class ImportEvent(StrictValue):
    token_usage: ImportTokenUsage | None = None
    sequence: int
    phase: str
    message: str
    repository: str | None = None


class GitHubVerification(StrictValue):
    valid: bool = False
    account: str | None = None
    checked_at: str | None = None
    core_remaining: int | None = None
    core_reset: int | None = None
    search_remaining: int | None = None
    search_reset: int | None = None
    message: str = ""
