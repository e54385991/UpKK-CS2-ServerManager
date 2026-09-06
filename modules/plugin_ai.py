"""Validated, portable values for AI-assisted marketplace entries."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class InstallationConfig(StrictValue):
    asset_glob: str = Field(default="*", min_length=1, max_length=200)
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
    model: str = Field(max_length=255)
    reviewed: bool = False
    installation: InstallationConfig | None = None
    requirements: list[str] = Field(default_factory=list, max_length=50)
    sources: list[DocumentationSource] = Field(default_factory=list, max_length=10)

    @field_validator("requirements")
    @classmethod
    def bounded_requirements(cls, values: list[str]) -> list[str]:
        if any(len(value) > 1000 for value in values):
            raise ValueError("Requirement is too long")
        return values

    def revision(self) -> str:
        encoded = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()


class ImportOptions(StrictValue):
    framework: Literal["counterstrikesharp", "swiftly", "all"] = "all"
    keywords: str = Field(default="", max_length=200)
    min_stars: int = Field(default=10, ge=0, le=1_000_000)
    min_forks: int = Field(default=0, ge=0, le=1_000_000)
    sort: Literal["stars", "forks", "updated"] = "stars"
    updated_within_days: int = Field(default=365, ge=1, le=3650)
    repositories: list[str] = Field(default_factory=list, max_length=10)
    minutes: int = Field(default=15, ge=1, le=120)
    max_plugins: int = Field(default=20, ge=1, le=100)

    @field_validator("repositories")
    @classmethod
    def valid_repositories(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(repository_url(value) for value in values))


class RepositoryAnalysis(StrictValue):
    is_plugin: bool
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(max_length=10000)
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


class ImportEvent(StrictValue):
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
