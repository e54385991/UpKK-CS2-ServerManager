"""Marketplace release preview, synchronization and deployment contracts."""

from uuid import UUID

from pydantic import Field

from api.contracts.base import ApiRequest
from api.contracts.v1.identity import V1Model
from api.contracts.v1.operations import ServerOperationView


class MarketReleasePreviewRequest(ApiRequest):
    rule: str | None = Field(default=None, max_length=500)
    asset_name: str | None = Field(default=None, max_length=500)


class MarketReleaseAssetView(V1Model):
    name: str
    ordinary: bool


class MarketReleasePreviewView(V1Model):
    previous_tag: str | None
    previous_assets: list[str]
    tag: str
    assets: list[MarketReleaseAssetView]
    selected_assets: list[str]
    rule: str | None
    preview_id: str | None
    reason: str | None


class MarketReleaseSyncRequest(ApiRequest):
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class MarketReleaseTargetView(V1Model):
    server_id: int
    server_name: str
    installed_version: str
    installed_asset_name: str | None
    backup_before_update: bool
    restart_after_update: bool


class MarketReleaseTargetsView(V1Model):
    revision: str
    tag: str
    targets: list[MarketReleaseTargetView]


class MarketReleaseDeployRequest(ApiRequest):
    revision: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_id: UUID
    server_ids: list[int] = Field(min_length=1, max_length=50)


class MarketReleaseDeployItemView(V1Model):
    server_id: int
    server_name: str | None = None
    operation: ServerOperationView | None = None
    error: str | None = None


class MarketReleaseDeployView(V1Model):
    items: list[MarketReleaseDeployItemView]
