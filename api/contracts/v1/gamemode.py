"""Game-mode installation and preflight contracts."""

from __future__ import annotations

from pydantic import Field

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model
from api.contracts.v1.plugins import PluginConflictView, PluginInstallPlanView


class GameModeMapView(V1Model):
    name: str
    workshop_id: str


class GameModeSummaryView(V1Model):
    id: str
    launch_upsert: dict[str, str]
    frameworks: list[str]
    market_plugin_titles: list[str]
    maps: list[GameModeMapView]
    plugin_config: dict[str, object]
    startup_workshop_map: str
    present: dict[str, bool | None]
    missing_market_plugins: list[str] = Field(default_factory=list)


class GameModeCatalogView(V1Model):
    server_id: int
    reachable: bool
    additional_parameters: str | None = None
    addons_path: str
    addons_present: bool | None = None
    swiftly_installed: bool | None = None
    modes: list[GameModeSummaryView] = Field(default_factory=list)


class GameModeMutationView(V1Model):
    id: str
    target: str
    before: object | None = None
    after: object | None = None
    destructive: bool = False
    status: str


class GameModeStepView(V1Model):
    id: str
    action: str
    status: str
    destructive: bool = False
    path: str | None = None
    title: str | None = None
    plugin_id: int | None = None
    framework: str | None = None
    name: str | None = None
    workshop_id: str | None = None
    values: dict[str, object] | None = None
    files: list[str] | None = None


class GameModeStartupView(V1Model):
    before: str | None = None
    after: str | None = None
    changed: bool = False


class GameModePreflightRequest(ApiRequest):
    wipe_addons: bool = False


class GameModeInstallRequest(ApiRequest):
    wipe_addons: bool = False
    wipe_addons_acknowledged: bool = False
    plan_hash: str = Field(min_length=64, max_length=64)
    acknowledge_warning_rule_ids: list[int] = Field(default_factory=list)


class GameModePlanView(V1Model):
    server_id: int
    mode_id: str
    wipe_addons: bool
    addons_path: str
    current: dict[str, bool]
    startup: GameModeStartupView
    plugin_config: dict[str, object]
    maps: list[GameModeMapView]
    wait_files: list[str]
    plugin_plans: dict[str, PluginInstallPlanView] = Field(default_factory=dict)
    hard_conflicts: list[PluginConflictView] = Field(default_factory=list)
    warnings: list[PluginConflictView] = Field(default_factory=list)
    steps: list[GameModeStepView] = Field(default_factory=list)
    mutations: list[GameModeMutationView] = Field(default_factory=list)
    blocked: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    plan_hash: str


__all__ = [
    "GameModeMapView",
    "GameModeSummaryView",
    "GameModeCatalogView",
    "GameModeMutationView",
    "GameModeStepView",
    "GameModeStartupView",
    "GameModePreflightRequest",
    "GameModeInstallRequest",
    "GameModePlanView",
]
