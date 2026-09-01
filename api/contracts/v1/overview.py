"""Read-only overview and monitoring projections."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from api.contracts.v1.identity import V1Model


class OverviewSummary(V1Model):
    """Aggregate operational counters for the overview dashboard."""

    total: int
    running: int
    attention: int
    capacity: int
    ssh_connections: int = 0
    ssh_in_use: int = 0
    ssh_idle: int = 0
    ssh_leases: int = 0


class SteamLatestVersionView(V1Model):
    """Cached Steam CS2 advertised version. Never queries Steam on this request."""

    available: bool = False
    version: str | None = None
    message: str | None = None
    timestamp: datetime | None = None


class DiskSpaceView(V1Model):
    """Cached host disk snapshot for one game directory. Default reads never SSH."""

    server_id: int
    cached: bool = False
    used_gb: float | None = None
    total_gb: float | None = None
    available_gb: float | None = None
    used_percent: float | None = None


class DiskSpaceListView(V1Model):
    servers: list[DiskSpaceView] = Field(default_factory=list)
    timestamp: datetime


class A2SCacheView(V1Model):
    """Cached A2S snapshot for one server. Default reads never query A2S or SSH."""

    server_id: int
    cached: bool = False
    success: bool | None = None
    player_count: int | None = None
    max_players: int | None = None
    map_name: str | None = None
    server_name: str | None = None
    version: str | None = None
    last_updated: datetime | None = None
    response_time_ms: int | None = None


class A2SCacheListView(V1Model):
    servers: list[A2SCacheView] = Field(default_factory=list)
    timestamp: datetime


class A2SServerInfoView(V1Model):
    """One A2S_INFO payload. Extra Valve fields are ignored."""

    server_name: str | None = None
    map_name: str | None = None
    folder: str | None = None
    game: str | None = None
    player_count: int | None = None
    max_players: int | None = None
    bot_count: int | None = None
    server_type: str | None = None
    platform: str | None = None
    password_protected: bool | None = None
    vac_enabled: bool | None = None
    version: str | None = None
    ping: float | None = None
    keywords: str | None = None
    game_id: int | None = None


class A2SPlayerView(V1Model):
    name: str = ""
    score: int = 0
    duration: float = 0


class A2SQueryView(V1Model):
    """Last cached A2S snapshot, or a live query when requested."""

    query_host: str
    query_port: int
    success: bool
    cached: bool = False
    live: bool = False
    server_info: A2SServerInfoView | None = None
    players: list[A2SPlayerView] = Field(default_factory=list)
    timestamp: datetime | None = None
    last_updated: datetime | None = None
    response_time_ms: int | None = None
    error: str | None = None


class MonitoringLogView(V1Model):
    id: str
    event_type: str
    status: str
    message: str
    created_at: datetime | None = None


class MonitoringLogListView(V1Model):
    items: list[MonitoringLogView] = Field(default_factory=list)


class SshPoolView(V1Model):
    """Non-secret SSH connection-pool snapshot for the console chrome."""

    connections: int = 0
    in_use: int = 0
    idle: int = 0
    leases: int = 0
    draining: int = 0
    idle_timeout: int = 900
    max_lifetime: int = 3600
    keepalive_interval: int = 30
    keepalive_count_max: int = 3


class AuditEntry(V1Model):
    """One administrator-visible audit event (metadata only, non-secret)."""

    id: str
    created_at: datetime | None = None
    category: str
    action: str
    status: str
    actor_username: str | None = None
    ip_address: str | None = None
    source: str
    server_id: int | None = None
    details: dict = {}


__all__ = [
    "OverviewSummary",
    "SteamLatestVersionView",
    "DiskSpaceView",
    "DiskSpaceListView",
    "A2SCacheView",
    "A2SCacheListView",
    "A2SServerInfoView",
    "A2SPlayerView",
    "A2SQueryView",
    "MonitoringLogView",
    "MonitoringLogListView",
    "SshPoolView",
    "AuditEntry",
]
