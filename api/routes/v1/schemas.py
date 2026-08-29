"""Response schemas for the versioned ``/api/v1`` surface.

These models are the stable, browser-facing projections. They deliberately
exclude every secret held on the underlying ORM models (SSH/RCON passwords,
Steam GSLT, API keys). Detail views expose only operational, non-sensitive
fields; secret mutation happens through dedicated, explicit actions.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from modules.models.servers import ServerStatus


class ProblemDetail(BaseModel):
    """RFC 9457-style error body used by the versioned API."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None


class SessionUser(BaseModel):
    """The authenticated principal as the console needs it."""

    id: int
    username: str
    email: str | None = None
    is_admin: bool
    is_active: bool


class ServerSummary(BaseModel):
    """Non-secret server projection for list and card views."""

    id: int
    name: str
    host: str
    game_port: int
    status: ServerStatus
    description: str | None = None
    default_map: str
    max_players: int


class ServerDetail(ServerSummary):
    """Extended, still non-secret, server projection for the workspace."""

    ssh_port: int
    ssh_user: str
    game_directory: str
    game_mode: str
    game_type: str
    created_at: datetime
    updated_at: datetime
    last_deployed: datetime | None = None


class OverviewSummary(BaseModel):
    """Aggregate operational counters for the overview dashboard."""

    total: int
    running: int
    attention: int
    capacity: int
