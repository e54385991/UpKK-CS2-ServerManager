"""Small DTOs shared by queue adapters and workers.

These types intentionally have no FastAPI, Request, ORM or database-session
dependencies.  They are the seam between HTTP adapters and the durable
``server_operation_hub`` queue.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class OperationCommand:
    """Validated scalar command persisted before a worker is scheduled."""

    server_id: int
    action: str
    actor_user_id: int


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Worker outcome used by adapters and audit presenters."""

    operation_id: str
    success: bool
    message: str
    server_status: str | None = None
    completed_at: datetime | None = None
