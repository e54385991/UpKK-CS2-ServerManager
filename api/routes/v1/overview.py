"""Versioned overview aggregates for the dashboard."""

from fastapi import APIRouter

from api.dependencies import ActiveUser, DatabaseSession
from modules import Server
from modules.models.servers import ServerStatus

from .schemas import OverviewSummary

router = APIRouter(prefix="/api/v1/overview", tags=["v1-overview"])

_ATTENTION_STATUSES = frozenset({ServerStatus.ERROR, ServerStatus.UNKNOWN})


@router.get("/summary", response_model=OverviewSummary)
async def read_overview_summary(
    db: DatabaseSession,
    current_user: ActiveUser,
) -> OverviewSummary:
    """Aggregate operational counters across the current user's servers."""
    servers = await Server.get_all_by_user(db, current_user.id, skip=0, limit=1000)
    running = sum(1 for server in servers if server.status == ServerStatus.RUNNING)
    attention = sum(1 for server in servers if server.status in _ATTENTION_STATUSES)
    capacity = sum(server.max_players for server in servers)
    return OverviewSummary(
        total=len(servers),
        running=running,
        attention=attention,
        capacity=capacity,
    )
